# Valence statistics

PI and Percentage are bounded and often bimodal, so a Valence Experiment does not rest its result on a t-test. Its headline p-value comes from a **Permutation Test**, the non-parametric test the lab used to run by hand in R (`2Choice_Stats.R`). Welch's t-test still runs beside it, so you can see whether the two agree.

## The Permutation Test

For each phase and each tested metric (`FinalPI`, `FinalPercentage`, `TotalDistancePerMin`):

1. Compute Welch's t between the two groups, exactly as Welch's t-test does.
2. Shuffle the treatment labels among the flies and recompute t. Every shuffle keeps the real group sizes: 14 WT and 11 Mutant stay 14 and 11.
3. Repeat 100,000 times. The p-value is the share of shuffles at least as extreme as the real t, computed as (count + 1) / (shuffles + 1). It is never 0.

Welch's t is used rather than a plain difference in means because it keeps the test honest when one treatment's flies are more variable than the other's.

When fewer than 100,000 distinct relabellings exist, every one is enumerated and the p is **exact**. A fixed seed means re-running the analysis reproduces every p to the last digit.

**Small groups have a floor.** One Small Arena plate holding 3 WT and 3 Mutant has only 20 relabellings, and two of them (the real one and its mirror) are always the most extreme. No two-sided p can fall below 0.1, however large the effect. When an exact test is capped like this, the output says so: `0.1 (min 0.1)`. Pool replicates in a Project to get past it.

## What is compared

- **One factor** (e.g. `Genotype: WT, Mutant, Rescue`): the treatments are compared with each other.
- **Two or more factors** (e.g. `Sex` × `Genotype`): each factor is compared **within every level of the others**. The comparisons are Genotype within Female, Genotype within Male, Sex within WT and Sex within Mutant. `Female, WT` is never compared with `Male, Mutant`. Labels are shuffled only among flies that share the other factors' levels.
- **Three or more levels** in one comparison use **max-T**: each shuffle keeps its most extreme pair. Like Tukey HSD, this controls the error rate across the pairs. The Welch column beside it is Holm-adjusted.
- **One treatment** has nothing to compare against, so PI is tested against 0 and Percentage against 0.5 with a **Sign-Flip Test**. Each fly's distance from the null value has its sign flipped at random. It assumes only that, under no preference, values are symmetric about the null.

No correction is applied across phases, strata or metrics.

## In a Project

Two things change when there are several replicates:

- **Shuffles stay within each replicate.** Labels move only between flies recorded together, so a replicate whose flies all happen to prefer Light cannot pose as a treatment effect.
- **The statistic adjusts for replicate.** In place of a t over all flies pooled, each comparison uses the treatment's effect in the model `Value ~ replicate + treatment`, with replicate as a fixed effect. Its standard error is heteroscedasticity-robust (HC2), so unequal spreads are still handled the way Welch's t handles them. A replicate whose flies all run high or low no longer inflates the noise the comparison is measured against. With a single replicate this statistic is exactly Welch's t.

The statistic is signed (it is the t, not an F), so a declared direction stays one-sided.

Beside the permutation p, the Project shows:

- **Model p:** the same t on a t distribution.
- **Mixed p:** from a mixed model with replicate as a random effect.

A replicate holding only one level of a comparison (an all-WT plate, say) can swap nothing. Its flies keep their labels in every shuffle, and Stats.txt names it. If two treatments were never recorded in the same replicate, their difference cannot be separated from the replicate's, and the pair is reported as not testable.

## Directional Hypotheses

Two-sided by default. If you predicted a direction **before the data existed**, declare it in the design and that comparison becomes one-sided. This applies to the permutation, Welch (or model) and mixed-model p alike. A name that holds `>` itself, as fly genotypes often do, works when the comparison sign has spaces around it: `Nanchung>Chrimson > WCS>Chrimson`.

```
global:
  hypotheses:
  - expect: Mutant < WT          # mutants prefer Light less
  - expect: Mutant < WT
    phases: [Cooldown]           # optional; default = the Primary Phase
```

- A prediction names levels of **one** factor and holds within every level of the others.
- It covers `FinalPI` and `FinalPercentage` together, because higher means more time on Light for both. `TotalDistancePerMin` is always two-sided.
- Pairs you do not predict stay two-sided. With three levels, write one entry per predicted pair.
- A single treatment is predicted against indifference: `WT > indifference`.
- A phase that does not exist at the current cutoffs is a design error. Change `facet_cutoffs` and the default phase names go away, so update `phases`.
- A result in the opposite direction keeps its large one-sided p. It is not re-run two-sided.

Declare predictions in the Config Editor (**Directional Hypotheses**) or, for a Project, in **Edit config...** (**Predictions**). They are part of the shared design, so every replicate must carry the same ones. When you change them in a Project that already has replicates, the dialog offers to write them into each replicate for you.

## Reading the output

- **Stats.txt** opens with how the p-values were computed, the seed, and the declared predictions. Each test then lists its groups, the difference (B - A), the permutation p with how it was obtained, and the Welch p.
- **Report tables** lead with **Permutation p**. It alone decides **Significant** and the row highlight. A dagger (†) marks a one-sided test, and the caption names the prediction.
- **Publication figures** bracket the headline permutation p, looked up from the same results, never recomputed on the treatments a figure shows. Only pairs that are a real comparison get a bracket, and a one-sided p is labelled `(one-sided)`.

A Custom experiment on the same two-choice tracker keeps Welch's t-test and Tukey HSD on the treatment labels.
