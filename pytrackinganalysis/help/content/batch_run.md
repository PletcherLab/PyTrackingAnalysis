# Batch runs

A **Batch** is a folder with Projects anywhere beneath it. Nothing marks it —
choose such a folder with the Batch panel's own **Choose batch folder…**
button (or Project tile → **Open Project**) and the **Batch** tile fills with every
Project inside it auto-loaded into the table; select a single Project and the
Batch tile reads "selection is a project — load its parent to batch" instead.
The Batch and Project tiles are never dimmed: they are the way in, always
available whatever is selected. A
Batch is a processing convenience: it runs many Projects unattended and
reports per-Project success or failure. It never pools results across
Projects — each Project keeps its own design and its own outputs.

## How projects are found

The search is **recursive**, so Projects do not have to be immediate children:
`Sept2026/ProjA` and `Archive/2025/ProjC` are both found, and the folders in
between are just folders. It stops at each Project — a Project's own
subdirectories are its Experiments, so an archived copy carrying its own
`project.yaml` inside a Project is never picked up as a second entry, and no
recording is analyzed twice in one run.

A Project is a folder with a `project.yaml` **and** at least one experiment
directory the run can use. A `project.yaml` with nothing usable under it is
treated as a grouping folder and the search continues underneath it — one
stray marker file must not hide every Project below it — and each such folder
is named in the Output tab so it is never silently ignored. The same log
names anything else the scan skipped: symlinked folders (never followed) and
folders it could not read.

Because a Project can sit at any depth, a row's name is its **path inside the
batch folder** (`Sept2026/ProjA`). A Project directly in the batch folder
keeps its plain name, so existing `batch.yaml` designations and removal sheets
keep working unchanged.

## Blocked experiments, and fixing them

An experiment directory the run cannot use is **blocked**, and its Project's
row turns red with the count. There are five reasons:

- **Unfiled recording** — the DTrack export is still sitting at the
  experiment's root instead of in `data/`, where the loader looks. This is the
  one that can be repaired in place: filing moves the `.xlsx` and its
  `_Data_*.csv` companions into `data/`, and any other loose file into
  `extra_files/` beside it. YAML files never move — `tracking_config.yaml`
  moved would un-make the experiment directory, and `removed_regions.yaml`
  moved would silently return removed flies to the analysis — and neither does
  a removal sheet. Nothing is ever overwritten.
- **No config** — a recording with no `tracking_config.yaml`. The fix is
  **Experiment configs…**, which scaffolds one from the project design.
- **No recording** — a config with no `.xlsx` in `data/`. Nothing to do but
  add the recording.
- **Ambiguous** — two workbooks, so "which one is the experiment?" cannot be
  answered for you. Sort it out in a file manager; filing refuses to guess,
  because the loader would quietly pick one and analyze it.
- **Duplicate** — the experiment's `data_folder:` names a recording another
  replicate of the same Project already reads. Only the first, in name order,
  is analyzed; point the other at its own recording or remove it.

An experiment whose config names its recording with `data_folder:` is never
filed. When that folder is missing, or the experiment also holds a recording
of its own, it is blocked as **no recording** or **ambiguous**, and the reason
names the path the key resolved to.

Blocked belongs to the *experiment*, never to the Project: a Project with four
healthy replicates and one blocked one runs the four. A Project with nothing
usable starts unchecked — it can only fail — but you can still check it.

## The review before a run

**Run batch** opens a review window first, every time. It lists the projects
that were found with their paths, how many replicates are usable, and every
blocked experiment with its reason and the button that clears it — **File
data…**, or **Experiment configs…**. **Rescan** walks the folder again, for
changes made outside the app.

It also previews the **removal sheet**: how many rows apply, how many are
already declared, and which conflict with a reason already in place. One
switch declines the sheet for that run — the sheet itself and every standing
declaration are left exactly as they are. Rows naming a project you unchecked
are skipped, not written: unchecking means "do not touch this project".

