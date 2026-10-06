# Project actions

The Project panel's **Analysis** card runs work across the replicate set. Use it after the Project has a valid `project.yaml` and each replicate has a configured `tracking_config.yaml`.

## Replicate table

The table shows each configured replicate plus any data-looking folder that is still missing a config.

- **Config** is `yes` or `missing`.
- **Flies** is the analyzed fly count, `not analyzed`, or `no data`.
- **Excluded** counts the flies left out of the analysis, with the experimenter's own removals in brackets (`7 (4 removed)`), and says `re-run needed` when removals were declared after the last run. **Flagged** shows the low-movement count.
- **Report** shows whether the per-replicate PDF exists.

Double-click a configured row to load that replicate and run QC. Double-click a missing-config row to create its config from the Project design.

## Setup actions

- **Experiment configs...** opens the config manager for all immediate subdirectories. Create missing configs, create all missing configs, or open an existing replicate in the Config Editor.
- **Create experiment...** creates a new replicate directory and scaffolds its config from `project.yaml`, then asks how to finish it: **Edit config...** opens the scaffold in the Config Editor, **Copy config from...** replaces it with a config from an experiment that already works (checked against the project design before it is written, and refused with the scaffold left intact if it would not conform). See the **Creating experiments** help topic.
- **Initialize existing directory...** adopts a directory already in the Project that has no config: it files any loose recording into `data/`, scaffolds the config, and opens the Config Editor.

## Project pipeline

The Hub has one full Project refresh button:

- **Create report** appears when `<project>/<project>_report.pdf` does not exist.
- **Update report** appears after the Project report exists.

Both labels run the same sequence: unload any currently loaded replicate, analyze every configured replicate, create each per-replicate report, rebuild the Project Combined Analysis under `<project>/analysis/`, and write `<project>/<project>_report.pdf`. If any replicate fails, the Combined Analysis and Project report are not rebuilt.

Use the other Project actions around that full refresh:

- **Plot editor...** - open the Project-level publication figure editor after a report refresh has created combined faceted data. Edits are saved to `plot_specs.yaml` as you make them; click **Update report** to rebuild the PDF with them.
- **AI narrative...** - write a Project AI narrative from the current Combined Analysis and rebuild the Project report so the narrative is embedded. The prose is also saved as `analysis/ai_narrative.md` for later searching.
- **View reports** - open the Project report and every per-replicate report at once, each handed to your desktop's PDF viewer. Enabled only when both kinds exist; the tooltip says which half is missing when it does not.
- **Removed regions...** - declare tracking regions to leave out of the analysis. See below.

Every file this sequence writes - per-replicate `analysis/`, the Project's own `analysis/`, `figures/`, and both kinds of PDF - is listed in the **Outputs** help topic.

Rebuilding Combined Analysis deletes any saved Project AI narrative because the narrative describes one specific combined result. Because **Create report** and **Update report** rebuild Combined Analysis, run **AI narrative...** after the final report refresh if you want AI prose in the PDF.

## Removed regions

Some flies are lost in ways no automatic criterion can see: the fly died partway through, escaped during transfer, or the well was empty from the start. Only you know, so you enter it by hand.

**Removed regions...** opens a checklist of every tracking region of every replicate in the Project. Tick a region, type why, and Save. The reason is free text; a ticked region with nothing typed is recorded as `Undefined`.

The **Data** column says `no data` for a region that produced no tracker at all in the replicate's last analysis - an empty well, or one the recording never picked up. It stays blank for a replicate that has not been analyzed yet (nothing is known), and a region that is merely excluded is not `no data`: it had a fly, and the analysis says why it left.

What a removal does:

- Every fly in that region is excluded from figures, summary measures, statistics and the summary CSVs - the same treatment as a fly failing an automatic criterion.
- Each one is listed with its reason in the experiment report, in `analysis/<name>_Excluded.csv`, and in the Project report's **Excluded flies** table.
- Quality-control output still shows it: QC describes the recording, not the analysis population.
- Removal is all-or-nothing. A dead fly still registers as occupancy wherever it died, so its numbers are unreliable throughout - there is no "exclude only after minute 45".

