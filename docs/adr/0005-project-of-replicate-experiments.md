# A Project is a marker-file parent of replicate Experiments

## Context

Real studies run the same design several times. The app's unit was one
recording directory (until now called the "project directory"), with a bolt-on
batch mode that ran sibling directories independently and a Combine-summaries
tool that stacked their CSVs. There was no first-class object for "these N
directories are replicates of one design", no combined statistics, no
combined publication figures, and no report above the single recording.

## Decision

- **Identity by marker file.** A **Project** is a directory containing
  `project.yaml`; its **Experiments** (replicates) are auto-discovered as
  immediate subdirectories holding a `tracking_config.yaml` — exactly the
  existing batch layout, upgraded in place by dropping in the marker. A bare
  Experiment directory opened alone keeps working unchanged. We rejected
  inference-only (an innocent folder of unrelated experiments must not
  silently become a Project) and an out-of-tree registry (the Project must
  travel with its data). Terminology shifts accordingly: the single-recording
  unit is now the *Experiment directory*.

  **Amendment (2026-10): the name follows the folder.** `project.yaml` stores
  `name:` only when it differs from the Project folder's own name; absent,
  the Project is named after its folder. Writing the default pinned the name
  at creation, so a `project.yaml` copied into a sibling folder (the natural
  way to start the next line of a screen) wrote every sibling's report and
  pooled CSVs under the original Project's name. A name typed differently in
  Create/Edit Project is still stored — a copied *custom* name still travels,
  which is the one case the marker cannot disambiguate.
- **Hard design-match validation.** At Project load, every replicate must
  agree on `experiment_type` and on experimental-design factor names AND
  levels; a mismatch is a load error naming the offender (the ADR-0001
  fail-hard philosophy — pooling by treatment is garbage if levels diverge).
  Region assignments, fly counts, rigs, facet cutoffs, and quality criteria
  may differ; differing cutoffs/criteria are surfaced, not fatal.

  **Amendment (2026-08): `project.yaml` is the design authority.** The
  original decision kept the canonical design implicit (what all replicates
  agree on). It is now explicit: `project.yaml` carries a `design:` section —
  `design.global` holds the shared parameters (experiment type, design
  factors and levels, facet cutoffs and labels, quality criteria, and any
  other global key placed there), and `design.counting_regions` fixes the
  counting-region NAMES in order — and every replicate's *resolved* config is
  hard-validated against it (a config that omits a key the type defaults
  still matches a design stating that default). Aliases and region→treatment
  assignments remain per-experiment. The Create/Edit Project dialog edits the
  design, seeds it from the Experiment Type's defaults, and infers it from an
  existing first replicate when wrapping old directories; Add Experiment
  scaffolds new replicate configs FROM the design, so an empty Project (no
  replicates yet) is now legal. Projects without a `design:` section keep the
  original agreement-based validation.
- **Combined Analysis from filtered summaries, not a mega-arena.** The
  Project stacks each replicate's saved, exclusion-filtered summary CSVs with
  an `Experiment` column (formalizing Combine-summaries into
  `<project>/analysis/`). Re-loading all raw data into one Arena was rejected:
  N× load cost, duplicated exclusion logic, and everything the combined layer
  needs already lives in the summaries.
- **Statistics: pooled tests plus a mixed-model companion.** Primary tables
  use the familiar per-fly policy (Welch/Tukey) on the pooled flies —
  matching what the plots show. Beside them, a linear mixed model per metric
  (treatment fixed effect, experiment random intercept) accounts for
  between-replicate variation; where the two disagree, that divergence is
  itself the finding. Pooled-only (pseudoreplication bait), mixed-only
  (fragile at 2 replicates, mismatched to the pooled dots), and
  experiment-means-as-n (discards fly-level information) were rejected.

  **Amendment (2026-10): a single treatment is tested against
  indifference.** With one treatment level there is no pair to compare, and
  "Not applicable" left single-genotype screens with no inference at all. Yet
  PI and Percentage carry a built-in null — 0 and 0.5 mean no preference
  between the counting regions — so each phase's `FinalPI` / `FinalPercentage`
  is tested against it with a two-sided one-sample t-test (the one-sample
  counterpart of the Welch policy), at both levels: the experiment's
  `*_Stats.txt` and report, and the Project's pooled test beside an
  intercept-only mixed model (experiment random intercept) on the deviation
  from the null — the same pooled + mixed pairing as above. Metrics with no
  natural null (movement, transitions) stay "Not applicable". The test lives
  in one module (`indifference.py`) so Stats.txt, both reports and the
  publication figures' `p = …` label cannot disagree. A phase whose values
  are all identical is reported as untestable rather than as p = 0. The
  mixed-model p shares this section's caveat: with two or three replicates
  its normal approximation is optimistic, so a disagreement with the pooled p
  is the signal, not either number alone.
- **The Project Report uses the publication renderer.** Pooled figures in
  `<project>_report.pdf` come from the plotnine Spec/Style system
  (`plot_specs.yaml` at the project root), so the report shows exactly what
  the paper will — a deliberate, contained exception to ADR-0004's
  "report stays matplotlib", which continues to hold for the per-experiment
  report. Plus the pooled + mixed stats tables (Tests against Indifference
  for a single treatment), a per-replicate health table,
  and an opt-in AI narrative through the existing `ai/` stack (summarizes the
  pipeline's numbers, never computes its own; failures never block the
  report).
- **The Hub becomes project-centric.** Opening a Project shows an Experiments
  table with per-replicate status and a Project card (Run all, Build combined
  analysis, Project report, Plot editor); the Single/Batch radio mode is
  absorbed, and batch tools move into the Project view. The Plot Editor is
  **Project-level only**: `plot_specs.yaml` and `figures/` live at the
  project root; opening a replicate redirects up to its Project, and a
  standalone experiment is refused with guidance.

## Consequences

- Directory contract change: docs, Hub, and wizard speak two levels now;
  existing batch parents upgrade by adding `project.yaml`, and nothing forces
  migration of standalone experiments.
- The combined layer depends on replicates having been analyzed (their
  summary CSVs exist); building it reports which replicates are missing
  rather than silently analyzing them.
- Pooled publication figures gain an optional per-experiment marker (point
  shape) so batch structure can be shown without re-plotting.
- Renaming "project directory" → "Experiment directory" leaves stale wording
  in older notes/history; CONTEXT.md is the authority.
