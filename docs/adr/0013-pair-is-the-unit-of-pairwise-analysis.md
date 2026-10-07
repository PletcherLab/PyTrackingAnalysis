# The Pair is the unit of pairwise analysis: one summary row per Pair

## Context

A pairwise-interaction experiment puts two flies in each tracking region, and
treatment is assigned per region, so both flies of a **Pair** always share
every factor level. Their numbers are not independent: proximity is one
measurement of the pair (`PercentInteracting` is identical for both flies),
and each fly's movement and position are shaped by the other's. Yet
`Arena.summarize()` returned one row per *fly*, and three consumers handled
that differently:

- `Experiment.stats()` passed `remove_partners=True` for `*Interacting*`
  metrics only, keeping fly 0's row of each region — right for interaction,
  but it discarded fly 1 for anything else.
- `report_figures.build_analysis_figures` passed `remove_partners=True` for
  the *whole* summary, so the report's locomotion figure silently showed fly
  0 of every pair and nothing of fly 1.
- The Project's Combined Analysis stacks each replicate's `_Summary.csv`,
  which is per fly, so every pair's interaction value entered the pooled
  Welch/Tukey tests and the mixed model **twice** — double the n, p-values too
  small. (It also never ran: `Project._metrics()` fell back to `FinalPI`
  because the pairwise rows of `_TRACKING_TYPE_METRICS` are built at runtime,
  so the bug was latent rather than live.)

## Decision

- **The Pair is the experimental unit for every pairwise-tracker result.**
  `_Summary.csv` and `_Summary_Facet.csv` hold **one row per Pair**: per-fly
  measures (distance, activity, centrophobism, exploration — ADR-0015) are the
  mean of the two flies, pair measures (interaction, encounters, distance
  between the flies — ADR-0014) appear once. `Name` and `TrackingRegion` are
  both the region (`T_0`); `ObjectID` is dropped.
- **The individual flies are not lost:** `_Summary_PerFly.csv` and
  `_Summary_Facet_PerFly.csv` keep one row per fly, for inspection. Nothing
  statistical reads them.
- **Aggregation happens in `Arena`, after exclusion.** Excluded flies are
  dropped first (ADR-0003's choke point), then the survivors are averaged per
  region. Because every pairwise exclusion acts on the whole region (ADR-0016,
  ADR-0010), a half-excluded pair cannot arise.
- **`remove_partners` is retired for `PAIRWISEINTERACTIONTRACKER`.** With one
  row per Pair there is nothing to de-duplicate; stats, report figures and the
  Project all read the same rows, so the Project's n is right with no
  Project-side special case. `PAIRWISEINTERACTIONCOUNTER` already summarizes
  per region and is unchanged.
- **This applies to every pairwise-tracker project, Custom included** — it is
  a correction, not Paired Open Field policy.

Alternatives considered: per-fly rows with a pair random effect in a mixed
model (more power, but the pooled Welch/Tukey tests that sit beside the mixed
model everywhere would still double-count, and same-treatment pairs give the
extra fly little to say); keeping per-fly rows and fixing each consumer's
`remove_partners` (the three call sites above show how that drifts — any new
consumer reintroduces the bug).

## Consequences

- Re-running an existing Custom pairwise project changes the **shape** of its
  `_Summary.csv`: half the rows, region-named, averaged locomotion columns.
  Downstream spreadsheets keyed on `T_0_0`/`T_0_1` must move to
  `_Summary_PerFly.csv`.
- n in every pairwise test is the number of Pairs. Results previously read
  off fly-0-only locomotion figures will shift.
- `Name` no longer equals a tracker name for these rows, so anything mapping
  summary rows back to trackers must go through `TrackingRegion`.
