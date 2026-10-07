"""Experimenter-declared region removal (ADR-0010).

A **Removed Region** is a tracking region the experimenter declares out of the
analysis, with a free-text reason — death, escape, an empty well. Only the
experimenter can know it, so it is entered by hand and never inferred from the
data. Every tracker in a removed region becomes an Excluded Fly.

Two files, one authority:

* ``removed_regions.yaml`` at an Experiment Directory root is **the**
  declaration. Deliberately outside ``tracking_config.yaml``, which is a design
  specification — how a run turned out is not design.
* A **Removal Sheet** (``removed_regions.csv``/``.xlsx`` at a Batch or Project
  root) is a *writer*: applying it stamps its rows into each experiment's
  sidecar and nothing reads it at analysis time, so a Project carries its own
  removals wherever it is copied.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from .io_utils import atomic_write_text

#: The per-experiment declaration. Fixed name at the Experiment Directory root
#: — YAML, so Batch Tools' "Convert subdirectories" (which moves every
#: non-YAML top-level file into ``data/``) leaves it alone.
REMOVALS_FILENAME = "removed_regions.yaml"

#: Top-level key inside that file.
REMOVALS_KEY = "removed_regions"

#: The Removal Sheet: same stem, spreadsheet suffixes, at a Batch or Project
#: root. ``.csv`` wins when both are present.
SHEET_STEM = "removed_regions"
SHEET_SUFFIXES = (".csv", ".xlsx", ".xls")

#: What a declaration without a reason means. The experimenter can overwrite
#: it; the point is that every removal carries *something* into the audit.
DEFAULT_REASON = "Undefined"

#: Prefix marking an experimenter's removal in the ``Reason`` column, so
#: ``Undefined`` can never be mistaken for a machine verdict.
REMOVAL_PREFIX = "Removed"

#: The automatic criterion's reason string (ADR-0003).
LOW_TRANSITION_REASON = "Low transitions"

#: Paired Open Field's automatic criterion (ADR-0016).
LOW_TRACKING_REASON = "Low tracking"


# ---------------------------------------------------------------------------
# Wording for a type's automatic criterion
# ---------------------------------------------------------------------------
# An exclusion frame's attrs carry the type's own words for its criterion
# (``policy_label``, ``policy_cause``, ``policy_none``, ``policy_off``). The
# Low-Transition Exclusion predates them, so absent attrs mean its wording —
# a Valence report reads exactly as it did before a second criterion existed.

def policy_count(attrs) -> int:
    """How many flies the type's automatic criterion excluded."""
    return int(attrs.get("n_policy", attrs.get("n_low_transitions", 0)) or 0)


def policy_label(attrs) -> str:
    """Short name for a count breakdown: ``7 (4 removed, 3 low tracking)``."""
    return attrs.get("policy_label") or "low transitions"


def policy_cause(attrs) -> str:
    """Why those flies left, completing ``"3 fly(ies) …"``."""
    if attrs.get("policy_cause"):
        return attrs["policy_cause"]
    return (f"with fewer than {attrs.get('min_transitions')} transitions during "
            f"the {attrs.get('phase_label', 'Primary')} phase")


# ---------------------------------------------------------------------------
# The per-experiment sidecar
# ---------------------------------------------------------------------------

def removals_path(experiment_dir) -> str:
    return os.path.join(str(experiment_dir), REMOVALS_FILENAME)


def read_removals(experiment_dir) -> dict:
    """Declared removals for *experiment_dir* as ``{region: reason}``.

    Missing file, empty file, or a malformed one yields ``{}`` — a broken note
    must never be the thing that blocks an analysis run (ADR-0010). A region
    declared with no reason reads as :data:`DEFAULT_REASON`.
    """
    path = removals_path(experiment_dir)
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    section = loaded.get(REMOVALS_KEY, loaded)
    if not isinstance(section, dict):
        return {}
    out: dict[str, str] = {}
    for region, reason in section.items():
        name = str(region).strip()
        if not name:
            continue
        text = "" if reason is None else str(reason).strip()
        out[name] = text or DEFAULT_REASON
    return out


def write_removals(experiment_dir, removals: dict) -> str | None:
    """Persist *removals* for *experiment_dir*; an empty mapping deletes the
    file, so a sidecar never outlives the declarations it held (the rule
    :meth:`Experiment.write_run_notes` already follows for run notes).

    Returns the path written, or ``None`` when the file was removed.
    """
    path = removals_path(experiment_dir)
    cleaned = {str(region).strip(): (str(reason).strip() or DEFAULT_REASON)
               for region, reason in (removals or {}).items()
               if str(region).strip()}
    if not cleaned:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        return None
    body = yaml.safe_dump({REMOVALS_KEY: cleaned}, sort_keys=False,
                          default_flow_style=False, allow_unicode=True)
    atomic_write_text(path, lambda handle: handle.write(_SIDECAR_HEADER + body))
    return path


