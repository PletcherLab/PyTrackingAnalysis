# Batch discovery is recursive, and a Batch Run is confirmed in a preflight

Supersedes the structural half of ADR-0009 (Projects need not be immediate
children). Amends ADR-0010's note that an experiment-root sidecar is "safe
from Batch Tools' *Convert subdirectories*" — that tool is gone, and its
replacement protects the sidecar by rule instead.

## Context

Experimenters do not keep Projects in one flat folder. They keep
`Sept2026/ProjA`, `Archive/2025/ProjC`, a `pilot/` beside a `final/` — and
ADR-0009's Batch is one `os.listdir`, so pointing the Hub at the folder that
actually holds the work finds nothing. Splitting a tree into flat batch
folders to satisfy the tool is the wrong direction of accommodation.

Three other things pull in the same direction. Recordings arrive from DTrack
as loose files that someone must move into `data/` before a replicate loads;
*Convert subdirectories* used to do that in bulk and was removed with the
Batch Tools dialog (ADR-0009 amendment, 2026-08-24) because it was an
unguarded sweep over every subdirectory of the selection. A Batch Run applies
a Removal Sheet as its first act (ADR-0010), writing into every experiment's
sidecar with no preview and regardless of which Projects are checked. And a
Project whose replicates were never configured is invisible to the batch
list, so it fails at load, an hour into an unattended run.

## Decision

- **Discovery is recursive and prunes at each Project.** A **Batch** is a
  directory with at least one Project anywhere beneath it. The walk descends
  until it finds a Project — `project.yaml` plus at least one Experiment
  Directory — and never looks inside one, because a Project's subdirectories
  are its Experiments by definition (ADR-0005). An archived copy carrying its
  own `project.yaml` inside a Project therefore cannot become a second
  member, and no Experiment Directory can be analyzed twice in one run.
  Grouping folders are transparent. The walk does not follow symlinks, skips
  dot-directories, and ignores unreadable ones.

- **A Member is keyed by its path relative to the Batch root.**
  `Sept2026/ProjA`; a top-level Project is still just `ProjA`, so every
  existing `batch.yaml`, Removal Sheet, and `run_batch(project_names=...)`
  call keeps working untouched. Leaf names were rejected: two `ProjA`s under
  different parents collide, and disambiguating only on collision makes a
  key change when an unrelated Project is added elsewhere in the tree —
  silently invalidating a `batch.yaml` designation and every sheet row that
  named it. `removals.apply_sheet` already resolves the key with
  `os.path.join(root, row.project)`, so a relative path in a sheet's
  `project` column needs no change there.

- **A `project.yaml` directory with no Experiment Directory is judged by
  what is under it.** If its children hold recordings (`prj.has_experiment_data`)
  it is a Project whose configs were never scaffolded: it is listed, blocked,
  with "Experiment configs…" as the fix. Otherwise it is a grouping folder
  someone dropped a `project.yaml` into, and the walk passes straight
  through. Treating `project.yaml` alone as a stop (today's
  `prj.is_project_dir`) would let one stray file hide every Project beneath
  it — exactly the mistake recursion exists to tolerate.

- **Blocked is a property of the Experiment Directory, not the Project.** A
  **Blocked Experiment** is one a run cannot use: an **Unfiled Recording**
  (export loose at the root), a recording with no `tracking_config.yaml`, or
  a configured directory with no recording at all. A Member with four healthy
  replicates and one blocked replicate runs the four. Blocked Experiments are
  reported before the run and again in its summary; a run is **never refused**
  because of one — ADR-0009 is continue-on-error and ADR-0010's rule stands:
  a stale folder must not stop ten Projects at 2am. A Member with *no* usable
  replicate starts unchecked, since it can only produce a failure.

- **Filing is an allowlist, and the sidecars are exempt by rule.** Filing an
  Unfiled Recording moves `.xlsx` and `.csv` — exactly what `Arena` loads
  (`<name>.xlsx`, `<name>_Data_*.csv`) — into `data/`, and every other loose
  file into `extra_files/`. **Every `.yaml`/`.yml` stays at the experiment
  root**: YAML there is configuration or declaration, never data. That
  protects `tracking_config.yaml` (moving it un-makes the Experiment
  Directory) and `removed_regions.yaml` (moving it silently returns removed
  flies to the analysis — the precise failure ADR-0010 exists to prevent),
  and it protects any sidecar added later. ADR-0010 relied on the old
  Convert's *non-YAML* rule for the same protection; this is the same
  guarantee stated deliberately rather than inherited by luck. Subdirectories
  are never touched.

