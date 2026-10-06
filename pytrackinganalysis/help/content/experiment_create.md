# Creating experiments

This is the **Experiments** card in the middle of the Hub's Project panel: the replicate table, plus the three buttons that create and configure replicates. All three inherit the shared design from `project.yaml`, so all three stay disabled until a Project is open - a replicate with nothing to conform to is not a replicate.

The three buttons mirror the three on the **Create/Load** card above, one level down: the thing exists, it does not exist at all, or its directory exists but its config does not.

## Which button

- **Create experiment...** - the replicate does not exist yet. You give it a name; the Hub makes the directory (and its `data/`) inside the Project and scaffolds a `tracking_config.yaml`. It refuses a name that is already an Experiment Directory, and it refuses a name whose folder already exists - that is the next button's job. Then it offers the two ways to finish the config, below.
- **Initialize existing directory...** - the folder is already in the Project but has no `tracking_config.yaml`. It lists the candidate directories with what it found in each. Loose files are **filed first**: a recording sitting at the folder root moves into `data/`, everything else into `extra_files/`, and only then is the config scaffolded and the Config Editor opened. Filing first matters because the Config Editor and the QC that follows a load both read `data/`; a recording left at the root would make the freshly configured replicate look empty. A folder that is ambiguous or unreadable is refused rather than guessed at.
- **Experiment configs...** - the bulk view: every experiment directory in the Project with its config and data-file status, so you can create the missing ones and open the ones that exist without hunting through the file system.

You can also **double-click a replicate row** to load it. A row marked **Config: missing** is a folder with data but no config; double-clicking offers to create one from the project design.

## What the scaffold contains

The scaffolded `tracking_config.yaml` is design-conformant by construction, but it is a **starting point, not a finished config**:

- When the Project already has at least one replicate, the scaffold is a copy of the **first replicate's** config - including its rig and its region treatments, which almost certainly are not the ones for this recording.
- When this is the first replicate, the config is built from `project.yaml`'s design and the experiment type's defaults, with the **rig left blank and no tracking regions yet** - they appear when you choose the rig in the Config Editor (or use **Generate N regions** for a Custom layout).

Either way you still have to choose the rig, assign every tracking region to its design-factor levels, check the counting-region aliases, and put the DTrack export under `data/`. The exception is a design that admits only one treatment - every factor has a single level, such as `Genotype: [Chr]` - where the regions the Config Editor adds start assigned to it.

## Finishing a new replicate's config

Right after **Create experiment...** succeeds, the Hub asks how to finish it. Two buttons:

- **Edit config...** (the default) - opens the scaffold in the Config Editor.
- **Copy config from...** - replaces the scaffold with a `tracking_config.yaml` chosen from anywhere on disk. This is the common case for the second and later replicates of a run: the same rig and the same region treatments as an experiment that already works.

A copy is **checked against the project design before it is written**, using the same test every replicate passes at load time. That ordering is the point: a non-conforming replicate makes the whole Project refuse to load, and you would otherwise have to find and undo the copy by hand.

What can happen:

- **It conforms** - the file is copied over the scaffold and the replicate is ready; the editor is not forced open. If the copy is still incomplete (for example, region treatments that have not been assigned), the log lists what is left to fix rather than letting it fail at run time.
- **It does not conform** - nothing is written. A message lists the mismatches (wrong experiment type, different factors or levels, counting regions out of order, or any enforced `design.global` key that disagrees), the original scaffold stays in place, and the Config Editor opens on it.
- **It cannot be read, or you pick the replicate's own config, or you cancel** - nothing is written, you are told why, and the Config Editor opens on the scaffold.

In a legacy Project - one whose `project.yaml` has no `design:` section - the copy is checked against the existing replicates instead, which are the authority there.

## Related topics

- **Config file overview** and **Global settings** - what goes in a replicate's `tracking_config.yaml`.
- **Tracking and counting regions** - assigning treatments, plate layouts, and aliases.
- **Project YAML** - what the shared design enforces, and what stays per replicate.
