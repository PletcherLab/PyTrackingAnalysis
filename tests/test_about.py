# Mirrored from pyflic: tests/test_about.py at commit f6fb479, copied with the
# module it tests (pytrackinganalysis/about.py).  Only this note, the import
# and the word "pyflic" before the numbers of pyflic's ADRs differ.  Change
# the origin first, then copy it here whole (see tests/test_about_mirror.py).
"""``about:`` as a contract other tools share (pyflic ADR-0024, pyflic ADR-0026).

A catalogue files a recording from its folder and afterwards rewrites the
section to match its own record: it writes ``run:`` and, inside a level's
mapping, ``term:``, ``label:`` and ``role:``.  pyflic keeps all of them, edits
only a level's ``definition``, and lets a new recording start from an earlier
one's shared part.
"""

from __future__ import annotations

import copy

from pytrackinganalysis import about as about_mod

FULL = {
    "run": "R-20261121-scott-01",
    "title": "Progressive ratio, sucrose, replicate 1",
    "purpose": "Whether silencing MBON10 changes how hard flies work for sucrose.",
    "experimenter": "scott",
    "series": "pr-sucrose-2026",
    "organism": "Drosophila melanogaster",
    "sex": ["female"],
    "age_at_start": "5-7 days",
    "protocol": {"name": "FLIC progressive ratio", "version": "2"},
    "notes": "Lid 3 was replaced.",
    "factors": {
        "Genotype": {
            "kind": "genotype",
            "levels": {
                "MBON10": "R12C11-GAL4 > UAS-GtACR1",
                "Ctrl": {"definition": "w1118 > UAS-GtACR1", "term": "GEN-0012",
                         "label": "w1118 x UAS-GtACR1", "role": "reference",
                         "x_other": "kept"},
            },
        },
    },
    "x_lab": {"a": 1},
}


def test_run_is_written_first_and_kept_as_text():
    cleaned = about_mod.clean_about({"title": "T", "x_lab": {"a": 1}, "run": " R-20261121-scott-01 "})
    assert list(cleaned) == ["run", "title", "x_lab"]
    assert cleaned["run"] == "R-20261121-scott-01"
    assert about_mod.KEY_ORDER[0] == about_mod.RUN_KEY


def test_cleaning_keeps_every_key_of_a_level_mapping():
    cleaned = about_mod.clean_about(copy.deepcopy(FULL))
    assert cleaned == FULL
    assert list(cleaned)[-1] == "x_lab"                      # unknown keys follow, as written


def test_a_run_that_is_not_text_is_a_problem_not_an_error():
    problems = about_mod.about_problems({"run": ["R-1"]}, {})
    assert [where for where, _message in problems] == ["about.run"]


def test_a_level_meaning_set_on_text_stays_text():
    assert about_mod.with_meaning("old words", " new words ") == "new words"
    assert about_mod.with_meaning(None, "new words") == "new words"
    assert about_mod.with_meaning("old words", "  ") is None


def test_a_level_meaning_set_on_a_mapping_changes_only_its_definition():
    entry = FULL["factors"]["Genotype"]["levels"]["Ctrl"]
    changed = about_mod.with_meaning(entry, "the control cross")
    assert changed == {**entry, "definition": "the control cross"}
    assert list(changed)[0] == "definition"
    assert entry["definition"] == "w1118 > UAS-GtACR1"       # the entry given is not changed


def test_a_mapping_whose_meaning_is_cleared_keeps_its_other_keys():
    entry = {"definition": "words", "term": "GEN-0012"}
    assert about_mod.with_meaning(entry, "") == {"term": "GEN-0012"}
    assert about_mod.with_meaning({"definition": "words"}, "") is None


def test_a_level_term_is_read_only_from_a_mapping():
    levels = FULL["factors"]["Genotype"]["levels"]
    assert about_mod.level_term(levels["Ctrl"]) == "GEN-0012"
    assert about_mod.level_term(levels["MBON10"]) == ""
    assert about_mod.level_meaning(levels["Ctrl"]) == "w1118 > UAS-GtACR1"


