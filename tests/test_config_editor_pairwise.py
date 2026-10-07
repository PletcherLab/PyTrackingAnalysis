"""The Config Editor manages every pairwise setting (ADR-0014–0016).

The Global tab shows a Pairwise analysis section for a pairwise tracking type
— proximity and Encounter settings for both, the open-field ones only for the
tracker — and the Paired Open Field quality criterion; the Tracking regions
tab takes a plate that is not fixed by the rig from the recording itself.
"""

from __future__ import annotations

import os

import pandas as pd
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from pytrackinganalysis import config_validation  # noqa: E402
from pytrackinganalysis.project import tracking_regions_in_data  # noqa: E402

PAIRWISE_KEYS = ("merge_distance_mm", "encounter_hysteresis_mm", "encounter_gap_s",
                 "encounter_min_s", "wall_zone_mm", "fly_length_mm", "fly_width_mm")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as err:  # noqa: BLE001
            pytest.skip(f"Qt is unavailable: {err}")
    return app


@pytest.fixture
def tab(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab
    return GlobalTab()


def _pof(**extra):
    return {"global": {"experiment_type": "PairedOpenField",
                       "tracking_rig": "colosseum", **extra},
            "tracking_regions": {"T_0": {"experimental_factors": "A"}}}


def _custom(tracking_type, **extra):
    return {"global": {"tracking_type": tracking_type, "tracking_rig": "colosseum",
                       **extra},
            "tracking_regions": {"T_0": {"experimental_factors": "A"}}}


ALL_KNOBS = {"interaction_distances": [4, 8, 10], "merge_distance_mm": 3,
             "encounter_hysteresis_mm": 0.5, "encounter_gap_s": 0.5,
             "encounter_min_s": 0.5, "wall_zone_mm": 2.5, "fly_length_mm": 2.5,
             "fly_width_mm": 1, "min_valid_fraction": 0.8}


# --------------------------------------------------------------------------
# The Global tab
# --------------------------------------------------------------------------

def test_a_paired_open_field_config_round_trips_every_setting(tab):
    tab.load(_pof(**ALL_KNOBS))
    assert tab._pairwise_section.isVisibleTo(tab)
    assert tab._open_field_widget.isVisibleTo(tab)
    assert tab.min_valid_fraction.isVisibleTo(tab)
    g = tab.dump()["global"]
    for key, value in ALL_KNOBS.items():
        assert g[key] == value, key
    ## Whole numbers stay whole: 8.0 would name a PercentInteracting_8.0 column.
    assert all(isinstance(d, int) for d in g["interaction_distances"])


def test_a_blank_field_takes_the_default_and_says_which(tab):
    tab.load(_pof())
    assert tab.interaction_distances.placeholderText() == "default 4, 8, 10"
    assert tab.pairwise_fields["merge_distance_mm"].placeholderText() == "default 3  (0 = off)"
    assert tab.min_valid_fraction.text() == "0.8"          # the type default, shown
    g = tab.dump()["global"]
    assert not set(PAIRWISE_KEYS) & set(g)
    assert "interaction_distances" not in g


def test_custom_pairwise_tracker_gets_the_section_with_the_generic_default(tab):
    tab.load(_custom("PAIRWISEINTERACTIONTRACKER", interaction_distances=[2.5, 5]))
    assert tab._pairwise_section.isVisibleTo(tab)
    assert tab.interaction_distances.placeholderText() == "default 8"
    assert not tab.min_valid_fraction.isVisibleTo(tab)   # Paired Open Field policy only
    assert tab.dump()["global"]["interaction_distances"] == [2.5, 5]


def test_a_counter_shows_only_what_a_counter_measures(tab):
    tab.load(_custom("PAIRWISEINTERACTIONCOUNTER", **{
        k: v for k, v in ALL_KNOBS.items() if k != "min_valid_fraction"}))
    assert tab._pairwise_section.isVisibleTo(tab)
    assert not tab._open_field_widget.isVisibleTo(tab)
    assert not tab._proximity_form.isRowVisible(tab.pairwise_fields["encounter_gap_s"])
    assert tab._proximity_form.isRowVisible(tab.pairwise_fields["merge_distance_mm"])
    g = tab.dump()["global"]
    assert g["merge_distance_mm"] == 3 and g["interaction_distances"] == [4, 8, 10]
    ## Settings a counter never reads are not left in the file.
    for key in ("encounter_gap_s", "wall_zone_mm", "fly_length_mm"):
        assert key not in g


def test_a_non_pairwise_type_hides_and_drops_the_section(tab):
    tab.load(_custom("PAIRWISEINTERACTIONTRACKER", **{
        k: v for k, v in ALL_KNOBS.items() if k != "min_valid_fraction"}))
    tab.tracking_type.setCurrentIndex(tab.tracking_type.findData("TRACKER"))
    assert not tab._pairwise_section.isVisibleTo(tab)
    g = tab.dump()["global"]
    assert "interaction_distances" not in g and not set(PAIRWISE_KEYS) & set(g)


def test_choosing_paired_open_field_shows_its_settings(tab):
    tab.load(_custom("TRACKER"))
    assert not tab._pairwise_section.isVisibleTo(tab)
    tab.experiment_type.setCurrentIndex(tab.experiment_type.findData("PairedOpenField"))
    assert tab._pairwise_section.isVisibleTo(tab)
    assert tab.min_valid_fraction.isVisibleTo(tab) and tab.min_valid_fraction.text() == "0.8"


@pytest.mark.parametrize("key,text,needle", [
    ("merge_distance_mm", "-1", "zero or more"),
    ("wall_zone_mm", "0", "greater than zero"),
    ("fly_width_mm", "3", "cannot be wider"),
    ("encounter_gap_s", "soon", "not a number"),
])
def test_bad_pairwise_values_block_the_save(tab, key, text, needle):
    tab.load(_pof())
    tab.pairwise_fields[key].setText(text)
    assert any(needle in e for e in tab.validation_errors())


def test_bad_distances_and_fraction_block_the_save(tab):
    tab.load(_pof())
    tab.interaction_distances.setText("4, 0")
    tab.min_valid_fraction.setText("1.5")
    errors = tab.validation_errors()
    assert any("greater than zero" in e for e in errors)
    assert any("min_valid_fraction" in e for e in errors)


def test_opening_a_second_file_never_keeps_the_first_files_values(tab):
    """Fields used to keep the previous file's value when the next file
    lacked the key — and the next save wrote it into that file."""
    tab.load(_custom("PAIRWISEINTERACTIONTRACKER", interaction_distances=[3],
                     merge_distance_mm=2, micromove_speed_mm_sec=[0.1, 1]))
    tab.load(_custom("PAIRWISEINTERACTIONTRACKER"))
    assert tab.interaction_distances.text() == ""
    assert tab.pairwise_fields["merge_distance_mm"].text() == ""
    assert tab.micromove_min.text() == "" and tab.micromove_max.text() == ""


def test_a_dumped_paired_open_field_config_validates(tab):
    tab.load(_pof(**ALL_KNOBS))
    config = {**tab.dump(), "tracking_regions": {"T_0": {"experimental_factors": "A"}}}
    assert config_validation.validate_config(config) == []


# --------------------------------------------------------------------------
# Tracking regions from the recording
# --------------------------------------------------------------------------

def _experiment(tmp_path, regions, config):
    data = tmp_path / "data"
    data.mkdir(parents=True)
    pd.DataFrame({"Name": list(regions) + ["M0"],
                  "Type": ["Tracking"] * len(regions) + ["Masking"],
                  "Shape": ["Ellipse"] * (len(regions) + 1),
                  "Width": [200] * (len(regions) + 1),
                  "Height": [200] * (len(regions) + 1)}).to_excel(
        data / "Rec.xlsx", sheet_name="ROI", index=False)
    path = tmp_path / "tracking_config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    return path


def test_tracking_regions_in_data_reads_the_roi_sheet_in_natural_order(tmp_path):
    _experiment(tmp_path, ["T_10", "T_2", "T_1"], {"global": {}})
    assert tracking_regions_in_data(tmp_path) == ["T_1", "T_2", "T_10"]
    assert tracking_regions_in_data(tmp_path / "missing") == []


def test_an_empty_plate_is_filled_from_the_recording_on_open(qapp, tmp_path):
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    config = {"global": {"experiment_type": "PairedOpenField", "tracking_rig": "colosseum",
                         "experimental_design_factors": {"Diet": ["Fed"]}},
              "tracking_regions": {}}
    window = ConfigEditorWindow(str(_experiment(tmp_path, ["T_0", "T_1", "T_2"], config)))
    try:
        assert window._tracking_tab.region_names() == ["T_0", "T_1", "T_2"]
        assert window._tracking_tab.from_data_btn.isVisibleTo(window._tracking_tab)
        ## A design with one possible treatment assigns it to the new rows.
        regions = window._tracking_tab.dump()["tracking_regions"]
        assert {r["experimental_factors"] for r in regions.values()} == {"Fed"}
    finally:
        window.close()


def test_filling_from_data_keeps_the_treatments_of_regions_it_keeps(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab, TrackingRegionsTab

    tab = TrackingRegionsTab(GlobalTab())
    tab._global_tab.load({"global": {"experimental_design_factors": {"Diet": ["Fed", "Starved"]}}})
    tab.load({"tracking_regions": {"T_1": {"experimental_factors": "Starved"},
                                   "T_9": {"experimental_factors": "Fed"}}})
    tab.merge_region_names(["T_0", "T_1", "T_2"])
    regions = tab.dump()["tracking_regions"]
    assert list(regions) == ["T_0", "T_1", "T_2"]
    assert regions["T_1"]["experimental_factors"] == "Starved"
    assert regions["T_0"]["experimental_factors"] == ""
