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
    with *about* as its ``about:`` section (none when ``None``).

    Its config is one the Config Editor writes back unchanged — each region
    states its multipliers — so that whatever an editor test finds changed,
    the section changed it."""
    extra_global = {"experimental_design_factors": copy.deepcopy(FACTORS),
                    **kwargs.pop("extra_global", {})}
    top = dict(kwargs.pop("extra_top_level", {}))
    if about is not None:
        top[KEY] = copy.deepcopy(about)
    member = write_project(root, extra_global=extra_global,
                           extra_top_level=top or None, **kwargs)
    config = read_config(member)
    for region in config["tracking_regions"].values():
        region.update({"x_location_multiplier": 1, "y_location_multiplier": 1})
    write_config(member, config)
    return member


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


#: Sections holding several of those cases at once.
WRONG_TYPES = {"title": ["a", "list"], "sex": 5, "protocol": 3, "factors": ["Genotype"]}
ODD_VALUES = {"title": "x" * 61,
              "factors": {"Genotype": {"kind": "strain",
                                       "levels": {"Other": "some cross"}},
                          "Sex": {"kind": "sex"}}}


@pytest.mark.parametrize("section", ["just some text", WRONG_TYPES, ODD_VALUES],
                         ids=["not a mapping", "wrong types", "odd values"])
def test_a8_an_odd_section_still_loads_and_the_loader_says_so(tmp_path, capsys, section):
    member = recording(tmp_path / "M1", about=section)
    expected = config_validation.config_warnings(read_config(member))
    assert expected

    exp = Experiment(str(member))
    said = capsys.readouterr().out
    for warning in expected:
        assert f"Warning: {warning}" in said
    assert len(exp.arena.summarize()) == 2                   # and it computes as ever


def test_a8_an_odd_section_still_runs(tmp_path):
    member = recording(tmp_path / "M1", about={**WRONG_TYPES, "notes": {"a": 1}})
    Experiment(str(member)).run_analysis()
    assert (member / "analysis" / "Synthetic_Summary.csv").is_file()
    assert read_config(member)[KEY] == {**WRONG_TYPES, "notes": {"a": 1}}   # nothing rewrote it


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
    from test_project import _exp_config, _make_project

    def config_for(name):
        section = marked_about()
        section["factors"] = {"genotype": {"kind": "genotype", "levels": {
            "chr": f"{MARK} cross" if name == "Rep1" else f"{MARK} another cross",
            "control": {"definition": f"{MARK} control", "term": f"{MARK}-TERM"}}}}
        ## A criterion that differs too, so the other channel has a warning.
        return {KEY: section, **_exp_config(min_movement=140 if name == "Rep1" else 100)}

    root = tmp_path / "Study"
    root.mkdir()
    project = _make_project(root, config_for=config_for)     # saved analyses and all

    [warning] = project.about_warnings
    assert MARK in warning and "Rep1" in warning and "Rep2" in warning
    [other] = project.warnings
    assert "min_movement" in other

    project.build_combined_analysis()
    report = "\n".join(strings_in(project.build_report_model()))
    statistics = written_text(root)
    assert f"Note: {other}" in report                        # that channel is printed
    assert f"Note: {other}" in statistics["Proj_Stats.txt"]
    assert MARK not in report
    assert MARK not in build_payload(project).text
    for name, text in statistics.items():
        assert MARK not in text, name


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


# ---------------------------------------------------------------------------
# The Config Editor's About tab (T2, T3, A3, A6, A8)
# ---------------------------------------------------------------------------

@pytest.fixture
def dialogs(monkeypatch):
    """Capture the editor's modal dialogs: they would block a headless run."""
    from pytrackinganalysis.apps import config_editor as ce

    seen: dict[str, list] = {"information": [], "warning": [], "critical": []}
    for kind in seen:
        monkeypatch.setattr(
            ce.QMessageBox, kind,
            lambda *args, _kind=kind, **kwargs: seen[_kind].append(args))
    monkeypatch.setattr(ce.QMessageBox, "question",
                        lambda *args, **kwargs: ce.QMessageBox.StandardButton.No)
    return seen


@pytest.fixture
def open_editor(qapp, dialogs):
    """Open the Config Editor on a recording's config (or its folder)."""
    from pytrackinganalysis.apps.config_editor import ConfigEditorWindow

    windows = []

    def _open(target):
        window = ConfigEditorWindow(str(target))
        windows.append(window)
        return window

    yield _open
    for window in windows:
        window.close()


