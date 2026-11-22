"""The Batch level (ADR-0009, ADR-0011): many Projects run unattended.

A Batch is structural — a directory with at least one Project anywhere
beneath it. Discovery is recursive and **prunes at each Project**: the walk
descends until it finds one — ``project.yaml`` plus at least one
experiment-shaped subdirectory — and never looks inside, because a Project's
subdirectories are its Experiments by definition. Grouping folders
(``Sept2026/``, ``Archive/2025/``) are therefore transparent, and a **Member**
is identified by its POSIX path relative to the Batch root (``Sept2026/ProjA``;
a top-level Project is just ``ProjA``, so existing ``batch.yaml`` files,
Removal Sheets, and API calls keep working unchanged).

Nothing marks a Batch: ``batch.yaml`` at its
root appears only once batch-level scripting is authored, because unlike a
Project a Batch has no authority to declare. A Batch Run executes one
designated Project Script in every Project (continue-on-error, per-Project
log prefixes) and its only product is the per-Project run summary — a Batch
never pools results across Projects.

There is no third script level: the thing a Batch Run runs IS a Project
Script, resolved per Project as batch.yaml ``project_scripts:`` → the
Project's own ``scripts:`` → the built-ins (Report Pipeline, Standard
Pipeline). With no designation at all each Project runs its OWN default
script — every ``project.yaml`` is created with one, so nothing is
silently substituted for a Project that has none.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from . import layout
from . import project as prj
from . import removals

#: The lazy Batch file: the ``script:`` designation plus centrally-held
#: Project Scripts (the ``experiment_scripts:`` idea one level up).
BATCH_FILENAME = "batch.yaml"

#: The Batch AI narrative, written at the Batch root. Named distinctly from a
#: Project's ``ai_narrative.md`` so a recursive glob can tell the levels apart.
BATCH_NARRATIVE_FILENAME = "batch_ai_narrative.md"

#: What the batch-level agent is asked for. It reads the Projects' own
#: narratives, so it must synthesize across them rather than restate each:
#: the per-Project detail is already one file down.
BATCH_NARRATIVE_PROMPT = """\
You are given the AI narratives of several independent Projects from one
batch of insect-tracking experiments. Each Project is its own design with its
own replicates; results are NOT pooled across Projects.

Write one synthesis for a researcher reviewing the whole batch:

1. **Results across the batch, in reasonable detail.** What each Project
   found, and — more importantly — where the Projects agree, where they
   disagree, and what the batch shows taken together. Give effect directions
   and magnitudes where the narratives state them.
2. **Experimental design problems.** Call out specific Projects whose design
   looks compromised: too few replicates, unbalanced or missing treatment
   groups, inconsistent factors or counting regions between replicates,
   replicates that were never analyzed.
3. **Heavy fly loss.** Name any Project or replicate where a large share of
   flies was excluded or flagged (low movement, quality cutoffs), give the
   numbers the narratives report, and say what it implies for trusting that
   Project's result.

