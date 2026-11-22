"""Regression tests for the analysis-core findings in CODE_ANALYSIS.md.

Each test names the finding it pins. These are the paths that produce the
numbers a paper reports, so the assertions are about values, not smoke.
"""

from __future__ import annotations

import os
import stat

import numpy as np
import pandas as pd
import pytest

from conftest import FakeDesign, make_raw, make_regions
from test_experiment_integration import write_project

from pytrackinganalysis import Arena as ArenaMod
from pytrackinganalysis import Experiment as ExperimentMod
from pytrackinganalysis import Parameters
from pytrackinganalysis import Tracker as TrackerMod


@pytest.fixture
def tracker_project(tmp_path):
    """A minimal two-region TRACKER project."""
    return write_project(tmp_path / "proj")


# ---------------------------------------------------------------------------
# C6 — RLE event durations compared raw aliases against group names
# ---------------------------------------------------------------------------

def _two_choice_arena(tmp_path, counting_regions, cell_values):
    """A TWOCHOICETRACKER project whose CountingRegion cells are *cell_values*."""
    root = write_project(
        tmp_path / "proj",
        tracking_type="TWOCHOICETRACKER",
        regions=("T_1", "T_2"),
        n_samples=len(cell_values),
        counting_regions=counting_regions,
    )
    # Rewrite the CountingRegion column with the exact run structure we want.
    csv = root / "data" / "Synthetic_Data_1.csv"
    frame = pd.read_csv(csv)
    per_region = len(cell_values)
    frame["CountingRegion"] = list(cell_values) * (len(frame) // per_region)
    frame.to_csv(csv, index=False)
    return ExperimentMod.Experiment(str(root)).arena


#: Six frames: three in the first counting group, then three in the second.
RUN_STRUCTURE = ("Light", "Light", "Light", "NoLight", "NoLight", "NoLight")
ALIASED_RUN_STRUCTURE = ("L", "L", "L", "N", "N", "N")


def test_rle_finds_both_arms_when_labels_equal_the_group_names(tmp_path):
    """The baseline that already worked — kept so the alias case has a control."""
    arena = _two_choice_arena(
        tmp_path,
        {"Light": {"alias": "Light"}, "NoLight": {"alias": "NoLight"}},
        RUN_STRUCTURE,
    )
    result = arena.analyze_rle_data(change_none_to_light=False)
    assert (result["Treatment1_Rows"] > 0).all()
    assert (result["Treatment2_Rows"] > 0).all()
    assert (result["Treatment1_MeanLength"] == 3).all()
    assert (result["Treatment2_MeanLength"] == 3).all()


def test_rle_maps_region_aliases_to_their_group(tmp_path):
    """C6: raw cells hold aliases; comparing them to group names matched nothing.

    Before the fix Treatment2_Rows was 0 for every tracker and the mean run
    length collapsed, with no exception and no warning.
    """
    arena = _two_choice_arena(
        tmp_path,
        {"Light": {"alias": "L, LL"}, "NoLight": {"alias": "N, NN"}},
        ALIASED_RUN_STRUCTURE,
    )
    result = arena.analyze_rle_data(change_none_to_light=False)
    assert (result["Treatment1_Rows"] > 0).all()
    assert (result["Treatment2_Rows"] > 0).all(), "aliased second arm returned zero rows"
    assert (result["Treatment1_MeanLength"] == 3).all()
    assert (result["Treatment2_MeanLength"] == 3).all()


def test_rle_aliases_give_the_same_answer_as_group_names(tmp_path):
    """The alias feature must not change the numbers, only the spelling."""
    plain = _two_choice_arena(
        tmp_path / "plain",
        {"Light": {"alias": "Light"}, "NoLight": {"alias": "NoLight"}},
        RUN_STRUCTURE,
    ).analyze_rle_data(change_none_to_light=False)
    aliased = _two_choice_arena(
        tmp_path / "aliased",
        {"Light": {"alias": "L"}, "NoLight": {"alias": "N"}},
        ALIASED_RUN_STRUCTURE,
    ).analyze_rle_data(change_none_to_light=False)

    for column in ("Treatment1_Rows", "Treatment2_Rows",
                   "Treatment1_MeanLength", "Treatment2_MeanLength"):
        assert list(plain[column]) == list(aliased[column]), column


def test_rle_runs_in_the_same_group_fuse_across_different_aliases(tmp_path):
    """'L' followed by 'LL' is one visit to Light, not two."""
    arena = _two_choice_arena(
        tmp_path,
        {"Light": {"alias": "L, LL"}, "NoLight": {"alias": "N"}},
        ("L", "L", "LL", "N", "N", "N"),
    )
    result = arena.analyze_rle_data(change_none_to_light=False)
    assert (result["Treatment1_Rows"] == 1).all(), "one visit split into two"
    assert (result["Treatment1_MeanLength"] == 3).all()


# ---------------------------------------------------------------------------
# Transitions — a primary two-choice outcome with no test coverage at all
# ---------------------------------------------------------------------------

def _two_choice_tracker(counting_regions, cells, parameters):
    """A single TwoChoiceTracker whose CountingRegion column is *cells*."""
    from pytrackinganalysis import TwoChoiceTracker

    params = Parameters.Parameters(tracking_type=Parameters.TrackingType.TWOCHOICETRACKER)
    params.mm_per_pixel = 1.0
    params.fps = 0
    raw = make_raw(
        minutes=tuple(float(i) for i in range(len(cells))),
        xs=(0.0,) * len(cells),
        counting_region=cells,
    )
    return TwoChoiceTracker.TwoChoiceTracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(), params,
        FakeDesign(counting_regions=counting_regions), raw,
    )


