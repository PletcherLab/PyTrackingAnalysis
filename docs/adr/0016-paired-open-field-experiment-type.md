# Paired Open Field is the second Experiment Type

## Context

Pairwise-interaction experiments have run as Custom Experiments: whatever
`tracking_config.yaml` said, no exclusions, no flags, a generic report with
one interaction figure. The assay asks two questions at once — do the flies
engage socially, and are they healthy and behaving normally in an open
arena — and needs the policy machinery only an Experiment Type has (ADR-0001,
ADR-0002): defaults, inclusion rules, report sections, an AI-summary
prompt. The measures themselves (ADR-0013, -0014, -0015) are corrections and
additions to the tracker that every pairwise project gets; this ADR is the
policy layered on top.

## Decision

**Identity.** `experiment_type: PairedOpenField`, displayed *Paired Open
Field Experiment*. It names the set-up rather than the construct because it
measures two constructs, and it leaves room for a single-fly sibling, *Open
Field*, built on the same measures (ADR-0015). It fixes
`PAIRWISEINTERACTIONTRACKER`.

**Rigs and calibration.** Small Arena, Arena Max, Colosseum and Movie.
Calibration is owned **per rig**: a preset rig takes its preset `fps` and
`mm_per_pixel` and rejects overrides — every threshold here is in mm, and a
mistyped override would silently rescale them all — while Movie requires
both. `ExperimentType.allow_calibration_override` grows a per-rig form for
this; Valence's all-locked behaviour is the special case.

**Plates come from the data.** No region count is fixed per rig: open-field
pairs may run in different inserts from Valence plates. The loader already
requires exactly two objects per region; validation requires every region to
have a treatment.

**Phases.** Default cutoffs `[10, 70]` — Acclimation, Experiment, Cooldown —
editable as for Valence (ADR-0001 amendment); the Primary Phase is the
Experiment phase.

**Thresholds.** `interaction_distances` defaults to `[4, 8, 10]` mm, the
lab's established set, and is written into a new config as a visible knob,
as are the other yaml-overridable defaults of ADR-0014/-0015
(`merge_distance_mm`, `wall_zone_mm`, `fly_length_mm`, `fly_width_mm`,
the encounter parameters) and `min_valid_fraction` below.

**Inclusion — every rule acts on the whole region**, because the Pair is the
unit (ADR-0013) and a fly without its partner is not an observation:

- **Low-Tracking Exclusion** (policy, ADR-0003 style): a Pair is excluded
  when its valid frames — merged frames included (ADR-0014), so touching is
  never penalised — are under `min_valid_fraction` (default 0.8) of the
  Primary Phase. No frames there counts as failing. Reason string
  `Low tracking`; 0 turns it off.
- **Possibly-Dead Flag** (advisory, never removes): a fly whose High-quality
  positions over the final 20 minutes of the recording lie within one body
  length (`fly_length_mm`) of their median — at the 99th percentile, so one
  glitch frame cannot clear a dead fly — so it never recovered. A dead
  partner turns "interaction" into time spent beside a corpse, but death is
  something only the experimenter observes (ADR-0010), so the report lists
  flagged regions and the experimenter confirms by declaring a Removed
  Region. A spread-based rule, not path length, because tracking jitter
  accumulates distance on a motionless fly.

**Statistics are tiered and uncorrected**, matching the app's convention
(raw p with a family-size note). The tiers belong to the tracking type, so a
Custom pairwise project runs the same tests:

- *Primary* (headlined): `PercentInteracting_<d>` for each `d`,
  `CentrophobismIndex`, `ExplorationAUC`, `TotalDistancePerMin`.
- *Secondary* (tested, labelled exploratory): `EncounterRate_<d>`,
  `MeanEncounterDuration_<d>`, `WalkingSpeed_mm_s`, `WalkingBoutsPerMin`,
  `MeanWalkingBoutDuration_s`, `ExploredFraction`,
  `ExploredFractionCenter`, `ExploredFractionPeriphery`.
- *Descriptive* (CSV only): everything else — latencies,
  `TimeTo50PctExplored`, the all-frames CI, wall measures, `MergedFraction`,
  mean/median distance.

The Project's Combined Analysis reads the same resolved list, replacing the
`FinalPI` fallback that left pairwise Projects with no tests at all.

**No chance baseline.** `PercentInteracting` is reported raw. Two flies that
ignore each other but both follow the wall meet more often than two using the
arena uniformly, so proximity partly re-measures centrophobism. The report
and the AI-summary prompt say so and present interaction beside the CI.

**Report.** Beyond the per-phase strip plots of the primary measures and the
exclusion, flag and ROI-check tables: occupancy heatmaps by treatment
(normalised arena coordinates, location multipliers applied, centre-zone
boundary drawn); the interaction time course by treatment (10-min window,
5-min step, mean ± SEM over Pairs); exploration coverage curves by treatment
within each phase; and a per-arena trajectory thumbnail for every region,
flagged and excluded regions marked.

**Publication figures.** New plot types `faceted_centrophobism` (−1..1,
reference 0), `faceted_exploration` (`ExplorationAUC`, 0..1) and
`faceted_interacting_<d>` — generated per configured threshold — beside the
existing `faceted_movement`. The Plot Editor offers only plot types whose
metric exists in the Project's summary, so Valence and Paired Open Field each
see their own.

Alternatives considered: improving the tracker in Custom mode only (no
exclusions, flags or type-specific report — and every config sets its own
thresholds); pseudo-pair or time-shift chance baselines (declined: the raw
measure is the lab's established reading, with the confound stated);
thresholds of 2.5/5 mm (≈ one and two body lengths; declined for continuity
with the legacy notebooks); Holm correction within the primary family
(declined for consistency with Valence); Valence-style fixed plates; a
`[10]` Acclimation/Test default.

## Consequences

- At `d` = 8–10 mm a Small Arena well is mostly "interaction"; those
  thresholds read differently across rigs, so interaction results compare
  within a rig only.
- Each threshold adds five columns and three tests (one primary, two
  secondary) per phase, so the default three thresholds add fifteen columns
  and nine tests per phase.
- A config with `experiment_type: PairedOpenField` and a preset rig must not
  carry `fps` or `mm_per_pixel`; a Movie config must carry both.
