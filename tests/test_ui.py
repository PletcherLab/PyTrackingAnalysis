"""UI-layer regressions that can be checked headlessly.

Qt runs under the offscreen platform plugin; the whole module is skipped when a
QApplication cannot be created (no Qt libs in the environment).
"""

from __future__ import annotations

import os

import pandas as pd
import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as err:  # noqa: BLE001
            pytest.skip(f"Qt is unavailable: {err}")
    return app


# --------------------------------------------------------------------------
# Config Editor round trip
# --------------------------------------------------------------------------

CONFIG_WITH_EXTRAS = {
    "global": {
        "tracking_type": "TRACKER",
        "tracking_rig": "small_arena",
        "facet_cutoffs": [10, 70],
        # A key the Global tab does not render.
        "custom_lab_note": "keep me",
    },
    "tracking_regions": {"T_1": {"experimental_factors": "Ctrl"}},
    # A whole top-level section the editor has no tab for.
    "scripts": [
        {"name": "nightly", "steps": [{"action": "load_experiment", "params": {"path": "."}}]}
    ],
    "some_future_section": {"a": 1},
}


@pytest.fixture
def dialogs(monkeypatch):
    """Capture the editor's modal dialogs — they would block a headless run."""
    from pytrackinganalysis.apps import config_editor as ce

    seen: dict[str, list] = {"information": [], "warning": [], "critical": []}
    for kind in seen:
        monkeypatch.setattr(
            ce.QMessageBox, kind,
            lambda *args, _kind=kind, **kwargs: seen[_kind].append(args),
        )
    # A modal question() (e.g. the "seed required regions?" prompt) would block a
    # headless run forever. Default to "No" so nothing is auto-inserted.
    monkeypatch.setattr(
        ce.QMessageBox, "question",
        lambda *args, **kwargs: ce.QMessageBox.StandardButton.No,
    )
    return seen


@pytest.fixture
def editor(qapp, tmp_path, dialogs):
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    path = tmp_path / "tracking_config.yaml"
    path.write_text(yaml.safe_dump(CONFIG_WITH_EXTRAS, sort_keys=False))
    window = ConfigEditorWindow(str(path))
    yield window
    window.close()


def test_saving_preserves_the_scripts_section(editor):
    """Rebuilding the file from the three visible tabs used to delete scripts:."""
    dumped = editor._dump_config()
    assert dumped["scripts"] == CONFIG_WITH_EXTRAS["scripts"]


def test_saving_preserves_unknown_top_level_sections(editor):
    assert editor._dump_config()["some_future_section"] == {"a": 1}


def test_saving_preserves_unknown_global_keys(editor):
    assert editor._dump_config()["global"]["custom_lab_note"] == "keep me"


def test_saving_still_writes_the_edited_sections(editor):
    dumped = editor._dump_config()
    assert dumped["global"]["tracking_type"] == "TRACKER"
    assert "tracking_regions" in dumped


def test_a_full_write_round_trip_keeps_everything(editor, tmp_path):
    out = tmp_path / "written.yaml"
    editor._write(out)
    reloaded = yaml.safe_load(out.read_text())
    assert reloaded["scripts"] == CONFIG_WITH_EXTRAS["scripts"]
    assert reloaded["global"]["custom_lab_note"] == "keep me"


def test_an_invalid_number_blocks_the_save(editor, tmp_path, dialogs):
    """A bad value used to be dropped silently, leaving the analysis on defaults."""
    editor._global_tab.mm_per_pixel.setText("not-a-number")
    out = tmp_path / "blocked.yaml"
    editor._write(out)

    assert dialogs["warning"], "the user was not told the value was rejected"
    assert not out.exists(), "an invalid config was written to disk"