def is_dirty(window) -> bool:
    window._refresh_dirty()
    return bool(window._dirty_label.text())


def spelled(value) -> str:
    """*value* as YAML, key order and all: equal text is an equal section
    written in the same order."""
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def test_t2_a_full_section_opens_and_shows_every_field(tmp_path, open_editor):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)
    tab = window._about_tab

    assert window._tabs.count() == 4
    assert window._tabs.tabText(3) == "About" and window._tabs.widget(3) is tab
    assert tab.title.text() == FULL["title"]
    assert tab.title_count.text() == f"{len(FULL['title'])}/60"
    assert tab.purpose.toPlainText() == FULL["purpose"]
    assert tab.experimenter.text() == "atester"
    assert tab.series.text() == "valence-mbon10-2026"
    assert tab.organism.text() == "Drosophila melanogaster"
    assert tab.sex_checks["female"].isChecked() and not tab.sex_checks["male"].isChecked()
    assert tab.age_at_start.text() == "5-7 days"
    assert {field: edit.text() for field, edit in tab.protocol_edits.items()} == \
        FULL["protocol"]
    assert tab.notes.toPlainText() == FULL["notes"]
    assert tab.kinds() == {"Genotype": "genotype"}
    assert tab.meanings() == {("Genotype", "Ctrl"): "w1118 > UAS-GtACR1",
                              ("Genotype", "Treated"): "R12C11-GAL4 > UAS-GtACR1"}
    ## run: is the catalogue's: shown, with nothing to type into.
    assert tab.run_value.text() == FULL["run"] and not tab.run_value.isHidden()
    assert not hasattr(tab.run_value, "setReadOnly")


def test_t2_a_full_section_is_not_dirty_and_saves_to_an_equal_section(
        tmp_path, open_editor, dialogs):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)

    assert not is_dirty(window)
    assert window._dump_config()[KEY] == FULL
    window._save()

    assert dialogs["information"] and not dialogs["warning"]
    assert read_config(member)[KEY] == FULL
    assert not is_dirty(window)


def test_t2_a_config_without_one_is_not_dirty_and_saves_without_the_key(
        tmp_path, open_editor):
    member = recording(tmp_path / "M1")
    before = read_config(member)
    window = open_editor(member)
    tab = window._about_tab

    assert not is_dirty(window)
    assert KEY not in window._dump_config()
    assert tab.title.text() == "" and tab.run_value.isHidden()
    assert list(tab.meanings().values()) == ["", ""]         # a row per level, all blank
    window._save()

    saved = read_config(member)
    assert KEY not in saved and list(saved) == list(before)
    assert KEY not in (member / "tracking_config.yaml").read_text(encoding="utf-8")


#: Sections a person or another program could have written that are not in
#: the form this package writes. Opening one must not make the config look
#: changed, and saving with the tab untouched must leave it as it was.
AS_WRITTEN = {
    "keys in another order": {"notes": "N", "x_lab": {"a": 1}, "title": "T",
                              "run": "R-1", "experimenter": "atester"},
    "sex as one word": {"sex": "Female"},
    "protocol as text": {"protocol": "Valence two-choice"},
    "untrimmed text": {"title": "  A title  ", "purpose": "Two lines.\n\n"},
    "numbers for text": {"age_at_start": 5, "protocol": {"version": 2}},
    "a level the design lacks": {"factors": {"Genotype": {"levels": {
        "Gone": {"definition": "an earlier level", "term": "GEN-0001"}}}}},
    "an undeclared factor": {"factors": {"Sex": {"kind": "sex",
                                                 "levels": {"F": "virgin"}}}},
    "wrong types": WRONG_TYPES,
    "not a mapping": "just some text",
    "empty": {},
    "null": None,
}


@pytest.mark.parametrize("section", list(AS_WRITTEN.values()), ids=list(AS_WRITTEN))
def test_t2_a_section_merely_held_is_not_a_change(tmp_path, open_editor, section):
    member = recording(tmp_path / "M1")
    config = read_config(member)
    config[KEY] = copy.deepcopy(section)
    write_config(member, config)
    window = open_editor(member)

    assert not is_dirty(window)
    assert spelled(window._dump_config()[KEY]) == spelled(section)
    window._save()
    assert spelled(read_config(member)[KEY]) == spelled(section)


