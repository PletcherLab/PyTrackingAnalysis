"""End-to-end checks over a synthetic project directory on disk.

These build a real ``tracking_config.yaml`` + ``data/`` layout so the Arena and
Experiment code paths run exactly as they do for a user, without needing any of
the checked-in sample data.
"""

from __future__ import annotations

import matplotlib
import pandas as pd
import pytest
import yaml

matplotlib.use("Agg")

from pytrackinganalysis import Experiment as ExperimentMod  # noqa: E402
from pytrackinganalysis import Parameters, windowing  # noqa: E402


def write_project(root, tracking_type="TRACKER", rig="small_arena",
                  regions=("T_1", "T_2"), objects=(0,), n_samples=8,
                  counting_regions=None, extra_global=None, extra_top_level=None):
    """Create a minimal but complete project directory and return its path."""
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    global_cfg = {"tracking_type": tracking_type, "tracking_rig": rig, "facet_cutoffs": [2, 4]}
    if extra_global:
        global_cfg.update(extra_global)

    config = {
        "global": global_cfg,
        "tracking_regions": {
            name: {"experimental_factors": "Ctrl" if i % 2 == 0 else "Treated"}
            for i, name in enumerate(regions)
        },
    }
    if counting_regions:
        config["counting_regions"] = counting_regions
    if extra_top_level:
        config.update(extra_top_level)
    (root / "tracking_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    rows = []
    for region in regions:
        for object_id in objects:
            for frame in range(n_samples):
                rows.append({
                    "Frame": frame,
                    "MSec": frame * 60_000,
                    "Time": "00:00:00",
                    "Millisec": 0,
                    "TrackingRegion": region,
                    "ObjectID": object_id,
                    "RelX": frame * 10.0 + object_id,
                    "RelY": 0.0,
                    "X": frame * 10.0 + object_id,
                    "Y": 0.0,
                    "DataQuality": "High",
                    "NObjects": 1,
                    "CountingRegion": "Light" if frame % 2 == 0 else "NoLight",
                    "Indicator": 0,
                })
    pd.DataFrame(rows).to_csv(data_dir / "Synthetic_Data_1.csv", index=False)

    roi = pd.DataFrame({
        "Name": list(regions),
        "Type": ["Tracking"] * len(regions),
        "Shape": ["Rectangle"] * len(regions),
        "Width": [100] * len(regions),
        "Height": [100] * len(regions),
    })
    roi.to_excel(data_dir / "Synthetic.xlsx", sheet_name="ROI", index=False)
    return root


@pytest.fixture
def tracker_project(tmp_path):
    return write_project(tmp_path / "proj")


def test_experiment_loads_and_summarizes(tracker_project):
    exp = ExperimentMod.Experiment(str(tracker_project))
    summary = exp.arena.summarize()
    assert len(summary) == 2
    assert set(summary["Treatment"]) == {"Ctrl", "Treated"}


def test_facet_summary_accepts_a_list_of_cutoffs(tracker_project):
    """The guide and tracking_config.yaml both use lists; tuples-only rejected them."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    facet = exp.arena.summarize_facet(cutoffs=[2, 4])
    assert set(facet["FacetRange"]) == {(0.0, 2.0), (2.0, 4.0), (4.0, float("inf"))}


def test_facet_summary_accepts_a_scalar_cutoff(tracker_project):
    exp = ExperimentMod.Experiment(str(tracker_project))
    facet = exp.arena.summarize_facet(cutoffs=3)
    assert set(facet["FacetRange"]) == {(0.0, 3.0), (3.0, float("inf"))}


def test_facet_distances_sum_to_the_flat_total(tracker_project):
    """The scientific invariant, checked through the full Arena path."""
    exp = ExperimentMod.Experiment(str(tracker_project))
    flat = exp.arena.summarize().set_index("Name")["TotalDistance"]
    facet = exp.arena.summarize_facet(cutoffs=[2, 4])
    per_tracker = facet.groupby("Name")["TotalDistance"].sum()
    for name, total in flat.items():
        assert per_tracker[name] == pytest.approx(total)


def test_summary_cache_is_dropped_when_the_tracker_set_changes(tracker_project):
    exp = ExperimentMod.Experiment(str(tracker_project))
    assert len(exp.arena.summarize()) == 2
    exp.arena.set_trackers({k: v for k, v in list(exp.arena.trackers.items())[:1]})
    # A stale cache would still report the removed tracker.
    assert len(exp.arena.summarize()) == 1


def test_deleting_a_tracker_also_drops_the_cache(tracker_project):
    exp = ExperimentMod.Experiment(str(tracker_project))
    exp.arena.summarize()
    exp.arena.delete_tracker("T_2", 0)
    assert len(exp.arena.summarize()) == 1


def test_qc_survives_a_wholly_excluded_population(tracker_project, capsys):
    """Every tracker excluded leaves the faceted summary empty.

    That used to reach seaborn as a grid with zero columns and abort the whole
    Load + QC with matplotlib's "Number of rows must be a positive integer, not
    0". QC exists to show the problem, so it reports and carries on.
    """
    exp = ExperimentMod.Experiment(str(tracker_project))
    exp.arena.set_excluded_trackers(exp.arena.summarize(include_excluded=True)["Name"])
    exp.run_qc()
    out = capsys.readouterr().out
    assert "Skipping QC facet plot for TotalDistancePerMin" in out
    assert "2 of 2 tracker(s) are excluded" in out


def test_facet_plot_on_an_empty_summary_raises_a_named_error(tracker_project):
    from pytrackinganalysis import Arena

    exp = ExperimentMod.Experiment(str(tracker_project))
    exp.arena.set_excluded_trackers(exp.arena.summarize(include_excluded=True)["Name"])
    with pytest.raises(Arena.NoFacetData, match="Nothing to plot"):
        exp.arena.plot_totaldistance_facet_generaltracker(cutoffs=[2, 4])


def test_movie_rig_without_calibration_is_rejected(tmp_path):
    project = write_project(tmp_path / "movie", rig="movie")
    with pytest.raises(ValueError, match="requires explicit calibration"):
        ExperimentMod.Experiment(str(project))


def test_movie_rig_with_calibration_loads(tmp_path):
    project = write_project(
        tmp_path / "movie_ok", rig="movie",
        extra_global={"fps": 30, "mm_per_pixel": 0.05},
    )
    exp = ExperimentMod.Experiment(str(project))
    assert exp.parameters.fps == 30
    assert exp.parameters.mm_per_pixel == 0.05


def test_xchoice_total_distance_plot_is_dispatched(tmp_path):
    """The Hub advertises this plot for XCHOICETRACKER; it used to silently no-op."""
    project = write_project(tmp_path / "xchoice", tracking_type="XCHOICETRACKER")
    exp = ExperimentMod.Experiment(str(project))
    import matplotlib.pyplot as plt

    calls = []
    original_show = plt.show
    plt.show = lambda *a, **k: calls.append(plt.gcf())
    try:
        exp.arena.plot_totaldistance()
    finally:
        plt.show = original_show
        plt.close("all")
    assert calls, "XCHOICETRACKER produced no total-distance figure"


def test_counter_experiment_runs_qc_without_crashing(tmp_path):
    """run_analysis used to die in qc() because counters have no get_data_quality."""
    project = write_project(
        tmp_path / "counter", tracking_type="COUNTER",
        extra_global={"facet_cutoffs": [2, 4]},
    )
    exp = ExperimentMod.Experiment(str(project))
    assert not exp.arena.supports_data_quality()
    exp.qc()  # must not raise
    exp.experiment_summary(save=False)  # must not raise


def test_counter_data_quality_raises_a_clear_error(tmp_path):
    project = write_project(tmp_path / "counter2", tracking_type="COUNTER")
    exp = ExperimentMod.Experiment(str(project))
    with pytest.raises(ValueError, match="not available"):
        exp.arena.get_data_quality()


def test_pairwise_requires_exactly_two_objects_per_region(tmp_path):
    project = write_project(
        tmp_path / "pairwise_bad",
        tracking_type="PAIRWISEINTERACTIONTRACKER",
        regions=("T_1",),
        objects=(0, 1, 2),
    )
    with pytest.raises(ValueError, match="exactly two objects"):
        ExperimentMod.Experiment(str(project))


def test_pairwise_pairs_both_directions(tmp_path):
    project = write_project(
        tmp_path / "pairwise_ok",
        tracking_type="PAIRWISEINTERACTIONTRACKER",
        regions=("T_1",),
        objects=(0, 1),
    )
    exp = ExperimentMod.Experiment(str(project))
    first = exp.arena.get_tracker("T_1_0")
    second = exp.arena.get_tracker("T_1_1")
    assert first.neighbor_tracker is second
    assert second.neighbor_tracker is first


def test_rig_calibration_is_shared_between_generic_and_pairwise_setters():
    generic = Parameters.Parameters()
    generic.set_colloseum_values(Parameters.TrackingType.TRACKER)
    pairwise = Parameters.Parameters()
    pairwise.set_pairwise_interaction_values_colloseum([8])
    assert generic.mm_per_pixel == pairwise.mm_per_pixel
