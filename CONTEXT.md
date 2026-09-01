# PyTrackingAnalysis

Analysis pipeline and desktop UI for insect-tracking data exported from DTrack.
This glossary fixes the domain language; it is not a spec.

## Language

**Batch**:
A directory with at least one Project anywhere beneath it. Discovery is
**recursive and prunes at each Project**: the walk descends until it finds a
Project — `project.yaml` plus at least one Experiment Directory — and never
looks inside one, because a Project's subdirectories are its Experiments by
definition. Projects therefore need not be immediate children, and grouping
folders (`Sept2026/`, `Archive/2025/`) are transparent. A **Member** is
identified by its **path relative to the Batch root** (`Sept2026/ProjA`; a
top-level Project is just `ProjA`), which is what `batch.yaml`, a Removal
Sheet's `project` column, and a Batch Run's target list all name. Only
existing Projects qualify: a **Batch Run** (one execution of
batch mode over a Batch) never creates or upgrades a `project.yaml`, and
directories that are not Projects are skipped with a log line. Purely a
processing convenience: it exists to run many Projects unattended, it is not
itself a Project and holds no analysis outputs of its own, it never pools
results across Projects (each Project has its own design; there is no
cross-Project analysis), and its only product is a per-Project run summary.
Nothing marks a Batch — being one is structural, and a `batch.yaml` at its
root appears only once batch-level scripting is authored, because unlike a
Project a Batch has no authority to declare. A Batch Run executes one
**designated Project Script** in every Project (continue-on-error,
per-Project log prefixes; default: the Report Pipeline, so zero authoring
means "Create report on every Project"). `batch.yaml` holds that
designation (`script:`) and a
central `project_scripts:` section — one recipe serving every Project without
being copied, the `experiment_scripts:` idea one level up.
_Avoid_: study, collection, batch root (the Batch IS the directory), batch
parent, batch script (there is no third script level — the thing a Batch Run
runs IS a Project Script). Unrelated to the retired batch-over-experiments
mode, and to the Batch Tools dialog removed in 2026-08.

**Project**:
A directory with a `project.yaml` at its root whose immediate subdirectories
holding a `tracking_config.yaml` are its Experiments — replicates of one
design. The `project.yaml`'s `design:` section is the **authority** for the
shared parameters (experiment type, factors and levels, facets, quality
criteria, counting-region names); a Project owns the Combined Analysis, the
project-level `plot_specs.yaml`/`figures/`, and the Project Report.
_Avoid_: batch parent, parent directory

**Experiment Directory**:
One recording's directory — `tracking_config.yaml`, `data/`, `analysis/`,
`qc/`, its own report — either standalone or as a replicate inside a Project.
(Formerly called the "project directory".)

**Unfiled Recording**:
An Experiment Directory whose DTrack export sits loose at its root instead of
inside `data/`, where the loader looks. The data is intact — only its filing
is wrong — so **filing** it (moving the `.xlsx`/`.csv` into `data/`, any other
loose file into `extra_files/`, and never a `.yaml`, which at an experiment
root is configuration or declaration) makes it loadable with nothing lost.
_Avoid_: malformed, non-compliant, unconverted, needs conversion

**Blocked Experiment**:
An Experiment Directory a run cannot use as it stands: an Unfiled Recording,
a recording with no `tracking_config.yaml`, or a configured directory with no
recording at all. Each reason names its own fix — file the recording,
scaffold the config, supply the missing data. Blocked is a property of the
Experiment Directory, never of the Project or the Member: a Member with four
healthy replicates and one blocked replicate runs the four. Blocked
Experiments are named before a Batch Run starts and again in its summary —
being reported is the whole point, and a Batch Run is never refused because
of one (a stale folder must not stop ten Projects at 2am).
_Avoid_: invalid project, broken project (nothing is broken — the run just
cannot use it yet), blocked project

**Replicate**:
An Experiment inside a Project. Every replicate's resolved config is
hard-validated against the Project's `design:` section (experiment type,
factors and levels, facets, quality criteria, counting-region names in
order); region→treatment assignments, counting-region aliases, fly counts,
and rigs may differ. New replicates are scaffolded from the design.

**Combined Analysis**:
The Project-level results built by stacking each replicate's *filtered*
summaries (exclusions and flags already applied) with an `Experiment` column:
combined summary CSVs, aggregated exclusions, and statistics — pooled
per-fly tests (Welch/Tukey, matching the plots) beside a linear mixed model
(treatment fixed, experiment random) that accounts for between-replicate
variation.

