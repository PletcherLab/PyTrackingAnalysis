# Outputs

PyTrackingAnalysis writes outputs at two levels: per-experiment outputs inside each replicate, and Project outputs at the Project root.

## Experiment outputs

For one loaded replicate, results are written relative to that Experiment Directory.

### `analysis/`

- `*_experiment_summary.txt` - rig, design, quality overview, and per-tracker table.
- `*_Summary.csv` - per-tracker summary statistics after any exclusions.
- `*_Summary_Facet.csv` - the same summaries split by `facet_cutoffs`.
- `*_Excluded.csv` - every fly left out of the analysis, with a `Reason` column: the experimenter's own removal (`Removed: dead at ~20 min`), the Valence low-transition criterion, or both on one row.
- `*_Stats.txt` - faceted pairwise comparisons across treatments: Welch's t-test for two, Tukey HSD for more. A phase with only one treatment has nothing to compare, so FinalPI is tested against 0 and FinalPercentage against 0.5 instead, in **Test against indifference** blocks (two-sided one-sample t-test); other metrics say `Not applicable`.
- `*_Stats_flat.txt` - the same tests when run without faceting.
- `*_plot_*.png` - saved experiment-level figures.
- `*_Notes.txt` - optional run notes entered from the Hub.
- `*_AI_Summary.txt` - optional experiment AI Summary; deleted by the next `run_analysis()`.

The experiment PDF report, `<experiment>_report.pdf`, is written to the Experiment Directory root beside `tracking_config.yaml`. With only one treatment, its statistics are a **Tests against indifference** table: mean, indifference value, difference and p-value for PI and Percentage in each phase.

### `qc/`

- `*_data_quality.csv` - per-tracker fraction of high-quality, not-found, and indiscernible frames.

The QC Viewer also shows per-tracker diagnostic plots when you select a row.

## Project outputs

Project-level outputs are written at the Project root. `<project>` is the Project folder's name, unless `project.yaml` sets a different `name:`.

- `analysis/<project>_Summary.csv` - Combined Analysis: filtered replicate summaries stacked with an `Experiment` column.
- `analysis/<project>_Summary_Facet.csv` - combined faceted summaries, when available.
- `analysis/<project>_Excluded.csv` - excluded flies across replicates.
- `analysis/<project>_Stats.txt` - pooled per-fly tests plus mixed-model p-values that account for replicate-to-replicate variation. With only one treatment, a **Tests against indifference** section tests PI and Percentage the same two ways: all flies pooled, and an intercept-only mixed model with a per-experiment random intercept (only with two or more replicates).
- `analysis/<project>_AI_Summary.txt` - optional Project AI narrative; deleted by the next Combined Analysis build and then recreated by **AI narrative...** when requested.
- `plot_specs.yaml` - Plot Editor specs and styles.
- `figures/*.svg` and `figures/*.pdf` - pooled publication figures.
- `<project>_report.pdf` - Project Report with pooled figures, statistics (a **Tests against indifference** table, with pooled and mixed-model p, when there is only one treatment), replicate summary, and any saved AI narrative. In the Hub, **Create report** / **Update report** refreshes all replicate analyses, rebuilds Combined Analysis, and then writes this PDF.

## Batch outputs

A Batch Run writes only into each Project: their replicates' analyses and the Project-level Combined Analysis, figures, and reports listed above. The Batch folder itself gains at most a `batch.yaml`; there are no batch-level analysis outputs. See the **Batch runs** help topic.

The Hub has no folder-opening buttons: a Project has an `analysis/` of its own *and* one per replicate, so *Open analysis folder* had no single target, and *Open qc folder* was one click on a path the run already logs. Open the folder you want from the paths listed above.