def test_t2_an_edited_section_is_written_in_the_contracts_order(tmp_path, open_editor):
    section = {"notes": "N", "x_lab": {"a": 1}, "title": "T", "sex": "Female",
               "protocol": "Valence two-choice", "run": "R-1",
               "factors": {"Genotype": {"levels": {"Ctrl": "w1118"}, "kind": "Genotype",
                                        "x_factor": True}}}
    member = recording(tmp_path / "M1", about=section)
    window = open_editor(member)
    tab = window._about_tab

    tab.experimenter.setText("  atester ")
    assert is_dirty(window)
    window._save()

    saved = read_config(member)[KEY]
    assert list(saved) == ["run", "title", "experimenter", "sex", "protocol", "notes",
                           "factors", "x_lab"]
    assert saved == {
        "run": "R-1", "title": "T", "experimenter": "atester", "sex": ["female"],
        "protocol": {"name": "Valence two-choice"}, "notes": "N",
        "factors": {"Genotype": {"kind": "genotype", "levels": {"Ctrl": "w1118"},
                                 "x_factor": True}},
        "x_lab": {"a": 1}}
    assert not is_dirty(window)


def test_t2_a_new_section_is_written_at_the_top_of_the_file(tmp_path, open_editor):
    plain = recording(tmp_path / "Plain")
    window = open_editor(plain)
    window._about_tab.title.setText("A first title")
    window._save()
    assert list(read_config(plain)) == [KEY, "global", "tracking_regions"]
    assert read_config(plain)[KEY] == {"title": "A first title"}

    ## After data_folder: when the file leads with one, as a catalogue writes it.
    member = tmp_path / "Member"
    member.mkdir()
    write_config(member, {"data_folder": "../Plain/data", **read_config(plain)})
    config = read_config(member)
    del config[KEY]
    write_config(member, config)
    window = open_editor(member)
    window._about_tab.title.setText("A first title")
    assert list(window._dump_config()) == ["data_folder", KEY, "global",
                                           "tracking_regions"]


def test_t2_blanking_every_field_writes_no_section(tmp_path, open_editor):
    member = recording(tmp_path / "M1", about={"title": "T", "purpose": "P",
                                               "sex": ["male"],
                                               "protocol": {"name": "P"}})
    window = open_editor(member)
    tab = window._about_tab
    tab.title.clear()
    tab.purpose.clear()
    tab.sex_checks["male"].setChecked(False)
    tab.protocol_edits["name"].clear()

    assert KEY not in window._dump_config()
    window._save()
    assert KEY not in read_config(member)


def test_t2_what_the_form_has_no_field_for_outlives_blanking_it(tmp_path, open_editor):
    member = recording(tmp_path / "M1", about={"run": "R-1", "title": "T",
                                               "x_lab": {"a": 1}})
    window = open_editor(member)
    window._about_tab.title.clear()
    assert window._dump_config()[KEY] == {"run": "R-1", "x_lab": {"a": 1}}


def test_the_title_count_turns_at_the_limit_and_nothing_is_refused(
        tmp_path, open_editor, dialogs):
    member = recording(tmp_path / "M1")
    window = open_editor(member)
    tab = window._about_tab

    assert tab.title_count.text() == ""
    tab.title.setText("x" * 60)
    assert tab.title_count.text() == "60/60" and not tab.title_count.styleSheet()
    tab.title.setText("x" * 61)
    assert tab.title_count.text() == "61/60" and tab.title_count.styleSheet()
    window._save()

    assert read_config(member)[KEY] == {"title": "x" * 61}   # saved all the same
    [saved] = dialogs["information"]
    assert "about.title is 61 characters" in saved[2]        # and said, as a note
    assert not dialogs["warning"] and not dialogs["critical"]


def test_a8_an_odd_section_still_saves_and_the_editor_says_what_is_odd(
        tmp_path, open_editor, dialogs):
    member = recording(tmp_path / "M1", about=copy.deepcopy(ODD_VALUES))
    window = open_editor(member)
    window._global_tab.facet_cutoffs.setText("3, 5")         # some other edit
    window._save()

    assert not dialogs["warning"] and not dialogs["critical"]
    [saved] = dialogs["information"]
    for warning in config_validation.config_warnings(read_config(member)):
        assert warning in saved[2]
    assert read_config(member)["global"]["facet_cutoffs"] == [3, 5]
    assert spelled(read_config(member)[KEY]) == spelled(ODD_VALUES)   # untouched, kept