**Project Report**:
`<project>/<project>_report.pdf`: pooled publication figures rendered by the
same Plot Spec/Style system the Plot Editor saves, the pooled + mixed
statistics tables, a per-replicate summary table, a table naming every
Excluded Fly across the Project with its reason, and an opt-in AI-written
narrative (same rule as AI Summary: it summarizes, never analyzes).

**Analysis Hub**:
The main app (`pytrack`): a horizontal **tile ribbon** across the
top — three wide **container tiles** (Batch · Project · Experiment, one per
level of the hierarchy; each shows only live status, with a **status
readout** filling the strip to their right: the loaded project and
experiment in words) and, when the Experiment tile is expanded, a
**sub-strip** of four **sub-tiles** beneath it (Analyze · Plots · Scripts ·
AI — the tools that act on the loaded experiment; ADR-0012) — and a
full-width output/plots area below. All controls live in a tile's
**anchored panel** under the ribbon's last row (one open at a time; a
running task greys the cards in place rather than closing the panel).
Tiles never move or hide — an inapplicable tile dims and its panel holds the
fix; the Experiment tile alone is also *inert* while dimmed, since it holds
no fix (nothing loads from it). The **selection names the working container
— a Batch or a Project** —
and does only that one job: selecting a Batch lights the Batch tile and dims
the rest; double-clicking a row in its projects table is an ordinary selection
change down to that Project (no drill-in state, no up-button; ADR-0009). The
Hub is **Project-first**: an experiment is loaded only by
double-clicking its row in the Project panel's replicates table (ADR-0008),
and the Experiment tile reports the loaded experiment (ADR-0012).
_Avoid_: card column (the pre-2026-08 layout), Experiment *panel* (ADR-0008
removed it; the ADR-0012 tile is a group, not a panel), "strip" for the
whole two-row ribbon

**Experiment Script**:
A saved, re-runnable step list of experiment-level actions (run analysis,
plots, report…). Lives in an Experiment's `tracking_config.yaml` `scripts:` —
or, for replicates, centrally in the Project's `experiment_scripts:` section,
where one recipe serves every replicate without being copied into their
configs. Central scripts run only through the bridge (`run_in_experiments`,
broadcast or targeted); the Hub's Scripts tile lists solely the loaded
experiment's own `scripts:`.
_Avoid_: recipe, macro

**Project Script**:
A saved step list of project-level actions (`run_in_experiments`,
`project_report`, `render_publication_figures`,
`generate_ai_narrative`, `validate_design`) in
`project.yaml` `scripts:`. Same shape and visual editor as an Experiment
Script, but a separate action registry — levels cannot mix; the only bridge
is `run_in_experiments`, which runs a named Experiment Script in every
replicate — or, with its optional `only:` list of replicate directory names,
in just those replicates (project-defined first, each replicate's own as
fallback, continue-on-error). A replicate where the name resolves nowhere is
logged, counted in the failure summary, and the run continues; unknown
replicate names and unresolvable script names are flagged by pre-run
validation when a Project is in hand.

**Standard Pipeline**:
A built-in Project Script every Project can run without authoring anything:
validate design → project report → render publication figures. Never written
to `project.yaml`, so it tracks the shipped default.

**Report Pipeline**:
The built-in Project Script matching the Hub's Create report button plus
curated figures: project report → render publication figures (skipped when
the Project has no `plot_specs.yaml`). Preferred over the Standard Pipeline
for an unattended run because it does not gate on `validate_design` (which
would fail Projects mid-migration). Its steps are what every new
`project.yaml` is seeded with, under the name `batch`. Listed beside the
Standard Pipeline in every script picker, Project and Batch alike.
_Avoid_: default batch, batch pipeline

**batch (Project Script)**:
The Project Script named `batch` written into every new `project.yaml` — the
Project's own copy of the Report Pipeline's steps, and what a Batch Run
executes there when nothing else is designated. Named for the job rather than
the built-in it was seeded from, so a reader of the yaml can see what a batch
run will do. Distinct from the legacy `batch` **Experiment** Script
(ADR-0006), which lives under a replicate's `scripts:` or a Project's
`experiment_scripts:` — different level, different key, no collision.
_Avoid_: batch script (ambiguous across the two levels)

