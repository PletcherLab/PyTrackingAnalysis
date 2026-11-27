"""Project-level script actions (ADR-0006).

Project Scripts share the experiment-script language — ``{name,
steps:[{action, params}]}`` in ``project.yaml`` ``scripts:`` — but dispatch
through this separate registry with a :class:`ProjectRunContext`, so levels
cannot mix. The only bridge to experiment level is ``run_in_experiments``,
which runs a named Experiment Script in every replicate — or, with its
optional ``only:`` list of replicate directory names, in just those — (the
Project's central ``experiment_scripts:`` first, each replicate's own
``tracking_config.yaml`` as fallback — which also absorbs the legacy
``batch`` convention), continue-on-error with a failure summary.

Every other action is a thin wrapper over an existing ``Project`` method.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .. import layout
from ..ui import Category
from .actions import Action, ParamSpec


@dataclass
class ProjectRunContext:
    """State threaded through a Project Script run."""

    project: Any = None  # pytrackinganalysis.project.Project
    log: Callable[[str], None] = lambda _msg: None
    figure: Callable[[str, Any], None] = lambda _title, _fig: None
    #: Failures collected by continue-on-error steps; reported at the end.
    failures: list = field(default_factory=list)


class ProjectScriptError(RuntimeError):
    """A Project Script cannot execute (unknown action, bad params, or a
    step's hard failure)."""


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def _exec_validate_design(_params: dict, ctx: ProjectRunContext) -> None:
    """Re-validate every replicate against the design (a fresh load, so a
    config edited since the run started is still caught)."""
    from ..project import Project

    project = Project(ctx.project.project_directory)  # raises on mismatch
    ctx.log(f"Design OK: {len(project.experiment_names)} replicate(s) "
            f"({', '.join(project.experiment_names) or 'none yet'}).")
    for warning in (*project.warnings, *project.about_warnings):
        ctx.log(f"Note: {warning}")


def _parse_only(value) -> list[str]:
    """The ``only`` param as a list of replicate names. The inspector saves a
    real list; a hand-edited yaml may hold a comma-separated string. A null
    item (yaml ``~``) is dropped rather than becoming the name "None"."""
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value
                if v is not None and str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _exec_run_in_experiments(params: dict, ctx: ProjectRunContext) -> None:
    """The bridge: run an Experiment Script in every replicate — or, with
    ``only:``, in just the named ones."""
    from .runner import load_scripts, run_script

    project = ctx.project
    name = str(params.get("script", "")).strip()
    script = project.find_experiment_script(name)
    source = "project" if script is not None else None

    ran, failures = 0, []
    targets = list(project.experiment_names)
    only = _parse_only(params.get("only"))
    if only:
        known = set(project.experiment_names)
        for missing in [n for n in only if n not in known]:
            ## A targeted name that matches nothing is loud, never silent
            ## (grill 2026-08): recorded and summarized, run continues.
            failures.append(f"{missing}: no replicate of that name")
            ctx.log(f"[{missing}] SKIPPED — no replicate of that name")
        wanted = set(only)
        targets = [n for n in project.experiment_names if n in wanted]
    for exp_name in targets:
        exp_dir = project.experiment_dir(exp_name)
        replicate_script = script
        if replicate_script is None:
            ## Fallback: the replicate's own script of that name — this is
            ## also how a legacy 'batch' parent keeps running unchanged. A
            ## malformed config is that replicate's failure, never the
            ## step's (continue-on-error).
            import os
            try:
                own = load_scripts(layout.config_path(exp_dir))
            except Exception as err:  # noqa: BLE001
                failures.append(f"{exp_name}: unreadable scripts — {err}")
                ctx.log(f"[{exp_name}] FAILED to read scripts: {err}")
                continue
            replicate_script = next(
                (s for s in own if s.get("name") == name), None)
        if replicate_script is None:
            failures.append(f"{exp_name}: no script named '{name}' "
                            f"(neither in project.yaml experiment_scripts "
                            f"nor in its tracking_config.yaml)")
            ctx.log(f"[{exp_name}] SKIPPED — no script named '{name}'")
            continue
        where = source or "replicate config"
        ctx.log(f"[{exp_name}] running '{name}' (from {where})…")
        try:
            exp = project.load_experiment(exp_name)
            run_script(
                replicate_script, project_dir=exp_dir,
                log_cb=lambda msg, _n=exp_name: ctx.log(f"[{_n}] {msg}"),
                figure_cb=lambda title, fig, _n=exp_name:
                    ctx.figure(f"{_n}: {title}", fig),
                exp=exp,
            )
            ran += 1
        except Exception as err:  # noqa: BLE001
            failures.append(f"{exp_name}: {err}")
            ctx.log(f"[{exp_name}] FAILED: {err}")
    ctx.log(f"run_in_experiments('{name}'): {ran} succeeded, "
            f"{len(failures)} failed.")
    ctx.failures.extend(failures)


def _exec_render_figures(params: dict, ctx: ProjectRunContext) -> None:
    ## Without curated specs render_figures() invents default-spec figures
    ## nobody asked for — wrong for an unattended run (ADR-0009). The guard
    ## lives in the action, so it protects the default Project Script every
    ## project.yaml carries as well as the built-in pipelines.
    import os

    from ..pubfigures import SPECS_FILENAME

    specs = os.path.join(str(ctx.project.project_directory), SPECS_FILENAME)
    if not os.path.isfile(specs):
        ctx.log(f"No {SPECS_FILENAME} — skipped (curate figures in the "
                "Plot Editor first).")
        return
    choice = str(params.get("format", "svg"))
    formats = ("svg", "pdf") if choice == "both" else (choice,)
    written = ctx.project.render_figures(formats=formats)
    ctx.log(f"Wrote {len(written)} publication figure file(s) to figures/.")


def _exec_project_report(params: dict, ctx: ProjectRunContext) -> None:
    """The Hub's Create-report button, as a step — the same three calls in
    the same order (hub ``_project_report``): analyze every replicate, pool
    the results, then render the PDF. A script action mirrors a button, so
    there are no separate run-all / build-combined actions to forget."""
    failures = ctx.project.run_all(
        make_reports=bool(params.get("reports", True)),
        skip_analyzed=bool(params.get("skip_analyzed", False)),
        log=ctx.log,
    )
    if failures:
        ## The button refuses to pool a partial run, and so does this: a
        ## report built over half-regenerated replicates is worse than none.
        raise ProjectScriptError(
            f"{len(failures)} replicate(s) failed: " + "; ".join(failures))
    result = ctx.project.build_combined_analysis()
    ctx.log(f"Combined analysis written ({len(result['written'])} files).")
    if result["missing"]:
        ctx.log("Not yet analyzed (omitted): " + ", ".join(result["missing"]))
    path = ctx.project.create_report()
    ctx.log(f"Project report: {path}")


def _exec_ai_narrative(params: dict, ctx: ProjectRunContext) -> None:
    provider = str(params.get("provider", "anthropic"))
    soft = bool(params.get("soft_fail", True))
    try:
        ctx.project.generate_ai_summary(provider)
        ctx.log("AI narrative saved — the next Project report embeds it.")
    except Exception as err:  # noqa: BLE001
        if not soft:
            raise
        ## A provider hiccup must not kill a pipeline (ADR-0006).
        ctx.log(f"AI narrative skipped ({err}).")


def _validate_run_in_experiments(params: dict, _t: str | None) -> list[str]:
    errs: list[str] = []
    if not str(params.get("script", "")).strip():
        errs.append("'Experiment script name' is required")
    only = params.get("only", "")
    if only not in ("", None) and not isinstance(only, (list, tuple, str)):
        errs.append("'Only replicates' must be replicate names "
                    "(a list, or comma-separated)")
    return errs


def _build_project_actions() -> dict[str, Action]:
    actions = [
        Action(
            key="validate_design",
            title="Validate design",
            description="Re-check every replicate against the project design; "
                        "fails the script on any mismatch.",
            category=Category.TOOLS,
            icon_name="lint",
            params=(),
            execute_fn=_exec_validate_design,
        ),
        Action(
            key="run_in_experiments",
            title="Run in experiments",
            description="Run a named Experiment Script in every replicate — "
                        "or only the ones named below — the project's "
                        "experiment_scripts first, each replicate's own "
                        "config as fallback. Continues on error; failures "
                        "are summarized at the end.",
            category=Category.ANALYZE,
            icon_name="batch",
            params=(
                ParamSpec("script", "string", "Experiment script name",
                          default="", help="Name resolved in project.yaml "
                          "experiment_scripts, then in each replicate's "
                          "tracking_config.yaml scripts."),
                ParamSpec("only", "multilist", "Only replicates",
                          default="", help="Optional replicate directory "
                          "names; blank = every replicate. A name matching "
                          "no replicate is reported in the failure summary "
                          "and the run continues."),
            ),
            validate_fn=_validate_run_in_experiments,
            execute_fn=_exec_run_in_experiments,
        ),
        Action(
            key="render_publication_figures",
            title="Render publication figures",
            description="Write the pooled publication figures to figures/ "
                        "from plot_specs.yaml (the Plot Editor's saves, "
                        "headless).",
            category=Category.PLOTS,
            icon_name="plots",
            params=(
                ParamSpec("format", "choice", "Format", default="svg",
                          choices=("svg", "pdf", "both")),
            ),
            execute_fn=_exec_render_figures,
        ),
        Action(
            key="project_report",
            title="Create / update project report",
            description="The whole Create-report button in one step: analyze "
                        "every replicate (with its own report), pool the "
                        "results into analysis/, then build "
                        "<project>_report.pdf — pooled figures, pooled + "
                        "mixed statistics, replicate table, and any saved AI "
                        "narrative. Nothing else needs to run before it.",
            category=Category.ANALYZE,
            icon_name="report",
            params=(
                ParamSpec("reports", "bool", "Create per-replicate reports",
                          default=True),
                ParamSpec("skip_analyzed", "bool", "Skip analyzed replicates",
                          default=False,
                          help="Leave off to match the Create-report button, "
                               "which always re-analyzes every replicate."),
            ),
            execute_fn=_exec_project_report,
        ),
        Action(
            key="generate_ai_narrative",
            title="Generate AI narrative",
            description="Ask an AI provider to write the project narrative "
                        "from the Combined Analysis. Soft-fails by default: "
                        "a provider error logs and the script continues.",
            category=Category.AI,
            icon_name="ai",
            params=(
                ParamSpec("provider", "choice", "Provider",
                          default="anthropic",
                          choices=("anthropic", "openai")),
                ParamSpec("soft_fail", "bool", "Continue on provider error",
                          default=True),
            ),
            execute_fn=_exec_ai_narrative,
        ),
    ]
    return {a.key: a for a in actions}


PROJECT_ACTIONS: dict[str, Action] = _build_project_actions()

#: The built-in Project Script (never written to project.yaml, so it always
#: tracks the shipped default). Zero authoring gets a complete run.
STANDARD_PIPELINE: dict = {
    "name": "Standard pipeline",
    "steps": [
        {"action": "validate_design", "params": {}},
        {"action": "project_report", "params": {}},
        {"action": "render_publication_figures", "params": {"format": "svg"}},
    ],
}

#: The report-button sequence as a built-in (ADR-0009) — the default Batch
#: designation. No validate_design gate (it would fail Projects
#: mid-migration), and the publication-figure step runs only from curated
#: specs: :func:`report_pipeline_for` applies that condition per Project, so
#: the conditionality never appears in yaml.
## Figures come AFTER the report: project_report is the one that analyzes
## the replicates, and render_publication_figures reads their saved
## summaries — the other order fails on a Project nobody has analyzed yet.
REPORT_PIPELINE: dict = {
    "name": "Report pipeline",
    "steps": [
        {"action": "project_report", "params": {}},
        {"action": "render_publication_figures", "params": {"format": "svg"}},
    ],
}

#: Actions folded into ``project_report`` when it became the whole
#: Create-report button. Scripts saved before that still name them, so they
#: are absorbed at run time rather than failing validation as unknown.
ABSORBED_ACTIONS: dict[str, str] = {
    "run_all_analyses": "project_report",
    "build_combined_analysis": "project_report",
}


def absorb_legacy_steps(steps: list[dict]) -> tuple[list[dict], list[str]]:
    """*steps* with retired actions folded into their replacement.

    Returns ``(steps, notes)``. A step whose work the script already does
    elsewhere is dropped; when it does NOT — an old "just analyze
    everything" script with no ``project_report`` — the first such step
    becomes the replacement instead, so absorbing never turns a script into
    a no-op.

    The replacement also inherits the POSITION of the first step it absorbs.
    The retired steps marked where a script did its analysis work, and later
    steps were written expecting that work to be done: leaving
    ``project_report`` at the end would run
    ``render_publication_figures`` against replicates nothing had analyzed
    yet. *notes* says what happened, for the run log.
    """
    present = {s.get("action") for s in steps if isinstance(s, dict)}
    out: list[dict] = []
    notes: list[str] = []
    #: Where the first absorbed step stood, per replacement action.
    slot: dict[str, int] = {}
    for step in steps:
        if not isinstance(step, dict):
            out.append(step)
            continue
        key = step.get("action")
        target = ABSORBED_ACTIONS.get(key)
        if target is None:
            out.append(step)
            continue
        slot.setdefault(target, len(out))
        if target in present:
            notes.append(f"'{key}' is now part of "
                         f"'{PROJECT_ACTIONS[target].title}' — step skipped.")
            continue
        notes.append(f"'{key}' has been replaced by "
                     f"'{PROJECT_ACTIONS[target].title}' — running that.")
        out.insert(slot[target], {"action": target, "params": {}})
        present.add(target)

    for target, index in slot.items():
        at = next((i for i, s in enumerate(out)
                   if isinstance(s, dict) and s.get("action") == target), None)
        if at is None or at <= index:
            continue
        out.insert(index, out.pop(at))
        notes.append(f"'{PROJECT_ACTIONS[target].title}' moved to step "
                     f"{index + 1}, where the steps it absorbed ran.")
    return out, notes


#: The Project Script every ``project.yaml`` is created with, and the one a
#: Batch Run executes when nothing else is designated — hence the name. It is
#: written into the file (unlike the built-ins) so the user can SEE the
#: default run and edit it, which is why a Batch Run needs no built-in
#: fallback: a Project with no Project Script simply does not run.
## Not the built-in's name: "Report pipeline" is a code-defined script the
## user may also pick explicitly, and the two must stay distinguishable.
DEFAULT_PROJECT_SCRIPT_NAME = "batch"


def default_project_script() -> dict:
    """A fresh copy of the default Project Script, for seeding a new
    ``project.yaml``. A copy, not the module constant: the caller writes it
    into a file the user then edits."""
    return {
        "name": DEFAULT_PROJECT_SCRIPT_NAME,
        "notes": "Created with the project, and what a Batch Run runs here "
                 "unless another script is designated. Analyzes every "
                 "replicate, pools the results, builds the project report, "
                 "then renders curated figures. Edit or replace it in the "
                 "Script Editor — a project with no script here cannot be "
                 "run from the Project card or a Batch Run.",
        "steps": [{"action": s["action"], "params": dict(s["params"])}
                  for s in REPORT_PIPELINE["steps"]],
    }


def report_pipeline_for(project) -> tuple[dict, str | None]:
    """The Report Pipeline as it will actually run on *project*.

    The publication-figure step is dropped when the Project has no
    ``plot_specs.yaml`` — nobody curated figures, and an unattended run must
    not invent default-spec ones. Returns ``(script, note)``; *note* explains
    a dropped step, or is None.
    """
    import os

    from ..pubfigures import SPECS_FILENAME

    specs = os.path.join(str(project.project_directory), SPECS_FILENAME)
    if os.path.isfile(specs):
        return REPORT_PIPELINE, None
    steps = [dict(s) for s in REPORT_PIPELINE["steps"]
             if s.get("action") != "render_publication_figures"]
    return ({"name": REPORT_PIPELINE["name"], "steps": steps},
            f"no {SPECS_FILENAME} — publication-figure step skipped")


def project_validation_issues(steps: list[dict],
                              experiment_type: str | None = None) -> list[tuple[int, str]]:
    """Per-step validation problems, mirroring ``actions.validation_issues``
    (same signature so the Script Editor can treat both levels uniformly)."""
    del experiment_type  # project actions are type-agnostic
    issues: list[tuple[int, str]] = []
    for i, step in enumerate(steps):
        key = step.get("action", "")
        if key in ABSORBED_ACTIONS:
            ## Not an error: a script saved before the action was folded into
            ## its replacement still runs (absorb_legacy_steps rewrites it).
            issues.append((i, f"'{key}' has been replaced by "
                              f"'{PROJECT_ACTIONS[ABSORBED_ACTIONS[key]].title}'"
                              " — delete this step; the run absorbs it."))
            continue
        action = PROJECT_ACTIONS.get(key)
        if action is None:
            issues.append((i, f"Unknown project action '{key}'"))
            continue
        for err in action.validate(step.get("params", {})):
            issues.append((i, err))
    return issues


def preflight_project_script_issues(script: dict, project) -> list[str]:
    """Project-in-hand checks the static validator cannot do, run by the Hub
    before spawning a script so a typo never reaches a run (grill 2026-08):
    unknown ``only:`` replicate names, and an experiment-script name that
    resolves nowhere it is asked to run.

    For a targeted step every named replicate must resolve the script
    (centrally or in its own config); for a broadcast step only a name that
    resolves *nowhere at all* is an issue — a partial spread is normal
    fallback territory and stays a loud runtime summary, not a block.
    """
    import os

    from .runner import load_scripts

    issues: list[str] = []
    known = list(getattr(project, "experiment_names", []))

    def _own_has(rep: str, name: str) -> bool:
        cfg = layout.config_path(project.experiment_dir(rep))
        try:
            own = load_scripts(cfg)
        except Exception:  # noqa: BLE001
            return False
        return any(s.get("name") == name for s in own)

    ## Scripts arrive through the LENIENT parses (Project.__init__,
    ## load_batch_file), so steps may be None/non-list and a step or its
    ## params any type. This runs in a Qt slot, where an uncaught exception
    ## is fatal — malformed shapes are skipped here and reported by
    ## run_project_script's validation instead.
    raw_steps = script.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict) \
                or step.get("action") != "run_in_experiments":
            continue
        params = step.get("params")
        if not isinstance(params, dict):
            continue
        name = str(params.get("script", "")).strip()
        only = _parse_only(params.get("only"))
        unknown = [n for n in only if n not in known]
        if unknown:
            issues.append(f"Step {i}: no replicate named "
                          + ", ".join(f"'{n}'" for n in unknown))
        if not name or project.find_experiment_script(name) is not None:
            continue
        if only:
            unresolved = [n for n in only
                          if n in known and not _own_has(n, name)]
            if unresolved:
                issues.append(
                    f"Step {i}: script '{name}' resolves nowhere for "
                    + ", ".join(f"'{n}'" for n in unresolved)
                    + " (not in project.yaml experiment_scripts nor in "
                    "their tracking_config.yaml)")
        elif known and not any(_own_has(n, name) for n in known):
            issues.append(
                f"Step {i}: script '{name}' resolves nowhere — not in "
                "project.yaml experiment_scripts nor in any replicate's "
                "tracking_config.yaml")
    return issues