def test_a3_the_config_editor_keeps_the_section_through_other_edits(
        tmp_path, open_editor):
    member = recording(tmp_path / "M1", about=full_about(),
                       extra_top_level={"scripts": [copy.deepcopy(SCRIPT)]})
    window = open_editor(member)
    window._global_tab.facet_cutoffs.setText("3, 5")
    window._tracking_tab.table.item(0, 0).setText("T_9")
    window._save()

    saved = read_config(member)
    assert saved[KEY] == FULL
    assert spelled(saved[KEY]) == spelled(FULL)
    assert saved["scripts"] == [SCRIPT]
    assert "T_9" in saved["tracking_regions"]


# ---- factors and levels (T3) ------------------------------------------------

def rename_levels(window, text, row=0) -> None:
    """Type *text* into the Global tab's levels cell, as a person would."""
    window._global_tab.factors_table.item(row, 1).setText(text)


def test_t3_renaming_a_level_drops_its_row_and_keeps_the_others(tmp_path, open_editor):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)
    tab = window._about_tab

    rename_levels(window, "Control, Treated")

    assert tab.meanings() == {("Genotype", "Control"): "",
                              ("Genotype", "Treated"): "R12C11-GAL4 > UAS-GtACR1"}
    assert tab.kinds() == {"Genotype": "genotype"}
    levels = window._dump_config()[KEY]["factors"]["Genotype"]["levels"]
    assert levels == {"Treated": "R12C11-GAL4 > UAS-GtACR1"}
    ## Everything else in the section is as it was.
    rest = {k: v for k, v in window._dump_config()[KEY].items() if k != "factors"}
    assert rest == {k: v for k, v in FULL.items() if k != "factors"}


def test_t3_a_level_renamed_back_has_its_whole_entry_again(tmp_path, open_editor):
    """A level's entry may hold a catalogue's keys; a slip of the keyboard on
    the Global tab must not cost them."""
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)

    rename_levels(window, "Control, Treated")
    assert "Ctrl" not in window._dump_config()[KEY]["factors"]["Genotype"]["levels"]
    rename_levels(window, "Ctrl, Treated")

    assert window._about_tab.meanings()[("Genotype", "Ctrl")] == "w1118 > UAS-GtACR1"
    ## The section is the file's own again, to the letter. (The tracking
    ## regions that named Ctrl lost their level, as they always have.)
    assert spelled(window._dump_config()[KEY]) == spelled(read_config(member)[KEY])


def test_t3_what_was_typed_is_kept_as_the_factors_change(tmp_path, open_editor):
    member = recording(tmp_path / "M1")
    window = open_editor(member)
    tab = window._about_tab

    tab.meaning_edit("Genotype", "Ctrl").setText("w1118")
    tab.kind_combo("Genotype").setCurrentIndex(tab.kind_combo("Genotype").findData("genotype"))
    rename_levels(window, "Ctrl, Treated, Rescue")
    table = window._global_tab.factors_table
    table.insertRow(1)
    from PyQt6.QtWidgets import QTableWidgetItem
    table.setItem(1, 0, QTableWidgetItem("Sex"))
    table.setItem(1, 1, QTableWidgetItem("Female, Male"))

    assert list(tab.meanings()) == [("Genotype", "Ctrl"), ("Genotype", "Treated"),
                                    ("Genotype", "Rescue"), ("Sex", "Female"),
                                    ("Sex", "Male")]
    assert tab.meanings()[("Genotype", "Ctrl")] == "w1118"
    assert tab.kinds() == {"Genotype": "genotype", "Sex": ""}
    tab.meaning_edit("Sex", "Female").setText("virgin")
    tab.kind_combo("Sex").setCurrentIndex(tab.kind_combo("Sex").findData("sex"))
    assert window._dump_config()[KEY] == {"factors": {
        "Genotype": {"kind": "genotype", "levels": {"Ctrl": "w1118"}},
        "Sex": {"kind": "sex", "levels": {"Female": "virgin"}}}}


def test_t3_a_renamed_factor_drops_its_kind_and_its_rows(tmp_path, open_editor):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)

    window._global_tab.factors_table.item(0, 0).setText("Line")

    assert window._about_tab.kinds() == {"Line": ""}
    assert "factors" not in window._dump_config()[KEY]


def test_a_level_mapping_keeps_its_other_keys_when_its_meaning_changes(
        tmp_path, open_editor):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)
    tab = window._about_tab
    entry = FULL["factors"]["Genotype"]["levels"]["Ctrl"]

    tab.meaning_edit("Genotype", "Ctrl").setText(" the control cross ")
    tab.meaning_edit("Genotype", "Treated").setText("the silenced cross")
    levels = window._dump_config()[KEY]["factors"]["Genotype"]["levels"]
    assert levels["Ctrl"] == {**entry, "definition": "the control cross"}
    assert levels["Treated"] == "the silenced cross"         # text stays text

    tab.meaning_edit("Genotype", "Ctrl").clear()
    tab.meaning_edit("Genotype", "Treated").clear()
    levels = window._dump_config()[KEY]["factors"]["Genotype"]["levels"]
    assert levels == {"Ctrl": {k: v for k, v in entry.items() if k != "definition"}}