**Batch AI Narrative**:
`batch_ai_narrative.md` at the Batch root: an AI synthesis of the Projects'
own `ai_narrative.md` files — results across the batch, compromised designs,
and Projects that lost many flies — deliberately skipping the per-Project
detail that lives one level down. Opt-in per Batch Run. It is a synthesis,
not a pooling: a Batch never combines data across Projects. Because the
default `batch` script rebuilds each Combined Analysis (which deletes that
Project's narrative), Projects without one get a narrative generated first.
The front matter names which Projects the prose actually covers.
_Avoid_: batch summary, combined narrative (nothing is pooled)

**Absorbed Action**:
A project action retired into the one that already does its work —
`run_all_analyses` and `build_combined_analysis` into `project_report`, when
that action became the whole Create-report button. A script action mirrors a
Project-card button, and neither retired action had one. Saved scripts naming
them still run: `absorb_legacy_steps` drops the step (or promotes it to the
replacement, if the script has no `project_report` of its own) and moves the
replacement to the absorbed step's position, so later steps still find
analyzed replicates. The Script Editor flags them for cleanup.
_Avoid_: deprecated action, legacy step

**Experiment Type**:
A named bundle (e.g. Valence) that selects one Tracking Type and constrains the
rest of an experiment — the allowed rigs, the facets, the required counting
regions, the set of analyses that run, and the report produced. It is the
top-level thing a scientist chooses; everything else is derived or constrained
from it.
_Avoid_: assay, assay type, protocol, template

**Custom Experiment**:
The absence of a chosen Experiment Type — today's freeform mode, where the
config is driven directly by `tracking_type` with no constraints. A config with
no `experiment_type` key IS a Custom Experiment. Selectable explicitly too.
_Avoid_: generic, freeform, none

**Tracking Type**:
The tracker/counter class used to turn raw frames into metrics (e.g.
`TWOCHOICETRACKER`). An Experiment Type selects exactly one Tracking Type. This
is a lower-level, implementation-facing concept than Experiment Type.
_Avoid_: assay type

**Valence Experiment**:
The first Experiment Type. A two-choice light-preference assay: a two-choice
tracker, Light vs NoLight counting regions (in that order), on a Max, Colosseum,
or Small Arena rig (preset calibration only), with the fixed three-phase
structure below.

**Small Arena**:
A 6-well tracking rig (`tracking_rig: small_arena`; regions `T_0..T_5`, no
mirrored wells, preset 0.056 mm/px, frame timing from the DTrack MSec column
like every rig — there is no fps). Four interchangeable physical units exist
in the lab (Red1/Red2/Green1/Green2); the config never names the unit, only
the rig, because all four share one configuration. Valence-eligible with the
standard defaults (Light/NoLight aliases, 10/70 phases, min_transitions 5,
min_movement 140).
_Avoid_: SmallArenas (plural, as a rig name), chamber type (say rig), naming
Red1/Green1 etc. in configs

**Counting Region**:
A named group of raw DTrack region labels (its aliases) that an animal can
occupy. A Valence Experiment has exactly two: **Light** and **NoLight**, in that
order.

**Preference Index (PI)**:
A −1..+1 score of how strongly an animal favours the first counting region over
the second. For a Valence Experiment, Light is region-1, so **positive PI means
light-preference**; the group order is fixed to keep that sign stable.
_Avoid_: preference score

**Phase**:
A named time span within an Experiment Type's facet structure. A Valence
Experiment's phases come from its **default** cutoffs [10, 70] (which the user
may change):
- **Acclimation** — 0–10 min.
- **Experiment** — 10–70 min. The phase the primary result is read from.
- **Cooldown** — 70+ min.
The phase names apply only while the cutoffs are the default; changing them
yields plain minute-range labels instead.
_Avoid_: facet (facet = the generic windowing mechanism; a Phase is a named
facet at the type's default cutoffs).

**Primary Phase**:
The facet window the headline result — and the Low-Transition Exclusion — is
read from: the second window when there are two or more, else the only one.
Equals the Experiment phase at Valence's default cutoffs.
_Avoid_: middle facet, experiment window

**Low-Transition Exclusion**:
Valence-only inclusion criterion: a fly is kept only if its Transitions count
during the Primary Phase is at least `min_transitions` (yaml `global:` key;
Valence default 5; 0 = off; no data in the window counts as excluded).
_Avoid_: QC filter (data quality is a separate, always-reported concern)

**AI Summary**:
An optional, AI-written narrative (up to one page) of an experiment's analysis,
generated from the report's own content — figures, stats, and summary tables —
by a user-chosen provider (Anthropic or OpenAI). Opt-in per report, and offered
only when a provider API key is configured in `.env`. A failed generation never
blocks the report; the user gets an error message instead. The AI *summarizes*
the pipeline's analysis; it does not perform its own. An AI Summary is a
derivative of a single analysis run: re-running the analysis deletes it, and
the report embeds it only while the saved file exists. It is saved twice: as
`<name>_AI_Summary.txt` (what the report reads) and as `ai_narrative.md`
beside it — a fixed, name-independent filename so every narrative in a tree
is findable with one glob. Both die together.
_Avoid_: AI analysis, AI interpretation

**Excluded Fly**:
A fly removed from the **analysis population** for a recorded reason — absent
from figures, summary measures, statistics, and the summary CSVs, but listed
with its **Reason** in the report's removal table and `<exp>_Excluded.csv`,
and still shown in data-quality output. The reason is a column, not a class:
there is one exclusion list per experiment, and one row per fly, however many
criteria produced it. Today's reasons are the Low-Transition Exclusion and a
**Removed Region**; when both apply the reason string names both, the
experimenter's observation first (`Removed: dead at ~20 min; Low
transitions`), because an observation outranks an inferred rule.
_Avoid_: dropped fly, filtered fly

**Removed Region**:
A tracking region the experimenter declares out of the analysis, with a
free-text reason (default `Undefined`). Only the experimenter can know it —
death, escape, a flooded well, a mis-loaded plate — so it is entered by hand,
never inferred from the data. Declared in **`removed_regions.yaml`** at the
Experiment Directory root: an observation about how the run went, deliberately
outside `tracking_config.yaml`, which is a design specification. The unit is
the **region, not the fly**: every tracker in a removed region becomes an
Excluded Fly, so a well holding two animals loses both. Removal is
all-or-nothing — the whole recording for that region goes, not just the part
after the event, because a dead animal still registers as occupancy in
whichever region it died in and corrupts the fly's numbers throughout. Unlike
the Low-Transition Exclusion it is not an Experiment Type's policy but an
observation about the recording, so it applies to every type, Custom
Experiments included (ADR-0010).
_Avoid_: dead fly (death is one reason among several), bad well, censored

**Removal Sheet**:
`removed_regions.csv` (or `.xlsx`) at a Batch root — or a Project root — whose
`project, experiment, region, reason` rows declare Removed Regions in bulk,
the way lab notes actually arrive: a spreadsheet, authored without the app.
It is a **writer, not an authority**: applying it writes the declarations into
each Experiment's `removed_regions.yaml` and nothing reads it at analysis
time, so a Project carries its own removals wherever it is copied. Applied at
the start of a Batch Run and on an explicit button — never merely by browsing
to a folder — merging into what is already declared, where the standing
declaration wins and a differing reason is reported as a conflict.
_Avoid_: removal manifest, exclusion list (that is `<exp>_Excluded.csv`, an
output)

**Low-Movement Flag**:
Valence-only QC flag: a fly averaging less than `min_movement` mm/min (yaml
`global:` key; Valence default 140; 0 = off; no data counts as flagged) during
the *first* facet window is reported as potentially an issue — never removed
(`LowMovementFlag` column in the summary CSVs, table in the report). An
experiment with more than half of its analysed flies flagged is itself noted
as potentially an issue on the report cover.
_Avoid_: exclusion (a flag never removes a fly), QC filter

**Publication Figure**:
A hand-curated, journal-ready vector figure (SVG with editable text, or PDF)
rendered by plotnine from a Plot Spec + Plot Style and saved under
`<project>/figures/` — distinct from the matplotlib figures embedded in the
PDF report, and always regenerable from the spec.
_Avoid_: report figure, plot export

**Plot Style**:
A named, reusable look shared by every Publication Figure that references it:
figure size, theme, fonts, point/mean styling, and the treatment→color
mapping. Stored in the Project root's `plot_specs.yaml` under `styles:`;
`default_style:` names the one the Plot Editor auto-loads.
_Avoid_: theme (a plotnine theme is one field inside a style)

**Plot Spec**:
One Publication Figure's content decisions — axis labels, facet and treatment
inclusion/order/display names, y-limits, reference line — plus the name of
the Plot Style it uses. Stored in `plot_specs.yaml` under `plots:`, keyed by
plot id (e.g. `faceted_pi`).
_Avoid_: plot config, settings

**Plot Editor**:
The fourth PyQt6 app (`pytrack-plots`), a **Project-level** tool: opens a
Project, renders a live preview of the pooled figures from the same
Spec+Style that saving uses, and writes the vector Publication Figures.
Presentation only — it never alters `tracking_config.yaml`; opening a
replicate redirects up to its Project.
