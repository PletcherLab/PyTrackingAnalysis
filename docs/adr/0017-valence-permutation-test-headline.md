# Valence headlines a Permutation Test; comparisons are factor-aware

## Context

The lab treats Valence PI and Percentage data as non-normal: they are bounded,
often bimodal, and from small plates. It ran its real statistics by hand in R
(`2Choice_Stats.R`): shuffle the PI values, recompute the Welch t, and count
how often the shuffled t reaches the observed one. That script had four bugs:

- It split the pooled values in half whatever the group sizes were, which
  gave the wrong null with unequal groups and dropped a fly when n was odd.
- Its p was one-sided, in a direction set by the alphabetical order of the
  level names.
- It tested Genotype with the sexes pooled.
- Its p could come out as 0.

Meanwhile the app computed p-values in four places: Stats.txt, the report
tables, the Project's pooled and mixed tables, and the publication figure
brackets. All four ran Welch/Tukey on the **flat Treatment label**. A
Sex × Genotype design therefore got a four-arm Tukey, including diagonal pairs
such as `Female, WT` vs `Male, Mutant` that nobody asks about, and the
brackets were recalculated on whatever treatments a Plot Spec showed.

## Decision

**The Permutation Test is the Valence headline, and Welch always runs beside
it.**

- Per Phase, every tested metric (FinalPI, FinalPercentage,
  TotalDistancePerMin) is tested by shuffling treatment labels between flies.
  Every shuffle keeps the real group sizes.
- The statistic is the Welch t, which makes this a studentized permutation
  test, valid when one treatment's flies are more variable than the other's.
- p = (shuffles at least as extreme + 1) / (shuffles + 1), with 100,000
  shuffles and a fixed seed. When there are fewer distinct relabellings than
  that, all of them are enumerated and the p is exact.
- When few relabellings cap the result, the output states the smallest
  achievable p. 3 vs 3 on one Small Arena plate cannot go below 0.1.
- The permutation p alone decides significance. The Welch p, and in a Project
  the mixed-model p, are context.
- We rejected a switch between the two methods: the computation is cheap, and
  a choice of test made after seeing the results is a forking path.

**Comparisons read the design factors.**

- With two or more varying factors, each factor's levels are compared within
  every Stratum of the others, and labels are shuffled only inside the
  Stratum.
- With one factor, the Treatments themselves are compared.
- Three or more levels use **max-T** for the family-wise error rate. The
  parametric column becomes Welch with a **Holm** correction instead of Tukey,
  because Tukey HSD has no one-sided form.
- Correction stops at the comparison: phases, strata and metrics stay
  uncorrected, as everywhere else in the app.

**Replicates are strata.** In a Combined Analysis, labels move only between
flies recorded together, so a plate effect cannot pose as a treatment effect.
This is the non-parametric counterpart of the mixed model. A replicate that
holds a single level is fixed in every shuffle, and the output names it.

**One treatment gets a Sign-Flip Test** of PI against 0 and Percentage
against 0.5. It assumes values are symmetric about the null under no
preference, and it sits beside the one-sample t-test.

**Directional Hypotheses are part of the design.**

- They are declared as `hypotheses: [{expect: "Mutant < WT", phases: [...]}]`
  in `global:`, or in `design.global` for a Project.
- They turn the matching comparisons one-sided: the permutation, Welch and
  mixed-model p alike.
- They cover FinalPI and FinalPercentage, in every Stratum, and only the
  listed phases (the Primary Phase by default).
- They are validated against the factors and the current phase names, and
  hard-validated across replicates as predictions, not as text.
- When the Hub dialog changes a Project's hypotheses, it offers to write them
  into existing replicates. A prediction depends on no replicate's data, and
  without this the Project would refuse to load.
- A per-stratum (`within:`) prediction was deferred until someone needs one.

**One computation, many readers.** `comparisons.py` plans and runs the tests
on one tidy frame layout and caches results by content. Stats.txt, the
report tables and the figure brackets therefore read the same rows. A figure
looks its brackets up, so hiding a level can never change a family, and only
pairs that form an actual comparison get a bracket. `permutation.py` is the
numerics only.

**Valence policy, not tracker policy.** The policy is enabled by
`ExperimentType.permutation_stats` (ADR-0002). A Custom Experiment on the same
two-choice tracker keeps flat Welch/Tukey, because Custom promises no
constraints. Another type can opt in with one line; Paired Open Field could,
shuffling Pairs (ADR-0013).

## Consequences

- Re-running an existing Valence experiment or Project changes its
  statistics:
  - flat Tukey on four labels becomes within-stratum Welch;
  - three-level Tukey becomes Holm;
  - the Significant flag and the highlights follow the permutation p.

  Old reports keep their numbers until they are regenerated.
- A Valence Stats.txt, report table and figure no longer match a Custom run
  on the same data. That difference is deliberate.
- The figure for a Valence experiment with two treatments, one of them
  hidden, draws no p. No single-treatment test was run, and nothing is
  recalculated on what is shown.
- An exact test on small plates can be unable to reach 0.05. The output
  says so rather than letting `p = 0.1` read as "nearly significant".