def test_movie_rig_without_calibration_is_flagged(editor):
    from pytrackinganalysis.apps._config_tabs import TRACKING_RIGS

    index = [value for _, value in TRACKING_RIGS].index("movie")
    editor._global_tab.tracking_rig.setCurrentIndex(index)
    editor._global_tab.fps.setText("")
    editor._global_tab.mm_per_pixel.setText("")
    errors = " | ".join(editor._global_tab.validation_errors())
    assert "FPS" in errors and "mm per pixel" in errors


# --------------------------------------------------------------------------
# Experiment Type UI (Phase 5)
# --------------------------------------------------------------------------

def _select_experiment_type(tab, name):
    from pytrackinganalysis.apps._config_tabs import _find_data
    tab.experiment_type.setCurrentIndex(_find_data(tab.experiment_type, name))


def test_global_tab_defaults_to_custom_and_is_unconstrained(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab, TRACKING_RIGS
    tab = GlobalTab()
    assert tab.current_experiment_type().is_custom
    assert tab.tracking_type.isEnabled()          # editable for Custom
    # Full rig list available for Custom.
    assert tab.tracking_rig.count() == len(TRACKING_RIGS)


def test_selecting_valence_locks_owned_fields_and_constrains_rig(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab
    tab = GlobalTab()
    _select_experiment_type(tab, "Valence")
    # Tracking type fixed + disabled.
    assert tab.tracking_type.currentData() == "TWOCHOICETRACKER"
    assert not tab.tracking_type.isEnabled()
    # Rig constrained to Max/Colosseum.
    rigs = {tab.tracking_rig.itemData(i) for i in range(tab.tracking_rig.count())}
    assert rigs == {"arena_max", "colosseum", "small_arena"}
    # Facets default-filled but EDITABLE (a default, not owned); calibration disabled.
    assert tab.facet_cutoffs.text().replace(" ", "") == "10,70"
    assert tab.facet_cutoffs.isEnabled()
    assert not tab.fps.isEnabled() and not tab.mm_per_pixel.isEnabled()


def test_valence_dump_is_minimal_and_omits_owned_fields(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab, _find_data
    tab = GlobalTab()
    _select_experiment_type(tab, "Valence")
    tab.tracking_rig.setCurrentIndex(_find_data(tab.tracking_rig, "colosseum"))
    g = tab.dump()["global"]
    assert g["experiment_type"] == "Valence"
    assert g["tracking_rig"] == "colosseum"
    # Owned/derived fields must NOT be written to disk (ADR-0001).
    assert "tracking_type" not in g
    assert "fps" not in g and "mm_per_pixel" not in g
    # facet_cutoffs is an editable default, so it IS written.
    assert g["facet_cutoffs"] == [10, 70]


def test_custom_dump_has_no_experiment_type_key(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab
    tab = GlobalTab()  # defaults to Custom
    g = tab.dump()["global"]
    assert "experiment_type" not in g          # absence == Custom (back-compat)
    assert g["tracking_type"] == "TRACKER"


def test_load_then_dump_round_trips_a_valence_config(qapp):
    from pytrackinganalysis.apps._config_tabs import GlobalTab
    tab = GlobalTab()
    tab.load({"global": {"experiment_type": "Valence", "tracking_rig": "arena_max"}})
    assert tab.current_experiment_type().name == "Valence"
    assert tab.tracking_rig.currentData() == "arena_max"
    g = tab.dump()["global"]
    assert g["experiment_type"] == "Valence" and g["tracking_rig"] == "arena_max"
    assert "tracking_type" not in g


def test_editor_blocks_saving_an_incomplete_valence_config(editor, tmp_path, dialogs):
    # Switch to Valence but leave counting regions as the loaded Custom set
    # (which are not Light/NoLight) -> save must be refused.
    _select_experiment_type(editor._global_tab, "Valence")
    out = tmp_path / "bad_valence.yaml"
    editor._write(out)
    assert dialogs["warning"], "an invalid typed config was allowed through"
    assert not out.exists()


def _valence_editor(qapp, tmp_path, rig):
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow
    cfg = {"global": {"experiment_type": "Valence", "tracking_rig": rig},
           "counting_regions": {"Light": {"alias": "L"}, "NoLight": {"alias": "N"}}}
    path = tmp_path / "tracking_config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return ConfigEditorWindow(str(path))


def test_opening_a_valence_max_project_lays_out_36_regions(qapp, tmp_path, dialogs):
    win = _valence_editor(qapp, tmp_path, "arena_max")
    assert win._tracking_tab.region_names() == [f"T_{i}" for i in range(36)]
    win.close()


def test_a_max_plate_laid_out_by_the_editor_flips_the_first_18(qapp, tmp_path,
                                                              dialogs):
    """Arena Max's first 18 wells (T_0..T_17) face the other way, so their X
    multiplier is -1. The editor used to lay every row out at +1, silently
    disagreeing with the same config built from scratch."""
    win = _valence_editor(qapp, tmp_path, "arena_max")
    regions = win._tracking_tab.dump()["tracking_regions"]
    assert [regions[f"T_{i}"]["x_location_multiplier"] for i in range(18)] == \
        [-1] * 18
    assert [regions[f"T_{i}"]["x_location_multiplier"] for i in range(18, 36)] == \
        [1] * 18
    assert all(r["y_location_multiplier"] == 1 for r in regions.values())

    # The same plate the type builds from scratch, well for well.
    from pytrackinganalysis import experiment_types as et
    built = et.get_experiment_type("Valence").build_config(
        rig="arena_max")["tracking_regions"]
    assert {n: (r["x_location_multiplier"], r["y_location_multiplier"])
            for n, r in regions.items()} == \
        {n: (r["x_location_multiplier"], r["y_location_multiplier"])
         for n, r in built.items()}
    win.close()


def test_a_colosseum_plate_laid_out_by_the_editor_is_all_positive(
        qapp, tmp_path, dialogs):
    win = _valence_editor(qapp, tmp_path, "colosseum")
    regions = win._tracking_tab.dump()["tracking_regions"]
    assert all(r["x_location_multiplier"] == 1 for r in regions.values())
    win.close()


def test_opening_a_valence_colosseum_project_lays_out_24_regions(qapp, tmp_path, dialogs):
    win = _valence_editor(qapp, tmp_path, "colosseum")
    assert win._tracking_tab.region_names() == [f"T_{i}" for i in range(24)]
    win.close()


def test_an_eighteen_well_colosseum_plate_is_left_alone(qapp, tmp_path,
                                                       dialogs, monkeypatch):
    """The Colosseum is run at 18 wells as well as 24. Offering to "fix" a
    valid 18-well plate would reset every treatment assignment on it."""
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    cfg = {"global": {"experiment_type": "Valence",
                      "tracking_rig": "colosseum"},
           "counting_regions": {"Light": {"alias": "L"},
                                "NoLight": {"alias": "N"}},
           "tracking_regions": {
               f"T_{i}": {"experimental_factors": "chr",
                          "x_location_multiplier": 1,
                          "y_location_multiplier": 1}
               for i in range(18)}}
    path = tmp_path / "tracking_config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    win = ConfigEditorWindow(str(path))
    # Opening it leaves the plate as it is...
    assert win._tracking_tab.region_names() == [f"T_{i}" for i in range(18)]

    # ...and so does re-selecting the rig it is already on. question() would
    # say Yes here, so a plate that got offered up would come back as 24.
    from pytrackinganalysis.apps import config_editor as ce
    asked = []

    def _question(*a, **k):
        asked.append(a)
        return ce.QMessageBox.StandardButton.Yes

    monkeypatch.setattr(ce.QMessageBox, "question", staticmethod(_question))
    win._loading = False
    win._maybe_populate_tracking_regions("colosseum")
    assert not asked
    assert win._tracking_tab.region_names() == [f"T_{i}" for i in range(18)]

    # It saves without complaint, which is the whole point.
    out = tmp_path / "saved.yaml"
    win._write(out)
    assert out.is_file() and not dialogs["warning"]
    win.close()


def test_changing_valence_rig_regenerates_the_plate_on_confirm(qapp, tmp_path, dialogs, monkeypatch):
    from pytrackinganalysis.apps import config_editor as ce
    from pytrackinganalysis.apps._config_tabs import _find_data
    win = _valence_editor(qapp, tmp_path, "arena_max")
    assert len(win._tracking_tab.region_names()) == 36
    # Confirm the destructive regenerate.
    monkeypatch.setattr(ce.QMessageBox, "question",
                        lambda *a, **k: ce.QMessageBox.StandardButton.Yes)
    gt = win._global_tab
    gt.tracking_rig.setCurrentIndex(_find_data(gt.tracking_rig, "colosseum"))
    assert win._tracking_tab.region_names() == [f"T_{i}" for i in range(24)]
    win.close()


def test_declining_valence_rig_change_keeps_existing_regions(qapp, tmp_path, dialogs):
    # dialogs fixture answers question() with No -> plate is left untouched.
    from pytrackinganalysis.apps._config_tabs import _find_data
    win = _valence_editor(qapp, tmp_path, "arena_max")
    gt = win._global_tab
    gt.tracking_rig.setCurrentIndex(_find_data(gt.tracking_rig, "colosseum"))
    assert len(win._tracking_tab.region_names()) == 36  # unchanged
    win.close()


def test_scaffold_project_creates_a_shaped_incomplete_valence_project(qapp, tmp_path):
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow
    from pytrackinganalysis import config_validation

    config_path = ConfigEditorWindow.scaffold_project(tmp_path, "MyValence", "Valence")
    project = config_path.parent
    assert (project / "data").is_dir()
    assert (project / "analysis").is_dir() and (project / "qc").is_dir()

    cfg = yaml.safe_load(config_path.read_text())
    assert cfg["global"]["experiment_type"] == "Valence"
    assert list(cfg["counting_regions"]) == ["Light", "NoLight"]
    # Intentionally incomplete: rig is blank, so it does not yet validate.
    problems = config_validation.validate_config(cfg)
    assert any("tracking_rig" in p for p in problems)


def test_scaffold_project_refuses_to_overwrite(qapp, tmp_path):
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow
    (tmp_path / "Dup").mkdir()
    with pytest.raises(FileExistsError):
        ConfigEditorWindow.scaffold_project(tmp_path, "Dup", "Valence")


def test_editor_rig_calibrations_match_the_analysis(qapp):
    """The editor's placeholder table must not drift from Parameters."""
    from pytrackinganalysis import Parameters
    from pytrackinganalysis.apps._config_tabs import RIG_MM_PER_PIXEL

    for rig, value in Parameters.RIG_MM_PER_PIXEL.items():
        assert RIG_MM_PER_PIXEL[rig] == value


# --------------------------------------------------------------------------
# DataFrame table model
# --------------------------------------------------------------------------

def test_table_model_sorts(qapp):
    """Views call setSortingEnabled(True); without sort() the clicks did nothing."""
    from pytrackinganalysis.ui.table_model import DataFrameModel

    df = pd.DataFrame({"Tracker": ["c", "a", "b"], "HighQuality": [0.5, 0.9, 0.7]})
    model = DataFrameModel(df)

    model.sort(1, Qt.SortOrder.AscendingOrder)
    assert list(model.dataframe()["HighQuality"]) == [0.5, 0.7, 0.9]

    model.sort(0, Qt.SortOrder.DescendingOrder)
    assert list(model.dataframe()["Tracker"]) == ["c", "b", "a"]


def test_table_model_sort_ignores_an_out_of_range_column(qapp):
    from pytrackinganalysis.ui.table_model import DataFrameModel

    model = DataFrameModel(pd.DataFrame({"A": [1, 2]}))
    model.sort(9)  # must not raise
    assert list(model.dataframe()["A"]) == [1, 2]
