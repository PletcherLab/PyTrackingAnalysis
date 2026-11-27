# Experimenter-declared region removal is a sidecar the Batch sheet writes into

Amended by ADR-0021: one replicate's removals are also ticked in the Config
Editor's regions grid and the QC Viewer.

## Context

Flies die, escape, or were never loaded — outcomes only the experimenter
observes, invisible to every automatic criterion. Valence's Low-Transition
Exclusion (ADR-0003) catches most of them by accident (a dead fly stops
transitioning), but not the fly that died at minute 65 after transitioning
plenty, and it can never say *why* a fly is out. Experimenters routinely run
many Projects through a Batch Run (ADR-0009), so a window is not enough: the
declaration must also be authorable in a spreadsheet, from lab notes, without
opening the app.

## Decision

- **The unit is the tracking region, not the fly.** A **Removed Region** is a
  region the experimenter declares out of the analysis; every tracker in it
  becomes an Excluded Fly, so a well holding two animals loses both. Regions
  are what a human observes at the rig, they are already the addressable key
  in `tracking_regions:`, and they survive a DTrack re-export that would
  renumber object IDs. Region→tracker expansion matches on the underscore
  boundary (`name == region or name.startswith(region + "_")`) — a plain
  `startswith` makes `T_1` swallow `T_10`.
- **Removal is all-or-nothing.** The whole recording for that region goes,
  not just the span after the event. A dead animal still registers as
  occupancy wherever it died, dragging PI toward ±1 and collapsing
  Transitions, so its numbers are corrupt throughout — and censoring at a
  recorded time would give different flies different window durations,
  silently comparing 60-minute flies against 35-minute ones in the pooled
  tests and the mixed model. Time-censoring is a separate feature, not a
  variant of this one.
- **It is an observation, not a type policy — so it applies to every
  Experiment Type**, Custom included. This amends ADR-0003's "Custom
  Experiments are never filtered": a Custom Experiment is still never
  filtered *by policy*, but a declared removal filters it. `Experiment`
  merges the type's `compute_exclusions` result with the declared removals
  and hands the union to `Arena.set_excluded_trackers` — the choke point
  ADR-0003 already established. `_Excluded.csv` still appears only when
  there is a criterion or a declaration, so untouched Custom projects are
  byte-identical.
- **Declared in `removed_regions.yaml` at the Experiment Directory root**, a
  mapping of region → free-text reason (`Undefined` by default):

  ```yaml
  removed_regions:
    T_14: dead at ~20 min
    T_22: escaped during transfer
  ```

  Deliberately *not* in `tracking_config.yaml`: that file is a design
  specification, and how a run turned out is not design. Rejected: a
  `removed:` key inside each `tracking_regions:` entry (mixes outcome into
  the design spec, though the Config Editor would have round-tripped it);
  reusing `<exp>_Excluded.csv` as the input (it is a derived artifact
  rewritten by every `run_analysis`, and it does not exist until the first
  run — but the experimenter knows a fly died before anyone analyzes
  anything); a CSV in `analysis/` beside `_Notes.txt`; and a Project-level
  manifest (a second authority, with "which one wins" rules nobody
  remembers). A sidecar at the experiment root is also safe from Batch
  Tools' *Convert subdirectories*, which moves every non-YAML top-level file
  into `data/`.
