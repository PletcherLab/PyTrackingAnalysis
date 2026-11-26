"""Tests for the Hub tile-strip redesign (ADR-0007), the Project-first Hub
(ADR-0008), the Batch level (ADR-0009), and the Experiment group tile
(ADR-0012): the ribbon's three container tiles and the four experiment-level
tiles the Experiment tile expands into, their live summaries and dimming
across loading states, the anchored panels (open/close/one-at-a-time/
auto-close-on-launch), loading an experiment by double-clicking its row in
the replicates table, and the Batch panel's projects table, script picker,
and Batch Run."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from test_project import _make_project, qapp  # noqa: F401  (fixture reuse)


@pytest.fixture
def hub(qapp):  # noqa: F811
    from pytrackinganalysis.apps.hub import HubWindow

    win = HubWindow()
    win.resize(1400, 900)
    win.show()
    qapp.processEvents()
    yield win
    win.close()


#: Every tile, in ribbon order: the strip's three container tiles, then the
#: sub-strip's four experiment-level tiles (ADR-0012).
CONTAINER_TILES = ["batch", "project", "experiment"]
SUBTILES = ["analyze", "plots", "scripts", "ai"]
TILE_ORDER = CONTAINER_TILES + SUBTILES


def _fake_exp(name="Rep1", tracking=None, n_total=None, excluded=None,
              flagged=None):
    """The least an object needs to count as the loaded experiment for the
    tile refresh (its lines are read from attributes, never summarized)."""
    from types import SimpleNamespace

    import pandas as pd

    flagged_df = None
    if n_total is not None or flagged is not None:
        flagged_df = pd.DataFrame({"fly": list(range(flagged or 0))})
        flagged_df.attrs["n_total"] = n_total
    exp = SimpleNamespace(
        arena=SimpleNamespace(experiment_name=name),
        flagged_flies=flagged_df,
        excluded_flies=None if excluded is None else list(range(excluded)))
    if tracking is not None:
        exp.parameters = SimpleNamespace(
            get_tracking_type=lambda: SimpleNamespace(name=tracking))
    return exp


def _accept_preflight(monkeypatch, keys=None, apply_removals=True):
    """Run the real preflight dialog but never block on it (ADR-0011).

    The dialog is built for real — so discovery, the member tree, and the
    sheet preview are all exercised — and only ``exec`` is replaced, which is
    the one call that would wait forever with no user in a headless run.
    """
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog

    from pytrackinganalysis.apps import batch_preflight

    def _exec(self):
        if keys is not None:
            for index in range(self._tree.topLevelItemCount()):
                item = self._tree.topLevelItem(index)
                item.setCheckState(
                    0, Qt.CheckState.Checked
                    if item.data(0, batch_preflight._KEY_ROLE) in keys
                    else Qt.CheckState.Unchecked)
        self._sheet_box.setChecked(apply_removals)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(batch_preflight.BatchPreflightDialog, "exec", _exec)


def _make_batch(tmp_path, names=("P1", "P2")):
    """A Batch on disk: Projects in subdirectories, plus one non-Project
    child that must never be listed."""
    for name in names:
        (tmp_path / name).mkdir()
        _make_project(tmp_path / name)
    (tmp_path / "notes").mkdir()
    return tmp_path


def test_ribbon_has_three_container_tiles_over_four_sub_tiles(hub):
    assert list(hub._tiles) == TILE_ORDER
    assert not hasattr(hub, "_sidebar")
    # Tools is gone: its one lasting action (Validate YAMLs) moved to the
    # Project panel, and the width it held went to the status readout.
    assert "tools" not in hub._panels
    assert "tools" not in hub._cards
    # Experiments load from the Project panel's table — the Experiment tile
    # is a group (it expands the sub-strip), not a panel (ADR-0012).
    assert "experiment" not in hub._panels
    assert "load" not in hub._cards
    strip = hub._strip.layout()
    assert [strip.itemAt(i).widget() for i in range(3)] == \
        [hub._tiles[k] for k in CONTAINER_TILES]
    sub = hub._sub_strip.layout()
    assert [sub.itemAt(i).widget() for i in range(4)] == \
        [hub._tiles[k] for k in SUBTILES]
    # The output dock owns nearly the full window width under the strip.
    assert hub._plot_dock.width() > hub.width() - 80


def test_container_tiles_are_wide_and_one_width(hub):
    """Batch, Project, and Experiment are the three container levels, drawn
    at one width — WIDE_SCALE (1.75x, then a quarter off; 2026-08-29) a
    regular tile — so the hierarchy reads as three equal levels; the four
    experiment-level tiles keep the regular width, and the status readout
    takes the whole of what the container tiles leave."""
    from pytrackinganalysis.apps._hub_tiles import StatusTile

    scale = StatusTile.WIDE_SCALE
    assert scale == pytest.approx(1.75 * 0.75)
    wide = [hub._tiles[k] for k in CONTAINER_TILES]
    assert all(t.is_wide() for t in wide)
    assert {t.minimumWidth() for t in wide} == {round(StatusTile.MIN_WIDTH * scale)}
    assert {t.maximumWidth() for t in wide} == {round(StatusTile.MAX_WIDTH * scale)}
    assert len({t.width() for t in wide}) == 1
    ## At a comfortable window width they get the whole of it, not just the
    ## content hint (which is barely wider than a regular tile's).
    assert wide[0].width() == round(StatusTile.MAX_WIDTH * scale)
    lay = hub._strip.layout()
    spacing = lay.spacing() * 3
    margins = lay.contentsMargins().left() + lay.contentsMargins().right()
    assert hub._status_panel.width() == \
        hub._strip.width() - sum(t.width() for t in wide) - spacing - margins
    regular = [hub._tiles[k] for k in SUBTILES]
    assert not any(t.is_wide() for t in regular)
    assert {t.minimumWidth() for t in regular} == {StatusTile.COMPACT_MIN_WIDTH}
    assert {t.maximumWidth() for t in regular} == {StatusTile.COMPACT_MAX_WIDTH}
    assert wide[0].max_line_chars() > regular[0].max_line_chars()


def test_sub_tiles_are_title_only(hub):
    """The sub-strip's tiles show just icon and title — the Experiment tile
    above already says what is loaded (user feedback 2026-08-29). Their
    status lines live on in the tooltip, and the strip is one short row."""
    from pytrackinganalysis.apps._hub_tiles import StatusTile

    for key in SUBTILES:
        tile = hub._tiles[key]
        assert tile.is_compact()
        assert tile.height() == StatusTile.COMPACT_HEIGHT
        assert tile.maximumWidth() == StatusTile.COMPACT_MAX_WIDTH
        assert tile._summary_lbl.isHidden()
        assert tile._summary_lbl.text() == ""
        assert tile.toolTip() == tile.summary_text()
    assert hub._tiles["analyze"].toolTip() == "load an experiment first"
    assert hub._SUB_STRIP_HEIGHT == StatusTile.COMPACT_HEIGHT + 8
    for key in CONTAINER_TILES:
        assert not hub._tiles[key].is_compact()
        assert hub._tiles[key].height() == StatusTile.HEIGHT


def test_experiment_tile_is_inert_until_an_experiment_loads(hub, qapp):
    """Unlike Batch and Project, the Experiment tile is not a way in —
    nothing loads from it — so with no experiment it is dimmed AND inert."""
    tile = hub._tiles["experiment"]
    assert tile.is_dimmed()
    assert not tile.is_clickable()
    assert "no experiment" in tile.summary_text()
    hub._toggle_experiment()
    qapp.processEvents()
    assert not hub._experiment_expanded
    assert not hub._sub_strip.isVisible()

    hub._exp = _fake_exp("Rep1")
    hub._refresh_tiles()
    assert not tile.is_dimmed()
    assert tile.is_clickable()
    assert "Rep1" in tile.summary_text()
    ## Loading alone does not expand it — that is the click's job.
    assert not hub._experiment_expanded


def test_experiment_tile_expands_the_sub_tiles_and_collapses_them(hub, qapp):
    hub._exp = _fake_exp()
    hub._refresh_tiles()
    for key in SUBTILES:
        assert not hub._tiles[key].isVisible(), key

    hub._toggle_experiment()
    qapp.processEvents()
    assert hub._experiment_expanded
    assert hub._sub_strip.isVisible()
    assert hub._tiles["experiment"]._active
    for key in SUBTILES:
        assert hub._tiles[key].isVisible(), key
    central = hub.centralWidget()

    def _top_left(widget):
        return widget.mapTo(central, widget.rect().topLeft())

    ## Left-aligned under the Experiment tile, on the row below it.
    assert _top_left(hub._tiles["analyze"]).x() == _top_left(hub._tiles["experiment"]).x()
    assert _top_left(hub._tiles["analyze"]).y() > _top_left(hub._tiles["experiment"]).y() \
        + hub._tiles["experiment"].height()

    ## A sub tile opens its (existing) panel, anchored under the sub-strip.
    hub._toggle_panel("plots")
    qapp.processEvents()
    panel = hub._panels["plots"]
    assert hub._open_panel_key == "plots"
    assert panel.isVisible()
    assert hub._tiles["plots"]._active
    assert panel.x() == _top_left(hub._tiles["plots"]).x()
    assert panel.y() == hub._ribbon_bottom() + 1
    assert panel.y() > _top_left(hub._sub_strip).y() + hub._sub_strip.height() - 1

    ## Collapsing takes the sub tiles and the panel hanging from one of them.
    hub._toggle_experiment()
    qapp.processEvents()
    assert not hub._experiment_expanded
    assert not hub._tiles["experiment"]._active
    assert hub._open_panel_key is None
    assert not panel.isVisible()
    for key in SUBTILES:
        assert not hub._tiles[key].isVisible(), key


def test_experiment_panels_are_narrow_but_every_button_label_fits(hub, qapp):
    """The four experiment-level panels are a column of buttons, and a
    button only needs its label (user feedback 2026-08-29): each panel is
    at most 390px, and no button is squeezed below its minimum size hint —
    Qt would clip the label, not elide it."""
    from PyQt6.QtWidgets import QPushButton

    hub._exp = _fake_exp("Rep1", tracking="TWOCHOICETRACKER", n_total=12)
    hub._exp.facet_cutoffs = [10, 20]
    hub._on_experiment_ready()               # populates the plot buttons
    qapp.processEvents()
    for key in SUBTILES:
        hub._open_panel(key)
        qapp.processEvents()
        panel = hub._panels[key]
        assert panel.width() <= 390, key
        buttons = [b for b in panel.findChildren(QPushButton)
                   if b.text() and b.isVisible()]
        assert buttons, key
        if key == "plots":
            assert len(buttons) >= 4         # the real plot buttons, not a stub
        for btn in buttons:
            assert btn.width() >= btn.minimumSizeHint().width(), \
                (key, btn.text(), btn.width(), btn.minimumSizeHint().width())


def test_opening_an_experiment_panel_expands_the_sub_strip(hub, qapp):
    """Programmatic opens (the auto-open after Load + QC) reach _open_panel
    with the sub-strip collapsed: the panel needs its tile for an anchor."""
    hub._open_panel("analyze")
    qapp.processEvents()
    assert hub._experiment_expanded
    assert hub._sub_strip.isVisible()
    assert hub._panels["analyze"].y() == hub._ribbon_bottom() + 1
    assert hub._panels["analyze"].y() > hub._strip.height() + hub._SUB_STRIP_HEIGHT


def test_choosing_a_container_tile_collapses_the_sub_strip(hub, qapp):
    """One thing open at a time: the expanded Experiment group counts, so
    choosing Batch or Project folds it away along with any sub-tile panel
    (user feedback 2026-08-29), and the container panel opens under the
    strip — never over the sub tiles."""
    hub._exp = _fake_exp()
    hub._refresh_tiles()
    hub._toggle_experiment()
    hub._toggle_panel("plots")
    qapp.processEvents()
    assert hub._experiment_expanded
    assert hub._panels["plots"].isVisible()

    hub._toggle_panel("project")
    qapp.processEvents()
    assert not hub._experiment_expanded
    assert not hub._sub_strip.isVisible()
    assert not hub._tiles["experiment"]._active
    assert not hub._panels["plots"].isVisible()
    assert hub._open_panel_key == "project"
    ## Flush with the strip — measured from the ribbon's top and the tile
    ## height, not from the strip's own reported bottom: the strip once
    ## stretched into the sub-strip's not-yet-reclaimed space, so the two
    ## agreed with each other and both were a row too low.
    from pytrackinganalysis.apps._hub_tiles import StatusTile

    central = hub.centralWidget()
    ribbon_top = hub._ribbon.mapTo(central, hub._ribbon.rect().topLeft()).y()
    margins = hub._strip.layout().contentsMargins()
    strip_height = StatusTile.HEIGHT + margins.top() + margins.bottom()
    assert hub._strip.height() == strip_height
    assert hub._panels["project"].y() == ribbon_top + strip_height

    ## And the sub-strip alone, with no panel hanging from it.
    hub._close_panel()
    hub._toggle_experiment()
    qapp.processEvents()
    assert hub._experiment_expanded
    hub._toggle_panel("batch")
    qapp.processEvents()
    assert not hub._experiment_expanded
    assert hub._open_panel_key == "batch"


def test_expanding_the_group_closes_an_open_container_panel(hub, qapp):
    """The other direction of the same rule: clicking Experiment with the
    Project (or Batch) panel open closes that panel (user feedback
    2026-08-29)."""
    hub._exp = _fake_exp()
    hub._refresh_tiles()
    for key in ("project", "batch"):
        hub._open_panel(key)
        qapp.processEvents()
        assert hub._panels[key].isVisible()
        hub._toggle_experiment()
        qapp.processEvents()
        assert hub._experiment_expanded
        assert hub._open_panel_key is None
        assert not hub._panels[key].isVisible()
        assert not hub._tiles[key]._active
        hub._toggle_experiment()
        qapp.processEvents()


def test_a_click_on_the_output_collapses_the_sub_strip(hub, monkeypatch, qapp):
    """Click-away folds the sub-strip like it closes a panel — with a
    sub-tile panel open or without one; a click on the ribbon does not."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication

    class _Press:
        def globalPosition(self):
            return QPointF(0, 0)

    def _clicking(widget):
        monkeypatch.setattr(QApplication, "widgetAt",
                            staticmethod(lambda _p: widget))

    hub._exp = _fake_exp()
    hub._refresh_tiles()
    hub._toggle_experiment()
    hub._toggle_panel("analyze")
    qapp.processEvents()

    _clicking(hub._tiles["scripts"])          # on the sub-strip: stays
    hub._handle_click_away(_Press())
    assert hub._experiment_expanded
    assert hub._open_panel_key == "analyze"

    _clicking(hub._log)                       # the output: both go
    hub._handle_click_away(_Press())
    assert hub._open_panel_key is None
    assert not hub._experiment_expanded
    assert not hub._sub_strip.isVisible()

    hub._toggle_experiment()                  # expanded, no panel
    qapp.processEvents()
    _clicking(hub._tiles["experiment"])
    hub._handle_click_away(_Press())
    assert hub._experiment_expanded
    _clicking(hub._err_log)
    hub._handle_click_away(_Press())
    assert not hub._experiment_expanded