@pytest.mark.parametrize("cells,expected", [
    # Two visits to Light, one to NoLight, back to Light: three alternations.
    (("Light", "Light", "NoLight", "NoLight", "Light", "Light"), 2),
    # Never leaves one region.
    (("Light", "Light", "Light"), 0),
    # A "None" gap does not break a run — the animal is still in the same state.
    (("Light", "None", "Light"), 0),
    (("Light", "None", "NoLight"), 1),
])
def test_transitions_counts_alternations_between_groups(cells, expected, parameters):
    """Pins the `sum(changes) - 1` arithmetic and the None-gap semantics."""
    tracker = _two_choice_tracker(
        {"Light": ["Light"], "NoLight": ["NoLight"]}, cells, parameters,
    )
    assert tracker.get_transitions() == expected


def test_transitions_are_counted_on_the_group_not_the_alias(parameters):
    """Same bug class as C6: 'L' -> 'LL' is one group, so it is not a transition."""
    aliased = _two_choice_tracker(
        {"Light": ["L", "LL"], "NoLight": ["N"]},
        ("L", "L", "LL", "N", "N"), parameters,
    )
    assert aliased.get_transitions() == 1

    plain = _two_choice_tracker(
        {"Light": ["Light"], "NoLight": ["NoLight"]},
        ("Light", "Light", "Light", "NoLight", "NoLight"), parameters,
    )
    assert aliased.get_transitions() == plain.get_transitions()


def test_transitions_in_an_empty_window_are_na(parameters):
    """"Zero transitions" is a claim an empty window cannot support."""
    tracker = _two_choice_tracker(
        {"Light": ["Light"], "NoLight": ["NoLight"]},
        ("None", "None", "None"), parameters,
    )
    assert pd.isna(tracker.get_transitions())


# ---------------------------------------------------------------------------
# R2 — np.where on scalars produced object-dtype columns
# ---------------------------------------------------------------------------

def test_distance_by_light_columns_are_numeric(tmp_path):
    """R2: object dtype defeated skipna, so one empty region NaN'd a whole mean."""
    root = write_project(
        tmp_path / "proj",
        tracking_type="TWOCHOICETRACKER",
        regions=("T_1", "T_2"),
        counting_regions={"Light": {"alias": "Light"}, "NoLight": {"alias": "NoLight"}},
    )
    csv = root / "data" / "Synthetic_Data_1.csv"
    frame = pd.read_csv(csv)
    # T_1 never enters NoLight — the degenerate-but-common case.
    frame.loc[frame["TrackingRegion"] == "T_1", "CountingRegion"] = "Light"
    frame.to_csv(csv, index=False)

    arena = ExperimentMod.Experiment(str(root)).arena
    result = arena.analyze_distance_by_light()

    for column in ("Light_Distance_mm_sec", "NoLight_Distance_mm_sec"):
        assert result[column].dtype == np.float64, f"{column} is {result[column].dtype}"
        # The exported CSV must be re-readable numerically.
        assert pd.to_numeric(result[column], errors="raise") is not None

    # T_1 has no NoLight time, so its rate is NaN — but it must not poison T_2.
    assert result["NoLight_Distance_mm_sec"].notna().any()
    assert not np.isnan(result["NoLight_Distance_mm_sec"].mean())