The declaration is saved as `removed_regions.yaml` at the experiment's own directory root, so it travels with the recording:

```yaml
removed_regions:
  T_14: dead at ~20 min
  T_22: escaped during transfer
```

It is deliberately not part of `tracking_config.yaml`: that file is the design specification, and how a run turned out is not design. Edit the file by hand if you prefer - the window and the file are the same thing.

Removals declared after a replicate was analyzed do not change results until it is analyzed again. The replicate table says `re-run needed`, and a Project Script that skips analyzed replicates re-runs those anyway.

## Removal sheets

For many replicates at once, keep the notes in a spreadsheet. Put `removed_regions.csv` (or `.xlsx`) at the **batch folder** - or at a Project root - with these columns:

| project | experiment | region | reason |
|---------|-----------|--------|--------|
| Starved2026 | Rep3 | T_14 | dead at ~20 min |
| Starved2026 | Rep3 | T_22 | escaped |

`project` is only needed at a batch folder. A missing `reason` becomes `Undefined`.

Applying the sheet writes its rows into each experiment's `removed_regions.yaml`; nothing reads the sheet at analysis time, so a Project keeps its removals wherever you copy it. It is applied:

- automatically at the start of a **Batch run**, before any Project script runs;
- when you press **Apply removal sheet...** in the Batch panel, or open **Removed regions...** on a Project that has one.

Selecting a folder never applies anything on its own - browsing a colleague's batch folder must not rewrite their experiments.

Applying is additive and safe to repeat. A region already declared keeps the reason it has: if the sheet gives a different reason, the row is reported as a **conflict** and the standing reason is kept, so a wording you refined in the window is never reset by the spreadsheet. To un-remove a region, untick it in the window - deleting the row from the sheet does not.

Every row is reported: `applied`, `already declared`, `conflict`, or one of `unknown project` / `unknown experiment` / `unknown region` when the name matches nothing. Nothing here ever aborts a run - a stale note must not kill an overnight batch - so check the log for rows that did nothing.

The **Removed regions** help topic covers all of this in detail: what a removal does to the analysis, how it interacts with the automatic checks, when a replicate needs re-running, and every place a removal is reported.

## Project scripts

The Project **Script** picker runs Project Scripts from `project.yaml`, listing that file's own scripts first. Every Project is created with one already written there, named **`batch`** - the script a Batch run executes in this Project - so you can open the Script Editor and read exactly what the default run does.

Each script action mirrors a button on this card. **Create / update project report** is therefore the whole **Create report** sequence in one step - analyze every replicate, pool the results, build the PDF - and nothing needs to run before it. (There is no Run-all or Build-combined button, so there is no such action either; both were folded into the report step. Scripts saved before that still run, and the Script Editor flags the old steps so you can delete them.)

**Report pipeline** runs:

1. create / update project report
2. render publication figures - skipped when the Project has no `plot_specs.yaml`

**Standard pipeline (built-in)** is the same, with a `validate design` gate first. Both built-ins appear in the Project and Batch script pickers, below your own scripts.

Use **Edit scripts...** to author custom Project Scripts or centrally held Experiment Scripts. If you need something more specific than the Hub's full report refresh, use a Project Script: scripts still expose figure-rendering, report, AI, design-validation, and `run_in_experiments` steps for custom automation. `run_in_experiments` takes an optional `only:` list of replicate directory names; blank means all replicates, and the Script Editor shows `only:` as a checkable list of the Project's replicates. Before starting a script, the Hub pre-checks it and aborts on a mistyped replicate name or an experiment-script name that resolves nowhere; during a run, a name that matches no replicate is logged, counted in the failure summary, and the remaining replicates still run.

To run one script across many sibling Projects unattended, see the **Batch runs** help topic.
