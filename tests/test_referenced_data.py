"""A replicate that reads its recording from somewhere else (ADR-0018).

``data_folder:`` at the top of a replicate's ``tracking_config.yaml`` names the
folder that holds what ``data/`` holds today, resolved against the replicate
folder. The package only ever *reads* that folder: everything it writes for the
replicate stays in the replicate folder or the Project folder.

The shared tests (S1–S8) build the layout a study analysis has — Project and
replicates under ``studies/``, recordings under ``runs/`` — beneath a prefix with
spaces, an apostrophe, an ampersand and non-ASCII characters, and make ``runs/``
read-only while the whole pipeline runs over it. The package tests (T1–T6) pin
the classifier and the loader to one rule, filing and clock repair to "never
write at the data location", and the run manifest to "say where the data came
from".
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import yaml

from pytrackinganalysis import batch as batch_mod
from pytrackinganalysis import clock_repair, config_validation, layout
from pytrackinganalysis import project as prj
from pytrackinganalysis.Experiment import Experiment, _is_experiment_dir

from test_clock_repair import STEP_MS, write_export
from test_experiment_integration import write_project

#: Fixed by the shared contract, so spelled out here rather than imported.
KEY = "data_folder"
ASSAY = "dtrack"
RUN1 = "R-20261020-test-01"
RUN2 = "R-20261021-test-02"
ANALYSIS = "A-20261105-test-01"
ANALYSIS2 = "A-20261105-test-02"
#: The member folders' own names sort RUN1, RUN2, then this one — so the third
#: member is the one reported as the duplicate (contract item 7).
DUPLICATE = "Z-also-test-01"

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


# ---------------------------------------------------------------------------
# Building the layout
# ---------------------------------------------------------------------------

def managed_root(tmp) -> Path:
    return Path(tmp) / "Experiments & Data" / "MBa'b'ap Ünï" / "managed"


def run_dir(managed, run) -> Path:
    return Path(managed) / "runs" / ASSAY / "2026" / run


def reference(run) -> str:
    """The value labdata writes: relative, forward slashes, five levels up."""
    return f"../../../../../runs/{ASSAY}/2026/{run}/data"


def make_run(managed, run, regions) -> Path:
    """A run folder with its recording in ``data/`` and the config it was
    recorded with, the way a labdata run holds one."""
    return write_project(run_dir(managed, run), regions=regions)


def run_config(managed, run) -> dict:
    with open(run_dir(managed, run) / "tracking_config.yaml",
              encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_config(member: Path, config: dict) -> Path:
    member.mkdir(parents=True, exist_ok=True)
    path = member / "tracking_config.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False,
                                   allow_unicode=True), encoding="utf-8")
    return path


def referencing_config(config: dict, value) -> dict:
    """*config* with the key first, as a person would read it."""
    return {KEY: value, **copy.deepcopy(config)}


def make_analysis(managed, name, members, *, cutoffs=None) -> Path:
    """A Project under ``studies/`` whose members reference runs.

    *members* maps member folder name -> run it reads.
    """
    analysis = Path(managed) / "studies" / "test-study" / "analyses" / name
    analysis.mkdir(parents=True)
    prj.create_project_file(str(analysis))
    for member, run in members.items():
        config = run_config(managed, run)
        if cutoffs is not None:
            config["global"]["facet_cutoffs"] = list(cutoffs)
        write_config(analysis / member,
                     referencing_config(config, reference(run)))
    return analysis


def snapshot(root) -> dict:
    """Every path under *root* and, for files, its SHA-256."""
    root = Path(root)
    found = {}
    for directory, dirs, files in os.walk(root):
        rel = os.path.relpath(directory, root)
        found[rel + "/"] = None
        for name in files:
            path = Path(directory) / name
            found[os.path.join(rel, name)] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return found


def changed(before, after) -> set:
    return {path for path in set(before) | set(after)
            if before.get(path, "missing") != after.get(path, "missing")}


def lock(root) -> bool:
    """Make *root* read-only: files 0o444, folders 0o555. False as root,
    where permissions do not stop a write."""
    if IS_ROOT:
        return False
    for directory, dirs, files in os.walk(root):
        for name in files:
            os.chmod(os.path.join(directory, name), 0o444)
    for directory, dirs, files in os.walk(root, topdown=False):
        os.chmod(directory, 0o555)
    return True


def unlock(root) -> None:
    for directory, dirs, files in os.walk(root):
        os.chmod(directory, 0o755)
        for name in files:
            os.chmod(os.path.join(directory, name), 0o644)


def summary_tables(member) -> dict:
    """A replicate's per-member summary tables (analysis/ and qc/), with
    path/name/time columns out."""
    tables = {}
    member = Path(member)
    for path in sorted([*member.glob("analysis/*.csv"), *member.glob("qc/*.csv")]):
        frame = pd.read_csv(path)
        drop = [column for column in frame.columns
                if any(word in column.lower()
                       for word in ("path", "file", "time", "date", "experiment"))]
        tables[path.name] = frame.drop(columns=drop)
    return tables


# ---------------------------------------------------------------------------
# One full pipeline over a read-only runs/ tree (S2, S3, S4, S7, T6)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pipeline(tmp_path_factory):
    """Build the shared layout, hash and lock ``runs/``, then run everything
    that reads it: a Batch Run (each replicate's QC, analysis and report, then
    the pooled analysis and Project Report), layout classification, batch
    discovery and the preflight dialog."""
    tmp = tmp_path_factory.mktemp("referenced")
    managed = managed_root(tmp)
    make_run(managed, RUN1, regions=("T_1", "T_2"))
    make_run(managed, RUN2, regions=("T_1", "T_2", "T_3", "T_4"))
    analysis = make_analysis(managed, ANALYSIS, {RUN1: RUN1, RUN2: RUN2})
    batch_root = analysis.parent
    runs = managed / "runs"

    runs_before = snapshot(runs)
    tree_before = snapshot(tmp)
    locked = lock(runs)
    try:
        logs: list = []
        results = batch_mod.run_batch(str(batch_root), log=logs.append)
        layouts = layout.experiments_in(analysis)
        classified = {name: layout.classify(analysis / name)
                      for name in (RUN1, RUN2)}
        found = batch_mod.discover(batch_root)

        from PyQt6.QtWidgets import QApplication

        from pytrackinganalysis.apps.batch_preflight import BatchPreflightDialog

        app = QApplication.instance() or QApplication([])
        dialog = BatchPreflightDialog(None, str(batch_root))
        dialog.close()
        app.processEvents()

        yield SimpleNamespace(
            tmp=tmp, managed=managed, runs=runs, analysis=analysis,
            batch_root=batch_root, results=results, logs=logs,
            layouts=layouts, classified=classified, found=found,
            runs_before=runs_before, runs_after=snapshot(runs),
            tree_before=tree_before, tree_after=snapshot(tmp), locked=locked)
    finally:
        unlock(runs)


def test_s2_the_full_pipeline_runs_through_the_key(pipeline):
    assert pipeline.results == {ANALYSIS: "ok"}, pipeline.logs
    for name in (RUN1, RUN2):
        member = pipeline.analysis / name
        assert (member / "analysis" / "Synthetic_Summary.csv").is_file()
        assert (member / "analysis" / ".pytracking_run.json").is_file()
        assert list((member / "qc").glob("*_data_quality.csv"))
        assert (member / f"{name}_report.pdf").is_file()
    assert (pipeline.analysis / "analysis" / f"{ANALYSIS}_Summary.csv").is_file()
    assert (pipeline.analysis / f"{ANALYSIS}_report.pdf").is_file()


def test_s2_every_output_is_under_a_member_or_the_project(pipeline):
    prefix = os.path.relpath(pipeline.analysis, pipeline.tmp) + os.sep
    written = changed(pipeline.tree_before, pipeline.tree_after)
    assert written, "the pipeline wrote nothing at all"
    assert sorted(p for p in written if not p.startswith(prefix)) == []


def test_s2_the_classifier_and_discovery_see_two_healthy_members(pipeline):
    assert [item.name for item in pipeline.layouts] == [RUN1, RUN2]
    assert all(item.usable for item in pipeline.layouts)
    assert all(item.usable for item in pipeline.classified.values())
    [member] = pipeline.found["members"]
    assert member.key == ANALYSIS
    assert len(member.usable) == 2 and not member.blocked


def test_s3_the_recordings_are_untouched(pipeline):
    assert pipeline.runs_after == pipeline.runs_before


@pytest.mark.skipif(IS_ROOT, reason="running as root: file permissions do "
                    "not stop a write, so read-only proves nothing here")
def test_s3_the_recordings_were_read_only_throughout(pipeline):
    assert pipeline.locked
    for directory, dirs, files in os.walk(pipeline.runs):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o555
        for name in files:
            assert stat.S_IMODE(os.stat(os.path.join(directory, name)).st_mode) == 0o444


def test_s4_a_referencing_member_gets_the_same_numbers_as_a_copy(pipeline, tmp_path):
    ordinary = tmp_path / "ordinary" / RUN1
    shutil.copytree(run_dir(pipeline.managed, RUN1) / "data", ordinary / "data")
    write_config(ordinary, run_config(pipeline.managed, RUN1))
    Experiment(str(ordinary)).run_analysis()

    referencing = summary_tables(pipeline.analysis / RUN1)
    copied = summary_tables(ordinary)
    assert set(referencing) == set(copied)
    assert {"Synthetic_Summary.csv", "Synthetic_Summary_Facet.csv",
            "Synthetic_data_quality.csv"} <= set(referencing)
    assert "TotalDistancePerMin" in referencing["Synthetic_Summary.csv"]
    for name, frame in referencing.items():
        pd.testing.assert_frame_equal(frame, copied[name], obj=name)


def test_t6_the_run_manifest_says_where_the_data_came_from(pipeline):
    for name in (RUN1, RUN2):
        manifest = json.loads(
            (pipeline.analysis / name / "analysis" / ".pytracking_run.json")
            .read_text(encoding="utf-8"))
        assert manifest["data_location"] == {
            "written": reference(name),
            "resolved": os.path.realpath(run_dir(pipeline.managed, name) / "data"),
        }


def test_s7_two_projects_can_read_one_recording(pipeline):
    """A second analysis with a different setting writes only its own output;
    the first analysis folder is byte-identical afterwards."""
    first_before = snapshot(pipeline.analysis)
    second = make_analysis(pipeline.managed, ANALYSIS2,
                           {RUN1: RUN1, RUN2: RUN2}, cutoffs=(3, 6))

    results = batch_mod.run_batch(str(pipeline.batch_root),
                                  project_names=[ANALYSIS2],
                                  log=lambda _m: None)

    assert results == {ANALYSIS2: "ok"}
    assert snapshot(pipeline.analysis) == first_before
    assert snapshot(pipeline.runs) == pipeline.runs_before
    assert (second / f"{ANALYSIS2}_report.pdf").is_file()
    ours = pd.read_csv(second / RUN1 / "analysis" / "Synthetic_Summary_Facet.csv")
    theirs = pd.read_csv(pipeline.analysis / RUN1 / "analysis"
                         / "Synthetic_Summary_Facet.csv")
    assert set(ours["FacetRange"]) != set(theirs["FacetRange"])


# ---------------------------------------------------------------------------
# S5: one recording, one member
# ---------------------------------------------------------------------------

@pytest.fixture
def three_members(tmp_path):
    managed = managed_root(tmp_path)
    make_run(managed, RUN1, regions=("T_1", "T_2"))
    make_run(managed, RUN2, regions=("T_1", "T_2"))
    analysis = make_analysis(managed, ANALYSIS,
                             {RUN1: RUN1, RUN2: RUN2, DUPLICATE: RUN1})
    return SimpleNamespace(managed=managed, analysis=analysis)


def test_s5_the_project_keeps_the_first_and_names_the_duplicate(three_members):
    project = prj.Project(str(three_members.analysis))
    assert project.experiment_names == [RUN1, RUN2]
    assert project.duplicates == {DUPLICATE: RUN1}
    [warning] = [w for w in project.warnings if DUPLICATE in w]
    assert "duplicate" in warning and RUN1 in warning


def test_s5_layout_and_discovery_report_the_duplicate_as_blocked(three_members):
    items = {item.name: item for item in layout.experiments_in(three_members.analysis)}
    assert items[RUN1].usable and items[RUN2].usable
    assert items[DUPLICATE].status == layout.DUPLICATE
    assert items[DUPLICATE].blocked and items[DUPLICATE].fix is None
    assert RUN1 in items[DUPLICATE].detail
    assert os.path.realpath(run_dir(three_members.managed, RUN1) / "data") \
        in items[DUPLICATE].detail

    [member] = batch_mod.discover_members(three_members.analysis.parent)
    assert [e.name for e in member.usable] == [RUN1, RUN2]
    assert [e.name for e in member.blocked] == [DUPLICATE]


def test_s5_a_batch_run_names_the_duplicate(three_members, monkeypatch):
    from pytrackinganalysis.script_editor import project_actions as pa

    monkeypatch.setattr(pa, "run_project_script",
                        lambda script, project, log_cb, figure_cb=None: None)
    logs: list = []
    batch_mod.run_batch(str(three_members.analysis.parent), log=logs.append)
    assert any(DUPLICATE in line and "duplicate" in line for line in logs)


def test_s5_the_duplicates_units_appear_once_in_project_output(three_members):
    from test_project import _summary_frame

    for seed, name in enumerate((RUN1, RUN2, DUPLICATE)):
        (three_members.analysis / name / "analysis").mkdir()
        _summary_frame(seed=seed).to_csv(
            three_members.analysis / name / "analysis" / "Rec_Summary.csv",
            index=False)
    project = prj.Project(str(three_members.analysis))
    project.build_combined_analysis()

    pooled = pd.read_csv(three_members.analysis / "analysis"
                         / f"{ANALYSIS}_Summary.csv")
    assert set(pooled["Experiment"]) == {RUN1, RUN2}
    assert (pooled["Experiment"] == RUN1).sum() == len(_summary_frame(seed=0))


def test_s5_whichever_member_sorts_first_is_the_one_kept(three_members):
    first = "A-reads-test-01-first"
    shutil.copytree(three_members.analysis / DUPLICATE,
                    three_members.analysis / first)
    project = prj.Project(str(three_members.analysis))
    assert project.experiment_names == [first, RUN2]
    assert project.duplicates == {RUN1: first, DUPLICATE: first}


def test_without_the_key_symlinked_data_folders_keep_todays_behaviour(tmp_path):
    """Contract item 3: no key, no change — two plain replicates whose data/
    happen to be one folder are not compared, exactly as before."""
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks here")
    write_project(tmp_path / "P" / "A")
    (tmp_path / "P" / "B").mkdir()
    shutil.copy(tmp_path / "P" / "A" / "tracking_config.yaml", tmp_path / "P" / "B")
    os.symlink(tmp_path / "P" / "A" / "data", tmp_path / "P" / "B" / "data")
    prj.create_project_file(str(tmp_path / "P"))

    assert prj.Project(str(tmp_path / "P")).experiment_names == ["A", "B"]
    assert [i.status for i in layout.experiments_in(tmp_path / "P")] \
        == [layout.OK, layout.OK]


# ---------------------------------------------------------------------------
# S6 / T1: the classifier and the loader agree, case by case
# ---------------------------------------------------------------------------

def referencing_member(tmp_path, value="../../runs/R1/data", name="M1") -> Path:
    """``<tmp>/study/<name>`` whose key is *value* — by default a reference to
    ``<tmp>/runs/R1/data``."""
    run = tmp_path / "runs" / "R1"
    if not run.exists():
        write_project(run)
    with open(run / "tracking_config.yaml", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    member = tmp_path / "study" / name
    write_config(member, referencing_config(config, value))
    return member


def outcome(member):
    """``(classifier says usable, loader succeeded, loader error)``."""
    item = layout.classify(member)
    try:
        Experiment(str(member))
    except layout.DataLocationError as err:
        return item, False, str(err)
    return item, True, ""


def test_t1_a_good_folder_is_healthy_and_loads_from_there(tmp_path):
    member = referencing_member(tmp_path)
    item, loaded, _ = outcome(member)
    assert item.status == layout.OK and loaded
    exp = Experiment(str(member))
    assert exp.data_path == os.path.realpath(tmp_path / "runs" / "R1" / "data") + "/"
    assert not (member / "data").exists()


def test_t1_a_missing_folder_blocks_and_the_loader_names_it(tmp_path):
    member = referencing_member(tmp_path, "../../runs/NOPE/data")
    before = snapshot(member)
    item, loaded, error = outcome(member)
    resolved = os.path.realpath(tmp_path / "runs" / "NOPE" / "data")

    assert item.blocked and not loaded
    assert item.status == layout.NO_RECORDING
    assert resolved in item.detail
    for part in (KEY, "../../runs/NOPE/data", resolved):
        assert part in error
    assert snapshot(member) == before, "a refused load wrote into the member"


def test_t1_a_file_where_a_folder_belongs_blocks_and_the_loader_names_it(tmp_path):
    value = "../../runs/R1/data/Synthetic.xlsx"
    member = referencing_member(tmp_path, value)
    item, loaded, error = outcome(member)
    resolved = os.path.realpath(tmp_path / "runs" / "R1" / "data" / "Synthetic.xlsx")

    assert item.blocked and not loaded
    assert resolved in item.detail and "not a folder" in item.detail
    for part in (KEY, value, resolved):
        assert part in error


def test_t1_the_key_and_a_local_recording_conflict(tmp_path):
    member = referencing_member(tmp_path)
    (member / "data").mkdir()
    (member / "data" / "Other.xlsx").write_bytes(b"x")
    item, loaded, error = outcome(member)
    resolved = os.path.realpath(tmp_path / "runs" / "R1" / "data")

    assert item.status == layout.AMBIGUOUS and not loaded
    for text in (item.detail, error):
        assert "Other.xlsx" in text and resolved in text


def test_t1_an_empty_local_data_folder_is_not_a_conflict(tmp_path):
    member = referencing_member(tmp_path)
    (member / "data").mkdir()
    item, loaded, _ = outcome(member)
    assert item.status == layout.OK and loaded


def test_t1_a_loose_workbook_beside_the_key_is_a_conflict_not_unfiled(tmp_path):
    member = referencing_member(tmp_path)
    (member / "Loose.xlsx").write_bytes(b"x")
    item, loaded, error = outcome(member)
    assert item.status == layout.AMBIGUOUS and not loaded
    assert "Loose.xlsx" in item.detail and "Loose.xlsx" in error


def test_a_csv_at_the_root_of_a_referencing_member_is_not_a_recording(tmp_path):
    """At the root only what the ordinary classifier counts as a recording —
    a loadable workbook — competes with the key. A units table beside the
    config is just a file."""
    member = referencing_member(tmp_path)
    (member / "units.csv").write_text("unit,genotype\nT_1,CS\n", encoding="utf-8")
    item, loaded, error = outcome(member)
    assert item.status == layout.OK and loaded, error


def test_a_csv_in_the_local_data_folder_still_conflicts(tmp_path):
    """In data/ anything that looks like part of an export counts: a stray
    ``_Data_1.csv`` there is a piece of a second recording."""
    member = referencing_member(tmp_path)
    (member / "data").mkdir()
    (member / "data" / "Other_Data_1.csv").write_bytes(b"x")
    item, loaded, error = outcome(member)
    assert item.status == layout.AMBIGUOUS and not loaded
    assert "Other_Data_1.csv" in item.detail and "Other_Data_1.csv" in error


def test_the_key_may_not_name_the_member_folder_itself(tmp_path):
    """Everything the package writes goes into the member folder, so a data
    location that holds it would be written into."""
    member = referencing_member(tmp_path, ".")
    item, loaded, error = outcome(member)
    assert item.blocked and not loaded
    assert os.path.realpath(member) in error


def test_an_absolute_value_works(tmp_path):
    target = tmp_path / "runs" / "R1" / "data"
    referencing_member(tmp_path)                    # builds the run
    member = referencing_member(tmp_path, str(target), name="M2")
    item, loaded, _ = outcome(member)
    assert item.usable and loaded


def test_the_key_is_spelled_as_the_contract_says():
    assert layout.DATA_FOLDER_KEY == KEY


def test_a_blank_value_is_no_key_at_all(tmp_path):
    for value in ("", None):
        member = referencing_member(tmp_path, value, name=f"M-{value!r}")
        location = layout.data_location(member)
        assert not location.referenced
        assert location.path == layout.data_dir(member)


def test_without_the_key_the_resolver_is_todays_data_folder(tmp_path):
    member = write_project(tmp_path / "plain")
    location = layout.data_location(member)
    assert not location.referenced and location.problem == ""
    assert location.path == layout.data_dir(member)
    exp = Experiment(str(member))
    assert exp.data_path == str(member / "data") + "/"
    assert "data_folder" not in str(exp)


def test_the_loader_says_where_the_data_came_from(tmp_path):
    member = referencing_member(tmp_path)
    text = str(Experiment(str(member)))
    assert "../../runs/R1/data" in text
    assert os.path.realpath(tmp_path / "runs" / "R1" / "data") in text


def test_batch_analyze_reports_a_broken_reference_instead_of_skipping_it(tmp_path):
    from pytrackinganalysis.Experiment import batch_analyze

    member = referencing_member(tmp_path, "../../runs/NOPE/data")
    assert _is_experiment_dir(str(member))
    results = batch_analyze(str(member.parent))
    assert KEY in results[str(member)]


# ---------------------------------------------------------------------------
# T2: discovery helpers read through the key
# ---------------------------------------------------------------------------

def test_t2_region_and_data_discovery_read_the_referenced_folder(tmp_path):
    member = referencing_member(tmp_path)
    assert prj.tracking_regions_in_data(member) == ["T_1", "T_2"]
    assert prj.has_experiment_data(member) is True

    broken = referencing_member(tmp_path, "../../runs/NOPE/data", name="M2")
    assert prj.tracking_regions_in_data(broken) == []
    assert prj.has_experiment_data(broken) is False


# ---------------------------------------------------------------------------
# T3: never unfiled, never filed
# ---------------------------------------------------------------------------

def test_t3_a_referenced_folder_holding_a_loose_workbook_is_not_unfiled(tmp_path):
    """The key names the run folder itself, where the export still sits
    loose: that folder IS the data folder, so its workbook is filed."""
    run = tmp_path / "runs" / "R1"
    write_project(run)
    for path in (run / "data").iterdir():
        shutil.move(str(path), run / path.name)
    (run / "data").rmdir()
    member = referencing_member(tmp_path, "../../runs/R1")
    before = snapshot(tmp_path)

    item = layout.classify(member)
    assert item.status == layout.OK
    plan = layout.plan_filing(member)
    assert not plan.possible and KEY in plan.refused
    layout.file_recording(member)
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("arrange", ["loose at the member root",
                                     "in the run folder's own data/"])
def test_t3_filing_a_referencing_member_is_refused_and_moves_nothing(tmp_path, arrange):
    if arrange == "loose at the member root":
        member = referencing_member(tmp_path)
        (member / "Loose.xlsx").write_bytes(b"x")
        (member / "Loose_Data_1.csv").write_bytes(b"x")
    else:
        member = referencing_member(tmp_path, "../../runs/R1")
    before = snapshot(tmp_path)

    item = layout.classify(member)
    assert item.status != layout.UNFILED and item.fix != "file"
    plan = layout.plan_filing(member)
    assert not plan.possible and KEY in plan.refused
    result = layout.file_recording(member)
    assert result.moves == []
    assert snapshot(tmp_path) == before


def test_t3_a_folder_one_level_too_high_says_where_the_workbook_is(tmp_path):
    member = referencing_member(tmp_path, "../../runs/R1")
    item = layout.classify(member)
    assert item.status == layout.NO_RECORDING
    assert "data/" in item.detail


# ---------------------------------------------------------------------------
# T4: writes that already go to the member folder stay there
# ---------------------------------------------------------------------------

def test_t4_hypotheses_and_a_removal_sheet_write_only_into_members(tmp_path):
    managed = managed_root(tmp_path)
    make_run(managed, RUN1, regions=("T_1", "T_2"))
    make_run(managed, RUN2, regions=("T_1", "T_2"))
    analysis = make_analysis(managed, ANALYSIS, {RUN1: RUN1, RUN2: RUN2})
    (analysis / "removed_regions.csv").write_text(
        "experiment,region,reason\n"
        f"{RUN1},T_1,dead at ~20 min\n{RUN2},T_2,escaped\n", encoding="utf-8")
    runs = managed / "runs"
    before = snapshot(tmp_path)
    lock(runs)
    try:
        changed_configs = prj.write_replicate_hypotheses(
            str(analysis), ["Treated < Ctrl"])
        result = prj.Project(str(analysis)).apply_removal_sheet(log=lambda _m: None)
        exp = Experiment(str(analysis / RUN2))
        exp.write_removed_regions({"T_1": "flooded"})
    finally:
        unlock(runs)

    assert len(changed_configs) == 2 and result["written"]
    members = tuple(os.path.relpath(analysis / name, tmp_path) + os.sep
                    for name in (RUN1, RUN2))
    written = changed(before, snapshot(tmp_path))
    assert written
    assert sorted(p for p in written if not p.startswith(members)) == []
    for name in (RUN1, RUN2):
        config = yaml.safe_load((analysis / name / "tracking_config.yaml")
                                .read_text(encoding="utf-8"))
        assert config[KEY] == reference(name)        # survives the rewrite


def test_a_new_replicate_does_not_inherit_the_key(tmp_path):
    """Contract item 1: the key belongs to one member's config alone."""
    managed = managed_root(tmp_path)
    make_run(managed, RUN1, regions=("T_1", "T_2"))
    analysis = make_analysis(managed, ANALYSIS, {RUN1: RUN1})
    project = prj.Project(str(analysis))

    assert KEY not in project.scaffold_replicate_config()
    path = project.scaffold_replicate("Rep2")
    assert KEY not in yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert not (analysis / RUN1 / "data").exists()


