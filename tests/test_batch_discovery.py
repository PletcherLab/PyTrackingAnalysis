"""Recursive Batch discovery, Blocked Experiments, and filing (ADR-0011).

The folder structures here are the ones that actually turn up in a lab share:
projects two and three levels down, a stray ``project.yaml`` at a grouping
level, recordings still sitting loose where DTrack wrote them, symlinked
archives, and folders nobody can read.
"""

from __future__ import annotations

import os

import pytest

from pytrackinganalysis import batch as batch_mod
from pytrackinganalysis import layout
from pytrackinganalysis import project as prj


def make_project(directory, replicates=("Rep1",), configs=True, data=True):
    """A Project on disk: ``project.yaml`` plus replicate directories."""
    directory.mkdir(parents=True, exist_ok=True)
    prj.create_project_file(str(directory), name=directory.name)
    for name in replicates:
        experiment = directory / name
        (experiment / "data").mkdir(parents=True, exist_ok=True)
        if data:
            (experiment / "data" / f"{name}.xlsx").write_bytes(b"x")
        if configs:
            (experiment / "tracking_config.yaml").write_text("global: {}\n",
                                                             encoding="utf-8")
    return directory


def unfiled(directory, stem="Trial7", config=True):
    """An Unfiled Recording: the export still at the experiment root."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{stem}.xlsx").write_bytes(b"x")
    (directory / f"{stem}_Data_1.csv").write_bytes(b"x")
    if config:
        (directory / "tracking_config.yaml").write_text("global: {}\n",
                                                        encoding="utf-8")
    return directory


def keys(root):
    return [member.key for member in batch_mod.discover_members(root)]


# ---- the walk --------------------------------------------------------------

def test_projects_are_found_at_any_depth(tmp_path):
    """The reason for the whole feature: nobody keeps Projects in one flat
    folder, and splitting a tree to satisfy the tool is the wrong direction."""
    make_project(tmp_path / "ProjTop")
    make_project(tmp_path / "Sept2026" / "ProjA")
    make_project(tmp_path / "Archive" / "2025" / "ProjC")

    assert keys(tmp_path) == ["Archive/2025/ProjC", "ProjTop", "Sept2026/ProjA"]
    # A top-level Project keeps its bare name, so every batch.yaml, sheet row,
    # and API call written before ADR-0011 still resolves.
    assert "ProjTop" in keys(tmp_path)


def test_the_walk_prunes_at_a_project(tmp_path):
    """An archived copy inside a Project must not become a second Member —
    its experiments would be analyzed twice in one run."""
    project = make_project(tmp_path / "ProjA", replicates=("Rep1", "Rep2"))
    make_project(project / "old_copy", replicates=("Rep1",))

    assert keys(tmp_path) == ["ProjA"]


def test_a_stray_marker_never_hides_the_projects_beneath_it(tmp_path):
    """A project.yaml at a grouping level is the mistake recursion exists to
    tolerate — not a reason to lose every Project under it."""
    make_project(tmp_path / "Sept2026" / "ProjA")
    make_project(tmp_path / "Sept2026" / "ProjB")
    prj.create_project_file(str(tmp_path / "Sept2026"), name="stray")

    assert keys(tmp_path) == ["Sept2026/ProjA", "Sept2026/ProjB"]
    found = batch_mod.discover(tmp_path)
    assert any(key == "Sept2026" for key, _why in found["skipped"])


def test_a_marker_over_junk_still_yields_the_projects_below(tmp_path):
    """The nastier version: the grouping folder has a project.yaml AND a junk
    subdirectory holding a stray workbook, so it looks exactly like a Project
    whose configs were never scaffolded."""
    make_project(tmp_path / "Sept2026" / "ProjA")
    unfiled(tmp_path / "Sept2026" / "loose_junk", config=False)
    prj.create_project_file(str(tmp_path / "Sept2026"), name="stray")

    assert keys(tmp_path) == ["Sept2026/ProjA"]


def test_a_project_whose_configs_were_never_scaffolded_is_a_blocked_member(
        tmp_path):
    """With nothing below it, the same shape IS the Project — listed, blocked,
    and unchecked by default, because it can only produce a failure."""
    make_project(tmp_path / "ProjA")
    make_project(tmp_path / "Pending", replicates=("R1", "R2"), configs=False)

    members = {m.key: m for m in batch_mod.discover_members(tmp_path)}
    assert set(members) == {"ProjA", "Pending"}
    pending = members["Pending"]
    assert not pending.runnable
    assert [e.status for e in pending.blocked] == [layout.NO_CONFIG] * 2
    assert members["ProjA"].runnable


def test_a_project_is_never_also_a_batch(tmp_path):
    project = make_project(tmp_path / "ProjA", replicates=("Rep1",))
    assert not batch_mod.is_batch_dir(project)
    assert batch_mod.discover(project)["members"] == []


def test_symlinked_directories_are_skipped_and_reported(tmp_path):
    """A link to an ancestor is a cycle; a link into an archive share would
    double-run its experiments. Either way, silence is the wrong answer."""
    make_project(tmp_path / "ProjA")
    os.symlink(tmp_path, tmp_path / "loop")
    os.symlink(tmp_path / "ProjA", tmp_path / "shortcut")

    found = batch_mod.discover(tmp_path)
    assert [m.key for m in found["members"]] == ["ProjA"]
    assert {key for key, _why in found["skipped"]} == {"loop", "shortcut"}
    assert all("symlink" in why for _key, why in found["skipped"])


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_an_unreadable_directory_is_reported_not_swallowed(tmp_path):
    """A permission-denied folder may hold a whole Project; dropping it
    silently reports the batch as smaller than it is."""
    make_project(tmp_path / "ProjA")
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    try:
        found = batch_mod.discover(tmp_path)
        assert [m.key for m in found["members"]] == ["ProjA"]
        assert any(key == "locked" and "listed" in why
                   for key, why in found["skipped"])
    finally:
        os.chmod(locked, 0o755)


def test_a_truncated_walk_says_so(tmp_path, monkeypatch):
    """A partial scan reported as a complete one is how an unattended run
    silently skips half a batch."""
    make_project(tmp_path / "a" / "b" / "ProjA")
    monkeypatch.setattr(batch_mod, "MAX_WALKED_DIRECTORIES", 1)
    found = batch_mod.discover(tmp_path)
    assert found["truncated"]


def test_member_keys_survive_the_round_trip(tmp_path):
    make_project(tmp_path / "Sept2026" / "ProjA")
    key = keys(tmp_path)[0]
    assert key == "Sept2026/ProjA"
    assert os.path.isdir(batch_mod.member_directory(tmp_path, key))
    assert batch_mod.normalize_member_key("./Sept2026\\ProjA/") == key


# ---- experiment layout -----------------------------------------------------

def test_classify_names_each_blocked_state(tmp_path):
    healthy = make_project(tmp_path / "P") / "Rep1"
    assert layout.classify(healthy).status == layout.OK

    loose = unfiled(tmp_path / "P" / "Rep2")
    item = layout.classify(loose)
    assert item.status == layout.UNFILED and item.fix == "file"

    no_config = unfiled(tmp_path / "P" / "Rep3", config=False)
    (no_config / "data").mkdir()
    (no_config / "data" / "Rep3.xlsx").write_bytes(b"x")
    (no_config / "Trial7.xlsx").unlink()
    (no_config / "Trial7_Data_1.csv").unlink()
    assert layout.classify(no_config).fix == "config"

    empty = tmp_path / "P" / "Rep4"
    (empty / "data").mkdir(parents=True)
    (empty / "tracking_config.yaml").write_text("global: {}\n", encoding="utf-8")
    item = layout.classify(empty)
    assert item.status == layout.NO_RECORDING and item.fix is None

    ambiguous = unfiled(tmp_path / "P" / "Rep5")
    (ambiguous / "Other.xlsx").write_bytes(b"x")
    assert layout.classify(ambiguous).status == layout.AMBIGUOUS


def test_a_projects_own_output_folders_are_not_replicates(tmp_path):
    """A Project root that also holds data/ used to have its own data folder
    listed as an unfiled replicate — and filing it would nest data/data/."""
    project = make_project(tmp_path / "P")
    (project / "data").mkdir(exist_ok=True)
    (project / "data" / "stray.xlsx").write_bytes(b"x")
    (project / "analysis").mkdir(exist_ok=True)
    (project / "analysis" / "leftover.xlsx").write_bytes(b"x")

    names = [e.name for e in layout.experiments_in(project)]
    assert names == ["Rep1"]


def test_initializable_dirs_lists_every_folder_without_a_config(tmp_path):
    """The candidates for "initialize this directory as a replicate" are a
    wider set than experiments_in's: a folder nobody has filled yet is
    precisely the one that needs initializing."""
    project = make_project(tmp_path / "P")
    unfiled(project / "Loose", config=False)      # recording at the root
    (project / "Empty").mkdir()                   # nothing in it at all
    (project / "NoConfig" / "data").mkdir(parents=True)
    (project / "NoConfig" / "data" / "Rec.xlsx").write_bytes(b"x")
    (project / "analysis").mkdir(exist_ok=True)   # an output folder

    found = {item.name: item.status
             for item in layout.initializable_dirs(project)}
    # Rep1 has a config, so it is a replicate already; analysis/ is never one.
    assert set(found) == {"Loose", "Empty", "NoConfig"}
    assert found["Loose"] == layout.UNFILED
    assert found["NoConfig"] == layout.NO_CONFIG
    # An empty folder is not experiment-shaped, and is still offered.
    assert found["Empty"] == layout.NOT_AN_EXPERIMENT
    assert [item.name for item in layout.experiments_in(project)] != \
        list(found)


def test_a_workbook_the_loader_cannot_see_is_blocked_not_healthy(tmp_path):
    """Experiment globs '*.xlsx'. A classifier that says healthy where the
    loader says "No .xlsx file found" moves the failure into hour three of an
    unattended run."""
    experiment = tmp_path / "P" / "Rep1"
    (experiment / "data").mkdir(parents=True)
    (experiment / "data" / "RUN.XLSX").write_bytes(b"x")
    (experiment / "tracking_config.yaml").write_text("global: {}\n",
                                                     encoding="utf-8")
    item = layout.classify(experiment)
    if os.path.exists(os.path.join(experiment, "data", "run.xlsx")):
        pytest.skip("case-insensitive filesystem")
    assert item.status == layout.NO_RECORDING
    assert "lower-case" in item.detail


def test_two_workbooks_in_data_are_ambiguous_not_ok(tmp_path):
    """Experiment picks the first and logs a line nobody reads."""
    experiment = tmp_path / "P" / "Rep1"
    (experiment / "data").mkdir(parents=True)
    (experiment / "data" / "a.xlsx").write_bytes(b"x")
    (experiment / "data" / "b.xlsx").write_bytes(b"x")
    (experiment / "tracking_config.yaml").write_text("global: {}\n",
                                                     encoding="utf-8")
    assert layout.classify(experiment).status == layout.AMBIGUOUS


def test_a_lock_file_is_not_a_recording(tmp_path):
    experiment = tmp_path / "P" / "Rep1"
    (experiment / "data").mkdir(parents=True)
    (experiment / "data" / "~$Run.xlsx").write_bytes(b"x")
    (experiment / "tracking_config.yaml").write_text("global: {}\n",
                                                     encoding="utf-8")
    assert layout.classify(experiment).status == layout.NO_RECORDING
    assert not prj.has_experiment_data(str(experiment))


# ---- filing ----------------------------------------------------------------

def test_filing_moves_the_recording_and_parks_the_rest(tmp_path):
    experiment = unfiled(tmp_path / "Rep1")
    (experiment / "notes.txt").write_text("lab notes", encoding="utf-8")
    (experiment / "photo.jpg").write_bytes(b"x")

    plan = layout.file_recording(experiment)
    assert not plan.refused
    assert sorted(os.listdir(experiment / "data")) == \
        ["Trial7.xlsx", "Trial7_Data_1.csv", "extra_files"]
    ## Inside data/, so the folder holding the recording holds all of it
    ## (ADR-0011, amended 2026-11-26).
    assert sorted(os.listdir(experiment / "data" / "extra_files")) == \
        ["notes.txt", "photo.jpg"]
    assert not (experiment / "extra_files").exists()
    assert plan.describe() == "2 file(s) → data/, 2 → data/extra_files/"
    assert layout.classify(experiment).status == layout.OK


def test_files_parked_in_data_extra_files_never_change_what_loads(tmp_path):
    ## A CSV-free folder inside data/ is invisible to the loader's test and to
    ## the classifier: the recording is still the one workbook beside it.
    experiment = unfiled(tmp_path / "Rep1")
    (experiment / "Trial7_BG.jpg").write_bytes(b"x")
    layout.file_recording(experiment)

    assert layout.classify(experiment).status == layout.OK
    assert layout.data_location(experiment).path == str(experiment / "data")


def test_filing_never_moves_a_yaml_or_a_removal_sheet(tmp_path):
    """tracking_config.yaml moved un-makes the Experiment Directory, and
    removed_regions.* moved silently returns removed flies to the analysis —
    the ADR-0010 failure this exemption exists to prevent."""
    experiment = unfiled(tmp_path / "Rep1")
    (experiment / "removed_regions.yaml").write_text(
        "removed_regions:\n  T_1: dead\n", encoding="utf-8")
    (experiment / "removed_regions.csv").write_text(
        "experiment,region,reason\n", encoding="utf-8")

    layout.file_recording(experiment)

    assert (experiment / "tracking_config.yaml").is_file()
    assert (experiment / "removed_regions.yaml").is_file()
    assert (experiment / "removed_regions.csv").is_file()
    assert not (experiment / "data" / "removed_regions.csv").exists()


def test_filing_never_overwrites(tmp_path):
    experiment = unfiled(tmp_path / "Rep1")
    (experiment / "data").mkdir()
    (experiment / "data" / "Trial7.xlsx").write_bytes(b"the real one")

    # Root workbook + one already in data/ is ambiguous: refuse, touch nothing.
    plan = layout.file_recording(experiment)
    assert plan.refused
    assert (experiment / "Trial7.xlsx").is_file()
    assert (experiment / "data" / "Trial7.xlsx").read_bytes() == b"the real one"


def test_filing_refuses_when_the_workbook_is_open_in_excel(tmp_path):
    experiment = unfiled(tmp_path / "Rep1")
    (experiment / "~$Trial7.xlsx").write_bytes(b"lock")

    plan = layout.file_recording(experiment)
    assert "open" in plan.refused
    assert (experiment / "Trial7.xlsx").is_file()


def test_filing_refuses_when_data_is_a_file(tmp_path):
    experiment = unfiled(tmp_path / "Rep1")
    (experiment / "data").write_text("not a directory", encoding="utf-8")

    plan = layout.file_recording(experiment)
    assert plan.refused
    assert (experiment / "Trial7.xlsx").is_file()


def test_a_symlinked_recording_refuses_the_whole_filing(tmp_path):
    """Moving the companions while refusing the symlinked workbook would
    leave the directory in a worse state than it started."""
    experiment = unfiled(tmp_path / "Rep1")
    target = tmp_path / "elsewhere.csv"
    target.write_text("x", encoding="utf-8")
    os.symlink(target, experiment / "linked.csv")

    plan = layout.file_recording(experiment)

    assert "symlink" in plan.refused
    assert (experiment / "Trial7.xlsx").is_file()      # nothing moved
    assert not (experiment / "data" / "Trial7.xlsx").exists()


def test_a_symlinked_note_is_skipped_but_the_recording_still_files(tmp_path):
    experiment = unfiled(tmp_path / "Rep1")
    target = tmp_path / "notes.txt"
    target.write_text("x", encoding="utf-8")
    os.symlink(target, experiment / "notes.txt")

    plan = layout.file_recording(experiment)

    assert not plan.refused
    assert (experiment / "data" / "Trial7.xlsx").is_file()
    assert (experiment / "notes.txt").is_symlink()
    assert any(name == "notes.txt" and "symlink" in why
               for name, why in plan.skipped)


# ---- the run, and the Removal Sheet it applies -----------------------------

def test_a_batch_run_targets_nested_members(tmp_path, monkeypatch):
    """Keys reach run_batch as relative paths and resolve to the right
    directories — the identity decision, end to end."""
    from pytrackinganalysis.script_editor import project_actions as pa

    make_project(tmp_path / "Sept2026" / "ProjA")
    make_project(tmp_path / "Archive" / "ProjB")

    ran: list = []
    monkeypatch.setattr(
        pa, "run_project_script",
        lambda script, project, log_cb, figure_cb=None:
            ran.append(os.path.relpath(project.project_directory, tmp_path)))

    results = batch_mod.run_batch(str(tmp_path), log=lambda _m: None)

    assert sorted(results) == ["Archive/ProjB", "Sept2026/ProjA"]
    assert all(value == "ok" for value in results.values())
    assert sorted(ran) == [os.path.join("Archive", "ProjB"),
                           os.path.join("Sept2026", "ProjA")]


def _sheet(root, rows):
    lines = ["project,experiment,region,reason"]
    lines += [",".join(row) for row in rows]
    (root / "removed_regions.csv").write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")


def test_the_removal_sheet_only_touches_members_that_run(tmp_path):
    """Unchecking a Member means "do not touch this Project" — and recursion
    surfaces Projects the user may never have known were there (ADR-0011)."""
    from pytrackinganalysis import removals

    for name in ("ProjA", "ProjB"):
        project = make_project(tmp_path / name)
        (project / "Rep1" / "tracking_config.yaml").write_text(
            "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("ProjA", "Rep1", "T_1", "dead"),
                      ("ProjB", "Rep1", "T_1", "escaped")])

    result = batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None,
                                           members=["ProjA"])

    assert result["skipped"] == 1
    assert removals.read_removals(tmp_path / "ProjA" / "Rep1") == {"T_1": "dead"}
    assert removals.read_removals(tmp_path / "ProjB" / "Rep1") == {}


def test_declining_the_sheet_writes_nothing(tmp_path):
    from pytrackinganalysis import removals

    project = make_project(tmp_path / "ProjA")
    (project / "Rep1" / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("ProjA", "Rep1", "T_1", "dead")])

    logs: list = []
    batch_mod.run_batch(str(tmp_path), log=logs.append, apply_removals=False)

    assert removals.read_removals(project / "Rep1") == {}
    assert any("declined" in line for line in logs)


def test_the_preview_and_the_write_agree(tmp_path):
    """The preview runs the same evaluation the write does, so what the user
    was shown cannot disagree with what happens (ADR-0011)."""
    project = make_project(tmp_path / "ProjA")
    (project / "Rep1" / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("ProjA", "Rep1", "T_1", "dead"),
                      ("ProjA", "Nope", "T_1", "typo"),
                      ("ProjA", "Rep1", "T_9", "no such region")])

    preview = batch_mod.preview_removal_sheet(tmp_path)
    assert preview["counts"] == {"applied": 1, "unknown experiment": 1,
                                 "unknown region": 1}
    # ...and previewing wrote nothing.
    from pytrackinganalysis import removals
    assert removals.read_removals(project / "Rep1") == {}

    applied = batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None)
    assert applied["counts"] == preview["counts"]
    assert removals.read_removals(project / "Rep1") == {"T_1": "dead"}


def test_a_sheet_row_cannot_write_outside_the_batch(tmp_path):
    """os.path.join honours '../' and absolute paths, and a Removal Sheet is a
    hand-edited spreadsheet."""
    from pytrackinganalysis import removals

    batch = tmp_path / "batch"
    outside = make_project(tmp_path / "elsewhere" / "ProjX")
    (outside / "Rep1" / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    make_project(batch / "ProjA")
    _sheet(batch, [("../elsewhere/ProjX", "Rep1", "T_1", "dead"),
                   (str(outside), "Rep1", "T_2", "dead")])

    result = batch_mod.apply_removal_sheet(batch, log=lambda _m: None)

    assert removals.read_removals(outside / "Rep1") == {}
    assert result["counts"].get("unknown project") == 2


def test_two_spellings_of_one_experiment_do_not_discard_each_other(tmp_path):
    """Both rows reported "applied" while the second write threw away the
    first's regions."""
    from pytrackinganalysis import removals

    project = make_project(tmp_path / "ProjA")
    (project / "Rep1" / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n  T_2: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("ProjA", "Rep1", "T_1", "dead"),
                      ("./ProjA", "Rep1", "T_2", "escaped")])

    batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None)

    assert removals.read_removals(project / "Rep1") == {
        "T_1": "dead", "T_2": "escaped"}


