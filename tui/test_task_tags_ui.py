"""Task tags are editable, undoable, and useful across real project views."""

from pathlib import Path

import pytest
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList

from tui import app as appmod, taskman as tm
from tui.commands import CommandScreen
from tui.notes import NotesStore, TaskLink
from tui.notes_ui import NotesWorkspace
from tui.shortcut_bar import ShortcutBar
from tui.vault_screen import VaultChoice, VaultScreen


@pytest.fixture
def tag_vault(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TASKMAN_THEME", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Tasks/Inbox.md").write_text("- [ ] Capture target\n", encoding="utf-8")
    (tmp_path / "Projects/Alpha.md").write_text(
        "# Alpha\n\n"
        "- [ ] Alpha review #person/amy #review #project/Alpha\n"
        "- [ ] Alpha near match #person/amylia\n"
        "- [ ] Untagged parent\n"
        "  - [ ] Child Amy #person/amy\n"
        "  - [ ] Child Bob #person/bob\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects/Beta.md").write_text(
        "# Beta\n\n"
        "- [ ] Beta review #person/amy #review\n"
        "- [ ] Beta other #person/bob\n",
        encoding="utf-8",
    )
    return tmp_path


def _task(root: Path, description: str):
    return next(task for task in tm.load_all(root) if task.description == description)


def _rows(app):
    return [row for row in app.query_one(appmod.TaskList).rows if isinstance(row, appmod.TaskRow)]


def _matches(app):
    return {row.task.description for row in _rows(app) if not row.task.context}


async def _select(app, pilot, description):
    table = app.query_one(appmod.TaskList)
    index = table.index_of(_task(app.vault, description).id)
    assert index >= 0, f"{description} must be visible before selecting it"
    table.cursor = index
    table._cursor_moved()
    table.focus()
    await pilot.pause()


async def _filter(app, pilot, tag="person/amy"):
    await pilot.press("ctrl+g")
    await pilot.pause()
    assert isinstance(app.screen, CommandScreen)
    wanted = f"tag:value:{tag.casefold()}" if tag else "tag:all"
    assert wanted in {command.id for command in app.screen.commands}
    app.screen.dismiss(wanted)
    await pilot.pause()


@pytest.mark.asyncio
@pytest.mark.parametrize("subtask", [False, True])
async def test_add_tags_field_merges_inline_tags_and_preserves_parent(tag_vault, subtask):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("1")
        await _select(app, pilot, "Capture target")
        await pilot.press("t" if subtask else "a")
        dialog = app.screen
        assert isinstance(dialog, appmod.AddScreen)
        dialog.query_one("#text", Input).value = "New tagged work #review"
        dialog.query_one("#tags", Input).value = "#person/Amy, review, follow-up"
        await pilot.click(dialog.query_one("#ok"))
        await pilot.pause()
        added = _task(tag_vault, "New tagged work")
        assert {tag.casefold() for tag in added.plain_tags} == {"person/amy", "review", "follow-up"}
        assert len(added.plain_tags) == 3
        assert app.query_one(appmod.TaskList).current.id == added.id
        assert app.history.can_undo
        if subtask:
            assert added.parent_lineno == _task(tag_vault, "Capture target").lineno
            assert _task(tag_vault, "Capture target").plain_tags == ()


@pytest.mark.asyncio
async def test_add_invalid_tags_keeps_draft_and_does_not_write(tag_vault):
    before = (tag_vault / "Tasks/Inbox.md").read_bytes()
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("1", "a")
        dialog = app.screen
        dialog.query_one("#text", Input).value = "Keep this draft"
        dialog.query_one("#tags", Input).value = "person/amy!"
        await pilot.press("enter")
        assert app.screen is dialog
        assert dialog.query_one("#text", Input).value == "Keep this draft"
        assert dialog.query_one("#tags", Input).value == "person/amy!"
        assert str(dialog.query_one("#form-error", Label).render()).strip()
        assert (tag_vault / "Tasks/Inbox.md").read_bytes() == before
        assert not app.history.can_undo
        await pilot.press("escape")


