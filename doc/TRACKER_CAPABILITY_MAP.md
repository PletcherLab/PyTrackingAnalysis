# Tracker & Counter Capability Map

*A comparison of every tracking type in PyTrackingAnalysis — what each one
computes, which plots and statistics it exposes, and exactly what must be wired
up to define the types that are currently incomplete. Grounded in the working
tree at `Tracker.py`, `Counter.py`, the four subclasses, `Arena.py`,
`Experiment.py` and `Parameters.py`.*

---

## 1. The type system at a glance

`Parameters.TrackingType` declares **nine** types, split into two *tracking
classes* (`TrackingClass.TRACKING` vs `TrackingClass.COUNTING`). A **tracker**
follows one identified blob per region across frames, so it can measure change
between consecutive frames — position, distance, speed, activity.

A **counter** is for video where flies are seen as a large group and individuals
**cannot be assigned from one frame to the next**. Because no blob's identity
persists across frames, every per-frame difference is undefined: **speed,
distance, and activity fractions do not exist for a counter.** All counter
analyses rely only on *current-frame* information — how many blobs are in each
counting region at each moment — aggregated to per-minute counts. This is the
fundamental reason the counter branch carries no kinematics, and it is a
property of the data, not a gap in the code.

| # | `TrackingType` | Class | Python class | Status |
|---|---|---|---|---|
| 1 | `TRACKER` | TRACKING | `Tracker.Tracker` | ✅ complete |
| 2 | `TWOCHOICETRACKER` | TRACKING | `TwoChoiceTracker` | ✅ complete |
| 3 | `XCHOICETRACKER` | TRACKING | `XChoiceTracker` | ✅ complete |
| 4 | `DDROPTRACKER` | TRACKING | — *(none)* | ⚠️ **orphan** — see §5 |
| 5 | `PAIRWISEINTERACTIONTRACKER` | TRACKING | `PairwiseInteractionTracker` | ✅ complete |
| 6 | `CENTROPHOBISMTRACKER` | TRACKING | — *(none)* | ⚠️ **orphan** — see §5 |
| 7 | `COUNTER` | COUNTING | `Counter.Counter` | ◐ loads, but no outcome metric/plots |
| 8 | `TWOCHOICECOUNTER` | COUNTING | `TwoChoiceCounter` | ✅ complete |
| 9 | `PAIRWISEINTERACTIONCOUNTER` | COUNTING | `PairwiseInteractionCounter` | ✅ complete |