_SIDECAR_HEADER = (
    "# Tracking regions the experimenter removed from the analysis (ADR-0010).\n"
    "# Every tracker in a listed region is excluded from figures, summary\n"
    "# measures, statistics and the summary CSVs, and is listed with this\n"
    "# reason in the report and _Excluded.csv. Free text; 'Undefined' when\n"
    "# no reason was given.\n"
)


# ---------------------------------------------------------------------------
# Region -> tracker resolution
# ---------------------------------------------------------------------------

def name_in_region(name, region) -> bool:
    """Does tracker *name* belong to tracking region *region*?

    Tracker names are ``<region>_<object_id>`` and counter names are the bare
    region, so the match is on the underscore boundary. A plain ``startswith``
    makes ``T_1`` swallow ``T_10_0`` — the well next door.
    """
    name, region = str(name), str(region)
    return name == region or name.startswith(region + "_")


def expand_regions(regions, tracker_names) -> dict:
    """``{region: [tracker names]}`` for every region in *regions*.

    Regions matching nothing map to an empty list; the caller reports those as
    unmatched declarations rather than dropping them silently.
    """
    names = [str(n) for n in tracker_names]
    return {str(region): [n for n in names if name_in_region(n, region)]
            for region in regions}


# ---------------------------------------------------------------------------
# The Removal Sheet
# ---------------------------------------------------------------------------

#: Header spellings accepted for each field (compared case- and space-
#: insensitively), so a sheet written by hand does not have to guess.
_SHEET_COLUMNS = {
    "project": ("project", "projectname", "projectdir", "projectdirectory"),
    "experiment": ("experiment", "experimentname", "replicate", "replicatename",
                   "experimentdir", "experimentdirectory"),
    "region": ("region", "trackingregion", "well", "tube"),
    "reason": ("reason", "note", "notes", "comment", "comments"),
}


@dataclass
class SheetRow:
    """One row of a Removal Sheet, as read."""

    index: int          # 1-based row number as a spreadsheet shows it
    project: str
    experiment: str
    region: str
    reason: str


@dataclass
class RowResult:
    """What applying one :class:`SheetRow` did."""

    row: SheetRow
    #: applied | already declared | conflict | unknown project |
    #: unknown experiment | unknown region | incomplete
    status: str
    detail: str = ""

    @property
    def wrote(self) -> bool:
        return self.status == "applied"

    def describe(self) -> str:
        where = "/".join(p for p in (self.row.project, self.row.experiment,
                                     self.row.region) if p)
        line = f"row {self.row.index} {where or '(blank)'}: {self.status}"
        return f"{line} — {self.detail}" if self.detail else line


def find_sheet(root) -> str | None:
    """The Removal Sheet at *root*, or ``None``. ``.csv`` beats ``.xlsx``.

    Matched case-insensitively: "Save As" in Excel routinely produces
    ``Removed_Regions.csv``, and an exact-case test made that sheet invisible
    on Linux — the preflight then reported the batch simply had no sheet, and
    the run applied nothing with no line anywhere saying so.
    """
    try:
        entries = sorted(os.listdir(str(root)))
    except OSError:
        return None
    for suffix in SHEET_SUFFIXES:
        wanted = (SHEET_STEM + suffix).lower()
        for entry in entries:
            path = os.path.join(str(root), entry)
            if entry.lower() == wanted and os.path.isfile(path):
                return path
    return None


