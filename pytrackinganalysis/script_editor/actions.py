"""Action registry for the visual Script Editor.

Each action is a :class:`Action` with:

* a ``key`` (how it's referenced in the saved YAML),
* a ``title`` / ``description`` / ``category`` / ``icon_name`` for the UI,
* a ``params`` list of :class:`ParamSpec` describing the inspector form,
* a ``validate`` callable returning a list of error strings, and
* an ``execute`` callable that runs the action against a :class:`RunContext`.

The starter library is intentionally small — the user flagged actions as
TBD.  Extend this module and register with :data:`ACTIONS`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from ..ui import Category


# ---------------------------------------------------------------------------
# Parameter specs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    """Describes one parameter of an action.

    ``kind`` picks the inspector widget:

    * ``"string"`` → QLineEdit
    * ``"int"``    → QSpinBox
    * ``"float"``  → QDoubleSpinBox
    * ``"bool"``   → QCheckBox
    * ``"choice"`` → QComboBox (requires ``choices``)
    * ``"path"``   → QLineEdit + browse button
    * ``"list"``   → QLineEdit (comma-separated; parsed via ``parse_list``)
    """

    name: str
    kind: str
    label: str
    default: Any = None
    help: str = ""
    choices: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None
    # Name of a sibling bool param that must be True for this widget to be
    # enabled in the inspector. When the controller is False the widget is
    # greyed out; the saved value is still preserved so toggling the
    # controller back on restores it.
    enabled_when: str | None = None


# ---------------------------------------------------------------------------
# RunContext and Action
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    """State threaded through a script run.

    ``exp`` is set by :func:`load_experiment` and reused by subsequent
    actions.  ``log`` / ``figure`` receive plain text and matplotlib
    figures respectively so the caller (the Hub) can route them to the
    :class:`OutputLog` and :class:`PlotDock`.
    """

    project_dir: Any = None  # Path-ish
    exp: Any = None  # pytrackinganalysis.Experiment.Experiment | None
    log: Callable[[str], None] = lambda _msg: None
    figure: Callable[[str, Any], None] = lambda _title, _fig: None


@dataclass
class Action:
    key: str
    title: str
    description: str
    category: Category
    icon_name: str
    params: tuple[ParamSpec, ...]
    validate_fn: Callable[[dict, str | None], list[str]] | None = None
    execute_fn: Callable[[dict, RunContext], None] | None = None
    # Tracking types this action is relevant for. ``None`` means "any" — used
    # by Palette.set_experiment_type to hide actions that don't apply to the
    # currently-loaded tracking type.
    applicable_types: tuple[str, ...] | None = None

    def validate(self, params: dict, experiment_type: str | None = None) -> list[str]:
        errs: list[str] = []
        # Generic required-field check: treat blank strings / None as missing
        # only when no default is provided.
        for spec in self.params:
            if spec.default is None and spec.name not in params:
                errs.append(f"'{spec.label}' is required")
        if self.validate_fn is not None:
            errs.extend(self.validate_fn(params, experiment_type))
        return errs

    def applies_to(self, experiment_type: str | None) -> bool:
        """Return True if this action should appear in the palette for *experiment_type*."""
        if self.applicable_types is None or experiment_type is None:
            return True
        return experiment_type in self.applicable_types

    def execute(self, params: dict, ctx: RunContext) -> None:
        if self.execute_fn is None:
            raise NotImplementedError(f"Action {self.key!r} has no execute function")
        # Merge defaults so downstream code can rely on every spec'd key existing.
        merged: dict[str, Any] = {}
        for spec in self.params:
            if spec.name in params:
                merged[spec.name] = params[spec.name]
            elif spec.default is not None:
                merged[spec.name] = spec.default
        self.execute_fn(merged, ctx)


# ---------------------------------------------------------------------------
# Helpers reused by actions
# ---------------------------------------------------------------------------

def _parse_cutoffs(s: Any) -> tuple[int, ...] | None:
    if s is None or (isinstance(s, str) and not s.strip()):
        return None
    if isinstance(s, (list, tuple)):
        return tuple(int(x) for x in s)
    return tuple(int(x.strip()) for x in str(s).split(",") if x.strip())


def _require_exp(ctx: RunContext, action: str) -> None:
    if ctx.exp is None:
        raise RuntimeError(
            f"{action}: no experiment loaded — add a 'Load experiment' step first."
        )


def _resolve_facet_cutoffs(params: dict, ctx: RunContext) -> tuple[int, ...] | None:
    """Return cutoffs to pass to a faceted method, or ``None`` for flat.

    ``params['facet']`` controls whether facets are used at all. When facet is
    on, ``params['cutoffs']`` overrides the experiment's configured cutoffs;
    if blank, the configured ``facet_cutoffs`` are used. Returns ``None``
    when facet is off, or when facet is on but no cutoffs are available
    anywhere — caller is responsible for falling back to flat behaviour.
    """
    if not bool(params.get("facet", False)):
        return None
    cutoffs = _parse_cutoffs(params.get("cutoffs"))
    if cutoffs is not None:
        return cutoffs
    cfg = getattr(ctx.exp, "facet_cutoffs", None)
    return tuple(cfg) if cfg is not None else None


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

def _exec_load_experiment(params: dict, ctx: RunContext) -> None:
    from .. import Experiment as ExperimentMod

    raw = (params.get("path") or "").strip()
    if raw in ("", "."):
        path = str(ctx.project_dir) if ctx.project_dir else "."
    else:
        path = raw
    force = bool(params.get("force_preprocessing", False))
    ctx.log(f"[load_experiment] {path} (force={force})")
    ctx.exp = ExperimentMod.Experiment(path, force_preprocessing=force)
    ctx.log(str(ctx.exp))


def _exec_filter_by_quality(params: dict, ctx: RunContext) -> None:
    _require_exp(ctx, "filter_by_quality")
    threshold = float(params.get("min_high_quality", 0.8))
    arena = ctx.exp.arena
    if not arena.supports_data_quality():
        ctx.log(
            "[filter_by_quality] per-frame data quality is not available for "
            f"{arena.parameters.get_tracking_type().name}; no trackers were filtered."
        )
        return

    before = len(arena.trackers)
    keep: dict = {}
    for key, tracker in arena.trackers.items():
        # get_data_quality returns a one-row DataFrame; pull the scalar out of it.
        dq = tracker.get_data_quality()
        hq = dq["HighQuality"].iloc[0]
        if pd.isna(hq):
            ctx.log(f"[filter_by_quality] {key}: no quality data; dropped")
            continue
        if float(hq) >= threshold:
            keep[key] = tracker
    arena.set_trackers(keep)
    ctx.log(
        f"[filter_by_quality] threshold={threshold:.2f} kept {len(keep)}/{before} trackers"
    )


def _region_prefix(name: Any) -> str:
    """``'T_0_3'`` → ``'T_0'``; anything with fewer than two ``_`` parts is returned as-is.

    Region ids are conventionally ``<letter>_<index>[_<object>]``, but nothing
    enforces it: a rig configured with a plain region name like ``Chamber``
    used to raise ``IndexError`` here and kill the whole script run.
    """
    parts = str(name).split("_")
    if len(parts) < 2:
        return str(name)
    return f"{parts[0]}_{parts[1]}"


def _exec_filter_by_region(params: dict, ctx: RunContext) -> None:
    _require_exp(ctx, "filter_by_region")
    regions_raw = params.get("regions", "")
    regions = {r.strip() for r in str(regions_raw).split(",") if r.strip()}
    if not regions:
        raise ValueError("filter_by_region: please list at least one region")
    arena = ctx.exp.arena
    before = len(arena.trackers)
    keep = {
        key: t for key, t in arena.trackers.items()
        if _region_prefix(getattr(t, "tracking_region_id", None) or key) in regions
        or getattr(t, "tracking_region_id", None) in regions
    }
    # Fallback simple match: key prefix
    if not keep:
        keep = {k: t for k, t in arena.trackers.items() if any(k.startswith(r) for r in regions)}
    arena.set_trackers(keep)
    ctx.log(f"[filter_by_region] kept {len(keep)}/{before} trackers matching {regions}")


def _exec_run_qc(_params: dict, ctx: RunContext) -> None:
    _require_exp(ctx, "run_qc")
    ctx.log("[run_qc]")
    ctx.exp.qc()


def _exec_run_analysis(params: dict, ctx: RunContext) -> None:
    _require_exp(ctx, "run_analysis")
    cutoffs = _resolve_facet_cutoffs(params, ctx)
    if bool(params.get("facet", False)) and cutoffs is None:
        ctx.log(
            "[run_full_analysis] facet=True but no cutoffs provided or configured — "
            "running flat."
        )
    ctx.log(f"[run_full_analysis] cutoffs={cutoffs}")
    ctx.exp.run_analysis(cutoffs=cutoffs)


def _exec_summarize(params: dict, ctx: RunContext) -> None:
    """Mirrors the Hub's Summarize button (with the Faceted checkbox)."""
    _require_exp(ctx, "summarize")
    cutoffs = _resolve_facet_cutoffs(params, ctx)
    if bool(params.get("facet", False)) and cutoffs is None:
        ctx.log(
            "[summarize] facet=True but no cutoffs provided or configured — "
            "running flat only."
        )
    ctx.log(f"[summarize] cutoffs={cutoffs}")
    ctx.exp.save_summary(cutoffs=cutoffs)


