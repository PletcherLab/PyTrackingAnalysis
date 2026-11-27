# Removed regions

Some flies are lost in ways no automatic check can see: the fly died partway through the recording, escaped while the plate was loaded, or the well was empty from the start. The tracker keeps producing rows regardless - a dead fly is still a blob with a position - so only you can say what happened.

A **removed region** is a tracking region you declare out of the analysis, with a reason in your own words.

## How it differs from the automatic checks

| Check | Who decides | Effect |
|-------|-------------|--------|
| Low-transition exclusion (`min_transitions`) | The pipeline | Removes the fly from every result. Valence only |
| Low-movement flag (`min_movement`) | The pipeline | Reports the fly, never removes it. Valence only |
| **Removed region** | **You** | Removes every fly in the region from every result. Any experiment type |

## What a removal means

**The unit is the region, not the fly.** You tick `T_14`, not `T_14_0`. That is what you observe at the rig, it is the key the config already uses, and it survives a re-export that renumbers object IDs. Every tracker in the region goes - for the usual one-fly-per-well plate that is exactly one; a well holding two animals loses both.

**It is all-or-nothing.** There is no "exclude only after minute 45". A dead animal still registers as occupancy wherever it died, so its PI is dragged toward the side it died on and its transition count collapses - the numbers are unreliable for the whole recording, not just the tail. Censoring at a time of death would also compare flies observed for different lengths of time.

**Every removal carries a reason.** Free text: `dead at ~20 min`, `escaped during transfer`, `well never loaded`. Ticked and left blank is recorded as `Undefined`, so an unexplained removal is still visible as one.

## The window

**Project** tile -> **Removed regions...**, or right-click a project row in the Batch panel. It lists every tracking region of every replicate in the Project:

- **Remove** - tick to remove; regions already declared open ticked.
- **Experiment** / **Region** - the replicate directory, and the region as `tracking_config.yaml` spells it.
- **Treatment** - that region's factors, so you can see what you are dropping.
- **Data** - `no data` when the replicate has been analyzed and the region produced no tracker at all (an empty well). Blank when the replicate has not been analyzed, and blank for a region that produced a fly - including one that is currently excluded.
- **Reason** - free text, editable.

**Filter** narrows to one replicate or region; **Show removed only** reviews what is already declared. Only regions the config declares are listed, so the window opens instantly even for a Project of eighty replicates.

**Save** writes one file per replicate you changed. **Cancel** discards. A replicate whose `removed_regions.yaml` cannot be read is greyed out, with the reason under the table: saving would overwrite what it says, so fix or delete it by hand first. Unticking a region deletes its declaration - that is how you un-remove a fly, and it is deliberately explicit.

If the experiment you have loaded is one you changed, its data is re-filtered immediately: press a plot button straight afterwards and the fly is already gone.

## One replicate at a time

Two windows that show one replicate also have the same tick and reason, so you can remove a region where you happen to be looking at it:

- **Config Editor** -> **Tracking regions** tab: the **Removed** and **Reason** columns at the right of each row. **Save** writes the config and then the removed regions. This is also how a standalone experiment that is not in a Project gets a window for its removals.
- **QC Viewer** -> **Removed regions**, under the trackers table: one row per region, with the number of flies the recording has in it (`no data` for an empty well). Selecting a tracker selects its region. **Save removed regions** writes them and re-filters the loaded experiment; closing with unsaved ticks asks first.

Both write only what you changed in them, over the file as it is when you save, so a removal declared meanwhile in another window or by a removal sheet is kept. A reason can be typed once its region is ticked. Unticking a region and ticking it again brings back the reason it was saved with.

Both save to the folder they were opened on: the replicate's. For a replicate that reads its recording's config through `tracking_member.yaml`, that is still the replicate's folder, never the recording's. The columns are greyed out, with the reason underneath, when that folder is a run folder in the lab's labdata store (it holds `run.yaml`), or when its `removed_regions.yaml` cannot be read: saving would overwrite it, so fix or delete it by hand first.

## The file

Saving writes `removed_regions.yaml` at the root of the replicate's folder: beside its `tracking_config.yaml`, or beside its `tracking_member.yaml` when it reads its recording's config:

```yaml
removed_regions:
  T_14: dead at ~20 min
  T_22: escaped during transfer
```

