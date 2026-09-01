"""Tests for the ExperimentType hierarchy (Phase 0): registry, derivation,
Valence constraints, and the Custom (permissive) default."""

from __future__ import annotations

import pytest

from pytrackinganalysis import Parameters
from pytrackinganalysis import experiment_types as et


def _valence_config(rig="arena_max", **global_overrides):
    g = {"experiment_type": "Valence", "tracking_rig": rig}
    g.update(global_overrides)
    # Lay out the plate the rig actually has, so a well-formed config validates.
    # Unconstrained/invalid rigs (region check skipped) get a single stub region.
    from pytrackinganalysis import config_validation as _cv

    n = {"arena_max": 36, "colosseum": 24,
         "small_arena": 6}.get(_cv.normalize_rig(rig), 1)
    return {
        "global": g,
        "counting_regions": {
            "Light": {"alias": "Light, L"},
            "NoLight": {"alias": "NoLight, N"},
        },
        "tracking_regions": {
            f"T_{i}": {"experimental_factors": "control"} for i in range(n)
        },
    }


# ---- registry -------------------------------------------------------------

def test_registry_resolves_names_case_insensitively():
    assert isinstance(et.get_experiment_type("Valence"), et.ValenceExperimentType)
    assert isinstance(et.get_experiment_type("valence"), et.ValenceExperimentType)
    assert isinstance(et.get_experiment_type("VALENCE"), et.ValenceExperimentType)


def test_absent_type_is_custom():
    for value in (None, "", "  "):
        t = et.get_experiment_type(value)
        assert isinstance(t, et.CustomExperimentType)
        assert t.is_custom


def test_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown experiment_type"):
        et.get_experiment_type("Aggression")


def test_available_types_lists_custom_first():
    names = [t.name for t in et.available_experiment_types()]
    assert names[0] == "Custom"
    assert "Valence" in names


# ---- derivation (ADR-0001) ------------------------------------------------

def test_valence_derives_tracking_type_and_defaults_facets():
    t = et.get_experiment_type("Valence")
    g = {}  # minimal yaml: neither field present
    assert t.resolve_tracking_type(g) == Parameters.TrackingType.TWOCHOICETRACKER
    assert t.resolve_facet_cutoffs(g) == (10, 70)  # the default
    # Facets are a default, not owned — tracking_type and calibration are owned.
    assert t.owned_keys() == {"tracking_type", "fps", "mm_per_pixel"}


def test_valence_facets_are_an_overridable_default():
    t = et.get_experiment_type("Valence")
    # A facet_cutoffs in the yaml overrides the default and is NOT a conflict.
    assert t.resolve_facet_cutoffs({"facet_cutoffs": [15, 60]}) == (15, 60)
    assert t.validate(_valence_config(facet_cutoffs=[15, 60])) == []


def test_custom_reads_tracking_type_and_facets_from_yaml():
    t = et.get_experiment_type(None)
    g = {"tracking_type": "TWOCHOICECOUNTER", "facet_cutoffs": [5, 30]}
    assert t.resolve_tracking_type(g) == Parameters.TrackingType.TWOCHOICECOUNTER
    assert t.resolve_facet_cutoffs(g) == (5, 30)
    assert t.owned_keys() == set()


def test_valence_phase_labels():
    t = et.get_experiment_type("Valence")
    windows = [(0, 10), (10, 70), (70, float("inf"))]
    assert t.phase_labels_for(windows) == ["Acclimation", "Experiment", "Cooldown"]
    # Non-default cutoffs -> honest minute-range labels, not phase names.
    assert t.phase_labels_for([(0, 5), (5, float("inf"))]) == ["0–5", "5+"]


def test_yaml_facet_labels_override_the_type_defaults():
    t = et.get_experiment_type("Valence")
    windows = [(0, 10), (10, 70), (70, float("inf"))]
    g = {"facet_labels": ["Baseline", "Stimulus", "Recovery"]}
    assert t.phase_labels_for(windows, g) == ["Baseline", "Stimulus", "Recovery"]
    # Custom names also apply to non-default cutoffs when the count matches.
    assert t.phase_labels_for([(0, 5), (5, float("inf"))],
                              {"facet_labels": ["Before", "After"]}) == ["Before", "After"]
    # A count mismatch falls back rather than mislabelling phases.
    assert t.phase_labels_for(windows, {"facet_labels": ["Only", "Two"]}) \
        == ["Acclimation", "Experiment", "Cooldown"]