- **Filing never overwrites and never guesses.** A destination name that
  already exists in `data/` is skipped and reported, leaving both files where
  they are. More than one workbook at the root — or one at the root and one
  already in `data/` — refuses to file at all and asks which is the
  experiment. `Experiment` globs `data/*.xlsx` and, given several, logs
  "Multiple .xlsx files; using X" and carries on: a one-click fix that can
  silently change which recording an analysis is of is worse than a folder
  that stays blocked.

- **Run batch opens a preflight, always.** One modal listing the discovered
  Members with their relative paths, replicate counts, and per-Experiment
  block reasons; a reason-matched action on each (file the recording;
  "Experiment configs…" for a missing config — reusing the one design-aware
  scaffolding path rather than writing a second); the Removal Sheet preview;
  then Run or Cancel. It is shown even when nothing is wrong, because with
  recursive discovery the target list is no longer obvious from the folder
  you picked, and that list is the one thing no other surface states.

- **The Removal Sheet is previewed, declinable, and scoped to the run.** The
  preflight shows every row's outcome (applied / already declared / conflict /
  unknown) and a single "apply before running" switch, checked by default;
  declining skips the write for that run and never edits the sheet or any
  standing declaration. Rows naming a Member that is not in the run are
  skipped and reported rather than written — this changes ADR-0010's
  behaviour, where `apply_sheet` walked every row regardless of which
  Projects were checked. Unchecking a Member means "do not touch this
  Project", and recursion now surfaces Projects the user may not have known
  were there.

- **The walk is cached per selection.** It runs when a batch folder is
  chosen and is invalidated by choosing another, filing a recording,
  scaffolding a config, or finishing a run; an explicit **Rescan** covers
  changes made outside the app. `is_batch_dir` short-circuits at the first
  Project found. Without this, `_refresh_tiles` would re-walk the tree on
  every checkbox click and every finished task.

