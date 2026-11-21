# Config file overview

`tracking_config.yaml` describes one Experiment Directory. In a Project, it is the per-replicate config; the shared design lives in the Project's `project.yaml`.

## Top-level sections

- **`global`** - experiment type or tracking type, rig, design factors, facet settings, quality criteria, and optional parameter overrides.
- **`tracking_regions`** - one entry per tube, well, or tracked animal region.
- **`counting_regions`** - treatment labels and DTrack aliases for two-choice and counter assays.
- **`scripts`** - optional experiment-level scripts run from the Hub's **Scripts** tile or from a Project Script (the `run_in_experiments` bridge - every replicate, or only ones named in its `only:` list).
- **`data_folder`** - optional. Read this experiment's recording from another folder instead of its own `data/` (see below).
- **`about`** - optional. What the recording is, in your own words: title, purpose, who ran it, the flies, the protocol, and what each factor and level means. No analysis reads it (see below).

Project-level scripts live in `project.yaml`, not in a replicate's `tracking_config.yaml`.

## Reading a recording stored elsewhere: `data_folder`

```
data_folder: ../../../../../runs/tracking/2026/R-20261020-asmith-01/data
global:
  ...
```

- The folder holds what `data/` would hold: the `.xlsx` and its `_Data_*.csv` files.
- Write the path **relative to this experiment's folder**, with **forward slashes**. An absolute path works, but **Validate YAMLs** warns that it is not portable.
- The folder is **only ever read**. `analysis/`, `qc/`, the report and `removed_regions.yaml` are still written in this experiment's folder. Nothing files, moves or repairs anything in the named folder.
- It is a top-level key of this one config: never under `global:`, never in the Project design, and never copied into a new replicate.
- The experiment is **blocked** when the folder is missing or is a file, or when this experiment's own `data/` (or its root) also holds a recording. An empty `data/` is fine.
- Two replicates of one Project may not read the same folder: the second, in name order, is reported as a **duplicate** and not analyzed. Two *Projects* may read the same folder with different settings.
- Leave the key out (or empty) and the experiment reads `data/` exactly as always.

The Config Editor shows the key as **Data folder** on the Global tab.

## Describing the recording: `about`

```
about:
  title: Light preference, MBON10 silenced, replicate 1
  purpose: Whether silencing MBON10 changes the preference for light.
  experimenter: asmith
  series: valence-mbon10-2026
  organism: Drosophila melanogaster
  sex: [female]
  age_at_start: 5-7 days
  protocol: {name: Valence two-choice, version: "2", link: https://example.org/valence}
  notes: Plate 3 was reloaded before the start.
  factors:
    Genotype:
      kind: genotype
      levels:
        MBON10: R12C11-GAL4 > UAS-GtACR1
        Ctrl: w1118 > UAS-GtACR1
global:
  ...
```

**Where to start.** For a recording that has no Project yet, run `pytrack-config /path/to/MyRecording` on its folder. The Config Editor opens even when the folder has no `tracking_config.yaml`, and saving writes one; it reads no data and creates nothing else there. Fill in the **About** tab, and the design on the other tabs whenever you are ready. For a replicate of a Project, open the same editor from the Hub: **Project -> Experiments -> Experiment configs... -> Edit config...**.

Every field is optional, and so is the section:

- `title` - one line. Up to 60 characters fit a list of recordings; the tab counts them, and a longer title is saved and noted.
- `purpose` - one or two sentences: what was varied, in which flies, and what was measured.
- `experimenter` - who ran it, as you write it.
- `series` - a name shared by replicate recordings of the same question.
- `organism`, `sex` (a list, such as `[female, male]`), `age_at_start` - the flies.
- `protocol` - its `name`, `version` and `link`.
- `notes` - free text.
- `factors` - for each design factor a `kind`, one of `genotype`, `treatment`, `diet`, `condition`, `sex`, `other`; and for each level a few words on what it is: the cross, the dose, the diet.
- `run` - the recording's id in a catalogue that filed it. The catalogue writes it. The tab shows it and you cannot type it.

What the section is, and is not:

