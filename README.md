# PyTrackingAnalysis

A Python pipeline and desktop UI for analysing insect-tracking data exported
from DTrack. Describe each recording in `tracking_config.yaml`, group
replicate recordings with a Project-level `project.yaml`, and the pipeline
produces summary CSVs, statistics (pairwise between treatments, or tests
against indifference for a single treatment), publication-quality plots, and
PDF reports.

## What's included

Four PyQt6 desktop apps sharing a common theme, plus a full Python API:

| Interface | Purpose |
|-----------|---------|
| **Analysis Hub** (`pytrack`) | Project-first driver — manage replicates, load experiments, run analyses, build combined results, view plots in a tabbed dock |
| **Config Editor** (`pytrack-config`) | Structured editor for `tracking_config.yaml`, with a visual Script Editor for saved analysis recipes |
| **QC Viewer** (`pytrack-qc`) | Per-tracker data-quality tables with XY, distance, and quality-timeline plots |
| **Plot Editor** (`pytrack-plots`) | Project-level publication figures from pooled replicate data |
| **Python API** | Everything scriptable from a notebook or script (`Experiment`, `batch_analyze`, …) |

Supported assay types: plain position tracking, two-choice (tracker or
counter), X-choice, and pairwise-interaction (tracker or counter), with
built-in calibration presets for the small arena, arena max, colosseum, and
obscura rigs.

**Experiment Types** bundle an assay end to end: choosing one (e.g. `Valence`,
a two-choice light-preference assay) fixes the tracking type and phases,
constrains the rig, requires the right counting regions, runs a fixed analysis
set, and produces a report tailored to that assay. Set `experiment_type:` in
`tracking_config.yaml`, or pick it in the Config Editor or Project editor.
Omitting it is a *Custom* experiment — the
freeform, `tracking_type`-driven behavior, unchanged. See the
[user guide](doc/guide.md) §4.1 and `docs/adr/`.

**Removed regions** cover the losses no automatic check can see — a fly that
died partway through, escaped, or a well that was never loaded. Declare them
per experiment in `removed_regions.yaml` (from the Hub's **Removed regions…**
checklist, or by hand), or in bulk from a `removed_regions.csv` spreadsheet
that a Batch Run applies before it starts. Every fly in a removed region
leaves the analysis population with your reason recorded beside it in
`*_Excluded.csv` and in both reports. See the [user guide](doc/guide.md) §9
and `docs/adr/0010`.

## Installation

Requires Python ≥ 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/spletcher1/PyTrackingAnalysis.git
cd PyTrackingAnalysis
uv sync
```

`uv sync` creates the virtual environment and installs everything, including
the `pytrack*` commands below.

On Linux, optionally install launcher/taskbar entries for all four apps
(Hub, Config Editor, QC Viewer, Plot Editor):

```bash
uv run pytrack-install-desktop
```

## Quick start

### Desktop UI

```bash
uv run pytrack                        # Analysis Hub
uv run pytrack /path/to/MyProject     # Hub with a Project pre-loaded
uv run pytrack-config                 # Config Editor
uv run pytrack-qc /path/to/MyExperiment
uv run pytrack-plots /path/to/MyProject   # Plot Editor (publication figures)
```

An *experiment directory* holds a `tracking_config.yaml` plus a `data/`
sub-folder with the DTrack export; a *Project* holds a `project.yaml` and one
such directory per replicate. In the Hub: pick the Project folder, then
**double-click a replicate row** to load it — output streams to the dock, and
every plot opens as a tab.

### Python

```python
from pytrackinganalysis.Experiment import Experiment

exp = Experiment("/path/to/MyExperiment/")
exp.run_analysis()      # summary → QC → CSVs → statistics → plots
exp.create_report()     # multi-page PDF beside tracking_config.yaml

# Or the same fixed pipeline over every experiment under a parent folder:
from pytrackinganalysis.Experiment import batch_analyze
results = batch_analyze("/path/to/AllExperiments/")
```

Experiment results are written into each replicate's own `analysis/` and
`qc/` folders; Project combined outputs are written at the Project root.

## Documentation

- **[User guide](doc/guide.md)** — environment setup per OS, project
  directory layout, the complete `tracking_config.yaml` reference, the
  desktop apps, the Python API, Project workflows, and a quick reference.
- **[Scripts & the Script Editor](doc/scripts_guide.md)** — saved analysis
  recipes: authoring them visually, every available action and its
  parameters, faceting rules, Project Scripts, and legacy batch migration.
- In-app help (the **?** buttons) covers the same ground topic by topic —
  including **Removed regions**, which documents declaring a removal, what it
  does to the analysis, and everywhere it is reported.

## Tests

The suite is hermetic — it synthesises complete projects in a temp directory and
depends on nothing in `Testing/`:

```bash
uv run pytest -q                                    # run everything
uv run pytest -q --cov=pytrackinganalysis           # with coverage
```

Qt runs headless, so no display is needed. CI runs the same command on every
push and pull request (`.github/workflows/tests.yml`).

## Repository layout

```
pytrackinganalysis/    the package: analysis pipeline, PyQt6 apps, script engine
doc/                   user guides (start with doc/guide.md)
doc/archive/           superseded review documents, kept for history only
tests/                 the pytest suite
Testing/               sample projects and test data (gitignored)
```
