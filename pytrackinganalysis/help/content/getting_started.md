# Getting started

PyTrackingAnalysis analyzes insect-tracking data exported from DTrack. The Hub is now Project-first: a **Project** is a parent directory with a `project.yaml`, and each replicate is an **Experiment Directory** underneath it.

## What you need

1. A **Project directory** with `project.yaml`. This file holds the shared design: experiment type, factors and levels, facets, quality criteria, and counting-region names.
2. One **Experiment Directory** per replicate. Each one has a top-level `tracking_config.yaml` with the rig, tracking-region treatment assignments, counting-region aliases, and optional experiment scripts.
3. A `data/` folder inside each Experiment Directory, containing the DTrack workbook (`.xlsx`) and its `*_Data_*.csv` files.

The pipeline creates `analysis/` and `qc/` folders when it writes results.

## First-time workflow

1. Open the **Analysis Hub** (`pytrack`) and make or open the Project from the **Project -> Create/Load** section. Which button depends on what is already on disk:
   - **Open Project** - the directory and its `project.yaml` both exist.
   - **Create project...** - neither exists yet; you choose where it goes and name it, and the directory is created for you.
   - **Initialize existing directory...** - the directory exists (often with replicate subdirectories already in it) but has no `project.yaml`. It keeps its own name, and the design is inferred from the first replicate that has a config.
2. Check the design in the Project editor. **Edit config...** reopens it at any time; if you point at a folder that is already an Experiment Directory, the Hub offers to create the Project on the parent so that experiment becomes a replicate.
3. Add or adopt replicates from the Project panel's **Experiments** card, which mirrors the same three cases one level down: **Create experiment...** for a replicate that does not exist yet, **Initialize existing directory...** for a folder that is already there but has no `tracking_config.yaml` (its loose files are filed into `data/` and `extra_files/` on the way), and **Experiment configs...** to create or edit configs in bulk.
4. Finish the new replicate's config. **Create experiment...** asks how: **Edit config...** opens the scaffold in the Config Editor, or **Copy config from...** replaces it with one taken from an experiment that already works — the usual choice for the second and later replicates of a run. A copied config is checked against the project design before it is written, so one that would not conform is refused with the scaffold left in place.
5. In the Config Editor, choose the rig, assign every tracking region to the design factors, and check counting-region aliases. Choosing a rig for a typed experiment lays out its plate for you, geometry included — on Valence + Arena Max the first 18 wells come out with an X multiplier of `-1`. A region left blank is unassigned and its flies are left out of the results; in a design with only one treatment, new regions start assigned to it.
6. Load a replicate by **double-clicking its row** in the Project table. This is the Hub's only experiment-loading path, and it runs QC as the experiment loads.
7. Run a single replicate from **Analyze -> Run Analysis**, or run the whole study from the Project panel with **Create report**. After the PDF exists, the same button reads **Update report**; both labels analyze every replicate, rebuild Combined Analysis, and write the Project report.

Later, when several sibling Projects sit in one folder, the Hub's **Batch** tile can run them all unattended - pick the parent with the Batch panel's **Choose batch folder...** button; see the **Batch runs** help topic.

## Apps

- **Analysis Hub** (`pytrack` / `pytrack-hub`) - day-to-day Project workflow: load replicates, run analyses, view plots, build combined results, run Project scripts and Batch runs.
- **Config Editor** (`pytrack-config`) - structured editor for each replicate's `tracking_config.yaml`, plus the Script Editor for experiment scripts.
- **QC Viewer** (`pytrack-qc`) - per-tracker data-quality table and diagnostic plots for one Experiment Directory.
- **Plot Editor** (`pytrack-plots`) - Project-level publication figures: live preview, Plot Specs and Styles in `plot_specs.yaml`, vector exports to `figures/`.

On Linux, run `pytrack-install-desktop` once per environment to install launcher entries and an icon for all four apps. Without them each app logs `App info not found for 'pytrack-…'` at startup and shows a generic taskbar icon. Re-run it after moving the project or recreating the virtualenv.

Use the **?** buttons next to controls for topic-specific help. The top-bar **Help** button opens the full help browser.
