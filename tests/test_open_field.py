"""Open-field measures (ADR-0015) and Encounters (ADR-0014), on inputs small
enough that the expected answer can be worked out by hand."""

from __future__ import annotations

import numpy as np
import pytest

from pytrackinganalysis import openfield as of
from pytrackinganalysis.PairwiseInteractionTracker import encounter_bouts

rng = np.random.default_rng(0)


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [(10.0, 6.0), (6.0, 10.0), (8.0, 8.0)])
def test_ellipse_wall_distance_matches_brute_force(a, b):
    geometry = of.ArenaGeometry("Ellipse", a, b, 0.05)
    theta = np.linspace(0, 2 * np.pi, 100001)
    bx, by = a * np.cos(theta), b * np.sin(theta)
    points = rng.uniform(-12, 12, size=(80, 2))
    brute = np.array([np.min(np.hypot(bx - x, by - y)) for x, y in points])
    signed = geometry.wall_distance(points[:, 0], points[:, 1])
    assert np.abs(np.abs(signed) - brute).max() < 1e-3
    ## Positive inside, negative outside.
    inside = geometry.normalized_radius(points[:, 0], points[:, 1]) < 1
    assert ((signed > 0) == inside).all()


def test_rectangle_wall_distance_is_the_nearest_wall():
    geometry = of.ArenaGeometry("Rectangle", 10.0, 5.0, 0.1)
    assert geometry.wall_distance(np.array([0.0]), np.array([0.0]))[0] == pytest.approx(5.0)
    assert geometry.wall_distance(np.array([8.0]), np.array([0.0]))[0] == pytest.approx(2.0)
    assert geometry.wall_distance(np.array([13.0]), np.array([9.0]))[0] == pytest.approx(-5.0)


@pytest.mark.parametrize("shape", ["Ellipse", "Rectangle"])
def test_the_centre_zone_holds_half_the_area(shape):
    """So a fly using the arena uniformly scores CI = 0 in either shape."""
    geometry = of.ArenaGeometry(shape, 10.0, 6.0, 0.05)
    points = rng.uniform(-1, 1, size=(200000, 2)) * [10, 6]
    rho = geometry.normalized_radius(points[:, 0], points[:, 1])
    inside = rho <= 1
    assert (rho[inside] < of.CENTER_ZONE_RHO).mean() == pytest.approx(0.5, abs=0.01)


# --------------------------------------------------------------------------
# Bouts
# --------------------------------------------------------------------------

TEN_FPS = np.arange(40) / 600.0     # minutes at 10 frames per second


def test_short_breaks_are_bridged():
    active = np.array([1] * 6 + [0] * 2 + [1] * 6 + [0] * 26, bool)
    assert of.find_bouts(active, TEN_FPS, bridge_s=0.5, min_s=0.5).tolist() == [[0, 13]]


def test_short_bouts_are_dropped():
    active = np.array([1] * 2 + [0] * 8 + [1] * 8 + [0] * 22, bool)
    assert of.find_bouts(active, TEN_FPS, bridge_s=0.5, min_s=0.5).tolist() == [[10, 17]]


def test_hysteresis_makes_hovering_one_encounter():
    """Distance wobbling across the threshold is one Encounter, not many."""
    distance = np.array([10] * 5 + [3.9, 4.2, 3.8, 4.3, 3.9, 4.1, 3.7] * 3 + [10] * 14, float)
    valid = np.ones(distance.size, bool)
    minutes = np.arange(distance.size) / 600.0
    with_h = encounter_bouts(distance, valid, minutes, 4, 0.5, 0.0, 0.0)
    without = encounter_bouts(distance, valid, minutes, 4, 0.0, 0.0, 0.0)
    assert len(with_h) == 1
    assert len(without) > 1


def test_a_long_tracking_gap_ends_an_encounter():
    distance = np.array([1.0] * 10 + [np.nan] * 20 + [1.0] * 10)
    valid = ~np.isnan(distance)
    minutes = np.arange(distance.size) / 600.0
    assert len(encounter_bouts(distance, valid, minutes, 4, 0.5, 0.5, 0.5)) == 2


# --------------------------------------------------------------------------
# Centrophobism
# --------------------------------------------------------------------------

def _walk(xs, ys, seconds_per_frame=0.1):
    n = len(xs)
    return (np.asarray(xs, float), np.asarray(ys, float), np.ones(n, bool),
            np.ones(n, bool), np.full(n, seconds_per_frame))


def test_a_wall_follower_scores_plus_one():
    geometry = of.ArenaGeometry("Ellipse", 10.0, 10.0, 0.05)
    theta = np.linspace(0, 6 * np.pi, 600)
    x, y, valid, walking, dt = _walk(9.5 * np.cos(theta), 9.5 * np.sin(theta))
    c = of.centrophobism(x, y, valid, walking, dt, geometry)
    assert c["CentrophobismIndex"] == pytest.approx(1.0)
    assert c["WallZoneFraction"] == pytest.approx(1.0)
    assert c["MeanWallDistance_mm"] == pytest.approx(0.5, abs=1e-6)


