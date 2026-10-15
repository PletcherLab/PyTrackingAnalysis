"""Factor Comparisons, Directional Hypotheses and their tests — the
statistics a Valence Experiment headlines (see ``CONTEXT.md``: Permutation
Test, Factor Comparison, Directional Hypothesis, Test against Indifference).

One module so every place that reports a Valence p-value — the experiment's
``*_Stats.txt``, the experiment and Project report tables, and the
Publication Figure brackets — gets the same rows from the same data and
cannot drift apart (the reason :mod:`.indifference` exists, one level up).

* :func:`plan` decides which groups are compared: with two or more varying
  factors, each factor's levels **within a Stratum** (the flies sharing every
  other factor's level); otherwise the Treatments themselves.
* :func:`phase_results` runs one metric's comparisons in every Phase of a
  tidy ``Treatment / Phase / Value [/ Experiment]`` frame: the Permutation
  Test (max-T, shuffled within replicate × stratum) beside Welch (Holm for
  three or more levels) — or, with one treatment, the Sign-Flip Test beside
  the one-sample t-test.
* :class:`Hypothesis` / :func:`validate_hypotheses` — the design's
  ``hypotheses:`` list.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indifference, permutation

#: Metrics a Directional Hypothesis covers: higher means more time on the
#: first counting region (Light) for both, so one direction reads the same.
DIRECTIONAL_METRICS = ("FinalPI", "FinalPercentage")

#: The right-hand side naming the no-preference value in a single-treatment
#: hypothesis (``WT > indifference``).
INDIFFERENCE = "indifference"

ALPHA = 0.05

#: The comparison sign with whitespace on both sides — how a name that itself
#: holds '>' (fly genotypes: 'Nanchung>Chrimson') stays one name.
_SPACED_SIGN = re.compile(r"\s+([<>])\s+")


def split_expect(text: str) -> tuple[str, str, str] | None:
    """``'A < B'`` → ``('A', '<', 'B')``, or None when it is not one
    comparison. A sign with spaces around it is the comparison, so names may
    contain '<' or '>' ('Nanchung>Chrimson > WCS>Chrimson'); without spaces
    the text may hold only the one sign ('Mutant<WT')."""
    text = str(text or "").strip()
    spaced = list(_SPACED_SIGN.finditer(text))
    if len(spaced) == 1:
        m = spaced[0]
        left, right = text[:m.start()].strip(), text[m.end():].strip()
        op = m.group(1)
    elif not spaced and text.count("<") + text.count(">") == 1:
        op = "<" if "<" in text else ">"
        left, right = (part.strip() for part in text.split(op))
    else:
        return None
    if not left or not right:
        return None
    return left, op, right


def normalize_expect(text: str) -> str:
    """The prediction as written, with single spaces around its sign — the
    form the editors save ('Mutant<WT' → 'Mutant < WT')."""
    parts = split_expect(text)
    return f"{parts[0]} {parts[1]} {parts[2]}" if parts else str(text).strip()


# ---------------------------------------------------------------------------
# Directional Hypotheses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Hypothesis:
    """``low < high`` in light preference, in *phases* (None = the Primary
    Phase). *high* or *low* may be :data:`INDIFFERENCE`."""
    low: str
    high: str
    phases: tuple[str, ...] | None = None
    #: The prediction as the design writes it (spacing normalised), shown to
    #: readers — 'Nanchung>Chrimson > WCS>Chrimson' reads as intended, its
    #: rewritten form 'WCS>Chrimson < Nanchung>Chrimson' does not.
    written: str = ""

    @property
    def text(self) -> str:
        return self.written or f"{self.low} < {self.high}"

    def covers(self, metric: str, phase: str, primary: str | None) -> bool:
        if metric not in DIRECTIONAL_METRICS:
            return False
        if self.phases:
            return phase in self.phases
        return primary is None or phase == primary

    def side_for(self, a_names, b_names) -> int | None:
        """Sidedness of the B − A comparison this hypothesis predicts, or None
        when it is about another pair. *a_names*/*b_names* are every name a
        group answers to (its Treatment label and its level)."""
        if self.low in b_names and self.high in a_names:
            return -1
        if self.low in a_names and self.high in b_names:
            return 1
        return None

    def side_vs_indifference(self, names) -> int | None:
        if self.high == INDIFFERENCE and self.low in names:
            return -1
        if self.low == INDIFFERENCE and self.high in names:
            return 1
        return None


def parse_hypothesis(entry) -> tuple[Hypothesis | None, str | None]:
    """One ``hypotheses:`` entry → (Hypothesis, None) or (None, problem)."""
    if isinstance(entry, str):
        entry = {"expect": entry}
    if not isinstance(entry, dict):
        return None, f"each hypothesis must be a mapping with 'expect', got {entry!r}"
    unknown = set(entry) - {"expect", "phases"}
    if unknown:
        return None, (f"hypothesis {entry.get('expect', entry)!r} has unknown "
                      f"key(s) {', '.join(sorted(map(str, unknown)))}; only "
                      "'expect' and 'phases' are allowed")
    expect = entry.get("expect")
    parts = split_expect(expect)
    if parts is None:
        return None, (f"hypothesis 'expect: {expect}' must read 'A < B' or "
                      "'A > B' — one comparison, levels of one factor or a "
                      "treatment against 'indifference'. When a name holds "
                      "'<' or '>' itself, put spaces around the comparison "
                      "sign only: 'Nanchung>Chrimson > WCS>Chrimson'.")
    left, op, right = parts
    low, high = (left, right) if op == "<" else (right, left)
    if low == high:
        return None, f"hypothesis 'expect: {expect}' compares a group with itself"
    if low.lower() == INDIFFERENCE:
        low = INDIFFERENCE
    if high.lower() == INDIFFERENCE:
        high = INDIFFERENCE
    phases = entry.get("phases")
    if phases is not None:
        if isinstance(phases, str):
            phases = [p.strip() for p in phases.split(",") if p.strip()]
        if not isinstance(phases, (list, tuple)) or not phases:
            return None, (f"hypothesis 'expect: {expect}': 'phases' must be a "
                          "non-empty list of phase names (omit it for the "
                          "Primary Phase)")
        phases = tuple(str(p).strip() for p in phases)
    return Hypothesis(low=low, high=high, phases=phases,
                      written=f"{left} {op} {right}"), None


def parse_hypotheses(raw) -> tuple[list[Hypothesis], list[str]]:
    """The design's ``hypotheses:`` value → (hypotheses, problems)."""
    if raw in (None, "", []):
        return [], []
    if not isinstance(raw, (list, tuple)):
        return [], ["'hypotheses' must be a list of {expect, phases} entries"]
    out, problems = [], []
    for entry in raw:
        hyp, problem = parse_hypothesis(entry)
        if problem:
            problems.append(problem)
        else:
            out.append(hyp)
    return out, problems


def canonical_hypotheses(raw):
    """A comparable form of a ``hypotheses:`` value — order-free, spacing- and
    direction-normalised (``WT > Mutant`` ≡ ``Mutant < WT``) — or the raw
    value when it does not parse (so a broken entry still mismatches)."""
    hyps, problems = parse_hypotheses(raw)
    if problems:
        return raw
    return sorted((h.low, h.high, tuple(h.phases or ())) for h in hyps)


def validate_hypotheses(raw, factors: dict | None,
                        phase_labels: list[str]) -> list[str]:
    """Design problems in a ``hypotheses:`` list: unparseable entries, names
    that are not levels of one declared factor, phases that do not exist at
    the current cutoffs, and the same pair predicted twice."""
    hyps, problems = parse_hypotheses(raw)
    factors = {str(k): [str(v) for v in (lv or [])]
               for k, lv in (factors or {}).items()}
    seen: dict[frozenset, str] = {}
    for h in hyps:
        names = [n for n in (h.low, h.high) if n != INDIFFERENCE]
        if factors:
            homes = [{f for f, levels in factors.items() if n in levels}
                     for n in names]
            for n, home in zip(names, homes):
                if not home:
                    problems.append(
                        f"hypothesis '{h.text}': '{n}' is not a level of any "
                        f"declared factor ({_levels_text(factors)})")
            if len(names) == 2 and all(homes) and not (homes[0] & homes[1]):
                problems.append(
                    f"hypothesis '{h.text}': '{names[0]}' and '{names[1]}' are "
                    "levels of different factors; a prediction compares levels "
                    "of one factor")
        for phase in h.phases or ():
            if phase not in phase_labels:
                problems.append(
                    f"hypothesis '{h.text}': phase '{phase}' does not exist at "
                    f"the current cutoffs (phases: {', '.join(phase_labels)})")
        key = frozenset((h.low, h.high))
        if key in seen:
            problems.append(f"hypothesis '{h.text}' predicts the same pair as "
                            f"'{seen[key]}'; state each pair once")
        seen[key] = h.text
    return problems


def _levels_text(factors: dict) -> str:
    return "; ".join(f"{f}: {', '.join(lv)}" for f, lv in factors.items())


# ---------------------------------------------------------------------------
# Design (what a consumer needs to know to run the comparisons)
# ---------------------------------------------------------------------------

@dataclass
class Design:
    """The parts of an experiment's design the comparisons read."""
    factors: dict = field(default_factory=dict)
    hypotheses: list = field(default_factory=list)
    #: The phase a hypothesis without ``phases`` covers.
    primary_phase: str | None = None

    @classmethod
    def from_global(cls, global_cfg: dict | None, phase_labels=()) -> "Design":
        g = global_cfg or {}
        hyps, _problems = parse_hypotheses(g.get("hypotheses"))
        labels = list(phase_labels or [])
        primary = (labels[1] if len(labels) >= 2 else labels[0]) if labels else None
        factors = {str(k): [str(v) for v in (lv or [])]
                   for k, lv in (g.get("experimental_design_factors") or {}).items()}
        return cls(factors=factors, hypotheses=hyps, primary_phase=primary)

    def hypotheses_text(self) -> list[str]:
        out = []
        for h in self.hypotheses:
            where = ", ".join(h.phases) if h.phases else \
                f"{self.primary_phase or 'primary'} (Primary Phase)"
            out.append(f"{h.text}  [{where}]")
        return out


# ---------------------------------------------------------------------------
# Factor Comparison planning
# ---------------------------------------------------------------------------

def split_treatment(label: str, factors: dict) -> dict:
    """``{factor: level}`` for a comma-joined Treatment label — each token to
    the first not-yet-filled factor whose levels hold it (the Config Editor's
    rule). Tokens matching no factor are ignored."""
    out: dict[str, str] = {}
    for tok in (t.strip() for t in str(label).split(",")):
        if not tok:
            continue
        for name, levels in factors.items():
            if name not in out and tok in levels:
                out[name] = tok
                break
    return out


@dataclass
class Comparison:
    """One family of groups compared against each other."""
    groups: list[str]                 # Treatment labels, in comparison order
    names: list[str]                  # display names (levels, or the labels)
    factor: str | None = None         # None: the Treatments themselves
    stratum: dict = field(default_factory=dict)

    @property
    def stratum_text(self) -> str:
        return ", ".join(self.stratum.values())

    @property
    def label(self) -> str:
        if self.factor is None:
            return "Treatments"
        return f"{self.factor} within {self.stratum_text}" if self.stratum \
            else self.factor


def plan(treatments: list[str], factors: dict) -> tuple[list[Comparison], list[str]]:
    """The comparisons to run among *treatments* (labels present in the data).

    Two or more factors varying in the data → Factor Comparisons: each
    varying factor across its levels within every Stratum of the others.
    Otherwise (or when a label cannot be read as factor levels) one flat
    comparison of the Treatments. Returns ``(comparisons, notes)``.
    """
    treatments = list(dict.fromkeys(treatments))
    notes: list[str] = []
    parsed = {t: split_treatment(t, factors) for t in treatments}
    varying = [f for f in factors
               if len({parsed[t].get(f) for t in treatments} - {None}) >= 2]
    unreadable = [t for t in treatments
                  if any(f not in parsed[t] for f in varying)]
    if len(varying) < 2 or unreadable:
        if len(varying) >= 2:
            notes.append("Treatment label(s) " + ", ".join(repr(t) for t in unreadable)
                         + " do not name a level of every factor, so the "
                         "Treatments are compared directly instead of within "
                         "strata.")
        only = varying[0] if len(varying) == 1 and not unreadable else None
        if only:
            ## The design's level order, as Factor Comparisons use: the first
            ## declared level is A, so B − A reads against it (a control
            ## listed first is the reference), whatever order the data came in.
            order = factors[only]
            treatments = sorted(treatments, key=lambda t: order.index(parsed[t][only])
                                if parsed[t][only] in order else len(order))
        names = [parsed[t].get(only, t) if only else t for t in treatments]
        if len(set(names)) != len(names):
            names = treatments
        return [Comparison(groups=treatments, names=names)], notes

    comparisons: list[Comparison] = []
    for factor in varying:
        others = [f for f in varying if f != factor]
        strata: dict[tuple, list[str]] = {}
        for t in treatments:
            strata.setdefault(tuple(parsed[t][o] for o in others), []).append(t)
        order = factors[factor]
        for key in sorted(strata, key=lambda k: tuple(
                factors[o].index(v) if v in factors[o] else 99
                for o, v in zip(others, k))):
            groups = sorted(strata[key], key=lambda t: order.index(parsed[t][factor])
                            if parsed[t][factor] in order else 99)
            if len(groups) < 2:
                continue
            comparisons.append(Comparison(
                groups=groups, names=[parsed[t][factor] for t in groups],
                factor=factor, stratum=dict(zip(others, key))))
    return comparisons, notes


# ---------------------------------------------------------------------------
# Running one metric
# ---------------------------------------------------------------------------

def _canonical(data: pd.DataFrame) -> pd.DataFrame:
    """Rows in a fixed order, so the same flies always give the same p
    whichever consumer built the frame."""
    cols = [c for c in ("Experiment", "Treatment", "Value") if c in data.columns]
    return data.sort_values(cols, kind="mergesort").reset_index(drop=True)


def _sides(comparison: Comparison, pairs, design: Design, metric: str,
           phase: str) -> tuple[list[int], list[str | None]]:
    sides, texts = [], []
    hyps = [h for h in design.hypotheses
            if h.covers(metric, phase, design.primary_phase)]
    for i, j in pairs:
        a_names = {comparison.groups[i], comparison.names[i]}
        b_names = {comparison.groups[j], comparison.names[j]}
        side, text = 0, None
        for h in hyps:
            s = h.side_for(a_names, b_names)
            if s is not None:
                side, text = s, h.text
                break
        sides.append(side)
        texts.append(text)
    return sides, texts


def compare(data: pd.DataFrame, comparison: Comparison, metric: str,
            phase: str, design: Design) -> tuple[list[dict], list[str]]:
    """The rows of one comparison: every pair with its permutation p, the
    parametric p of the same replicate-adjusted Welch t, and — with two or
    more replicates — the mixed-model p. *data* holds Treatment / Value
    [/ Experiment] rows with values (NaN already dropped). Returns
    ``(rows, notes)``; a note names any pair that could not be estimated."""
    sub = data[data["Treatment"].isin(comparison.groups)]
    counts = sub["Treatment"].value_counts()
    keep = [i for i, t in enumerate(comparison.groups) if counts.get(t, 0) >= 2]
    if len(keep) < 2:
        return [], []
    groups = [comparison.groups[i] for i in keep]
    names = [comparison.names[i] for i in keep]
    comp = Comparison(groups=groups, names=names, factor=comparison.factor,
                      stratum=comparison.stratum)
    sub = _canonical(sub[sub["Treatment"].isin(groups)])
    code = {t: i for i, t in enumerate(groups)}
    codes = sub["Treatment"].map(code).to_numpy()
    values = sub["Value"].to_numpy(dtype=float)
    strata = sub["Experiment"].to_numpy() if "Experiment" in sub.columns else None
    pairs = list(itertools.combinations(range(len(groups)), 2))
    sides, texts = _sides(comp, pairs, design, metric, phase)

    perm = permutation.permutation_test(values, codes, strata=strata,
                                        pairs=pairs, sides=sides)
    samples = [values[codes == i] for i in range(len(groups))]
    param = permutation.parametric_family(values, codes, strata, pairs, sides)
    live = [c for c in range(len(pairs)) if perm["estimable"][c]]
    notes = [f"{phase}, {comp.label}: {names[i]} and {names[j]} were never "
             "recorded in the same replicate, so their difference cannot be "
             "separated from the replicates' and is not tested."
             for c, (i, j) in enumerate(pairs) if not perm["estimable"][c]]
    mixed_p = [None] * len(pairs)
    if strata is not None and len(set(strata)) > 1 and live:
        ## Holm over the same family as the other columns: estimable pairs.
        raw = [mixed_p_value(sub, groups[pairs[c][0]], groups[pairs[c][1]],
                             sides[c]) for c in live]
        if any(p is not None for p in raw):
            adjusted = permutation.holm([np.nan if p is None else p for p in raw])
            for c, p, q in zip(live, raw, adjusted):
                mixed_p[c] = None if p is None else q

    fixed: list[str] = []
    if strata is not None:
        for rep, grp in sub.groupby("Experiment", sort=True):
            if grp["Treatment"].nunique() < 2:
                fixed.append(str(rep))

    rows = []
    for c in live:
        i, j = pairs[c]
        a, b = samples[i], samples[j]
        rows.append({
            "metric": metric, "phase": phase,
            "factor": comp.factor, "stratum": comp.stratum_text,
            "comparison": comp.label,
            "a": names[i], "b": names[j],
            "a_treatment": groups[i], "b_treatment": groups[j],
            "n_a": int(len(a)), "n_b": int(len(b)),
            "mean_a": float(np.mean(a)), "mean_b": float(np.mean(b)),
            "sd_a": float(np.std(a, ddof=1)), "sd_b": float(np.std(b, ddof=1)),
            "diff": float(np.mean(b) - np.mean(a)),
            "t": perm["t"][c],
            "side": permutation.SIDE_NAMES[sides[c]],
            "hypothesis": texts[c],
            "p_perm": perm["p"][c], "p_param": param[c], "p_mixed": mixed_p[c],
            "significant": bool(perm["p"][c] < ALPHA),
            "family": len(live),
            "exact": perm["exact"], "relabellings": perm["relabellings"],
            "p_floor": perm["p_floor"],
            "fixed_replicates": fixed,
            "replicate_adjusted": strata is not None and len(set(strata)) > 1,
        })
    return rows, notes


def against_indifference(data: pd.DataFrame, treatment: str, metric: str,
                         phase: str, design: Design) -> dict | None:
    """The single-treatment row: Sign-Flip Test beside the one-sample t-test
    (and, with two or more replicates, the mixed model), one-sided under a
    Directional Hypothesis against indifference."""
    null = indifference.null_for(metric)
    if null is None:
        return None
    vals = data["Value"].to_numpy(dtype=float)
    base = indifference.one_sample(vals, metric)
    if base is None:
        return None
    side, text = 0, None
    names = {treatment, *split_treatment(treatment, design.factors).values()}
    for h in design.hypotheses:
        if h.covers(metric, phase, design.primary_phase):
            s = h.side_vs_indifference(names)
            if s is not None:
                side, text = s, h.text
                break
    flip = permutation.sign_flip_test(np.sort(vals) - null, side=side)
    if flip is None:
        return None
    t_p = base["p"]
    if side:
        from scipy import stats as sstats
        t_p = float(sstats.ttest_1samp(
            vals[~np.isnan(vals)], null,
            alternative="greater" if side > 0 else "less").pvalue)
    p_mixed = None
    if "Experiment" in data.columns and data["Experiment"].nunique() > 1:
        p_mixed = mixed_p_vs_null(data, null, side)
    return {
        "metric": metric, "phase": phase, "treatment": treatment,
        "n": base["n"], "mean": base["mean"], "sd": base["sd"],
        "null": base["null"], "diff": base["diff"], "t": base["t"],
        "side": permutation.SIDE_NAMES[side], "hypothesis": text,
        "p_perm": flip["p"], "p": t_p, "p_mixed": p_mixed,
        "significant": bool(flip["p"] < ALPHA),
        "exact": flip["exact"], "relabellings": flip["relabellings"],
        "p_floor": flip["p_floor"],
    }


def tidy_frame(frame: pd.DataFrame, metric: str, phase,
               phase_labels) -> pd.DataFrame:
    """The one tidy layout every consumer computes on: Treatment (stripped,
    blank for an unassigned region), Phase (ordered categorical), Value
    (numeric, NaN kept) and, for a Project, Experiment. *phase* is a label
    per row of *frame* (a Series aligned with it, or one label for all)."""
    if metric not in frame.columns:
        return pd.DataFrame(columns=["Treatment", "Phase", "Value"])
    labels = list(phase_labels)
    if not isinstance(phase, pd.Series):
        phase = pd.Series([phase] * len(frame), index=frame.index)
    df = pd.DataFrame({
        ## fillna first: under pandas 3 astype(str) keeps NaN as NaN, so a
        ## blank Treatment cell slipped past the != "" filter as a float.
        "Treatment": frame["Treatment"].fillna("").astype(str).str.strip(),
        "Phase": pd.Categorical(phase, categories=labels, ordered=True),
        "Value": pd.to_numeric(frame[metric], errors="coerce"),
    })
    if "Experiment" in frame.columns:
        df["Experiment"] = frame["Experiment"].astype(str)
    return df.reset_index(drop=True)


#: Results already computed, keyed by the data's content: Stats.txt, the
#: report tables and a figure's brackets ask for the same comparison and
#: get the same object, with no 100,000-shuffle recomputation.
_CACHE: dict = {}
_CACHE_LIMIT = 512


def _fingerprint(tidy: pd.DataFrame, metric: str, design: Design) -> tuple:
    cols = [c for c in ("Treatment", "Phase", "Value", "Experiment")
            if c in tidy.columns]
    frame = tidy[cols].astype({"Phase": str}) if "Phase" in cols else tidy[cols]
    digest = int(pd.util.hash_pandas_object(frame, index=False).sum()) \
        if len(frame) else 0
    phases = tuple(map(str, tidy["Phase"].cat.categories)) \
        if isinstance(tidy["Phase"].dtype, pd.CategoricalDtype) else ()
    return (metric, digest, len(frame), phases,
            tuple((f, tuple(lv)) for f, lv in design.factors.items()),
            tuple(design.hypotheses), design.primary_phase)


def phase_results(tidy: pd.DataFrame, metric: str, design: Design) -> dict:
    """Every comparison of *metric* in every Phase of *tidy* — cached by
    content (see :func:`_phase_results`)."""
    if tidy is None or not len(tidy):
        return {"comparisons": [], "indifference": [], "notes": []}
    key = _fingerprint(tidy, metric, design)
    if key not in _CACHE:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = _phase_results(tidy, metric, design)
    return _CACHE[key]


def _phase_results(tidy: pd.DataFrame, metric: str, design: Design) -> dict:
    """Every comparison of *metric* in every Phase of *tidy* (columns
    Treatment, Phase, Value, optional Experiment; Value may hold NaN).

    Returns ``{"comparisons": [...], "indifference": [...], "notes": [...]}``.
    A phase with one assigned treatment level is tested against indifference
    (when the metric has a null); the level count is taken before dropping
    missing values — a two-arm experiment with one thin arm is not a
    single-treatment experiment.
    """
    out = {"comparisons": [], "indifference": [], "notes": []}
    if tidy is None or not len(tidy):
        return out
    phases = (list(tidy["Phase"].cat.categories)
              if isinstance(tidy["Phase"].dtype, pd.CategoricalDtype)
              else list(dict.fromkeys(tidy["Phase"])))
    for phase in phases:
        frame = tidy[tidy["Phase"] == phase]
        labels = frame["Treatment"].fillna("").astype(str).str.strip()
        unassigned = int((labels == "").sum())
        if unassigned:
            ## A blank region is not a treatment arm (Arena._treatment_arms):
            ## left out, and said so, never analysed as a nameless group.
            _note(out, f"{unassigned} fly(ies) have no experimental_factors "
                       "assigned and were left out — check tracking_config.yaml.")
        frame = frame.assign(Treatment=labels)[labels != ""]
        treatments = list(dict.fromkeys(frame["Treatment"]))
        data = frame.assign(Value=pd.to_numeric(frame["Value"], errors="coerce"))
        data = data.dropna(subset=["Value"])
        if len(treatments) == 1:
            if indifference.null_for(metric) is None:
                _note(out, f"One treatment level ({treatments[0]}): {metric} has "
                           "no indifference value, so there is nothing to test.")
                continue
            row = against_indifference(data, treatments[0], metric, str(phase),
                                       design)
            if row is not None:
                out["indifference"].append(row)
            else:
                _note(out, f"{phase}: {treatments[0]} not tested against "
                           "indifference — fewer than two values, or every "
                           "value identical.")
            continue
        comparisons, notes = plan(treatments, design.factors)
        for note in notes:
            _note(out, note)
        for comparison in comparisons:
            rows, pair_notes = compare(data, comparison, metric, str(phase), design)
            for note in pair_notes:
                _note(out, note)
            if not rows and not pair_notes:
                _note(out, f"{phase}: {comparison.label} not tested — fewer than "
                           "two groups have at least two flies with a value.")
            out["comparisons"] += rows
    return out


def _note(out: dict, text: str) -> None:
    if text not in out["notes"]:
        out["notes"].append(text)


# ---------------------------------------------------------------------------
# Mixed model (the Project's replicate-aware parametric companion)
# ---------------------------------------------------------------------------

def _mixed_fit(formula: str, data: pd.DataFrame, term: str, side: int,
               methods) -> float | None:
    """p of *term* from a per-Experiment random-intercept model, trying each
    optimiser in turn: when the replicates barely differ the random-effect
    variance sits at zero and one optimiser hits a singular matrix where
    another converges."""
    import warnings

    import statsmodels.formula.api as smf
    for method in methods:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                fit = smf.mixedlm(formula, data,
                                  groups=data["Experiment"]).fit(
                    reml=True, method=method)
            p = float(fit.pvalues[term])
        except Exception:  # noqa: BLE001
            continue
        if np.isfinite(p):
            return float(permutation.one_sided_from_two(
                p, float(fit.params[term]), side))
    return None


def mixed_p_value(data: pd.DataFrame, a: str, b: str, side: int = 0) -> float | None:
    """Treatment p from a linear mixed model on the (a, b) subset — Value ~
    Treatment with a per-Experiment random intercept — one-sided from the
    estimate's sign when *side* predicts a direction (B − A)."""
    sub = data[data["Treatment"].isin([a, b])].copy()
    if sub["Experiment"].nunique() < 2:
        return None
    sub["is_b"] = (sub["Treatment"] == b).astype(float)
    return _mixed_fit("Value ~ is_b", sub, "is_b", side,
                      ("lbfgs", "bfgs", "powell"))


def mixed_p_vs_null(data: pd.DataFrame, null: float, side: int = 0) -> float | None:
    """p that the mean differs from *null*, from an intercept-only linear
    mixed model on (Value − null) with a per-Experiment random intercept."""
    if data["Experiment"].nunique() < 2:
        return None
    sub = data.assign(Deviation=data["Value"] - null)
    ## Not lbfgs first: on an intercept-only model it raises "Singular
    ## matrix" even when the replicates differ. bfgs converges; powell is the
    ## fallback.
    return _mixed_fit("Deviation ~ 1", sub, "Intercept", side,
                      ("bfgs", "powell"))


# ---------------------------------------------------------------------------
# Entry points for the consumers
# ---------------------------------------------------------------------------

def experiment_tidy(experiment, metric: str, cutoffs=None) -> pd.DataFrame:
    """One experiment's exclusion-filtered *metric* per fly and Phase — the
    frame its Stats.txt, report tables and figures all compute on. Without
    facet cutoffs the whole recording is a single phase."""
    if cutoffs is None:
        cutoffs = getattr(experiment, "facet_cutoffs", None)
    if cutoffs:
        summary = experiment.arena.summarize_facet(cutoffs)
        windows: list = []
        for w in summary["FacetRange"]:
            if tuple(w) not in windows:
                windows.append(tuple(w))
        exp_type = getattr(experiment, "experiment_type", None)
        if exp_type is not None:
            global_cfg = (getattr(experiment, "config", None) or {}).get("global") or {}
            labels = exp_type.phase_labels_for(windows, global_cfg)
        else:
            labels = [str(w) for w in windows]
        label_of = dict(zip(windows, labels))
        phase = summary["FacetRange"].map(lambda w: label_of[tuple(w)])
    else:
        summary = experiment.arena.summarize()
        labels = ["Whole recording"]
        phase = "Whole recording"
    return tidy_frame(summary, metric, phase, labels)


def experiment_design(experiment, cutoffs=None) -> Design:
    """The comparison :class:`Design` of one (duck-typed) experiment: its
    factors, Directional Hypotheses, and the Primary Phase at *cutoffs*
    (default: its configured ones)."""
    from . import windowing
    if cutoffs is None:
        cutoffs = getattr(experiment, "facet_cutoffs", None)
    config = getattr(experiment, "config", None)
    global_cfg = (config.get("global") or {}) if isinstance(config, dict) else {}
    exp_type = getattr(experiment, "experiment_type", None)
    if cutoffs and exp_type is not None:
        labels = exp_type.phase_labels_for(
            list(windowing.facet_windows(cutoffs)), global_cfg)
    else:
        labels = ["Whole recording"]
    return Design.from_global(global_cfg, labels)


def hypotheses_sentence(design: Design) -> str:
    """One reader-facing sentence naming the declared predictions."""
    declared = design.hypotheses_text()
    if not declared:
        return ("Directional Hypotheses: none declared — every test is "
                "two-sided.")
    return ("Directional Hypotheses (declared in the design; those comparisons "
            "are tested one-sided, everything else two-sided): "
            + "; ".join(declared) + ".")


def experiment_results(experiment, metrics, cutoffs=None) -> dict:
    """``{metric: results}`` for one experiment (duck-typed: an arena, its
    cutoffs, type and config) — empty for a type that does not run the
    Permutation Test. A metric that cannot be computed is skipped."""
    exp_type = getattr(experiment, "experiment_type", None)
    if not getattr(exp_type, "permutation_stats", False):
        return {}
    config = getattr(experiment, "config", None)
    global_cfg = (config.get("global") or {}) if isinstance(config, dict) else {}
    out = {}
    for metric in metrics:
        try:
            tidy = experiment_tidy(experiment, metric, cutoffs)
        except Exception as err:  # noqa: BLE001
            print(f"Warning: could not run comparisons for '{metric}': {err}")
            continue
        res = headline_for(tidy, metric, exp_type, global_cfg)
        if res is not None:
            out[metric] = res
    return out


def headline_for(tidy: pd.DataFrame, metric: str, experiment_type,
                 global_cfg: dict | None) -> dict | None:
    """The headline results of *metric* for a type that runs the Permutation
    Test, else None. The Primary Phase is read off *tidy*'s own phases."""
    if not getattr(experiment_type, "permutation_stats", False):
        return None
    if tidy is None or "Phase" not in tidy.columns:
        return None
    labels = (list(tidy["Phase"].cat.categories)
              if isinstance(tidy["Phase"].dtype, pd.CategoricalDtype)
              else list(dict.fromkeys(tidy["Phase"])))
    return phase_results(tidy, metric, Design.from_global(global_cfg, labels))


# ---------------------------------------------------------------------------
# Reader-facing text shared by Stats.txt and the report captions
# ---------------------------------------------------------------------------

def method_lines(design: Design, project: bool = False) -> list[str]:
    """How the headline p was computed — printed once, above the numbers."""
    import textwrap

    shuffle = ("labels shuffled within each replicate and stratum" if project
               else "labels shuffled within each stratum")
    statistic = (
        "the replicate-adjusted Welch t of each pair (its contrast in Value ~ "
        "replicate + treatment, replicate a fixed effect, over a "
        "heteroscedasticity-robust HC2 standard error; signed, so a one-sided "
        "test stays one-sided)" if project else "the Welch t of each pair")
    paragraphs = [
        f"Headline test: Permutation Test — {statistic}, located among "
        f"{permutation.SHUFFLES:,} relabellings of the flies ({shuffle}; "
        "every relabelling keeps the real group sizes). Three or more levels "
        "in one comparison: max-T, family-wise like Tukey. When there are "
        "fewer distinct relabellings than that, every one is enumerated "
        f"(exact p). Fixed seed {permutation.SEED}: a re-run reproduces every "
        "p exactly.",
        ("Beside it: model p, the same replicate-adjusted Welch t on a t "
         "distribution (Satterthwaite df), and a linear mixed model "
         "(replicate random intercept); both Holm-adjusted for three or more "
         "levels. With a single replicate the model p is Welch's t-test."
         if project else
         "Beside it: Welch's t-test (Holm-adjusted for three or more levels)."),
        "One treatment level: Sign-Flip Test of PI against 0 and Percentage "
        "against 0.5 (assumes values symmetric about the null under no "
        "preference), beside the one-sample t-test.",
        "Significance is read from the permutation p alone (alpha = 0.05); "
        "the other p-values are context. Two-sided unless a Directional "
        "Hypothesis below predicts the direction. No correction across "
        "phases, strata or metrics.",
    ]
    lines = [line for para in paragraphs for line in textwrap.wrap(para, 72)]
    if design.factors and sum(1 for lv in design.factors.values() if len(lv) > 1) >= 2:
        lines += textwrap.wrap(
            "Factor Comparisons: each factor's levels are compared within "
            "every stratum of the other factors (never across them).", 72)
    hyps = design.hypotheses_text()
    if hyps:
        lines.append("Directional Hypotheses (one-sided; FinalPI and "
                     "FinalPercentage only):")
        lines += [f"  {h}" for h in hyps]
    else:
        lines.append("Directional Hypotheses: none declared (all tests two-sided).")
    return lines


def p_text(row: dict, key: str = "p_perm") -> str:
    """A p cell: the value, plus the floor when an exact test with few
    relabellings caps how small it can get."""
    p = row.get(key)
    if p is None:
        return "—"
    text = f"{p:.4g}"
    if key == "p_perm" and row.get("exact") and row.get("p_floor", 0) >= 0.01:
        text += f" (min {row['p_floor']:.2g})"
    return text


def side_mark(row: dict) -> str:
    """'' for a two-sided row, else the prediction it tested."""
    return f" [{row['hypothesis']}]" if row.get("hypothesis") else ""