def test_arena_csv_exports_never_write_into_a_referenced_folder(tmp_path):
    member = referencing_member(tmp_path)
    runs_before = snapshot(tmp_path / "runs")
    exp = Experiment(str(member))
    with pytest.raises(PermissionError, match=KEY):
        exp.arena.summarize(write_to_csvfile=True)
    assert snapshot(tmp_path / "runs") == runs_before


# ---------------------------------------------------------------------------
# T5: clock repair never rewrites referenced data
# ---------------------------------------------------------------------------

@pytest.fixture
def rolled_back(tmp_path):
    """A rolled-back recording under a read-only ``runs/``, and a member
    referencing it."""
    run = tmp_path / "runs" / "R1"
    write_export(run, n_frames=50, rollbacks={30: clock_repair.HOUR_MS},
                 frames_per_file=20)
    pd.DataFrame({"Name": ["T_0", "T_1"], "Type": ["Tracking"] * 2,
                  "Shape": ["Rectangle"] * 2, "Width": [100] * 2,
                  "Height": [100] * 2}).to_excel(
        run / "data" / "Synthetic.xlsx", sheet_name="ROI", index=False)
    config = {"global": {"tracking_type": "TRACKER",
                         "tracking_rig": "small_arena"},
              "tracking_regions": {"T_0": {"experimental_factors": "Ctrl"},
                                   "T_1": {"experimental_factors": "Treated"}}}
    member = tmp_path / "study" / "M1"
    write_config(member, referencing_config(config, "../../runs/R1/data"))
    lock(tmp_path / "runs")
    yield SimpleNamespace(tmp=tmp_path, run=run, member=member, config=config)
    unlock(tmp_path / "runs")


