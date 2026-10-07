"""The Pair as the unit of pairwise analysis, and merged frames as contact.

ADR-0013 (one summary row per Pair) and ADR-0014 (a lost run bracketed by
contact is the two flies touching, not missing data).
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from conftest import FakeDesign, make_raw, make_regions
from test_experiment_integration import write_project
from pytrackinganalysis import Experiment as ExperimentMod
from pytrackinganalysis import Parameters, PairwiseInteractionTracker
from pytrackinganalysis.PairwiseInteractionTracker import classify_merges
from pytrackinganalysis.project import Project


# --------------------------------------------------------------------------
# classify_merges
# --------------------------------------------------------------------------

def test_a_lost_run_bracketed_by_contact_is_merged():
    d = [5, 2, np.nan, np.nan, 2.5, 8]
    measured = ~np.isnan(d)
    assert classify_merges(d, measured, 3.0).tolist() == [0, 0, 1, 1, 0, 0]


def test_a_lost_run_with_one_far_bracket_is_not_merged():
    """The fly was lost while the flies were apart — the distance is unknown."""
    d = [2, np.nan, 9, 9, np.nan, 2]
    assert not classify_merges(d, ~np.isnan(d), 3.0).any()


def test_runs_at_either_end_of_the_recording_are_never_merged():
    """One bracket is not enough: nobody saw the flies come apart."""
    d = [np.nan, 1, 1, np.nan]
    assert not classify_merges(d, ~np.isnan(d), 3.0).any()


def test_a_zero_merge_distance_turns_inference_off():
    d = [1, np.nan, 1]
    assert not classify_merges(d, ~np.isnan(d), 0).any()


# --------------------------------------------------------------------------
# The tracker counts merged frames as contact
# --------------------------------------------------------------------------

def _pair(distances_px, lost):
    """Two paired trackers whose frames in *lost* lost fly 0 (NotFound)."""
    p = Parameters.Parameters(tracking_type=Parameters.TrackingType.PAIRWISEINTERACTIONTRACKER)
    p.mm_per_pixel = 1.0
    p.fps = 0
    p.interaction_distance_mm = [4]
    n = len(distances_px)
    minutes = tuple(i / 600.0 for i in range(n))

    def _raw(object_id, quality):
        raw = make_raw(minutes=minutes, xs=(0.0,) * n, quality=quality)
        raw["ObjectID"] = object_id
        raw["ClosestNeighbor"] = [(-1 if i in lost else d) for i, d in enumerate(distances_px)]
        return raw

    lost_quality = tuple("NotFound" if i in lost else "High" for i in range(n))
    first = PairwiseInteractionTracker.PairwiseInteractionTracker(
        "T_1", 0, make_regions(), pd.DataFrame(), p, FakeDesign(), _raw(0, lost_quality))
    second = PairwiseInteractionTracker.PairwiseInteractionTracker(
        "T_1", 1, make_regions(), pd.DataFrame(), p, FakeDesign(), _raw(1, ("High",) * n))
    first.set_neighbor(second)
    second.set_neighbor(first)
    return first


def test_merged_frames_count_as_interaction():
    """Two touching flies DTrack resolved as one blob: contact, not a gap.

    Excluding these used to remove the closest contacts from both numerator
    and denominator, biasing PercentInteracting down.
    """
    tracker = _pair([10, 2, 0, 0, 2, 10], lost={2, 3})
    assert tracker.rawdata["IsMerged"].tolist() == [False, False, True, True, False, False]
    assert tracker.rawdata["IsNeighborValid"].all()
    assert tracker.rawdata["ClosestNeighbor_mm"].iloc[2] == 0.0
    summary = tracker.summarize()
    assert summary["ValidFrames"] == 6
    assert summary["PercentInteracting_4"] == pytest.approx(4 / 6)
    assert summary["MergedFraction"] == pytest.approx(2 / 6)


def test_a_fly_lost_while_far_away_stays_excluded():
    tracker = _pair([10, 10, 0, 10, 10], lost={2})
    assert not tracker.rawdata["IsMerged"].any()
    assert tracker.summarize()["ValidFrames"] == 4


def test_both_partners_agree_on_merged_frames():
    tracker = _pair([10, 2, 0, 2, 10], lost={2})
    assert tracker.rawdata["IsMerged"].tolist() == \
        tracker.neighbor_tracker.rawdata["IsMerged"].tolist()


# --------------------------------------------------------------------------
# One summary row per Pair
# --------------------------------------------------------------------------

@pytest.fixture
def pair_project(tmp_path):
    return write_project(tmp_path / "pairs", tracking_type="PAIRWISEINTERACTIONTRACKER",
                         regions=("T_1", "T_2", "T_3", "T_4"), objects=(0, 1),
                         extra_global={"interaction_distances": [4, 8]})


def test_summaries_hold_one_row_per_pair(pair_project):
    exp = ExperimentMod.Experiment(str(pair_project))
    summary = exp.arena.summarize()
    assert list(summary["Name"]) == ["T_1", "T_2", "T_3", "T_4"]
    assert list(summary["TrackingRegion"]) == list(summary["Name"])
    assert "ObjectID" not in summary.columns
    per_fly = exp.arena.summarize(per_fly=True)
    assert len(per_fly) == 8
    ## A per-fly measure is the mean of the Pair's two flies.
    t1 = per_fly[per_fly["TrackingRegion"] == "T_1"]["TotalDistance"]
    assert summary.loc[0, "TotalDistance"] == pytest.approx(t1.mean())


def test_facet_summaries_are_per_pair_too(pair_project):
    exp = ExperimentMod.Experiment(str(pair_project))
    facet = exp.arena.summarize_facet(cutoffs=[2, 4])
    assert len(facet) == 4 * 3
    assert "ObjectID" not in facet.columns


def test_an_excluded_pair_leaves_no_row(pair_project):
    exp = ExperimentMod.Experiment(str(pair_project))
    exp.arena.set_excluded_trackers(["T_2_0", "T_2_1"])
    assert "T_2" not in set(exp.arena.summarize()["Name"])


def test_per_fly_companions_are_written_beside_the_pair_summaries(pair_project):
    exp = ExperimentMod.Experiment(str(pair_project))
    exp.save_summary()
    analysis = exp.analysis_path
    name = exp.arena.experiment_name
    pairs = pd.read_csv(os.path.join(analysis, f"{name}_Summary.csv"))
    flies = pd.read_csv(os.path.join(analysis, f"{name}_Summary_PerFly.csv"))
    facet_flies = pd.read_csv(os.path.join(analysis, f"{name}_Summary_Facet_PerFly.csv"))
    assert len(pairs) == 4 and len(flies) == 8 and len(facet_flies) == 8 * 3


def test_stats_count_pairs_not_flies(pair_project):
    exp = ExperimentMod.Experiment(str(pair_project))
    text = exp.stats(save=False)
    assert "PercentInteracting_4" in text
    ## Four Pairs, two per treatment — not four flies per treatment.
    assert "Ctrl (n=2" in text and "Treated (n=2" in text
    assert "n=4" not in text


# --------------------------------------------------------------------------
# The Project tests pairwise metrics instead of falling back to FinalPI
# --------------------------------------------------------------------------

def test_project_resolves_pairwise_metrics_from_its_columns():
    project = Project.__new__(Project)
    project.tracking_type_name = "PAIRWISEINTERACTIONTRACKER"
    columns = ["Treatment", "PercentInteracting_4", "PercentInteracting_8", "TotalDistance"]
    metrics = project._metrics(columns)
    ## Primary tier first (ADR-0016), the distances read off the columns.
    assert metrics[:5] == ["PercentInteracting_4", "PercentInteracting_8",
                           "CentrophobismIndex", "ExplorationAUC", "TotalDistancePerMin"]
    assert "EncounterRate_8" in metrics and "FinalPI" not in metrics


def test_project_metrics_for_other_types_are_unchanged():
    project = Project.__new__(Project)
    project.tracking_type_name = "TWOCHOICETRACKER"
    assert project._metrics([]) == ["FinalPI", "FinalPercentage", "TotalDistancePerMin"]
