"""The resampling engine behind the Permutation Test and the Sign-Flip Test
(ADR-0017): checked against brute-force enumeration, and against the bugs of
the lab's 2Choice_Stats.R it replaces (half-split groups, alphabetical
one-sided p, p = 0)."""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from scipy import stats

from pytrackinganalysis import permutation as P


def _welch_t(a, b):
    return stats.ttest_ind(b, a, equal_var=False).statistic


def _brute_force(x, g, side=0):
    """p over every relabelling, by hand (two groups, codes 0/1)."""
    obs = _welch_t(x[g == 0], x[g == 1])
    n1 = int((g == 1).sum())
    hits = total = 0
    for chosen in itertools.combinations(range(len(x)), n1):
        gg = np.zeros(len(x), int)
        gg[list(chosen)] = 1
        t = _welch_t(x[gg == 0], x[gg == 1])
        stat, ref = {0: (abs(t), abs(obs)), 1: (t, obs), -1: (-t, -obs)}[side]
        hits += stat >= ref - 1e-9
        total += 1
    return hits / total


@pytest.mark.parametrize("side", [0, 1, -1])
def test_exact_p_matches_brute_force_with_unequal_groups(side):
    # 7 vs 4: the R script split the pooled values in half (and dropped a fly
    # at odd n); every relabelling here keeps the real group sizes.
    rng = np.random.default_rng(5)
    x = rng.normal(size=11)
    x[7:] += 0.7
    g = np.array([0] * 7 + [1] * 4)
    r = P.permutation_test(x, g, sides=[side])
    assert r["exact"] and r["relabellings"] == 330
    assert r["p"][0] == pytest.approx(_brute_force(x, g, side))
    assert r["t"][0] == pytest.approx(_welch_t(x[g == 0], x[g == 1]))


def test_three_vs_three_cannot_reach_significance():
    x = np.array([0.1, 0.2, 0.3, 0.8, 0.9, 1.0])
    g = np.array([0, 0, 0, 1, 1, 1])
    r = P.permutation_test(x, g)
    # 20 relabellings; the observed one and its mirror are both extreme.
    assert r["exact"] and r["relabellings"] == 20
    assert r["p"][0] == pytest.approx(0.1) and r["p_floor"] == pytest.approx(0.1)


def test_two_sided_p_ignores_level_order_and_names():
    rng = np.random.default_rng(1)
    x = rng.normal(size=12)
    g = np.array([0] * 6 + [1] * 6)
    forward = P.permutation_test(x, g)["p"][0]
    swapped = P.permutation_test(x, 1 - g)["p"][0]
    assert forward == pytest.approx(swapped)


def test_monte_carlo_is_reproducible_never_zero_and_near_exact():
    rng = np.random.default_rng(2)
    x = rng.normal(size=18)
    x[9:] += 0.5
    g = np.array([0] * 9 + [1] * 9)            # 48,620 relabellings
    exact = P.permutation_test(x, g)
    mc1 = P.permutation_test(x, g, shuffles=20_000)
    mc2 = P.permutation_test(x, g, shuffles=20_000)
    assert exact["exact"] and not mc1["exact"]
    assert mc1["p"] == mc2["p"]                # fixed seed
    assert abs(mc1["p"][0] - exact["p"][0]) < 0.02
    # A huge effect: no shuffle reaches it, and p is 1/(B+1), never 0.
    y = np.r_[np.zeros(30), np.ones(30)] + rng.normal(0, 0.01, 60)
    big = P.permutation_test(y, np.array([0] * 30 + [1] * 30), shuffles=5_000)
    assert big["p"][0] == pytest.approx(1 / 5_001)


def test_labels_move_only_within_strata():
    # Replicate 1 holds only A, replicate 2 only B: a plate effect would look
    # like a treatment effect to a pooled test, but with the replicate in the
    # model the difference cannot be separated from it at all — not
    # estimable, so no p (and no relabelling exists to shuffle anyway).
    x = np.r_[np.full(5, 0.1), np.full(5, 0.9)] + np.linspace(0, 0.01, 10)
    g = np.array([0] * 5 + [1] * 5)
    strata = np.array([0] * 5 + [1] * 5)
    assert P.count_relabellings(g, strata) == 1
    r = P.permutation_test(x, g, strata=strata)
    assert r["estimable"] == [False] and np.isnan(r["p"][0])
    assert np.isnan(P.parametric_family(x, g, strata, [(0, 1)], [0])[0])
    assert stats.ttest_ind(x[:5], x[5:], equal_var=False).pvalue < 1e-6
    # Balanced within each stratum: the counts multiply.
    g2 = np.array([0, 0, 1, 1, 0, 1, 0, 1])
    s2 = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    assert P.count_relabellings(g2, s2) == 6 * 6