- **One exclusion list, one row per fly, a `Reason` column.** `_Excluded.csv`
  gains `Reason`; a fly caught by both criteria appears **once**, with the
  string documenting both, the experimenter's observation first — `Removed:
  dead at ~20 min; Low transitions`. An observation outranks an inferred
  rule, and counting stays a `len()` everywhere it already is. Custom
  Experiments get the same file without the `Transitions` column. Counts stay
  a single number — `Excluded flies: 7 (4 removed, 3 low transitions)` —
  because the analysis population is `flies − excluded` and that arithmetic
  must have exactly one input; a separate Removed column invites someone to
  add the two together.
- **Both reports name the removed flies, not just count them.** In the
  experiment report, the exclusion section generalizes: today it is
  `_valence_exclusions`, gated on `min_transitions`, so it renders nothing for
  a Custom Experiment and announces "exclusion is off" at threshold 0 — both
  wrong once a removal can exist without a Valence policy. It renders whenever
  there is an exclusion frame, gains a **Reason** column, still says the
  low-transition policy is off when it is *and* lists the removals below that,
  and carries the unmatched-declaration footnotes. In the Project Report, the
  per-replicate table's `Excluded` cell carries the breakdown (`7 (4
  removed)`) and a **Removed flies** table lists every removal across the
  Project — Experiment, Fly, Region, Treatment, Reason — built from the
  aggregated `<project>_Excluded.csv`, which already concatenates each
  replicate's file with an `Experiment` column. When nothing was removed, one
  sentence says so: absence never needs interpreting, the same rule that
  writes `_Excluded.csv` even when empty (ADR-0003). A removal that no reader
  can see is indistinguishable from data quietly going missing, which is the
  failure this whole feature exists to prevent.
- **The Batch spreadsheet is a writer, never an overlay.**
  `removed_regions.csv` (or `.xlsx`) at a Batch root — or a Project root, same
  contract with `project` optional — with columns `project, experiment,
  region, reason`. Applying it **writes into each Experiment's sidecar**;
  nothing reads it at analysis time. An overlay was rejected on
  reproducibility: copy a Project to another drive, or rename the sheet, and
  an overlay silently returns removed flies to the analysis with nothing
  anywhere in the Project recording that they were ever out. It would also
  make a Batch own part of every Project's analysis population, which
  contradicts a Batch being a processing convenience that holds no analysis
  outputs of its own (ADR-0009).
- **Applied at Batch Run start and on an explicit button — never on
  browsing.** Selecting a Batch folder reports what it found and offers to
  apply; it does not write. Pointing the Hub at a colleague's Batch to look
  at it must not silently edit 80 experiment directories.
- **Merge, existing declaration wins, conflicts are loud.** The sheet is
  re-applied on every Batch Run, so letting it win would keep resetting a
  reason someone refined in the window; and replacing would let a row tidied
  out of a spreadsheet quietly return a fly to the analysis. Removals only
  accumulate from the sheet; un-removing is an explicit act in the window. A
  row whose reason differs from the standing declaration is reported as a
  conflict, not skipped quietly.
- **Two entry surfaces, one authority.** A button on the Project card opens a
  checklist of every region defined in each replicate's
  `tracking_config.yaml` (the design's plate — no raw-data parsing, so a
  Project of 80 replicates opens instantly), annotated `no data` where a
  saved `_Summary.csv` has no such region. The same window opens from the
  Batch panel by **right-click** on a project row — double-click is taken:
  it selects that Project (ADR-0009), as it loads a replicate in the
  replicates table (ADR-0008). The Hub cannot load a standalone Experiment
  Directory at all (ADR-0008), so a standalone experiment declares removals
  by hand-editing its sidecar.
- **Stale analyses are detected by content, not mtime.** A replicate is stale
  when the regions in its `removed_regions.yaml` disagree with the removal
  rows in its saved `_Excluded.csv`; `skip_analyzed` treats a stale replicate
  as not analyzed and re-runs it. Content comparison survives copying a
  Project between drives, which mtimes do not. Saving the window for the
  currently loaded experiment re-applies exclusions in memory immediately
  (recompute the set, call `set_excluded_trackers`) — without that, the
  window appears to do nothing until the next full analysis.
- **A declaration that matches nothing warns; it never aborts.** Unmatched
  regions are warned in the run log *and* footnoted in the report, which is
  the artifact anyone actually reads after an overnight batch; unmatched
  experiments or projects in the sheet are summarized as a count at the top
  of the Batch Run log. Aborting was rejected: one stale note would kill an
  unattended run across ten Projects at 2am.

## Consequences

- Results change relative to earlier runs of the same data, as ADR-0003's did.
  The audit trail is the defence: `Reason` records who removed what and why.
- `_Excluded.csv` files written before this change have no `Reason` column,
  so a Project mixing old and new files aggregates with `NaN` reasons until
  every replicate is re-run; they read as `(not recorded)` rather than being
  migrated.
- Data-quality output still shows removed flies — QC describes the recording,
  not the analysis population (ADR-0003).
- The AI payload already ships `_Excluded.csv` to the provider, so reasons
  reach the AI Summary and — one level up, through the Projects' own
  narratives — the Batch AI Narrative with no further plumbing.
- The `Stats.txt` exclusion line must render from the exclusion frame rather
  than from `min_transitions`, since a Custom Experiment can now have
  exclusions with no threshold in sight.
- With `min_transitions: 0` the Low-Transition Exclusion is off but
  declared removals still apply — off means off for the *policy*, not for
  the experimenter.
