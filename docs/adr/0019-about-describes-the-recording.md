# `about:` says what a recording is, and no analysis reads it

## Context

The lab's `labdata` system files each recording as a **run** (ADR-0018). On
2026-11-21 the lab head decided how the analysis packages and labdata meet:

- **Describe, then file.** A person describes a recording in its own folder
  with the package, usually before any Project exists. labdata then files the
  run from that folder: it reads the config, copies it and the recording into
  a run folder, and asks for nothing twice.
- **Every field is optional and in the person's own words.** labdata applies
  its own vocabulary (who the person is, registry names for strains and
  treatments) when it files the run.
- **After filing, labdata's record is the truth,** and labdata rewrites the
  description to match it — in the run folder's copy of the config and in
  every copy an analysis holds, which here is a replicate of a Project. So the
  section can change on disk while one of this package's windows has the
  file open.
- **A new recording starts from an earlier one.** Replicates share most of
  their description, and nobody should type it twice.

Whoever runs a recording knows things no file records: why it was run, who
ran it, the flies' sex and age, the protocol, and what each design factor and
level means (`MBON10` is which cross). A catalogue, a colleague a year later,
or an agent reading the config cannot recover them from a DTrack export.
`tracking_config.yaml` is already where a recording's design lives — factors,
levels, the plate — and a replicate states its whole `global:` itself, so the
description belongs beside the design, in the one file that travels with the
recording.

pyflic has the section already (its ADR-0024) and the module that knows its
shape. A shared contract fixes that shape for both packages, so one catalogue
reads both.

## Decision

- **One optional top-level section, `about:`.** Every field is optional, and
  so is the section: `title` (one line; 60 characters fit a list), `purpose`,
  `experimenter`, `series`, `organism`, `sex` (a list; one word reads as a
  list of one), `age_at_start`, `protocol` (`name`, `version`, `link`; plain
  text reads as the name), `notes`, and `factors`, which gives each design
  factor a `kind` (genotype, treatment, diet, condition, sex, other) and each
  level a few words of meaning — text, or a mapping whose `definition` is
  that text. A key the package does not know is kept.

- **No analysis reads it.** No number, summary table, "re-run needed" verdict
  or skip-analyzed decision depends on it; nothing hashes the config, so there
  is no cache to exclude it from. None of it is put on a report, and the AI
  payload is the serialized report model, so none of it reaches an AI
  provider either. A config without the section loads, validates and analyses
  exactly as before, and every file the package writes for it is
  byte-identical ("no key, no change", ADR-0018).

- **It belongs to the recording,** like `data_folder:`: never under
  `global:`, never in a Project `design:`, never inherited, never compared
  across replicates for errors.

- **One module, mirrored.** `pytrackinganalysis/about.py` is a verbatim copy
  of pyflic's `pyflic/base/about.py` at the commit its header names: the field
  names, the written order, the six kinds, the title limit and the helpers.
  pyflic is the origin — change it there first, then copy. The copy differs
  only in that header and in the word "pyflic" before the numbers of pyflic's
  ADRs, and `tests/test_about_mirror.py` holds it to that with the digest of
  the origin file, so a change made only here fails. Its docstrings speak
  pyflic's language (`flic_config.yaml`, Member, Design); that is the price of
  one module, and the header says how to read them.

- **Warnings, never errors.** `config_validation.config_warnings` reports
  `about.about_problems` against the config's own
  `experimental_design_factors`: a field of the wrong type, an unknown kind, a
  title over 60 characters, a factor or level the design does not declare.
  The loader, the Config Editor's save and Validate YAMLs already show that
  channel. `validate_config` is not touched: its problems are fatal for a
  typed experiment (ADR-0001) and block the editor's save, and nothing in
  `about:` may stop a load, a save or a run. That holds for the checks
  themselves: the mirrored module takes each factor's levels as a list and
  raises on a number, so callers here hand it the design through
  `config_validation.declared_factors`, and a check that fails anyway is
  reported as one more warning.

- **Every writer of the config keeps what it does not edit.** That includes
  keys inside `about:` the package does not know, `run:`, and the keys of a
  level's mapping (`term:`, `label:`, `role:`, which a catalogue writes). The
  writers, and how each treats the section:
  - *The Script Editor* (`script_editor.runner.save_scripts`) splices the
    `scripts:` block into the file as it is at save time; `about:` is left as
    spelled.
  - *`project.write_replicate_hypotheses`* re-reads each replicate's file,
    changes `global.hypotheses` and writes it back; `about:` survives by
    value.
  - *`Project.scaffold_replicate`* and the Hub's *Copy config from…* give a
    new replicate only the **shared About** of the config it starts from
    (below).
  - *`ConfigEditorWindow.scaffold_project`* builds a new config from an
    Experiment Type and has no source to take a section from.
  - *The Config Editor's save* is the one writer that edits it (next item).

