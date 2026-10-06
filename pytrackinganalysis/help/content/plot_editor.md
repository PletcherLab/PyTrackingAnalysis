# Plot Editor

The Plot Editor (`pytrack-plots`) creates Project-level publication figures from pooled replicate data. It writes `plot_specs.yaml` and vector files under `figures/`; it does not edit `project.yaml` or any `tracking_config.yaml`.

## Before opening

Run **Create report** or **Update report** first. The Plot Editor needs the Project's combined faceted summary data, and the report refresh creates it. If you open a replicate inside a Project, the app redirects to the Project root. A standalone experiment is refused with guidance to create a Project around it first.

When nothing can be drawn, the preview says why instead of showing an error. The usual cause is that no fly has a treatment assigned: every tracking region's `experimental_factors` is blank. Assign treatments on the Config Editor's **Tracking regions** tab, then re-run the analysis. The other messages are no fly having a value for the metric, every treatment hidden (tick one under **Treatments**), or none of the plot's facets in the data (tick one under **Facets**).

## What it edits

- **`plot_specs.yaml`** stores Plot Specs and Plot Styles. It is saved automatically about half a second after every edit, before another Project is opened in the same window, and on close, so the Hub's report and **Render publication figures** always see your latest edits. If the file cannot be written (a read-only folder, a Project moved away), the status bar says so.
- **`figures/*.svg`** and **`figures/*.pdf`** are saved publication outputs.

SVG text stays editable in Illustrator. PDF output embeds fonts.

## Main controls

- **Open project...** loads the Project.
- **Plot** chooses the metric figure to edit.
- **Style** chooses the shared style used by the current plot.
- **Save style as...** stores the current look under a new style name.
- **Set as project default** makes that style the default for new or reset plots.
- **Restore defaults** resets the current plot's content settings, not the shared style.
- **Save SVG...** and **Save PDF...** write vector files to `figures/`.

## Left panel

**Style (shared across plots)** controls figure size, font, theme, point styling, mean styling, geometry, line weights, strip style, and optional panel background.

**This plot** controls title, axis labels, y limits, independent y axes, reference line, p-value brackets, and whether points are marked by replicate.

**Facets** lets you include, exclude, and rename phases.

**Treatments** lets you include, exclude, rename, recolor, and reorder treatment groups.

## Project-specific options

- **Mark experiments** gives each replicate its own point shape and legend.
- **P-value brackets** use the same treatment-comparison policy as the statistics output: Welch's t-test for two treatments and Tukey HSD for more. With only one treatment shown, PI and Percentage plots print `p = <value>` above each facet's group instead of a bracket - the test against indifference (PI 0, Percentage 50%); movement and transitions plots get no label. What counts is the treatments shown in the figure, so hiding one arm of a two-arm experiment leaves one.
- **Free y** is useful for movement and transitions, where each phase may need its own scale.

## Headless rendering

The same specs can be rendered without opening the app by the Project API or a Project Script:

```
Project(project_dir).render_figures(formats=("svg", "pdf"))
```

The Project Script action is **Render publication figures**.

## Batch runs

Opening a Project here is how it opts into figures during unattended runs: each project's **`batch`** script (the default script of a Batch Run) renders a Project's publication figures only when its `plot_specs.yaml` exists, and the Plot Editor writes that file after any edit and again when it closes. A Project you never opened here skips that step rather than inventing default-spec figures. See the **Batch runs** help topic.