def test_t5_the_loader_still_refuses_the_rolled_back_original(rolled_back):
    with pytest.raises(clock_repair.ClockRollbackError, match="--out"):
        Experiment(str(rolled_back.member))


def test_t5_in_place_repair_of_a_referencing_member_is_refused(rolled_back):
    before = snapshot(rolled_back.tmp)
    with pytest.raises(ValueError, match="--out") as excinfo:
        clock_repair.repair_export(rolled_back.member, log=None)
    assert KEY in str(excinfo.value)
    assert snapshot(rolled_back.tmp) == before


def test_t5_out_of_place_repair_writes_a_complete_copy(rolled_back):
    out = rolled_back.tmp / "derived" / "clock_repaired"
    runs_before = snapshot(rolled_back.tmp / "runs")

    report = clock_repair.repair_export(rolled_back.member, out_dir=out, log=None)

    assert snapshot(rolled_back.tmp / "runs") == runs_before
    assert report.out_dir == out and report.backup_dir is None
    originals = rolled_back.run / "data"
    names = sorted(p.name for p in originals.iterdir())
    assert sorted(p.name for p in out.iterdir()) == sorted(names + ["README.txt"])
    assert (out / "Synthetic.xlsx").read_bytes() \
        == (originals / "Synthetic.xlsx").read_bytes()
    # The first file is entirely before the rollback: byte for byte.
    assert (out / "Synthetic_Data_1.csv").read_bytes() \
        == (originals / "Synthetic_Data_1.csv").read_bytes()
    repaired = pd.concat([pd.read_csv(p) for p in sorted(out.glob("*_Data_*.csv"))])
    rows = repaired.query("TrackingRegion == 'T_0'").sort_values("Frame")
    assert np.allclose(np.diff(rows["MSec"]), STEP_MS)
    readme = (out / "README.txt").read_text(encoding="utf-8")
    assert "clock went back" in readme and str(originals) in readme
    assert not list(rolled_back.tmp.rglob(clock_repair.DEFAULT_BACKUP_DIRNAME))