def test_one_unwritable_sidecar_does_not_discard_the_rest(tmp_path,
                                                          monkeypatch):
    """A read-only experiment directory in a batch of forty must not throw
    away the other thirty-nine's results, or report "nothing written" when
    sidecars have already been rewritten."""
    from pytrackinganalysis import removals

    for name in ("ProjA", "ProjB"):
        project = make_project(tmp_path / name)
        (project / "Rep1" / "tracking_config.yaml").write_text(
            "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("ProjA", "Rep1", "T_1", "dead"),
                      ("ProjB", "Rep1", "T_1", "dead")])

    real_write = removals.write_removals

    def flaky(experiment_dir, declared):
        if os.path.basename(os.path.dirname(str(experiment_dir))) == "ProjA":
            raise OSError(13, "Permission denied")
        return real_write(experiment_dir, declared)

    monkeypatch.setattr(removals, "write_removals", flaky)
    result = removals.apply_sheet(str(tmp_path),
                                  removals.read_sheet(
                                      tmp_path / "removed_regions.csv"))

    assert len(result["written"]) == 1
    assert result["failed"] and "ProjA" in result["failed"][0]
    assert removals.read_removals(tmp_path / "ProjB" / "Rep1") == {"T_1": "dead"}


def test_a_nested_batch_yaml_is_ignored_and_named(tmp_path):
    import yaml

    make_project(tmp_path / "Sept2026" / "ProjA")
    (tmp_path / "Sept2026" / "batch.yaml").write_text(
        yaml.safe_dump({"script": "some other run"}), encoding="utf-8")

    assert batch_mod.nested_batch_files(tmp_path) == ["Sept2026/batch.yaml"]
    logs: list = []
    batch_mod.run_batch(str(tmp_path), log=logs.append)
    assert any("Sept2026/batch.yaml" in line and "ignored" in line
               for line in logs)


