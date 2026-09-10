"""Project dialogs validate names and locations before they dismiss."""

import asyncio
import os

import pytest
from textual.app import App
from textual.widgets import Input, Label

from tui.app import AddScreen, ProjectScreen, TaskApp, validate_project_name


@pytest.mark.parametrize("raw", ["", ".", "...", "#project/", "Work/../Outside",
                                      "../Outside", "/Outside", "C:/Outside", "C:Outside",
                                      "\\\\server\\share", "Work\\..\\Outside", "Work//Alpha",
                                      "CON", "con.txt", "Work/NUL", "Work/LPT1", "COM¹", "CONIN$",
                                      "Work./Alpha", "Work/.. /Outside", "Alpha:stream"])
def test_invalid_project_names_are_rejected(raw):
    with pytest.raises(ValueError):
        validate_project_name(raw)


@pytest.mark.parametrize(("raw", "expected"), [
    ("Work/Alpha", "Work/Alpha"), ("Q4 Plan (v2)", "Q4-Plan-v2"),
    ("#project/Work/Planning", "Work/Planning"), ("  Résumé/Q4 Plan  ", "Résumé/Q4-Plan"),
    ("COM10", "COM10"), ("Planning.v2", "Planning.v2"),
])
def test_ordinary_and_nested_names_are_cleaned(raw, expected):
    assert validate_project_name(raw) == expected


def test_preflight_reads_only_project_and_never_changes_it(tmp_path):
    target = tmp_path / "Projects" / "Work" / "Alpha.md"
    target.parent.mkdir(parents=True)
    original = "# Alpha\r\nRésumé 📝\r\n".encode("utf-8")
    target.write_bytes(original)
    assert validate_project_name("Work/Alpha", tmp_path) == "Work/Alpha"
    assert target.read_bytes() == original
    assert validate_project_name("New/Project", tmp_path) == "New/Project"
    assert not (tmp_path / "Projects" / "New").exists()
    (tmp_path / "Projects" / "Directory.md").mkdir()
    with pytest.raises(ValueError, match="location"):
        validate_project_name("Directory", tmp_path)


class DialogHost(App):
    CSS = TaskApp.CSS


def test_add_validation_preserves_input_and_accepts_correction(tmp_path):
    async def run():
        host = DialogHost()
        host.vault = tmp_path
        result = []
        async with host.run_test(size=(110, 36)) as pilot:
            screen = AddScreen([])
            host.push_screen(screen, result.append)
            await pilot.pause()
            screen.query_one("#text", Input).value = "Keep my task text"
            project = screen.query_one("#proj", Input)
            project.value = "Work/../../Outside"
            project.focus()
            await pilot.press("enter")
            assert host.screen is screen and result == []
            assert project.value == "Work/../../Outside" and project.has_focus
            assert screen.query_one("#text", Input).value == "Keep my task text"
            assert "relative" in str(screen.query_one("#form-error", Label).render())
            project.value = "Work/Q4 Plan"
            await pilot.press("enter")
            assert result == [{"text": "Keep my task text", "project": "Work/Q4-Plan"}]
            assert not (tmp_path / "Projects").exists()
    asyncio.run(run())


def test_add_blank_project_means_inbox_for_standalone_host():
    async def run():
        host = DialogHost()
        result = []
        async with host.run_test() as pilot:
            screen = AddScreen([])
            host.push_screen(screen, result.append)
            await pilot.pause()
            screen.query_one("#text", Input).value = "Inbox task"
            await pilot.press("enter")
            assert result == [{"text": "Inbox task", "project": ""}]
    asyncio.run(run())


def test_project_invalid_query_cannot_accidentally_remove_current_project(tmp_path):
    async def run():
        host = DialogHost()
        host.vault = tmp_path
        result = []
        async with host.run_test(size=(110, 36)) as pilot:
            screen = ProjectScreen(["Alpha"], "Alpha", "Task")
            host.push_screen(screen, result.append)
            await pilot.pause()
            field = screen.query_one("#filter", Input)
            for invalid in ("...", "../Outside", "NUL"):
                field.value = invalid
                await pilot.press("enter")
                assert host.screen is screen and result == []
                assert field.value == invalid and field.has_focus
                assert str(screen.query_one("#form-error", Label).render())
            field.value = "Work/Q4 Plan"
            await pilot.press("enter")
            assert result == ["Work/Q4-Plan"]
    asyncio.run(run())


def test_project_empty_submission_and_explicit_removal():
    async def run():
        host = DialogHost()
        result = []
        async with host.run_test(size=(110, 36)) as pilot:
            screen = ProjectScreen([], "", "Task")
            host.push_screen(screen, result.append)
            await pilot.pause()
            await pilot.press("enter")
            assert host.screen is screen and result == []
            assert str(screen.query_one("#form-error", Label).render())
            await pilot.press("escape")
            assert result == [None]
            screen = ProjectScreen([], "Alpha", "Task")
            host.push_screen(screen, result.append)
            await pilot.pause()
            await pilot.press("enter")
            assert result == [None, ""]
    asyncio.run(run())


@pytest.mark.parametrize(("query", "project"), [("con", "Connect"),
                                                ("Q4 Plan", "Q4-Plan"),
                                                ("#project/Work/Alpha", "Work/Alpha")])
def test_existing_project_search_still_accepts_clean_names_and_prefixes(query, project):
    async def run():
        host = DialogHost()
        result = []
        async with host.run_test(size=(110, 36)) as pilot:
            screen = ProjectScreen([project], "", "Task")
            host.push_screen(screen, result.append)
            await pilot.pause()
            screen.query_one("#filter", Input).value = query
            await pilot.press("enter")
            assert result == [project]
    asyncio.run(run())


@pytest.mark.skipif(os.name != "nt", reason="Windows junction safety")
def test_linked_project_locations_are_refused_inline(tmp_path):
    import _winapi

    vault, outside = tmp_path / "vault", tmp_path / "outside"
    (vault / "Projects").mkdir(parents=True)
    outside.mkdir()
    outside_file = outside / "Alpha.md"
    outside_file.write_bytes(b"external file")
    linked = vault / "Projects" / "Linked"
    _winapi.CreateJunction(str(outside), str(linked))
    try:
        with pytest.raises(ValueError, match="location"):
            validate_project_name("Linked/Alpha", vault)

        async def run():
            host = DialogHost()
            host.vault = vault
            result = []
            async with host.run_test(size=(110, 36)) as pilot:
                screen = ProjectScreen(["Linked/Alpha"], "", "Task")
                host.push_screen(screen, result.append)
                await pilot.pause()
                await pilot.press("enter")
                assert host.screen is screen and result == []
                assert screen.query_one("#filter", Input).has_focus
                assert "location" in str(screen.query_one("#form-error", Label).render())
        asyncio.run(run())
        assert outside_file.read_bytes() == b"external file"
    finally:
        linked.rmdir()