def test_t5_a_member_pointed_at_the_repaired_copy_loads(rolled_back):
    out = rolled_back.tmp / "derived" / "clock_repaired"
    clock_repair.repair_export(rolled_back.member, out_dir=out, log=None)
    write_config(rolled_back.member,
                 referencing_config(rolled_back.config,
                                    "../../derived/clock_repaired"))
    exp = Experiment(str(rolled_back.member))
    minutes = exp.arena.trackers["T_0_0"].rawdata["Minutes"]
    assert minutes.is_monotonic_increasing


def test_t5_out_of_place_refuses_a_folder_that_is_not_empty(rolled_back):
    out = rolled_back.tmp / "derived"
    out.mkdir()
    (out / "keep.txt").write_text("mine", encoding="utf-8")
    before = snapshot(rolled_back.tmp)
    with pytest.raises(FileExistsError, match="not empty"):
        clock_repair.repair_export(rolled_back.member, out_dir=out, log=None)
    assert snapshot(rolled_back.tmp) == before


def test_t5_out_of_place_never_writes_inside_the_data_folder(tmp_path):
    files = write_export(tmp_path, n_frames=50, rollbacks={30: clock_repair.HOUR_MS},
                         frames_per_file=20)
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="inside"):
        clock_repair.repair_export(tmp_path, out_dir=tmp_path / "data" / "fixed",
                                   log=None)
    assert snapshot(tmp_path) == before
    assert files