# ---- regressions the audit found -------------------------------------------

def test_a_stray_marker_at_the_batch_root_does_not_kill_the_batch(tmp_path):
    """Every flat batch someone once clicked "Edit config" on has a
    project.yaml at its root. The library and the UI must agree about it."""
    make_project(tmp_path / "Sept2026" / "ProjA")
    make_project(tmp_path / "TopProj")
    prj.create_project_file(str(tmp_path), name="legacy marker")

    assert batch_mod.is_batch_dir(tmp_path)
    assert keys(tmp_path) == ["Sept2026/ProjA", "TopProj"]


def test_a_template_config_does_not_hide_the_projects_below_it(tmp_path):
    """A grouping folder with a project.yaml and a template/ holding only a
    config looks exactly like a Project whose replicates are all blocked."""
    make_project(tmp_path / "Archive" / "2025" / "ProjA")
    template = tmp_path / "Archive" / "template"
    template.mkdir(parents=True)
    (template / "tracking_config.yaml").write_text("global: {}\n",
                                                   encoding="utf-8")
    prj.create_project_file(str(tmp_path / "Archive"), name="stray")

    assert keys(tmp_path) == ["Archive/2025/ProjA"]


def test_a_member_key_cannot_escape_the_batch(tmp_path):
    make_project(tmp_path / "ProjA")
    with pytest.raises(ValueError):
        batch_mod.member_directory(tmp_path, "../elsewhere")
    with pytest.raises(ValueError):
        batch_mod.member_directory(tmp_path, "/etc")