**Two enum values have no implementation at all.** `DDROPTRACKER` and
`CENTROPHOBISMTRACKER` appear in the enum and in the `Experiment` policy tables,
but there is no class file and no branch for them in
[`Arena.create_trackers`](../pytrackinganalysis/Arena.py#L189-L198) — loading a
project of either type raises `ValueError: Invalid tracking type … Must be an
instance of TrackingType enum` before any analysis runs. These are the "trackers
that are not well defined" and are the subject of §5.

### Class hierarchy

```
Tracker                          Counter
├── TwoChoiceTracker             ├── TwoChoiceCounter
├── XChoiceTracker               └── PairwiseInteractionCounter  ── owns 2 ▸
└── PairwiseInteractionTracker  ◄──────────────────────────── PairwiseInteractionTracker
        (DDropTracker)  ← to add          (pseudo-trackers)
        (CentrophobismTracker)  ← to add
```

`PairwiseInteractionCounter` is the one cross-class type: it is a `Counter`
subclass that internally builds **two `PairwiseInteractionTracker` pseudo-trackers**
(one per `ObjectID`) and averages their results.

---

## 2. What each type puts in the summary CSV

Every type's `summarize(range_minutes)` returns a `pandas.Series` that becomes
one row of `_Summary.csv` / `_Summary_Facet.csv`. Subclasses **extend** the base
row via `pd.concat`, so columns are strictly additive down each branch.

### Tracker branch (per animal, per frame kinematics)

| Column group | `TRACKER` | `TWOCHOICETRACKER` | `XCHOICETRACKER` | `PAIRWISEINTERACTIONTRACKER` |
|---|:--:|:--:|:--:|:--:|
| **Base** — `Treatment, Name, TrackingRegion, ObjectID, ObsMinutes` | ✅ | ✅ | ✅ | ✅ |
| **Distance** — `TotalDistance, TotalDistanceHighQualityOnly, TotalDistancePerMin` | ✅ | ✅ | ✅ | ✅ |
| **Activity** — `PercSleeping, PercWalking, PercMicro, PercResting, PercUnmeasurable, AvgSpeed` | ✅ | ✅ | ✅ | ✅ |
| **Quality** — `PercHighQuality, StartMinutes, EndMinutes` | ✅ | ✅ | ✅ | ✅ |
| **Choice** — `FinalPI, FinalPercentage, <group counts>, Transitions, TransitionsPerMin` | — | ✅ | — | — |
| **X-choice** — `AvgX_mm, VarX_mm, AvgAdjX_mm, VarAdjX_mm, TotalXDistance_mm` | — | — | ✅ | — |
| **Interaction** — `MeanDistance, MedianDistance, ValidFrames, ValidFraction, MergedFraction, FramesInteracting_<d>, PercentInteracting_<d>, EncounterRate_<d>, MeanEncounterDuration_<d>, LatencyToFirstEncounter_<d>` | — | — | — | ✅ |
| **Open field** — `WalkingSpeed_mm_s, WalkingBoutsPerMin, MeanWalkingBoutDuration_s, CentrophobismIndex(AllFrames), WallZoneFraction, MeanWallDistance_mm, ExploredFraction(Center/Periphery), ExplorationAUC, TimeTo50PctExplored` | — | — | — | ✅ (`openfield.py`) |

*(`<group counts>` = one column per counting-region group, e.g. `Light`,
`NoLight`. `<d>` iterates over `parameters.interaction_distance_mm`.)*

A `PAIRWISEINTERACTIONTRACKER` summary is **one row per Pair** (ADR-0013):
`Arena.summarize` averages the two flies' rows by region after exclusion
(`Name` = the region, no `ObjectID`); `summarize(per_fly=True)` and the
`_Summary_PerFly.csv` companions keep the individual flies. Merged-blob frames
count as contact (ADR-0014); the open-field measures are ADR-0015.

### Counter branch (per region, per minute counts — no kinematics)

| Column group | `COUNTER` | `TWOCHOICECOUNTER` | `PAIRWISEINTERACTIONCOUNTER` |
|---|:--:|:--:|:--:|
| **Base** — `Treatment, Name, TrackingRegion, ObsMinutes, StartMinutes, EndMinutes` | ✅ | ✅ | ✅ |
| **Choice** — `FinalPI, FinalPercentage, <group counts>` | — | ✅ | — |
| **Interaction** — `MeanDistance, MedianDistance, ValidFrames, MergedFraction, FramesInteracting_<d>, PercentInteracting_<d>` | — | — | ✅ (averaged over the 2 pseudo-trackers; pair measures only — no fly identity, so no open-field measures) |

**Key asymmetry:** the counter base row carries **no distance, speed, or
activity** — a `Counter` never computes `Dist_mm`, because those quantities are
per-frame *differences* of one identified individual and a counter has no
frame-to-frame identity to difference against (see §1). So `COUNTER` alone has
*no outcome column at all*, which is why it has an empty stats-metric list (§4).
The two-choice PI is also computed differently between the branches: the tracker
derives PI from per-frame region membership (`+1/−1/0`), the counter from
per-minute blob **counts** `(a−b)/(a+b)`.

---

## 3. Plots available to each type

There are two plotting layers:

- **Instance plots** — methods on the `Tracker`/`Counter` object, one figure per
  animal. Called directly in notebooks or by the Arena `plot_trackers_*` fan-out.
- **Arena plots** — aggregate strip plots across all trackers of the arena
  (`plot_pi`, `plot_totaldistance`, …) plus their `_facet` variants. These are
  what the Hub buttons and `Experiment.save_plots` invoke.

### Instance plots (inherited ✅ down each branch)

| Method | `Tracker` | `TwoChoiceTracker` | `XChoiceTracker` | `PW-Tracker` | `Counter` | `TwoChoiceCounter` | `PW-Counter` |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| `plot_xy` | ✅ | ✅ | ✅ | ✅ (color = neighbor dist) | ✅ | ✅ | ✅ |
| `plot_x` / `plot_y` | ✅ line/hist | ✅ | ✅ | ✅ | ✅ hist | ✅ | ✅ |
| `plot_x_hist` | ✅ | ✅ | ✅ | ✅ | — | — | — |
| `plot_total_distance` | ✅ | ✅ | ✅ | ✅ | — | — | — |
| `plot_xy_animated` | ✅ | ✅ | ✅ | ✅ | — | — | — |
| `plot_pis` / `plot_percentages` | — | ✅ | — | — | — | ✅ | — |
| `plot_cumulative_pi` / `_percentage` | — | ✅ | — | — | — | ✅ | — |
| `plot_time_dependent_pi` / `_percentage` | — | ✅ | — | — | — | ✅ | — |
| `plot_time_dependent_distances` | — | — | — | ✅ | — | — | ✅ (▸ tracker 0) |
| `plot_time_dependent_interactions` | — | — | — | ✅ | — | — | ✅ (▸ tracker 0) |

### Arena aggregate plots (which type each dispatch accepts)

| Arena method | Accepted types | Backend |
|---|---|---|
| `plot_totaldistance` / `_facet` | `TRACKER`, `TWOCHOICETRACKER`, `XCHOICETRACKER`, `PAIRWISEINTERACTIONTRACKER` (`_DISTANCE_TRACKING_TYPES`) | `_generaltracker` |
| `plot_pi` / `_facet` | `TWOCHOICETRACKER`; `_facet` also `TWOCHOICECOUNTER` | `_twochoicetracker` / `_twochoicecounter` |
| `plot_percentage` / `_facet` | `TWOCHOICETRACKER`; `_facet` also `TWOCHOICECOUNTER` | same |
| `plot_transitions` / `_facet` | `TWOCHOICETRACKER` | `_twochoicetracker` |
| `plot_adjusted_x_position` / `_facet` | `XCHOICETRACKER` | `_xchoicetracker` |
| `plot_interactions` / `_facet` | `PAIRWISEINTERACTIONTRACKER`, `PAIRWISEINTERACTIONCOUNTER` | `_pairwiseinteractiontracker` |
| `plot_trackers_pis` / `plot_trackers_percentages` | `TWOCHOICETRACKER`, `TWOCHOICECOUNTER` | fan-out |
| `plot_trackers_x` / `_y` / `_xy` / `_x_hist` | tracker-class | fan-out |
| `plot_trackers_time_dependent_interactions` | `PAIRWISEINTERACTION*` | fan-out |

Any other type reaching these methods raises `ValueError: Invalid tracking
type`. Note `plot_x` (`plot_trackers_x`) `one_plot=True` is broken on modern
matplotlib (`plt.cm.get_cmap` removed ≥ 3.9).

### Which plots the *pipeline* auto-generates — `_TRACKING_TYPE_PLOTS`

`Experiment.save_plots` only draws the plots registered for the type in
[`_TRACKING_TYPE_PLOTS`](../pytrackinganalysis/Experiment.py#L33-L68):

| Type | Auto-generated facet plots |
|---|---|
| `TRACKER`, `DDROPTRACKER`, `CENTROPHOBISMTRACKER` | `plot_totaldistance_facet` |
| `TWOCHOICETRACKER` | `plot_pi_facet`, `plot_percentage_facet`, `plot_transitions_facet`, `plot_totaldistance_facet` |
| `TWOCHOICECOUNTER` | `plot_pi_facet`, `plot_percentage_facet` |
| `XCHOICETRACKER` | `plot_adjusted_x_position_facet`, `plot_totaldistance_facet` |
| `PAIRWISEINTERACTIONTRACKER` | `plot_interactions_facet`, `plot_totaldistance_facet` |
| `PAIRWISEINTERACTIONCOUNTER` | `plot_interactions_facet` |
| `COUNTER` | **none** (not in the table) |

`DDROPTRACKER`/`CENTROPHOBISMTRACKER` are *listed* here but can never reach this
code because they fail to load (§5).

---

## 4. Statistics available to each type — `_TRACKING_TYPE_METRICS`

`Experiment.stats()` runs `run_pairwise_comparisons_facet` for each metric in
[`_TRACKING_TYPE_METRICS`](../pytrackinganalysis/Experiment.py#L71-L85) (2
treatment levels → Welch's t-test; 3+ → Tukey HSD; <2 → "Not applicable"). With
exactly **one** treatment level, a metric with a natural indifference value is
instead tested against it — `FinalPI` against 0 and `FinalPercentage` against
0.5, two-sided one-sample t-test ([`indifference.py`](../pytrackinganalysis/indifference.py));
every other metric stays "Not applicable":

| Type | Compared metrics |
|---|---|
| `TRACKER`, `DDROPTRACKER`, `CENTROPHOBISMTRACKER` | `TotalDistancePerMin` |
| `TWOCHOICETRACKER` | `FinalPI`, `FinalPercentage`, `TotalDistancePerMin` |
| `TWOCHOICECOUNTER` | `FinalPI`, `FinalPercentage` |
| `XCHOICETRACKER` | `AvgAdjX_mm`, `TotalDistancePerMin` |
| `PAIRWISEINTERACTIONTRACKER` | **Primary:** `PercentInteracting_<d>`, `CentrophobismIndex`, `ExplorationAUC`, `TotalDistancePerMin`; **secondary (exploratory):** `EncounterRate_<d>`, `MeanEncounterDuration_<d>`, walking speed/bouts, explored fractions — `Experiment.metric_tiers()`, shared with the Project |
| `PAIRWISEINTERACTIONCOUNTER` | `PercentInteracting_<d>` for each distance |
| `COUNTER` | **`[]`** — no outcome column exists, so stats() prints "nothing to compare" |

### Type-specific analyses (event durations & light) — Arena only

| Analysis | Restricted to | Notes |
|---|---|---|
| `analyze_rle_data` / `_facet` (run-length event durations) | `TWOCHOICETRACKER` only (explicit guard) | writes `_EventDuration_Summary_Facet.csv` |
| `analyze_distance_by_light` / `_facet` (mm/sec per counting-region group) | any tracker-class type with `counting_regions` (skips trackers lacking a design) | writes `_DistanceByLight_Facet.csv` |
| `get_transitions` / `rle` | `TWOCHOICETRACKER` | alias-aware group transitions |

---

## 5. Map for the incomplete types

### `COUNTER` — loads but produces nothing
A plain `Counter` computes only presence/timing, no outcome. **This is a
deliberate minimum**, marked so in the metrics table. If a bare occupancy count
should be an outcome (e.g. *total blobs seen* or *mean occupancy per minute*),
the work is: add those columns to `Counter.summarize`, add a `COUNTER` entry to
`_TRACKING_TYPE_METRICS` naming them, and (optionally) a plot backend + a
`_TRACKING_TYPE_PLOTS` entry.

### `DDROPTRACKER` — enum + policy tables, no class, cannot load
"D-drop" (dye/drop-response) is registered everywhere *except* where it matters.
Its policy rows reuse the generic distance metric, implying it was intended to
behave like a plain `Tracker` plus a drop-specific outcome. **To finish it:**

1. Create `pytrackinganalysis/DDropTracker.py` with `class DDropTracker(Tracker.Tracker)`.
2. Override `summarize` to `pd.concat` the base row with the drop metric(s)
   (e.g. latency-to-drop, response magnitude) computed from the raw columns.
3. Add the instantiation branch in `Arena.create_trackers` (the `TrackingClass.TRACKING` loop).
4. Import it in `Arena.py` and register in `pytrackinganalysis/__init__.py`.
5. Point `_TRACKING_TYPE_METRICS[DDROPTRACKER]` at the new column(s); add plot
   backends + `_TRACKING_TYPE_PLOTS` rows if a drop plot is wanted.
6. Add an Arena dispatch method if a bespoke aggregate plot is needed.

### `CENTROPHOBISMTRACKER` — same orphan status
Centrophobism now exists as a measure: `openfield.py` (ADR-0015) computes the
Centrophobism Index, wall measures, exploration and walking structure from any
tracker's frames and its ROI, and `PairwiseInteractionTracker` uses it. The
planned replacement for this enum value is a single-fly **Open Field**
Experiment Type that calls the same module — a thin tracker whose `summarize`
concatenates `openfield.fly_measures(...)` onto the base row.

---

## 6. Recipe: registering any new tracker type

A type is only "well defined" when it is present at **all five** wiring points.
This is the checklist the two orphans fail:

| # | Wiring point | File | What to add |
|---|---|---|---|
| 1 | Enum value | `Parameters.py` `TrackingType` + `TrackingTypeDetails` class lists | already present for all 9 |
| 2 | **Class** | new `<Name>.py` | subclass `Tracker`/`Counter`, override `summarize` (+ any plots) |
| 3 | **Instantiation branch** | `Arena.create_trackers` | `elif tracking_type == …: tracker = <Name>(…)` |
| 4 | Stats metrics | `Experiment._TRACKING_TYPE_METRICS` | list the outcome columns your `summarize` emits |
| 5 | Auto plots + dispatch | `Experiment._TRACKING_TYPE_PLOTS` and an `Arena.plot_*` dispatch | register facet plot names and route them to a backend |

Steps 2 and 3 are load-bearing: without them the project cannot be opened.
Steps 4 and 5 are what turn a type that *loads* (like `COUNTER`) into one that
*produces analyses and plots*.

### Fast audit of completeness

```python
from pytrackinganalysis import Parameters
from pytrackinganalysis.Experiment import _TRACKING_TYPE_PLOTS, _TRACKING_TYPE_METRICS
for t in Parameters.TrackingType:
    has_plots   = t in _TRACKING_TYPE_PLOTS
    has_metrics = t in _TRACKING_TYPE_METRICS   # note: PW types map to None (runtime-built)
    print(f"{t.name:28} plots={has_plots!s:5} metrics={has_metrics}")
```

---

## 7. Summary of the comparison

- **Frame-to-frame identity is the tracker/counter divide.** A tracker keeps
  each individual's identity across frames, so it can difference consecutive
  frames into distance + speed + activity (all inherited from
  `Tracker.summarize`). A counter is used when flies are tracked as a group and
  individuals *cannot* be linked frame to frame, so none of those quantities are
  even defined — only current-frame region counts are. Choose a counter when
  identity cannot be maintained; you get *how many blobs are where* per minute,
  never *how one blob moved*.
- **The three "choice/interaction" behaviors each exist in both a tracker and a
  counter form** (two-choice, pairwise-interaction), computing the same-named
  metric two different ways — per-frame membership vs per-minute counts. Results
  are not interchangeable; the PI definitions differ.
- **`XCHOICETRACKER` is the only behavior with a single (tracker) form**, keyed
  on adjusted X position rather than discrete counting regions.
- **Two enum types are stubs** (`DDROPTRACKER`, `CENTROPHOBISMTRACKER`): fully
  advertised in the policy tables but with no class and no loader branch. §5–6
  give the exact steps to complete them, and the ROI/ geometry they'd need is
  already loaded on every tracker.
- **`COUNTER` is a deliberate floor**: it loads and reports timing but has no
  outcome metric or plot by design; promoting it means giving `Counter.summarize`
  something to report and registering it in the two policy tables.