def test_t5_out_of_place_works_for_an_ordinary_member_too(tmp_path):
    files = write_export(tmp_path / "Rep1", n_frames=50,
                         rollbacks={30: clock_repair.HOUR_MS}, frames_per_file=20)
    originals = {f.name: f.read_bytes() for f in files}
    report = clock_repair.repair_export(tmp_path / "Rep1",
                                       out_dir=tmp_path / "fixed", log=None)
    assert {f.name: f.read_bytes() for f in files} == originals
    assert report.rows_shifted == 40
    assert not (tmp_path / "Rep1" / clock_repair.DEFAULT_BACKUP_DIRNAME).exists()


def test_t5_a_dry_run_on_a_referencing_member_is_allowed(rolled_back):
    before = snapshot(rolled_back.tmp)
    report = clock_repair.repair_export(rolled_back.member, dry_run=True, log=None)
    assert [r.frame for r in report.rollbacks] == [30]
    assert snapshot(rolled_back.tmp) == before


def test_t5_the_command_line_has_an_out_option(rolled_back, capsys):
    out = rolled_back.tmp / "derived" / "cli"
    assert clock_repair.main([str(rolled_back.member)]) == 1
    assert "--out" in capsys.readouterr().err
    assert clock_repair.main([str(rolled_back.member), "--out", str(out)]) == 0
    assert (out / "README.txt").is_file()


