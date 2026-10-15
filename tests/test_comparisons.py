"""Factor Comparisons, Directional Hypotheses and the Valence headline
statistics (ADR-0017): what is compared, which way a test is sided, how the
design enforces predictions, and that Stats.txt, the report and a figure's
bracket all show the same p."""

from __future__ import annotations

import copy
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pandas as pd
import pytest
import yaml

from pytrackinganalysis import comparisons as C
from pytrackinganalysis import config_validation
from pytrackinganalysis import experiment_types as et
from pytrackinganalysis import project as prj
from pytrackinganalysis import pubfigures as pf

PHASES = ["Acclimation", "Experiment", "Cooldown"]
FACTORS = {"sex": ["Female", "Male"], "genotype": ["WT", "Mutant"]}


def _tidy(effect=0.4, n=6, seed=0, replicates=None, factors=True):
    """Sex × Genotype flies in three phases; mutants prefer Light less."""
    rng = np.random.default_rng(seed)
    rows = []
    for sex in ("Female", "Male"):
        for gen in ("WT", "Mutant"):
            label = f"{sex}, {gen}" if factors else gen
            for i in range(n):
                for phase in PHASES:
                    row = {"Treatment": label, "Phase": phase,
                           "Value": rng.normal(0.4 if gen == "WT" else 0.4 - effect,
                                               0.1)}
                    if replicates:
                        row["Experiment"] = f"Rep{i % replicates}"
                    rows.append(row)
    df = pd.DataFrame(rows)
    df["Phase"] = pd.Categorical(df["Phase"], PHASES, ordered=True)
    return df


def _design(hypotheses=None, factors=FACTORS):
    g = {"experimental_design_factors": copy.deepcopy(factors)}
    if hypotheses is not None:
        g["hypotheses"] = hypotheses
    return C.Design.from_global(g, PHASES)


# ---- Directional Hypotheses -------------------------------------------------

def test_parse_hypotheses_accepts_either_direction_and_rejects_nonsense():
    hyps, problems = C.parse_hypotheses(
        ["Mutant < WT", {"expect": "WT > Mutant", "phases": "Experiment, Cooldown"},
         {"expect": "chr > Indifference"}])
    assert not problems
    assert (hyps[0].low, hyps[0].high, hyps[0].phases) == ("Mutant", "WT", None)
    assert (hyps[1].low, hyps[1].high) == ("Mutant", "WT")
    assert hyps[1].phases == ("Experiment", "Cooldown")
    assert (hyps[2].low, hyps[2].high) == (C.INDIFFERENCE, "chr")

    _, problems = C.parse_hypotheses(
        ["Mutant", {"expect": "A < B < C"}, {"expect": "A < A"},
         {"expect": "A < B", "tails": 1}, {"expect": "A < B", "phases": []}])
    assert len(problems) == 5
    _, problems = C.parse_hypotheses({"expect": "A < B"})
    assert problems == ["'hypotheses' must be a list of {expect, phases} entries"]


def test_validate_hypotheses_against_the_design():
    ok = C.validate_hypotheses(
        [{"expect": "Mutant < WT", "phases": ["Experiment"]}], FACTORS, PHASES)
    assert ok == []
    problems = C.validate_hypotheses(
        ["Mutant < Male",                                  # two factors
         "Rescue < WT",                                    # unknown level
         {"expect": "Mutant < WT", "phases": ["Persistence"]},   # no such phase
         "WT > Mutant"],                                   # same pair twice
        FACTORS, PHASES)
    text = " | ".join(problems)
    assert "levels of different factors" in text
    assert "'Rescue' is not a level" in text
    assert "phase 'Persistence' does not exist" in text
    assert "predicts the same pair" in text
    # A single treatment is predicted against indifference.
    assert C.validate_hypotheses(["WT > indifference"], {"genotype": ["WT"]},
                                 PHASES) == []


