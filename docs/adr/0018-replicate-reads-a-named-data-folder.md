# A replicate can read its recording from a folder its config names

Amends ADR-0005 (a Project no longer always travels with its data) and
ADR-0011 (its "one recording, one replicate" rule now compares data
locations too, and its filing never touches a referencing replicate).

## Context

The lab's `labdata` system gives every data collection a permanent, locked
folder: a **run** (`runs/<assay>/<year>/<run ID>/`, recording in `data/`),
checksummed a week after registration so nothing under `data/` may change
after that. A study's analysis lives elsewhere
(`studies/<study>/analyses/<analysis ID>/`) and corresponds to a Project here.
Several studies analyse the same run with different settings.

This package could not do that. A Project's replicates are its
subdirectories (ADR-0005); each replicate read its recording from its own
`data/`; each wrote its output into its own folder. The only way to pool runs
was to copy their raw data into the Project, and `labdata` forbids raw-data
copies. pyflic and pySurvAnalysis copied this repository's layout and batch
code, so the rule is decided here first and ported from here.

## Decision

- **One optional key, `data_folder:`, at the top of a replicate's
  `tracking_config.yaml`.** It names a folder that holds what `data/` holds
  today. It belongs to that config alone: never under `global:`, never in a
  Project `design:`, never compared across replicates, and never inherited —
  scaffolding a replicate from the first one drops it, and so does the Hub's
  **Copy config from…**, because it names *that* experiment's recording. A
  blank value is no key.

- **Relative to the replicate folder, forward slashes.**
  `../../../../../runs/tracking/2026/R-20261020-asmith-01/data` is what labdata
  writes. Resolution is the operating system's own (`realpath` of the joined
  path, so `..` is taken after any symlink, as `open()` would take it).
  Windows accepts backslashes natively. An absolute value works, but the
  linter warns that it is not portable — as it does for a backslash — and a
  warning never blocks a load or a save: a typed experiment's validation
  problems are fatal (ADR-0001), and an absolute path is not wrong, only
  fragile. So warnings are a separate channel (`config_warnings`).

- **No key, no change.** Other labs use this package. A config whose text
  never mentions `data_folder` is not even parsed by the resolver; every code
  path, output and message for it is what it was. That is why the run
  manifest and the report gain their data-location lines only for a
  replicate that uses the key.