def test_t5_the_backup_and_out_options_do_not_mix(tmp_path, capsys):
    write_export(tmp_path, n_frames=50, rollbacks={30: clock_repair.HOUR_MS},
                 frames_per_file=20)
    with pytest.raises(SystemExit):
        clock_repair.main([str(tmp_path), "--out", str(tmp_path / "a"),
                           "--backup-dir", str(tmp_path / "b")])
    assert not (tmp_path / "a").exists() and not (tmp_path / "b").exists()


# ---------------------------------------------------------------------------
# S8: the linter
# ---------------------------------------------------------------------------

def _valid_config(**top):
    return {**top, "global": {"tracking_type": "TRACKER",
                              "tracking_rig": "small_arena"},
            "tracking_regions": {"T_1": {}}}


def test_s8_the_key_is_accepted():
    config = _valid_config(**{KEY: "../../runs/R1/data"})
    assert config_validation.validate_config(config) == []
    assert config_validation.config_warnings(config) == []


def test_s8_an_absolute_value_warns_that_it_is_not_portable(tmp_path):
    config = _valid_config(**{KEY: (tmp_path / "runs" / "R1" / "data").as_posix()})
    assert config_validation.validate_config(config) == []
    [warning] = config_validation.config_warnings(config)
    assert KEY in warning and "portable" in warning