def test_names_holding_the_comparison_sign():
    # Fly genotypes are written Gal4>UAS: spaces mark the comparison sign.
    genotypes = {"Genotype": ["WCS>Chrimson", "Nanchung>Chrimson"]}
    hyps, problems = C.parse_hypotheses(["Nanchung>Chrimson > WCS>Chrimson"])
    assert not problems
    assert (hyps[0].low, hyps[0].high) == ("WCS>Chrimson", "Nanchung>Chrimson")
    assert C.validate_hypotheses(["Nanchung>Chrimson > WCS>Chrimson"],
                                 genotypes, PHASES) == []
    _, problems = C.parse_hypotheses(["Nanchung>Chrimson>WCS>Chrimson"])
    assert "put spaces around the comparison sign" in problems[0]
    assert C.normalize_expect("Mutant<WT") == "Mutant < WT"


def test_canonical_hypotheses_ignore_order_spacing_and_direction():
    a = [{"expect": "Mutant<WT"}, "Female > Male"]
    b = ["Male < Female", {"expect": "WT > Mutant"}]
    assert C.canonical_hypotheses(a) == C.canonical_hypotheses(b)
    assert C.canonical_hypotheses(None) == C.canonical_hypotheses([]) == []
    assert C.canonical_hypotheses(["Mutant < WT"]) != \
        C.canonical_hypotheses([{"expect": "Mutant < WT", "phases": ["Cooldown"]}])


# ---- Factor Comparisons -------------------------------------------------------

def test_two_factors_are_compared_within_strata_never_across():
    treatments = ["Female, WT", "Female, Mutant", "Male, WT", "Male, Mutant"]
    plans, notes = C.plan(treatments, FACTORS)
    assert not notes
    labels = [(p.label, tuple(p.groups)) for p in plans]
    assert labels == [
        ("sex within WT", ("Female, WT", "Male, WT")),
        ("sex within Mutant", ("Female, Mutant", "Male, Mutant")),
        ("genotype within Female", ("Female, WT", "Female, Mutant")),
        ("genotype within Male", ("Male, WT", "Male, Mutant")),
    ]
    pairs = {frozenset(p.groups) for p in plans}
    assert frozenset({"Female, WT", "Male, Mutant"}) not in pairs   # diagonal


def test_one_varying_factor_compares_the_treatments_flat():
    # Sex declared but constant: the genotype levels are the comparison.
    plans, _ = C.plan(["Female, WT", "Female, Mutant", "Female, Rescue"],
                      {"sex": ["Female"], "genotype": ["WT", "Mutant", "Rescue"]})
    assert len(plans) == 1 and plans[0].factor is None
    assert plans[0].names == ["WT", "Mutant", "Rescue"]
    # The design's level order sets A and B, not the order the data came in:
    # the first declared level is the reference, so B − A reads against it.
    plans, _ = C.plan(["Female, Nanchung>Chrimson", "Female, WCS>Chrimson"],
                      {"Sex": ["Male", "Female"],
                       "Genotype": ["WCS>Chrimson", "Nanchung>Chrimson"]})
    assert plans[0].names == ["WCS>Chrimson", "Nanchung>Chrimson"]
    # No factors declared: the labels themselves, in data order.
    plans, _ = C.plan(["b", "a"], {})
    assert plans[0].names == ["b", "a"]
    # Labels that do not name every factor fall back to flat, and say so.
    plans, notes = C.plan(["Female, WT", "Male, Mutant", "Male"], FACTORS)
    assert len(plans) == 1 and plans[0].factor is None and notes


def test_hypothesis_sides_only_its_metrics_and_phases():
    tidy = _tidy(effect=0.3)
    two = C.phase_results(tidy, "FinalPI", _design())
    one = C.phase_results(tidy, "FinalPI", _design(["Mutant < WT"]))
    dist = C.phase_results(tidy, "TotalDistancePerMin", _design(["Mutant < WT"]))

    def row(res, phase, label):
        return next(r for r in res["comparisons"]
                    if r["phase"] == phase and r["comparison"] == label)

    exp_two, exp_one = (row(two, "Experiment", "genotype within Female"),
                        row(one, "Experiment", "genotype within Female"))
    assert exp_one["side"] == "less" and exp_one["hypothesis"] == "Mutant < WT"
    assert exp_one["diff"] < 0                       # the predicted direction
    assert exp_one["p_perm"] < exp_two["p_perm"]
    assert exp_one["p_param"] == pytest.approx(exp_two["p_param"] / 2)
    # Primary Phase only by default; the sex comparisons are not predicted.
    assert row(one, "Acclimation", "genotype within Female")["side"] == "two-sided"
    assert row(one, "Experiment", "sex within WT")["side"] == "two-sided"
    # Distance is never directional.
    assert all(r["side"] == "two-sided" for r in dist["comparisons"])
    # Listed phases replace the default.
    listed = C.phase_results(tidy, "FinalPI", _design(
        [{"expect": "Mutant < WT", "phases": ["Cooldown"]}]))
    assert row(listed, "Cooldown", "genotype within Male")["side"] == "less"
    assert row(listed, "Experiment", "genotype within Male")["side"] == "two-sided"


