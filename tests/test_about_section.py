"""The ``about:`` section of a recording's ``tracking_config.yaml`` (ADR-0019).

An optional top-level section that says what the recording is, in the person's
own words. The package helps fill it in once, keeps it intact through every
writer of the config, and never uses it for an analysis.

The shared tests (A1–A8) are the contract this package and pyflic both hold
to; the package tests (T1–T7) pin what is particular to PyTrackingAnalysis.
T1, the mirrored module itself, is ``test_about_mirror.py`` and
``test_about.py``. A1 is the rest of the suite, which passes unedited.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest
import yaml

from pytrackinganalysis import about as about_mod
from pytrackinganalysis import config_validation
from pytrackinganalysis import project as prj
from pytrackinganalysis.Experiment import Experiment

from test_experiment_integration import write_project

#: Fixed by the shared contract, so spelled out here rather than imported.
KEY = "about"

#: The design every synthetic recording here declares; ``write_project``
#: assigns its regions to these two levels.
FACTORS = {"Genotype": ["Ctrl", "Treated"]}

#: Every field, an unknown key, ``run:``, and a level mapping holding what a
#: catalogue writes (``term``, ``label``, ``role``) beside an unknown key.
FULL = {
    "run": "R-20261121-test-01",
    "title": "Light preference, MBON10 silenced, replicate 1",
    "purpose": "Whether silencing MBON10 changes the preference for light.",
    "experimenter": "atester",
    "series": "valence-mbon10-2026",
    "organism": "Drosophila melanogaster",
    "sex": ["female"],
    "age_at_start": "5-7 days",
    "protocol": {"name": "Valence two-choice", "version": "2",
                 "link": "https://example.org/valence"},
    "notes": "Plate 3 was reloaded before the start.",
    "factors": {
        "Genotype": {
            "kind": "genotype",
            "levels": {
                "Treated": "R12C11-GAL4 > UAS-GtACR1",
                "Ctrl": {"definition": "w1118 > UAS-GtACR1", "term": "GEN-0012",
                         "label": "w1118 x UAS-GtACR1", "role": "reference",
                         "x_other": "kept"},
            },
        },
    },
    "x_lab": {"a": 1},
}


def full_about() -> dict:
    return copy.deepcopy(FULL)


def recording(root, about=None, **kwargs):
    """A complete synthetic Experiment Directory declaring :data:`FACTORS`,
    with *about* as its ``about:`` section (none when ``None``)."""
    extra_global = {"experimental_design_factors": copy.deepcopy(FACTORS),
                    **kwargs.pop("extra_global", {})}
    top = dict(kwargs.pop("extra_top_level", {}))
    if about is not None:
        top[KEY] = copy.deepcopy(about)
    return write_project(root, extra_global=extra_global,
                         extra_top_level=top or None, **kwargs)


def read_config(directory) -> dict:
    with open(directory / "tracking_config.yaml", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def write_config(directory, config) -> None:
    (directory / "tracking_config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8")


def set_about(directory, about) -> None:
    """Rewrite the config on disk with *about* as its section (none when
    ``None``), as another program would: nothing else changes."""
    config = read_config(directory)
    config.pop(KEY, None)
    if about is not None:
        config[KEY] = copy.deepcopy(about)
    write_config(directory, config)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        try:
            app = QApplication([])
        except Exception as err:  # noqa: BLE001
            pytest.skip(f"Qt is unavailable: {err}")
    return app


def valid_config(**top) -> dict:
    return {**top,
            "global": {"tracking_type": "TRACKER", "tracking_rig": "small_arena",
                       "experimental_design_factors": copy.deepcopy(FACTORS)},
            "tracking_regions": {"T_1": {"experimental_factors": "Ctrl"}}}


# ---------------------------------------------------------------------------
# T6 / A8: warnings, never errors
# ---------------------------------------------------------------------------

#: Each case of contract item 9: a section, and what its one warning names.
ODD_SECTIONS = {
    "not a mapping": ("just some text", ["about:", "mapping"]),
    "a field of the wrong type": ({"title": ["a", "list"]}, ["about.title", "text"]),
    "sex of the wrong type": ({"sex": 5}, ["about.sex"]),
    "protocol of the wrong type": ({"protocol": 3}, ["about.protocol"]),
    "factors of the wrong type": ({"factors": ["Genotype"]}, ["about.factors"]),
    "an unknown kind": ({"factors": {"Genotype": {"kind": "strain"}}},
                        ["about.factors.Genotype.kind", "strain"]),
    "a title over 60 characters": ({"title": "x" * 61}, ["about.title", "61", "60"]),
    "a factor the design does not declare": (
        {"factors": {"Sex": {"kind": "sex"}}}, ["Sex", "does not declare"]),
    "a level the design does not declare": (
        {"factors": {"Genotype": {"levels": {"Other": "some cross"}}}},
        ["Other", "does not declare"]),
}


@pytest.mark.parametrize("section, named", list(ODD_SECTIONS.values()),
                         ids=list(ODD_SECTIONS))
def test_a8_each_odd_section_is_one_warning_and_no_problem(section, named):
    config = valid_config(**{KEY: section})
    assert config_validation.validate_config(config) == []
    [warning] = config_validation.config_warnings(config)
    for text in named:
        assert text in warning


def test_t6_validation_is_the_same_with_and_without_a_malformed_section():
    """Problems are fatal for a typed experiment and block the editor's
    save, so nothing about ``about:`` may ever be one."""
    sound = valid_config()
    broken = valid_config()
    broken["global"]["tracking_rig"] = "no_such_rig"
    del broken["tracking_regions"]
    assert config_validation.validate_config(broken)        # it has problems of its own
    for config in (sound, broken):
        expected = config_validation.validate_config(config)
        for section, _named in ODD_SECTIONS.values():
            assert config_validation.validate_config({**config, KEY: section}) == expected


def test_t6_the_warning_names_the_malformed_field():
    config = valid_config(**{KEY: {"title": "A title", "age_at_start": {"days": 5}}})
    [warning] = config_validation.config_warnings(config)
    assert "about.age_at_start" in warning


def test_a_well_formed_section_and_no_section_warn_about_nothing():
    assert config_validation.config_warnings(valid_config()) == []
    assert config_validation.config_warnings(valid_config(**{KEY: full_about()})) == []
    assert config_validation.config_warnings(valid_config(**{KEY: None})) == []


def test_the_section_is_checked_against_this_configs_own_factors():
    """A replicate states its whole design itself, so that is what its
    description is held to: nothing is inherited (ADR-0019)."""
    config = valid_config(**{KEY: full_about()})
    config["global"]["experimental_design_factors"] = {"Genotype": ["Ctrl"]}
    [warning] = config_validation.config_warnings(config)
    assert "Treated" in warning


def test_about_warnings_come_after_the_data_folders(tmp_path):
    config = valid_config(**{KEY: {"sex": 5},
                             "data_folder": (tmp_path / "elsewhere").as_posix()})
    first, second = config_validation.config_warnings(config)
    assert "data_folder" in first and "about.sex" in second


def test_the_file_checker_reports_the_warning_too(tmp_path):
    member = recording(tmp_path / "M1", about={"title": "x" * 80})
    path = str(member / "tracking_config.yaml")
    assert config_validation.validate_config_file(path) == []
    [warning] = config_validation.config_file_warnings(path)
    assert "about.title" in warning


@pytest.mark.parametrize("section", [
    "just some text",
    {"title": ["a", "list"], "sex": 5, "protocol": 3, "factors": ["Genotype"]},
    {"title": "x" * 61,
     "factors": {"Genotype": {"kind": "strain", "levels": {"Other": "some cross"}},
                 "Sex": {"kind": "sex"}}},
], ids=["not a mapping", "wrong types", "odd values"])
def test_a8_an_odd_section_still_loads_and_runs(tmp_path, capsys, section):
    member = recording(tmp_path / "M1", about=section)
    expected = config_validation.config_warnings(read_config(member))
    assert expected

    exp = Experiment(str(member))
    said = capsys.readouterr().out
    for warning in expected:
        assert f"Warning: {warning}" in said                 # the loader says so
    exp.run_analysis()
    assert (member / "analysis" / "Synthetic_Summary.csv").is_file()
    assert read_config(member)[KEY] == section               # and nothing rewrote it


# ---------------------------------------------------------------------------
# A2: inert
# ---------------------------------------------------------------------------

def analysis_outputs(member) -> dict:
    """Every table and text file a run writes for *member*, by name."""
    return {path.name: path.read_bytes()
            for folder in ("analysis", "qc")
            for path in sorted((member / folder).iterdir())
            if path.suffix in (".csv", ".txt")}


def test_a2_the_section_changes_no_number_and_no_verdict(tmp_path):
    study = tmp_path / "Study"
    member = recording(study / "Rep1")
    prj.create_project_file(str(study))

    edited = full_about()
    edited["title"] = "Another title altogether"
    edited["run"] = "R-20261122-test-07"
    edited["factors"]["Genotype"]["levels"]["Ctrl"]["definition"] = "Canton-S"
    edited["factors"]["Genotype"]["levels"]["Treated"] = "something else"

    outputs = {}
    for label, section in (("none", None), ("full", full_about()), ("edited", edited)):
        set_about(member, section)
        Experiment(str(member)).run_analysis()
        outputs[label] = analysis_outputs(member)
        status = prj.Project(str(study)).experiment_status("Rep1")
        assert status["analyzed"] and not status["stale"], label

    assert {"Synthetic_Summary.csv", "Synthetic_Summary_Facet.csv",
            "Synthetic_data_quality.csv", "Synthetic_Stats.txt"} <= set(outputs["none"])
    assert outputs["full"] == outputs["none"]
    assert outputs["edited"] == outputs["none"]

    ## Edited again with no run in between: the saved analysis still stands.
    set_about(member, {"title": "Edited after the analysis", "notes": "And notes."})
    project = prj.Project(str(study))
    assert not project.experiment_status("Rep1")["stale"]
    assert "re-run needed" not in prj.format_excluded_cell(
        project.experiment_status("Rep1"))
    logged: list[str] = []
    assert project.run_all(skip_analyzed=True, log=logged.append) == []
    assert logged == ["[Rep1] already analyzed — skipped"]
    assert analysis_outputs(member) == outputs["none"]


# ---------------------------------------------------------------------------
# T7: nothing of the section on a report, or in what an AI provider is sent
# ---------------------------------------------------------------------------

#: In every free-text value of :func:`marked_about`, and nowhere else.
MARK = "Qz7"


def marked_about() -> dict:
    return {
        "run": f"R-{MARK}-run",
        "title": f"{MARK} title",
        "purpose": f"{MARK} purpose",
        "experimenter": f"{MARK}person",
        "series": f"{MARK}-series",
        "organism": f"{MARK} organism",
        "sex": [f"{MARK}sex"],
        "age_at_start": f"{MARK} days",
        "protocol": {"name": f"{MARK} protocol", "version": f"{MARK}v",
                     "link": f"https://example.org/{MARK}"},
        "notes": f"{MARK} notes",
        "factors": {"Genotype": {"kind": "genotype", "levels": {
            "Treated": f"{MARK} cross",
            "Ctrl": {"definition": f"{MARK} control", "term": f"{MARK}-TERM",
                     "label": f"{MARK} label", "role": f"{MARK} role"}}}},
        "x_lab": {"a": f"{MARK} unknown"},
    }


def strings_in(value):
    """Every string a report model holds: block fields, cells, labels."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (bytes, bytearray)):
        return
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from strings_in(getattr(value, field.name))
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from strings_in(key)
            yield from strings_in(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from strings_in(item)


def written_text(directory) -> dict:
    """The text of every table, note and manifest under *directory*'s
    ``analysis/`` and ``qc/`` (a Project has no ``qc/``)."""
    return {path.name: path.read_text(encoding="utf-8", errors="replace")
            for folder in ("analysis", "qc") if (directory / folder).is_dir()
            for path in sorted((directory / folder).iterdir())
            if path.suffix in (".csv", ".txt", ".json", ".md")}


def test_t7_a_report_holds_none_of_the_sections_text(tmp_path):
    from pytrackinganalysis.ai.payload import build_payload

    member = recording(tmp_path / "Marked", about=marked_about())
    exp = Experiment(str(member))
    exp.run_analysis()

    report = "\n".join(strings_in(exp.build_report_model()))
    assert "Marked" in report and "Genotype" in report      # the walk reads the report
    assert MARK not in report

    payload = build_payload(exp)
    assert "Synthetic_Summary.csv" in payload.text           # report and tables both
    assert MARK not in payload.text

    assert MARK not in str(exp)                              # what the Hub logs on load
    for name, text in written_text(member).items():
        assert MARK not in text, name
    assert exp.create_report().endswith("Marked_report.pdf")


# ---------------------------------------------------------------------------
# A3 / A4: the writers that do not edit the section
# ---------------------------------------------------------------------------

SCRIPT = {"name": "nightly",
          "steps": [{"action": "load_experiment", "params": {"path": "."}},
                    {"action": "run_qc", "params": {}}]}


@pytest.fixture
def script_dialogs(monkeypatch):
    """Capture the Script Editor's modal dialogs: they would block a headless run."""
    from pytrackinganalysis.script_editor import window as win

    seen = {"information": [], "warning": [], "critical": []}
    for kind in seen:
        monkeypatch.setattr(
            win.QMessageBox, kind,
            lambda *args, _kind=kind, **kwargs: seen[_kind].append(args))
    return seen


def about_block(directory) -> str:
    """The ``about:`` block of the config as the file spells it."""
    lines = (directory / "tracking_config.yaml").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{KEY}:")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i] and not lines[i][0].isspace()), len(lines))
    return "\n".join(lines[start:end])


def test_a3_the_script_editor_keeps_the_section_to_the_letter(
        qapp, tmp_path, script_dialogs):
    from pytrackinganalysis.script_editor.runner import load_scripts
    from pytrackinganalysis.script_editor.window import ScriptEditorWindow

    member = recording(tmp_path / "M1", about=full_about())
    spelled = about_block(member)
    window = ScriptEditorWindow(str(member / "tracking_config.yaml"))
    try:
        window._sections[0]["scripts"].append(copy.deepcopy(SCRIPT))
        window._save()
    finally:
        window.close()

    assert script_dialogs["information"] and not script_dialogs["critical"]
    assert load_scripts(member / "tracking_config.yaml") == [SCRIPT]
    assert read_config(member)[KEY] == FULL
    assert about_block(member) == spelled                    # spliced around, not rewritten


def test_a4_the_script_editor_writes_back_the_section_now_on_disk(
        qapp, tmp_path, script_dialogs):
    """A catalogue may rewrite the section while the window is open."""
    from pytrackinganalysis.script_editor.window import ScriptEditorWindow

    member = recording(tmp_path / "M1", about={"title": "As first typed"})
    window = ScriptEditorWindow(str(member / "tracking_config.yaml"))
    try:
        set_about(member, full_about())                      # changed on disk meanwhile
        window._sections[0]["scripts"].append(copy.deepcopy(SCRIPT))
        window._save()
    finally:
        window.close()

    assert read_config(member)[KEY] == FULL
    assert read_config(member)["scripts"] == [SCRIPT]


def test_a4_save_scripts_adds_no_section_to_a_config_without_one(tmp_path):
    from pytrackinganalysis.script_editor.runner import save_scripts

    member = recording(tmp_path / "M1")
    save_scripts(member / "tracking_config.yaml", [copy.deepcopy(SCRIPT)])
    assert KEY not in read_config(member)


def valence_config(about=None, **global_keys) -> dict:
    """A replicate config of a type that reads Directional Hypotheses."""
    config = {
        "global": {"experiment_type": "Valence", "tracking_rig": "colosseum",
                   "facet_cutoffs": [10, 70],
                   "experimental_design_factors": copy.deepcopy(FACTORS),
                   **global_keys},
        "counting_regions": {"Light": {"alias": "L"}, "NoLight": {"alias": "N"}},
        "tracking_regions": {"T_0": {"experimental_factors": "Ctrl"}},
    }
    if about is not None:
        config = {KEY: copy.deepcopy(about), **config}
    return config


def test_a3_writing_hypotheses_into_replicates_keeps_the_section(tmp_path):
    for name in ("Rep1", "Rep2"):
        (tmp_path / name).mkdir()
        write_config(tmp_path / name, valence_config(about=full_about()))
    (tmp_path / "Plain").mkdir()
    write_config(tmp_path / "Plain", valence_config())

    changed = prj.write_replicate_hypotheses(str(tmp_path), ["Treated < Ctrl"])

    assert len(changed) == 3
    for name in ("Rep1", "Rep2"):
        config = read_config(tmp_path / name)
        assert config["global"]["hypotheses"] == ["Treated < Ctrl"]
        assert config[KEY] == FULL
        assert list(config)[0] == KEY                        # where it was
    assert KEY not in read_config(tmp_path / "Plain")


def test_a4_the_project_dialog_writes_back_the_section_now_on_disk(
        qapp, tmp_path, monkeypatch):
    """The Project dialog carries new predictions into its replicates. What
    it writes there starts from each file as it is at that moment."""
    from PyQt6.QtWidgets import QMessageBox

    from pytrackinganalysis.apps.hub import ProjectInfoDialog

    design = {"global": {"experiment_type": "Valence", "facet_cutoffs": [10, 70],
                         "experimental_design_factors": copy.deepcopy(FACTORS)},
              "counting_regions": ["Light", "NoLight"]}
    prj.create_project_file(str(tmp_path), "P", design=design)
    for name in ("Rep1", "Rep2"):
        (tmp_path / name).mkdir()
        write_config(tmp_path / name, valence_config(about={"title": "As first typed"}))

    dialog = ProjectInfoDialog(None, start_dir=str(tmp_path))
    try:
        dialog._prefill_from_dir()
        set_about(tmp_path / "Rep1", full_about())           # changed on disk meanwhile
        dialog.hypotheses_edit.add_row("Treated < Ctrl", "Experiment")
        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        dialog._save()
    finally:
        dialog.close()

    predicted = [{"expect": "Treated < Ctrl", "phases": ["Experiment"]}]
    assert read_config(tmp_path / "Rep1")["global"]["hypotheses"] == predicted
    assert read_config(tmp_path / "Rep1")[KEY] == FULL
    assert read_config(tmp_path / "Rep2")[KEY] == {"title": "As first typed"}
    prj.Project(str(tmp_path))                               # and it still loads


# ---------------------------------------------------------------------------
# A7 / T4: a new replicate starts from the first one's shared About
# ---------------------------------------------------------------------------

#: What replicate recordings share of :data:`FULL`: no title, purpose, notes
#: or run, and no key of its own; each level's whole entry.
SHARED = {key: FULL[key] for key in ("experimenter", "series", "organism", "sex",
                                     "age_at_start", "protocol", "factors")}
DATA_FOLDER = "../../runs/R1/data"


def study(root, about=None, data_folder=None, names=("Rep1",)):
    """A Project at *root* whose replicates carry *about* (and, when given,
    a ``data_folder:``)."""
    from test_project import _make_project

    def config_for(_name):
        config = valence_config(about=about)
        return config if data_folder is None else {"data_folder": data_folder, **config}

    root.mkdir(parents=True, exist_ok=True)
    return _make_project(root, names=names, with_analysis=False, config_for=config_for)


@pytest.mark.parametrize("data_folder", [None, DATA_FOLDER],
                         ids=["own data", "data_folder"])
def test_a7_a_scaffolded_replicate_holds_the_shared_about_only(tmp_path, data_folder):
    project = study(tmp_path / "Study", about=full_about(), data_folder=data_folder)

    path = project.scaffold_replicate("Rep2")

    with open(path, encoding="utf-8") as handle:
        scaffolded = yaml.safe_load(handle)
    assert scaffolded[KEY] == SHARED
    assert not {"run", "title", "purpose", "notes", "x_lab"} & set(scaffolded[KEY])
    ctrl = scaffolded[KEY]["factors"]["Genotype"]["levels"]["Ctrl"]
    assert ctrl == FULL["factors"]["Genotype"]["levels"]["Ctrl"]     # mapping keys and all
    assert "data_folder" not in scaffolded
    assert scaffolded["global"] == valence_config()["global"]
    assert read_config(tmp_path / "Study" / "Rep1")[KEY] == FULL     # the first is untouched
    assert config_validation.config_warnings(scaffolded) == []
    prj.Project(str(tmp_path / "Study"))                             # and it loads


def test_t4_scaffolding_from_a_replicate_with_no_section_is_as_it_was(tmp_path):
    project = study(tmp_path / "Study")
    first = read_config(tmp_path / "Study" / "Rep1")

    path = project.scaffold_replicate("Rep2")

    assert KEY not in project.scaffold_replicate_config()
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == yaml.safe_dump(first, sort_keys=False,
                                               allow_unicode=True)


@pytest.mark.parametrize("section", [
    {"title": "Only this recording's", "purpose": "Its own.", "notes": "Its own.",
     "run": "R-20261121-test-01"},
    "just some text",
    {},
], ids=["nothing shared", "not a mapping", "empty"])
def test_a_section_with_nothing_to_share_leaves_no_key_behind(tmp_path, section):
    project = study(tmp_path / "Study", about=section)
    assert KEY not in project.scaffold_replicate_config()


def test_scaffolding_leaves_out_a_level_the_design_does_not_declare(tmp_path):
    """Its description would only draw a warning in the new replicate too."""
    section = full_about()
    section["factors"]["Genotype"]["levels"]["Gone"] = "a level of an earlier design"
    section["factors"]["Sex"] = {"kind": "sex"}
    project = study(tmp_path / "Study", about=section)

    assert project.scaffold_replicate_config()[KEY] == SHARED


def copy_into_new_replicate(qapp, tmp_path, monkeypatch, source_config, text=None):
    """Create ``Rep2`` in a Project and take **Copy config from…**, choosing a
    config outside the Project. Returns ``(source path, target path, log)``."""
    from test_project import _create_then_copy

    study(tmp_path / "Study")
    earlier = tmp_path / "Earlier" / "RepA"
    earlier.mkdir(parents=True)
    source = earlier / "tracking_config.yaml"
    if text is None:
        write_config(earlier, source_config)
    else:
        source.write_text(text, encoding="utf-8")
    win, warned, launched, _told = _create_then_copy(
        qapp, tmp_path / "Study", monkeypatch, str(source))
    try:
        assert not warned and not launched
        log = win._log.toPlainText()
    finally:
        win.close()
    return source, tmp_path / "Study" / "Rep2" / "tracking_config.yaml", log


@pytest.mark.parametrize("data_folder", [None, DATA_FOLDER],
                         ids=["own data", "data_folder"])
def test_t4_copy_config_from_takes_the_shared_about_only(
        qapp, tmp_path, monkeypatch, data_folder):
    config = valence_config(about=full_about(), tracking_rig="arena_max")
    if data_folder is not None:
        config = {"data_folder": data_folder, **config}

    source, target, _log = copy_into_new_replicate(qapp, tmp_path, monkeypatch, config)

    copied = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert copied[KEY] == SHARED
    assert "data_folder" not in copied
    assert copied["global"]["tracking_rig"] == "arena_max"           # the copy, not the scaffold
    assert {k: v for k, v in copied.items() if k != KEY} == \
        {k: v for k, v in config.items() if k not in (KEY, "data_folder")}
    assert yaml.safe_load(source.read_text(encoding="utf-8")) == config   # source untouched
    assert "Rep2" in prj.Project(str(tmp_path / "Study")).experiment_names


def test_t4_a_config_with_no_section_is_still_copied_byte_for_byte(
        qapp, tmp_path, monkeypatch):
    text = ("# Plate 3, as loaded on the day.\n"
            + yaml.safe_dump(valence_config(tracking_rig="arena_max"), sort_keys=False)
            + "# trailing note\n")

    source, target, _log = copy_into_new_replicate(qapp, tmp_path, monkeypatch,
                                                   None, text=text)

    assert target.read_bytes() == source.read_bytes()
    assert target.read_text(encoding="utf-8") == text


def test_t4_a_section_holding_only_what_is_shared_is_copied_byte_for_byte(
        qapp, tmp_path, monkeypatch):
    """Nothing in it is one recording's alone, so nothing needs rewriting
    and the file keeps its comments."""
    text = ("# Plate 3, as loaded on the day.\n"
            + yaml.safe_dump(valence_config(about=copy.deepcopy(SHARED),
                                            tracking_rig="arena_max"), sort_keys=False))

    source, target, _log = copy_into_new_replicate(qapp, tmp_path, monkeypatch,
                                                   None, text=text)

    assert target.read_bytes() == source.read_bytes()


def test_t4_the_hub_says_what_of_the_section_stayed_behind(qapp, tmp_path, monkeypatch):
    _source, _target, log = copy_into_new_replicate(
        qapp, tmp_path, monkeypatch, valence_config(about=full_about()))
    assert "about:" in log and "RepA" in log
    assert "title" in log and "run:" in log


# ---------------------------------------------------------------------------
# T5: a Project says when its replicates describe one level differently
# ---------------------------------------------------------------------------

def tracker_study(root, abouts: dict):
    """A Project at *root* of complete synthetic recordings, one per name in
    *abouts*, each with that ``about:`` (none for ``None``)."""
    for name, section in abouts.items():
        recording(root / name, about=section)
    prj.create_project_file(str(root))
    return prj.Project(str(root))


def two_meanings() -> dict:
    """Two replicates that give ``Treated`` different meanings."""
    second = full_about()
    second["factors"]["Genotype"]["levels"]["Treated"] = "Canton-S"
    return {"Rep1": full_about(), "Rep2": second}


def test_t5_one_level_described_two_ways_is_one_warning_naming_both(tmp_path):
    project = tracker_study(tmp_path / "Study", two_meanings())

    assert project.experiment_names == ["Rep1", "Rep2"]         # it loads
    [warning] = project.about_warnings
    assert "Rep1" in warning and "Rep2" in warning
    assert "Genotype" in warning and "Treated" in warning
    assert "R12C11-GAL4 > UAS-GtACR1" in warning and "Canton-S" in warning
    ## In this package's words: a Member is a Project of a Batch here.
    assert "replicates" in warning and "members" not in warning.lower()


def test_t5_replicates_that_agree_or_say_nothing_are_no_warning(tmp_path):
    project = tracker_study(tmp_path / "Study", {
        "Rep1": full_about(), "Rep2": full_about(), "Rep3": None,
        "Rep4": {"title": "Says nothing of the factors"}, "Rep5": "not a mapping"})
    assert project.about_warnings == []
    assert tracker_study(tmp_path / "Plain", {"A": None, "B": None}).about_warnings == []


def test_t5_one_factor_given_two_kinds_is_a_warning_too(tmp_path):
    second = full_about()
    second["factors"]["Genotype"]["kind"] = "treatment"
    project = tracker_study(tmp_path / "Study", {"Rep1": full_about(), "Rep2": second})

    [warning] = project.about_warnings
    assert "kinds" in warning and "genotype" in warning and "treatment" in warning
    assert "Rep1" in warning and "Rep2" in warning


def test_t5_the_warning_never_reaches_a_report_or_the_statistics(tmp_path):
    """The Project's other warnings are printed in the Combined Analysis
    statistics and on the Project Report, and the report is what an AI
    provider is sent. Nothing of ``about:`` goes there, so this one is kept
    apart."""
    from pytrackinganalysis.ai.payload import build_payload

    first, second = marked_about(), marked_about()
    second["factors"]["Genotype"]["levels"]["Treated"] = f"{MARK} another cross"
    project = tracker_study(tmp_path / "Study", {"Rep1": first, "Rep2": second})

    [warning] = project.about_warnings
    assert MARK in warning
    assert not any("about" in line for line in project.warnings)

    assert project.run_all(make_reports=False, log=lambda _line: None) == []
    project.build_combined_analysis()
    report = "\n".join(strings_in(project.build_report_model()))
    assert "Rep1" in report and "Genotype" in report         # the walk reads the report
    assert MARK not in report
    assert MARK not in build_payload(project).text
    for name, text in written_text(tmp_path / "Study").items():
        assert MARK not in text, name
    assert "Study_Stats.txt" in written_text(tmp_path / "Study")


def test_t5_the_hub_shows_it_with_the_projects_other_notes(qapp, tmp_path):
    from pytrackinganalysis.apps.hub import HubWindow

    tracker_study(tmp_path / "Study", two_meanings())
    win = HubWindow(initial_project=str(tmp_path / "Study"))
    qapp.processEvents()
    try:
        shown = win._projectview_summary.text()
        assert "Note: about: replicates describe Genotype" in shown
        assert "Rep1" in shown and "Rep2" in shown
        ## A cross is written with > and may hold <: escaped, not markup.
        assert "R12C11-GAL4 &gt; UAS-GtACR1" in shown
    finally:
        win.close()


def test_t5_validate_yamls_reports_it_as_a_note_not_a_problem(qapp, tmp_path):
    from pytrackinganalysis.apps.hub import HubWindow

    tracker_study(tmp_path / "Study", two_meanings())
    win = HubWindow(initial_project=str(tmp_path / "Study"))
    qapp.processEvents()
    try:
        win._validate_yaml()
        said = win._log.toPlainText()
        assert "note: about: replicates describe Genotype" in said
        assert "3 file(s) checked, 0 problem(s) found." in said
        assert "about:" not in win._err_log.toPlainText()
    finally:
        win.close()


def test_t5_the_validate_design_step_logs_it(tmp_path):
    from pytrackinganalysis.script_editor import project_actions

    project = tracker_study(tmp_path / "Study", two_meanings())
    logged: list[str] = []
    ctx = project_actions.ProjectRunContext(project=project, log=logged.append)
    project_actions._exec_validate_design({}, ctx)
    assert any(line.startswith("Note: about: replicates describe Genotype")
               for line in logged)