# ---------------------------------------------------------------------------
# R3 — QC silently passed a tracker with no data in the window
# ---------------------------------------------------------------------------

def test_a_tracker_with_no_data_in_the_window_fails_qc(tracker_project, capsys):
    """R3: `NA < cutoff` is NA, which boolean indexing reads as False."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    # A window past the end of the recording: every tracker contributes zero frames.
    exp.arena.print_short_data_quality_report(cutoff=0.9, range_minutes=(500, 600))
    out = capsys.readouterr().out
    assert "Warning" in out, "a tracker with zero frames was reported as passing QC"
    assert "no data in range" in out


def test_a_good_tracker_still_passes_qc(tracker_project, capsys):
    """Guard-rail: the fix must not flag healthy trackers."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    exp.arena.print_short_data_quality_report(cutoff=0.9)
    assert "Warning" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# R4 — a blank experimental_factors became a third treatment arm
# ---------------------------------------------------------------------------

def test_a_blank_experimental_factor_is_not_a_treatment_arm(tmp_path, capsys):
    """R4: one blank region switched the test from t-test to three-arm Tukey."""
    import yaml

    root = write_project(tmp_path / "proj",
                         regions=("T_1", "T_2", "T_3", "T_4", "T_5", "T_6"))
    config_path = root / "tracking_config.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["tracking_regions"]["T_6"]["experimental_factors"] = ""
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    exp = ExperimentMod.Experiment(str(root))
    exp.arena.run_pairwise_comparisons(metric="TotalDistancePerMin")
    out = capsys.readouterr().out

    assert "T-Test" in out, "the blank region turned a two-arm design into Tukey HSD"
    assert "Multiple Comparison" not in out
    assert "no experimental_factors assigned" in out, "the exclusion was silent"


# ---------------------------------------------------------------------------
# C8 — statistics reported no group sizes while dropna() shrank them
# ---------------------------------------------------------------------------

def test_comparison_output_reports_each_group_size(tmp_path, capsys):
    """C8: a reader could not tell whether p rested on 18 animals or 8."""
    root = write_project(tmp_path / "proj", regions=("T_1", "T_2", "T_3", "T_4"))
    exp = ExperimentMod.Experiment(str(root))
    exp.arena.run_pairwise_comparisons(metric="TotalDistancePerMin")
    out = capsys.readouterr().out

    assert "n=2" in out, f"group sizes missing from:\n{out}"
    assert "mean=" in out and "sd=" in out
    assert "Welch" in out, "the test actually used must be named in the output"


def test_dropped_rows_are_announced(tmp_path, capsys):
    """C8: dropna() silently shrinks a group; the count must be stated."""
    root = write_project(tmp_path / "proj",
                         regions=("T_1", "T_2", "T_3", "T_4", "T_5", "T_6"))
    exp = ExperimentMod.Experiment(str(root))

    summary = exp.arena.summarize()
    # Blank the metric for one tracker, exactly as an empty window would.
    summary.loc[summary.index[0], "TotalDistancePerMin"] = pd.NA
    exp.arena.computed_summaries[(0, 0)] = summary

    exp.arena.run_pairwise_comparisons(metric="TotalDistancePerMin")
    out = capsys.readouterr().out
    assert "1 tracker(s) excluded" in out, f"silent exclusion in:\n{out}"


# ---------------------------------------------------------------------------
# R8 — summarize() handed out its cache object
# ---------------------------------------------------------------------------

def test_summarize_does_not_hand_out_its_cache(tracker_project):
    """R8: summarize_facet wrote FacetRange into the cached flat summary."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    first = exp.arena.summarize(range_minutes=(0, 2))
    assert "FacetRange" not in first.columns

    exp.arena.summarize_facet(cutoffs=[2, 4])

    again = exp.arena.summarize(range_minutes=(0, 2))
    assert "FacetRange" not in again.columns, "the facet call leaked into the cache"


def test_mutating_a_returned_summary_does_not_corrupt_the_cache(tracker_project):
    exp = ExperimentMod.Experiment(str(tracker_project))
    first = exp.arena.summarize()
    first["Injected"] = 1
    assert "Injected" not in exp.arena.summarize().columns


# ---------------------------------------------------------------------------
# R5 — facet methods fell back to a hardcoded (10, 70)
# ---------------------------------------------------------------------------

def test_a_facet_method_without_cutoffs_raises_instead_of_guessing(tracker_project):
    """R5: the literal (10, 70) default silently mislabelled every facet plot."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    with pytest.raises(ValueError, match="facet_cutoffs"):
        exp.arena.summarize_facet()