def test_a_result_against_the_prediction_keeps_its_large_p():
    tidy = _tidy(effect=-0.3)                        # mutants prefer Light MORE
    res = C.phase_results(tidy, "FinalPI", _design(["Mutant < WT"]))
    row = next(r for r in res["comparisons"] if r["phase"] == "Experiment"
               and r["comparison"] == "genotype within Female")
    assert row["diff"] > 0 and row["p_perm"] > 0.9 and not row["significant"]


def test_three_levels_use_max_t_and_holm():
    rng = np.random.default_rng(3)
    rows = [{"Treatment": t, "Phase": "Experiment", "Value": rng.normal(m, 0.1)}
            for t, m in (("WT", 0.4), ("MutA", 0.1), ("MutB", 0.35))
            for _ in range(8)]
    tidy = pd.DataFrame(rows)
    tidy["Phase"] = pd.Categorical(tidy["Phase"], ["Experiment"])
    design = C.Design.from_global(
        {"experimental_design_factors": {"genotype": ["WT", "MutA", "MutB"]},
         "hypotheses": ["MutA < WT"]}, ["Experiment"])
    res = C.phase_results(tidy, "FinalPI", design)["comparisons"]
    assert len(res) == 3 and all(r["family"] == 3 for r in res)
    sides = {(r["a"], r["b"]): r["side"] for r in res}
    assert sides[("WT", "MutA")] == "less"
    assert sides[("WT", "MutB")] == "two-sided"
    assert sides[("MutA", "MutB")] == "two-sided"


def test_single_treatment_gets_the_sign_flip_test():
    rng = np.random.default_rng(0)
    tidy = pd.DataFrame({"Treatment": "WT", "Phase": "Experiment",
                         "Value": rng.normal(0.3, 0.2, 20)})
    tidy["Phase"] = pd.Categorical(tidy["Phase"], ["Experiment"])
    design = C.Design.from_global(
        {"experimental_design_factors": {"genotype": ["WT"]},
         "hypotheses": ["WT > indifference"]}, ["Experiment"])
    res = C.phase_results(tidy, "FinalPI", design)
    row = res["indifference"][0]
    assert row["side"] == "greater" and row["p_perm"] < 0.01
    assert row["p"] < 0.01 and not row["exact"]      # 2^20 > 100,000
    # A metric with no indifference value is said to be untestable.
    res = C.phase_results(tidy, "TotalDistancePerMin", design)
    assert not res["indifference"] and "no indifference value" in res["notes"][0]


def test_a_thin_arm_is_not_a_single_treatment_experiment():
    tidy = pd.DataFrame({"Treatment": ["WT"] * 6 + ["Mutant"],
                         "Phase": ["Experiment"] * 7,
                         "Value": [0.1, 0.2, 0.3, 0.2, 0.1, 0.3, 0.5]})
    tidy["Phase"] = pd.Categorical(tidy["Phase"], ["Experiment"])
    res = C.phase_results(tidy, "FinalPI", _design(factors={"genotype": ["WT", "Mutant"]}))
    assert not res["comparisons"] and not res["indifference"]
    assert "fewer than two groups" in res["notes"][0]


def test_replicates_are_strata_and_get_the_mixed_model():
    tidy = _tidy(effect=0.3, n=6, replicates=3)
    res = C.phase_results(tidy, "FinalPI", _design())
    row = res["comparisons"][0]
    assert row["p_mixed"] is not None and row["fixed_replicates"] == []
    # A replicate holding one level of a comparison is fixed and named.
    lopsided = tidy[~((tidy["Experiment"] == "Rep0")
                      & (tidy["Treatment"] == "Female, Mutant"))]
    res = C.phase_results(lopsided, "FinalPI", _design())
    row = next(r for r in res["comparisons"]
               if r["comparison"] == "genotype within Female")
    assert row["fixed_replicates"] == ["Rep0"]


