"""Tests for tui/app.py: pure helpers (theme resolve, due words) plus a
headless end-to-end run of the TUI against a temp vault. Run: pytest tui/"""

import asyncio
import datetime as dt
from pathlib import Path
import pytest
from textual.widgets._toast import Toast

try:
    from tui import app as appmod
    from tui import taskman as tm
except ImportError:  # running from inside tui/
    import app as appmod
    import taskman as tm


def test_opening_view_inbox_all_or_now(tmp_path):
    (tmp_path / "Tasks").mkdir()
    inbox = tmp_path / "Tasks" / "Inbox.md"
    inbox.write_text("", encoding="utf-8")
    assert appmod._opening_view(tm.Store(tmp_path).refresh()) == "inbox"
    inbox.write_text("- [ ] Loose end\n", encoding="utf-8")
    assert appmod._opening_view(tm.Store(tmp_path).refresh(force=True)) == "all"
    inbox.write_text(f"- [ ] Due today 📅 {dt.date.today().isoformat()}\n", encoding="utf-8")
    assert appmod._opening_view(tm.Store(tmp_path).refresh(force=True)) == "now"
    assert appmod.DEFAULT_VIEW == "now"


def test_format_due_words():
    day = dt.date(2026, 9, 4)
    assert appmod.format_due(day, day) == "today"
    assert appmod.format_due(day + dt.timedelta(days=1), day) == "tomorrow"
    assert appmod.format_due(day + dt.timedelta(days=3), day) == "Mon 9/7"
    assert appmod.format_due(day + dt.timedelta(days=30), day) == "Oct 4"
    assert appmod.format_due(dt.date(2027, 1, 2), day) == "2027-01-02"
    assert appmod.format_due(day - dt.timedelta(days=1), day) == "1d late"   # words, not just red
    assert all(len(appmod.format_due(day + dt.timedelta(days=n), day)) <= 10
               for n in range(-120, 400))                                     # fits the column


def test_format_date_long():
    day = dt.date(2026, 9, 4)
    assert appmod.format_date_long(day + dt.timedelta(days=1), day) == "Sat Sep 5 (tomorrow)"
    assert appmod.format_date_long(day - dt.timedelta(days=2), day, past="late") == "Wed Sep 2 (2 days late)"
    assert appmod.format_date_long(day - dt.timedelta(days=1), day, past="late") == "Thu Sep 3 (1 day late)"
    assert appmod.format_date_long(day - dt.timedelta(days=1), day) == "Thu Sep 3 (yesterday)"
    assert appmod.format_date_long(day + dt.timedelta(days=40), day) == "Wed Oct 14"


def test_status_and_priority_glyphs_are_distinct_shapes():
    glyphs = [g for g, _ in appmod.STATUS_GLYPH.values()]
    assert len(set(glyphs)) >= 5                       # open/progress/done/cancel/forward differ
    prio = [appmod.PRIO_GLYPH[i] for i in range(1, 6)]
    assert len(set(prio)) == 5 and all(len(g) == 1 for g in prio)


