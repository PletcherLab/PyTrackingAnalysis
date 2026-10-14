# PyTrackingAnalysis — Valence Permutation p

October 2026

## What the permutation p is

Every Valence experiment and Project now reports a **permutation p** for each treatment comparison, and it is the p-value results are read from. Significance, the report's highlighted rows and the brackets on figures all follow it. Welch's t-test still runs beside it as a cross-check.

- **Nothing to turn on.** It runs whenever the experiment type is Valence: Run analysis, Create report and Batch Runs all produce it.
- **What is tested:** `FinalPI`, `FinalPercentage` and `TotalDistancePerMin`, once per Phase (Acclimation, Experiment, Cooldown).
- **Why:** PI is bounded and often bimodal, so a t-test's normality assumption is shaky. A permutation test makes no such assumption.
- **It replaces `2Choice_Stats.R`.** It is the same idea the lab ran by hand in R, with that script's bugs fixed: wrong group sizes when groups were unequal, and a one-sided p whose direction depended on the alphabetical order of the level names.

## How it is calculated

The test asks how often randomly reassigned labels produce a difference as large as the real one. For each comparison:

1. Compute Welch's t between the two groups, as Welch's t-test does.
2. Shuffle the treatment labels among the flies and recompute t. Every shuffle keeps the real group sizes, so 14 WT and 11 Mutant stay 14 and 11.
3. Repeat 100,000 times, and count the shuffles at least as extreme as the real t.

```latex
p = \frac{\text{shuffles at least as extreme} + 1}{\text{shuffles} + 1}
```

The +1 means p is never 0, and its smallest value shows the test's resolution.

- **Exact when possible.** If there are fewer than 100,000 distinct relabellings, every one is used and the p is exact.
- **Reproducible.** A fixed random seed means a re-run gives the same p to the last digit.
- **Welch t, not a plain mean difference,** so a treatment whose flies are more variable cannot fake an effect.
- **Two factors** (e.g. Sex × Genotype): each factor is compared within each level of the other: Genotype within Female, Genotype within Male, Sex within WT, Sex within Mutant. Labels are only shuffled among flies sharing the other factor's level. `Female, WT` is never compared with `Male, Mutant`.
- **Three or more levels** (e.g. WT, MutA, MutB): **max-T**, where each shuffle keeps its most extreme pair. This controls false positives across the pairs, like Tukey HSD. The Welch column beside it is Holm-adjusted.
- **In a Project,** labels are shuffled only within each replicate, so a plate whose flies all happen to like Light cannot pose as a treatment effect.
- **One treatment:** a **Sign-Flip Test** of PI against 0 and Percentage against 0.5. Each fly's distance from the null has its sign flipped at random. It assumes only that values are symmetric about the null when there is no preference.

There is no correction across phases, strata or metrics, as before.

## Making it one-sided: Directional Hypotheses

All tests are two-sided unless you declare a **Directional Hypothesis**, a prediction made before the data existed. That comparison is then tested one-sided, for the permutation p, Welch's p and the mixed-model p alike.

```yaml
global:                      # design: → global: in project.yaml
  hypotheses:
  - expect: Mutant < WT      # mutants prefer Light less
  - expect: Mutant < WT
    phases: [Cooldown]       # optional; default = the Primary Phase
```

**Where to enter it**

- **One experiment:** Config Editor → Global tab → **Directional Hypotheses**. Add a row with the prediction, and the phases or blank.
- **A Project:** Project tile → **Edit config…** → **Predictions**. Predictions are part of the shared design, so every replicate must carry the same ones. When you change them, the dialog offers to write them into each replicate for you.

**Rules**

- Name levels of **one** factor (`Mutant < WT`, not `Mutant < Male`). With two factors, the prediction holds within every level of the other one.
- It covers `FinalPI` and `FinalPercentage` only; higher means more time on Light for both. Movement is always two-sided.
- Leaving out `phases` means the Primary Phase (Experiment, 10–70 min). If you change the facet cutoffs, the default phase names go away, so update `phases` too.
- With three levels, write one entry per predicted pair. Pairs you don't predict stay two-sided.
- A single treatment is predicted against indifference: `WT > indifference`.
- Saving is blocked if a prediction names an unknown level, levels of two factors, or a phase that doesn't exist.