def test_a_windows_authored_sheet_row_applies_on_linux(tmp_path):
    """The scope test and the write must resolve a cell the same way."""
    from pytrackinganalysis import removals

    project = make_project(tmp_path / "Sept2026" / "ProjA")
    (project / "Rep1" / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("Sept2026\\ProjA", "Rep1", "T_1", "dead")])

    result = batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None,
                                           members=["Sept2026/ProjA"])

    assert result["counts"] == {"applied": 1}
    assert removals.read_removals(project / "Rep1") == {"T_1": "dead"}


def test_a_sheet_saved_with_excels_capitalisation_is_still_found(tmp_path):
    from pytrackinganalysis import removals

    make_project(tmp_path / "ProjA")
    (tmp_path / "Removed_Regions.csv").write_text(
        "project,experiment,region,reason\n", encoding="utf-8")

    assert removals.find_sheet(tmp_path) is not None


def test_a_symlinked_replicate_is_not_analyzed_twice(tmp_path):
    """One recording stacked into the Combined Analysis under two labels is
    the same failure the walk prevents one level up (ADR-0011)."""
    project = make_project(tmp_path / "ProjA", replicates=("Rep1",))
    os.symlink(project / "Rep1", project / "Rep1_copy")

    assert prj.Project(str(project)).experiment_names == ["Rep1"]
    assert [e.name for e in layout.experiments_in(project)] == ["Rep1"]


