# Publication figures: a plotnine Style+Spec path beside the report

## Context

Valence experiments need journal-ready figures — vector output that opens in
Adobe Illustrator with editable text, a consistent look across every figure in
a paper, and an editor (the fourth PyQt6 app) to adjust, regenerate, and save
them. The existing matplotlib report figures are pipeline output, not
publication material. Three questions had genuine alternatives: which engine
renders publication figures (and how far any migration goes), what the editor
edits (one flat config vs a reusable-look layer), and where that state lives.

## Decision

- **plotnine renders Publication Figures; the PDF report stays matplotlib.**
  The two paths share the same summarized, exclusion-filtered data — never
  rendering code. plotnine's ggplot grammar supplies the faceting and theming
  quality the report's hand-rolled panels lack, proven first on the faceted
  PI plot. We rejected migrating all ~10 report figure types at once (big-bang
  visual churn before the first publication figure exists) and restyling
  matplotlib harder (keeps paying the hand-rolled-faceting cost the move is
  meant to end). Report figures may migrate later, one at a time, if the
  results convince.
- **Two-layer model: Plot Style + Plot Spec.** A named Style holds everything
  that must look identical across figures (size, theme, fonts, point/mean
  styling, treatment→color mapping); a per-plot Spec holds content decisions
  (labels, facet/treatment inclusion and order, y-limits, reference line,
  per-facet p-values) plus its Style's name. P-values follow the statistics
  policy — Welch/Tukey brackets between shown treatments, and for a single
  shown treatment a `p = …` label for its test against indifference
  (ADR-0005 amendment 2026-10). "Save style as…" captures the current look;
  `default_style` names the one the editor auto-loads. A flat per-plot config
  with copy-from was rejected: nothing would enforce consistency after the
  copy, and no single artifact would *be* the project's look.
- **Presentation state lives in its own file, `<project>/plot_specs.yaml`**
  (`styles:` / `default_style:` / `plots:`), written only by the Plot Editor.
  Keeping it out of `tracking_config.yaml` preserves ADR-0001's line — the
  config defines the experiment; styling is presentation — and spares the
  Config Editor from round-tripping a section it doesn't understand.

  **Amendment (2026-10): the editor saves as you edit.** Saving only on
  Save/close made the file lag the screen while the Hub's Project Report and
  `render_publication_figures` — other processes — read it, so a ticked
  option silently missed the report. Every edit now reaches the file within
  about half a second (and before another project is opened), written
  atomically so a concurrent reader never sees half a file; a failed write is
  a status-bar message, not a crash.
- **Vector output is SVG with `svg.fonttype='none'`** (labels stay live text
  in Illustrator), with PDF as a secondary option; default face Arial with
  fallbacks. Outlined-text SVG was rejected because relabeling in Illustrator
  is the exact workflow being replaced. Figures save to `<project>/figures/`,
  beside — not inside — `analysis/`, whose manifest/stale-artifact logic must
  not see hand-curated files.

## Consequences

- Two plotting engines coexist deliberately; new *publication* work goes to
  plotnine, report maintenance stays matplotlib until a figure type is
  consciously migrated.
- A figure is always regenerable: the Spec+Style that produced it is versioned
  in `plot_specs.yaml`, which also enables future headless batch re-render.
- Styles are per-project for now; a style's treatment→color map keys treatment
  names, so reusing a style across projects with different treatments needs an
  ordered-palette fallback (planned, not built).
- Editable-text SVG requires the chosen font on the machine that opens the
  file; the PDF option (fonts embedded) is the escape hatch.
- New runtime dependency: `plotnine` (pulls `mizani`; matplotlib/pandas/
  statsmodels already present).