def test_what_the_tab_never_showed_is_kept_when_another_field_is_edited(
        tmp_path, open_editor, dialogs):
    """A level or factor the design does not declare has no row. It is not
    this tab's to drop: it stays, and the save says it is undeclared."""
    section = full_about()
    section["factors"]["Genotype"]["levels"]["Gone"] = {"definition": "an earlier level",
                                                        "term": "GEN-0001"}
    section["factors"]["Genotype"]["x_factor"] = True
    section["factors"]["Sex"] = {"kind": "sex", "levels": {"F": "virgin"}}
    member = recording(tmp_path / "M1", about=section)
    window = open_editor(member)
    tab = window._about_tab

    assert list(tab.meanings()) == [("Genotype", "Ctrl"), ("Genotype", "Treated")]
    tab.title.setText("A new title")
    window._save()

    saved = read_config(member)[KEY]
    assert saved == {**section, "title": "A new title"}
    [said] = dialogs["information"]
    assert "'Gone'" in said[2] and "'Sex'" in said[2]


def test_other_values_the_form_has_no_widget_for_are_kept(tmp_path, open_editor):
    section = {"sex": ["Female", "hermaphrodite"],
               "protocol": {"name": "P", "x_doi": "10.1/x"},
               "factors": {"Genotype": {"kind": "strain"}}}
    member = recording(tmp_path / "M1", about=section)
    window = open_editor(member)
    tab = window._about_tab

    assert tab.sex_checks["female"].isChecked()
    assert "hermaphrodite" in tab.other_sex_label.text()
    assert tab.kinds() == {"Genotype": "strain"}             # shown as the file has it
    tab.sex_checks["male"].setChecked(True)

    assert window._dump_config()[KEY] == {
        "sex": ["female", "male", "hermaphrodite"],
        "protocol": {"name": "P", "x_doi": "10.1/x"},
        "factors": {"Genotype": {"kind": "strain"}}}


def test_opening_a_second_file_never_keeps_the_first_files_description(
        tmp_path, open_editor):
    first = recording(tmp_path / "First", about=full_about())
    second = recording(tmp_path / "Second")
    window = open_editor(first)
    window._about_tab.meaning_edit("Genotype", "Ctrl").setText("typed, never saved")

    window._load_path(second / "tracking_config.yaml")

    tab = window._about_tab
    assert tab.title.text() == "" and tab.notes.toPlainText() == ""
    assert tab.run_value.isHidden() and tab.kinds() == {"Genotype": ""}
    assert set(tab.meanings().values()) == {""}
    assert KEY not in window._dump_config() and not is_dirty(window)


# ---- Start from another recording… (A6) ------------------------------------

MINE = {"run": "R-20261122-test-02", "title": "Replicate 2", "purpose": "Mine.",
        "notes": "My notes.", "experimenter": "someone", "organism": "D. simulans",
        "x_mine": True}


def test_a6_starting_from_an_earlier_recording_fills_the_shared_part_only(
        tmp_path, open_editor):
    earlier = recording(tmp_path / "Rep1", about=full_about())
    member = recording(tmp_path / "Rep2", about=copy.deepcopy(MINE))
    window = open_editor(member)
    tab = window._about_tab

    assert tab.start_from_file(earlier / "tracking_config.yaml")

    ## Filled in: who ran it, the series, the flies, the protocol, the factors.
    assert tab.experimenter.text() == "atester"
    assert tab.series.text() == "valence-mbon10-2026"
    assert tab.organism.text() == "Drosophila melanogaster"
    assert tab.sex_checks["female"].isChecked()
    assert tab.age_at_start.text() == "5-7 days"
    assert tab.protocol_edits["link"].text() == "https://example.org/valence"
    assert tab.kinds() == {"Genotype": "genotype"}
    assert tab.meanings()[("Genotype", "Ctrl")] == "w1118 > UAS-GtACR1"
    ## Left as they were: what is this recording's alone.
    assert tab.title.text() == "Replicate 2"
    assert tab.purpose.toPlainText() == "Mine."
    assert tab.notes.toPlainText() == "My notes."
    assert tab.run_value.text() == "R-20261122-test-02"

    started = window._dump_config()[KEY]
    for key in about_mod.SHARED_KEYS:
        assert started[key] == FULL[key], key                # mapping keys and all
    assert {key: started[key] for key in ("run", "title", "purpose", "notes", "x_mine")} \
        == {key: MINE[key] for key in ("run", "title", "purpose", "notes", "x_mine")}
    assert "x_lab" not in started                            # the earlier one's own
    assert "Rep1" in tab.start_note.text()
    assert read_config(earlier)[KEY] == FULL                 # only ever read
    assert is_dirty(window)                                  # nothing is saved until Save