- **Only the selected Batch's `batch.yaml` governs.** A nested grouping
  folder may be a Batch in its own right and carry its own designation; it is
  ignored and named in the run log. ADR-0009's resolution is already three
  steps (batch `project_scripts:` → the Project's `scripts:` → built-ins) and
  a fourth that depends on where the user clicked would be unmemorable.

- **The fix takes the free gesture.** On the batch table, double-click
  selects a Project (ADR-0009) and right-click opens its removed-regions
  window (ADR-0010). "Fix layout…" becomes a second entry on that existing
  menu, disabled when the Member has nothing blocked. No gesture is
  reassigned.

## Consequences

- `batch.yaml` and Removal Sheets written after this change may contain
  path-shaped Member keys; older flat ones keep working unchanged, and a key
  is stable as long as the folder is not moved within the Batch.
- `extra_files/` starts appearing in experiment directories. It is inert —
  nothing reads it — and exists so filing never has to decide that an
  unrecognised file is disposable.
- A Batch can now contain Batches. Whichever one is selected is the one that
  runs; nesting has no other meaning.
- The run summary gains a per-Member replicate ratio (`3/5`) and a blocked
  list, so "succeeded" can no longer be read as "analyzed everything".
- Discovery, blocked status, and filing are batch-level concerns but
  experiment-level facts; the same status logic feeds the Project view, where
  an Unfiled Recording is equally invisible today.

## Amendment (2026-08-24, at implementation): three rules the code sharpened

Written after building it and auditing the result. Each of these is the ADR
above being wrong in a way the code had to fix, not the code drifting.

- **`is_batch_dir` does not short-circuit — the cache is what makes it
  affordable.** The ADR said it would stop at the first Project found. That
  made it a *different* predicate from the walk, and the two disagreed at the
  root: a batch folder carrying a stray or legacy `project.yaml` (every flat
  batch anyone once clicked "Edit config" on has one) enumerated its Members
  perfectly and showed an empty, dead Batch card, because the short-circuit
  asked `is_project_dir` and gave up. One predicate — `batch.project_kind` —
  now decides Project-hood for the library and the UI alike, `is_batch_dir` is
  `bool(discover(...)["members"])`, and the per-selection cache carries the
  cost.

- **The prune bar is a *usable* replicate, not "evidence of recordings".** The
  ADR named `prj.has_experiment_data` as the test for a marker directory. The
  walk uses `layout.experiments_in` instead, which sees Unfiled Recordings
  that `has_experiment_data` is blind to — but that made the bar *lower*, and
  a grouping folder holding one `template/tracking_config.yaml` pruned the
  walk and hid every real Project beneath it. Pruning now requires at least
  one replicate the run could actually use; anything weaker descends first and
  is only called a Member if no real Project turns up below it.

- **"No Experiment Directory is analyzed twice" needed enforcing one level
  down too.** The ADR guaranteed it for the nested-`project.yaml` case, which
  the walk's pruning handles. It said nothing about two symlinked replicate
  directories pointing at one recording — `Project.experiment_names` counted
  both, so the same flies were analyzed twice and stacked into the Combined
  Analysis under two labels. Both `Project.__init__` and
  `layout.experiments_in` now de-duplicate by real path.

Also corrected while implementing, all of them pre-existing: a Removal Sheet
cell could escape its root via `../` or an absolute path; two spellings of one
experiment directory in a sheet silently discarded each other's regions; one
unwritable sidecar aborted the whole sheet and reported "nothing written"; a
sheet saved as `Removed_Regions.csv` was invisible; and four `glob.glob` call
sites interpolated a path without `glob.escape`, so a folder named
`Sept2026 [pilot]` made every replicate under it read "not analyzed" forever.

## Amendment (2026-08-24, after adversarial review): four more rules

An adversarial pass over the finished implementation confirmed these by
execution. All four are the same shape — a rule stated for one marker, one
surface, or one spelling, and not for its twin.

- **A stray `tracking_config.yaml` gets the same treatment as a stray
  `project.yaml`.** Stopping unconditionally at a config marker hid every
  Project beneath one file, and at a batch root it emptied the Batch — with no
  skipped entry and a run summary whose denominator counted only the
  survivors. `pytrack-config <folder>` + Save creates exactly that file. The
  walk now descends first and stops only if nothing turns up below.

- **A Member repaired inside the preflight joins the run.** Check state is
  derived from what the user actually said plus what the Member can do *now*,
  never from the previous check column: filing a recording is what *makes* a
  Member runnable, so re-deriving "unchecked" from the pre-repair state
  excluded the very Project the user had just fixed — silently, on the mixed
  case, and on every subsequent run until someone noticed the checkbox.

- **Both preflight entry points end the same way.** Opened from the table's
  right-click "Fix blocked experiments…", the dialog's Run button was inert
  because that caller dropped the result. There is one review window and one
  meaning for its buttons.

- **Scoping means "one of the Members running", with no exceptions.** A row
  with a blank `project` cell used to pass the filter as "the root itself" —
  the Project-root sheet's contract — and so wrote outside every checked
  Member at a Batch root. That contract now applies only where it was written
  for: a sheet at a Project root, where no scoping is asked for at all.

Refuted by execution and deliberately left alone: `run_batch`'s key matching
(it normalizes both sides), the heading refresh, the re-armed sheet switch,
the `extra_files/` lazy check, and the symlinked-workbook refusal — each was
reported against an older snapshot of a file this work had already changed.

## Amendment (2026-11-26): the parked files go in `data/extra_files/`

Filing now parks every loose file that is not the recording in
`data/extra_files/`, not `extra_files/` at the experiment root. The allowlist
is unchanged: `.xlsx` and `.csv` into `data/`, YAML and a Removal Sheet stay
at the root, and nothing is overwritten.

- **Why.** labdata files an experiment's `data/` as the recording, and a run
  folder there is itself an Experiment Directory. With `extra_files/` at the
  root, a DTrack export's background image and light programs were filed
  without the recording they belong to. They cannot be told apart by name
  (both are optional, and the person names them), so they move with
  everything else loose. labdata keeps them in the run's `data/extra_files/`
  too, so the two layouts are one (labdata decision BX).
- **Nothing reads them.** The loader and the classifier look only at the
  files directly in `data/` (`glob('*.xlsx')`, `_Data_*.csv`,
  `_recording_files`), and nothing filing parks is a `.xlsx` or `.csv`, so
  the folder can never make the recording ambiguous. Clock repair copies
  the whole of `data/`, `data/extra_files/` with it, and rewrites only the
  export.
- **Older folders stay as they are.** One filed before this keeps its
  `extra_files/` at the root; nothing moves it. labdata files that one into
  the run's `data/extra_files/` as well.