@pytest.mark.asyncio
async def test_edit_tags_preserves_project_and_undo_restores_filter_context(tag_vault):
    path = tag_vault / "Projects/Alpha.md"
    before = path.read_bytes()
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(110, 30)) as pilot:
        app._sidebar_pick("proj:Alpha")
        app.action_focus_tasks()
        await _filter(app, pilot)
        await _select(app, pilot, "Alpha review")
        await pilot.press("g")
        dialog = app.screen
        assert isinstance(dialog, appmod.TaskTagsScreen)
        field = dialog.query_one("#task-tags", Input)
        assert "person/amy" in field.value and "review" in field.value
        assert "project/" not in field.value
        field.value = "person/bob, follow-up"
        await pilot.press("enter")
        await pilot.pause()
        changed = path.read_bytes()
        assert changed != before
        task = _task(tag_vault, "Alpha review")
        assert task.project == "Alpha"
        assert "project/Alpha" in task.tags
        assert task.plain_tags == ("person/bob", "follow-up")
        assert "Alpha review" not in _matches(app)
        assert app.task_tag == "person/amy"
        app._sidebar_pick("proj:Beta")
        app.action_focus_tasks()
        await _filter(app, pilot, "")
        await pilot.press("u")
        await pilot.pause()
        assert path.read_bytes() == before
        assert app.view == "project" and app.project == "Alpha"
        assert app.task_tag == "person/amy"
        assert "Alpha review" in _matches(app)
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert path.read_bytes() == changed
        assert app.task_tag == "person/amy"
        assert "Alpha review" not in _matches(app)


@pytest.mark.asyncio
async def test_clear_tags_does_not_remove_project_or_children(tag_vault):
    child_before = _task(tag_vault, "Child Amy").tags
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("1")
        await _select(app, pilot, "Alpha review")
        await pilot.press("g")
        app.screen.query_one("#task-tags", Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        task = _task(tag_vault, "Alpha review")
        assert task.plain_tags == () and task.project == "Alpha"
        assert task.tags == ("project/Alpha",)
        assert _task(tag_vault, "Child Amy").tags == child_before


@pytest.mark.asyncio
async def test_tag_validation_and_storage_failure_preserve_editor_until_retry(tag_vault, monkeypatch):
    path = tag_vault / "Projects/Alpha.md"
    before = path.read_bytes()
    original = tm.set_task_tags
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("Test write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(tm, "set_task_tags", fail_once)
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("1")
        await _select(app, pilot, "Alpha review")
        await pilot.press("g")
        dialog = app.screen
        field = dialog.query_one("#task-tags", Input)
        field.value = "project/Forbidden"
        await pilot.press("enter")
        assert app.screen is dialog and attempts == []
        assert field.value == "project/Forbidden"
        assert path.read_bytes() == before and not app.history.can_undo
        field.value = "person/bob, follow-up"
        await pilot.press("enter")
        assert app.screen is dialog and len(attempts) == 1
        assert field.value == "person/bob, follow-up"
        assert "Test write failure" in str(dialog.query_one("#form-error", Label).render())
        assert path.read_bytes() == before and not app.history.can_undo
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, appmod.TaskTagsScreen)
        assert len(attempts) == 2
        assert _task(tag_vault, "Alpha review").plain_tags == ("person/bob", "follow-up")
        assert app.history.can_undo


@pytest.mark.asyncio
async def test_exact_tag_filter_crosses_projects_and_counts_only_real_matches(tag_vault):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("1")
        await _filter(app, pilot)
        assert _matches(app) == {"Alpha review", "Beta review", "Child Amy"}
        context = [row.task.description for row in _rows(app) if row.task.context]
        assert context == ["Untagged parent"]
        assert "Alpha near match" not in {row.task.description for row in _rows(app)}
        assert "3 tasks" in str(app._status_info)
        assert "person/amy" in str(app._status_info)
        # Following one person through projects retains the filter.
        app._sidebar_pick("proj:Alpha")
        await pilot.pause()
        assert app.task_tag == "person/amy"
        assert _matches(app) == {"Alpha review", "Child Amy"}
        app._sidebar_pick("proj:Beta")
        await pilot.pause()
        assert _matches(app) == {"Beta review"}
        await pilot.press("1")
        assert app.task_tag == "person/amy"
        assert _matches(app) == {"Alpha review", "Beta review", "Child Amy"}


@pytest.mark.asyncio
async def test_filter_picker_includes_other_project_tags_and_can_search_and_clear(tag_vault):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(100, 28)) as pilot:
        app._sidebar_pick("proj:Beta")
        app.action_focus_tasks()
        await pilot.press("ctrl+g")
        picker = app.screen
        assert isinstance(picker, CommandScreen)
        ids = {command.id for command in picker.commands}
        assert {"tag:all", "tag:value:person/amy", "tag:value:person/amylia"} <= ids
        assert not any(command.id.startswith("tag:value:project/") for command in picker.commands)
        picker.query_one("#command-input", Input).value = "person/amy"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.task_tag == "person/amy"
        assert _matches(app) == {"Beta review"}
        await _filter(app, pilot, "")
        assert app.task_tag == "" and _matches(app) == {"Beta review", "Beta other"}