def test_unloading_the_experiment_collapses_the_sub_strip(hub, qapp):
    hub._exp = _fake_exp()
    hub._refresh_tiles()
    hub._toggle_experiment()
    hub._toggle_panel("analyze")
    qapp.processEvents()
    assert hub._experiment_expanded
    assert hub._open_panel_key == "analyze"

    hub._exp = None
    hub._refresh_tiles()
    qapp.processEvents()
    assert not hub._experiment_expanded
    assert not hub._sub_strip.isVisible()
    assert hub._open_panel_key is None
    assert hub._tiles["experiment"].is_dimmed()
    assert not hub._tiles["experiment"].is_clickable()


def test_tiles_dim_with_hints_when_nothing_is_loaded(hub):
    # Every experiment-dependent tile waits on a load, Scripts included (its
    # recipes run against the loaded experiment) and AI too — a provider key
    # with nothing to summarize is not "ready".
    for key in ("experiment", "analyze", "plots", "scripts", "ai"):
        assert hub._tiles[key].is_dimmed(), key

    # Batch and Project are never dimmed (user feedback 2026-08-24): they are
    # the way in, always available, and say their state in words.
    for key in ("batch", "project"):
        assert not hub._tiles[key].is_dimmed(), key
    assert "no project" in hub._tiles["project"].summary_text()
    assert "no batch" in hub._tiles["batch"].summary_text()


def test_a_dimmed_tile_actually_paints_dimmed(hub):
    """`_dimmed` was tracked but never reached the stylesheet, so all seven
    chips looked equally available whatever their state (user feedback
    2026-08-24)."""
    ## One tile compared with itself: the chips differ in corner rounding by
    ## position, so tile-vs-tile would pass on the wrong difference.
    tile = hub._tiles["analyze"]
    assert tile.is_dimmed()
    dim_style = tile.styleSheet()
    dim_title = tile._title_lbl.styleSheet()
    dim_icon = tile._icon_lbl.pixmap().toImage()

    tile.set_dimmed(False)
    assert tile.styleSheet() != dim_style, "the tile surface never changed"
    assert tile._title_lbl.styleSheet() != dim_title, "the title never changed"
    assert tile._icon_lbl.pixmap().toImage() != dim_icon, "the icon never changed"

    ## Lit again it wears the live surface, and dimmed the recessed one —
    ## the strip is one surface, not seven independently styled chips.
    from pytrackinganalysis.apps._hub_tiles import chrome_colors

    chrome = chrome_colors()
    assert f"background: {chrome['hover']}" in tile.styleSheet()
    assert f"background: {chrome['band']}" in dim_style
    assert chrome["muted"] in dim_title


def test_experiment_cards_are_dimmed_until_something_is_loaded(hub, qapp):  # noqa: F811
    """The tiles dim, and so do the cards inside their panels: an Analyze,
    Plots, Scripts, or AI card with no loaded experiment has nothing to act
    on, and a greyed surface says so before the user reads the buttons."""
    from types import SimpleNamespace

    for key in ("analyze", "plots", "scripts", "ai"):
        assert hub._cards[key].is_dimmed(), key
    # Cards whose actions stand on their own never dim.
    assert not hub._cards["project"].is_dimmed()

    hub._exp = SimpleNamespace(arena=SimpleNamespace(experiment_name="Rep1"),
                               facet_cutoffs=None)
    hub._scripts = [{"name": "one"}]
    hub._ai_available = True
    hub._refresh_tiles()
    for key in ("analyze", "plots", "scripts", "ai"):
        assert not hub._cards[key].is_dimmed(), key

    hub._unload_experiment()
    qapp.processEvents()
    for key in ("analyze", "plots", "scripts", "ai"):
        assert hub._cards[key].is_dimmed(), key


def test_a_dimmed_card_stays_live_and_restyles_with_the_theme(hub):
    """Dimming is presentation only — the Scripts card's Reload button is
    how a user fixes an empty script list, so the card must not go inert."""
    card = hub._cards["scripts"]
    assert card.is_dimmed()
    assert card.isEnabled()
    assert hub._btn_refresh_scripts.isEnabled()

    ## Several cues, because on the dark theme a background shift alone is
    ## invisible: surface, a border, the title color, and the icon.
    dimmed_style, dimmed_title = card.styleSheet(), card._title_lbl.styleSheet()
    dimmed_icon = card._icon_lbl.pixmap().toImage()
    card.set_dimmed(False)
    assert card.styleSheet() != dimmed_style, "the surface never changed"
    assert card._title_lbl.styleSheet() != dimmed_title, "the title never changed"
    assert card._icon_lbl.pixmap().toImage() != dimmed_icon, "the icon never changed"
    card.set_dimmed(True)
    assert card.styleSheet() == dimmed_style