def test_a_pair_never_in_a_shared_replicate_is_not_tested():
    rng = np.random.default_rng(2)
    rows = [{"Treatment": t, "Phase": "Experiment", "Experiment": rep,
             "Value": rng.normal()}
            for t, rep in (("WT", "R1"), ("Mutant", "R2")) for _ in range(5)]
    tidy = pd.DataFrame(rows)
    tidy["Phase"] = pd.Categorical(tidy["Phase"], ["Experiment"])
    res = C.phase_results(tidy, "FinalPI",
                          _design(factors={"genotype": ["WT", "Mutant"]}))
    assert res["comparisons"] == []
    assert "never recorded in the same replicate" in res["notes"][0]


def test_results_are_cached_by_content_whatever_the_row_order():
    tidy = _tidy()
    first = C.phase_results(tidy, "FinalPI", _design())
    shuffled = tidy.sample(frac=1, random_state=3).reset_index(drop=True)
    again = C.phase_results(shuffled, "FinalPI", _design())
    assert [r["p_perm"] for r in again["comparisons"]] == \
        [r["p_perm"] for r in first["comparisons"]]


# ---- Valence: config, Stats.txt, report and figure agree ----------------------

def _valence_config(**g):
    return {"global": {"experiment_type": "Valence", "tracking_rig": "colosseum",
                       "facet_cutoffs": [10, 70],
                       "experimental_design_factors": copy.deepcopy(FACTORS), **g},
            "counting_regions": {"Light": {"alias": "L"}, "NoLight": {"alias": "N"}},
            "tracking_regions": {"T_0": {"experimental_factors": "Female, WT"}}}


def test_valence_validates_hypotheses_and_custom_refuses_them():
    good = _valence_config(hypotheses=[{"expect": "Mutant < WT",
                                        "phases": ["Experiment", "Cooldown"]}])
    assert not [p for p in config_validation.validate_config(good)
                if "hypothes" in p.lower()]
    bad = _valence_config(hypotheses=[{"expect": "Mutant < WT",
                                       "phases": ["Persistence"]}])
    assert any("Persistence" in p for p in config_validation.validate_config(bad))
    custom = {"global": {"tracking_type": "TWOCHOICETRACKER",
                         "hypotheses": ["a < b"]},
              "counting_regions": {"A": {"alias": "a"}, "B": {"alias": "b"}}}
    assert any("Permutation Test" in p
               for p in config_validation.validate_config(custom))
    # Default cutoffs at a changed facet structure: names must follow.
    renamed = _valence_config(facet_cutoffs=[5, 60],
                              hypotheses=[{"expect": "Mutant < WT",
                                           "phases": ["Experiment"]}])
    assert any("does not exist" in p
               for p in config_validation.validate_config(renamed))


def test_valence_build_config_carries_hypotheses():
    t = et.get_experiment_type("Valence")
    cfg = t.build_config(rig="colosseum", factors=FACTORS,
                         hypotheses=[{"expect": "Mutant < WT"}])
    assert cfg["global"]["hypotheses"] == [{"expect": "Mutant < WT"}]
    assert "hypotheses" not in t.build_config(rig="colosseum")["global"]
    assert et.get_experiment_type("Valence").permutation_stats
    assert not et.get_experiment_type(None).permutation_stats
    assert not et.get_experiment_type("PairedOpenField").permutation_stats


class _Arena:
    """summarize / summarize_facet over a fixed per-fly frame."""

    def __init__(self, summary):
        self._summary = summary
        self.experiment_name = "E"

    def summarize(self, range_minutes=(0, 0), **kw):
        return self._summary.copy()

    def summarize_facet(self, cutoffs=None, **kw):
        from pytrackinganalysis import windowing
        frames = []
        for i, win in enumerate(windowing.facet_windows(cutoffs)):
            tmp = self._summary.copy()
            tmp["FinalPI"] = tmp["FinalPI"] + 0.01 * i
            tmp["FacetRange"] = [win] * len(tmp)
            frames.append(tmp)
        return pd.concat(frames, ignore_index=True)


