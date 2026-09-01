# Global settings

The **Global** tab edits the `global:` section of one replicate's `tracking_config.yaml`.

## Experiment type

An **Experiment Type** is a named assay bundle. For example, **Valence Experiment** fixes the tracking type to two-choice tracking, constrains the rig to Arena Max, Colosseum, or Small Arena, requires Light and NoLight counting regions, supplies default phases, and adds Valence-specific quality criteria.

Choose **Custom** when you want the older freeform behavior driven directly by `tracking_type`.

## Tracking type

For Custom experiments, choose the analysis mode that matches the DTrack data:

- `TRACKER` - distance and speed
- `TWOCHOICETRACKER` - preference index, percentage, transitions, distance
- `XCHOICETRACKER` - adjusted X position plus distance
- `PAIRWISEINTERACTIONTRACKER` - proximity interactions plus distance
- `COUNTER`, `TWOCHOICECOUNTER`, `PAIRWISEINTERACTIONCOUNTER` - occupancy-based modes with no continuous identity between frames

Typed experiments may lock this field because the type owns it.

## Tracking rig

The rig sets calibration defaults such as frames per second and millimeters per pixel. Presets include `small_arena`, `arena_max`, `colosseum`, `obscura`, and `movie`. The `movie` rig has no preset calibration, so `fps` and `mm_per_pixel` are required.

Some Experiment Types constrain the allowed rigs. Valence accepts Arena Max (36 wells) and Colosseum (24 or 18 — new configs are laid out with 24), and the Config Editor can lay out the matching plate automatically. The layout includes the plate's **geometry**, not just its region names: on Arena Max the first 18 wells come out with an **X multiplier** of `-1` because they face the other way. See **Tracking and counting regions** for the full plate.

## Experimental design factors

Define factor names and levels, such as `Genotype: CS, Mutant`. Tracking-region rows then choose one level for each factor.

In a Project, these factors must match the shared design in `project.yaml`. The replicate config carries the matching values so a single Experiment can still load on its own.

## Facets and phase names

Enable faceted analysis to split the recording by minute cutoffs. `10, 70` creates three windows: `0-10`, `10-70`, and `70+`. Phase names are optional, but if present they must name every window.

The Hub's **Faceted** checkbox, plot buttons, summaries, and pairwise comparisons use the loaded experiment's configured cutoffs.

## Quality criteria

Experiment Types may expose quality knobs:

- **`min_transitions`** - for Valence, flies with fewer transitions than this during the primary phase are excluded from every result and listed in `*_Excluded.csv`; `0` turns the exclusion off.
- **`min_movement`** - for Valence, flies below this movement rate during the first phase are flagged in `LowMovementFlag` but kept in the results; `0` turns the flag off.

## Parameter overrides

Leave overrides blank to use rig defaults. Fill them only when the recording needs a deliberate departure from the preset: `fps`, `mm_per_pixel`, speed windows, walking/sleep thresholds, micro-movement thresholds, or pairwise interaction distances.