- **No analysis reads it.** No number, table or "re-run needed" depends on it, none of it is printed on a report, and none of it is sent to an AI provider. Editing it never makes a saved analysis out of date. Its `notes` are not the run notes typed at **Run Analysis**, which are printed on the report; and a fly that died is still declared under **Removed regions**, not noted here.
- **Nothing in it is ever an error.** A field of the wrong type, an unknown kind, a title over 60 characters, a factor or level the design does not declare: each is a note when the experiment loads, when the Config Editor saves and in **Validate YAMLs**. Nothing is refused.
- **It is this recording's alone.** Never under `global:`, never in the Project design, never inherited. A Project only notes - in its panel, in **Validate YAMLs** and in the `validate_design` step, never on its report - when two replicates describe one level differently, because the Combined Analysis pools levels by name.
- **A new replicate starts from an earlier one.** Replicates share who ran them, the series, the flies, the protocol and what the factors and levels mean. **Create experiment...**, **Experiment configs...** and **Copy config from...** carry that shared part into the new config. The title, the purpose, the notes and `run` describe one recording and are never carried.
- **Another program may write here.** A catalogue that files the recording can rewrite the section to match its own record: it adds `run`, and may turn a level's text into a mapping that keeps your words as `definition` beside its own `term`, `label` and `role`. The apps keep every key they do not know and change only a level's `definition`.

### The About tab

The fields are the ones above. Under them, each design factor has a **Kind** and each declared level a row for **What it means**. The rows follow the factors on the Global tab, and what you typed is kept while the factors change. Rename a level there and its row here goes, and its meaning is no longer written; the other rows stay, and renamed back it is as it was. A level the file describes but the design does not declare has no row: it is kept, and saving notes that it is undeclared.

**Start from another recording...** asks for another recording's `tracking_config.yaml` and fills in what replicates share: experimenter, series, organism, sex, age at start, protocol, and the kinds and meanings of the factors and levels this config declares. Your title, purpose and notes are left as they are. Nothing is saved until you save.

A section the tab writes is in the order above, `run` first when there is one, with any key it does not know last. A section you did not edit keeps the values and the order the file had, so opening a config never makes it look changed.

### If the file changed on disk

Another program can write a config while the editor has it open: the Hub writing new Directional Hypotheses into a replicate, or a catalogue rewriting `about` once the recording is filed. Before every save the editor compares the file with what it last read or wrote. If it differs, the save stops and says so, naming the sections that differ, and you choose:

- **Reload from disk** - see what the file holds now. Your unsaved edits are lost and nothing is written.
- **Overwrite** - write what the window shows.
- **Cancel** - the default: neither.

Saving scripts from the Script Editor opened in this window is not such a change.

## Creating a valid config

1. In the Hub Project panel, use **Experiment configs...** to scaffold missing replicate configs from the Project design.
2. Open a replicate in **Config Editor** to assign rig, tracking-region treatments, and aliases.
3. Copy an existing config with the Hub's **Copy config from...**, offered right after **Create experiment...**. The chosen file is checked against the project design *before* it is written; a config that would not conform is refused and nothing is overwritten. Copy only when the region layout truly matches as well — conforming to the design does not mean the treatments belong to this recording.
4. For a standalone experiment outside a Project, the Config Editor's new-file flow can create a starter Experiment Directory. For a folder that already holds a recording, `pytrack-config /path/to/MyRecording` writes its config there instead.

## Rules

- The file must be named exactly `tracking_config.yaml`.
- It belongs at the Experiment Directory root, beside `data/` (or beside nothing, when `data_folder:` names the recording's folder).
- A Project root uses `project.yaml`; it should not contain its own `tracking_config.yaml`.
- In a Project, enforced keys must match `project.yaml`'s `design:` section. Region treatments, aliases, fly counts, and rigs may differ between replicates.
- `TWOCHOICETRACKER` and `TWOCHOICECOUNTER` need exactly two counting regions, each with an `alias`.
- Typed Experiment Types, such as Valence, may fix or constrain fields. The editor disables fixed fields and validates required regions before saving.

Validate from the Hub with **Project -> Create/Load -> Validate YAMLs**. With a Project selected it checks the `project.yaml` *and* every replicate's `tracking_config.yaml` in one pass, reporting each file separately with a count at the end; with a standalone experiment it checks that one config. Use **Experiment configs...** to open a replicate config you want to edit.

## Minimal Custom example

```
global:
  tracking_type: TWOCHOICETRACKER
  tracking_rig: colosseum
  experimental_design_factors:
    Genotype: [CS, Mutant]
tracking_regions:
  T_0:
    experimental_factors: CS
    x_location_multiplier: 1
    y_location_multiplier: 1
counting_regions:
  Light:
    alias: Light
  Dark:
    alias: Dark
```

Open **Global settings** or **Tracking and counting regions** for field details.