Do not itemize minor per-Project or per-experiment detail — that lives in the
Project narratives themselves. Prefer plain prose over bullet soup. Where the
source narratives are silent on something, say so rather than inferring it.
Do not perform new analysis: you are summarizing what the pipeline already
computed."""


#: A runaway walk is a mis-clicked home directory, not a Batch. The cap is far
#: above any real batch (a 200-Project batch visits a few hundred directories)
#: and exists so pointing the Hub at ``/`` cannot hang it.
MAX_WALKED_DIRECTORIES = 20000

#: Never descended into: caches and anything hidden. Everything else is
#: decided structurally — a denylist of output-directory names would misfire
#: on a grouping folder that happens to be called ``figures``.
_SKIP_DIRNAMES = {"__pycache__"}


@dataclass(frozen=True)
class BatchMember:
    """One Project a Batch Run can target, with its layout already read.

    *key* is the identity (POSIX path relative to the Batch root);
    *experiments* holds every experiment-shaped subdirectory, healthy or
    Blocked (ADR-0011) — blocked is a property of the Experiment Directory,
    never of the Member, so a Member with four healthy replicates and one
    blocked one runs the four.
    """

    key: str
    directory: str
    experiments: tuple = ()
    has_report: bool = False

    @property
    def usable(self) -> tuple:
        return tuple(e for e in self.experiments if e.usable)

    @property
    def blocked(self) -> tuple:
        return tuple(e for e in self.experiments if e.blocked)

    @property
    def runnable(self) -> bool:
        """False when nothing in it can be analyzed — the run would only
        produce a failure, so such a Member starts unchecked."""
        return bool(self.usable)

    def summary(self) -> str:
        text = f"{len(self.usable)}/{len(self.experiments)} replicates"
        if self.blocked:
            text += f", {len(self.blocked)} blocked"
        return text


def project_kind(directory) -> tuple[str, tuple]:
    """Classify *directory* as a Batch Member candidate.

    Returns ``(kind, experiments)`` where kind is:

    ``"project"``
        ``project.yaml`` and at least one *configured* replicate — certainly a
        Project. The walk prunes here.
    ``"unconfirmed"``
        ``project.yaml`` and experiment-shaped children, but not one the run
        could use — every replicate is blocked. It is probably a Project
        needing repair, but a grouping folder holding one junk subdirectory
        (a ``template/`` with a config, an exported workbook) looks identical,
        so the walk descends first and only calls it a Member if no real
        Project turns up below (ADR-0011).
    ``"marker"``
        ``project.yaml`` and nothing experiment-shaped at all: a grouping
        folder someone dropped a marker into. Walk through it.
    ``""``
        not a Project.
    """
    if not prj.is_project_dir(directory):
        return "", ()
    experiments = tuple(layout.experiments_in(directory))
    if any(e.usable for e in experiments):
        return "project", experiments
    if experiments:
        ## Configured-but-unusable is not strong enough to prune on: a
        ## grouping folder with one ``template/tracking_config.yaml`` looks
        ## exactly like a Project whose replicates are all blocked, and
        ## pruning there hides every real Project beneath it.
        return "unconfirmed", experiments
    return "marker", ()


def _member(path, root, experiments) -> BatchMember:
    return BatchMember(
        key=os.path.relpath(path, root).replace(os.sep, "/"),
        directory=path,
        experiments=tuple(experiments),
        has_report=_has_report(path),
    )


class _Walk:
    """One recursive discovery pass, with its budget and cycle guard.

    Depth-first and *decide-after-descending* for the ambiguous case, so a
    stray ``project.yaml`` at a grouping level can never hide the Projects
    beneath it — the exact mistake recursion exists to tolerate.
    """

    def __init__(self, root) -> None:
        self.root = os.path.abspath(str(root))
        self.members: list = []
        self.skipped: list = []
        self.truncated = False
        self._seen: set[str] = set()
        self._budget = MAX_WALKED_DIRECTORIES

    def key(self, path) -> str:
        return os.path.relpath(path, self.root).replace(os.sep, "/")

    def note(self, path, why: str) -> None:
        self.skipped.append((self.key(path), why))

    def run(self) -> dict:
        kind, experiments = project_kind(self.root)
        if kind == "project":
            ## A Project is never also a Batch (ADR-0009): its subdirectories
            ## are its Experiments, not Members.
            return self.result()
        ## The root's own tracking_config.yaml does not stop the walk either:
        ## a folder can be both a stray experiment directory and the place the
        ## user keeps their projects.
        self.descend(self.root)
        return self.result()

    def result(self) -> dict:
        self.members.sort(key=lambda m: m.key)
        return {"members": self.members, "skipped": sorted(self.skipped),
                "truncated": self.truncated}

    def descend(self, directory) -> int:
        """Walk *directory*'s children; returns how many Members were found.

        A caller deciding "no Projects below, so this IS the Project" must
        check :attr:`truncated` first — a budget-exhausted descent found
        nothing because it never looked.
        """
        if self._budget <= 0:
            self.truncated = True
            return 0
        self._budget -= 1
        try:
            real = os.path.realpath(directory)
        except OSError:
            return 0
        if real in self._seen:
            return 0
        self._seen.add(real)

        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError as err:
            ## A directory nobody can read may hold a whole Project. Silently
            ## pruning it would report the batch as smaller than it is.
            self.note(directory, f"cannot be listed ({err.strerror or err})")
            return 0

        found = 0
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name in _SKIP_DIRNAMES:
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    if entry.is_symlink() and entry.is_dir():
                        ## A link into an archive share would double-run its
                        ## experiments, and a link to an ancestor is a cycle.
                        self.note(entry.path, "symlinked directory — not "
                                              "followed")
                    continue
            except OSError:
                self.note(entry.path, "cannot be read")
                continue
            found += self.visit(entry.path)
        return found

    def visit(self, path) -> int:
        kind, experiments = project_kind(path)
        if kind == "project":
            self.members.append(_member(path, self.root, experiments))
            return 1                              # prune: a Project is a leaf
        if kind == "unconfirmed":
            below = self.descend(path)
            if below or self.truncated:
                ## Real Projects live under it, so the marker was a grouping
                ## folder with a junk subdirectory — say so and keep them.
                self.note(path, f"has {prj.PROJECT_FILENAME} and "
                                f"{len(experiments)} folder(s) nothing can "
                                "run; treated as a folder of projects")
                return below
            self.members.append(_member(path, self.root, experiments))
            return 1
        if kind == "marker":
            below = self.descend(path)
            ## Always reported, either way: "I marked that folder as a
            ## Project — why isn't it in the list?" is the question this
            ## rule creates, and it deserves an answer in the log.
            self.note(path, f"has {prj.PROJECT_FILENAME} but no experiment "
                            "directory" + (f"; {below} project(s) found "
                                           "inside it instead" if below else ""))
            return below
        if layout.has_config(path):
            ## An Experiment Directory: its children are data/, analysis/,
            ## qc/ — never Projects. But a stray tracking_config.yaml at a
            ## grouping level looks identical, so descend first and stop only
            ## if nothing turns up: an unconditional stop here hid every
            ## Project below one stray file, exactly as the project.yaml
            ## marker did.
            below = self.descend(path)
            if below:
                self.note(path, f"has {prj.CONFIG_FILENAME} and "
                                f"{below} project(s) inside it")
            return below
        return self.descend(path)


def _has_report(project_dir) -> bool:
    try:
        return any(name.lower().endswith("_report.pdf")
                   for name in os.listdir(project_dir))
    except OSError:
        return False


def discover(root) -> dict:
    """Everything one walk of *root* found.

    ``{"members": [...], "skipped": [(key, why)], "truncated": bool}`` — the
    Members in run order, every directory the walk dropped and why, and
    whether the walk hit its budget. The single source of truth for what a
    Batch contains: the table, the preflight, and the run all read it, so what
    the user was shown is what runs. Cheap by construction — directory
    listings only, no Project loads and no YAML parsing.
    """
    return _Walk(root).run()


def discover_members(root) -> list:
    """Every Project under *root*, in run order (by relative-path key)."""
    return discover(root)["members"]


def batch_project_names(path) -> list[str]:
    """The Members' keys, in the order a Batch Run visits them.

    A key is the Project's path relative to the Batch root, so a top-level
    Project is still its bare folder name and every stored designation, sheet
    row, and API call written before ADR-0011 still resolves.
    """
    return [member.key for member in discover_members(path)]


def member_directory(root, key) -> str:
    """Absolute directory of Member *key* in Batch *root*.

    A key comes from the walk today, but this is the public key→directory
    resolver and the Removal Sheet's project column is hand-typed, so an
    escaping key is refused rather than resolved.
    """
    parts = [part for part in str(key).split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts) or os.path.isabs(str(key)):
        raise ValueError(f"{key!r} is not a member of this batch")
    return os.path.join(os.path.abspath(str(root)), *parts)


def is_batch_dir(path) -> bool:
    """A Batch is structural: at least one Project lies somewhere beneath.

    A Project is never also a Batch — its subdirectories are its Experiments,
    so a nested ``project.yaml`` does not turn it into one. Deliberately the
    SAME walk the table and the run use: an earlier short-circuit that asked
    ``is_project_dir`` at the root disagreed with the walk's stricter test, so
    one stray ``project.yaml`` at a batch root emptied the whole panel.
    """
    return bool(discover(path)["members"])


def nested_batch_files(root) -> list[str]:
    """``batch.yaml`` files below *root* that this run ignores.

    Recursive discovery means a grouping folder can be a Batch in its own
    right and carry its own designation. Only the selected Batch's file
    governs — ADR-0009's resolution is already three steps and a fourth that
    depended on where the user clicked would be unmemorable — so these are
    named rather than silently overridden (ADR-0011).
    """
    root = os.path.abspath(str(root))
    found: list[str] = []
    for member in discover_members(root):
        directory = os.path.dirname(member.directory)
        while len(directory) > len(root):
            candidate = os.path.join(directory, BATCH_FILENAME)
            relative = os.path.relpath(candidate, root).replace(os.sep, "/")
            if os.path.isfile(candidate) and relative not in found:
                found.append(relative)
            directory = os.path.dirname(directory)
    return sorted(found)


def load_batch_file(path) -> dict:
    """The parsed ``batch.yaml`` of Batch *path* as
    ``{"script": str | None, "project_scripts": list[dict]}``.

    Lenient like ``Project.__init__``'s scripts parse: a missing file, a
    malformed document, or a bad block yields empty sections rather than an
    exception — one bad block must not take down a whole Batch Run.
    """
    result: dict = {"script": None, "project_scripts": []}
    file_path = os.path.join(str(path), BATCH_FILENAME)
    if not os.path.isfile(file_path):
        return result
    try:
        with open(file_path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:  # noqa: BLE001
        return result
    if not isinstance(data, dict):
        return result
    name = data.get("script")
    if isinstance(name, str) and name.strip():
        result["script"] = name.strip()
    raw = data.get("project_scripts") or []
    if isinstance(raw, list):
        result["project_scripts"] = [
            dict(item) for item in raw
            if isinstance(item, dict) and item.get("name")]
    return result


def save_batch_designation(path, script_name: str | None) -> None:
    """Persist the designated Project Script name in ``batch.yaml``.

    The lazy-marker rule: ``None`` (the shipped default, the Report
    Pipeline) never CREATES the file — it only clears the ``script:`` key of
    an existing one. Unknown keys are preserved; the write is atomic.
    """
    from .io_utils import atomic_write_text

    file_path = os.path.join(str(path), BATCH_FILENAME)
    exists = os.path.isfile(file_path)
    if script_name is None and not exists:
        return
    payload: dict = {}
    if exists:
        try:
            with open(file_path, encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
        except Exception:  # noqa: BLE001
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
    if script_name is None:
        payload.pop("script", None)
    else:
        payload["script"] = script_name
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    atomic_write_text(file_path, lambda f: f.write(text))


def resolve_designated_script(name: str | None, central_scripts: list[dict],
                              project) -> tuple[dict | None, str, str | None]:
    """Resolve the designated Project Script *name* for *project*.

    Order (ADR-0009): the Batch's central ``project_scripts:``, then the
    Project's own ``scripts:``, then the built-ins. Returns
    ``(script, source, note)`` where *note* explains a conditionally dropped
    step; ``(None, "", None)`` when the name resolves nowhere.

    ``None`` means "no designation": each Project runs its OWN default
    script (ADR-0009 amendment). Every ``project.yaml`` is created with one,
    so there is no built-in fallback here — a Project whose ``scripts:`` is
    empty does not run, and says so.
    """
    from .script_editor.project_actions import (
        DEFAULT_PROJECT_SCRIPT_NAME,
        REPORT_PIPELINE,
        STANDARD_PIPELINE,
        report_pipeline_for,
    )

    if not name:
        ## The seeded default by name, else the first authored script — a
        ## Project whose default was renamed still runs its own pipeline.
        own = project.find_script(DEFAULT_PROJECT_SCRIPT_NAME)
        if own is None:
            own = project.scripts[0] if project.scripts else None
        if own is None:
            return None, "", None
        return own, "project.yaml scripts", None
    for script in central_scripts:
        if str(script.get("name", "")).strip() == name:
            return script, "batch.yaml project_scripts", None
    own = project.find_script(name)
    if own is not None:
        return own, "project.yaml scripts", None
    if name == REPORT_PIPELINE["name"]:
        script, note = report_pipeline_for(project)
        return script, "built-in", note
    if name == STANDARD_PIPELINE["name"]:
        return STANDARD_PIPELINE, "built-in", None
    return None, "", None


def project_narrative_path(batch_dir, name: str) -> str:
    """Where Project *name*'s own AI narrative lives, if it has one."""
    from .ai.narrative import AI_NARRATIVE_FILENAME

    return os.path.join(str(batch_dir), name, "analysis",
                        AI_NARRATIVE_FILENAME)