def test_s8_a_backslash_warns_to_write_forward_slashes():
    config = _valid_config(**{KEY: "..\\..\\runs\\R1\\data"})
    [warning] = config_validation.config_warnings(config)
    assert "forward slash" in warning


def test_s8_a_value_that_is_not_a_path_is_a_problem():
    problems = config_validation.validate_config(_valid_config(**{KEY: 5}))
    assert any(KEY in p for p in problems)


def test_s8_no_key_no_warnings():
    assert config_validation.config_warnings(_valid_config()) == []


def test_s8_the_file_validator_reports_the_warning_beside_the_problems(tmp_path):
    path = write_config(tmp_path / "M1",
                        _valid_config(**{KEY: (tmp_path / "elsewhere").as_posix()}))
    assert config_validation.validate_config_file(str(path)) == []
    [warning] = config_validation.config_file_warnings(str(path))
    assert "portable" in warning


# ---------------------------------------------------------------------------
# The Hub
# ---------------------------------------------------------------------------

def test_copying_a_referencing_config_into_a_new_replicate_leaves_the_key(
        tmp_path, monkeypatch):
    """Copy config from… takes the design, never another experiment's data
    location: the new replicate reads its own data/."""
    from PyQt6.QtWidgets import QApplication

    from test_project import _create_then_copy, _exp_config, _make_project

    app = QApplication.instance() or QApplication([])
    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    elsewhere = tmp_path / "elsewhere"
    write_config(elsewhere, referencing_config(_exp_config(), "../runs/R1/data"))

    win, warned, _launched, _told = _create_then_copy(
        app, tmp_path, monkeypatch, str(elsewhere / "tracking_config.yaml"))
    try:
        copied = yaml.safe_load((tmp_path / "Rep2" / "tracking_config.yaml")
                                .read_text(encoding="utf-8"))
        assert KEY not in copied
        assert copied["global"] == _exp_config()["global"]
        assert not warned
        assert "Rep2" in prj.Project(str(tmp_path)).experiment_names
    finally:
        win.close()