- **The Config Editor's About tab edits it.** The fourth tab holds the
  fields; `run:` is shown and cannot be typed; each design factor has a kind
  and each declared level a row for its meaning. The rows follow the Global
  tab's factors table, and what was typed is remembered while the factors
  change: a level the design stops declaring loses its row and is no longer
  written, and renamed back it has its whole entry again. Only a level's
  meaning is edited (`about.with_meaning`): text stays text, and a mapping
  keeps its other keys. A level or factor the file describes but the design
  never declared has no row; it is not the tab's to drop, so it is kept and
  the save warns about it.
  - An edited section is written in the contract's order (`about.KEY_ORDER`,
    then unknown keys), none at all when every field is blank, and a new one
    at the top of the file (after a leading `data_folder:`).
  - **A section nobody touched is written back exactly as the file had it.**
    The editor's dirty check compares whole-document dumps, so rewriting a
    hand-ordered section, `sex: female` or a plain-text `protocol:` into the
    contract's form on load would make every such config look changed.

- **A file that changed on disk is never overwritten silently.** The Config
  Editor keeps each file's text as it last read or wrote it and compares
  before a save. If the file differs, the save says so, names the sections
  that differ, and offers to reload (the edits in the window are lost, nothing
  is written), to overwrite, or to cancel, which is the default. Compared by
  content, not modification time, for ADR-0010's reason — content survives a
  copy between drives — and as text rather than by value, so a comment added
  by hand is not dropped unasked either. The Script Editor is the window's own
  child and its save is not a change to ask about, unless the sections the
  window shows are no longer what it read: then something else wrote the file
  first, and the next save still asks.

- **A new recording starts from an earlier one.** The **shared About** is
  `experimenter`, `series`, `organism`, `sex`, `age_at_start`, `protocol` and
  `factors`, each level's whole entry included, so a name a catalogue
  standardized once travels to the next replicate. `title`, `purpose`,
  `notes` and `run:` are one recording's alone and are never taken. The About
  tab's **Start from another recording…** fills the shared part from a chosen
  config (`about.start_from`), limited to the factors and levels this config
  declares. Scaffolding a replicate from the first one, and **Copy config
  from…**, do the same through `project.new_replicate_config`; that is also
  where `data_folder:` is left behind (ADR-0018). Copy config from… rewrites
  the file only when something has to stay behind — otherwise it is still a
  byte-for-byte copy.

- **A Project notes when replicates describe one level differently**
  (`about.meaning_spread`), because the Combined Analysis pools levels by
  name. It is never an error. The lines are `Project.about_warnings`, a list
  of their own: `Project.warnings` is printed in the Combined Analysis
  statistics and on the Project Report, where nothing of `about:` may go. The
  Hub's Project panel, Validate YAMLs (as notes, not problems) and the
  `validate_design` step show both lists.

- **A lone folder is described with `pytrack-config <folder>`.** The Hub
  cannot open a standalone Experiment Directory (ADR-0008), and a recording is
  described before any Project exists. The Config Editor opens on a folder
  with no config and writes one. It does not construct an `Experiment` to do
  so, and must not start to: loading one creates `analysis/` and `qc/` in
  the folder. For a replicate, the Hub opens the same editor from
  **Experiment configs…**.

- **The package stays unaware of the catalogue.** It reads no labdata file and
  gains no dependency. `run:` is the recording's id there; it has nothing to
  do with an analysis run or a Batch Run.

Rejected:

- **A sidecar file**, the way `removed_regions.yaml` is one (ADR-0010). A
  removal is an observation that changes the analysis population, entered
  after the fact and applied in bulk from a sheet; it earned a file of its
  own so the design specification would not carry outcomes. `about:` changes
  nothing the analysis does, the catalogue reads the description where the
  design is, and two files per recording would drift. ADR-0010 stands:
  `about.notes` is free text nobody's code reads, and a fly that died is
  still declared as a Removed Region, not noted there.
- **Describing the recording in `project.yaml`.** A recording is described
  before a Project exists, and several Projects analyse one recording
  (ADR-0018).
- **Adding the meaning-spread lines to `Project.warnings`.** That is where
  the Project's other warnings go, and from there onto the Project Report and
  into the AI payload.
- **Rewriting every section into the contract's form on load.** See the dirty
  check, above.
- **Storing the recording's start date.** The export holds it, and two places
  for one date disagree eventually. The About tab does not show it either:
  that would mean opening the export, which describing a folder does not need.
- **Making the Hub open a lone Experiment Directory.** ADR-0008 stands.

## Consequences

- The mirrored module must be copied again whenever pyflic's changes; until
  then the digest test names the commit this copy is from. Its wording cannot
  be changed here, so the Project translates "members" to "replicates" where
  it shows the module's lines — a Member is a Project of a Batch in this
  package.
- `Project.about_warnings` is a second list. Code that shows a Project's
  warnings to a person shows both; code that prints them on a report or into
  statistics uses `Project.warnings` alone.
- Every writer that rewrites the file whole goes through PyYAML, so comments
  are still lost on a full rewrite and a `|` block comes back as a quoted
  scalar, equal in value. That is unchanged.
- The Config Editor's save can now ask a question it never asked. An open
  editor no longer writes over what another writer of the file — the Project
  dialog carrying hypotheses, a catalogue — put there in the meantime.
- After a save, "untouched" means "as last saved": the file as it was before
  that save is not brought back by undoing an edit in the form.
- An undeclared level kept by the About tab keeps drawing its warning until a
  person removes it by hand or declares the level.
- Untested on Windows: the suite runs on Linux only.
