# Tracking and counting regions

These tabs edit the replicate-specific parts of `tracking_config.yaml`.

## Tracking regions

Each row is one tracking region, such as a tube or well. Region names usually follow DTrack numbering: `T_0`, `T_1`, and so on. `ExperimentName_Data_1.csv` maps to `T_0`; `ExperimentName_Data_2.csv` maps to `T_1`.

For each row:

- Pick one level for every design factor from the **Global** tab. A region left blank is unassigned: its flies are left out of statistics and figures, with a warning, rather than grouped as a blank treatment.
- Set **X multiplier** and **Y multiplier** to `1` or `-1` when a mirrored arena needs its coordinates flipped.
- Use **From data** to take the regions from the recording itself — the tracking regions in the DTrack export's ROI sheet (`data/*.xlsx`). Regions already in the table keep their treatments; any the recording lacks are removed only after you confirm. A config with no regions yet is filled this way when you open it, for any type whose rig does not fix the plate (Custom, Paired Open Field).
- Use **Generate N regions** for Custom layouts, or let a typed rig such as Valence on Arena Max, Colosseum, or Small Arena lay out the required plate. Arena Max is always 36 wells (`T_0`..`T_35`); the Colosseum is run at either 24 (`T_0`..`T_23`) or 18 (`T_0`..`T_17`), and both validate — a config laid out from scratch gets 24; to run 18, remove the last six rows. The Small Arena is 6 wells (`T_0`..`T_5`). An existing plate of a valid size is left alone.

## Plate geometry on a typed rig

When an Experiment Type fixes the plate, it owns the **X** and **Y multipliers** as well as the region names, and the editor lays them out that way:

- **Valence on Arena Max** — the first 18 wells (`T_0`..`T_17`) face the opposite direction, so their **X multiplier** is `-1`; `T_18`..`T_35` are `+1`. Every **Y multiplier** is `+1`.
- **Valence on the Colosseum** — all `+1`, at either plate size.
- **Custom layouts and untyped rigs** — everything is laid out at `+1`, and you set the flips yourself.

A plate laid out by the Config Editor and one built from scratch by the Experiment Type are now the same plate, well for well. (Earlier versions laid every editor row out at `+1`, which silently disagreed with the built config for Arena Max.) You can still override any individual row by hand when a recording genuinely differs; the layout only decides what a *fresh* plate starts as, and an existing one is never rewritten without asking.

In a Project, region-to-treatment assignments remain per replicate. The shared design says which factor levels exist; each recording still chooses which physical region belongs to which level.

New rows - the plate laid out when you choose a typed experiment's rig, **Add region**, and **Generate N regions** - start blank, unless the design admits exactly one treatment (every factor has a single level, such as `Genotype: [Chr]`); then they start assigned to it. With a choice anywhere (`Genotype: [Chr]`, `Sex: [M, F]`), new rows stay blank. Rows already saved are never changed: a saved blank region may be a deliberately empty well.

## Counting regions

Counting regions map DTrack strings to treatment labels. Each row has:

- **Treatment label** - the label used in summaries and plots.
- **Aliases** - comma-separated strings that may appear in the data file.

Example: label `Light` with aliases `Light, LL, L` assigns the Light treatment whenever DTrack reports any of those aliases.

Rules:

- Two-choice types need exactly two counting-region names.
- Each counting-region entry needs an `alias`.
- In a Project, `project.yaml` enforces the counting-region names and their order; aliases remain per replicate because DTrack exports can differ.

After saving, reload the replicate in the Hub so analyses use the updated config.