def test_a_bracketed_folder_name_does_not_hide_saved_analysis(tmp_path):
    """glob.escape: 'Sept2026 [pilot]' turned the path into a character class
    and every replicate read "not analyzed" forever."""
    project = make_project(tmp_path / "Sept2026 [pilot]" / "ProjA")
    analysis = project / "Rep1" / "analysis"
    analysis.mkdir(parents=True)
    (analysis / "Rep1_Summary.csv").write_text("Name\n", encoding="utf-8")

    status = prj.Project(str(project)).experiment_status("Rep1")
    assert status["analyzed"]


# ---- the preflight dialog --------------------------------------------------

@pytest.fixture
def qapp_for_dialog():
    import os as _os

    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_the_preflight_lists_members_and_defaults_the_checks(
        tmp_path, qapp_for_dialog):
    from pytrackinganalysis.apps.batch_preflight import BatchPreflightDialog

    make_project(tmp_path / "Sept2026" / "ProjA", replicates=("Rep1", "Rep2"))
    unfiled(tmp_path / "Sept2026" / "ProjA" / "Rep3")
    make_project(tmp_path / "Pending", configs=False)

    dialog = BatchPreflightDialog(None, tmp_path, log=lambda _t: None)
    try:
        # A Member with nothing usable starts unchecked; the healthy one runs.
        assert dialog.selected_keys == ["Sept2026/ProjA"]
        assert dialog.apply_removals
        # Its blocked experiment is shown, expanded, with the reason.
        top = dialog._tree.topLevelItem(1)
        assert top.data(0, 0) or True
        assert top.childCount() == 1
        assert top.isExpanded()
        assert top.child(0).text(2) == layout.UNFILED
    finally:
        dialog.deleteLater()