Nothing here blocks the run. Cancel does; everything else is there so you find
out before an overnight batch rather than during it.

## The Batch panel

- The **projects table** lists every Project found, with its replicate count
  (`3/5` means five experiment directories, three of which the run can use),
  whether a report exists, and its status. Checked rows join the next run —
  all are checked by default except a Project with nothing usable in it.
  **Double-click a row to select that Project**: the strip switches to it,
  exactly as if you had loaded its folder. There is no "up to batch" button —
  to return, load the Batch folder again. **Right-click** a row to fix its
  blocked experiments, or to open its removed-regions window.
- **Rescan folder** walks the batch folder again. The project list is read
  once when the folder is selected and reused after that, so rescan when you
  have added or repaired projects outside the app.
- The **Script** picker holds the designated Project Script. **Each
  project's own script (default)** runs the script each Project carries in
  its own `project.yaml` — every Project is created with one, named **Report
  pipeline**: create / update the project report (the Create-report button in
  one step — analyze every replicate, pool, build the PDF) → render
  publication figures (skipped when the Project has no `plot_specs.yaml`).
  Because it
  lives in the file you can open it in the Script Editor, read exactly what
  a batch run will do, and change it per Project. **Report pipeline
  (built-in)**, **Standard pipeline (built-in)**, and any scripts in
  `batch.yaml`'s `project_scripts:` section are also listed as explicit
  choices; a saved designation naming a script the Projects define
  themselves stays listed too, shown as "(from each project)".
- **Run batch** opens the review window (above) and then runs the designated
  script in every checked Project — one at a time, in path order —
  continue-on-error with `[project]`-prefixed log lines and a summary at the
  end. It unloads the loaded experiment first — the run rewrites every
  replicate's analysis. Only the batch folder's own `batch.yaml` designation
  applies; a `batch.yaml` in a folder further down is ignored and named in
  the log.
- **AI narrative of the batch** asks an AI provider, after the run, to
  synthesize the Projects' own narratives into `batch_ai_narrative.md` at the
  batch folder: results across the batch, Projects whose design looks
  compromised, and Projects that lost a lot of flies. Minor per-Project
  detail is left where it belongs, in each Project's own narrative. You pick
  the provider before the run starts (the same providers and models as the
  Project-level **AI narrative...**). Only Projects that succeeded are
  included, and the file's front matter names which ones they were.

  Note the interaction with the default script: `project_report` rebuilds
  each Project's Combined Analysis, and that **deletes** that Project's
  narrative. So a Project with no narrative left gets a fresh one generated
  first — one extra provider call per Project, each logged. A provider
  failure never fails the Batch Run itself; the run's own result stands.
- **Suppress new plot / output tabs** stops the output area opening a tab
  for every figure and every saved file. A Batch Run touches each replicate
  of each Project, so unchecked it can bury the Output tab under hundreds of
  them. The **Output** and **Errors** tabs keep updating as usual, and every
  figure and artifact is still written to disk — only the tabs are skipped.
  The switch applies to all runs while it is checked, not only Batch Runs.

## How the script resolves, per Project

1. `batch.yaml` `project_scripts:` — one recipe serving every Project,
   never copied into their `project.yaml`s.
2. The Project's own `scripts:` section.
3. The built-ins (Report pipeline, Standard pipeline).

With no designation (the default) a Project runs its own script — the one
named **`batch`**, or its first script if that one was renamed. A
Project whose `project.yaml` has no `scripts:` section **does not run**: it
is reported and skipped, and the run continues. There is no invisible
fallback — add a script in the Script Editor, or designate one above.

A name that resolves nowhere fails that Project; the run continues and the
summary says so. A Project that fails to load at all (say, a design
mismatch) is likewise recorded as that Project's failure, never the run's.
A Batch Run never creates or upgrades a `project.yaml`.

## batch.yaml

Optional, created only when needed: changing the Script picker away from the
default writes the designation (`script:`) there, and a `project_scripts:`
list can be added by hand. Leaving **Each project's own script** selected
never creates the file.
