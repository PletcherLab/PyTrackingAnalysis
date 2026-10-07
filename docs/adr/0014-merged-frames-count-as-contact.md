# Merged frames count as contact; encounters are hysteresis bouts

## Context

`PercentInteracting_<d>` is the fraction of *valid* frames in which the two
flies of a Pair are closer than `d`. A frame was valid only when both flies
were tracked at High quality as two separate blobs; every other frame left
both the numerator and the denominator.

When two flies touch, DTrack sees one blob and loses one of them. In the one
real pairwise recording we have (`Arena_R_Code/.../XVR001`, 2 pairs, 25,001
frames) the lost frames are overwhelmingly these merges: the last valid
distance before a run of lost frames has a median of ~21 px — the closest
1–5% of all valid distances — and in one arena every lost run followed a
contact. Excluding them removed 2–5% of the recording, almost all of it
contact, so the metric was biased **down**, most at the smallest thresholds:
with the rule below, one arena's `PercentInteracting_4` goes from 0.093 to
0.123 — a third of its contact time had been discarded.
The two code paths also disagreed: the slow preprocessing path in
`Arena.calculate_distances_for_pairwise_tracker` already wrote distance 0 for
a collapsed blob, while the tracker's validity mask threw the same frame away.

`PercentInteracting` also cannot tell many brief meetings from one long
huddle — approach and contact-seeking from staying together.

## Decision

- **A lost run is classified by its brackets.** A maximal run of non-valid
  frames is a **Merged** run when the last valid distance before it *and* the
  first valid distance after it are both below `merge_distance_mm` (yaml
  `global:`, default 3 mm — just above two touching flies' centroid spacing,
  below the smallest interaction distance). Merged frames are valid, at
  distance 0, so interacting at every threshold. Every other lost run —
  including any run touching the start or end of the recording, which has
  only one bracket — stays excluded: the distance there is genuinely unknown.
  Requiring *both* brackets is the conservative choice: flies cannot part and
  rejoin unseen within a short run at 10 fps.
- **Classification runs once over the whole recording**, before any windowing,
  so a run straddling a phase boundary is judged with its full context.
- **The inference is visible**: `MergedFraction` (merged ÷ valid frames) sits
  beside `PercentInteracting_<d>`, so a reader sees how much contact time was
  inferred rather than measured.
- **One rule for both paths.** The preprocessing path computes raw distances
  for two-blob frames only; merge inference lives in
  `PairwiseInteractionTracker` alone. `PAIRWISEINTERACTIONCOUNTER`, whose
  pseudo-trackers use the same class, gets it too — the rule needs no stable
  fly identity, only the symmetric distance.
- **An Encounter is a bout below an interaction distance**, per threshold
  `d`: it begins when the distance drops below `d` and ends only when it
  rises above `d + encounter_hysteresis_mm` (default 0.5 mm); breaks shorter
  than `encounter_gap_s` (0.5 s) — above-threshold or lost — are bridged;
  bouts shorter than `encounter_min_s` (0.5 s) are dropped. Merged frames are
  inside a bout. Per window: `EncounterRate_<d>` (bouts per valid minute),
  `MeanEncounterDuration_<d>` (s), `LatencyToFirstEncounter_<d>` (min from
  window start; NA if none). Bouts are found within each window, so one that
  straddles a phase boundary is split.
- **Applies to every pairwise project, Custom included.**

Alternatives considered: reporting merges in a separate `PercentMerged`
column and leaving `PercentInteracting` alone (no silent change, but every
reader must add two columns to get contact time, and most will not); treating
any frame with one blob between two flies as distance 0 (`NObjects` cannot
tell a merge from one fly lost in a corner while the other is far away);
bouts as raw runs below `d` (a pair hovering at the threshold registers as
dozens of one-frame encounters).

## Consequences

- **Re-running an existing pairwise project raises its `PercentInteracting`**
  — by roughly the merged fraction, more at small `d`. Old and new values must
  not be pooled or compared.
- A window with **no** valid frame now reports `PercentInteracting_<d>` as
  NA (it used to report 0, which read — and was tested — as "never
  interacted" for a phase the recording never reached).
- `ValidFrames` grows by the merged frames, so the Low-Tracking Exclusion
  (ADR-0016) does not penalise pairs for touching.
- Per-fly spatial measures (ADR-0015) do **not** get a position for the lost
  fly in a merged frame; only the pair distance is inferred.