def read_sheet(path) -> list[SheetRow]:
    """Parse a Removal Sheet into rows.

    Headers are matched case-insensitively and ignoring spaces/underscores;
    ``project`` and ``reason`` are optional (a Project-root sheet needs no
    project column, and a missing reason becomes :data:`DEFAULT_REASON`).
    Raises ``ValueError`` when the required columns are absent — an
    unparseable sheet is a mistake worth stopping for, unlike an unmatched row.
    """
    import pandas as pd

    if str(path).lower().endswith((".xlsx", ".xls")):
        frame = pd.read_excel(path, dtype=str)
    else:
        frame = pd.read_csv(path, dtype=str)

    def canon(header) -> str:
        return "".join(ch for ch in str(header).lower()
                       if ch.isalnum())

    mapping: dict[str, str] = {}
    for column in frame.columns:
        key = canon(column)
        for field, spellings in _SHEET_COLUMNS.items():
            if key in spellings and field not in mapping:
                mapping[field] = column
    missing = [f for f in ("experiment", "region") if f not in mapping]
    if missing:
        raise ValueError(
            f"{os.path.basename(str(path))} is missing the "
            f"{', '.join(missing)} column(s). Expected headers: "
            "project, experiment, region, reason.")

    def cell(row, field) -> str:
        column = mapping.get(field)
        if column is None:
            return ""
        value = row.get(column)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        text = str(value).strip()
        return "" if text.lower() == "nan" else text

    rows: list[SheetRow] = []
    for offset, (_, raw) in enumerate(frame.iterrows(), start=2):
        row = SheetRow(index=offset,
                       project=cell(raw, "project"),
                       experiment=cell(raw, "experiment"),
                       region=cell(raw, "region"),
                       reason=cell(raw, "reason") or DEFAULT_REASON)
        if not (row.project or row.experiment or row.region):
            continue                      # a blank spacer row
        rows.append(row)
    return rows


def is_readable_declaration(experiment_dir) -> bool:
    """Can the sidecar at *experiment_dir* be read as a declaration?

    ``read_removals`` deliberately returns ``{}`` for a malformed file so a
    broken note never blocks an analysis (ADR-0010). Writing on top of that
    ``{}`` would be a different act entirely: it would *delete* whatever the
    experimenter wrote, which is the audit trail this feature exists to keep.
    """
    path = removals_path(experiment_dir)
    if not os.path.isfile(path):
        return True                          # nothing to lose
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError):
        return False
    if loaded is None:
        return True                          # empty file
    if not isinstance(loaded, dict):
        return False
    section = loaded.get(REMOVALS_KEY, loaded)
    return section is None or isinstance(section, dict)


def _canonical(path) -> str:
    """One spelling per directory: resolved, and case-folded where the
    filesystem is (Windows/macOS), so the same folder is never two keys."""
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(root, path) -> bool:
    """Is *path* within *root*? Guards a hand-edited sheet from writing
    outside the tree it was found in."""
    root_real = os.path.realpath(str(root))
    target = os.path.realpath(str(path))
    try:
        return os.path.commonpath([root_real, target]) == root_real
    except ValueError:              # different drives on Windows
        return False


def plan_sheet(root, rows) -> list:
    """What applying *rows* under *root* would do — writes nothing.

    The preflight's preview and the run's write share this one evaluation
    (ADR-0011), so what the user was shown cannot disagree with what happens.
    """
    results, _pending = _evaluate_sheet(root, rows)
    return results


def _evaluate_sheet(root, rows) -> tuple:
    """Match every row against the tree; returns ``(results, pending)``.

    Pure: per-experiment declarations accumulate in memory and are handed
    back for the caller to write — or to throw away, for a preview.
    """
    from . import project as prj

    root = str(root)
    results: list[RowResult] = []
    # region declarations gathered per experiment directory, written once at
    # the end so a sheet touching one experiment 20 times writes one file.
    pending: dict[str, dict] = {}
    config_regions: dict[str, set] = {}

    for row in rows:
        if not row.experiment or not row.region:
            results.append(RowResult(row, "incomplete",
                                     "experiment and region are required"))
            continue

        ## One seam normalizes the cell, so scoping and resolution cannot
        ## disagree: a Windows-authored 'Sept2026\\ProjA' resolved fine on
        ## Windows and was reported "unknown project" on the Linux box that
        ## actually ran the batch.
        from .batch import normalize_member_key

        project = normalize_member_key(row.project) if row.project else ""
        project_dir = os.path.join(root, project) if project else root
        if project and not _inside(root, project_dir):
            ## A sheet is a hand-edited spreadsheet, and os.path.join honours
            ## both '../' and an absolute path: without this a stray cell
            ## writes removed_regions.yaml anywhere on disk that happens to
            ## hold a tracking_config.yaml.
            results.append(RowResult(
                row, "unknown project",
                f"{row.project!r} is outside {os.path.basename(root)}"))
            continue
        if project and not os.path.isdir(project_dir):
            results.append(RowResult(row, "unknown project",
                                     f"no directory {row.project!r} under {root}"))
            continue

        experiment_dir = os.path.join(project_dir, row.experiment)
        if not _inside(root, experiment_dir):
            results.append(RowResult(
                row, "unknown experiment",
                f"{row.experiment!r} is outside {os.path.basename(root)}"))
            continue
        if not prj.is_experiment_dir(experiment_dir):
            results.append(RowResult(
                row, "unknown experiment",
                f"no tracking_config.yaml in {os.path.relpath(experiment_dir, root)}"))
            continue

        known = config_regions.get(experiment_dir)
        if known is None:
            known = _config_regions(experiment_dir)
            config_regions[experiment_dir] = known
        if known and row.region not in known:
            results.append(RowResult(
                row, "unknown region",
                f"{row.region} is not a tracking region of {row.experiment}"))
            continue

        ## Keyed by the resolved path: two spellings of one directory
        ## ('P1/Rep1' and './P1/Rep1') used to accumulate two independent
        ## declaration sets, and the second write threw away the first's
        ## regions while both rows reported "applied".
        experiment_dir = _canonical(experiment_dir)
        declared = pending.get(experiment_dir)
        if declared is None:
            declared = dict(read_removals(experiment_dir))
            pending[experiment_dir] = declared

        existing = declared.get(row.region)
        if existing is None:
            declared[row.region] = row.reason
            results.append(RowResult(row, "applied"))
        elif existing == row.reason:
            results.append(RowResult(row, "already declared"))
        else:
            results.append(RowResult(
                row, "conflict",
                f"kept {existing!r}, sheet says {row.reason!r} "
                "(edit the removals window to change it)"))


    return results, pending