def run_project_script(script: dict, project,
                       log_cb: Callable[[str], None],
                       figure_cb: Callable[[str, Any], None] = lambda *_: None,
                       ) -> ProjectRunContext:
    """Execute a Project Script and return the context.

    Validation runs before any side-effect. Bridge/replicate failures are
    collected continue-on-error style and raised as one summary at the end,
    so a pipeline finishes its independent work before reporting what broke.
    """
    steps = script.get("steps", [])
    if not steps:
        raise ProjectScriptError("Script has no steps")
    ## Retired actions are rewritten BEFORE validation — a script saved when
    ## run_all_analyses was its own action must still run, not fail as
    ## "unknown". The editor still flags them, so they get cleaned up.
    steps, absorbed = absorb_legacy_steps(steps)
    for note in absorbed:
        log_cb(f"Note: {note}")
    issues = project_validation_issues(steps)
    if issues:
        lines = [f"Step {i + 1}: {err}" for i, err in issues]
        raise ProjectScriptError("Script validation failed:\n"
                                 + "\n".join(lines))

    ctx = ProjectRunContext(project=project, log=log_cb, figure=figure_cb)
    for i, step in enumerate(steps):
        action = PROJECT_ACTIONS[step.get("action", "")]
        ctx.log(f"── Step {i + 1}/{len(steps)}: {action.title}")
        action.execute(step.get("params", {}), ctx)
    if ctx.failures:
        raise ProjectScriptError(
            f"Script finished with {len(ctx.failures)} replicate "
            "failure(s):\n  - " + "\n  - ".join(ctx.failures))
    ctx.log("✓ Project script complete.")
    return ctx