@pytest.mark.asyncio
async def test_a_tag_named_all_is_distinct_from_clearing_the_filter(tag_vault):
    tm.set_task_tags(tag_vault, _task(tag_vault, "Capture target"), "all")
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("1")
        await _filter(app, pilot, "all")
        assert app.task_tag == "all" and _matches(app) == {"Capture target"}
        await _filter(app, pilot, "")
        assert app.task_tag == "" and "Alpha review" in _matches(app)


@pytest.mark.asyncio
async def test_search_and_filter_combine_and_escape_clears_in_stages(tag_vault):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press("1")
        await _filter(app, pilot)
        await pilot.press("/")
        search = app.query_one("#search", Input)
        search.value = "review"
        await pilot.pause()
        assert _matches(app) == {"Alpha review", "Beta review"}
        # Ctrl+G works while Find owns focus without modifying its query.
        await _filter(app, pilot, "person/bob")
        assert search.value == "review" and app.search_query == "review"
        assert _matches(app) == set()
        assert app.query_one(appmod.TaskList).empty_message
        await _filter(app, pilot)
        await pilot.press("i")
        assert app.query_one(appmod.Inspector).display
        await pilot.press("escape")
        await pilot.pause()
        assert app.search_query == "" and app.task_tag == "person/amy"
        assert app.query_one(appmod.Inspector).display
        await pilot.press("escape")
        await pilot.pause()
        assert app.task_tag == ""
        assert app.query_one(appmod.Inspector).display
        await pilot.press("escape")
        assert not app.query_one(appmod.Inspector).display


@pytest.mark.asyncio
async def test_palette_tag_editor_targets_inspector_child(tag_vault):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(130, 32)) as pilot:
        await pilot.press("1")
        await _select(app, pilot, "Untagged parent")
        await pilot.press("right", "tab")
        await pilot.pause()
        children = app.query_one("#ins-kids", OptionList)
        assert children.has_focus
        assert app._selected().description == "Child Amy"
        await pilot.press("ctrl+k")
        assert isinstance(app.screen, CommandScreen)
        assert "task_tags" in {command.id for command in app.screen.commands}
        app.screen.dismiss("task_tags")
        await pilot.pause()
        assert isinstance(app.screen, appmod.TaskTagsScreen)
        field = app.screen.query_one("#task-tags", Input)
        assert "person/amy" in field.value
        field.value = "person/bob, delegation"
        await pilot.press("enter")
        await pilot.pause()
        assert _task(tag_vault, "Child Amy").plain_tags == ("person/bob", "delegation")
        assert _task(tag_vault, "Untagged parent").plain_tags == ()
        assert _task(tag_vault, "Child Bob").plain_tags == ("person/bob",)