def _exec_run_pairwise_comparisons(params: dict, ctx: RunContext) -> None:
    """Mirrors the Hub's Run pairwise comparisons button."""
    _require_exp(ctx, "run_pairwise_comparisons")
    cutoffs = _resolve_facet_cutoffs(params, ctx)
    if cutoffs is None:
        if bool(params.get("facet", False)):
            ctx.log(
                "[run_pairwise_comparisons] facet=True but no cutoffs provided or "
                "configured — running flat."
            )
        else:
            ctx.log("[run_pairwise_comparisons] facet=False")
        _run_flat_pairwise(ctx)
        return
    ctx.log(f"[run_pairwise_comparisons] cutoffs={cutoffs}")
    ctx.exp.stats(cutoffs=cutoffs, save=True)


def _run_flat_pairwise(ctx: RunContext) -> None:
    """Run flat (non-faceted) pairwise comparisons for every relevant metric."""
    import io as _io
    import os as _os
    import sys as _sys

    exp = ctx.exp
    metrics = exp._stats_metrics()
    if not metrics:
        ctx.log("[run_pairwise_comparisons] no metrics for this tracking type")
        return

    buf = _io.StringIO()
    saved = _sys.stdout
    _sys.stdout = buf
    try:
        for metric in metrics:
            try:
                exp.arena.run_pairwise_comparisons(metric=metric)
            except Exception as err:  # noqa: BLE001
                print(f"Warning: could not run comparison for '{metric}': {err}")
    finally:
        _sys.stdout = saved

    text = buf.getvalue()
    ctx.log(text)
    path = _os.path.join(
        exp.analysis_path, f"{exp.arena.experiment_name}_Stats_flat.txt"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    ctx.log(f"Saved: {path}")


def _make_plot_executor(base_method: str):
    """Build an executor for a per-type plot action with a ``facet`` boolean."""

    def _exec(params: dict, ctx: RunContext) -> None:
        _require_exp(ctx, base_method)
        facet = bool(params.get("facet", True))
        cutoffs = _resolve_facet_cutoffs(params, ctx) if facet else None
        kwargs: dict[str, Any] = {}
        if facet and cutoffs is not None:
            method_name = f"{base_method}_facet"
            kwargs["cutoffs"] = cutoffs
        else:
            if facet and cutoffs is None:
                ctx.log(
                    f"[{base_method}] facet=True but no cutoffs provided or "
                    f"configured — falling back to flat ``{base_method}``."
                )
            method_name = base_method

        arena = ctx.exp.arena
        fn = getattr(arena, method_name, None)
        if fn is None:
            raise ValueError(f"Arena has no method {method_name!r}")

        import matplotlib.pyplot as plt

        figures: list = []
        orig_show = plt.show

        def _capture(*_a, **_k) -> None:  # noqa: ANN002, ANN003
            figures.append(plt.gcf())

        plt.show = _capture  # type: ignore[assignment]
        try:
            ctx.log(f"[{base_method}] arena.{method_name}({kwargs})")
            fn(**kwargs)
        finally:
            plt.show = orig_show  # type: ignore[assignment]

        title = _PLOT_TITLES.get(base_method, base_method.replace("_", " ").title())
        if facet and method_name.endswith("_facet"):
            title = f"{title} (facet)"
        for i, fig in enumerate(figures):
            tab_title = title if len(figures) == 1 else f"{title} ({i+1})"
            ctx.figure(tab_title, fig)

    return _exec


def _exec_create_report(_params: dict, ctx: RunContext) -> None:
    _require_exp(ctx, "create_report")
    ctx.log("[create_report]")
    path = ctx.exp.create_report()
    ctx.log(f"[create_report] wrote {path}")


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------

def _validate_filter_by_quality(params: dict, _exp_type: str | None) -> list[str]:
    errs: list[str] = []
    try:
        thr = float(params.get("min_high_quality", 0.8))
        if not 0.0 <= thr <= 1.0:
            errs.append("min_high_quality must be between 0 and 1")
    except (TypeError, ValueError):
        errs.append("min_high_quality must be a number")
    return errs


def _validate_cutoffs(params: dict, _exp_type: str | None) -> list[str]:
    raw = params.get("cutoffs")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    try:
        _parse_cutoffs(raw)
    except (TypeError, ValueError):
        return ["cutoffs must be a comma-separated list of integers"]
    return []


# Per-tracking-type plot button mapping. Each entry is (base_method, label,
# applicable_types). The executor uses ``base_method`` for flat and
# ``base_method + "_facet"`` for the faceted variant. ``label`` is the user-facing
# title in the palette and the resulting PlotDock tab.
_PLOT_BUTTON_DEFS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "plot_pi", "PI",
        ("TWOCHOICETRACKER", "TWOCHOICECOUNTER"),
    ),
    (
        "plot_percentage", "Percentage",
        ("TWOCHOICETRACKER", "TWOCHOICECOUNTER"),
    ),
    (
        "plot_transitions", "Transitions",
        ("TWOCHOICETRACKER",),
    ),
    (
        "plot_totaldistance", "Total distance",
        (
            "TRACKER", "TWOCHOICETRACKER", "XCHOICETRACKER",
            "PAIRWISEINTERACTIONTRACKER",
        ),
    ),
    (
        "plot_adjusted_x_position", "Adjusted X position",
        ("XCHOICETRACKER",),
    ),
    (
        "plot_interactions", "Interactions",
        ("PAIRWISEINTERACTIONTRACKER", "PAIRWISEINTERACTIONCOUNTER"),
    ),
)

