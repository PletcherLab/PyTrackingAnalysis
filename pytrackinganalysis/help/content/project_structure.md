# Project directory layout

PyTrackingAnalysis uses two directory levels:

- An **Experiment Directory** holds one DTrack recording: `tracking_config.yaml`, `data/`, and that recording's results.
- A **Project** holds `project.yaml` and one subdirectory per replicate Experiment Directory.

The Hub works from the Project level. The Config Editor and QC Viewer work on one Experiment Directory at a time.

## Experiment Directory

```
MyExperiment/
├── tracking_config.yaml      <- required, top level
├── data/                     <- required
│   ├── ExperimentName.xlsx
│   ├── ExperimentName_Data_1.csv
│   ├── ExperimentName_Data_2.csv
│   └── ...
├── analysis/                 <- created by the pipeline
├── qc/                       <- created by the pipeline
└── ExperimentName_report.pdf <- created by Create PDF Report
```

### Naming rules

- The `.xlsx` workbook name becomes the experiment name used in output filenames.
- Per-tracker CSVs must match `<name>_Data_<N>.csv`. `_Data_1.csv` maps to YAML region `T_0`, `_Data_2.csv` maps to `T_1`, and so on.
- `tracking_config.yaml` must sit next to `data/`, not inside it.

### Recording stored elsewhere

An Experiment Directory can leave out `data/` and name the folder that holds its recording with `data_folder:` at the top of `tracking_config.yaml` — for example a locked run folder that several studies share:

```
studies/my-study/analyses/A-20261105-asmith-01/      <- the Project
├── project.yaml
└── R-20261020-asmith-01/                            <- a replicate
    ├── tracking_config.yaml   <- data_folder: ../../../../../runs/tracking/2026/R-20261020-asmith-01/data
    ├── analysis/  qc/         <- written here, as always
    └── R-20261020-asmith-01_report.pdf
```

The named folder is only read; every output stays in the replicate folder. See **Config file overview** for the rules.

## Project

A directory with `project.yaml` is a **Project**. Its immediate subdirectories that contain `tracking_config.yaml` are replicates. The Project's `design:` section is the authority for the shared design, and each replicate is hard-validated against it when the Project loads.

```
MyProject/
├── project.yaml              <- shared design and Project scripts
├── plot_specs.yaml           <- Plot Editor specs and styles
├── Rep1/
│   ├── tracking_config.yaml
│   └── data/ ...
├── Rep2/
│   └── ...
├── analysis/                 <- Combined Analysis
├── figures/                  <- publication figures
└── MyProject_report.pdf      <- Project Report
```

`project.yaml` owns the shared design: experiment type, design factors and levels, facet settings, quality criteria, and counting-region names. Each replicate's `tracking_config.yaml` owns recording-specific details: rig, tracking-region treatment assignments, counting-region aliases, and optional experiment scripts. A Project root should not have its own `tracking_config.yaml`.

## Batch

A folder whose immediate subdirectories holding `project.yaml` are its Projects is a **Batch** - the same rule one level up. A Project is never also a Batch. Nothing marks a Batch; it is a processing convenience for running many Projects unattended, never a third design level. An optional `batch.yaml` appears at its root only once batch-level scripting is authored: `script:` names the designated Project Script, and `project_scripts:` holds Project Scripts served to every Project. See the **Batch runs** help topic.

```
MyBatch/
├── batch.yaml                <- optional: script: and project_scripts:
├── ProjectA/
│   ├── project.yaml
│   └── ...
└── ProjectB/
    └── ...
```

## Project panel

The Hub's **Project** panel has three cards:

- **Create/Load** opens or makes the Project and describes the loaded one: **Open Project**, **Create project...**, **Initialize existing directory...**, and **Edit config...**, with **Validate YAMLs** below them checking the `project.yaml` and every replicate config in one pass. Its **?** opens **Creating and opening a Project**, which says which of the four ways in to use when.
- **Experiments** shows the replicate table plus **Create experiment...** (a replicate that does not exist yet), **Initialize existing directory...** (one whose folder exists but has no `tracking_config.yaml`) and **Experiment configs...** (create or edit the configs in bulk). All three inherit the design from `project.yaml`, so they wait for a Project. Its **?** opens **Creating experiments**, which also covers the choice offered right after a replicate is created — edit the scaffold, or replace it with a config copied from an experiment that already works.
- **Analysis** holds the Project actions over every replicate: **Create report** or **Update report**, **Plot editor...**, **AI narrative...**, **View reports**, **Removed regions...**, and the Project script runner at the bottom.

Double-click a replicate row to load that experiment. Rows marked **Config: missing** are folders with data but no `tracking_config.yaml`; double-clicking offers to create the config from the project design. Use **Experiment configs...** to create or edit replicate configs in bulk. **Create report** appears before the Project PDF exists; **Update report** appears after it exists. Both run every replicate, rebuild Combined Analysis, and write the Project report.

## Migrating from the retired batch-over-experiments mode

The retired batch-over-experiments mode has been absorbed into Projects. Point the Hub at the old parent folder and use **Create project** to write `project.yaml`. The dialog can infer the design from the first existing replicate config, and the existing subfolders become replicates. Use **Create report** or the built-in **Standard pipeline** instead of the retired Batch experiments panel.
