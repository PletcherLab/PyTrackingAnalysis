# Open-field measures are scale-free, ROI-bounded, and shared

## Context

A Paired Open Field Experiment (ADR-0016) asks about general health and
behaviour as well as social behaviour: how much a fly moves, whether it
avoids the open centre of the arena (**centrophobism**), and how much of the
arena it explores. It runs on all four rigs — Small Arena, Arena Max,
Colosseum and Movie — whose arenas differ several-fold in size, and in shape
(the ROI sheet says `Ellipse` or `Rectangle`). The legacy R code measured
centrophobism with absolute thresholds (time within 2/5/10/15 mm of the
wall), which mean different things in a small well and a large arena.
`CENTROPHOBISMTRACKER` has been an enum value with no class since the port.

## Decision

**Where.** The measures live in one module of pure functions over a
tracker's frame data and its ROI, not on the base `Tracker` — a
"centrophobism" column on a Valence two-choice chamber would be meaningless.
`PairwiseInteractionTracker` uses it now; a future single-fly *Open Field*
type reuses it unchanged and retires the orphan enum.

**The wall is the ROI as drawn.** DTrack's `RelX`/`RelY` are relative to the
ROI centre, and the ROI's `Shape`, `Width` and `Height` define the arena. The
definition never depends on how the flies behaved. An ROI drawn a millimetre
off would bias every fly, so an **ROI check** reports per region `ReachGap_mm`
— the 0.5th-percentile distance to the wall over both flies' High-quality
positions — and flags |ReachGap| > 1 mm: flies never reaching the edge (ROI
too big, or a lethargic pair) or positions outside it (ROI too small). A
well-drawn ROI gives about +0.5 mm, the half-width of a fly.

**Centrophobism** uses a normalised radius ρ — `√((x/a)²+(y/b)²)` for an
ellipse, `max(|x|/a, |y|/b)` for a rectangle (a, b the half-width and
half-height) — so ρ = 0 at the centre and 1 at the wall. The **centre zone**
ρ < 1/√2 holds exactly half the area in either shape, so a fly using the
arena uniformly spends half its time there.

- `CentrophobismIndex` (CI) = P(periphery) − P(centre), −1..+1, 0 = uniform,
  positive = wall-hugging — the lab's PI sign convention. Computed over
  **walking** High-quality frames (`IsWalking`), so it measures where a fly
  goes when it moves, independent of how much it moves; flies rest and sleep
  at the wall, and an all-frames index mostly re-measures activity. NA with
  under 30 s of walking in the window.
- Descriptive, over all High-quality frames: `CentrophobismIndexAllFrames`,
  `WallZoneFraction` (time within `wall_zone_mm` of the wall, default 2.5 mm
  ≈ one body length) and `MeanWallDistance_mm` (nearest-wall distance for a
  rectangle, exact point-to-ellipse distance otherwise; 0 outside the ROI).

**Exploration** marks what the fly's body touched, not where its centroid
went. The body is an ellipse `fly_length_mm` × `fly_width_mm` (default
2.5 × 1.0) centred on the tracked position, oriented along the heading of
the path smoothed over 0.5 s of walking, and held at the last heading while
still. (DTrack's `Direction` is the heading of each frame-to-frame step,
−1 when the fly did not move and quantised to 45° at one-pixel steps — it is
not a body axis.) Between consecutive High-quality frames the ellipse is
**swept** along the step, so a fast fly leaves a continuous band rather than
a dotted trail; no sweep crosses a gap over 1 s or a step faster than
50 mm/s, which are tracking glitches or identity swaps. Coverage is counted
on the native pixel raster, clipped to the arena. Per window:

- `ExploredFraction`, `ExploredFractionCenter`, `ExploredFractionPeriphery` —
  share of each zone covered by the window's end.
- `ExplorationAUC` — mean coverage over the window, ∫coverage(t)dt ÷ T, 0..1.
  Any active fly nears 100% coverage of a small well in a long phase;
  the AUC still separates the fly that got there in 3 minutes from the one
  that took 50, and it is never NA.
- `TimeTo50PctExplored` — minutes from window start; NA if never reached
  (descriptive: the NAs would bias any test that drops them).

**Walking** separates how often a fly walks from how fast:
`WalkingSpeed_mm_s` (mean speed over walking High-quality frames — motor
capacity), `WalkingBoutsPerMin` and `MeanWalkingBoutDuration_s` (initiation
and persistence; the same 0.5 s bridging and minimum as encounters,
ADR-0014).

Alternatives considered: inferring the wall from the flies' occupied extent
(self-calibrating, but an inactive pair shrinks its own arena and looks less
centrophobic); the legacy absolute-threshold definitions (not comparable
across rigs); mean ρ as the headline (continuous, but a zero-less scale);
a centroid grid for exploration (sized relative to the arena or in mm — the
first makes a cell smaller than the fly in a small well, the second ties the
number to the rig, and neither lets the centroid reach the outermost
half-millimetre the body touches); an unswept per-frame ellipse (fast flies
leave gaps and look less exploratory); tortuosity (pixel-step headings are
too noisy without resampling — deferred).

## Consequences

- Every pairwise-tracker project gains these columns, Custom included
  (ADR-0013 averages them within the Pair).
- `WallZoneFraction` and `MeanWallDistance_mm` are in mm and therefore only
  comparable within a rig; the CI and the exploration fractions compare
  across rigs.
- The lost fly of a merged frame (ADR-0014) has no position, so it adds no
  footprint and no zone occupancy for those frames.
- Exploration is the costliest computation in the pipeline (a footprint per
  frame per fly); frames where the fly neither moved nor turned are skipped,
  since their footprint repeats the previous one.