def test_the_preflight_caps_and_elides_long_project_names(
        tmp_path, qapp_for_dialog):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QHeaderView

    from pytrackinganalysis.apps.batch_preflight import (
        BatchPreflightDialog,
        PROJECT_COLUMN_MAX_WIDTH,
    )

    long_key = (
        "September-2026/Archive/"
        "Very-Long-Project-Name-That-Should-Not-Widen-The-Batch-Review"
    )
    make_project(tmp_path / long_key)

    dialog = BatchPreflightDialog(None, tmp_path, log=lambda _t: None)
    try:
        tree = dialog._tree
        assert tree.topLevelItem(0).text(0) == long_key
        assert tree.textElideMode() == Qt.TextElideMode.ElideRight
        assert tree.columnWidth(0) <= PROJECT_COLUMN_MAX_WIDTH
        assert (
            tree.header().sectionResizeMode(0)
            == QHeaderView.ResizeMode.Fixed
        )
    finally:
        dialog.deleteLater()


def test_file_everything_skips_unchecked_members(tmp_path, qapp_for_dialog):
    """Moving files inside a Project the user unchecked is the same violation
    as writing a removal sheet into one."""
    from PyQt6.QtCore import Qt

    from pytrackinganalysis.apps.batch_preflight import BatchPreflightDialog

    for name in ("Mine", "Colleague"):
        make_project(tmp_path / name)
        unfiled(tmp_path / name / "Rep2")

    dialog = BatchPreflightDialog(None, tmp_path, log=lambda _t: None)
    try:
        for index in range(dialog._tree.topLevelItemCount()):
            item = dialog._tree.topLevelItem(index)
            if item.text(0) == "Colleague":
                item.setCheckState(0, Qt.CheckState.Unchecked)
        targets = [e.directory for e in dialog._unfiled()]
        assert len(targets) == 1
        assert "Mine" in targets[0]
    finally:
        dialog.deleteLater()