def test_custom_type_reads_facet_labels_from_yaml():
    t = et.get_experiment_type(None)
    windows = [(0, 5), (5, float("inf"))]
    assert t.phase_labels_for(windows) == ["0–5", "5+"]
    assert t.phase_labels_for(windows, {"facet_labels": ["Warmup", "Assay"]}) \
        == ["Warmup", "Assay"]


# ---- validation -----------------------------------------------------------

def test_valence_accepts_a_well_formed_config():
    t = et.get_experiment_type("Valence")
    assert t.validate(_valence_config()) == []
    assert t.validate(_valence_config(rig="colosseum")) == []


def test_valence_accepts_small_arena():
    t = et.get_experiment_type("Valence")
    assert t.validate(_valence_config(rig="small_arena")) == []
    assert t.validate(_valence_config(rig="smallarena")) == []   # spelling alias


def test_valence_small_arena_is_six_wells_with_no_flips():
    """One Small Arena unit per recording: exactly T_0..T_5, and no mirrored
    wells — every X multiplier is +1 (the Arena Max flip never applies)."""
    t = et.get_experiment_type("Valence")
    assert t.region_counts_for_rig("small_arena") == (6,)
    assert t.regions_for_rig("small_arena") == [f"T_{i}" for i in range(6)]
    cfg = t.build_config(rig="small_arena")
    regions = cfg["tracking_regions"]
    assert list(regions) == [f"T_{i}" for i in range(6)]
    assert all(r["x_location_multiplier"] == 1 for r in regions.values())
    assert list(cfg["counting_regions"]) == ["Light", "NoLight"]
    ## Calibration comes from the preset (0.056 mm/px, MSec timing): the
    ## config must not carry its own fps / mm_per_pixel.
    assert "fps" not in cfg["global"]
    assert "mm_per_pixel" not in cfg["global"]
    assert t.validate(cfg) == []
    ## The wrong plate size is refused, exactly as for the fixed rigs.
    cfg["tracking_regions"] = {f"T_{i}": {"experimental_factors": "",
                                          "x_location_multiplier": 1,
                                          "y_location_multiplier": 1}
                               for i in range(8)}
    assert any("6 tracking regions" in p for p in t.validate(cfg))


def test_valence_rejects_wrong_rig():
    t = et.get_experiment_type("Valence")
    problems = t.validate(_valence_config(rig="obscura"))
    assert any("tracking_rig" in p for p in problems)


def test_valence_requires_a_rig():
    t = et.get_experiment_type("Valence")
    problems = t.validate(_valence_config(rig=""))
    assert any("choose a tracking_rig" in p for p in problems)


def test_valence_rejects_conflicting_owned_fields():
    t = et.get_experiment_type("Valence")
    p1 = t.validate(_valence_config(tracking_type="TRACKER"))
    assert any("tracking_type is fixed" in p for p in p1)
    # facet_cutoffs is NOT owned for Valence (editable default) — not a conflict.
    assert t.validate(_valence_config(facet_cutoffs=[5, 20])) == []
    p3 = t.validate(_valence_config(mm_per_pixel=0.05))
    assert any("mm_per_pixel" in p for p in p3)


def test_valence_requires_light_nolight_in_order():
    t = et.get_experiment_type("Valence")
    cfg = _valence_config()
    cfg["counting_regions"] = {"NoLight": {"alias": "N"}, "Light": {"alias": "L"}}
    assert any("in that order" in p for p in t.validate(cfg))

    cfg2 = _valence_config()
    cfg2["counting_regions"] = {"Light": {"alias": "L"}, "Dark": {"alias": "D"}}
    assert any("Light" in p and "NoLight" in p for p in t.validate(cfg2))


