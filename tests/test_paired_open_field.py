"""The Paired Open Field Experiment Type (ADR-0016), end to end over a
synthetic plate written to disk.

The plate is built so each rule has exactly one region to catch:

* T_0 (Ctrl)    — two flies circling: nothing to exclude or flag.
* T_1 (Treated) — fly 1 never moves: Possibly-Dead, kept in the results.
* T_2 (Ctrl)    — fly 0 is lost most of the time: Low-Tracking Exclusion.
* T_3 (Treated) — two flies circling.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest
import yaml

from pytrackinganalysis import Experiment as ExperimentMod
from pytrackinganalysis import config_validation
from pytrackinganalysis import experiment_types as et
from pytrackinganalysis.experiment_types.paired_open_field import (
    POSSIBLY_DEAD, PairedOpenFieldExperimentType)

POF = PairedOpenFieldExperimentType()
SECONDS_PER_FRAME = 0.5
N_FRAMES = 3600                       # 30 minutes


def _circle(radius_px, phase, n=N_FRAMES):
    t = np.arange(n) * 0.05 + phase
    return radius_px * np.cos(t), radius_px * np.sin(t)


def write_pof_project(root, global_extra=None):
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    regions = {"T_0": "Ctrl", "T_1": "Treated", "T_2": "Ctrl", "T_3": "Treated"}
    config = {
        "global": {"experiment_type": "PairedOpenField", "tracking_rig": "small_arena",
                   **(global_extra or {})},
        "tracking_regions": {r: {"experimental_factors": t} for r, t in regions.items()},
    }
    (root / "tracking_config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))

    rows = []
    frames = np.arange(N_FRAMES)
    for region in regions:
        for fly in (0, 1):
            ## The ROI is 200 px wide, so radius 85 px is rho 0.85 (periphery)
            ## and 55 px is rho 0.55 (centre zone, and a 3.1 mm loop — more
            ## than a body length, so not "motionless").
            x, y = _circle(85 if fly == 0 else 55, phase=fly * 1.3)
            quality = np.array(["High"] * N_FRAMES, dtype=object)
            if region == "T_1" and fly == 1:
                x, y = np.full(N_FRAMES, 30.0), np.full(N_FRAMES, -20.0)
            if region == "T_2" and fly == 0:
                quality[frames % 4 != 0] = "NotFound"   # tracked 25% of the time
            for i in frames:
                rows.append({
                    "Frame": int(i), "MSec": int(i * SECONDS_PER_FRAME * 1000),
                    "Time": "00:00:00", "Millisec": 0,
                    "TrackingRegion": region, "ObjectID": fly,
                    "RelX": x[i], "RelY": y[i], "X": x[i], "Y": y[i],
                    "DataQuality": quality[i],
                    "NObjects": 1 if quality[i] == "High" else 0,
                    "CountingRegion": "None", "Indicator": 0,
                })
    pd.DataFrame(rows).to_csv(data_dir / "Plate_Data_1.csv", index=False)
    pd.DataFrame({
        "Name": list(regions), "Type": ["Tracking"] * 4, "Shape": ["Ellipse"] * 4,
        "Width": [200] * 4, "Height": [200] * 4,
    }).to_excel(data_dir / "Plate.xlsx", sheet_name="ROI", index=False)
    return root


@pytest.fixture(scope="module")
def pof(tmp_path_factory):
    return ExperimentMod.Experiment(str(write_pof_project(tmp_path_factory.mktemp("pof"))))


# --------------------------------------------------------------------------
# The type's definition
# --------------------------------------------------------------------------

def test_registered_after_valence_so_valence_stays_the_default():
    names = [t.name for t in et.available_experiment_types()]
    assert names == ["Custom", "Valence", "PairedOpenField"]
    assert et.get_experiment_type("pairedopenfield").display_name == \
        "Paired Open Field Experiment"


def test_calibration_is_locked_on_preset_rigs_only():
    assert {"fps", "mm_per_pixel"} <= POF.owned_keys("small_arena")
    assert not {"fps", "mm_per_pixel"} & POF.owned_keys("movie")


def _problems(global_cfg):
    return POF.validate({"global": {"experiment_type": "PairedOpenField", **global_cfg},
                         "tracking_regions": {"T_0": {}}})


def test_validation():
    assert _problems({"tracking_rig": "small_arena"}) == []
    assert any("override is not allowed" in p
               for p in _problems({"tracking_rig": "arena_max", "fps": 30}))
    assert any("'fps' is required" in p for p in _problems({"tracking_rig": "movie"}))
    assert _problems({"tracking_rig": "movie", "fps": 0, "mm_per_pixel": 0.1}) == []
    assert any("min_valid_fraction" in p
               for p in _problems({"tracking_rig": "colosseum", "min_valid_fraction": 1.5}))


def test_a_config_without_distances_still_validates():
    """The type supplies [4, 8, 10]; the generic "default of [8] mm will be
    used" warning would otherwise fail a typed config at load."""
    config = {"global": {"experiment_type": "PairedOpenField", "tracking_rig": "colosseum"},
              "tracking_regions": {"T_0": {"experimental_factors": "A"}}}
    assert config_validation.validate_config(config) == []