def test_filing_is_offered_on_a_batch_of_freshly_arrived_recordings(
        tmp_path, qapp_for_dialog):
    """The main case: nothing runs until it is filed, so every Member starts
    unchecked — and "File every unfiled recording" must still act on them."""
    from pytrackinganalysis.apps.batch_preflight import BatchPreflightDialog

    for name in ("ProjA", "ProjB"):
        (tmp_path / name).mkdir()
        prj.create_project_file(str(tmp_path / name), name=name)
        unfiled(tmp_path / name / "Rep1")

    dialog = BatchPreflightDialog(None, tmp_path, log=lambda _t: None)
    try:
        assert dialog.selected_keys == []          # nothing runnable yet
        assert len(dialog._unfiled()) == 2         # ...but both are filable
        for experiment in list(dialog._unfiled()):
            layout.file_recording(experiment.directory)
        dialog.reload()
        assert len(dialog._unfiled()) == 0
    finally:
        dialog.deleteLater()


def test_a_row_that_splits_the_path_across_cells_is_still_scoped(tmp_path):
    """project=Sept2026, experiment=ProjA/Rep1 names the same experiment as
    project=Sept2026/ProjA, experiment=Rep1 — and used to slip past scoping."""
    from pytrackinganalysis import removals

    project = make_project(tmp_path / "Sept2026" / "ProjA")
    (project / "Rep1" / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("Sept2026", "ProjA/Rep1", "T_1", "dead")])

    result = batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None,
                                           members=[])          # nothing runs

    assert result.get("skipped") == 1
    assert removals.read_removals(project / "Rep1") == {}


def test_a_malformed_sidecar_is_never_overwritten(tmp_path):
    """read_removals returns {} for a broken note so it cannot block a run —
    writing on top of that {} would delete what the experimenter wrote."""
    from pytrackinganalysis import removals

    project = make_project(tmp_path / "ProjA")
    experiment = project / "Rep1"
    (experiment / "tracking_config.yaml").write_text(
        "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    broken = experiment / "removed_regions.yaml"
    broken.write_text("removed_regions: [this is a list\n", encoding="utf-8")
    _sheet(tmp_path, [("ProjA", "Rep1", "T_1", "dead")])

    result = batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None)

    assert broken.read_text(encoding="utf-8").startswith("removed_regions: [")
    assert result["failed"]


def test_a_project_repaired_in_the_preflight_joins_the_run(tmp_path,
                                                           qapp_for_dialog):
    """Filing a recording is what MAKES a Project runnable — so the Project
    you just repaired must not stay excluded from the run you are starting."""
    from pytrackinganalysis.apps.batch_preflight import BatchPreflightDialog

    (tmp_path / "ProjA").mkdir()
    prj.create_project_file(str(tmp_path / "ProjA"), name="ProjA")
    unfiled(tmp_path / "ProjA" / "Rep1")

    dialog = BatchPreflightDialog(None, tmp_path, log=lambda _t: None)
    try:
        assert dialog.selected_keys == []            # nothing runs yet
        for experiment in list(dialog._unfiled()):
            layout.file_recording(experiment.directory)
        dialog.reload()
        assert dialog.selected_keys == ["ProjA"]     # ...and now it does
    finally:
        dialog.deleteLater()