def _factorial_summary(n=5, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for sex in ("Female", "Male"):
        for gen in ("WT", "Mutant"):
            for i in range(n):
                rows.append({"Name": f"{sex}{gen}{i}",
                             "Treatment": f"{sex}, {gen}",
                             "FinalPI": rng.normal(0.45 if gen == "WT" else 0.1, 0.15),
                             "FinalPercentage": rng.uniform(0.4, 0.8),
                             "TotalDistancePerMin": rng.uniform(80, 300)})
    return pd.DataFrame(rows)


def _valence_experiment(tmp_path, hypotheses=None):
    from pytrackinganalysis import Parameters
    from pytrackinganalysis.Experiment import Experiment

    (tmp_path / "analysis").mkdir(exist_ok=True)

    class _Exp(Experiment):
        def __init__(self):
            self.arena = _Arena(_factorial_summary())
            self.project_directory = str(tmp_path)
            self.analysis_path = str(tmp_path / "analysis") + os.sep
            self.qc_path = str(tmp_path) + os.sep
            self.config = _valence_config(
                **({"hypotheses": hypotheses} if hypotheses else {}))
            self.facet_cutoffs = [10, 70]
            self.experiment_type = et.get_experiment_type("Valence")
            self.parameters = Parameters.Parameters(
                tracking_type=Parameters.TrackingType.TWOCHOICETRACKER)

    return _Exp()


def test_stats_report_and_figure_show_the_same_p(tmp_path):
    from pytrackinganalysis import report_figures
    from pytrackinganalysis.report import model as m

    exp = _valence_experiment(tmp_path, hypotheses=["Mutant < WT"])
    results = exp.headline_results()["FinalPI"]
    row = next(r for r in results["comparisons"] if r["phase"] == "Experiment"
               and r["comparison"] == "genotype within Female")
    assert row["side"] == "less" and row["exact"]  # 5 vs 5: 252 relabellings
    # Stats.txt
    text = exp.stats(save=False)
    assert "genotype within Female" in text and "sex within WT" in text
    assert "one-sided, predicted Mutant < WT" in text
    assert "Mutant < WT  [Experiment (Primary Phase)]" in text
    assert C.p_text(row) in text
    # Report table
    blocks = report_figures.build_stats_table(exp)
    assert "Mutant < WT" in blocks[0].text
    table = next(b for b in blocks if isinstance(b, m.Table))
    assert "Comparison" in table.columns
    cells = next(r for r in table.rows if r[1] == "Experiment"
                 and r[2] == "genotype within Female" and r[0] == "FinalPI")
    assert cells[table.columns.index("Permutation p")] == C.p_text(row) + " †"
    # Figure bracket: looked up, same number, within-stratum pairs only.
    df = pf.faceted_data(exp, "FinalPI")
    brackets = [b for b in df.attrs["headline"] if b[0] == "Experiment"]
    assert ("Experiment", "Female, WT", "Female, Mutant", row["p_perm"], True) \
        in brackets
    assert all({a, b} != {"Female, WT", "Male, Mutant"} for _p, a, b, *_ in brackets)
    # Hiding Male, Mutant drops the brackets that need it; nothing recomputed.
    shown = pf.shown_headline_pvalues(
        df.attrs["headline"], ["Female, WT", "Female, Mutant", "Male, WT"],
        PHASES, {}, {})
    assert len(shown[shown["Phase"] == "Experiment"]) == 2
    assert shown["one_sided"].any()


def test_headline_rows_report_exact_enumeration(tmp_path):
    exp = _valence_experiment(tmp_path)
    row = exp.headline_results()["FinalPI"]["comparisons"][0]
    assert row["exact"] and row["relabellings"] == 252     # 5 vs 5


# ---- the Project: design authority, scaffolding, pooled results ----------------

_DESIGN = {"global": {"experiment_type": "Valence",
                      "facet_cutoffs": [10, 70],
                      "experimental_design_factors": copy.deepcopy(FACTORS),
                      "hypotheses": [{"expect": "Mutant < WT"}]},
           "counting_regions": ["Light", "NoLight"]}


def _write_replicate(root, name, **g):
    d = root / name
    (d / "analysis").mkdir(parents=True, exist_ok=True)
    (d / "tracking_config.yaml").write_text(
        yaml.safe_dump(_valence_config(**g)), encoding="utf-8")
    return d


def test_design_enforces_hypotheses_on_every_replicate(tmp_path):
    prj.create_project_file(str(tmp_path), "P", design=copy.deepcopy(_DESIGN))
    _write_replicate(tmp_path, "Rep1", hypotheses=["WT > Mutant"])   # same claim
    prj.Project(str(tmp_path))                                       # loads
    _write_replicate(tmp_path, "Rep2")                               # none
    with pytest.raises(ValueError, match="hypotheses"):
        prj.Project(str(tmp_path))
    # Carrying the design's predictions to the replicates repairs it.
    changed = prj.write_replicate_hypotheses(
        str(tmp_path), _DESIGN["global"]["hypotheses"])
    assert [os.path.basename(os.path.dirname(c)) for c in changed] == ["Rep2"]
    p = prj.Project(str(tmp_path))
    assert p.design_problems_for(_valence_config(hypotheses=["Mutant < WT"])) == []
    # A replicate predicting what the design does not is refused too.
    bare = copy.deepcopy(_DESIGN)
    del bare["global"]["hypotheses"]
    prj.create_project_file(str(tmp_path), "P", design=bare)
    with pytest.raises(ValueError, match="not in the project design"):
        prj.Project(str(tmp_path))


def test_scaffolded_replicate_carries_the_design_hypotheses(tmp_path):
    prj.create_project_file(str(tmp_path), "P", design=copy.deepcopy(_DESIGN))
    p = prj.Project(str(tmp_path))
    cfg = p.scaffold_replicate_config()
    assert cfg["global"]["hypotheses"] == [{"expect": "Mutant < WT"}]
    assert p.design_problems_for(cfg) == []


def test_legacy_project_replicates_must_agree_on_hypotheses(tmp_path):
    prj.create_project_file(str(tmp_path), "P")
    _write_replicate(tmp_path, "Rep1", hypotheses=["Mutant < WT"])
    _write_replicate(tmp_path, "Rep2")
    with pytest.raises(ValueError, match="hypotheses"):
        prj.Project(str(tmp_path))


def test_project_headline_is_shuffled_within_replicates(tmp_path):
    from pytrackinganalysis.report import model as m

    prj.create_project_file(str(tmp_path), "P", design=copy.deepcopy(_DESIGN))
    for i, name in enumerate(("Rep1", "Rep2", "Rep3")):
        d = _write_replicate(tmp_path, name, hypotheses=["Mutant < WT"])
        s = _factorial_summary(n=4, seed=i)
        s.to_csv(d / "analysis" / "Rec_Summary.csv", index=False)
        frames = []
        for win in ["(0, 10)", "(10, 70)", "(70, inf)"]:
            f = s.copy()
            f["FacetRange"] = win
            frames.append(f)
        pd.concat(frames).to_csv(d / "analysis" / "Rec_Summary_Facet.csv",
                                 index=False)
    p = prj.Project(str(tmp_path))
    summary, facet, _, _ = p.combined_frames()
    results = p.headline_results(summary, facet)
    row = next(r for r in results["FinalPI"]["comparisons"]
               if r["phase"] == "Experiment"
               and r["comparison"] == "genotype within Male")
    assert row["n_a"] == row["n_b"] == 12 and row["side"] == "less"
    assert row["p_mixed"] is not None and not row["exact"]   # 70^3 > 100,000
    text = p.stats_text(summary, facet)
    assert "labels shuffled within each replicate and stratum" \
        in " ".join(text.split())                    # wrapped at 72 columns
    assert "genotype within Male" in text and "Mutant < WT" in text
    model = p.build_report_model(include_ai_summary=False)
    table = next(b for b in model.blocks if isinstance(b, m.Table)
                 and b.title == "Statistical comparisons")
    assert table.columns[-3:] == ["Permutation p", "Model p", "Mixed p"]
    # The figure data of the same Project carries the same p.
    windows, label_of = p.window_labels(facet)
    df = pf.faceted_data_from_combined(
        facet, "FinalPI", [label_of[w] for w in windows],
        lambda v: label_of[p.parse_window(v)], p.experiment_type,
        p._design_global())
    assert ("Experiment", "Male, WT", "Male, Mutant", row["p_perm"], True) \
        in df.attrs["headline"]
    assert "permutation p" in p.ai_prompt()