def test_the_experiment_configs_dialog_counts_files_where_the_loader_reads(
        tmp_path):
    from PyQt6.QtWidgets import QApplication

    from pytrackinganalysis.apps import hub as hub_mod

    app = QApplication.instance() or QApplication([])
    managed = managed_root(tmp_path)
    make_run(managed, RUN1, regions=("T_1", "T_2"))
    analysis = make_analysis(managed, ANALYSIS, {RUN1: RUN1})

    win = hub_mod.HubWindow(initial_project=str(analysis))
    app.processEvents()
    try:
        dlg = hub_mod.ExperimentConfigsDialog(win, win._current_project())
        assert dlg._table.item(0, 0).text() == RUN1
        assert dlg._table.item(0, 2).text() == "2 file(s)"
        dlg.close()
    finally:
        win.close()


# ---------------------------------------------------------------------------
# The Config Editor keeps the key and explains it
# ---------------------------------------------------------------------------

def test_the_config_editor_round_trips_and_explains_the_key(tmp_path):
    from PyQt6.QtWidgets import QApplication

    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    QApplication.instance() or QApplication([])
    member = referencing_member(tmp_path)
    window = ConfigEditorWindow(str(member / "tracking_config.yaml"))
    try:
        field = window._global_tab.data_folder
        assert field.text() == "../../runs/R1/data"
        assert KEY in field.toolTip() and "relative" in field.toolTip()
        assert window._dump_config()[KEY] == "../../runs/R1/data"
        ## The plate offered "from data" comes from the referenced recording.
        assert window._tracking_tab.data_region_names() == ["T_1", "T_2"]

        field.setText("")
        assert KEY not in window._dump_config()
        field.setText("../../runs/R2/data")
        dumped = window._dump_config()
        assert dumped[KEY] == "../../runs/R2/data"
        assert KEY not in dumped["global"]
    finally:
        window.close()


def test_the_config_editor_adds_no_key_to_a_plain_config(tmp_path):
    from PyQt6.QtWidgets import QApplication

    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    QApplication.instance() or QApplication([])
    member = write_project(tmp_path / "plain")
    window = ConfigEditorWindow(str(member / "tracking_config.yaml"))
    try:
        assert window._global_tab.data_folder.text() == ""
        assert KEY not in window._dump_config()
    finally:
        window.close()