def batch_narrative_path(batch_dir) -> str:
    return os.path.join(str(batch_dir), BATCH_NARRATIVE_FILENAME)


def generate_batch_narrative(batch_dir, provider: str,
                             model: str | None = None,
                             project_names: list[str] | None = None,
                             ensure_projects: bool = True,
                             log=print) -> str:
    """Synthesize the Projects' AI narratives into one Batch narrative.

    Reads each Project's ``analysis/ai_narrative.md`` and asks *provider* for
    a batch-level summary, written to ``batch_ai_narrative.md`` at the Batch
    root. Returns that path.

    With *ensure_projects* a Project that has no narrative gets one generated
    first. That is the normal case rather than the exception: the default
    ``batch`` script rebuilds each Combined Analysis, and that deletes the
    Project's narrative (ADR-0004), so straight after a Batch Run there is
    usually nothing left to read. Each generation is an extra provider call
    and is logged as one.

    A Project that cannot produce a narrative is named and skipped — the
    Batch narrative describes the Projects it could actually read, and says
    which it could not.
    """
    from .ai.base import AISummaryError
    from .ai.payload import SummaryPayload

    root = os.path.abspath(str(batch_dir))
    names = (list(project_names) if project_names is not None
             else batch_project_names(root))
    pieces: list[tuple[str, str]] = []
    generated: list[str] = []
    failed: list[str] = []

    for name in names:
        path = project_narrative_path(root, name)
        if not os.path.isfile(path) and ensure_projects:
            log(f"[{name}] no narrative yet — generating one first…")
            try:
                project = prj.Project(os.path.join(root, name))
                project.generate_ai_summary(provider, model=model)
                generated.append(name)
            except Exception as err:  # noqa: BLE001
                failed.append(f"{name}: {err}")
                log(f"[{name}] could not generate a narrative: {err}")
                continue
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read().strip()
        except OSError:
            failed.append(f"{name}: no {os.path.basename(path)}")
            continue
        if text:
            pieces.append((name, text))

    if not pieces:
        raise AISummaryError(
            "No Project narratives to summarize"
            + (f" ({'; '.join(failed)})" if failed else "")
            + ". Generate a narrative in at least one Project first.")

    body = "\n\n".join(
        f"===== PROJECT: {name} =====\n{text}" for name, text in pieces)
    if failed:
        body += ("\n\n===== PROJECTS WITHOUT A NARRATIVE =====\n"
                 + "\n".join(failed))

    from .ai import get_summarizer

    summarizer = get_summarizer(provider, model=model)
    log(f"Requesting the batch narrative from {summarizer.display_name} "
        f"({summarizer.model}) over {len(pieces)} Project narrative(s)…")
    text = summarizer.summarize(SummaryPayload(text=body),
                                BATCH_NARRATIVE_PROMPT)
    path = write_batch_narrative(
        root, text, summarizer.display_name, summarizer.model,
        included=[name for name, _ in pieces], missing=failed,
        regenerated=generated)
    log(f"Saved: {path}")
    return path


