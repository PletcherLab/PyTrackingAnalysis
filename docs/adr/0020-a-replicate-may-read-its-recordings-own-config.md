# A replicate may read its recording's own config

Extends ADR-0018 (a replicate may read its recording from elsewhere) from the
data to the config, as pyflic's ADR-0031 did for pyflic. Changes nothing for a
replicate that holds its own `tracking_config.yaml`.

## Context

The lab's `labdata` system keeps each recording in a run folder: the DTrack
export in `data/` and its `tracking_config.yaml` beside it. An analysis of
runs is a PyTrackingAnalysis Project, and until now each of its replicates
held a copy of the run's config, with `data_folder:` pointing back at the data
(ADR-0018). So one recording had a config in its run folder and another in
every Project that analysed it, and a correction made in one never reached the
others.

On 2026-11-26 the lab head asked for what pyflic already does: "ensure that
there are not multiple copies of the tracking_config for any specific run. Any
modifications through the pyTrackingAnalysis app should be stored in the
tracking_config in the labstore, not a copy of it." And: "the member file
simply replaces the run files and points them to the labstore run hierarchy.
But within those run folders a single config remains." He also asked that this
only add to the usual way of working: a Project whose replicate folders hold
their own config and `data/` works as it always has.

## Decision

- **A replicate may hold `tracking_member.yaml` instead of a config.**
  - Its `config:` names the recording's `tracking_config.yaml`.
  - Its optional `data_folder:` names the data.

  Both are resolved against the replicate folder, as `data_folder:` is
  (ADR-0018). Without `data_folder:`, the data is the `data/` beside the named
  config, or the folder that config's own `data_folder:` names, resolved from
  there.
- **One resolver answers which config a replicate reads.**
  - `layout.config_location` and `layout.config_path` sit beside
    `layout.data_location`. These ask them: `has_config` (and so
    `project.is_experiment_dir`), `Experiment`, the Project, the classifier,
    Batch discovery, removals, the Hub, the Config Editor and the script
    editor.
  - With no `tracking_member.yaml`, the answer is the replicate's own
    `tracking_config.yaml`, and nothing more is read.
- **What blocks such a replicate** is said with the key, the value as written
  and the resolved path, by the classifier, the loader and the Project's
  warnings:
  - a `config:` that is missing or is not a path;
  - a config that does not exist, or is a folder;
  - a replicate that also holds a `tracking_config.yaml` of its own ("which is
    the experiment's config?"), or a recording of its own;
  - a recording folder that holds the replicate folder.

  A Project that cannot read such a replicate's config leaves it out and says
  why.
- **The named config is only read for the replicate.**
  - Everything written for the replicate (`analysis/`, `qc/`, its report,
    `removed_regions.yaml`) stays in the replicate folder, and filing refuses
    such a replicate.
  - Writing the design's hypotheses into the replicates
    (`replicate_config_paths`, `write_replicate_hypotheses`) skips it.
  - The Config Editor opened on the replicate folder opens the recording's own
    config. Edits are saved there, never to a copy. A pointer that cannot be
    followed is refused rather than adopted as a new file.
- **Inside a Project with a design, the analysis settings are the design's
  alone** (`project.apply_design_settings`).
  - The recording's config describes the recording, and this description must
    agree with the design, as any replicate's must:
    - the `global:` keys `experiment_type`, `tracking_type`, `tracking_rig`,
      `fps`, `mm_per_pixel` and `experimental_design_factors`
      (`DESCRIPTION_KEYS`);
    - `tracking_regions`, `counting_regions` and `about:`.
  - Every other `global:` key is a setting (`facet_cutoffs`, `facet_labels`,
    `hypotheses`, `min_transitions`, `min_movement` and the parameter
    overrides). It comes from `design.global`, or else the Experiment Type's
    default.
  - Settings the recording's config states are not used, and are named in a
    Project warning and when the Experiment loads. So two Projects may analyse
    one recording with different settings.
  - In a Project without a design, or loaded on its own, the config is read
    whole. So is a first design inferred from it.
  - `Experiment` applies this itself. Every way of opening the replicate (the
    Project, the Hub, the QC Viewer, the Plot Editor, scripts) analyses it the
    same way.
- **Two replicates naming one recording are one replicate,** as two naming one
  data folder are (ADR-0018).

## Consequences

- A region's levels or a rig corrected in the recording's config reach every
  Project that reads it, at once, and so may change what re-running an
  analysis computes. labdata records a fingerprint of each named config when
  an analysis's results are updated, and warns when it changes.
- A replicate holding its own config behaves exactly as before, and a Project
  may mix the two kinds. The existing test suite pins this.
- Unlike pyflic's member file, `tracking_member.yaml` is read by
  PyTrackingAnalysis alone. labdata writes it for an analysis of filed runs.
