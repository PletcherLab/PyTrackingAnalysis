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

import pytest
import yaml

from pytrackinganalysis import about as about_mod
from pytrackinganalysis import config_validation
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
