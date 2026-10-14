"""Directional Hypotheses are editable where a design is written (ADR-0017):
the Config Editor's Global tab and the Hub's project design dialog."""

from __future__ import annotations

import copy
import os

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pytrackinganalysis import config_validation  # noqa: E402
from pytrackinganalysis import project as prj  # noqa: E402

FACTORS = {"sex": ["Female", "Male"], "genotype": ["WT", "Mutant"]}


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


def _valence(**g):
    return {"global": {"experiment_type": "Valence", "tracking_rig": "colosseum",
                       "experimental_design_factors": copy.deepcopy(FACTORS), **g},
            "counting_regions": {"Light": {"alias": "L"}, "NoLight": {"alias": "N"}},
            "tracking_regions": {"T_0": {"experimental_factors": "Female, WT"}}}


def test_valence_round_trips_hypotheses(tab):
    hyps = [{"expect": "Mutant < WT"},
            {"expect": "Male > Female", "phases": ["Experiment", "Cooldown"]}]
    tab.load(_valence(hypotheses=hyps))
    assert not tab.hypotheses.isHidden()             # shown for Valence
    assert tab.validation_errors() == []
    g = tab.dump()["global"]
    assert g["hypotheses"] == hyps
    assert "hypotheses" in tab.owned_keys()
    # What the tab writes is a valid Valence design.
    config = _valence()
    config["global"].update(g)
    assert not [p for p in config_validation.validate_config(config)
                if "ypothes" in p]


def test_a_bad_prediction_blocks_the_save(tab):
    tab.load(_valence())
    tab.hypotheses.add_row("Mutant < Male")
    errors = tab.validation_errors()
    assert any("Directional Hypotheses" in e and "different factors" in e
               for e in errors)
    tab.hypotheses.set_hypotheses([{"expect": "Mutant < WT",
                                    "phases": ["Persistence"]}])
    assert any("Persistence" in e for e in tab.validation_errors())


def test_a_type_without_the_permutation_test_hides_and_drops_them(tab):
    tab.load(_valence(hypotheses=[{"expect": "Mutant < WT"}]))
    custom = {"global": {"tracking_type": "TWOCHOICETRACKER",
                         "hypotheses": [{"expect": "Mutant < WT"}]}}
    tab.load(custom)
    assert tab.hypotheses.isHidden()
    assert "hypotheses" not in tab.dump()["global"]


def test_opening_a_second_file_never_keeps_the_first_files_predictions(tab):
    tab.load(_valence(hypotheses=[{"expect": "Mutant < WT"}]))
    tab.load(_valence())
    assert tab.hypotheses.hypotheses() == []
    assert "hypotheses" not in tab.dump()["global"]


_DESIGN = {"global": {"experiment_type": "Valence", "facet_cutoffs": [10, 70],
                      "experimental_design_factors": copy.deepcopy(FACTORS)},
           "counting_regions": ["Light", "NoLight"]}


def test_hub_dialog_writes_hypotheses_and_carries_them_to_replicates(
        qapp, tmp_path, monkeypatch):
    from pytrackinganalysis.apps.hub import ProjectInfoDialog

    prj.create_project_file(str(tmp_path), "P", design=copy.deepcopy(_DESIGN))
    for name in ("Rep1", "Rep2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "tracking_config.yaml").write_text(
            yaml.safe_dump(_valence(facet_cutoffs=[10, 70])), encoding="utf-8")
    prj.Project(str(tmp_path))                       # conforming to start with

    dlg = ProjectInfoDialog(None, start_dir=str(tmp_path))
    dlg._prefill_from_dir()
    assert not dlg.hypotheses_edit.isHidden()
    assert dlg.hypotheses_edit.hypotheses() == []
    dlg.hypotheses_edit.add_row("Mutant < WT", "Experiment")
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg._save()
    meta = yaml.safe_load((tmp_path / "project.yaml").read_text())
    assert meta["design"]["global"]["hypotheses"] == \
        [{"expect": "Mutant < WT", "phases": ["Experiment"]}]
    for name in ("Rep1", "Rep2"):
        g = yaml.safe_load((tmp_path / name / "tracking_config.yaml")
                           .read_text())["global"]
        assert g["hypotheses"] == [{"expect": "Mutant < WT",
                                    "phases": ["Experiment"]}]
    prj.Project(str(tmp_path))                       # still loads

    # Re-opening shows them; an invalid prediction is refused, nothing written.
    dlg2 = ProjectInfoDialog(None, start_dir=str(tmp_path))
    dlg2._prefill_from_dir()
    assert dlg2.hypotheses_edit.hypotheses() == \
        [{"expect": "Mutant < WT", "phases": ["Experiment"]}]
    dlg2.hypotheses_edit.add_row("Rescue < WT")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    assert dlg2._build_design() is None
    dlg.close()
    dlg2.close()


def test_hub_dialog_infers_hypotheses_from_existing_experiments(qapp, tmp_path):
    from pytrackinganalysis.apps.hub import ProjectInfoDialog

    (tmp_path / "Old1").mkdir()
    (tmp_path / "Old1" / "tracking_config.yaml").write_text(
        yaml.safe_dump(_valence(hypotheses=[{"expect": "Mutant < WT"}])),
        encoding="utf-8")
    dlg = ProjectInfoDialog(None, start_dir=str(tmp_path))
    dlg._prefill_from_dir()
    assert dlg.hypotheses_edit.hypotheses() == [{"expect": "Mutant < WT"}]
    dlg.close()