def apply_sheet(root, rows=None, *, sheet_path=None, log=None) -> dict:
    """Write a Removal Sheet's rows into each experiment's sidecar.

    *root* is a Batch root (rows name a project) or a Project root (they do
    not). Merging is additive and the standing declaration wins: a region
    already declared is never rewritten, and a differing reason is reported as
    a **conflict** — the sheet is re-applied on every Batch Run, so letting it
    win would keep resetting reasons refined in the window (ADR-0010).

    Returns ``{"results": [RowResult], "written": [paths], "counts": {...}}``.
    Nothing here raises for a row that matches nothing: it is reported.
    """
    if rows is None:
        sheet_path = sheet_path or find_sheet(root)
        if sheet_path is None:
            return {"results": [], "written": [], "counts": {}, "sheet": None}
        rows = read_sheet(sheet_path)

    results, pending = _evaluate_sheet(root, rows)

    written: list[str] = []
    failed: list[str] = []
    for experiment_dir, declared in pending.items():
        if declared == read_removals(experiment_dir):
            continue
        if not is_readable_declaration(experiment_dir):
            ## Merging onto a file we cannot parse would silently replace the
            ## experimenter's own declaration with the sheet's rows.
            failed.append(
                f"{os.path.basename(experiment_dir)}: its "
                f"{os.path.basename(removals_path(experiment_dir))} is not "
                "readable — fix or delete it, nothing was written")
            continue
        try:
            path = write_removals(experiment_dir, declared)
        except OSError as err:
            ## One read-only experiment directory in a batch of forty must not
            ## discard the other thirty-nine's results — and reporting
            ## "nothing written" when sidecars HAVE been rewritten is the one
            ## thing an audit trail must never do.
            ## Name the Project too — "Rep1" alone is ambiguous across a
            ## batch where every Project has one.
            parent = os.path.basename(os.path.dirname(experiment_dir))
            failed.append(
                f"{os.path.join(parent, os.path.basename(experiment_dir))}: "
                f"{err}")
            continue
        if path:
            written.append(path)

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1

    if log is not None:
        _log_results(results, counts, written, sheet_path, log)

    if failed and log is not None:
        for note in failed:
            log(f"[removals] could not write {note}")
    return {"results": results, "written": written, "counts": counts,
            "sheet": sheet_path, "failed": failed}


def _log_results(results, counts, written, sheet_path, log) -> None:
    """Report an application: the summary first (what a batch reader sees),
    then every row that did not simply apply."""
    name = os.path.basename(str(sheet_path)) if sheet_path else "removal sheet"
    total = len(results)
    log(f"[removals] {name}: {total} row(s), "
        + ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        + f" — {len(written)} file(s) written")
    for result in results:
        if result.status != "applied":
            log(f"[removals] {result.describe()}")


def _config_regions(experiment_dir) -> set:
    """Region names declared in an experiment's ``tracking_config.yaml``, or an
    empty set when the file cannot be read (then nothing is rejected — the
    analysis warns about an unmatched declaration instead)."""
    from . import project as prj

    path = os.path.join(str(experiment_dir), prj.CONFIG_FILENAME)
    try:
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return set()
    regions = config.get("tracking_regions")
    if not isinstance(regions, dict):
        return set()
    return {str(name) for name in regions}