_PLOT_TITLES: dict[str, str] = {base: label for base, label, _ in _PLOT_BUTTON_DEFS}

_PLOT_ICON_BY_LABEL: dict[str, str] = {
    "Total distance": "distance",
    "PI": "plot",
    "Percentage": "plot",
    "Transitions": "transition",
    "Adjusted X position": "xy",
    "Interactions": "plot",
}


def _facet_param_pair() -> tuple[ParamSpec, ParamSpec]:
    """The standard (facet, cutoffs) pair used by every analysis/plot action."""
    return (
        ParamSpec(
            "facet", "bool", "Faceted", default=True,
            help="When checked, runs the faceted variant; cutoffs below override "
                 "the project's facet_cutoffs (blank = use config).",
        ),
        ParamSpec(
            "cutoffs", "list", "cutoffs (minutes, comma-sep)", default="",
            enabled_when="facet",
            help="Optional. When blank, the project's facet_cutoffs are used.",
        ),
    )


# ---------------------------------------------------------------------------
# Registered actions
# ---------------------------------------------------------------------------

def _build_actions() -> dict[str, Action]:
    actions: dict[str, Action] = {
        "load_experiment": Action(
            key="load_experiment",
            title="Load experiment",
            description=(
                "Load an Experiment from a project directory "
                "(data/*.xlsx + tracking_config.yaml)."
            ),
            category=Category.LOAD,
            icon_name="load",
            params=(
                ParamSpec(
                    "path", "path", "Project dir",
                    default=".", help="Defaults to the script's project dir.",
                ),
                ParamSpec("force_preprocessing", "bool", "Force preprocessing", default=False),
            ),
            execute_fn=_exec_load_experiment,
        ),
        "filter_by_quality": Action(
            key="filter_by_quality",
            title="Filter trackers by quality",
            description="Drop trackers whose %HighQuality is below the threshold.",
            category=Category.QC,
            icon_name="quality",
            params=(
                ParamSpec(
                    "min_high_quality", "float", "min_high_quality",
                    default=0.8, min=0.0, max=1.0,
                ),
            ),
            validate_fn=_validate_filter_by_quality,
            execute_fn=_exec_filter_by_quality,
        ),
        "filter_by_region": Action(
            key="filter_by_region",
            title="Filter trackers by region",
            description="Keep only trackers whose tracking_region matches the list.",
            category=Category.QC,
            icon_name="quality",
            params=(
                ParamSpec("regions", "list", "regions (comma-separated)", default="T_0, T_1"),
            ),
            execute_fn=_exec_filter_by_region,
        ),
        "run_qc": Action(
            key="run_qc",
            title="Run QC",
            description="Print a data-quality report and write {exp}_data_quality.csv.",
            category=Category.ANALYZE,
            icon_name="quality",
            params=(),
            execute_fn=_exec_run_qc,
        ),
        "run_analysis": Action(
            key="run_analysis",
            title="Run Full Analysis",
            description=(
                "Run the multi-step pipeline: experiment summary → QC → "
                "summary CSVs → statistics → plots. Mirrors the Hub's "
                "Run Analysis button."
            ),
            category=Category.ANALYZE,
            icon_name="basic",
            params=_facet_param_pair(),
            validate_fn=_validate_cutoffs,
            execute_fn=_exec_run_analysis,
        ),
        "summarize": Action(
            key="summarize",
            title="Summarize",
            description=(
                "Write {exp}_Summary.csv (and, when faceted, {exp}_Summary_Facet.csv). "
                "Mirrors the Hub's Summarize button."
            ),
            category=Category.ANALYZE,
            icon_name="csv",
            params=_facet_param_pair(),
            validate_fn=_validate_cutoffs,
            execute_fn=_exec_summarize,
        ),
        "run_pairwise_comparisons": Action(
            key="run_pairwise_comparisons",
            title="Run pairwise comparisons",
            description=(
                "Run treatment-vs-treatment comparisons for every metric appropriate "
                "to the tracking type. Mirrors the Hub's Run pairwise comparisons button."
            ),
            category=Category.ANALYZE,
            icon_name="compare",
            params=_facet_param_pair(),
            validate_fn=_validate_cutoffs,
            execute_fn=_exec_run_pairwise_comparisons,
        ),
        "create_report": Action(
            key="create_report",
            title="Create PDF Report",
            description="Write <project>_report.pdf (in the project root) with figures, stats, and QC.",
            category=Category.ANALYZE,
            icon_name="report",
            params=(),
            execute_fn=_exec_create_report,
        ),
    }

    # Per-tracking-type plot actions. One palette tile per Hub Plots-card button,
    # filtered by the current experiment's tracking type.
    for base_method, label, applies in _PLOT_BUTTON_DEFS:
        actions[base_method] = Action(
            key=base_method,
            title=label,
            description=(
                f"Render the {label} plot. When ``Faceted`` is checked, calls the "
                f"_facet variant; cutoffs below override the project's facet_cutoffs."
            ),
            category=Category.PLOTS,
            icon_name=_PLOT_ICON_BY_LABEL.get(label, "plot"),
            params=_facet_param_pair(),
            validate_fn=_validate_cutoffs,
            execute_fn=_make_plot_executor(base_method),
            applicable_types=applies,
        )

    return actions


ACTIONS: dict[str, Action] = _build_actions()


def validation_issues(steps: list[dict], experiment_type: str | None) -> list[tuple[int, str]]:
    """Return ``[(step_index, error), …]`` for an entire script.

    *experiment_type* is inferred at runtime by the runner once a
    ``load_experiment`` step has executed; the editor passes ``None`` when
    nothing is loaded, so some validations (e.g. ``plot`` method) are
    deferred until runtime.
    """
    issues: list[tuple[int, str]] = []
    for i, step in enumerate(steps):
        key = step.get("action", "")
        action = ACTIONS.get(key)
        if action is None:
            issues.append((i, f"unknown action {key!r}"))
            continue
        for err in action.validate(step.get("params", {}), experiment_type=experiment_type):
            issues.append((i, err))
    return issues