@pytest.mark.asyncio
async def test_notes_and_modal_text_keep_their_existing_shortcuts(tag_vault):
    NotesStore(tag_vault).create("Reference", "Keep this note", tags=("reference",))
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("1", "a")
        dialog = app.screen
        await pilot.press("g", "ctrl+g")
        assert app.screen is dialog
        assert dialog.query_one("#text", Input).value == "g"
        assert app.task_tag == ""
        await pilot.press("escape", "8")
        assert app.view == "notes"
        note_before = app.query_one(NotesWorkspace).current
        base = app.screen
        await pilot.press("g", "ctrl+g")
        assert app.screen is base and not isinstance(app.screen, ModalScreen)
        assert app.query_one(NotesWorkspace).current == note_before
        await pilot.press("t")
        assert isinstance(app.screen, CommandScreen)
        assert any(command.title == "reference" for command in app.screen.commands)
        assert not any(command.id.startswith("tag:") for command in app.screen.commands)
        await pilot.press("escape", "ctrl+k")
        assert not {"task_tags", "filter_task_tag"} & {command.id for command in app.screen.commands}


@pytest.mark.asyncio
async def test_opening_a_linked_task_reveals_it_when_a_person_filter_would_hide_it(tag_vault):
    task = tm.ensure_task_anchor(tag_vault, _task(tag_vault, "Beta other"))
    NotesStore(tag_vault).create("Linked reference", "Context for Bob's task",
                                tasks=(TaskLink(task.anchor, task.description),))
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("1")
        await _filter(app, pilot)
        await pilot.press("8", "k")
        assert isinstance(app.screen, CommandScreen)
        app.screen.dismiss("0")
        await pilot.pause()
        assert app.view == "all" and app.task_tag == ""
        assert app.query_one(appmod.TaskList).current.description == "Beta other"


@pytest.mark.asyncio
@pytest.mark.parametrize("new_tag,expected_filter", [("", ""), ("person/amy", "person/amy")])
async def test_new_task_is_revealed_without_discarding_compatible_tag_filter(tag_vault, new_tag, expected_filter):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.press("1")
        await _filter(app, pilot)
        await pilot.press("a")
        app.screen.query_one("#text", Input).value = "Fresh capture"
        app.screen.query_one("#tags", Input).value = new_tag
        await pilot.press("enter")
        await pilot.pause()
        assert app.task_tag == expected_filter
        assert app.query_one(appmod.TaskList).current.description == "Fresh capture"


@pytest.mark.asyncio
async def test_switching_vault_clears_old_task_tag_filter(tag_vault, tmp_path):
    other = tmp_path / "Other vault"
    (other / "Tasks").mkdir(parents=True)
    (other / "Tasks/Inbox.md").write_text("- [ ] Other vault work\n", encoding="utf-8")
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.press("1")
        await _filter(app, pilot)
        await pilot.press("ctrl+o")
        assert isinstance(app.screen, VaultScreen)
        app.screen.dismiss(VaultChoice(other, initialize=False))
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.vault == other and app.task_tag == ""
        await pilot.press("1")
        assert _matches(app) == {"Other vault work"}


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(40, 16), (60, 18), (80, 24)])
async def test_tags_editor_and_picker_keep_controls_visible_in_small_terminal(tag_vault, size):
    app = appmod.TaskApp(tag_vault)
    async with app.run_test(size=size) as pilot:
        await pilot.press("1")
        await _select(app, pilot, "Alpha review")
        await pilot.press("g")
        await pilot.pause()
        assert isinstance(app.screen, appmod.TaskTagsScreen)
        for selector in ("#task-tags", "#ok", "#cancel"):
            widget = app.screen.query_one(selector)
            assert widget.region.width > 0 and widget.region.height > 0
            assert widget.region.x >= 0 and widget.region.right <= size[0]
            assert widget.region.y >= 0 and widget.region.bottom <= size[1]
        field = app.screen.query_one("#task-tags", Input)
        field.value = "person/amy"
        await pilot.click(app.screen.query_one("#ok"))
        await pilot.pause()
        assert _task(tag_vault, "Alpha review").plain_tags == ("person/amy",)
        assert app.query_one(ShortcutBar).size.height <= 2
        await _filter(app, pilot)
        assert _matches(app) == {"Alpha review", "Beta review", "Child Amy"}