- **One resolver: `layout.data_location`.** It answers "where is this
  replicate's recording?" from the replicate folder and its config: today's
  case-insensitive `data/` (`layout.data_dir`), or the resolved
  `data_folder:` with any problem the loader would refuse it for. The loader
  (`Experiment`), the classifier (`layout.classify`), `Project`,
  `layout.experiments_in` (and through it Batch discovery and the preflight),
  `has_experiment_data`, `tracking_regions_in_data` (and through it the
  Config Editor's From-data plate), the Hub's Experiment configs dialog,
  `batch_analyze` and clock repair all call it — so the classifier and the
  loader cannot disagree, which is the rule this module's docstring has
  always stated. The loader resolves before it creates anything, so a
  reference it refuses writes nothing.

- **A referenced recording is only ever read.** Everything written for the
  replicate stays in the replicate folder (`analysis/`, `qc/`, the report,
  `.pytracking_run.json`, `removed_regions.yaml`, hypotheses written from
  the Project) or the Project folder, exactly where it went before. Filing
  refuses a referencing replicate and moves nothing. Clock repair refuses to
  rewrite one in place. Arena's opt-in `write_to_csvfile` exports, which
  write beside the recording, refuse for one; redirecting them into
  `analysis/` was rejected because `summarize(write_to_csvfile=True)` would
  then overwrite the pipeline's own `<exp>_Summary.csv` with a differently
  filtered table.

- **Conflicts are errors, not guesses.** A referencing replicate is blocked —
  and the loader raises `DataLocationError`, naming the key, the value as
  written and the resolved path — when the folder does not exist or is a
  file (**no recording**); when the replicate folder also holds a recording
  of its own (**ambiguous**: which is the experiment?) — any `.xlsx` or
  `.csv` in its own `data/`, where an export-shaped file is a piece of one,
  but at its root only a loadable workbook, which is all the ordinary
  classifier counts there, so a `units.csv` beside the config is just a
  file; or when the named folder holds the replicate folder itself
  (**ambiguous**: the analysis would be written inside the recording). An
  empty local `data/` is not a conflict. A referencing replicate is never an
  **Unfiled Recording**: a workbook in the named folder is where the loader
  looks, and a workbook beside the key is a conflict to settle by hand.

- **One recording, one replicate — by data location too.** ADR-0011 drops a
  symlinked copy of a replicate by real path. Two replicates whose data
  locations resolve to one folder (`normcase(realpath(...))`, for the
  case-insensitive share) are now one as well: the first in sorted order is
  kept, and the other is reported by name — a Blocked Experiment with the
  new status **duplicate** in `experiments_in`, so the preflight and the
  Batch Run summary list it with the other blocked replicates, and
  `Project.duplicates` plus a Project warning, which the Hub shows as a note.
  `Project.__init__` and `experiments_in` share the rule
  (`layout.DataLocations`), so they cannot disagree about who the duplicate
  is. The rule applies only when one of the two uses the key: two plain
  replicates whose `data/` folders are one folder by symlink are left exactly
  as they were, because "no key, no change" outranks tidying an old corner.
  Across Projects nothing is compared: two Projects reading one recording
  with different settings is the point of the feature.

- **Say where the data came from.** For a referencing replicate the run
  manifest gains `data_location: {written, resolved}`; the experiment
  summary, `str(Experiment)` (which the Hub logs on load) and the report
  cover give the resolved path with the key as written.

- **Clock repair gains an out-of-place mode.** `repair_export(target,
  out_dir=...)` and `--out <folder>` write a complete copy of the data
  folder into a new or empty folder — the export CSVs rewritten exactly as an
  in-place repair rewrites them, every other file copied byte for byte
  (contents and modification times, not permission bits, so a copy of a
  locked run is an ordinary folder) — plus a `README.txt` describing the
  repair (`README_clock_repair.txt` if the export already has a README). The
  originals are untouched, so no backup is made; a destination inside the
  data folder is refused. It works for any replicate. In place on a
  referencing replicate is refused with the out-of-place command in the
  message; a dry run is allowed, since it writes nothing. The loader's
  `ClockRollbackError` names both options. labdata writes repaired copies
  into a run's `derived/` folder and points replicates there.

Rejected:

- **A Project-level data root**, or a registry mapping replicates to
  recordings. It would make the design section carry per-replicate facts and
  split one replicate's definition across two files; ADR-0005 rejected an
  out-of-tree registry for the same reason.
- **Symlinking `data/`.** Symlinks on the lab's Windows SMB share need
  privileges most users lack, and a symlinked `data/` is indistinguishable
  from a local one — filing would happily move a loose workbook into the
  locked run.
- **Honouring the key by changing `layout.data_dir`.** That function is also
  where filing puts a recording; a referencing replicate must never be filed
  into, so "where is the recording" and "where would filing put one" stay
  two functions.

## Consequences

- A Project no longer always travels with its data. ADR-0005 kept identity
  in the Project folder so that it "travels with its data"; identity still
  does — replicates are still the configured subdirectories — but a copy of
  the analysis folder without the `runs/` tree finds its referencing
  replicates blocked, each naming the path it resolved. Moving the managed
  tree as a whole keeps every relative reference working.
- `layout.classify` now parses YAML for a config that mentions
  `data_folder`; every other config is still only listed. A config that
  mentions the key but cannot be parsed counts as having none, so the
  classifier keeps today's answer and the loader reports the broken YAML.
- With an alternate config (`Experiment(..., config_path=...)`) the loader
  reads the key from that file; the classifier always reads
  `tracking_config.yaml`. The Hub only ever loads the canonical file.
- On Windows, `realpath` of a path longer than 260 characters may come back
  with a `\\?\` prefix, and that is the resolved path the messages, the
  report and the run manifest then show. It still reads correctly. Untested:
  the suite runs on Linux only.
- `layout.DUPLICATE` is a new Blocked Experiment status with no fix button —
  the fix is deciding which replicate should read that recording.
- pyflic and pySurvAnalysis port this rule from `layout.py` and `project.py`;
  pySurvAnalysis keeps its existing `data_file:` key.
