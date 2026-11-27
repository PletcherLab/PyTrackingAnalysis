"""A replicate may read its recording's own config (ADR-0020).

A replicate folder may hold ``tracking_member.yaml`` instead of a
``tracking_config.yaml``: its ``config:`` names the recording's config where
the lab's catalogue keeps it, so a recording has one config however many
Projects analyse it. The layout these tests build is the catalogue's: runs
under ``runs/``, each with its config and ``data/``; Projects under
``studies/``, whose replicates hold only the pointer. A replicate holding its
own config is exactly what it always was, and a Project may mix the two.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from pytrackinganalysis import batch as batch_mod
from pytrackinganalysis import layout
from pytrackinganalysis import project as prj
from pytrackinganalysis.Experiment import Experiment, _is_experiment_dir
from test_experiment_integration import write_project
from test_referenced_data import (
    ANALYSIS,
    ASSAY,
    RUN1,
    RUN2,
    changed,
    lock,
    make_run,
    managed_root,
    run_dir,
    snapshot,
    unlock,
)

#: Where each run's config is, from a replicate folder of a Project under
#: studies/<study>/analyses/<analysis>/: relative, forward slashes.
def config_ref(run) -> str:
    return f"../../../../../runs/{ASSAY}/2026/{run}/tracking_config.yaml"


def pointer(member: Path, run, **extra) -> Path:
    """A replicate folder holding only ``tracking_member.yaml``."""
    member.mkdir(parents=True, exist_ok=True)
    body = {"config": config_ref(run), **extra}
    (member / layout.MEMBER_FILENAME).write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return member


def analysis_dir(managed, name=ANALYSIS, design=None) -> Path:
    path = Path(managed) / "studies" / "test-study" / "analyses" / name
    path.mkdir(parents=True)
    prj.create_project_file(str(path), design=design)
    return path


def real(path) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


@pytest.fixture
def store(tmp_path):
    managed = managed_root(tmp_path)
    make_run(managed, RUN1, regions=("T_1", "T_2"))
    make_run(managed, RUN2, regions=("T_1", "T_2", "T_3", "T_4"))
    return managed


# ---------------------------------------------------------------------------
# The usual layout is untouched
# ---------------------------------------------------------------------------


def test_a_replicate_holding_its_own_config_reads_it_as_before(tmp_path):
    replicate = write_project(tmp_path / "Rep1")
    found = layout.config_location(replicate)
    assert not found.referenced and not found.problem
    assert found.path == str(replicate / "tracking_config.yaml")
    config = yaml.safe_load((replicate / "tracking_config.yaml").read_text())
    assert prj.apply_design_settings(replicate, config) == (config, [])
    location = layout.data_location(replicate)
    assert location.path == str(replicate / "data") and not location.referenced


# ---------------------------------------------------------------------------
# A replicate that names its recording's config
# ---------------------------------------------------------------------------


def test_a_member_reads_its_runs_config_and_the_data_beside_it(store):
    analysis = analysis_dir(store)
    member = pointer(analysis / RUN1, RUN1)

    found = layout.config_location(member)
    assert found.referenced and not found.problem
    assert real(found.path) == real(run_dir(store, RUN1) / "tracking_config.yaml")
    location = layout.data_location(member)
    assert real(location.path) == real(run_dir(store, RUN1) / "data")
    assert location.via == config_ref(RUN1) and location.referenced
    assert prj.is_experiment_dir(member) and _is_experiment_dir(str(member))
    assert layout.classify(member).status == layout.OK
    assert prj.Project(str(analysis)).experiment_names == [RUN1]


def test_the_pointers_data_folder_names_the_data(store):
    repaired = run_dir(store, RUN1) / "derived" / "clock-repaired"
    repaired.mkdir(parents=True)
    for source in (run_dir(store, RUN1) / "data").iterdir():
        (repaired / source.name).write_bytes(source.read_bytes())
    analysis = analysis_dir(store)
    member = pointer(analysis / RUN1, RUN1, data_folder=(
        f"../../../../../runs/{ASSAY}/2026/{RUN1}/derived/clock-repaired"))
    assert real(layout.data_location(member).path) == real(repaired)
    assert layout.classify(member).status == layout.OK


@pytest.mark.parametrize(("case", "says"), [
    ("no key", "names no config"),
    ("missing", "does not exist"),
    ("own config too", "which is the experiment's config"),
])
def test_a_pointer_that_cannot_be_followed_blocks_and_says_why(store, case, says):
    analysis = analysis_dir(store)
    pointer(analysis / RUN2, RUN2)  # a healthy one, so the Project loads
    member = analysis / RUN1
    if case == "no key":
        member.mkdir()
        (member / layout.MEMBER_FILENAME).write_text("data_folder: x\n")
    elif case == "missing":
        pointer(member, "R-20990101-test-09")
    else:
        pointer(member, RUN1)
        write_project(member)
    found = layout.config_location(member)
    assert says in found.problem
    assert layout.classify(member).blocked
    project = prj.Project(str(analysis))
    assert project.experiment_names == [RUN2]
    assert any(line.startswith(f"{RUN1} is not loaded") and says in line
               for line in project.warnings)


def test_a_pointer_into_a_folder_holding_the_member_is_refused(store):
    member = run_dir(store, RUN1) / "analyses" / "M1"
    member.mkdir(parents=True)
    (member / layout.MEMBER_FILENAME).write_text(
        "config: ../../tracking_config.yaml\n")
    assert "holds the replicate folder" in layout.config_location(member).problem


def test_a_member_is_never_filed(store):
    member = pointer(analysis_dir(store) / RUN1, RUN1)
    (member / "Loose.xlsx").write_bytes(b"x")
    plan = layout.plan_filing(member)
    assert layout.MEMBER_FILENAME in plan.refused and not plan.moves


# ---------------------------------------------------------------------------
# Inside a Project: the recording's description, the design's settings
# ---------------------------------------------------------------------------


def test_a_design_sets_the_settings_and_the_recordings_description_stays(store):
    design = {"global": {"facet_cutoffs": [1, 3]}}
    analysis = analysis_dir(store, design=design)
    pointer(analysis / RUN1, RUN1)
    ## A replicate of its own, in the same Project, matching the design.
    write_project(analysis / "Local", extra_global={"facet_cutoffs": [1, 3]})

    project = prj.Project(str(analysis))

    assert project.experiment_names == ["Local", RUN1]
    used = project.configs[RUN1]["global"]
    assert used["facet_cutoffs"] == [1, 3]  # the run's config says [2, 4]
    assert used["tracking_type"] == "TRACKER" and used["tracking_rig"] == "small_arena"
    assert project.unused_settings == {RUN1: ["facet_cutoffs"]}
    assert any("facet_cutoffs" in line and "not used" in line
               for line in project.warnings)
    assert "Local" not in project.unused_settings


def test_without_a_design_the_recordings_config_is_read_whole(store):
    analysis = analysis_dir(store)
    pointer(analysis / RUN1, RUN1)
    project = prj.Project(str(analysis))
    assert project.configs[RUN1]["global"]["facet_cutoffs"] == [2, 4]
    assert project.unused_settings == {}


def test_an_experiment_opened_on_a_member_uses_the_designs_settings(store):
    analysis = analysis_dir(store, design={"global": {"facet_cutoffs": [1, 3]}})
    member = pointer(analysis / RUN1, RUN1)
    runs_before = snapshot(store / "runs")

    exp = Experiment(str(member))

    assert real(exp.config_path) == real(run_dir(store, RUN1) / "tracking_config.yaml")
    assert exp.config["global"]["facet_cutoffs"] == [1, 3]
    assert exp.unused_settings == ["facet_cutoffs"]
    assert (member / "analysis").is_dir() and (member / "qc").is_dir()
    assert changed(runs_before, snapshot(store / "runs")) == set()


def test_the_hypotheses_are_never_written_into_a_runs_config(store):
    analysis = analysis_dir(store)
    pointer(analysis / RUN1, RUN1)
    before = (run_dir(store, RUN1) / "tracking_config.yaml").read_bytes()
    assert prj.replicate_config_paths(str(analysis)) == []
    prj.write_replicate_hypotheses(str(analysis), [{"expect": "Treated > Ctrl"}])
    assert (run_dir(store, RUN1) / "tracking_config.yaml").read_bytes() == before


# ---------------------------------------------------------------------------
# The whole pipeline, with the runs read-only
# ---------------------------------------------------------------------------


def test_a_batch_run_through_pointers_writes_only_in_the_project(store):
    analysis = analysis_dir(store)
    pointer(analysis / RUN1, RUN1)
    pointer(analysis / RUN2, RUN2)
    runs = store / "runs"
    runs_before = snapshot(runs)
    lock(runs)
    try:
        logs: list = []
        results = batch_mod.run_batch(str(analysis.parent), log=logs.append)
    finally:
        unlock(runs)
    assert results == {ANALYSIS: "ok"}, logs
    for name in (RUN1, RUN2):
        assert (analysis / name / "analysis" / "Synthetic_Summary.csv").is_file()
        assert (analysis / name / f"{name}_report.pdf").is_file()
        assert not (analysis / name / "tracking_config.yaml").exists()
    assert changed(runs_before, snapshot(runs)) == set()


# ---------------------------------------------------------------------------
# Editing: the run's own config, never a copy
# ---------------------------------------------------------------------------


def test_the_config_editor_opened_on_a_member_opens_the_runs_config(store, monkeypatch):
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    app = QApplication.instance() or QApplication([])  # kept, or it is collected
    member = pointer(analysis_dir(store) / RUN1, RUN1)
    window = ConfigEditorWindow(initial_path=str(member))
    try:
        assert real(window._current_path) == real(
            run_dir(store, RUN1) / "tracking_config.yaml")
    finally:
        window.close()
        app.processEvents()

    warned = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warned.append(a)))
    broken = pointer(analysis_dir(store, name="A-20261105-test-09") / "M1",
                     "R-20990101-test-09")
    window = ConfigEditorWindow(initial_path=str(broken))
    try:
        assert warned and "does not exist" in warned[0][2]
        ## Nothing adopted, so a Save has nowhere to write a stray config.
        assert window._current_path is None
        assert not (broken / "tracking_config.yaml").exists()
    finally:
        window.close()