def test_build_config_writes_the_knobs_visibly():
    g = POF.build_config(rig="colosseum")["global"]
    assert g["interaction_distances"] == [4, 8, 10]
    assert g["facet_cutoffs"] == [10, 70]
    assert g["facet_labels"] == ["Acclimation", "Experiment", "Cooldown"]
    assert g["min_valid_fraction"] == 0.8 and g["merge_distance_mm"] == 3


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------

def test_the_type_default_distances_apply(pof):
    assert list(pof.parameters.interaction_distance_mm) == [4, 8, 10]
    assert "PercentInteracting_10" in pof.arena.summarize().columns


def test_low_tracking_excludes_the_whole_pair(pof):
    excluded = pof.excluded_flies
    assert set(excluded["Name"]) == {"T_2_0", "T_2_1"}
    assert set(excluded["Reason"]) == {"Low tracking"}
    assert "T_2" not in set(pof.arena.summarize()["Name"])
    assert pof.exclusion_summary() == (
        "2 fly(ies) — 2 in Pairs tracked for under 80% of the Experiment phase "
        "(see _Excluded.csv)")


def test_a_motionless_fly_is_flagged_not_removed(pof):
    flags = pof.advisory_flags
    dead = flags[flags["Flag"] == POSSIBLY_DEAD]
    assert list(dead["Name"]) == ["T_1_1"]
    assert "T_1" in set(pof.arena.summarize()["Name"])


def test_flag_columns_mark_the_pair_and_the_fly(pof, tmp_path):
    pairs = pof._with_flag_column(pof.arena.summarize())
    flies = pof._with_flag_column(pof.arena.summarize(per_fly=True))
    assert pairs.set_index("Name")["PossiblyDeadFlag"].to_dict() == \
        {"T_0": False, "T_1": True, "T_3": False}
    assert flies.set_index("Name")["PossiblyDeadFlag"]["T_1_1"]
    assert not flies.set_index("Name")["PossiblyDeadFlag"]["T_1_0"]
    assert not pairs["ROICheckFlag"].any()


def test_open_field_measures_tell_the_flies_apart(pof):
    flies = pof.arena.summarize(per_fly=True).set_index("Name")
    ## The motionless fly explores only its own footprint; a circler covers a ring.
    assert flies.loc["T_1_1", "ExploredFraction"] < 0.05
    assert flies.loc["T_0_0", "ExploredFraction"] > flies.loc["T_1_1", "ExploredFraction"]
    ## Circling at 85% of the radius is periphery; at 55% it is the centre zone.
    assert flies.loc["T_0_0", "CentrophobismIndexAllFrames"] == pytest.approx(1.0)
    assert flies.loc["T_0_1", "CentrophobismIndexAllFrames"] == pytest.approx(-1.0)


def test_stats_label_the_tiers(pof):
    text = pof.stats(save=False)
    assert "Metric: PercentInteracting_4  [primary]" in text
    assert "Metric: EncounterRate_4  [secondary - exploratory]" in text
    assert "Metric: CentrophobismIndex  [primary]" in text


def test_report_sections_build(pof):
    from pytrackinganalysis import report_figures

    titles = [getattr(b, "title", None) for b in
              report_figures.build_paired_open_field_sections(pof)]
    for expected in ("Advisory flags", "ROI check", "Headline results",
                     "Where the flies spent their time", "Proximity over time",
                     "Exploration", "Every arena"):
        assert expected in titles


def test_outputs_match_the_manifest(pof):
    pof.save_summary()
    pof.stats(save=True)
    produced = os.listdir(pof.analysis_path)
    for suffix in POF.output_manifest():
        assert any(name.endswith(suffix) for name in produced), suffix
