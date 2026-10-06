# Project YAML

`project.yaml` is the marker file that makes a parent folder a Project. It is also the authority for the shared design every replicate must match.

## What it owns

Typical keys:

```
name: My Study
notes: Optional Project notes.
design:
  global:
    experiment_type: Valence
    experimental_design_factors:
      Genotype: [CS, Mutant]
    facet_cutoffs: [10, 70]
    facet_labels: [Acclimation, Experiment, Cooldown]
    min_transitions: 5
    min_movement: 140
  counting_regions:
  - Light
  - NoLight
scripts: []
experiment_scripts: []
```

- **`name`** is optional: without it, the Project's name is its folder's name. It is the display name used in the Hub, combined output filenames (`analysis/<project>_Summary.csv` and the rest), and the Project report (`<project>_report.pdf`). It is written only when you give the Project a name that differs from its folder, so a `project.yaml` copied into another folder, or a renamed folder, takes the folder's name rather than carrying the old one into output filenames.
- **`notes`** are optional and appear near the top of the Project report.
- **`design.global`** is the shared global design: experiment type, design factors and levels, facets, phase names, and type-specific quality criteria.
- **`design.counting_regions`** fixes the treatment-region names and their order. Aliases stay per replicate.
- **`scripts`** and **`experiment_scripts`** hold the two script levels - see **Scripts sections** below.

Advanced hand-edited keys placed under `design.global` are enforced too. Use that deliberately: if you put a key there, every replicate must resolve to the same value.

## Scripts sections

Two-level scripting lives in `project.yaml`:

- **`scripts`** holds Project Scripts: named step lists of project-level actions (`project_report`, `render_publication_figures`, `generate_ai_narrative`, `validate_design`, `run_in_experiments`). Each mirrors a button on the Project card, so `project_report` is the whole **Create report** sequence — analyze every replicate, pool the results, build the PDF — not three separate steps. (`run_all_analyses` and `build_combined_analysis` were folded into it; older scripts naming them still run, and the Script Editor flags the steps so you can delete them.) Every `project.yaml` is created with one already in it — **Report pipeline**, the default run — so you can read and edit what a Run script or a Batch run will do. Run them from the Project panel's **Script** picker, which lists this file's scripts first, then the built-in **Standard pipeline** and **Report pipeline**; the built-ins themselves are never written to the file. A `project.yaml` with no `scripts:` section cannot be run by a Batch run.
- **`experiment_scripts`** holds centrally managed Experiment Scripts: one recipe serving every replicate without being copied into their configs. They run only through a Project Script's `run_in_experiments` step - in every replicate, or just the ones its `only:` list names. The Hub's Scripts tile lists solely the loaded experiment's own scripts, not these.

Both sections are optional; **Edit scripts...** (the Script Editor) writes them, and other keys in the file are preserved. See the **Scripts and Script Editor** and **Project actions** help topics.

One level up, a Batch has the analogous lazy file: `batch.yaml`, holding the designated Project Script (`script:`) and centrally held Project Scripts (`project_scripts:`). Unlike `project.yaml` it never marks the folder - a Batch is structural, and the file appears only once batch-level scripting is authored. See the **Batch runs** help topic.

## What stays out

Do not put a `tracking_config.yaml` at the Project root. Each replicate directory has its own `tracking_config.yaml` for recording-specific details:

- tracking rig and calibration overrides
- tracking-region treatment assignments
- counting-region aliases from that DTrack export
- replicate-local experiment scripts

## Validation

When a Project loads, every configured replicate is checked against `project.yaml`.

Hard failures include:

- different Experiment Type
- missing or extra design factors
- different factor levels
- counting-region names out of order
- any enforced `design.global` key resolving to a different value

Region assignments, aliases, fly counts, and rigs may differ unless you explicitly enforce them in `design.global`.

## Creating replicate configs

**Experiment configs...**, **Create experiment...** and **Initialize existing directory...** all scaffold `tracking_config.yaml` files from `project.yaml`. The scaffold matches the shared design by construction, but you still need to assign region treatments, check aliases, choose the rig when needed, and add the DTrack export under `data/`. (When every design factor has a single level, the regions the Config Editor adds start assigned to that one treatment.) When the Project already has a replicate, the scaffold is a copy of the *first* one, so its rig and treatments are that recording's, not this one's.

The same validation also runs **before** a config is brought in from outside. **Create experiment...** offers **Copy config from...**, and the chosen file is tested against `design:` exactly as a replicate is at load time. If it would fail, nothing is written and the scaffold stays — a non-conforming replicate would otherwise stop the whole Project from loading. In a legacy Project with no `design:` section, the incoming config is checked against the existing replicates instead. See **Creating experiments**.