def test_a_uniform_wanderer_scores_near_zero():
    geometry = of.ArenaGeometry("Rectangle", 10.0, 10.0, 0.05)
    points = rng.uniform(-10, 10, size=(20000, 2))
    x, y, valid, walking, dt = _walk(points[:, 0], points[:, 1])
    assert of.centrophobism(x, y, valid, walking, dt, geometry)["CentrophobismIndex"] \
        == pytest.approx(0.0, abs=0.03)


def test_the_index_needs_enough_walking():
    """Under 30 s of walking the walking-only index is NA, not a guess."""
    geometry = of.ArenaGeometry("Ellipse", 10.0, 10.0, 0.05)
    x, y, valid, walking, dt = _walk(np.full(100, 9.0), np.zeros(100))
    c = of.centrophobism(x, y, valid, walking, dt, geometry)
    assert np.isnan(c["CentrophobismIndex"])
    assert c["CentrophobismIndexAllFrames"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Exploration
# --------------------------------------------------------------------------

BODY = np.pi * 1.25 * 0.5      # area of the default 2.5 x 1.0 mm body ellipse


def test_a_still_fly_covers_its_own_body():
    geometry = of.ArenaGeometry("Ellipse", 15.0, 15.0, 0.05)
    n = 50
    first = of.footprint_first_visits(np.zeros(n), np.zeros(n), np.ones(n, bool),
                                      np.arange(n) / 600.0, geometry, np.zeros(n))
    assert np.isfinite(first).sum() * 0.05 ** 2 == pytest.approx(BODY, rel=0.05)


def test_a_straight_run_covers_a_body_wide_band():
    """20 mm at 20 mm/s (2 mm per frame, gaps a dotted trail would leave) is
    swept into one band one body-width wide."""
    geometry = of.ArenaGeometry("Ellipse", 15.0, 15.0, 0.05)
    n = 101
    x, y = np.linspace(-10, 10, n), np.zeros(n)
    minutes = np.arange(n) / 600.0
    heading = of.smoothed_heading(x, y, np.ones(n, bool), minutes)
    first = of.footprint_first_visits(x, y, np.ones(n, bool), minutes, geometry, heading)
    area = np.isfinite(first).sum() * 0.05 ** 2
    assert area == pytest.approx(20 * 1.0 + BODY, rel=0.06)


def test_no_sweep_across_a_tracking_glitch():
    """A teleport faster than 50 mm/s is a glitch, not a path."""
    geometry = of.ArenaGeometry("Ellipse", 15.0, 15.0, 0.05)
    x, y = np.array([-10.0, 10.0]), np.zeros(2)
    first = of.footprint_first_visits(x, y, np.ones(2, bool), np.array([0, 0.1 / 60]),
                                      geometry, np.zeros(2))
    assert np.isfinite(first).sum() * 0.05 ** 2 == pytest.approx(2 * BODY, rel=0.05)


def test_exploration_auc_is_mean_coverage_over_the_window():
    """Half the arena covered at the very start, nothing more: AUC = 0.5;
    the same half covered only at the end: AUC ~ 0."""
    geometry = of.ArenaGeometry("Rectangle", 0.5, 0.5, 0.5)   # a 2 x 2 pixel arena
    early = np.array([0.0, 0.0, np.inf, np.inf])
    late = np.array([10.0, 10.0, np.inf, np.inf])
    assert of.exploration(early, geometry, 0.0, 10.0)["ExplorationAUC"] == pytest.approx(0.5)
    assert of.exploration(late, geometry, 0.0, 10.0)["ExplorationAUC"] == pytest.approx(0.0)
    assert of.exploration(early, geometry, 0.0, 10.0)["TimeTo50PctExplored"] == 0.0
    assert np.isnan(of.exploration(np.array([0.0, np.inf, np.inf, np.inf]), geometry,
                                   0.0, 10.0)["TimeTo50PctExplored"])


def test_the_coverage_curve_ends_at_the_explored_fraction():
    first = np.array([1.0, 2.0, 3.0, np.inf])
    curve = of.coverage_curve(first, [0.0, 1.5, 10.0])
    assert curve.tolist() == [0.0, 0.25, 0.75]


# --------------------------------------------------------------------------
# Possibly-Dead spread
# --------------------------------------------------------------------------

def test_terminal_spread_separates_still_from_moving():
    minutes = np.arange(0, 60, 0.1)
    still = of.terminal_spread(np.full(minutes.size, 3.0), np.zeros(minutes.size),
                               np.ones(minutes.size, bool), minutes)
    moving = of.terminal_spread(5 * np.cos(minutes), 5 * np.sin(minutes),
                                np.ones(minutes.size, bool), minutes)
    assert still == pytest.approx(0.0)
    assert moving > 4
    lost = of.terminal_spread(np.zeros(minutes.size), np.zeros(minutes.size),
                              minutes < 30, minutes)
    assert np.isnan(lost)