def test_a6_the_button_asks_for_a_config_and_starts_from_it(
        tmp_path, open_editor, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    earlier = recording(tmp_path / "Rep1", about=full_about())
    member = recording(tmp_path / "Rep2")
    window = open_editor(member)
    asked = []

    def choose(_parent, caption, start, _filter):
        asked.append((caption, start))
        return str(earlier / "tracking_config.yaml"), ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(choose))
    window._about_tab.start_button.click()

    [(caption, start)] = asked
    assert "another recording" in caption
    assert start == str(tmp_path)                            # beside this recording's folder
    assert window._dump_config()[KEY] == SHARED

    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    window._about_tab.start_button.click()                   # cancelled: nothing changes
    assert window._dump_config()[KEY] == SHARED


def test_a6_only_the_factors_and_levels_this_config_declares_are_taken(
        tmp_path, open_editor):
    section = full_about()
    section["factors"]["Sex"] = {"kind": "sex", "levels": {"Female": "virgin"}}
    earlier = recording(tmp_path / "Rep1", about=section)
    member = recording(tmp_path / "Rep2", extra_global={
        "experimental_design_factors": {"Genotype": ["Ctrl", "Rescue"]}})
    window = open_editor(member)

    assert window._about_tab.start_from_file(earlier / "tracking_config.yaml")

    assert window._dump_config()[KEY]["factors"] == {"Genotype": {
        "kind": "genotype",
        "levels": {"Ctrl": FULL["factors"]["Genotype"]["levels"]["Ctrl"]}}}
    assert config_validation.config_warnings(window._dump_config()) == []


def test_a6_a_field_the_earlier_recording_lacks_keeps_this_recordings(
        tmp_path, open_editor):
    earlier = recording(tmp_path / "Rep1", about={"series": "theirs", "title": "Theirs"})
    member = recording(tmp_path / "Rep2", about=copy.deepcopy(MINE))
    window = open_editor(member)

    assert window._about_tab.start_from_file(earlier / "tracking_config.yaml")
    assert window._dump_config()[KEY] == {**MINE, "series": "theirs"}


@pytest.mark.parametrize("earlier_about, said", [
    (None, "nothing to start from"),
    ({"title": "Only its own", "notes": "Only its own."}, "nothing to start from"),
    ("just some text", "nothing to start from"),
], ids=["no section", "nothing shared", "not a mapping"])
def test_a6_a_recording_with_nothing_to_share_changes_nothing(
        tmp_path, open_editor, earlier_about, said):
    earlier = recording(tmp_path / "Rep1", about=earlier_about)
    member = recording(tmp_path / "Rep2", about=copy.deepcopy(MINE))
    window = open_editor(member)

    assert not window._about_tab.start_from_file(earlier / "tracking_config.yaml")
    assert said in window._about_tab.start_note.text()
    assert window._dump_config()[KEY] == MINE and not is_dirty(window)


def test_a6_this_recordings_own_config_and_an_unreadable_one_are_refused(
        tmp_path, open_editor):
    member = recording(tmp_path / "Rep2", about=copy.deepcopy(MINE))
    window = open_editor(member)
    tab = window._about_tab

    assert not tab.start_from_file(member / "tracking_config.yaml")
    assert "own config" in tab.start_note.text()

    broken = tmp_path / "Broken" / "tracking_config.yaml"
    broken.parent.mkdir()
    broken.write_text("global: [oops\n", encoding="utf-8")
    assert not tab.start_from_file(broken)
    assert "Could not read" in tab.start_note.text()
    assert not tab.start_from_file(tmp_path / "Missing" / "tracking_config.yaml")
    assert "Could not read" in tab.start_note.text()
    assert window._dump_config()[KEY] == MINE


# ---------------------------------------------------------------------------
# A5: the file changed on disk while the editor had it open
# ---------------------------------------------------------------------------