def test_an_explicit_uncheck_survives_a_rescan(tmp_path, qapp_for_dialog):
    """The other half of the same rule: what the user actually said stands."""
    from PyQt6.QtCore import Qt

    from pytrackinganalysis.apps.batch_preflight import BatchPreflightDialog

    make_project(tmp_path / "Mine")
    make_project(tmp_path / "Colleague")

    dialog = BatchPreflightDialog(None, tmp_path, log=lambda _t: None)
    try:
        for index in range(dialog._tree.topLevelItemCount()):
            item = dialog._tree.topLevelItem(index)
            if item.text(0) == "Colleague":
                item.setCheckState(0, Qt.CheckState.Unchecked)
        dialog.reload()
        assert dialog.selected_keys == ["Mine"]
    finally:
        dialog.deleteLater()


def test_a_stray_tracking_config_does_not_hide_projects_below_it(tmp_path):
    """The mirror of the stray project.yaml: an unconditional stop at a config
    marker hid every Project beneath one file."""
    make_project(tmp_path / "Archive" / "2025" / "ProjA")
    (tmp_path / "Archive" / "tracking_config.yaml").write_text(
        "global: {}\n", encoding="utf-8")

    assert keys(tmp_path) == ["Archive/2025/ProjA"]


def test_a_replicate_named_data_is_still_a_replicate(tmp_path):
    """Discovery must not disagree with the Project about its own membership."""
    project = make_project(tmp_path / "ProjA", replicates=("Rep1",))
    odd = project / "data"
    (odd / "data").mkdir(parents=True)
    (odd / "data" / "run.xlsx").write_bytes(b"x")
    (odd / "tracking_config.yaml").write_text("global: {}\n", encoding="utf-8")

    names = sorted(e.name for e in layout.experiments_in(project))
    assert names == sorted(prj.Project(str(project)).experiment_names)


def test_a_blank_project_cell_cannot_escape_the_checked_members(tmp_path):
    """At a batch root a row must name one of the members that are running —
    a blank project cell used to pass as "the root itself" and slip out."""
    from pytrackinganalysis import removals

    for name in ("ProjA", "ProjB"):
        project = make_project(tmp_path / name)
        (project / "Rep1" / "tracking_config.yaml").write_text(
            "tracking_regions:\n  T_1: {}\n", encoding="utf-8")
    _sheet(tmp_path, [("", "ProjB/Rep1", "T_1", "sneaky")])

    result = batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None,
                                           members=["ProjA"])

    assert result["skipped"] == 1
    assert removals.read_removals(tmp_path / "ProjB" / "Rep1") == {}
    # ...and with no scoping asked for, the same row still applies.
    batch_mod.apply_removal_sheet(tmp_path, log=lambda _m: None)
    assert removals.read_removals(tmp_path / "ProjB" / "Rep1") == {
        "T_1": "sneaky"}


def test_a_stray_config_at_the_batch_root_does_not_empty_the_batch(tmp_path):
    make_project(tmp_path / "ProjTop")
    make_project(tmp_path / "Sept2026" / "ProjA")
    (tmp_path / "tracking_config.yaml").write_text("global: {}\n",
                                                   encoding="utf-8")

    assert batch_mod.is_batch_dir(tmp_path)
    assert keys(tmp_path) == ["ProjTop", "Sept2026/ProjA"]


def test_sheet_scoping_holds_on_windows_where_normcase_turns_slashes(monkeypatch):
    """On Windows os.path.normcase lowercases AND turns '/' into '\\', so a
    row's 'projb\\rep1' never started with the Member 'projb' + '/': every
    row of a scoped Removal Sheet was skipped, and the removed flies stayed
    in the analysis."""
    import ntpath

    from pytrackinganalysis.removals import SheetRow

    monkeypatch.setattr(os.path, "normcase", ntpath.normcase)
    rows = [SheetRow(2, "ProjA", "Rep1", "T_1", "dead"),
            SheetRow(3, "Sept2026", "ProjB/Rep2", "T_2", "escaped"),
            SheetRow(4, "Other", "Rep1", "T_1", "dead")]
    keep, skip = batch_mod.scope_sheet_rows(
        rows, ["ProjA", "Sept2026\\ProjB"])
    assert [row.index for row in keep] == [2, 3]
    assert [row.index for row in skip] == [4]