def test_valence_requires_nonempty_aliases():
    t = et.get_experiment_type("Valence")
    cfg = _valence_config()
    cfg["counting_regions"]["Light"] = {"alias": ""}
    assert any("alias" in p for p in t.validate(cfg))


def test_valence_requires_tracking_regions():
    t = et.get_experiment_type("Valence")
    cfg = _valence_config()
    cfg["tracking_regions"] = {}
    assert any("tracking_regions" in p for p in t.validate(cfg))


def test_valence_colosseum_runs_at_eighteen_or_twenty_four_wells():
    """The Colosseum is run in two plate sizes. Both validate; a config built
    from scratch gets the 24-well plate."""
    from pytrackinganalysis import config_validation

    t = et.get_experiment_type("Valence")
    assert t.region_counts_for_rig("colosseum") == (24, 18)
    assert t.region_counts_for_rig("colloseum") == (24, 18)   # spelling alias
    # From scratch: 24.
    assert t.regions_for_rig("colosseum") == [f"T_{i}" for i in range(24)]
    assert len(t.build_config(rig="colosseum")["tracking_regions"]) == 24

    for count in (18, 24):
        cfg = t.build_config(rig="colosseum")
        cfg["tracking_regions"] = {
            f"T_{i}": {"experimental_factors": "",
                       "x_location_multiplier": 1,
                       "y_location_multiplier": 1}
            for i in range(count)}
        assert config_validation.validate_config(cfg) == [], count

    # A size the rig is not run in is still refused, and says both.
    cfg = t.build_config(rig="colosseum")
    cfg["tracking_regions"] = {
        f"T_{i}": {"experimental_factors": "",
                   "x_location_multiplier": 1,
                   "y_location_multiplier": 1}
        for i in range(20)}
    problems = config_validation.validate_config(cfg)
    assert problems and "18 or 24" in problems[0]

    # T_0..T_17 exactly — a partial plate of the right SIZE but wrong names
    # is not an 18-well Colosseum.
    cfg["tracking_regions"] = {
        f"T_{i}": {"experimental_factors": "",
                   "x_location_multiplier": 1,
                   "y_location_multiplier": 1}
        for i in range(6, 24)}
    assert config_validation.validate_config(cfg) != []


def test_arena_max_still_takes_one_plate_size_only():
    """One count stays one count — the message must not grow an 'or'."""
    from pytrackinganalysis import config_validation

    t = et.get_experiment_type("Valence")
    assert t.region_counts_for_rig("arena_max") == (36,)
    cfg = t.build_config(rig="arena_max")
    cfg["tracking_regions"] = {
        f"T_{i}": {"experimental_factors": "",
                   "x_location_multiplier": 1,
                   "y_location_multiplier": 1}
        for i in range(24)}
    problems = config_validation.validate_config(cfg)
    assert problems and "exactly 36 tracking regions" in problems[0]


def test_valence_regions_for_rig():
    t = et.get_experiment_type("Valence")
    assert t.regions_for_rig("arena_max") == [f"T_{i}" for i in range(36)]
    assert t.regions_for_rig("colosseum") == [f"T_{i}" for i in range(24)]
    assert t.regions_for_rig("colloseum") == [f"T_{i}" for i in range(24)]  # alias
    assert t.regions_for_rig("") is None
    assert et.get_experiment_type(None).regions_for_rig("arena_max") is None


def test_valence_enforces_exact_region_count_for_the_rig():
    t = et.get_experiment_type("Valence")
    # Correct plate: 36 for Max.
    cfg = _valence_config(rig="arena_max")
    cfg["tracking_regions"] = {f"T_{i}": {} for i in range(36)}
    assert t.validate(cfg) == []
    # Wrong count -> a problem.
    cfg["tracking_regions"] = {f"T_{i}": {} for i in range(24)}
    assert any("36 tracking regions" in p for p in t.validate(cfg))
    # Colosseum wants 24.
    cfg2 = _valence_config(rig="colosseum")
    cfg2["tracking_regions"] = {f"T_{i}": {} for i in range(24)}
    assert t.validate(cfg2) == []