@pytest.fixture
def disk_change(monkeypatch):
    """Answer the editor's changed-on-disk question by clicking the button
    whose label starts with ``choose`` — and record every time it is asked."""
    from pytrackinganalysis.apps import config_editor as ce

    asked: list[dict] = []
    answer = {"choose": "Cancel"}

    def _exec(box):
        labels = [button.text().replace("&", "") for button in box.buttons()]
        asked.append({"title": box.windowTitle(), "text": box.text(),
                      "detail": box.informativeText(), "buttons": labels,
                      "default": box.defaultButton().text().replace("&", "")})
        for button in box.buttons():
            if button.text().replace("&", "").startswith(answer["choose"]):
                button.click()
                return 0
        raise AssertionError(f"no {answer['choose']!r} button among {labels}")

    monkeypatch.setattr(ce.QMessageBox, "exec", _exec)
    return type("DiskChange", (), {"asked": asked, "answer": answer})


def edited_elsewhere(member) -> dict:
    """Rewrite *member*'s section the way a catalogue does once the recording
    is filed, and return it."""
    set_about(member, full_about())
    return full_about()


def test_a5_a_file_changed_on_disk_is_never_overwritten_silently(
        tmp_path, open_editor, dialogs, disk_change):
    member = recording(tmp_path / "M1", about={"title": "As first typed"})
    path = member / "tracking_config.yaml"
    window = open_editor(member)
    window._about_tab.purpose.setPlainText("Typed in this window.")

    on_disk = edited_elsewhere(member)
    written = path.read_bytes()
    window._save()                                           # answered: Cancel

    [question] = disk_change.asked
    assert "tracking_config.yaml" in question["text"]
    assert "changed on disk" in question["text"]
    assert "Sections that differ: about." in question["detail"]   # an informed choice
    assert sorted(question["buttons"]) == ["Cancel", "Overwrite", "Reload from disk"]
    assert question["default"] == "Cancel"                   # Enter writes nothing
    assert path.read_bytes() == written                      # nothing was written
    assert read_config(member)[KEY] == on_disk
    assert not dialogs["information"]                        # and nothing claims a save
    assert window._about_tab.purpose.toPlainText() == "Typed in this window."

    window._save()                                           # it asks again, every time
    assert len(disk_change.asked) == 2 and path.read_bytes() == written


def test_a5_reloading_shows_what_is_on_disk_and_writes_nothing(
        tmp_path, open_editor, dialogs, disk_change):
    member = recording(tmp_path / "M1", about={"title": "As first typed"})
    path = member / "tracking_config.yaml"
    window = open_editor(member)
    window._about_tab.purpose.setPlainText("Typed in this window.")
    edited_elsewhere(member)
    written = path.read_bytes()

    disk_change.answer["choose"] = "Reload"
    window._save()

    tab = window._about_tab
    assert path.read_bytes() == written
    assert tab.title.text() == FULL["title"] and tab.purpose.toPlainText() == FULL["purpose"]
    assert tab.run_value.text() == FULL["run"]
    assert not is_dirty(window) and not dialogs["information"]

    tab.notes.setPlainText("Typed after the reload.")
    window._save()                                           # now it is this window's turn
    assert len(disk_change.asked) == 1
    assert read_config(member)[KEY] == {**FULL, "notes": "Typed after the reload."}


def test_a5_overwriting_is_the_persons_choice_and_is_asked_once(
        tmp_path, open_editor, dialogs, disk_change):
    member = recording(tmp_path / "M1", about={"title": "As first typed"})
    window = open_editor(member)
    window._about_tab.purpose.setPlainText("Typed in this window.")
    edited_elsewhere(member)

    disk_change.answer["choose"] = "Overwrite"
    window._save()

    mine = {"title": "As first typed", "purpose": "Typed in this window."}
    assert read_config(member)[KEY] == mine
    assert len(disk_change.asked) == 1 and len(dialogs["information"]) == 1

    window._about_tab.notes.setPlainText("And then some.")
    window._save()                                           # its own write is no change
    assert len(disk_change.asked) == 1
    assert read_config(member)[KEY] == {**mine, "notes": "And then some."}


def test_a5_a_file_nobody_else_touched_is_saved_without_a_question(
        tmp_path, open_editor, dialogs, disk_change):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)
    for note in ("One.", "Two."):
        window._about_tab.notes.setPlainText(note)
        window._save()
    assert disk_change.asked == []
    assert read_config(member)[KEY]["notes"] == "Two." and len(dialogs["information"]) == 2