Edit it by hand if you prefer - the windows and the file are the same thing. Keys must match the config's region names exactly; one that matches nothing is warned about and ignored, never fatal. Deleting the file un-removes everything.

It is deliberately *not* part of `tracking_config.yaml`: that file is the design specification, and how a run turned out is not design. Because the declaration sits in the experiment directory, it travels with the recording wherever you copy it.

## Removal sheets: many projects at once

Keep the notes where lab notes already live. Put `removed_regions.csv` (or `.xlsx`) at the batch folder - or at a Project root - with these columns:

| project | experiment | region | reason |
|---------|-----------|--------|--------|
| Starved2026 | Rep3 | T_14 | dead at ~20 min |
| Fed2026 | Rep1 | T_2 | well never loaded |

`project` is needed only at a batch folder, and is the Project's path relative to it - `Sept2026/ProjA` for a Project under a grouping folder, or just `ProjA` at the top level. Headers are matched loosely (`replicate`, `well`, `note` all work); a missing reason becomes `Undefined`.

Applying the sheet writes its rows into each experiment's `removed_regions.yaml`. Nothing reads the sheet at analysis time, so a Project keeps its removals wherever you copy it, spreadsheet or no spreadsheet.

It is applied at three moments, all deliberate:

1. at the start of a **Batch run**, before any Project script;
2. when you press **Apply removal sheet...** in the Batch panel (greyed out until the chosen folder holds a sheet);
3. when you open **Removed regions...** on a Project that has one - you are asked first.

Choosing a folder never applies anything by itself: browsing a colleague's batch folder must not rewrite their experiments.

Applying is additive and safe to repeat. A region already declared keeps the reason it has; if the sheet disagrees, the row is reported as a **conflict** and your existing wording is kept, because the sheet is re-applied on every batch run. Deleting a row from the sheet does not un-remove anything - untick it in the window.

Every row is reported: `applied`, `already declared`, `conflict`, `unknown project`, `unknown experiment`, `unknown region`, `incomplete`. Nothing here aborts a run, so check the log for rows that did nothing.

## What it does to the results

Removed flies are merged with anything the experiment type excluded automatically, and the combined list is applied in one place, so no figure and table can disagree.

**Filtered** - the fly is absent from: the summary CSVs, every plot, `*_Stats.txt`, the Project's Combined Analysis, and therefore the pooled statistics, the mixed model, and the publication figures.

**Not filtered** - the fly is still shown in: everything under `qc/`, the QC figures in the report, and the QC Viewer. Quality control describes the recording, not the analysis population.

Other effects worth knowing:

- `min_transitions: 0` turns the automatic exclusion off; your removals still apply.
- A fly caught by both appears once, its reason naming both causes, yours first: `Removed: dead at ~20 min; Low transitions`.
- The low-movement flag is computed after exclusions, so removed flies never count toward the ">50% flagged" verdict.
- A Custom experiment gains an `_Excluded.csv` it never produced before, without the `Transitions` column.

## Re-running after a removal

Declaring a removal does not rewrite results already on disk. A replicate whose declarations have not reached its saved analysis is **stale**: the replicate table and the Project report say `re-run needed`, and a script step told to skip analyzed replicates re-runs it anyway. **Create/Update report** and the default `batch` script re-analyze everything, so in the usual workflow it is picked up automatically.

## Where removals are reported

- **`analysis/<name>_Excluded.csv`** - one row per fly, with a `Reason` column: `Removed: <your text>`, `Low transitions`, or both. Written even when empty.
- **`analysis/<name>_Stats.txt`** - a preamble line naming the counts by cause, plus a warning for any declaration that matched nothing.
- **The experiment report** - `Excluded flies: 4 (3 removed, 1 low transitions)` on the cover, and an **Excluded flies** table before any result, listing fly, region, treatment, transitions and reason, with a footnote per unmatched declaration.
- **The Project report** - `7 (4 removed)` in the per-replicate table (red, `re-run needed`, when stale) and an **Excluded flies** table covering every replicate.
- **AI summaries** - the exclusion file is part of what a provider is shown, so the reasons reach the narrative, and through it the batch narrative.

See the **Project actions** and **Outputs** topics for the surrounding workflow, and `doc/guide.md` section 9 for the full reference.
