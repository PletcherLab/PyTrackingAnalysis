# One replicate's removed regions are ticked in the Config Editor and the QC Viewer

Amends ADR-0010, whose "two entry surfaces" (the Hub's removals window and the
Removal Sheet) both work a Project or a Batch at a time. Changes nothing about
where a removal is stored or what it does.

## Context

On 2026-11-27 Scott asked for a Removed checkbox on each row of the Config
Editor's tracking regions grid, writing `removed_regions.yaml`, and then for
the same in the QC Viewer: "Let's incorporate into both." pyflic already works
this way for a single Member: its QC Viewer ticks chambers and saves that
Member's `remove_chambers.csv`.

Until now every way of declaring a removal went through a Project. The Hub's
window lists every replicate of one, and a Removal Sheet sits at a Project or
Batch root. ADR-0010 itself noted the gap: the Hub cannot load a standalone
Experiment Directory, so a standalone experiment could only have its sidecar
edited by hand. The Config Editor and the QC Viewer each open one replicate,
and they are where its regions are looked at: the grid lists them, and the QC
Viewer shows the data that tells a dead fly from a live one. The Hub opens the
QC Viewer after every load.

## Decision

- **Two more ways to write the same file.** The Config Editor's Tracking
  Regions grid gains **Removed** and **Reason** columns. The QC Viewer gains a
  **Removed regions** card: Remove, Region, Flies (`no data` for an empty
  well) and Reason. Both read and write the replicate's `removed_regions.yaml`
  and nothing else. The config is still never where a removal is written
  (ADR-0010).
- **Every removal still carries a reason.** A Reason cell is editable once its
  region is ticked, and blank is saved as `Undefined`. A checkbox alone would
  have made every removal from these windows `Undefined`, which is the audit
  trail ADR-0010 exists for.
- **A save writes only what the window changed** (`removals.update_removals`).
  It reads the file again at save time and applies only the regions whose
  tick or reason differs from what the window read. A removal declared
  meanwhile (the Hub's window, a Removal Sheet applied by a Batch Run, the
  other of these two windows) is kept. The Hub's window writes each replicate
  whole; it is a modal dialog, opened and saved in one sitting, while these
  windows stay open beside others for as long as someone works.
- **The folder is the one the window was opened on.** For a replicate holding
  `tracking_member.yaml` (ADR-0020), the Config Editor opens the recording's
  config but keeps the replicate folder for the removals, because the analysis
  reads them from the replicate folder, never from beside the recording. The
  QC Viewer loads the replicate folder itself.
- **A labdata run folder is shown and never written.** A folder holding
  `run.yaml` beside its config is a run in the lab's store. Its
  `removed_regions.yaml` is the run's own record, which labdata reads only on
  a refresh from the files and locks a week after registration. Writing it
  from here would leave the record and the file disagreeing with nothing to
  warn. Both windows show what it declares, greyed out, and say why. Whether
  run-level removals should ever be written from here is a separate decision.
- **A file that cannot be read is never written over** (`edit_refusal`): its
  columns are greyed out, as applying a Removal Sheet already refuses it. The
  Hub's window did not check: it read such a file as nothing declared and
  could save over it. It now greys out that replicate's rows and refuses to
  save it.
- **Removals are kept by region name, not by row.** In the grid they outlive
  every rebuild of the rows (a factor change, **From data**, **Generate**). A
  declared region with no row (deleted, renamed, or never in the design) is
  kept as it is and listed under the table: deleting a row is a change to the
  design, and un-removing a fly is only ever the explicit act of unticking it.
  **Remove selected** is renamed **Delete selected**, since "remove" is now the
  Removed column's word.
- **Saving follows each window's own habits.** The Config Editor writes the
  removals in its one **Save**, after the config is written, and counts them in
  its unsaved-changes mark. The QC Viewer has its own **Save removed regions**,
  re-applies the exclusions to the loaded experiment as the Hub's window does,
  and asks before closing or reloading over unsaved ticks.

## Consequences

- A standalone experiment now has a window for its removals.
- Three windows can edit one replicate's file. Because each writes only its own
  changes, the last save of a region wins, and nothing else is lost.
- These windows run as processes of their own, so a Hub with the same
  replicate loaded does not re-filter it when they save. Its plots show the
  removed fly until the replicate is loaded or analysed again. The saved
  results show it as stale in the meantime, as for any other removal (ADR-0010).
- The QC Viewer's trackers table still shows a removed fly: QC describes the
  recording, not the analysis population (ADR-0003).