def test_custom_validate_is_permissive():
    t = et.get_experiment_type(None)
    assert t.validate({"global": {"tracking_type": "TRACKER"}}) == []


# ---- scaffold / report ----------------------------------------------------

def test_valence_scaffold_is_shaped_but_incomplete():
    t = et.get_experiment_type("Valence")
    cfg = t.scaffold_config()
    assert cfg["global"]["experiment_type"] == "Valence"
    assert list(cfg["counting_regions"]) == ["Light", "NoLight"]
    # Rig blank on purpose → scaffold does not yet validate.
    assert any("tracking_rig" in p for p in t.validate(cfg))


def test_valence_build_config_max_flips_first_18_x_multipliers():
    t = et.get_experiment_type("Valence")
    cfg = t.build_config(rig="arena_max", facet_cutoffs=[10, 70],
                         factors={"feeding": ["Starved", "Control"]})
    g = cfg["global"]
    assert g["experiment_type"] == "Valence"
    assert g["tracking_rig"] == "arena_max"
    assert g["facet_cutoffs"] == [10, 70]
    assert g["experimental_design_factors"] == {"feeding": ["Starved", "Control"]}
    assert "tracking_type" not in g  # owned/derived, not written
    regions = cfg["tracking_regions"]
    assert len(regions) == 36
    assert regions["T_0"]["x_location_multiplier"] == -1
    assert regions["T_17"]["x_location_multiplier"] == -1
    assert regions["T_18"]["x_location_multiplier"] == 1
    assert regions["T_35"]["x_location_multiplier"] == 1
    assert all(r["y_location_multiplier"] == 1 for r in regions.values())
    assert cfg["counting_regions"]["Light"]["alias"].startswith("Light, Left1")
    # The built config must validate.
    from pytrackinganalysis import config_validation
    assert config_validation.validate_config(cfg) == []


def test_valence_build_config_colosseum_all_positive():
    t = et.get_experiment_type("Valence")
    cfg = t.build_config(rig="colosseum")
    regions = cfg["tracking_regions"]
    assert len(regions) == 24
    assert all(r["x_location_multiplier"] == 1 for r in regions.values())
    assert cfg["global"]["facet_cutoffs"] == [10, 70]  # default when not given


def test_valence_build_config_writes_default_facet_labels():
    t = et.get_experiment_type("Valence")
    g = t.build_config(rig="colosseum")["global"]
    assert g["facet_labels"] == ["Acclimation", "Experiment", "Cooldown"]
    from pytrackinganalysis import config_validation
    assert config_validation.validate_config(t.build_config(rig="colosseum")) == []


def test_valence_build_config_honours_wizard_facet_labels():
    t = et.get_experiment_type("Valence")
    g = t.build_config(rig="colosseum", facet_cutoffs=[10, 70],
                       facet_labels=["Dark", "Light on", "Dark again"])["global"]
    assert g["facet_labels"] == ["Dark", "Light on", "Dark again"]


def test_valence_build_config_drops_labels_that_cannot_name_every_phase():
    t = et.get_experiment_type("Valence")
    # Four windows from three cutoffs: neither the wizard's two names nor the
    # type's three fit, so no labels are written (minute ranges at runtime).
    g = t.build_config(rig="colosseum", facet_cutoffs=[5, 20, 40],
                       facet_labels=["A", "B"])["global"]
    assert "facet_labels" not in g


def test_custom_build_config_writes_facet_labels_only_with_cutoffs():
    t = et.get_experiment_type(None)
    g = t.build_config(tracking_type="TRACKER", rig="small_arena",
                       facet_cutoffs=[5, 30], facet_labels=["A", "B", "C"])["global"]
    assert g["facet_labels"] == ["A", "B", "C"]
    g2 = t.build_config(tracking_type="TRACKER", rig="small_arena",
                        facet_labels=["A", "B", "C"])["global"]
    assert "facet_cutoffs" not in g2 and "facet_labels" not in g2