def test_save_plots_skips_facet_plots_when_no_cutoffs_are_configured(tmp_path, capsys):
    """R5: one PDF used to mix flat p-values with plots split at 10 and 70 min."""
    root = write_project(tmp_path / "proj", extra_global={"facet_cutoffs": None})
    exp = ExperimentMod.Experiment(str(root))
    assert exp.facet_cutoffs is None

    exp.save_plots(output_dir=str(tmp_path / "out"))
    out = capsys.readouterr().out
    assert "skipping" in out.lower()
    written = list((tmp_path / "out").glob("*.png"))
    assert not any("facet" in p.name for p in written), written


# ---------------------------------------------------------------------------
# R6 — a region in the data but missing from the config aborted everything
# ---------------------------------------------------------------------------

def test_a_region_missing_from_the_config_does_not_abort_the_run(tmp_path, capsys):
    """R6: get_tracking_region returns an empty frame, so `is not None` passed."""
    import yaml

    root = write_project(tmp_path / "proj", regions=("T_1", "T_2"))
    config_path = root / "tracking_config.yaml"
    config = yaml.safe_load(config_path.read_text())
    del config["tracking_regions"]["T_2"]      # present in the data, not the config
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))

    exp = ExperimentMod.Experiment(str(root))
    summary = exp.arena.summarize()            # used to raise IndexError

    assert len(summary) == 2
    unassigned = summary[summary["TrackingRegion"] == "T_2"]
    assert list(unassigned["Treatment"]) == [""]

    # And the whole pipeline entry point survives it.
    exp.experiment_summary(save=False)


# ---------------------------------------------------------------------------
# R7 — frames with undefined speed were counted as resting
# ---------------------------------------------------------------------------

def test_unmeasurable_frames_are_reported_separately_from_resting(parameters):
    """R7: a stalled clock reported 100% resting from 3 unmeasurable frames.

    The four activity fractions always sum to 1, so before the fix a reader
    could not distinguish "the animal rested" from "we could not tell". Only one
    of these four frames has a defined speed; PercUnmeasurable now says so.
    """
    raw = make_raw(minutes=(0.0, 0.0, 1.0, 1.0), xs=(0.0, 0.0, 0.0, 0.0))
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(),
        parameters, FakeDesign(), raw,
    )
    result = tracker.summarize()

    assert result["PercUnmeasurable"] == pytest.approx(0.75)
    # The remaining fractions are denominated over the one measurable frame.
    assert result["PercResting"] == pytest.approx(1.0)


def test_a_fully_unmeasurable_tracker_reports_na_rather_than_resting(parameters):
    """Every frame unmeasurable must not read as a perfectly still animal."""
    raw = make_raw(minutes=(0.0, 0.0, 0.0), xs=(0.0, 0.0, 0.0))
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(),
        parameters, FakeDesign(), raw,
    )
    result = tracker.summarize()

    assert result["PercUnmeasurable"] == pytest.approx(1.0)
    assert pd.isna(result["PercResting"])


def test_activity_fractions_still_sum_to_one_over_measurable_frames(parameters):
    """Guard-rail: the fractions stay interpretable after the denominator change."""
    raw = make_raw(minutes=(0.0, 1.0, 2.0, 3.0), xs=(0.0, 1.0, 2.0, 3.0))
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(),
        parameters, FakeDesign(), raw,
    )
    result = tracker.summarize()
    total = result["PercWalking"] + result["PercMicro"] + result["PercResting"]
    assert total == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# R1 — TotalDistance includes frames where tracking was lost
# ---------------------------------------------------------------------------

def test_lost_tracking_frames_are_excluded_from_the_quality_filtered_distance(parameters):
    """R1: the inflation is now visible instead of being folded into the total."""
    raw = make_raw(
        minutes=(0.0, 1.0, 2.0, 3.0),
        xs=(0.0, 1.0, 50.0, 51.0),                 # a 49mm jump into the lost frame
        quality=("High", "High", "NotFound", "High"),
    )
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(),
        parameters, FakeDesign(), raw,
    )
    result = tracker.summarize()

    assert result["TotalDistance"] == pytest.approx(51.0)
    # Steps arriving at and departing from the lost frame are both discarded.
    assert result["TotalDistanceHighQualityOnly"] == pytest.approx(1.0)
    assert result["TotalDistanceHighQualityOnly"] < result["TotalDistance"]