def test_the_shared_part_leaves_out_what_is_this_recordings_alone():
    shared = about_mod.shared_about(FULL)
    assert list(shared) == ["experimenter", "series", "organism", "sex", "age_at_start",
                            "protocol", "factors"]
    assert shared["factors"] == FULL["factors"]              # mapping keys and all
    shared["factors"]["Genotype"]["levels"]["Ctrl"]["term"] = "changed"
    assert FULL["factors"]["Genotype"]["levels"]["Ctrl"]["term"] == "GEN-0012"   # a deep copy


def test_the_shared_part_can_be_limited_to_the_design_in_hand():
    shared = about_mod.shared_about(FULL, {"Genotype": ["MBON10", "Other"], "Sex": ["Male"]})
    assert shared["factors"] == {"Genotype": {"kind": "genotype",
                                              "levels": {"MBON10": "R12C11-GAL4 > UAS-GtACR1"}}}
    assert "factors" not in about_mod.shared_about(FULL, {"Light": ["On", "Off"]})


def test_starting_from_an_earlier_recording_takes_only_its_shared_part():
    mine = {"run": "R-20261122-scott-01", "title": "Replicate 2", "purpose": "Mine.",
            "notes": "My notes.", "experimenter": "someone", "x_mine": True}
    started = about_mod.start_from(mine, FULL)
    assert started["title"] == "Replicate 2"
    assert started["purpose"] == "Mine."
    assert started["notes"] == "My notes."
    assert started["run"] == "R-20261122-scott-01"
    assert started["x_mine"] is True
    assert "x_lab" not in started                            # the earlier one's own
    for key in about_mod.SHARED_KEYS:
        assert started[key] == FULL[key]


def test_starting_from_an_earlier_recording_never_brings_its_run_or_title():
    started = about_mod.start_from({}, FULL)
    assert set(started) == set(about_mod.SHARED_KEYS)


def test_a_field_the_earlier_recording_lacks_keeps_this_recordings():
    started = about_mod.start_from({"organism": "D. simulans", "series": "mine"},
                                   {"series": "theirs"})
    assert started == {"series": "theirs", "organism": "D. simulans"}


def _about(entry):
    return {"factors": {"Genotype": {"levels": {"Ctrl": entry}}}}


def test_members_with_one_term_describe_one_level_however_they_word_it():
    abouts = {"rep1": _about({"definition": "w1118 > UAS-GtACR1", "term": "GEN-0012"}),
              "rep2": _about({"definition": "the w1118 control", "term": "GEN-0012"})}
    assert about_mod.meaning_spread(abouts) == []


def test_members_with_different_terms_describe_a_level_differently():
    abouts = {"rep1": _about({"definition": "same words", "term": "GEN-0012"}),
              "rep2": _about({"definition": "same words", "term": "GEN-0031"})}
    lines = about_mod.meaning_spread(abouts)
    assert len(lines) == 1
    assert "GEN-0012" in lines[0] and "GEN-0031" in lines[0]
    assert "rep1" in lines[0] and "rep2" in lines[0]


def test_a_member_with_a_term_and_one_without_are_compared_by_their_words():
    same = {"rep1": _about({"definition": "w1118 > UAS-GtACR1", "term": "GEN-0012"}),
            "rep2": _about("w1118 > UAS-GtACR1")}
    assert about_mod.meaning_spread(same) == []
    different = {"rep1": _about({"definition": "w1118 > UAS-GtACR1", "term": "GEN-0012"}),
                 "rep2": _about("Canton-S")}
    assert len(about_mod.meaning_spread(different)) == 1


def test_a_member_that_says_nothing_about_a_level_is_not_a_difference():
    abouts = {"rep1": _about("w1118 > UAS-GtACR1"), "rep2": {}, "rep3": _about({"term": "GEN-0012"})}
    assert about_mod.meaning_spread(abouts) == []