def test_custom_build_config_writes_tracking_type():
    t = et.get_experiment_type(None)
    cfg = t.build_config(tracking_type="TWOCHOICECOUNTER", rig="small_arena",
                         facet_cutoffs=[5, 30])
    g = cfg["global"]
    assert "experiment_type" not in g            # Custom has no type key
    assert g["tracking_type"] == "TWOCHOICECOUNTER"
    assert g["tracking_rig"] == "small_arena"
    assert g["facet_cutoffs"] == [5, 30]


def test_report_sections_hook(monkeypatch):
    # Custom: no type-specific report blocks. Valence: delegates to the
    # report_figures builder (matplotlib stays out of experiment_types).
    assert et.get_experiment_type(None).report_sections(object()) == []

    import pytrackinganalysis.report_figures as rf
    sentinel = [object()]
    monkeypatch.setattr(rf, "build_valence_sections", lambda exp: sentinel)
    assert et.get_experiment_type("Valence").report_sections(object()) is sentinel


def test_report_title_and_intro():
    assert et.get_experiment_type("Valence").report_title() == "Valence Experiment Report"
    assert et.get_experiment_type("Valence").report_intro()
    assert et.get_experiment_type(None).report_title() == "Tracking Analysis Report"
    assert et.get_experiment_type(None).report_intro() is None


# ---- config_validation integration (Phase 4) ------------------------------

def test_validate_config_consults_the_type():
    from pytrackinganalysis import config_validation
    assert config_validation.validate_config(_valence_config()) == []
    assert config_validation.validate_config(_valence_config(rig="colosseum")) == []
    bad = config_validation.validate_config(_valence_config(rig="obscura"))
    assert any("tracking_rig" in p for p in bad)


def test_validate_config_reports_unknown_experiment_type():
    cfg = _valence_config()
    cfg["global"]["experiment_type"] = "Nope"
    assert any("Unknown experiment_type" in p
               for p in __import__("pytrackinganalysis.config_validation",
                                   fromlist=["x"]).validate_config(cfg))


def test_custom_config_validation_unchanged():
    # A two-choice Custom config with only one counting group still trips the
    # generic 'exactly two' rule (unchanged behaviour).
    from pytrackinganalysis import config_validation
    cfg = {"global": {"tracking_type": "TWOCHOICECOUNTER", "tracking_rig": "small_arena"},
           "counting_regions": {"Light": {"alias": "L"}},
           "tracking_regions": {"T_0": {}}}
    assert any("exactly two" in p for p in config_validation.validate_config(cfg))


# ---- Experiment fail-hard-at-load (Phase 1) -------------------------------

def _exp_stub(config, exp_type_name):
    """An Experiment with just the attributes _validate_config touches."""
    from pytrackinganalysis.Experiment import Experiment
    exp = Experiment.__new__(Experiment)
    exp.config = config
    exp.config_path = "tracking_config.yaml"
    exp.experiment_type = et.get_experiment_type(exp_type_name)
    return exp


def test_typed_experiment_fails_hard_on_bad_config():
    exp = _exp_stub(_valence_config(rig="obscura"), "Valence")
    with pytest.raises(ValueError, match="Valence Experiment configuration"):
        exp._validate_config()


def test_typed_experiment_accepts_valid_config():
    _exp_stub(_valence_config(), "Valence")._validate_config()  # must not raise


def test_custom_experiment_warns_but_does_not_raise(capsys):
    cfg = {"global": {"tracking_type": "TWOCHOICECOUNTER", "tracking_rig": "small_arena"},
           "counting_regions": {"Light": {"alias": "L"}},  # only one -> a problem
           "tracking_regions": {"T_0": {}}}
    _exp_stub(cfg, None)._validate_config()  # lenient: no raise
    assert "Warning" in capsys.readouterr().out


# ---- Low-Transition Exclusion (ADR-0003) ----------------------------------

def test_min_transitions_default_override_and_off():
    t = et.get_experiment_type("Valence")
    assert t.resolve_min_transitions({}) == 5
    assert t.resolve_min_transitions({"min_transitions": 8}) == 8
    assert t.resolve_min_transitions({"min_transitions": 0}) == 0
    # Custom has no exclusion criterion at all, even with the key set.
    assert et.get_experiment_type(None).resolve_min_transitions(
        {"min_transitions": 3}) is None


