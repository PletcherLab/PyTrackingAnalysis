"""Help topic registry — ids map to packaged markdown under ``content/``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpTopic:
    """One help page shown in a dialog or the help browser."""

    id: str
    title: str
    summary: str
    filename: str

    @property
    def path_name(self) -> str:
        return self.filename


# Order is the sidebar order in HelpBrowser.
TOPICS: tuple[HelpTopic, ...] = (
    HelpTopic(
        "getting_started",
        "Getting started",
        "Set up a Project, configure replicates, and run the first analysis.",
        "getting_started.md",
    ),
    HelpTopic(
        "project_structure",
        "Project directory layout",
        "Experiment and Project layouts, naming rules, and the two config levels.",
        "project_structure.md",
    ),
    HelpTopic(
        "project_create",
        "Creating and opening a Project",
        "The Create/Load card: Open, Create, Initialize existing, and Edit config.",
        "project_create.md",
    ),
    HelpTopic(
        "experiment_create",
        "Creating experiments",
        "The Experiments card: Create, Initialize existing, Experiment configs, "
        "and how a new replicate's config is finished.",
        "experiment_create.md",
    ),
    HelpTopic(
        "project_yaml",
        "Project YAML",
        "What project.yaml owns, what stays in each tracking_config.yaml, and how validation works.",
        "project_yaml.md",
    ),
    HelpTopic(
        "config_overview",
        "Config file overview",
        "What belongs in a replicate tracking_config.yaml.",
        "config_overview.md",
    ),
    HelpTopic(
        "config_global",
        "Global settings",
        "Tracking type, rig, design factors, and optional overrides.",
        "config_global.md",
    ),
    HelpTopic(
        "config_regions",
        "Tracking and counting regions",
        "How regions map treatments and DTrack aliases.",
        "config_regions.md",
    ),
    HelpTopic(
        "hub_workflow",
        "Analysis Hub workflow",
        "Project-first Hub tiles, panels, replicate loading, and outputs.",
        "hub_workflow.md",
    ),
    HelpTopic(
        "project_actions",
        "Project actions",
        "Refresh the full Project report, use publication plots and AI narrative, and run Project scripts.",
        "project_actions.md",
    ),
    HelpTopic(
        "removed_regions",
        "Removed regions",
        "Declare flies that died, escaped, or were never loaded, and see how "
        "they are excluded and reported.",
        "removed_regions.md",
    ),
    HelpTopic(
        "valence_statistics",
        "Valence statistics",
        "The Permutation Test, Factor Comparisons, Directional Hypotheses, "
        "and how to read the p-values.",
        "valence_statistics.md",
    ),
    HelpTopic(
        "batch_run",
        "Batch runs",
        "Run a designated Project Script in every Project of a folder.",
        "batch_run.md",
    ),
    HelpTopic(
        "plot_editor",
        "Plot Editor",
        "Project-level publication figures, plot_specs.yaml, styles, and vector exports.",
        "plot_editor.md",
    ),
    HelpTopic(
        "batch_experiments",
        "Legacy batch migration",
        "How retired batch workflows map to Projects and Project Scripts.",
        "batch_experiments.md",
    ),
    HelpTopic(
        "scripts_overview",
        "Scripts and Script Editor",
        "Experiment Scripts, Project Scripts, and the visual editor.",
        "scripts_overview.md",
    ),
    HelpTopic(
        "ai_summary",
        "AI summary",
        "An optional AI-written one-page summary embedded in the report.",
        "ai_summary.md",
    ),
    HelpTopic(
        "qc_overview",
        "QC Viewer",
        "Data-quality table and per-tracker plots.",
        "qc_overview.md",
    ),
    HelpTopic(
        "outputs",
        "Outputs",
        "Experiment and Project artifacts under analysis/, qc/, and figures/.",
        "outputs.md",
    ),
)

_BY_ID = {t.id: t for t in TOPICS}


def get_topic(topic_id: str) -> HelpTopic:
    try:
        return _BY_ID[topic_id]
    except KeyError as exc:
        known = ", ".join(t.id for t in TOPICS)
        raise KeyError(f"Unknown help topic {topic_id!r}. Known: {known}") from exc


def topic_ids() -> list[str]:
    return [t.id for t in TOPICS]