def test_a5_any_other_section_changed_on_disk_asks_too(
        tmp_path, open_editor, dialogs, disk_change):
    """It is the file that is watched, not only the section: hypotheses
    written from the Project dialog, or a comment added by hand."""
    member = recording(tmp_path / "M1", about=full_about())
    path = member / "tracking_config.yaml"
    window = open_editor(member)

    config = read_config(member)
    config["global"]["facet_cutoffs"] = [1, 2]
    write_config(member, config)
    window._save()
    assert "Sections that differ: global." in disk_change.asked[-1]["detail"]

    disk_change.answer["choose"] = "Reload"
    window._save()
    path.write_text(path.read_text(encoding="utf-8") + "# a note to self\n",
                    encoding="utf-8")
    disk_change.answer["choose"] = "Cancel"
    window._save()
    assert "comments or layout" in disk_change.asked[-1]["detail"]
    assert path.read_text(encoding="utf-8").endswith("# a note to self\n")


def test_a5_a_file_that_is_gone_is_said_to_be_gone(
        tmp_path, open_editor, dialogs, disk_change):
    member = recording(tmp_path / "M1", about=full_about())
    path = member / "tracking_config.yaml"
    window = open_editor(member)
    path.rename(member / "moved_away.yaml")

    window._save()                                           # Cancel
    [question] = disk_change.asked
    assert "no longer on disk" in question["text"]
    assert not any(label.startswith("Reload") for label in question["buttons"])
    assert not path.exists()

    disk_change.answer["choose"] = "Save"
    window._save()
    assert read_config(member)[KEY] == FULL


def test_a5_a_file_that_appeared_since_the_editor_opened_is_a_change(
        tmp_path, open_editor, dialogs, disk_change):
    """Opened on a folder with no config yet; another program wrote one."""
    folder = tmp_path / "Lone"
    folder.mkdir()
    window = open_editor(folder)
    window._about_tab.title.setText("Typed before the file existed")
    elsewhere = recording(tmp_path / "Elsewhere", about=full_about())
    (folder / "tracking_config.yaml").write_bytes(
        (elsewhere / "tracking_config.yaml").read_bytes())

    window._save()                                           # Cancel
    assert len(disk_change.asked) == 1
    assert read_config(folder)[KEY] == FULL

    disk_change.answer["choose"] = "Reload"
    window._save()
    assert window._about_tab.title.text() == FULL["title"]


def test_a5_saving_as_a_new_file_asks_nothing(tmp_path, open_editor, dialogs,
                                              disk_change):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)
    window._write(tmp_path / "copy.yaml")
    assert disk_change.asked == [] and (tmp_path / "copy.yaml").is_file()


# ---- the Script Editor is this window's own child ---------------------------

def script_editor_saved(window, member, scripts) -> None:
    """What the Script Editor does on save: splice the scripts into the file
    as it is now, then tell the window that opened it."""
    from pytrackinganalysis.script_editor.runner import save_scripts

    path = member / "tracking_config.yaml"
    save_scripts(path, scripts)
    window._reload_external_changes(str(path))


def test_a5_the_script_editors_own_save_is_not_a_change_to_ask_about(
        tmp_path, open_editor, dialogs, disk_change):
    member = recording(tmp_path / "M1", about=full_about())
    window = open_editor(member)
    window._about_tab.notes.setPlainText("Typed in this window.")

    script_editor_saved(window, member, [copy.deepcopy(SCRIPT)])
    window._save()

    assert disk_change.asked == []
    saved = read_config(member)
    assert saved["scripts"] == [SCRIPT]
    assert saved[KEY] == {**FULL, "notes": "Typed in this window."}


def test_a5_a_rewrite_hidden_behind_the_script_editors_save_still_asks(
        tmp_path, open_editor, dialogs, disk_change):
    """The section is rewritten on disk, then the Script Editor saves over
    that file. The Script Editor keeps the new section (A4), and this window
    must still not write its stale one over it unasked."""
    member = recording(tmp_path / "M1", about={"title": "As first typed"})
    window = open_editor(member)
    window._about_tab.purpose.setPlainText("Typed in this window.")

    on_disk = edited_elsewhere(member)
    script_editor_saved(window, member, [copy.deepcopy(SCRIPT)])
    assert read_config(member)[KEY] == on_disk               # the child kept it
    window._save()                                           # Cancel

    assert len(disk_change.asked) == 1
    assert read_config(member)[KEY] == on_disk
    assert read_config(member)["scripts"] == [SCRIPT]