def test_max_t_adjusts_for_the_family():
    rng = np.random.default_rng(4)
    x = rng.normal(size=15)
    x[10:] += 1.5
    g = np.array([0] * 5 + [1] * 5 + [2] * 5)
    family = P.permutation_test(x, g)
    assert len(family["p"]) == 3
    alone = P.permutation_test(x[g != 1], (g[g != 1] == 2).astype(int))
    # The (0, 2) pair alone vs inside a 3-pair max-T family: never smaller.
    assert family["p"][1] >= alone["p"][0] - 1e-12


def test_sign_flip_matches_brute_force_and_states_its_floor():
    d = np.array([0.3, 0.5, -0.1, 0.4, 0.6, 0.2])
    r = P.sign_flip_test(d)
    obs = abs(stats.ttest_1samp(d, 0).statistic)
    hits = 0
    for signs in itertools.product((-1, 1), repeat=6):
        hits += abs(stats.ttest_1samp(d * np.array(signs), 0).statistic) >= obs - 1e-9
    assert r["exact"] and r["relabellings"] == 64
    assert r["p"] == pytest.approx(hits / 64)
    assert r["p_floor"] == pytest.approx(2 / 64)
    greater = P.sign_flip_test(d, side=1)["p"]
    assert greater < r["p"]                    # the effect is positive
    assert P.sign_flip_test([0.2]) is None
    assert P.sign_flip_test([0.3, 0.3, 0.3]) is None   # no spread


def test_holm():
    from statsmodels.stats.multitest import multipletests

    raw = [0.01, 0.04, 0.03, float("nan")]
    adjusted = P.holm(raw)
    expected = multipletests(raw[:3], method="holm")[1]
    assert adjusted[:3] == pytest.approx(list(expected))
    assert np.isnan(adjusted[3])


@pytest.mark.parametrize("side,alternative",
                         [(0, "two-sided"), (1, "greater"), (-1, "less")])
def test_one_replicate_is_welchs_t_test_exactly(side, alternative):
    # The replicate-adjusted statistic with a single replicate: Welch's t,
    # and its parametric p is Welch's p (Satterthwaite df), in any sidedness.
    rng = np.random.default_rng(8)
    a, b = rng.normal(0, 1, 9), rng.normal(0.8, 2.5, 14)
    x, g = np.r_[a, b], np.r_[np.zeros(9, int), np.ones(14, int)]
    welch = stats.ttest_ind(b, a, equal_var=False, alternative=alternative)
    assert P.permutation_test(x, g, shuffles=500)["t"][0] == \
        pytest.approx(welch.statistic)
    assert P.parametric_family(x, g, None, [(0, 1)], [side])[0] == \
        pytest.approx(welch.pvalue)


def test_replicates_are_fixed_effects_with_robust_errors():
    # Several replicates: each pair's t is its contrast in
    # Value ~ replicate + group over the HC2 standard error (statsmodels).
    import pandas as pd
    import statsmodels.formula.api as smf

    rng = np.random.default_rng(3)
    d = pd.DataFrame({"y": rng.normal(size=72),
                      "rep": np.repeat(["r1", "r2", "r3"], 24),
                      "grp": np.tile(["A", "B", "C"], 24)})
    d.loc[d.rep == "r2", "y"] += 3.0                    # a big plate effect
    d.loc[d.grp == "C", "y"] += 0.7
    codes = d.grp.map({"A": 0, "B": 1, "C": 2}).to_numpy()
    pairs = [(0, 1), (0, 2), (1, 2)]
    r = P.permutation_test(d.y.to_numpy(), codes, strata=d.rep.to_numpy(),
                           pairs=pairs, shuffles=2_000)
    fit = smf.ols("y ~ C(rep) + C(grp)", d).fit(cov_type="HC2")
    assert r["t"][0] == pytest.approx(fit.tvalues["C(grp)[T.B]"])
    assert r["t"][1] == pytest.approx(fit.tvalues["C(grp)[T.C]"])
    contrast = fit.t_test("C(grp)[T.C] - C(grp)[T.B] = 0")
    assert r["t"][2] == pytest.approx(np.asarray(contrast.tvalue).item())
    # One-sided stays one-sided: the signed t, counted in one tail.
    greater = P.permutation_test(d.y.to_numpy(), codes, strata=d.rep.to_numpy(),
                                 pairs=[(0, 2)], sides=[1], shuffles=2_000)
    less = P.permutation_test(d.y.to_numpy(), codes, strata=d.rep.to_numpy(),
                              pairs=[(0, 2)], sides=[-1], shuffles=2_000)
    assert greater["p"][0] < 0.05 < less["p"][0]


def test_one_sided_from_two():
    assert P.one_sided_from_two(0.04, 1.2, 1) == pytest.approx(0.02)
    assert P.one_sided_from_two(0.04, 1.2, -1) == pytest.approx(0.98)
    assert P.one_sided_from_two(0.04, 1.2, 0) == 0.04