def test_project_tile_reflects_state(hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    tile = hub._tiles["project"]
    assert not tile.is_dimmed()
    assert "Proj" in tile.summary_text()
    assert "2 replicates" in tile.summary_text()

    # Choosing a replicate directory selects the Project it belongs to.
    hub._set_project_dir(str(tmp_path / "Rep1"))
    assert not hub._tiles["project"].is_dimmed()
    assert "Proj" in hub._tiles["project"].summary_text()


def test_panels_open_one_at_a_time_and_toggle(hub, qapp):
    hub._open_panel("project")
    assert hub._open_panel_key == "project"
    assert hub._panels["project"].isVisible()
    assert hub._tiles["project"]._active

    hub._open_panel("analyze")                # switching closes the previous
    assert hub._open_panel_key == "analyze"
    assert not hub._panels["project"].isVisible()
    assert not hub._tiles["project"]._active

    hub._toggle_panel("analyze")              # toggling the open one closes
    assert hub._open_panel_key is None
    assert not hub._panels["analyze"].isVisible()


def test_ai_tile_opens_ai_panel(hub):
    hub._toggle_panel("ai")
    assert hub._open_panel_key == "ai"
    assert hub._panels["ai"].isVisible()
    assert hub._tiles["ai"]._active
    hub._close_panel()


def test_a_panel_survives_running_a_task(hub, qapp):
    """A panel closes on a click, not on a task: running one action then
    another from the same panel must not make it vanish underneath."""
    import time

    hub._open_panel("analyze")
    assert hub._panels["analyze"].isVisible()
    hub._spawn_task("noop", lambda: "done")
    assert hub._open_panel_key == "analyze"
    assert hub._panels["analyze"].isVisible()

    deadline = time.monotonic() + 10
    while hub._worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
    assert hub._worker is None
    assert hub._open_panel_key == "analyze"   # still open when it finishes


def test_a_click_on_the_output_closes_the_panel_but_the_strip_does_not(
        hub, monkeypatch):
    """The three ways out: the same tile, another tile, or a click on the
    background. Clicks on the strip or inside the panel are not one of them."""
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication

    class _Press:
        def globalPosition(self):
            return QPointF(0, 0)

    def _clicking(widget):
        monkeypatch.setattr(QApplication, "widgetAt",
                            staticmethod(lambda _p: widget))

    hub._open_panel("analyze")
    _clicking(hub._panels["analyze"])         # inside the panel: stays open
    hub._handle_click_away(_Press())
    assert hub._open_panel_key == "analyze"

    _clicking(hub._tiles["plots"])            # on the strip: the tile decides
    hub._handle_click_away(_Press())
    assert hub._open_panel_key == "analyze"

    _clicking(hub._log)                       # the output area: closes
    hub._handle_click_away(_Press())
    assert hub._open_panel_key is None


def test_batch_panel_stays_open_through_dialog_and_non_output_clicks(
        hub, monkeypatch):
    from PyQt6.QtCore import QPointF
    from PyQt6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

    class _Press:
        def globalPosition(self):
            return QPointF(0, 0)

    def _clicking(widget):
        monkeypatch.setattr(QApplication, "widgetAt",
                            staticmethod(lambda _p: widget))

    hub._open_panel("batch")
    dialog = QDialog(hub)
    button = QPushButton("Close", dialog)
    _clicking(button)                         # child dialog: stays open
    hub._handle_click_away(_Press())
    assert hub._open_panel_key == "batch"

    # App chrome is not an explicit return to the output/errors/analysis dock.
    _clicking(hub._status_panel)
    hub._handle_click_away(_Press())
    assert hub._open_panel_key == "batch"

    _clicking(hub._log)                       # Output tab content: closes
    hub._handle_click_away(_Press())
    assert hub._open_panel_key is None

    hub._open_panel("batch")
    _clicking(hub._err_log)                   # Errors tab content: closes
    hub._handle_click_away(_Press())
    assert hub._open_panel_key is None

    hub._open_panel("batch")
    analysis = QWidget()
    hub._plot_dock.add_widget("Analysis", analysis)
    _clicking(analysis)                       # analysis tab content: closes
    hub._handle_click_away(_Press())
    assert hub._open_panel_key is None


def test_batch_project_double_click_opens_project_panel(
        hub, qapp, tmp_path):  # noqa: F811
    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._open_panel("batch")

    table = hub._batch_table
    row = next(r for r in range(table.rowCount())
               if table.item(r, 0).text() == "P2")
    table.itemDoubleClicked.emit(table.item(row, 0))
    qapp.processEvents()

    assert hub._project_dir == tmp_path / "P2"
    assert hub._open_panel_key == "project"
    assert hub._panels["project"].isVisible()
    assert not hub._panels["batch"].isVisible()
    assert hub._tiles["project"]._active
    assert not hub._tiles["batch"]._active


def test_batch_panel_content_fits_the_visible_viewport(
        hub, qapp, tmp_path):  # noqa: F811
    from PyQt6.QtWidgets import QPushButton

    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._open_panel("batch")
    qapp.processEvents()

    panel = hub._panels["batch"]
    viewport = panel._scroll.viewport()
    host = panel._scroll.widget()
    run_button_right = hub._btn_run_batch.mapTo(
        viewport, hub._btn_run_batch.rect().bottomRight()).x()

    assert host.width() <= viewport.width()
    assert run_button_right <= viewport.width()

    buttons = {b.text(): b for b in
               hub._cards["batch"].findChildren(QPushButton)}
    choose = buttons["Choose batch folder…"]
    rescan = hub._btn_batch_rescan
    removals = hub._btn_batch_removals
    centers = [b.mapTo(viewport, b.rect().center())
               for b in (choose, rescan, removals)]
    assert centers[0].x() < centers[1].x() < centers[2].x()
    assert max(p.y() for p in centers) - min(p.y() for p in centers) <= 8


def test_batch_run_button_uses_normal_button_fill(hub):
    style = hub._btn_run_batch.styleSheet()
    assert "background: palette(button)" in style
    assert "palette(highlight)" not in style


def test_project_panel_uses_available_height_to_fit_content(
        hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    hub.resize(1400, 840)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._open_panel("project")
    qapp.processEvents()

    panel = hub._panels["project"]
    scroll = panel._scroll
    content = scroll.widget().sizeHint().height()
    assert scroll.viewport().height() >= content
    assert scroll.verticalScrollBar().maximum() == 0


def test_tile_panel_respects_bottom_bound_on_short_windows(
        hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    hub.resize(900, 260)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._open_panel("project")
    qapp.processEvents()

    central = hub.centralWidget()
    panel = hub._panels["project"]
    assert panel.y() + panel.height() <= central.height() - 8


def test_cards_live_inside_panels_and_stay_functional(hub):
    # The full existing cards moved into panels — handlers and widgets intact.
    project_panel = hub._panels["project"]
    assert hub._cards["project"] in project_panel.findChildren(type(hub._cards["project"]))
    for key in ("experiments", "projectview"):
        assert hub._cards[key] in project_panel.findChildren(type(hub._cards[key]))
    # The replicates table is reachable exactly as before.
    assert hub._exp_table.parent() is not None


def test_tile_summary_lines_are_hard_capped(hub):
    from pytrackinganalysis.apps._hub_tiles import StatusTile
    from pytrackinganalysis.ui import Category

    regular = StatusTile("x", "X", "batch", Category.LOAD)
    for tile, cap in ((regular, 26),
                      (hub._tiles["project"], round(26 * StatusTile.WIDE_SCALE))):
        assert tile.max_line_chars() == cap
        tile.set_summary(["x" * 100, "y" * 100, "third line is dropped"])
        text = tile.summary_text()
        assert all(len(line) <= cap for line in text.splitlines()), tile.key
        assert len(text.splitlines()) == 2


def test_batch_card_title_wears_the_batch_tiles_color(hub):
    """The Batch panel's title matches the tile it opens under — LOAD-blue
    text, like the tile's title — rather than the theme's text color the
    other cards use (user feedback 2026-08-29). Dimmed, it mutes like the
    rest."""
    from pytrackinganalysis.ui import Category, category_color

    blue = category_color(Category.LOAD)
    card = hub._cards["batch"]
    assert f"color: {blue}" in card._title_lbl.styleSheet()
    assert hub._tiles["batch"]._title_lbl.styleSheet().startswith(f"color: {blue}")
    assert f"color: {blue}" not in hub._cards["project"]._title_lbl.styleSheet()
    card.set_dimmed(True)
    assert f"color: {blue}" not in card._title_lbl.styleSheet()
    card.set_dimmed(False)
    assert f"color: {blue}" in card._title_lbl.styleSheet()


def test_strip_has_no_divider_and_uniform_spacing(hub):
    """The strip is one flat row: no divider widget, no extra spacer — the
    grouping cue lives in the tiles' own dim/lit states."""
    lay = hub._strip.layout()
    items = [lay.itemAt(i) for i in range(lay.count())]
    assert all(it.widget() is not None for it in items)
    assert not hasattr(hub, "_strip_divider")


def test_status_panel_fills_the_strip_right_of_the_last_tile(hub):
    lay = hub._strip.layout()
    assert lay.itemAt(lay.count() - 1).widget() is hub._status_panel
    assert lay.itemAt(lay.count() - 2).widget() is hub._tiles[CONTAINER_TILES[-1]]
    ## The width the Tools tile used to take is the readout's now: it has to
    ## be wide enough to say which project AND which experiment.
    tiles_width = sum(hub._tiles[k].width() for k in CONTAINER_TILES)
    assert hub._status_panel.width() > hub._strip.width() - tiles_width - 40


def test_status_panel_reports_the_project_and_loaded_experiment(hub, qapp, tmp_path):  # noqa: F811
    class _Arena:
        experiment_name = "Rep1"

    class _Exp:
        arena = _Arena()

    assert "no project" in hub._status_panel.status_text().lower()

    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    text = hub._status_panel.status_text()
    assert "Proj" in text
    assert str(tmp_path) in text            # which project on disk
    assert "2" in text                      # replicate count
    assert "none loaded" in text            # no experiment yet

    hub._exp = _Exp()
    hub._refresh_tiles()
    assert "Rep1" in hub._status_panel.status_text()


def test_project_actions_use_three_columns(hub):
    grid = hub._project_actions_grid
    assert grid.columnCount() == 3
    # Focused actions over three columns — no button spans the whole card.
    assert grid.count() == 5
    # "View reports" sits directly under the Create/Update report button.
    report_at = grid.getItemPosition(grid.indexOf(hub._btn_project_report))
    view_at = grid.getItemPosition(grid.indexOf(hub._btn_view_reports))
    assert view_at[1] == report_at[1] and view_at[0] == report_at[0] + 1
    labels = [grid.itemAt(i).widget().text() for i in range(grid.count())]
    assert labels == [
        "Create report", "Plot editor…", "AI narrative…",
        "View reports", "Removed regions…",
    ]


def test_experiments_card_owns_the_experiment_actions(hub):
    """Section 2 holds the replicates table and every action that makes or
    configures an experiment; section 3 holds none of them."""
    from pytrackinganalysis.ui.widgets import ActionButton

    grid = hub._experiments_actions_grid
    assert grid.columnCount() == 3
    labels = [grid.itemAt(i).widget().text() for i in range(grid.count())]
    assert labels == [
        "Create experiment…", "Initialize existing directory…",
        "Experiment configs…",
    ]
    # The table lives here, not on the Analysis card.
    assert hub._exp_table in hub._cards["experiments"].findChildren(
        type(hub._exp_table))
    analysis_labels = [b.text() for b in
                       hub._cards["projectview"].findChildren(ActionButton)]
    assert not any("experiment" in text.lower() for text in analysis_labels)


def test_experiment_actions_wait_for_a_project(hub, qapp, tmp_path):  # noqa: F811
    """Every replicate action inherits the design from project.yaml, so none
    of them is offered until there is a Project to inherit from."""
    from pytrackinganalysis.ui.widgets import ActionButton

    buttons = {b.text(): b for b in
               hub._cards["experiments"].findChildren(ActionButton)}
    assert not any(b.isEnabled() for b in buttons.values())

    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    assert all(b.isEnabled() for b in buttons.values())


def test_project_report_button_labels_create_or_update(hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    assert hub._btn_project_report.text() == "Create report"
    assert "background: palette(button)" in hub._btn_project_report.styleSheet()
    assert "palette(highlight)" not in hub._btn_project_report.styleSheet()

    (tmp_path / "Proj_report.pdf").write_bytes(b"%PDF-")
    hub._refresh_project_view()
    assert hub._btn_project_report.text() == "Update report"
    assert "background: palette(button)" in hub._btn_project_report.styleSheet()
    assert "palette(highlight)" not in hub._btn_project_report.styleSheet()


def test_project_report_button_runs_full_refresh(hub, tmp_path, monkeypatch):
    calls: list[str] = []

    class _Project:
        name = "Proj"
        project_directory = str(tmp_path)
        experiment_names = ["Rep1", "Rep2"]

        def run_all(self):
            calls.append("run_all")
            return []

        def build_combined_analysis(self):
            calls.append("combined")
            return {"written": ["a.csv", "b.txt"], "missing": []}

        def create_report(self):
            calls.append("report")
            return str(tmp_path / "Proj_report.pdf")

    spawned: list[tuple[str, str]] = []
    monkeypatch.setattr(hub, "_current_project", lambda: _Project())
    monkeypatch.setattr(
        hub, "_spawn_task",
        lambda name, fn: spawned.append((name, fn())),
    )

    hub._project_report()

    assert calls == ["run_all", "combined", "report"]
    assert spawned[0][0] == "Create report"
    assert "ran 2 replicate analyses" in spawned[0][1]
    assert "wrote Combined Analysis (2 files)" in spawned[0][1]


def test_project_ai_narrative_rebuilds_report(hub, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog

    class _Provider:
        provider_name = "testai"

    class _Project:
        def __init__(self):
            self.calls: list[tuple[str, str] | tuple[str]] = []

        def generate_ai_summary(self, provider):
            self.calls.append(("ai", provider))

        def create_report(self):
            self.calls.append(("report",))
            return str(tmp_path / "Proj_report.pdf")

    project = _Project()
    spawned: list[tuple[str, str]] = []
    monkeypatch.setattr("pytrackinganalysis.ai.available_providers",
                        lambda: [_Provider()])
    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda *a, **k: ("testai", True)),
    )
    monkeypatch.setattr(hub, "_current_project", lambda: project)
    monkeypatch.setattr(
        hub, "_spawn_task",
        lambda name, fn: spawned.append((name, fn())),
    )

    hub._project_ai_narrative()

    assert project.calls == [("ai", "testai"), ("report",)]
    assert spawned[0][0] == "AI narrative"
    assert "report rebuilt" in spawned[0][1]


def test_project_script_controls_stay_on_one_row(hub):
    from PyQt6.QtWidgets import QPushButton

    row = hub._project_script_row
    widgets = [row.itemAt(i).widget() for i in range(row.count())]
    assert hub._project_script_combo in widgets
    buttons = [w.text() for w in widgets if isinstance(w, QPushButton)]
    assert buttons == ["Run script", "Edit scripts…"]


def test_experiment_scripts_come_from_the_loaded_experiment(hub, qapp, tmp_path):  # noqa: F811
    """Experiment Scripts run against the loaded experiment, so that is where
    the list comes from — selecting the Project offers none."""
    from types import SimpleNamespace

    import yaml

    _make_project(tmp_path)
    cfg_path = tmp_path / "Rep1" / "tracking_config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["scripts"] = [{"name": "nightly", "steps": []}]
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    assert hub._scripts_combo.count() == 0
    assert hub._tiles["scripts"].is_dimmed()
    assert "load" in hub._tiles["scripts"].summary_text()

    hub._exp = SimpleNamespace(
        project_directory=str(tmp_path / "Rep1"),
        arena=SimpleNamespace(experiment_name="Rep1"))
    hub._refresh_scripts()
    hub._refresh_tiles()
    assert hub._scripts_combo.count() == 1
    assert not hub._tiles["scripts"].is_dimmed()


def test_create_config_on_an_experiment_folder_targets_the_parent(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """A project.yaml beside a tracking_config.yaml would be a Project with
    zero replicates — offer the parent, which is where it belongs."""
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from pytrackinganalysis import project as prj
    from pytrackinganalysis.apps import hub as hub_mod

    exp_dir = tmp_path / "Solo"
    (exp_dir / "data").mkdir(parents=True)
    (exp_dir / "tracking_config.yaml").write_text("global: {}\n", encoding="utf-8")

    opened: list = []

    class _Dlg:
        def __init__(self, parent=None, start_dir=None):
            opened.append(start_dir)
            self.saved_dir = start_dir

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(hub_mod, "ProjectInfoDialog", _Dlg)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    hub._set_project_dir(str(exp_dir))
    qapp.processEvents()
    hub._btn_edit_cfg.click()

    assert prj.is_project_dir(tmp_path)
    assert not (exp_dir / "project.yaml").exists()
    assert opened == [str(tmp_path)]


def test_experiment_tile_shows_the_loaded_experiment(hub, qapp, tmp_path):  # noqa: F811
    """Load status is the Experiment tile's (ADR-0012); the Project tile
    keeps reporting the Project's replicates whatever is loaded."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path / "Rep1"))
    qapp.processEvents()
    assert "2 replicates" in hub._tiles["project"].summary_text()
    assert "no experiment" in hub._tiles["experiment"].summary_text()

    hub._exp = _fake_exp("Rep1")
    hub._refresh_tiles()
    text = hub._tiles["project"].summary_text()
    assert "Proj" in text
    assert "2 replicates" in text
    assert "Rep1" not in text
    assert "Rep1" in hub._tiles["experiment"].summary_text()
    assert "Rep1" in hub._status_panel.status_text()


def test_experiment_tile_lines_are_whole_thoughts_that_fit(hub):
    """Two lines — which experiment, and its flies — each complete, and
    each chosen to render inside the tile without an ellipsis (user
    feedback 2026-08-29). Nothing about what the tile opens."""
    tile = hub._tiles["experiment"]
    hub._exp = _fake_exp("Rep1", tracking="TWOCHOICETRACKER", n_total=12,
                         excluded=1, flagged=0)
    hub._refresh_tiles()
    lines = tile.summary_text().splitlines()
    assert lines == ["Rep1 — two-choice tracker",
                     "12 flies · 1 excluded · 0 flagged"]
    assert all(tile.fits(line) for line in lines)
    assert "…" not in tile.summary_text()
    assert "Analyze" not in tile.summary_text()
    assert not tile._summary_lbl.wordWrap()


def test_experiment_tile_drops_detail_rather_than_eliding(hub):
    """A long name costs the tracking type, not the name's tail; a wide
    count line falls back to abbreviations, then to the total alone."""
    tile = hub._tiles["experiment"]
    name = "Replicate_2026-08-29_chr_vs_ctrl"
    hub._exp = _fake_exp(name, tracking="PAIRWISEINTERACTIONTRACKER",
                         n_total=1200, excluded=150, flagged=300)
    hub._refresh_tiles()
    lines = tile.summary_text().splitlines()
    assert lines[0] == name
    assert "…" not in tile.summary_text()
    assert all(tile.fits(line) for line in lines)
    assert lines[1].startswith("1200 flies")
    ## The full detail is one hover away, as for every tile.
    assert "1200 flies" in tile.toolTip()

    ## Only a name that cannot fit on its own reaches the character cap.
    hub._exp = _fake_exp("x" * 80)
    hub._refresh_tiles()
    assert tile.summary_text().splitlines()[0].endswith("…")


def test_experiment_tile_names_the_replicate_as_the_table_does(hub, qapp, tmp_path):  # noqa: F811
    """The recording's own name (the xlsx basename — the same rig name for
    every replicate) is not the replicate's name: the tile and the status
    readout show the directory name the Experiments table lists (user
    feedback 2026-08-29)."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    exp = _fake_exp("MaxIRSetup", n_total=12)
    exp.project_directory = str(tmp_path / "Rep2")
    hub._exp = exp
    hub._refresh_tiles()
    assert hub._tiles["experiment"].summary_text().splitlines()[0] == "Rep2"
    assert "MaxIRSetup" not in hub._tiles["experiment"].summary_text()
    assert "Rep2" in hub._status_panel.status_text()
    assert "MaxIRSetup" not in hub._status_panel.status_text()


def test_experiment_tile_without_counts_still_names_the_experiment(hub):
    tile = hub._tiles["experiment"]
    hub._exp = _fake_exp("Rep2")
    hub._refresh_tiles()
    assert tile.summary_text().splitlines() == ["Rep2", "no fly counts yet"]


def _unanalyze(tmp_path, name):
    """Strip *name*'s saved analysis so the table reads it as not analyzed."""
    for path in (tmp_path / name / "analysis").glob("*.csv"):
        path.unlink()


def test_double_clicking_a_replicate_loads_it(hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """The replicates table is the only way in: one double-click loads. An
    analyzed replicate loads as it is and reveals the Experiment group; one
    with no analysis yet loads with QC and reveals the Analyze panel (user
    feedback 2026-08-29)."""
    _make_project(tmp_path)                    # both replicates analyzed
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    loaded: list = []

    def _fake_load(self, directory=None, *, run_qc=True, reveal=None):
        loaded.append((str(directory), run_qc, reveal))

    monkeypatch.setattr(type(hub), "_load_experiment", _fake_load)

    def _double_click(name):
        row = next(r for r in range(hub._exp_table.rowCount())
                   if hub._exp_table.item(r, 0).text() == name)
        hub._exp_table.itemDoubleClicked.emit(hub._exp_table.item(row, 0))

    _double_click("Rep2")
    assert loaded == [(str(tmp_path / "Rep2"), False, "group")]
    # The row that was double-clicked is the load target; the selection is
    # still the Project, so its actions keep working on the whole set.
    assert str(hub._project_dir) == str(tmp_path)

    _unanalyze(tmp_path, "Rep2")
    _double_click("Rep2")
    assert loaded[-1] == (str(tmp_path / "Rep2"), True, "analyze")


def test_double_clicking_an_analyzed_replicate_loads_it_without_qc(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """An analyzed replicate is loaded as it is: no QC re-run, no QC viewer,
    and the Experiment group opens with no panel — Run QC / Run Analysis
    are in that group for anyone who wants them redone (user feedback
    2026-08-29)."""
    from types import SimpleNamespace

    from pytrackinganalysis.apps import hub as hub_mod

    _make_project(tmp_path)                    # both replicates analyzed
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._open_panel("project")
    qapp.processEvents()

    completed: list[str] = []
    launched: list[str] = []

    class _FakeExperiment:
        facet_cutoffs = None

        def __init__(self, project_directory, config_path=None):
            self.project_directory = str(project_directory)
            self.config_path = config_path
            ## The recording's name — the rig's, the same for every replicate.
            self.arena = SimpleNamespace(experiment_name="MaxIRSetup")
            self.parameters = SimpleNamespace(
                get_tracking_type=lambda: SimpleNamespace(name="TWOCHOICETRACKER"))

        def run_qc(self):
            completed.append("qc")

        def __str__(self):
            return "fake experiment"

    pending: dict = {}

    def _fake_spawn(label, task, on_ok, on_fail, **kwargs):
        pending.update({"label": label, "task": task, "on_ok": on_ok,
                        "worker": SimpleNamespace(task_name=label)})
        return True

    monkeypatch.setattr(hub_mod.ExperimentMod, "Experiment", _FakeExperiment)
    monkeypatch.setattr(hub, "_spawn_task_with_callbacks", _fake_spawn)
    monkeypatch.setattr(hub, "_launch_subapp",
                        lambda app, **k: launched.append(app))

    row = next(r for r in range(hub._exp_table.rowCount())
               if hub._exp_table.item(r, 0).text() == "Rep2")
    hub._exp_table.itemDoubleClicked.emit(hub._exp_table.item(row, 0))
    qapp.processEvents()

    assert pending["label"] == "Load"
    assert hub._open_panel_key is None

    msg = pending["task"]()
    pending["on_ok"](msg)
    hub._on_task_finished(pending["worker"])
    qapp.processEvents()

    assert completed == []
    assert launched == []
    assert "not re-run" in msg
    assert hub._exp.project_directory == str(tmp_path / "Rep2")
    assert hub._experiment_expanded
    assert hub._sub_strip.isVisible()
    assert hub._tiles["experiment"]._active
    assert hub._open_panel_key is None
    assert not hub._panels["analyze"].isVisible()
    ## Named as the table names it, not by the recording.
    assert hub._tiles["experiment"].summary_text().startswith("Rep2")
    assert "Experiment: Rep2" in hub._status_panel.status_text()
    assert "MaxIRSetup" not in hub._status_panel.status_text()


def test_replicate_double_click_opens_analyze_after_load_finishes(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """A replicate with no analysis yet: Load + QC, the QC viewer, then the
    Analyze panel once the load finishes."""
    from pathlib import Path
    from types import SimpleNamespace

    from pytrackinganalysis.apps import hub as hub_mod

    _make_project(tmp_path)
    _unanalyze(tmp_path, "Rep2")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._open_panel("project")
    qapp.processEvents()

    completed: list[str] = []

    class _FakeExperiment:
        facet_cutoffs = None

        def __init__(self, project_directory, config_path=None):
            self.project_directory = str(project_directory)
            self.config_path = config_path
            self.arena = SimpleNamespace(
                experiment_name=Path(project_directory).name)
            self.parameters = SimpleNamespace(
                get_tracking_type=lambda: SimpleNamespace(name="TWOCHOICETRACKER"))

        def run_qc(self):
            completed.append("qc")

        def __str__(self):
            return "fake experiment"

    pending: dict = {}

    def _fake_spawn(label, task, on_ok, on_fail, **kwargs):
        pending.update({
            "label": label,
            "task": task,
            "on_ok": on_ok,
            "worker": SimpleNamespace(task_name=label),
        })
        return True

    launched: list[str] = []
    monkeypatch.setattr(hub_mod.ExperimentMod, "Experiment", _FakeExperiment)
    monkeypatch.setattr(hub, "_spawn_task_with_callbacks", _fake_spawn)
    monkeypatch.setattr(hub, "_launch_subapp",
                        lambda app, **k: launched.append(app))

    row = next(r for r in range(hub._exp_table.rowCount())
               if hub._exp_table.item(r, 0).text() == "Rep2")
    hub._exp_table.itemDoubleClicked.emit(hub._exp_table.item(row, 0))
    qapp.processEvents()

    assert pending["label"] == "Load + QC"
    assert hub._open_panel_key is None
    assert not hub._panels["project"].isVisible()
    assert not hub._panels["analyze"].isVisible()

    msg = pending["task"]()
    pending["on_ok"](msg)
    hub._on_task_finished(pending["worker"])
    qapp.processEvents()

    assert completed == ["qc"]
    assert launched == ["qc"]
    assert hub._exp.project_directory == str(tmp_path / "Rep2")
    assert hub._open_panel_key == "analyze"
    assert hub._panels["analyze"].isVisible()
    assert not hub._panels["project"].isVisible()
    assert hub._tiles["analyze"]._active
    assert not hub._tiles["project"]._active


def test_failed_replicate_load_does_not_open_analyze_for_stale_experiment(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    _make_project(tmp_path)
    _unanalyze(tmp_path, "Rep2")
    hub._set_project_dir(str(tmp_path))
    hub._exp = SimpleNamespace(
        project_directory=str(tmp_path / "Rep1"),
        arena=SimpleNamespace(experiment_name="Rep1"),
        facet_cutoffs=None,
        parameters=SimpleNamespace(
            get_tracking_type=lambda: SimpleNamespace(name="TWOCHOICETRACKER")),
    )
    qapp.processEvents()
    hub._open_panel("project")
    qapp.processEvents()

    pending: dict = {}

    def _fake_spawn(label, task, on_ok, on_fail, **kwargs):
        pending.update({
            "label": label,
            "on_fail": on_fail,
            "worker": SimpleNamespace(task_name=label),
        })
        return True

    warnings: list[str] = []
    monkeypatch.setattr(hub, "_spawn_task_with_callbacks", _fake_spawn)
    monkeypatch.setattr(hub, "_warn", warnings.append)

    row = next(r for r in range(hub._exp_table.rowCount())
               if hub._exp_table.item(r, 0).text() == "Rep2")
    hub._exp_table.itemDoubleClicked.emit(hub._exp_table.item(row, 0))
    qapp.processEvents()

    assert pending["label"] == "Load + QC"
    assert hub._open_panel_key is None

    pending["on_fail"]("boom")
    hub._on_task_finished(pending["worker"])
    qapp.processEvents()

    assert warnings == ["Failed to load experiment — see Output for details."]
    assert hub._open_panel_key is None
    assert not hub._panels["analyze"].isVisible()
    assert not hub._tiles["analyze"]._active
    assert hub._exp.project_directory == str(tmp_path / "Rep1")


def test_create_project_lives_on_the_project_card(hub):
    """Creating a Project is project-level work, not part of loading one."""
    from PyQt6.QtWidgets import QPushButton

    labels = [b.text() for b in hub._cards["project"].findChildren(QPushButton)]
    assert any("Create project" in t for t in labels)


def test_one_load_action_and_no_reload(hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """Reloading a project WAS browsing to it and opening it again, so the
    card offers the one action — and picking the open project re-reads it."""
    from PyQt6.QtWidgets import QFileDialog, QPushButton

    buttons = {b.text(): b for b in
               hub._cards["project"].findChildren(QPushButton)}
    assert "Open Project" in buttons
    assert not [t for t in buttons if "Reload" in t or "Browse" in t]
    assert not hasattr(hub, "_reload_project")

    _make_project(tmp_path)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    buttons["Open Project"].click()
    qapp.processEvents()
    assert hub._project_dir == tmp_path
    assert hub._exp_table.rowCount() == 2      # the project was read

    # Clicking it again re-reads from disk: a replicate added outside the Hub
    # appears without any separate Reload.
    (tmp_path / "Rep3" / "data").mkdir(parents=True)
    (tmp_path / "Rep3" / "tracking_config.yaml").write_text(
        (tmp_path / "Rep1" / "tracking_config.yaml").read_text(),
        encoding="utf-8")
    buttons["Open Project"].click()
    qapp.processEvents()
    assert hub._exp_table.rowCount() == 3


def test_create_load_offers_the_three_ways_into_a_project(hub):
    """One button per scenario: the Project exists, it does not exist yet, or
    its directory does but its project.yaml does not — plus the editor."""
    from pytrackinganalysis.ui.widgets import ActionButton

    grid = hub._create_load_grid
    assert grid.columnCount() == 2
    labels = [grid.itemAt(i).widget().text() for i in range(grid.count())]
    assert labels == [
        "Open Project", "Create project…",
        "Initialize existing directory…", "Edit config…",
        # Not a fifth way in — the check over the Project that is open, on
        # its own full-width row.
        "Validate YAMLs",
    ]
    assert grid.getItemPosition(grid.indexOf(hub._btn_validate)) == (2, 0, 1, 2)
    # Nothing else creeps back into section 1.
    card_labels = {b.text() for b in
                   hub._cards["project"].findChildren(ActionButton)}
    assert card_labels == set(labels)


def test_initialize_existing_directory_writes_the_project_in_place(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """Scenario 3: the directory (and its experiments) already exist, so the
    directory is kept, named after itself, and gets a project.yaml."""
    from PyQt6.QtWidgets import QDialog

    from pytrackinganalysis import project as prj
    from pytrackinganalysis.apps import hub as hub_mod

    # An existing directory holding one experiment, but no project.yaml.
    _make_project(tmp_path)
    (tmp_path / prj.PROJECT_FILENAME).unlink()
    existing = tmp_path
    assert not prj.is_project_dir(existing)

    seen = {}

    def _fake_exec(dialog):
        seen["initialize"] = dialog._initialize
        seen["title"] = dialog.windowTitle()
        dialog.dir_edit.setText(str(existing))
        seen["name"] = dialog.name_edit.text()
        seen["read_only"] = dialog.name_edit.isReadOnly()
        dialog._save()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(hub_mod.ProjectInfoDialog, "exec", _fake_exec)
    hub._initialize_existing_directory()
    qapp.processEvents()

    assert seen["initialize"] is True
    assert seen["title"] == "Initialize existing directory"
    # The directory names the Project, and the field says so without letting
    # it be edited.
    assert seen["name"] == existing.name
    assert seen["read_only"] is True
    assert prj.is_project_dir(existing)
    assert prj.Project(str(existing)).name == existing.name
    # The new Project is the selection, with its replicates already listed.
    assert hub._project_dir == existing
    assert hub._exp_table.rowCount() == 2


def test_initialize_refuses_a_directory_that_is_already_a_project(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """Each way in stays disjoint: an existing project.yaml is Open Project's
    job, and a directory that is not there yet is Create project's."""
    from PyQt6.QtWidgets import QMessageBox

    from pytrackinganalysis.apps import hub as hub_mod

    _make_project(tmp_path)
    said = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: said.append(a[2])))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: said.append(a[2])))

    dialog = hub_mod.ProjectInfoDialog(hub, initialize_existing=True)
    assert dialog._resolve_existing_directory(str(tmp_path)) is None
    assert "already" in said[-1]

    assert dialog._resolve_existing_directory(str(tmp_path / "nope")) is None
    assert "not a directory" in said[-1]

    # A plain directory with no project.yaml is exactly what it takes.
    (tmp_path / "Plain").mkdir()
    assert dialog._resolve_existing_directory(str(tmp_path / "Plain")) == \
        str(tmp_path / "Plain")
    dialog.close()


def test_initialize_moves_up_from_an_experiment_directory(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """A project.yaml beside a tracking_config.yaml makes a Project with zero
    replicates, so the offer is to initialize the parent instead."""
    from PyQt6.QtWidgets import QMessageBox

    from pytrackinganalysis.apps import hub as hub_mod

    from pytrackinganalysis import project as prj

    _make_project(tmp_path)
    (tmp_path / prj.PROJECT_FILENAME).unlink()
    replicate = tmp_path / "Rep1"

    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dialog = hub_mod.ProjectInfoDialog(hub, initialize_existing=True)
    resolved = dialog._resolve_existing_directory(str(replicate))
    assert resolved == str(tmp_path)
    # The name follows the retarget, so the Project is not named "Rep1".
    assert dialog.name_edit.text() == tmp_path.name
    dialog.close()


def test_initialize_experiment_files_scaffolds_and_opens_the_editor(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """Case 3 for a replicate: the directory is there with its recording
    loose in it, so filing runs first, then the config, then the editor."""
    from PyQt6.QtWidgets import QInputDialog

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    loose = tmp_path / "Rep2"
    loose.mkdir()
    (loose / "Rec.xlsx").write_bytes(b"")
    (loose / "Rec_Data_1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (loose / "notes.txt").write_text("bench notes", encoding="utf-8")

    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    picked = {}

    def _fake_item(_parent, _title, _label, items, *a, **k):
        picked["items"] = list(items)
        return items[0], True

    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(_fake_item))
    launched = []
    monkeypatch.setattr(type(hub), "_launch_subapp",
                        lambda self, *a, **k: launched.append((a, k)))
    hub._project_initialize_experiment()
    qapp.processEvents()

    # Only the config-less directory was offered.
    assert [text.split("  —  ")[0] for text in picked["items"]] == ["Rep2"]
    # The recording was filed, the stray file parked, and the config written.
    assert (loose / "data" / "Rec.xlsx").is_file()
    assert (loose / "data" / "Rec_Data_1.csv").is_file()
    assert (loose / "data" / "extra_files" / "notes.txt").is_file()
    assert (loose / "tracking_config.yaml").is_file()
    assert not (loose / "Rec.xlsx").exists()
    # And the editor opened on it, which is the point of the button.
    assert launched and launched[-1][0][0] == "config"
    assert launched[-1][1]["directory"] == str(loose)
    # The Project now sees it as a replicate.
    names = [hub._exp_table.item(r, 0).text()
             for r in range(hub._exp_table.rowCount())]
    assert names == ["Rep1", "Rep2"]


def test_initialize_experiment_needs_a_directory_without_a_config(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """With every folder already configured there is nothing to initialize —
    that case is Create experiment's."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    warned = []
    monkeypatch.setattr(type(hub), "_warn",
                        lambda self, message: warned.append(message))
    hub._project_initialize_experiment()
    assert "Create experiment" in warned[-1]


def test_initialize_experiment_stops_when_filing_is_refused(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """A workbook open in Excel blocks the move; writing the config anyway
    would leave a replicate whose recording never gets filed."""
    from PyQt6.QtWidgets import QInputDialog

    _make_project(tmp_path, names=("Rep1",), with_analysis=False)
    blocked = tmp_path / "Rep2"
    blocked.mkdir()
    (blocked / "Rec.xlsx").write_bytes(b"")
    (blocked / "~$Rec.xlsx").write_bytes(b"")     # Excel's lock file

    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda _p, _t, _l, items, *a, **k: (items[0], True)))
    warned = []
    monkeypatch.setattr(type(hub), "_warn",
                        lambda self, message: warned.append(message))
    hub._project_initialize_experiment()

    assert "open" in warned[-1]
    assert not (blocked / "tracking_config.yaml").exists()
    assert (blocked / "Rec.xlsx").is_file()


def test_project_panel_headers_are_distinct(hub):
    """The panel is already titled Project — its cards name their own jobs."""
    titles = [hub._cards[key]._title_lbl.text()
              for key in ("project", "experiments", "projectview")]
    assert titles == ["Create/Load", "Experiments", "Analysis"]
    assert hub._cards["projectview"]._subtitle_lbl is None


def test_project_panel_uses_one_project_theme(hub):
    from pytrackinganalysis.ui import Category
    from pytrackinganalysis.ui.widgets import ActionButton

    for key in ("project", "experiments", "projectview"):
        assert hub._cards[key]._category == Category.NEUTRAL

    buttons = []
    for key in ("project", "experiments", "projectview"):
        buttons.extend(hub._cards[key].findChildren(ActionButton))

    assert buttons
    assert {button._category for button in buttons} == {Category.NEUTRAL}
    assert not any("palette(highlight)" in button.styleSheet()
                   for button in buttons)


def test_project_card_edits_project_yaml_not_tracking_configs(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """Project card: no config dropdown / QC; Edit/Create opens project.yaml."""
    from PyQt6.QtWidgets import QComboBox, QDialog

    from pytrackinganalysis import project as prj
    from pytrackinganalysis.apps import hub as hub_mod

    card = hub._cards["project"]
    assert not any(isinstance(w, QComboBox) for w in card.findChildren(QComboBox))
    assert not any("QC" in b.text() for b in card.findChildren(type(hub._btn_edit_cfg)))

    # No project.yaml yet → Create config writes a default and opens the editor.
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    assert hub._btn_edit_cfg.text().startswith("Create")
    assert hub._btn_edit_cfg.isEnabled()
    opened: list[str] = []

    class _Dlg:
        def __init__(self, parent=None, start_dir=None):
            opened.append(start_dir)
            self.saved_dir = start_dir

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(hub_mod, "ProjectInfoDialog", _Dlg)
    hub._btn_edit_cfg.click()
    qapp.processEvents()
    assert prj.is_project_dir(tmp_path)
    assert opened == [str(tmp_path.resolve())]

    # Already a Project → Edit config opens the same editor, no rewrite needed.
    assert hub._btn_edit_cfg.text().startswith("Edit")
    opened.clear()
    before = (tmp_path / "project.yaml").read_text(encoding="utf-8")
    hub._btn_edit_cfg.click()
    assert opened == [str(tmp_path.resolve())]
    assert (tmp_path / "project.yaml").read_text(encoding="utf-8") == before


# ---- the Batch tile & panel (ADR-0009) ------------------------------------

def test_batch_selection_fills_the_batch_tile_and_points_project_at_it(
        hub, qapp, tmp_path):  # noqa: F811
    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    assert "2 project" in hub._tiles["batch"].summary_text()
    # The Project tile's fix is choosing a project in the Batch panel — said
    # in its summary, since neither entry tile ever dims.
    assert "double-click a project" in hub._tiles["project"].summary_text()
    assert not hub._tiles["batch"].is_dimmed()
    assert not hub._tiles["project"].is_dimmed()
    text = hub._status_panel.status_text()
    assert "Batch" in text
    assert "2 Project" in text
    # No designation runs each Project's own 'batch' script, so that is what
    # the readout names — not a built-in the run would never reach.
    assert "batch" in text


def test_project_selection_tells_the_batch_tile_to_go_up_a_level(
        hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    assert "selection is a project" in hub._tiles["batch"].summary_text()
    assert not hub._tiles["batch"].is_dimmed()
    assert not hub._tiles["project"].is_dimmed()


def test_batch_table_lists_projects_checked_by_default(hub, qapp, tmp_path):  # noqa: F811
    from PyQt6.QtCore import Qt

    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    table = hub._batch_table
    # 'notes' (no project.yaml) is never a row; all Projects start checked.
    assert [table.item(r, 0).text()
            for r in range(table.rowCount())] == ["P1", "P2"]
    assert hub._batch_checked_names() == ["P1", "P2"]
    # Unchecking survives a refresh; new rows still default to checked.
    table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    hub._refresh_batch_view()
    assert hub._batch_checked_names() == ["P2"]


def test_batch_table_caps_and_elides_long_project_names(
        hub, qapp, tmp_path):  # noqa: F811
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QHeaderView

    long_key = (
        "September-2026/Archive/"
        "Very-Long-Project-Name-That-Should-Not-Widen-The-Batch-Panel"
    )
    (tmp_path / long_key).mkdir(parents=True)
    _make_batch(tmp_path / long_key, names=("Leaf",))
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    table = hub._batch_table
    header = table.horizontalHeader()
    assert table.textElideMode() == Qt.TextElideMode.ElideRight
    assert table.columnWidth(0) <= hub._BATCH_PROJECT_COLUMN_MAX_WIDTH
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
    assert table.item(0, 0).toolTip() == f"{long_key}/Leaf"


def test_batch_double_click_is_an_ordinary_selection_change(
        hub, qapp, tmp_path):  # noqa: F811
    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    table = hub._batch_table
    row = next(r for r in range(table.rowCount())
               if table.item(r, 0).text() == "P2")
    table.itemDoubleClicked.emit(table.item(row, 0))
    qapp.processEvents()
    # The selection moved down to the Project — no second context, no
    # 'up to batch' button to return from (ADR-0009).
    assert hub._project_dir == tmp_path / "P2"
    assert "replicates" in hub._tiles["project"].summary_text()
    assert "selection is a project" in hub._tiles["batch"].summary_text()


def test_the_batch_table_lists_nested_projects_and_reds_the_blocked(
        hub, qapp, tmp_path):  # noqa: F811
    """Recursive discovery (ADR-0011): rows carry the relative-path key, and a
    Project with a Blocked Experiment is red with the reason in its tooltip."""
    from pytrackinganalysis.apps.batch_preflight import blocked_color
    from tests.test_batch_discovery import make_project, unfiled

    make_project(tmp_path / "Sept2026" / "ProjA", replicates=("Rep1", "Rep2"))
    make_project(tmp_path / "Sept2026" / "ProjB")
    unfiled(tmp_path / "Sept2026" / "ProjB" / "Rep2")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    table = hub._batch_table
    rows = {table.item(r, 0).text(): r for r in range(table.rowCount())}
    assert sorted(rows) == ["Sept2026/ProjA", "Sept2026/ProjB"]
    ok_row, blocked_row = rows["Sept2026/ProjA"], rows["Sept2026/ProjB"]
    assert table.item(ok_row, 1).text() == "2/2"
    assert table.item(ok_row, 3).text() == "ok"
    assert table.item(blocked_row, 1).text() == "1/2"
    assert "blocked" in table.item(blocked_row, 3).text()
    assert table.item(blocked_row, 0).foreground().color() == blocked_color()
    assert "unfiled" in table.item(blocked_row, 3).toolTip()
    # ...and a healthy row is left in the ordinary text color.
    assert table.item(ok_row, 0).foreground().color() != blocked_color()


def test_a_member_with_nothing_runnable_starts_unchecked(hub, qapp, tmp_path):  # noqa: F811
    """It can only produce a failure, so it does not silently join the run —
    but it stays checkable (ADR-0011)."""
    from tests.test_batch_discovery import make_project

    make_project(tmp_path / "ProjA")
    make_project(tmp_path / "Pending", configs=False)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    assert hub._batch_checked_names() == ["ProjA"]


def test_the_batch_walk_is_cached_until_something_changes(hub, qapp, tmp_path,
                                                          monkeypatch):  # noqa: F811
    """_refresh_tiles fires on every checkbox toggle and finished task; the
    recursive walk must not run again each time (ADR-0011)."""
    from pytrackinganalysis import batch as batch_mod
    from tests.test_batch_discovery import make_project

    make_project(tmp_path / "ProjA")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    walks: list = []
    real = batch_mod.discover
    monkeypatch.setattr(batch_mod, "discover",
                        lambda root: walks.append(root) or real(root))

    hub._refresh_tiles()
    hub._refresh_batch_view()
    assert walks == []                       # served from the cache

    hub._rescan_batch()
    assert len(walks) == 1                   # ...until asked to look again


def test_run_batch_goes_through_the_preflight(hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """The preflight is where the target list is confirmed, so its answers —
    not the table's — are what the run receives (ADR-0011)."""
    from pytrackinganalysis import batch as batch_mod
    from tests.test_batch_discovery import make_project

    make_project(tmp_path / "Sept2026" / "ProjA")
    make_project(tmp_path / "Sept2026" / "ProjB")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    calls: list = []

    def fake_run_batch(root, script_name=None, project_names=None, log=print,
                       apply_removals=True):
        calls.append((project_names, apply_removals))
        return {n: "ok" for n in (project_names or [])}

    monkeypatch.setattr(batch_mod, "run_batch", fake_run_batch)
    monkeypatch.setattr(type(hub), "_unload_experiment", lambda self: None)
    monkeypatch.setattr(hub, "_spawn_task", lambda name, fn: fn())
    _accept_preflight(monkeypatch, keys={"Sept2026/ProjB"},
                      apply_removals=False)

    hub._run_batch()

    assert calls == [(["Sept2026/ProjB"], False)]


def test_cancelling_the_preflight_runs_nothing(hub, qapp, tmp_path,
                                               monkeypatch):  # noqa: F811
    from PyQt6.QtWidgets import QDialog

    from pytrackinganalysis.apps import batch_preflight
    from tests.test_batch_discovery import make_project

    make_project(tmp_path / "ProjA")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    monkeypatch.setattr(batch_preflight.BatchPreflightDialog, "exec",
                        lambda self: QDialog.DialogCode.Rejected)
    spawned: list = []
    monkeypatch.setattr(hub, "_spawn_task", lambda name, fn: spawned.append(name))

    hub._run_batch()
    assert spawned == []


def test_batch_picker_defaults_to_each_projects_own_script(
        hub, qapp, tmp_path):  # noqa: F811
    """The default designation is now 'each project's own script' (ADR-0009
    amendment) — the built-ins below it are explicit choices, not the silent
    fallback they used to be."""
    import yaml

    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    combo = hub._batch_script_combo
    items = [(combo.itemText(i), combo.itemData(i))
             for i in range(combo.count())]
    assert items[0] == ("Each project's own 'batch' script (default)",
                        ("default", None))
    assert items[1] == ("Report pipeline (built-in)", ("builtin", "report"))
    assert items[2] == ("Standard pipeline (built-in)",
                        ("builtin", "standard"))
    assert combo.currentIndex() == 0
    # The default never creates batch.yaml — the lazy-marker rule.
    assert not (tmp_path / "batch.yaml").exists()

    combo.setCurrentIndex(2)                  # the user designates Standard
    qapp.processEvents()
    data = yaml.safe_load((tmp_path / "batch.yaml").read_text())
    assert data["script"] == "Standard pipeline"

    # Designating the Report pipeline built-in is now an explicit name, not
    # the absence of one — it must round-trip through batch.yaml.
    combo.setCurrentIndex(1)
    qapp.processEvents()
    data = yaml.safe_load((tmp_path / "batch.yaml").read_text())
    assert data["script"] == "Report pipeline"
    hub._refresh_batch_view()
    assert hub._batch_script_combo.currentIndex() == 1

    combo.setCurrentIndex(0)                  # back to the default
    qapp.processEvents()
    data = yaml.safe_load((tmp_path / "batch.yaml").read_text()) or {}
    assert "script" not in data               # cleared, file kept


def test_run_batch_runs_checked_projects_and_unloads_first(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    from PyQt6.QtCore import Qt

    from pytrackinganalysis import batch as batch_mod

    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    hub._batch_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)

    calls: list = []

    def fake_run_batch(root, script_name=None, project_names=None, log=print,
                       apply_removals=True):
        calls.append((root, script_name, project_names))
        return {"P2": "ok"}

    monkeypatch.setattr(batch_mod, "run_batch", fake_run_batch)
    _accept_preflight(monkeypatch)
    unloaded: list = []
    monkeypatch.setattr(type(hub), "_unload_experiment",
                        lambda self: unloaded.append(True))
    spawned: list = []
    monkeypatch.setattr(hub, "_spawn_task",
                        lambda name, fn: spawned.append((name, fn())))

    hub._run_batch()

    assert unloaded == [True]
    assert spawned[0][0] == "Batch Run"
    root, script_name, project_names = calls[0]
    assert root == str(tmp_path)
    assert script_name is None                # Report pipeline default
    assert project_names == ["P2"]
    assert "1/1" in spawned[0][1]


def test_batch_panel_has_its_own_folder_picker(hub, qapp, tmp_path,
                                               monkeypatch):  # noqa: F811
    """Choosing the batch parent from the Batch panel auto-loads every
    Project inside it — no detour through the Project tile."""
    from PyQt6.QtWidgets import QFileDialog, QPushButton

    _make_batch(tmp_path)
    buttons = {b.text(): b for b in
               hub._cards["batch"].findChildren(QPushButton)}
    assert "Choose batch folder…" in buttons
    btn = buttons["Choose batch folder…"]
    assert btn.isEnabled()                    # the way in is never gated
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    btn.click()
    qapp.processEvents()
    assert hub._project_dir == tmp_path
    assert [hub._batch_table.item(r, 0).text()
            for r in range(hub._batch_table.rowCount())] == ["P1", "P2"]
    assert not hub._tiles["batch"].is_dimmed()


def test_batch_tools_are_gone_from_every_card(hub):
    """Removed (2026-08-24): the dialog had been disabled since ADR-0009, and
    its six tools iterated a Project's subdirectories by hand — work the
    Project workflow (Experiment configs…, Project reports) now owns."""
    from PyQt6.QtWidgets import QPushButton

    for key in ("batch", "project"):
        labels = [b.text() for b in hub._cards[key].findChildren(QPushButton)]
        assert not any("Batch tools" in t for t in labels), key
    assert not hasattr(hub, "_btn_batch_tools")
    assert not hasattr(hub, "_convert_subdirectories")


def test_hub_preflight_blocks_a_typoed_script_before_running(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """Typos never reach a run: an only: name matching no replicate aborts
    with a warning before anything spawns."""
    from pytrackinganalysis.script_editor.runner import save_scripts

    _make_project(tmp_path)
    save_scripts(str(tmp_path / "project.yaml"),
                 [{"name": "target", "steps": [
                     {"action": "run_in_experiments",
                      "params": {"script": "qc", "only": ["Ghost"]}}]}],
                 key="scripts")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    warnings: list = []
    spawned: list = []
    monkeypatch.setattr(type(hub), "_warn",
                        lambda self, msg: warnings.append(msg))
    monkeypatch.setattr(hub, "_spawn_task",
                        lambda name, fn: spawned.append(name))
    combo = hub._project_script_combo
    combo.setCurrentIndex(next(i for i in range(combo.count())
                               if combo.itemData(i) == "target"))
    hub._project_run_script()
    assert spawned == []
    assert warnings and "Ghost" in warnings[0]


def test_batch_table_shows_replicate_and_report_status(hub, qapp, tmp_path):  # noqa: F811
    """Replicates read "usable/total" now (ADR-0011), so a Project with a
    blocked experiment can be told from one without leaving the card."""
    _make_batch(tmp_path)
    (tmp_path / "P1" / "Proj_report.pdf").write_bytes(b"%PDF-")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    table = hub._batch_table
    rows = {table.item(r, 0).text():
            tuple(table.item(r, c).text() for c in (1, 2, 3))
            for r in range(table.rowCount())}
    assert rows["P1"] == ("2/2", "yes", "ok")
    assert rows["P2"] == ("2/2", "no", "ok")


def test_busy_state_greys_the_batch_card(hub):
    hub._set_busy(True)
    assert not hub._cards["batch"].isEnabled()
    hub._set_busy(False)
    assert hub._cards["batch"].isEnabled()


def test_run_batch_requires_a_checked_project(hub, qapp, tmp_path,
                                              monkeypatch):  # noqa: F811
    from PyQt6.QtCore import Qt

    _make_batch(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    for r in range(hub._batch_table.rowCount()):
        hub._batch_table.item(r, 0).setCheckState(Qt.CheckState.Unchecked)
    warnings: list = []
    spawned: list = []
    monkeypatch.setattr(type(hub), "_warn",
                        lambda self, msg: warnings.append(msg))
    monkeypatch.setattr(hub, "_spawn_task",
                        lambda name, fn: spawned.append(name))
    _accept_preflight(monkeypatch, keys=set())
    hub._run_batch()
    assert spawned == []
    assert warnings


def test_batch_picker_lists_an_unlisted_designation(hub, qapp, tmp_path):  # noqa: F811
    """A designation naming a script each project.yaml defines has no
    central entry to select — it gets its own '(from each project)' row
    rather than silently reverting to the default."""
    import yaml

    _make_batch(tmp_path)
    (tmp_path / "batch.yaml").write_text(
        yaml.safe_dump({"script": "per-project"}), encoding="utf-8")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    combo = hub._batch_script_combo
    items = [(combo.itemText(i), combo.itemData(i))
             for i in range(combo.count())]
    assert ("per-project (from each project)",
            ("name", "per-project")) in items
    assert combo.currentData() == ("name", "per-project")


# ---- View reports ----------------------------------------------------------

def test_view_reports_needs_both_kinds_of_report(hub, qapp, tmp_path):  # noqa: F811
    """The button lays the pooled report beside the replicates it pools, so
    it stays disabled until both exist — opening one half alone is what the
    replicate table already does."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()
    btn = hub._btn_view_reports
    assert not btn.isEnabled()
    assert "No reports yet" in btn.toolTip()

    (tmp_path / "Rep1" / "Rep1_report.pdf").write_bytes(b"%PDF-1.4\n")
    hub._refresh_project_view()
    qapp.processEvents()
    assert not btn.isEnabled()                      # no Project report yet
    assert "No Project report yet" in btn.toolTip()

    (tmp_path / "Proj_report.pdf").write_bytes(b"%PDF-1.4\n")
    hub._refresh_project_view()
    qapp.processEvents()
    assert btn.isEnabled()
    assert "1 replicate report(s)" in btn.toolTip()


def test_view_reports_opens_every_report(hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    (tmp_path / "Proj_report.pdf").write_bytes(b"%PDF-1.4\n")
    for rep in ("Rep1", "Rep2"):
        (tmp_path / rep / f"{rep}_report.pdf").write_bytes(b"%PDF-1.4\n")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    opened: list = []
    hub._open_pdf = lambda path: (opened.append(path.name), True)[1]
    hub._project_view_reports()
    # The Project report first, then each replicate in table order.
    assert opened == ["Proj_report.pdf", "Rep1_report.pdf", "Rep2_report.pdf"]


def test_view_reports_skips_a_replicate_report_deleted_since_the_refresh(
        hub, qapp, tmp_path):  # noqa: F811
    """Replicate paths are re-checked at click time, so a vanished one is
    simply not opened — no error, no missing path handed to the OS."""
    _make_project(tmp_path)
    (tmp_path / "Proj_report.pdf").write_bytes(b"%PDF-1.4\n")
    for rep in ("Rep1", "Rep2"):
        (tmp_path / rep / f"{rep}_report.pdf").write_bytes(b"%PDF-1.4\n")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    (tmp_path / "Rep1" / "Rep1_report.pdf").unlink()
    opened: list = []
    hub._open_pdf = lambda path: (opened.append(path.name), True)[1]
    hub._project_view_reports()
    assert opened == ["Proj_report.pdf", "Rep2_report.pdf"]


def test_view_reports_reports_a_project_report_deleted_since_the_refresh(
        hub, qapp, tmp_path, monkeypatch):  # noqa: F811
    """The button was enabled by a refresh; the pooled report can vanish
    before the click. Say so, and still open what remains."""
    _make_project(tmp_path)
    (tmp_path / "Proj_report.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "Rep1" / "Rep1_report.pdf").write_bytes(b"%PDF-1.4\n")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    (tmp_path / "Proj_report.pdf").unlink()
    warned: list = []
    monkeypatch.setattr(type(hub), "_warn",
                        lambda self, msg: warned.append(msg))
    opened: list = []
    hub._open_pdf = lambda path: (opened.append(path.name), True)[1]
    hub._project_view_reports()

    assert opened == ["Rep1_report.pdf"]
    assert warned and "Proj_report.pdf" in warned[0]
    # The stale enable is corrected on the way out.
    assert not hub._btn_view_reports.isEnabled()


# ---- suppressing new tabs --------------------------------------------------

def test_suppress_tabs_checkbox_lives_on_the_batch_card(hub):
    """A Batch Run touches every replicate of every Project, so it is the
    run that would bury the Output tab under hundreds of artifact tabs.
    On by default: the artifacts are all on disk either way, so tabs are the
    thing you opt IN to (user feedback 2026-08-24)."""
    box = hub._chk_suppress_tabs
    assert box.isChecked()
    assert box in hub._cards["batch"].findChildren(type(box))


def test_artifact_tabs_stop_when_suppressed(hub, qapp, tmp_path):  # noqa: F811
    """`Saved: <path>` lines open a tab per artifact; checked, they do not.
    The file is still written — only the tab is skipped."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    artifact = tmp_path / "one.txt"
    artifact.write_text("hello", encoding="utf-8")
    hub._chk_suppress_tabs.setChecked(False)
    before = hub._plot_dock.count()
    hub._on_worker_log(f"Saved: {artifact}\n")
    qapp.processEvents()
    assert hub._plot_dock.count() == before + 1

    hub._chk_suppress_tabs.setChecked(True)
    second = tmp_path / "two.txt"
    second.write_text("hello", encoding="utf-8")
    opened = hub._plot_dock.count()
    hub._on_worker_log(f"Saved: {second}\n")
    qapp.processEvents()
    assert hub._plot_dock.count() == opened      # no new tab
    assert second.is_file()                      # but the artifact exists


def test_output_and_errors_keep_updating_while_suppressed(hub, qapp, tmp_path):  # noqa: F811
    """Only tabs are suppressed — the two standing log tabs must keep
    streaming, since they are all the user has left to watch."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    hub._chk_suppress_tabs.setChecked(True)
    qapp.processEvents()

    tabs = hub._plot_dock.count()
    hub._on_worker_log("[P1] running analysis…\n[P1] done\n")
    hub._log_issue("[P2] FAILED: boom")
    qapp.processEvents()

    assert hub._plot_dock.count() == tabs
    assert "[P1] done" in hub._log.toPlainText()
    assert "[P2] FAILED: boom" in hub._err_log.toPlainText()


def test_suppressed_figures_are_closed_not_leaked(hub, qapp):  # noqa: F811
    """A figure that never becomes a tab has no widget to own it; pyplot
    would hold it for the life of the process. Over a Batch Run that is the
    very leak the switch exists to avoid."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.close("all")
    hub._chk_suppress_tabs.setChecked(True)
    figure = plt.figure()
    assert plt.get_fignums()

    assert hub._discard_figures([("Title", figure)], "plot") is True
    assert not plt.get_fignums()                 # closed, not leaked
    assert "not shown" in hub._log.toPlainText()

    # Unchecked, the caller keeps its figures and adds them as tabs itself.
    hub._chk_suppress_tabs.setChecked(False)
    other = plt.figure()
    assert hub._discard_figures([("Title", other)], "plot") is False
    assert plt.get_fignums()
    plt.close("all")


# ---- Validation ------------------------------------------------------------

def test_the_tools_panel_is_gone_and_validate_moved_to_the_project(hub):
    """Tools held three actions: one was folder-opening the file manager does
    better, one now happens on close, and Validate YAMLs is Project work — so
    it lives with the Project it validates."""
    from pytrackinganalysis.ui.widgets import ActionButton

    assert "tools" not in hub._cards and "tools" not in hub._tiles
    assert not hasattr(hub, "_open_folder")

    labels = [b.text() for b in
              hub._cards["project"].findChildren(ActionButton)]
    assert "Validate YAMLs" in labels
    # The two that did not survive, anywhere in the Hub.
    everywhere = [b.text() for card in hub._cards.values()
                  for b in card.findChildren(ActionButton)]
    assert "Open qc folder" not in everywhere
    assert "Clear matplotlib cache" not in everywhere


def test_the_matplotlib_cache_is_cleared_on_close(hub, qapp, monkeypatch,
                                                  tmp_path):
    """A stale font cache shows up as a figure that will not render, and by
    then the run is already lost — so it goes on the way out, not on a
    button nobody thinks to press."""
    import matplotlib as mpl
    from PyQt6.QtGui import QCloseEvent

    cache = tmp_path / "mplcache"
    (cache / "fontlist").mkdir(parents=True)
    monkeypatch.setattr(mpl, "get_cachedir", lambda: str(cache))
    hub.closeEvent(QCloseEvent())
    assert not cache.exists()


def test_clearing_the_cache_can_never_block_the_close(hub, monkeypatch):
    import matplotlib as mpl

    def _boom():
        raise RuntimeError("no cachedir for you")

    monkeypatch.setattr(mpl, "get_cachedir", _boom)
    hub._clear_mpl_cache()   # must not raise


def test_validate_yamls_covers_the_project_and_every_replicate(
        hub, qapp, tmp_path):  # noqa: F811
    """Validating only the loaded replicate left the rest of a Project
    unchecked — which is exactly where a bad config hides."""
    _make_project(tmp_path)
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    targets = hub._yaml_validation_targets()
    assert [p.name for p in targets] == [
        "project.yaml", "tracking_config.yaml", "tracking_config.yaml"]
    assert targets[0].parent == tmp_path
    assert {p.parent.name for p in targets[1:]} == {"Rep1", "Rep2"}

    hub._validate_yaml()
    qapp.processEvents()
    output = hub._log.toPlainText()
    assert "3 file(s) checked" in output


def test_validate_yamls_reports_a_broken_replicate_config(
        hub, qapp, tmp_path):  # noqa: F811
    _make_project(tmp_path)
    (tmp_path / "Rep2" / "tracking_config.yaml").write_text(
        "experiment_name: [oops\n", encoding="utf-8")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    hub._validate_yaml()
    qapp.processEvents()
    errors = hub._err_log.toPlainText()
    assert "Rep2" in errors and "YAML syntax error" in errors


def test_validate_yamls_flags_a_project_with_no_script(
        hub, qapp, tmp_path):  # noqa: F811
    """A project.yaml with no Project Script cannot be run by a Batch Run,
    so validation is where that should surface."""
    from pytrackinganalysis.script_editor.runner import save_scripts

    _make_project(tmp_path)
    save_scripts(str(tmp_path / "project.yaml"), [], key="scripts")
    hub._set_project_dir(str(tmp_path))
    qapp.processEvents()

    hub._validate_yaml()
    qapp.processEvents()
    assert "no Project Script" in hub._err_log.toPlainText()


def test_panel_opens_tall_enough_for_cards_rebuilt_while_closed(hub, qapp):  # noqa: F811
    """Plot buttons are rebuilt on load, while the Plots panel is closed. Qt
    defers geometry updates for hidden widgets, so the panel used to reopen at
    its pre-rebuild height with a scrollbar — it must fit all four buttons."""
    from pytrackinganalysis.apps.hub import _PLOT_BUTTONS
    from pytrackinganalysis.ui import Category
    from pytrackinganalysis.ui.widgets import ActionButton

    hub._open_panel("plots")                  # open once at the empty height
    qapp.processEvents()
    hub._close_panel()
    qapp.processEvents()

    for label, _flat, _facet in _PLOT_BUTTONS["TWOCHOICETRACKER"]:
        btn = ActionButton(label, Category.PLOTS, icon_name="plot")
        hub._plots_card.add_body(btn)
        hub._plot_buttons.append(btn)
    hub._plots_empty.setVisible(False)
    qapp.processEvents()

    hub._open_panel("plots")
    qapp.processEvents()
    panel = hub._panels["plots"]
    content = panel._scroll.widget().sizeHint().height()
    assert panel._scroll.viewport().height() >= content
    assert panel._scroll.verticalScrollBar().maximum() == 0