def test_valence_validates_min_transitions():
    t = et.get_experiment_type("Valence")
    assert t.validate(_valence_config(min_transitions=5)) == []
    assert t.validate(_valence_config(min_transitions=0)) == []
    for bad in (-1, 2.5, "many"):
        problems = t.validate(_valence_config(min_transitions=bad))
        assert any("min_transitions" in p for p in problems), bad


def test_valence_scaffold_and_build_config_write_min_transitions():
    t = et.get_experiment_type("Valence")
    assert t.scaffold_config()["global"]["min_transitions"] == 5
    assert t.build_config(rig="arena_max")["global"]["min_transitions"] == 5
    assert t.build_config(rig="arena_max",
                          min_transitions=7)["global"]["min_transitions"] == 7


def test_valence_manifest_includes_excluded_csv():
    assert "_Excluded.csv" in et.get_experiment_type("Valence").output_manifest()


class _ExclusionArena:
    """Just enough arena for compute_exclusions: records the window asked for."""

    def __init__(self, summary):
        self._summary = summary
        self.calls = []

    def summarize(self, range_minutes=(0, 0), include_excluded=False, **kw):
        self.calls.append((tuple(range_minutes), include_excluded))
        return self._summary.copy()


def _exclusion_exp(summary, global_cfg=None, facet_cutoffs=(10, 70)):
    from types import SimpleNamespace
    return SimpleNamespace(config={"global": dict(global_cfg or {})},
                           facet_cutoffs=facet_cutoffs,
                           arena=_ExclusionArena(summary))


def _exclusion_summary():
    import pandas as pd
    return pd.DataFrame({
        "Name": ["a", "b", "c", "d"],
        "TrackingRegion": ["T_0", "T_1", "T_2", "T_3"],
        "Treatment": ["x", "x", "y", "y"],
        "Transitions": [2, 7, float("nan"), 5],
    })


def test_valence_compute_exclusions_below_threshold_and_na():
    t = et.get_experiment_type("Valence")
    exp = _exclusion_exp(_exclusion_summary())
    excluded = t.compute_exclusions(exp)
    # 2 < 5 excluded; NA (no data in the window) excluded; 7 and exactly 5 kept.
    assert list(excluded["Name"]) == ["a", "c"]
    assert excluded.attrs["min_transitions"] == 5
    assert excluded.attrs["window"] == (10, 70)   # the Primary Phase
    assert excluded.attrs["phase_label"] == "Experiment"
    # Computed over the unfiltered population.
    assert exp.arena.calls == [((10, 70), True)]


def test_valence_compute_exclusions_off_and_custom_none():
    t = et.get_experiment_type("Valence")
    exp = _exclusion_exp(_exclusion_summary(), {"min_transitions": 0})
    excluded = t.compute_exclusions(exp)
    assert len(excluded) == 0
    assert excluded.attrs["min_transitions"] == 0
    assert exp.arena.calls == []  # off -> no summary computed
    assert et.get_experiment_type(None).compute_exclusions(exp) is None


def test_valence_compute_exclusions_primary_window():
    t = et.get_experiment_type("Valence")
    # Non-default cutoffs: Primary Phase is still window index 1.
    exp = _exclusion_exp(_exclusion_summary(), facet_cutoffs=(5, 20, 40))
    excluded = t.compute_exclusions(exp)
    assert excluded.attrs["window"] == (5, 20)
    # A single window (one facet would need no cutoffs) -> the only window.
    exp2 = _exclusion_exp(_exclusion_summary(), facet_cutoffs=None)
    excluded2 = t.compute_exclusions(exp2)
    assert excluded2.attrs["window"] == (0, 0)
    assert excluded2.attrs["phase_label"] == "whole recording"


