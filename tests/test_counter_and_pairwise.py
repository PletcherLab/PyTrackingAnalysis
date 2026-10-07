"""Counter aggregation and pairwise-interaction validity."""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import FakeDesign, make_raw, make_regions
from pytrackinganalysis import (
    Counter,
    PairwiseInteractionTracker,
    Parameters,
    TwoChoiceCounter,
)


# --------------------------------------------------------------------------
# Counter
# --------------------------------------------------------------------------

def counter_params():
    p = Parameters.Parameters(tracking_type=Parameters.TrackingType.COUNTER)
    p.mm_per_pixel = 1.0
    p.fps = 0
    return p


def test_counter_empty_window_reports_missing_not_a_crash():
    """Indexing .at[0] on an empty window used to raise KeyError."""
    counter = Counter.Counter(
        "T_1", make_regions(), pd.DataFrame(), counter_params(), FakeDesign(),
        make_raw(minutes=(0, 1, 2)),
    )
    summary = counter.summarize((100, 200))
    assert pd.isna(summary["ObsMinutes"])
    assert summary["Name"] == "T_1"


def test_counter_without_a_design_still_summarizes():
    """treatment was referenced before assignment when no design was attached."""
    counter = Counter.Counter(
        "T_1", make_regions(), pd.DataFrame(), counter_params(), None,
        make_raw(minutes=(0, 1, 2)),
    )
    assert counter.summarize()["Treatment"] == ""


# --------------------------------------------------------------------------
# TwoChoiceCounter alias aggregation
# --------------------------------------------------------------------------

def two_choice_counter(raw, design_map):
    p = Parameters.Parameters(tracking_type=Parameters.TrackingType.TWOCHOICECOUNTER)
    p.mm_per_pixel = 1.0
    p.fps = 0
    return TwoChoiceCounter.TwoChoiceCounter(
        "T_1", make_regions(), pd.DataFrame(), p,
        FakeDesign(counting_regions=design_map), raw,
    )


def test_counter_aliases_are_summed_not_overwritten():
    """Two aliases of the same group occupied in the same minute must both count.

    Assigning instead of summing kept only the last alias seen, so a minute with
    one animal in 'Light' and one in 'LL' was reported as a single animal.
    """
    raw = make_raw(
        minutes=(0, 0, 1, 1),
        xs=(0, 1, 2, 3),
        counting_region=("Light", "LL", "Light", "L"),
    )
    counter = two_choice_counter(raw, {"Light": ["Light", "LL", "L"], "NoLight": ["NoLight"]})
    pi = counter.pi_data
    assert list(pi["Light"]) == [2, 2]


def test_counter_group_with_no_visits_is_zero():
    raw = make_raw(minutes=(0, 1), xs=(0, 1), counting_region=("Light", "Light"))
    counter = two_choice_counter(raw, {"Light": ["Light"], "NoLight": ["NoLight"]})
    assert list(counter.pi_data["NoLight"]) == [0, 0]


def test_counter_pi_is_plus_one_for_an_exclusive_preference():
    raw = make_raw(minutes=(0, 1), xs=(0, 1), counting_region=("Light", "LL"))
    counter = two_choice_counter(raw, {"Light": ["Light", "LL"], "NoLight": ["NoLight"]})
    assert counter.get_final_pi() == pytest.approx(1.0)


def test_counter_counts_reflect_every_alias():
    raw = make_raw(
        minutes=(0, 0, 1),
        xs=(0, 1, 2),
        counting_region=("Light", "LL", "NoLight"),
    )
    counter = two_choice_counter(raw, {"Light": ["Light", "LL"], "NoLight": ["NoLight"]})
    counts = counter.get_counting_region_counts()
    assert counts["Light"] == 2
    assert counts["NoLight"] == 1


# --------------------------------------------------------------------------
# Pairwise interaction validity
# --------------------------------------------------------------------------

def pairwise_pair(quality_a, quality_b, nobjects_a, nobjects_b, distance):
    """Build a paired tracker/neighbour with a preset ClosestNeighbor column."""
    p = Parameters.Parameters(tracking_type=Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER)
    p.mm_per_pixel = 1.0
    p.fps = 0
    p.interaction_distance_mm = [8]

    def _raw(quality, nobjects, object_id):
        raw = make_raw(minutes=(0, 1), xs=(0, 1), quality=quality, nobjects=nobjects)
        raw["ObjectID"] = object_id
        raw["ClosestNeighbor"] = list(distance)
        return raw

    design = FakeDesign()
    first = PairwiseInteractionTracker.PairwiseInteractionTracker(
        "T_1", 0, make_regions(), pd.DataFrame(), p, design, _raw(quality_a, nobjects_a, 0),
    )
    second = PairwiseInteractionTracker.PairwiseInteractionTracker(
        "T_1", 1, make_regions(), pd.DataFrame(), p, design, _raw(quality_b, nobjects_b, 1),
    )
    first.set_neighbor(second)
    return first


def test_a_lost_animal_is_not_a_valid_neighbour_observation():
    """One tracker NotFound while the object counts still sum to two.

    The quality and object-count conditions used to be OR'd, so this frame was
    accepted as a real distance measurement.
    """
    tracker = pairwise_pair(
        quality_a=("NotFound", "High"),
        quality_b=("High", "High"),
        nobjects_a=(1, 1),
        nobjects_b=(1, 1),
        distance=(5.0, 5.0),
    )
    assert not bool(tracker.rawdata["IsNeighborValid"].iloc[0])
    assert bool(tracker.rawdata["IsNeighborValid"].iloc[1])


def test_a_sentinel_distance_is_not_valid():
    tracker = pairwise_pair(
        quality_a=("High", "High"),
        quality_b=("High", "High"),
        nobjects_a=(1, 1),
        nobjects_b=(1, 1),
        distance=(-1.0, 5.0),
    )
    assert not bool(tracker.rawdata["IsNeighborValid"].iloc[0])


def test_a_clean_pair_is_valid_and_scales_to_mm():
    tracker = pairwise_pair(
        quality_a=("High", "High"),
        quality_b=("High", "High"),
        nobjects_a=(1, 1),
        nobjects_b=(1, 1),
        distance=(5.0, 5.0),
    )
    assert tracker.rawdata["IsNeighborValid"].all()
    assert tracker.rawdata["ClosestNeighbor_mm"].iloc[0] == pytest.approx(5.0)


def test_invalid_frames_do_not_contribute_to_interaction_percentages():
    tracker = pairwise_pair(
        quality_a=("NotFound", "High"),
        quality_b=("High", "High"),
        nobjects_a=(1, 1),
        nobjects_b=(1, 1),
        distance=(1.0, 1.0),
    )
    # Only the second frame is a usable observation, and it is an interaction.
    assert tracker.get_total_frames_with_valid_neighbor() == 1
    assert tracker.get_percent_frames_interacting()[0] == pytest.approx(1.0)


def test_percent_interacting_is_missing_when_no_frame_is_valid():
    """No valid frame is no measurement — NA, not a 0 that reads as "never
    interacted" and gets tested as such (and never a division by zero)."""
    tracker = pairwise_pair(
        quality_a=("NotFound", "NotFound"),
        quality_b=("High", "High"),
        nobjects_a=(1, 1),
        nobjects_b=(1, 1),
        distance=(1.0, 1.0),
    )
    assert tracker.get_total_frames_with_valid_neighbor() == 0
    assert all(pd.isna(v) for v in tracker.get_percent_frames_interacting())
    assert pd.isna(tracker.summarize()["PercentInteracting_8"])