def test_a_step_out_of_a_lost_frame_before_a_cutoff_stays_discarded(parameters):
    """The first row of a phase carries the step from the row before the
    phase. When that row was lost the step is a coordinate jump, but the
    window's own shift saw no lost row before it and kept the jump: 0 mm
    over the whole recording, 50 mm over the phases."""
    raw = make_raw(
        minutes=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        xs=(0.0, 0.0, 50.0, 0.0, 0.0, 0.0),        # row 2 lost, with a jump
        quality=("High", "High", "NotFound", "High", "High", "High"),
    )
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(),
        parameters, FakeDesign(), raw,
    )
    whole = tracker.summarize()["TotalDistanceHighQualityOnly"]
    phases = (tracker.summarize((0, 3))["TotalDistanceHighQualityOnly"]
              + tracker.summarize((3, float("inf")))["TotalDistanceHighQualityOnly"])
    assert whole == pytest.approx(0.0)
    assert phases == pytest.approx(whole)


def test_a_clean_recording_reports_the_same_distance_both_ways(parameters):
    """Guard-rail: with no lost frames the correction must be exactly zero."""
    raw = make_raw(minutes=(0.0, 1.0, 2.0), xs=(0.0, 10.0, 20.0))
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(),
        parameters, FakeDesign(), raw,
    )
    result = tracker.summarize()
    assert result["TotalDistanceHighQualityOnly"] == pytest.approx(result["TotalDistance"])


# ---------------------------------------------------------------------------
# R10 — plot_trackers_x_hist was defined twice
# ---------------------------------------------------------------------------

def test_plot_trackers_x_hist_is_defined_once(tracker_project):
    """R10: the surviving twin silently drew nothing for most tracking types."""
    import inspect

    source_lines = inspect.getsource(ArenaMod.Arena)
    assert source_lines.count("def plot_trackers_x_hist") == 1


def test_plot_trackers_x_hist_rejects_a_positional_window(tracker_project):
    """R10: the two signatures ordered their parameters differently."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    with pytest.raises(TypeError, match="range_minutes"):
        exp.arena.plot_trackers_x_hist((0, 10))


# ---------------------------------------------------------------------------
# Distances must actually scale with the rig calibration
# ---------------------------------------------------------------------------

def test_distances_scale_with_mm_per_pixel():
    """Every existing fixture used mm_per_pixel=1.0, making the assertions vacuous."""
    p = Parameters.Parameters(tracking_type=Parameters.TrackingType.TRACKER)
    p.mm_per_pixel = 0.05
    p.fps = 0

    raw = make_raw(minutes=(0.0, 1.0), xs=(0.0, 20.0))   # 20px * 0.05 == 1.0mm
    tracker = TrackerMod.Tracker(
        "T_1", 0, make_regions("T_1"), pd.DataFrame(), p, FakeDesign(), raw,
    )
    assert tracker.summarize()["TotalDistance"] == pytest.approx(1.0)


# --------------------------------------------------------------------------
# An atomic write keeps the file readable by the lab
# --------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_an_atomic_write_keeps_the_files_permissions(tmp_path):
    """mkstemp makes its file 0600 and os.replace keeps that mode, so every
    rewritten removed_regions.yaml, tracking_config.yaml and repaired export
    became unreadable to the other users of a shared folder — and a
    removals file that cannot be read declares nothing."""
    from pytrackinganalysis.io_utils import atomic_write_text

    mask = os.umask(0o022)
    try:
        fresh = tmp_path / "fresh.yaml"
        atomic_write_text(fresh, lambda h: h.write("a: 1\n"))
        assert stat.S_IMODE(fresh.stat().st_mode) == 0o644

        shared = tmp_path / "shared.yaml"
        shared.write_text("a: 0\n")
        shared.chmod(0o664)
        atomic_write_text(shared, lambda h: h.write("a: 1\n"))
        assert stat.S_IMODE(shared.stat().st_mode) == 0o664
        assert shared.read_text() == "a: 1\n"
    finally:
        os.umask(mask)