def test_arena_drops_excluded_rows_on_the_way_out():
    import pandas as pd

    from pytrackinganalysis import Arena as ArenaModule

    arena = ArenaModule.Arena.__new__(ArenaModule.Arena)
    arena.computed_summaries = {}
    arena.excluded_names = set()
    df = pd.DataFrame({"Name": ["a", "b"], "Transitions": [1, 9]})
    arena.computed_summaries[(0, 0)] = df

    arena.set_excluded_trackers(["a"])
    assert list(arena.summarize()["Name"]) == ["b"]
    # The audit path still sees everyone; the cache itself stays unfiltered.
    assert list(arena.summarize(include_excluded=True)["Name"]) == ["a", "b"]
    assert list(arena.computed_summaries[(0, 0)]["Name"]) == ["a", "b"]


# ---- Low-Movement Flag ------------------------------------------------------

def test_min_movement_default_override_and_off():
    t = et.get_experiment_type("Valence")
    assert t.resolve_min_movement({}) == 140.0
    assert t.resolve_min_movement({"min_movement": 120}) == 120.0
    assert t.resolve_min_movement({"min_movement": 0}) == 0.0
    assert et.get_experiment_type(None).resolve_min_movement(
        {"min_movement": 100}) is None


def test_valence_validates_min_movement():
    t = et.get_experiment_type("Valence")
    assert t.validate(_valence_config(min_movement=140)) == []
    assert t.validate(_valence_config(min_movement=120.5)) == []  # floats OK
    assert t.validate(_valence_config(min_movement=0)) == []
    for bad in (-1, "slow"):
        problems = t.validate(_valence_config(min_movement=bad))
        assert any("min_movement" in p for p in problems), bad


def test_valence_configs_write_min_movement():
    t = et.get_experiment_type("Valence")
    assert t.scaffold_config()["global"]["min_movement"] == 140
    assert t.build_config(rig="arena_max")["global"]["min_movement"] == 140
    assert t.build_config(rig="arena_max",
                          min_movement=120.5)["global"]["min_movement"] == 120.5


def _movement_summary(values):
    import pandas as pd
    n = len(values)
    return pd.DataFrame({
        "Name": [f"fly{i}" for i in range(n)],
        "TrackingRegion": [f"T_{i}" for i in range(n)],
        "Treatment": ["x"] * n,
        "TotalDistancePerMin": values,
    })


def test_valence_movement_flags_below_threshold_and_na():
    t = et.get_experiment_type("Valence")
    # 100 flagged; exactly 140 NOT flagged ("less than"); NA flagged; 200 kept.
    exp = _exclusion_exp(_movement_summary([100.0, 140.0, float("nan"), 200.0]))
    flagged = t.compute_movement_flags(exp)
    assert list(flagged["Name"]) == ["fly0", "fly2"]
    attrs = flagged.attrs
    assert attrs["min_movement"] == 140.0
    assert attrs["window"] == (0, 10)          # the FIRST facet window
    assert attrs["phase_label"] == "Acclimation"
    assert attrs["n_total"] == 4
    # Exactly 50% flagged is NOT an experiment-level issue (strictly >50%).
    assert attrs["fraction"] == 0.5 and attrs["experiment_flagged"] is False
    # Computed on the analysis population (no include_excluded).
    assert exp.arena.calls == [((0, 10), False)]


def test_valence_movement_flags_experiment_level():
    t = et.get_experiment_type("Valence")
    exp = _exclusion_exp(_movement_summary([100.0, 139.9, float("nan"), 200.0]))
    flagged = t.compute_movement_flags(exp)
    assert len(flagged) == 3
    assert flagged.attrs["experiment_flagged"] is True


def test_valence_movement_flags_off_and_custom_none():
    t = et.get_experiment_type("Valence")
    exp = _exclusion_exp(_movement_summary([100.0]), {"min_movement": 0})
    flagged = t.compute_movement_flags(exp)
    assert len(flagged) == 0
    assert flagged.attrs["min_movement"] == 0.0
    assert flagged.attrs["experiment_flagged"] is False
    assert exp.arena.calls == []
    assert et.get_experiment_type(None).compute_movement_flags(exp) is None