def write_batch_narrative(batch_dir, text: str, provider: str, model: str,
                          included: list[str], missing: list[str] | None = None,
                          regenerated: list[str] | None = None) -> str:
    """Write the Batch narrative as Markdown; returns the path.

    The front matter names which Projects the prose is actually based on: a
    synthesis that silently skipped half the batch would read exactly like
    one that covered it.
    """
    from datetime import datetime as _dt

    stamp = _dt.now().strftime("%Y-%m-%d %H:%M")
    path = batch_narrative_path(batch_dir)
    lines = [
        f"# Batch AI narrative — {os.path.basename(os.path.normpath(str(batch_dir)))}",
        "",
        "- **Level:** Batch (independent Projects, not pooled)",
        f"- **Generated:** {stamp}",
        f"- **Model:** {provider} {model}",
        f"- **Projects summarized ({len(included)}):** {', '.join(included)}",
    ]
    if regenerated:
        lines.append(f"- **Project narratives regenerated for this run:** "
                     f"{', '.join(regenerated)}")
    if missing:
        lines.append(f"- **Projects with no narrative (excluded):** "
                     f"{'; '.join(missing)}")
    lines += [
        "",
        "> Written by an AI model from the Projects' own AI narratives. A "
        "Batch never pools results across Projects — each keeps its own "
        "design — so this synthesizes their separate findings rather than "
        "combining their data.",
        "",
        "---",
        "",
        text.strip(),
        "",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def normalize_member_key(value) -> str:
    """A sheet's ``project`` cell as a Member key.

    Sheets are written by hand, so a cell may carry a backslash separator, a
    leading ``./``, a trailing slash, a doubled separator, or an interior
    ``/./`` — all of them name the same Member, and the scope test and the
    write have to agree about which.
    """
    text = str(value or "").strip().replace("\\", "/")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    return "/".join(parts)


def fold_member_key(value) -> str:
    """A Member key as the filesystem compares it: case-folded where the
    filesystem is case-insensitive, with ``/`` separators throughout.

    ``os.path.normcase`` alone is not enough: on Windows it also turns ``/``
    into ``\\``, so a prefix test against ``key + "/"`` never matched and
    every row of a scoped Removal Sheet was skipped there.
    """
    return os.path.normcase(normalize_member_key(value)).replace("\\", "/")


def normalize_sheet_rows(rows) -> list:
    """Rewrite each row's ``project`` cell into a Member key.

    The scope test and the write must resolve the same cell the same way: a
    Windows-authored ``Sept2026\\ProjA`` that scopes in and then resolves to
    nothing is worse than one that never scoped in at all.
    """
    for row in rows:
        if getattr(row, "project", ""):
            row.project = normalize_member_key(row.project)
    return list(rows)


def row_member_key(row, members) -> str:
    """Which Member a sheet row addresses, given the known *members*.

    A hand-written sheet does not always put the whole path in the ``project``
    cell — ``project=Sept2026, experiment=ProjA/Rep1`` names the same
    experiment as ``project=Sept2026/ProjA, experiment=Rep1``. Scoping on the
    project cell alone let the second spelling slip past the filter and write
    into a Member that was not running, so the row's full path is matched
    against the longest Member key that prefixes it.
    """
    folded = fold_member_key(
        "/".join(part for part in (getattr(row, "project", ""),
                                   getattr(row, "experiment", "")) if part))
    best = ""
    for key in members:
        candidate = fold_member_key(key)
        if (folded == candidate or folded.startswith(candidate + "/")) \
                and len(candidate) > len(best):
            best = candidate
    return best


def scope_sheet_rows(rows, members) -> tuple[list, list]:
    """Split *rows* into (for these Members, for anyone else).

    Unchecking a Member means "do not touch this Project" — and with recursive
    discovery the walk surfaces Projects the user may never have known were
    there, so writing into one they excluded is the ADR-0010 failure mode one
    step removed (ADR-0011).
    """
    if members is None:
        return list(rows), []
    ## normcase, because on Windows and macOS 'sept2026/proja' opens the same
    ## directory as 'Sept2026/ProjA': scoping a row out on the very filesystem
    ## where its path resolves would silently drop a declaration.
    wanted = {fold_member_key(m) for m in members}
    keep, skip = [], []
    for row in rows:
        ## Matched on the row's whole path, not its project cell: the two
        ## columns are one path split at a place the author chose.
        key = row_member_key(row, wanted)
        ## Scoping is asked for (members is not None), so a row must name one
        ## of them. A blank project cell used to pass as "the root itself" —
        ## the Project-root sheet contract — but at a Batch root that contract
        ## does not apply, and it let a row write outside every checked Member.
        (keep if key else skip).append(row)
    return keep, skip


def apply_removal_sheet(batch_dir, log=print, members=None) -> dict:
    """Apply the Batch's Removal Sheet, if it has one (ADR-0010).

    *members* restricts the write to those Member keys — the ones actually
    running (ADR-0011); ``None`` applies every row. Never fatal: an unreadable
    sheet is reported and the run continues, and a row naming a project,
    experiment or region that does not exist is counted in the summary rather
    than aborting ten Projects' worth of work.
    """
    root = os.path.abspath(str(batch_dir))
    path = removals.find_sheet(root)
    if path is None:
        return {"results": [], "written": [], "counts": {}, "sheet": None}
    try:
        rows = normalize_sheet_rows(removals.read_sheet(path))
        rows, out_of_scope = scope_sheet_rows(rows, members)
        if out_of_scope:
            log(f"[removals] {len(out_of_scope)} row(s) skipped — they name "
                "projects that are not in this run")
        if not rows:
            return {"results": [], "written": [], "counts": {}, "sheet": path,
                    "skipped": len(out_of_scope)}
        result = removals.apply_sheet(root, rows, sheet_path=path, log=log)
        result["skipped"] = len(out_of_scope)
        return result
    except Exception as err:  # noqa: BLE001
        log(f"[removals] {os.path.basename(path)} could not be applied: {err}")
        return {"results": [], "written": [], "counts": {}, "sheet": path,
                "error": str(err)}


def preview_removal_sheet(batch_dir, members=None) -> dict:
    """What applying the sheet WOULD do — read-only (ADR-0011 preflight).

    Runs the same matching as :func:`apply_removal_sheet` against a copy of
    each experiment's declaration, so the preview and the write cannot
    disagree, and writes nothing: selecting a Batch reports, it never applies
    (ADR-0010).
    """
    root = os.path.abspath(str(batch_dir))
    path = removals.find_sheet(root)
    if path is None:
        return {"sheet": None, "results": [], "counts": {}, "skipped": 0}
    try:
        rows = normalize_sheet_rows(removals.read_sheet(path))
    except Exception as err:  # noqa: BLE001
        return {"sheet": path, "results": [], "counts": {}, "skipped": 0,
                "error": str(err)}
    rows, out_of_scope = scope_sheet_rows(rows, members)
    results = removals.plan_sheet(root, rows)
    counts: dict = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    return {"sheet": path, "results": results, "counts": counts,
            "skipped": len(out_of_scope)}


def run_batch(batch_dir, script_name: str | None = None,
              project_names: list[str] | None = None,
              log=print, apply_removals: bool = True) -> dict:
    """One Batch Run: the designated Project Script in every Project of
    *batch_dir* — continue-on-error, per-Project log prefixes.

    Returns ``{member_key: 'ok' | error message}``, keyed by each Project's
    path relative to *batch_dir* (ADR-0011). A Batch Run never creates or
    upgrades a ``project.yaml``. *script_name* ``None`` falls back to the
    ``batch.yaml`` designation, and with no designation each Project runs
    its own default script. *project_names* restricts the run to that
    (checked) subset; *apply_removals* False declines the Removal Sheet for
    this run without touching it or any standing declaration.
    """
    from .script_editor.project_actions import run_project_script

    root = os.path.abspath(str(batch_dir))
    meta = load_batch_file(root)
    if script_name is None:
        script_name = meta["script"]

    found = discover(root)
    members = found["members"]
    by_key = {member.key: member for member in members}

    if project_names is None:
        targets = [member.key for member in members]
    else:
        wanted = {fold_member_key(n) for n in project_names}
        targets = [member.key for member in members
                   if fold_member_key(member.key) in wanted]
        ## Folded on both sides: on Windows the raw keys never equal the
        ## folded ones, and every Project that did run was also logged as
        ## not being in the Batch.
        found_keys = {fold_member_key(key) for key in by_key}
        for missing in sorted(wanted - found_keys):
            ## An API caller's stale key must not vanish silently — the Hub
            ## table only offers real Members, so this is for scripts.
            log(f"[{missing}] not a Project in this Batch — ignored")

    results: dict[str, str] = {}
    if not targets:
        log(f"No Projects to run in {root}")
        return results

    log(f"Batch Run: {len(targets)} Project(s) in {root}")

    ## Discovery is recursive (ADR-0011), so say what it found: with Projects
    ## at arbitrary depth the target list is no longer obvious from the folder
    ## that was picked.
    for key in targets:
        member = by_key[key]
        log(f"  {key} — {member.summary()}")
        for experiment in member.blocked:
            log(f"      {experiment.describe()}")

    for key, why in found["skipped"]:
        log(f"[{key}] skipped — {why}")
    if found.get("truncated"):
        ## A truncated walk that said nothing would report a partial run as a
        ## complete one.
        log(f"[batch] WARNING: stopped after {MAX_WALKED_DIRECTORIES} "
            "directories — this folder is larger than a batch should be, and "
            "projects deeper in it were not found.")

    for note in nested_batch_files(root):
        ## Only the selected Batch's designation governs; a sub-batch's own
        ## batch.yaml is ignored, and saying so beats wondering why the wrong
        ## script ran (ADR-0011).
        log(f"[batch] {note} ignored — only the selected batch folder's "
            "designation applies")

    ## A Removal Sheet at the Batch root is applied before anything runs, so
    ## an unattended run honours the experimenter's notes (ADR-0010). It is a
    ## writer, never an overlay: the rows are stamped into each experiment's
    ## removed_regions.yaml and nothing reads the sheet at analysis time.
    ## Scoped to the Members actually running: unchecking one means "do not
    ## touch this Project" (ADR-0011).
    if apply_removals:
        apply_removal_sheet(root, log=log, members=targets)
    elif removals.find_sheet(root) is not None:
        log("[removals] sheet found but declined for this run — nothing "
            "written")

    for i, name in enumerate(targets, 1):
        project_dir = by_key[name].directory

        def plog(msg, _n=name):
            log(f"[{_n}] {msg}")

        log(f"[{i}/{len(targets)}] {name}")
        try:
            project = prj.Project(project_dir)
        except Exception as err:  # noqa: BLE001
            ## A raising constructor (design mismatch, empty designless
            ## Project) is that Project's failure, never the run's.
            results[name] = f"{type(err).__name__}: {err}"
            plog(f"FAILED to load: {err}")
            continue
        script, source, note = resolve_designated_script(
            script_name, meta["project_scripts"], project)
        if script is None:
            results[name] = (
                f"no Project Script named '{script_name}' (batch.yaml "
                f"project_scripts, project.yaml scripts, or built-ins)"
                if script_name else
                "project.yaml defines no Project Script — add one in the "
                "Script Editor (new Projects are created with a default "
                "one)")
            plog(f"SKIPPED — {results[name]}")
            continue
        if note:
            plog(note)
        plog(f"running '{script.get('name')}' (from {source})…")
        try:
            run_project_script(script, project, log_cb=plog)
            results[name] = "ok"
        except Exception as err:  # noqa: BLE001
            ## str() of a bare exception can be empty — keep the type name so
            ## the summary always has something to say.
            results[name] = str(err) or type(err).__name__
            plog(f"FAILED: {results[name]}")

    ok = sum(1 for v in results.values() if v == "ok")
    log(f"Batch Run complete: {ok}/{len(targets)} Project(s) succeeded.")
    if ok < len(targets):
        log("Failed:")
        for name, msg in results.items():
            if msg != "ok":
                headline = msg.splitlines()[0] if msg else "<no message>"
                log(f"  {name}: {headline}")
    return results