Only declare a direction you predicted in advance. Choosing it after seeing the data roughly halves the p-value without justification.

## Where it appears in the outputs

The permutation p is written everywhere statistics already were. Run the analysis (or **Create report** for a Project) and it is there.

| Output | File | What you see |
| --- | --- | --- |
| Experiment stats | `analysis/<exp>_Stats.txt` | A header with the method, seed and declared predictions, then per phase and comparison: group n, mean and sd, the difference (B − A), `permutation p` with how it was obtained (exact or 100,000 shuffles; one-sided and which prediction), and `Welch p` |
| Experiment report | `<exp>_report.pdf` | A sentence naming any Directional Hypotheses, then the **Statistical comparisons** table, and **Tests against indifference** for one treatment |
| Project stats | `analysis/<project>_Stats.txt` | One row per metric × phase × pair: perm p, Welch p, mixed p; replicates fixed in every shuffle are listed |
| Project report | `<project>_report.pdf` | The same tables pooled across replicates, with a Mixed p column |

**Reading the report table**

- **Permutation p** is the headline. It alone decides **Significant** and the green row highlight.
- **Welch p** (and **Mixed p** in a Project) is context. When they agree, good; when they disagree, look at the data.
- **Comparison** appears with two or more factors, e.g. `genotype within Female`.
- **†** after a p means it was tested one-sided; the caption names the prediction.
- **(min 0.1)** after a p means an exact test with so few flies that no smaller p was possible.
- For one treatment, the columns are **Sign-flip p** (headline) and **t-test p**.

## Putting it on plots

Publication figures show the permutation p as brackets over each pair, with the same numbers as Stats.txt.

1. Open the Project in the **Plot Editor** (Project panel → **Plot editor…**, or run `pytrack-plots`).
2. Choose the plot (PI, Percentage, movement…).
3. Under **This plot**, tick **P-value brackets: Permutation p per facet**. It is saved to `plot_specs.yaml` automatically.
4. Save the SVG/PDF, or let **Render publication figures** and the Project report use it. Both read the same `plot_specs.yaml`.

**What the brackets show**

- Each bracket is the headline permutation p, **looked up** from the statistics, never recalculated from what the figure shows.
- A one-sided result is labelled, e.g. `0.012 (one-sided)`.
- With two factors, only within-stratum pairs get a bracket (`Female, WT` – `Female, Mutant`), never diagonal pairs.
- **Hiding a treatment removes the brackets that need it and leaves the others unchanged.** Hiding one arm of a two-treatment experiment shows no p at all, because no single-treatment test was run.
- A single-treatment experiment shows `p = …` above the group: its Sign-Flip p against indifference.

If the checkbox still reads “Welch / Tukey per facet”, the loaded Project is not a Valence Project.

## Reading the result: things to know

- **Small plates have a floor.** One Small Arena plate with 3 WT and 3 Mutant has only 20 possible relabellings, so no two-sided p can go below 0.1, however large the effect. The output says `(min 0.1)`. Pool replicates in a Project to get past it.
- **A result against your prediction** keeps its large one-sided p (e.g. 0.97). It is reported as not supporting the prediction, never re-run two-sided.
- **Permutation and Welch usually agree closely** with typical group sizes. A large gap points to outliers, very unequal spreads or very small groups; look at the plotted points.
- **Permutation and mixed p disagreeing in a Project** suggests replicates differ. Check the per-replicate table in the report.
- **Older reports are not updated automatically.** Re-run the analysis, or Create report, to get the new statistics. Valence results that previously used Tukey HSD now use within-stratum Welch or Holm beside the permutation p.
- **Custom experiments are unchanged.** They keep Welch's t-test and Tukey HSD. To get the permutation p, the experiment must be typed as Valence.

For more detail, open **Help → Valence statistics** in any of the apps.