def test_resolve_theme_precedence(monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(appmod, "read_theme_file", lambda path=None: "taskman-moss")
    assert appmod.resolve_theme("taskman-ember") == "taskman-ember"   # explicit wins
    assert appmod.resolve_theme("taskman-rcm") == "taskman-dark-teal"  # legacy preference
    assert appmod.resolve_theme(None) == "taskman-moss"               # then the saved file
    monkeypatch.setenv("TASKMAN_THEME", "catppuccin-latte")
    assert appmod.resolve_theme(None) == "taskman-catppuccin-latte"  # saved Latte name maps to the port
    monkeypatch.setattr(appmod, "read_theme_file", lambda path=None: "bogus")
    monkeypatch.delenv("TASKMAN_THEME")
    assert appmod.resolve_theme(None) == appmod.DEFAULT_THEME        # invalid falls back
    monkeypatch.setenv("NO_COLOR", "1")
    assert appmod.resolve_theme("taskman-iris") == appmod.HIGH_CONTRAST  # NO_COLOR wins all


def test_theme_file_roundtrip(tmp_path):
    p = tmp_path / "theme.txt"
    assert appmod.read_theme_file(p) == ""
    appmod.write_theme_file("taskman-ocean", p)
    assert appmod.read_theme_file(p) == "taskman-ocean"
    p.write_text("taskman-rcm\n", encoding="utf-8")
    assert appmod.read_theme_file(p) == "taskman-dark-teal"
    p.write_text("bogus\n", encoding="utf-8")
    assert appmod.read_theme_file(p) == ""


def test_curated_themes_valid(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.setattr(appmod, "read_theme_file", lambda path=None: "")
    names = [t[0] for t in appmod.THEMES]
    assert appmod.DEFAULT_THEME in names and appmod.HIGH_CONTRAST in names
    assert len(names) == len(set(names))  # no duplicates
    dark = [n for n, _label, is_dark in appmod.THEMES if is_dark and n != appmod.HIGH_CONTRAST]
    assert dark == [t.name for t, _label in appmod.DARK_THEMES]
    assert appmod.DEFAULT_THEME in dark
    assert [label for _n, label, _d in appmod.THEMES][:5] == [
        "Teal", "Ocean", "Ember", "Iris", "Moss"]
    assert [label for _n, label, dark in appmod.THEMES if not dark] == [
        label for _t, label in appmod.LIGHT_THEMES]
    # Ports keep their signature colors.
    by_name = {t.name: t for t, _ in appmod.DARK_THEMES}
    assert by_name["taskman-darcula"].surface == "#2b2b2b" and by_name["taskman-darcula"].accent == "#cc7832"
    assert by_name["taskman-onedark"].surface == "#282c34" and by_name["taskman-onedark"].primary == "#61afef"
    dark_teal = by_name["taskman-dark-teal"]
    assert dark_teal.surface == "#2b2b2b" and dark_teal.foreground == "#ffffff"
    assert dark_teal.primary == "#244d4f" and dark_teal.success == "#4fa672"
    assert dark_teal.variables["block-cursor-foreground"] == "#ffffff"
    kanagawa = by_name["taskman-kanagawa"]
    assert kanagawa.surface == "#1f1f28" and kanagawa.primary == "#7e9cd8"
    assert kanagawa.foreground == "#dcd7ba" and kanagawa.accent == "#957fb8"
    gruvbox_dark = by_name["taskman-gruvbox-dark"]
    assert gruvbox_dark.background == "#282828" and gruvbox_dark.primary == "#83a598"
    assert gruvbox_dark.accent == "#fe8019" and gruvbox_dark.success == "#b8bb26"
    mocha = by_name["taskman-catppuccin-mocha"]
    assert mocha.background == "#181825" and mocha.primary == "#cba6f7"
    assert mocha.foreground == "#cdd6f4" and mocha.error == "#f38ba8"
    nord = by_name["taskman-nord"]
    assert nord.background == "#2e3440" and nord.primary == "#5e81ac"
    assert nord.variables["text-primary"] == "#88c0d0"
    phosphor = by_name["taskman-phosphor"]
    assert phosphor.background == "#0d0b06" and phosphor.primary == "#ffb000"
    latte = next(t for t, _ in appmod.LIGHT_THEMES if t.name == "taskman-catppuccin-latte")
    assert latte.background == "#eff1f5" and latte.primary == "#1e66f5"
    assert not appmod.theme_is_dark("catppuccin-latte")
    assert not appmod.theme_is_dark("gruvbox-light")
    assert appmod.theme_is_dark("catppuccin-mocha") and appmod.theme_is_dark("nord")
    assert "bold" in appmod.due_style_today("catppuccin-latte")  # light-safe, never bare yellow
    assert "bold" in appmod.due_style_today("taskman-gruvbox-light")
    # Every dark theme is a real, registerable Theme with the cursor variables set.
    for theme, _label in appmod.DARK_THEMES:
        assert theme.dark and theme.variables["block-cursor-background"] == theme.primary
        assert theme.variables["block-cursor-foreground"] != theme.primary
        assert appmod.theme_swatch(theme.name).plain == "████"
    for theme, _label in appmod.LIGHT_THEMES:
        assert not theme.dark and appmod.theme_swatch(theme.name).plain == "████"
    assert appmod.theme_swatch("catppuccin-latte").plain == "████"
    assert appmod.theme_swatch("kanagawa").plain == "████"
    assert appmod.resolve_theme("kanagawa") == "taskman-kanagawa"
    assert appmod.resolve_theme("gruvbox-light") == "taskman-gruvbox-light"
    assert appmod.resolve_theme("gruvbox-dark") == "taskman-gruvbox-dark"
    assert appmod.resolve_theme("catppuccin-mocha") == "taskman-catppuccin-mocha"
    assert appmod.resolve_theme("nord") == "taskman-nord"
    assert appmod.resolve_theme("solarized") == appmod.DEFAULT_THEME   # not curated -> default


# ---------------------------------------------------------------------------
# Headless end-to-end: drive the real app with Textual's pilot.
# ---------------------------------------------------------------------------

def _vault(tmp_path: Path) -> Path:
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        "# Inbox\n\n"
        "- [ ] Renew passport 🔼\n"
        "- [ ] Parent with kids 📅 2026-09-04\n"
        "  - [ ] Kid one\n"
        "  - [x] Kid two ✅ 2026-09-03\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects" / "Alpha.md").write_text(
        "# Alpha\n\n## Tasks\n\n- [ ] Ship it ⏫ 📅 2026-09-10 #project/Alpha\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(coro):
    return asyncio.run(coro)


def test_all_open_keeps_sections_and_tree(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(110, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            assert app.view == "all"                               # explicitly selected All open
            tl = app.query_one(appmod.TaskList)
            assert tl.has_focus and tl.current is not None
            headers = [r.title for r in tl.rows if isinstance(r, appmod.HeaderRow) and r.title]
            assert headers[0] in ("Overdue", "Today")              # depends on the real date
            descs = [r.task.description for r in tl.rows if isinstance(r, appmod.TaskRow)]
            assert "Kid two" not in descs                          # done: not in All open
            i_parent, i_kid = descs.index("Parent with kids"), descs.index("Kid one")
            assert i_kid == i_parent + 1                           # child hangs under parent
            kid_row = [r for r in tl.rows if isinstance(r, appmod.TaskRow)][i_kid]
            assert kid_row.prefix.endswith("└ ") and kid_row.depth == 1
            assert tl.cursor >= 1 and isinstance(tl.rows[0], appmod.HeaderRow)  # never on a header
            # Sidebar shows every view + the project, with counts.
            sidebar = app.query_one(appmod.Sidebar)
            ids = [o.id for o in sidebar.options]
            assert "view:all" in ids and "proj:Alpha" in ids
    _run(go())


def test_app_project_picker_creates_and_tags(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(110, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            tl = app.query_one(appmod.TaskList)
            # Put the cursor on "Renew passport" via search, then assign a NEW project.
            await pilot.press("slash")
            for ch in "passport":
                await pilot.press(ch)
            await pilot.pause()
            assert tl.current is not None and tl.current.description == "Renew passport"
            await pilot.press("escape")                              # clears search, back to list
            await pilot.pause()
            assert app.search_query == "" and tl.current.description == "Renew passport"
            await pilot.press("j")                                   # proJect
            await pilot.pause()
            assert isinstance(app.screen, appmod.ProjectScreen)
            for ch in "Beta Launch":
                await pilot.press(ch if ch != " " else "space")
            await pilot.pause()
            await pilot.press("enter")                               # "+ Create Beta-Launch"
            await pilot.pause()
            text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
            assert "- [ ] Renew passport 🔼 #project/Beta-Launch" in text
            assert (root / "Projects" / "Beta-Launch.md").exists()  # project note created
            assert tl.current is not None and tl.current.project == "Beta-Launch"
            # Now switch it to the existing project via filter + Enter.
            await pilot.press("j")
            await pilot.pause()
            for ch in "alp":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
            assert "#project/Alpha" in text and "#project/Beta-Launch" not in text
    _run(go())


def test_sidebar_browses_views_and_projects_one_step_at_a_time(tmp_path, monkeypatch):
    """Regression: the separator before PROJECTS is not an option, so the
    active-item index must be computed over Sidebar.options, not the raw
    content list — otherwise highlighting a project cascades two items on."""
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)
    (root / "Projects" / "Beta.md").write_text("# Beta\n\n## Tasks\n\n- [ ] B1\n", encoding="utf-8")

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(110, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            sidebar = app.query_one(appmod.Sidebar)
            await pilot.press("tab")                                  # list -> sidebar
            await pilot.pause()
            assert sidebar.has_focus
            seen = []
            for _ in range(len(appmod.SIDEBAR_VIEWS) + 1):            # 6 more views, Notes, 1st project
                await pilot.press("down")
                await pilot.pause()
                seen.append((app.view, app.project))
            assert seen[:6] == [(v, "") for _, v, _ in appmod.SIDEBAR_VIEWS[1:]]
            assert seen[6] == ("notes", "")
            assert seen[7] == ("project", "Alpha")                    # first project, not second
            assert sidebar.options[sidebar.highlighted].id == "proj:Alpha"
            await pilot.press("down")
            await pilot.pause()
            assert (app.view, app.project) == ("project", "Beta")
            tl = app.query_one(appmod.TaskList)
            assert tl.current is not None and tl.current.description == "B1"
            assert tl.border_title == "TASKS"
            assert "BETA" in str(app.query_one("#status-view").render())
            await pilot.press("enter")                                # back to the list
            await pilot.pause()
            assert tl.has_focus
    _run(go())


def test_every_shortcut_is_lowercase_and_labelled():
    """No Shift ever: every letter key is lowercase, and the label carries
    that letter as its capital (Add, subTask, proJect …)."""
    shifted = {"greater_than_sign", "less_than_sign", "question_mark", "colon", "plus",
               "exclamation_mark", "at", "number_sign", "dollar_sign", "percent_sign"}
    for b in appmod.TaskApp.BINDINGS:
        keys = b.key.split(",")
        for k in keys:
            assert not (len(k) == 1 and k.isalpha() and k.isupper()), f"{b.key} needs Shift"
        first = keys[0]                       # what the footer shows / what we teach
        if b.show:
            assert first not in shifted, f"{b.key}: displayed primary key needs Shift"
        if b.show and len(first) == 1 and first.isalpha():
            capitals = [ch for ch in b.description if ch.isupper()]
            assert capitals == [first.upper()], f"{b.description!r} should mark '{first}'"


def test_app_note_editor_saves_under_task(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(110, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            tl = app.query_one(appmod.TaskList)
            await pilot.press("f")                                   # Find
            for ch in "passport":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert tl.current.description == "Renew passport" and tl.current.note == ""
            await pilot.press("n")                                   # Note
            await pilot.pause()
            assert isinstance(app.screen, appmod.NoteScreen)
            for ch in "Expires in March.":
                await pilot.press(ch if ch != " " else "space")
            await pilot.press("enter")
            for ch in "Photos done.":
                await pilot.press(ch if ch != " " else "space")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
            assert ("- [ ] Renew passport 🔼\n  Expires in March.\n  Photos done.\n"
                    "- [ ] Parent with kids") in text
            assert tl.current.description == "Renew passport"
            assert tl.current.note == "Expires in March.\nPhotos done."
            # Esc with unsaved typing asks first; "Keep editing" returns to the editor.
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("x")
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, appmod.ConfirmScreen)
            await pilot.press("escape")                              # = keep editing
            await pilot.pause()
            assert isinstance(app.screen, appmod.NoteScreen)
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert tl.current.note.endswith("Photos done.x")
    _run(go())


def test_inspector_pane_toggles_and_shows_subtasks(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(130, 34)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            insp = app.query_one(appmod.Inspector)
            tl = app.query_one(appmod.TaskList)
            assert not insp.display                                  # closed by default
            await pilot.press("f")
            for ch in "Parent":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert tl.current.description == "Parent with kids"
            await pilot.press("i")                                   # open the pane
            await pilot.pause()
            assert insp.display and tl.has_focus                     # list keeps focus
            assert "Parent with kids" in insp.query_one("#ins-title").render().__str__()
            kids = insp.query_one("#ins-kids")
            assert [o.id for o in kids.options] == ["k:Tasks/Inbox.md:5", "k:Tasks/Inbox.md:6"]
            assert "1/2" in str(insp.query_one("#ins-subhead").render())
            assert str(app.query_one("#status-right").render()).startswith("also:")
            # Tab first reaches the scrollable inspector, then its child list.
            await pilot.press("tab")
            await pilot.pause()
            assert insp.has_focus
            await pilot.press("tab")
            await pilot.pause()
            assert kids.has_focus
            await pilot.press("enter")
            await pilot.pause()
            text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
            assert "  - [ ] Kid one\n" in text
            assert kids.has_focus
            await pilot.press("space")
            await pilot.pause()
            text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
            assert "  - [x] Kid one ✅ " in text
            assert "2/2" in str(insp.query_one("#ins-subhead").render())
            # Esc closes the inspector after the child action.
            await pilot.press("escape")
            await pilot.pause()
            assert not insp.display and tl.has_focus
            assert str(app.query_one("#status-right").render()).startswith("also:")
    _run(go())


def test_table_has_column_header_and_columns(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(140, 34)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            tl = app.query_one(appmod.TaskList)
            head = tl.render_line(0).text
            for col in ("PRIO", "TASK", "DATE", "PROJECT", "TAGS"):
                assert col in head
            # The first task row (virtual line 2: header, then section header, then task).
            row = tl.render_line(2).text
            assert "Parent with kids" in row or "Ship it" in row
            ship = next(r for r in tl.rows if isinstance(r, appmod.TaskRow) and r.task.description == "Ship it")
            width = tl.scrollable_content_region.width
            head = tl._render_colhead(width).plain
            line = tl._render_task(ship, width).plain
            assert "⇈ hi" in line and "Alpha" in line             # PRIO glyph+word, PROJECT column
            assert head.index("PROJECT") == line.index("Alpha")   # columns line up under headers
            assert head.index("DATE") == line.index("Due " + appmod.format_due(ship.task.due, tl.day))
    _run(go())


def test_ctrl_s_confirms_local_autosave_without_executing_vault_scripts(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)
    (root / "scripts").mkdir()
    (root / "scripts" / "push.ps1").write_text("Write-Host 'Pushed master -> test'\n", encoding="utf-8")
    def unexpected_execution(*args, **kwargs):
        pytest.fail("Saving a task must never execute a script from the vault")
    monkeypatch.setattr(appmod.subprocess, "run", unexpected_execution)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(120, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            task = app.query_one(appmod.TaskList).current
            await pilot.press("space")
            saved = (root / task.file).read_bytes()
            assert next(t for t in app.store.tasks if t.id == task.id).done
            await pilot.press("ctrl+s")                              # what most terminals send
            await pilot.pause()
            assert (root / task.file).read_bytes() == saved
            open_count = sum(1 for task in tm.load_all(root) if task.open)
            assert (f"Rescanned vault · {open_count} open "
                    f"task{'s' if open_count != 1 else ''}") in app.query_one("#status-info").render_line(0).text
            assert not list(app.screen.query(Toast))
    _run(go())


def test_explicit_theme_is_applied_at_startup(tmp_path, monkeypatch):
    """Regression: --theme NAME must win over the default (a loop variable
    once shadowed the parameter and every start-up came up Teal)."""
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(appmod, "read_theme_file", lambda path=None: "")
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="taskman-ember")
        assert app.theme == "taskman-ember" and app.theme_name == "taskman-ember"
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            assert app.theme == "taskman-ember"
            assert "Ember" in str(app.query_one("#clock").render())
    _run(go())


@pytest.mark.parametrize("name,label", [
    ("taskman-kanagawa", "Kanagawa"),
    ("taskman-gruvbox-light", "Gruvbox Light"),
    ("taskman-gruvbox-dark", "Gruvbox Dark"),
    ("taskman-catppuccin-mocha", "Catppuccin Mocha"),
    ("taskman-catppuccin-latte", "Catppuccin Latte"),
    ("taskman-nord", "Nord"),
    ("taskman-phosphor", "Phosphor"),
])
def test_curated_theme_applies_at_startup(tmp_path, monkeypatch, name, label):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(appmod, "read_theme_file", lambda path=None: "")
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme=name)
        assert app.theme == name and app.theme_name == name
        async with app.run_test(notifications=True, size=(100, 30)) as pilot:
            await pilot.press("1")
            await pilot.pause()
            assert app.theme == name
            assert label in str(app.query_one("#clock").render())
    _run(go())


def test_theme_picker_previews_reverts_and_keeps(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    saved: list[str] = []
    monkeypatch.setattr(appmod, "write_theme_file", lambda name, path=None: saved.append(name))
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="taskman-teal")
        async with app.run_test(notifications=True, size=(120, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            assert app.theme == "taskman-teal"
            await pilot.press("m")                                   # theMe
            await pilot.pause()
            assert isinstance(app.screen, appmod.ThemeScreen)
            await pilot.press("down")                                # highlight Ocean -> live preview
            await pilot.pause()
            assert app.theme == "taskman-ocean" and "Ocean" in str(app.query_one("#clock").render())
            await pilot.press("escape")                              # revert
            await pilot.pause()
            assert app.theme == "taskman-teal" and saved == []
            await pilot.press("m")
            await pilot.pause()
            for _ in range(3):                                       # Teal -> Ocean -> Ember -> Iris
                await pilot.press("down")
            await pilot.pause()
            assert app.theme == "taskman-iris"
            await pilot.press("enter")                               # keep
            await pilot.pause()
            assert app.theme == "taskman-iris" and saved == ["taskman-iris"]
            assert not isinstance(app.screen, appmod.ThemeScreen)
            # The sidebar re-baked its colors onto the new theme's Iris background.
            sidebar = app.query_one(appmod.Sidebar)
            style = sidebar.get_component_rich_style("sidebar--active")
            assert style.bgcolor is not None and style.bgcolor.triplet.hex.lower() == "#0f0c1b"
    _run(go())


def test_app_bracket_keys_indent_and_outdent(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)
    (root / "Tasks" / "P.md").write_text("- [ ] P1\n- [ ] P2\n", encoding="utf-8")

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(110, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            tl = app.query_one(appmod.TaskList)
            await pilot.press("f")
            for ch in "P2":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert tl.current.description == "P2"
            await pilot.press("right_square_bracket")                # ]  indent
            await pilot.pause()
            assert (root / "Tasks" / "P.md").read_text(encoding="utf-8") == "- [ ] P1\n  - [ ] P2\n"
            assert tl.current.description == "P2" and tl.current.depth == 1
            await pilot.press("left_square_bracket")                 # [  outdent
            await pilot.pause()
            assert (root / "Tasks" / "P.md").read_text(encoding="utf-8") == "- [ ] P1\n- [ ] P2\n"
    _run(go())


def test_app_toggle_and_status_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    root = _vault(tmp_path)

    async def go():
        app = appmod.TaskApp(root, theme="textual-dark")
        async with app.run_test(notifications=True, size=(110, 32)) as pilot:
            await pilot.press("1")  # This workflow exercises the All open view.
            await pilot.pause()
            tl = app.query_one(appmod.TaskList)
            await pilot.press("slash")
            for ch in "Ship":
                await pilot.press(ch)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert tl.current.description == "Ship it"
            await pilot.press("c")                                   # Complete
            await pilot.pause()
            alpha = (root / "Projects" / "Alpha.md").read_text(encoding="utf-8")
            assert "- [x] Ship it ⏫ 📅 2026-09-10 ✅ " in alpha
            # Done tasks leave "All open"; cursor lands on a neighbour, never a header.
            assert tl.current is None or tl.current.description != "Ship it"
            await pilot.press("7")                                   # Completed view
            await pilot.pause()
            assert app.view == "completed"
            descs = [r.task.description for r in tl.rows if isinstance(r, appmod.TaskRow)]
            assert "Ship it" in descs
    _run(go())
