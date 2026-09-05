"""Metadata and comment checkboxes never become actionable vault tasks."""
import pytest

from tui import taskman as tm


ANCHOR = "0123456789abcdef0123456789abcdef"
MARKER = f"<!-- taskman:id={ANCHOR} -->"


def _write(root, text):
    path = root / "Notes" / "Reference.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("opening,closing", [("---", "---"), ("---", "..."), ("+++", "+++")])
@pytest.mark.parametrize("bom", ["", "\ufeff"])
def test_frontmatter_checkboxes_cannot_resolve_or_target_task_actions(tmp_path, opening, closing, bom):
    header = f"{bom}{opening}\nexample:\n- [ ] Metadata {MARKER}\n{closing}\n\n"
    path = _write(tmp_path, header + f"- [ ] Actual {MARKER}\n  Keep this note.\n")
    tasks = tm.load_all(tmp_path)
    assert [(t.description, t.lineno) for t in tasks] == [("Actual", 6)]
    task = tm.find_task_by_anchor(tasks, ANCHOR)
    assert task.id == "Notes/Reference.md:6"
    assert task.note == "Keep this note." and task.note_end == 7

    tm.set_status(tmp_path, task, "/")
    assert path.read_text(encoding="utf-8") == header + f"- [/] Actual {MARKER}\n  Keep this note.\n"


@pytest.mark.parametrize("opening", ["---", "+++"])
def test_unclosed_initial_frontmatter_does_not_expose_metadata_tasks(tmp_path, opening):
    path = _write(tmp_path, f"{opening}\n- [ ] Metadata\n")
    before = path.read_bytes()
    assert tm.load_all(tmp_path) == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("opening", ["<!-- Example", '<!-- taskman-note: {"version": 1,'])
@pytest.mark.parametrize("closed", [False, True])
@pytest.mark.parametrize("at_start", [False, True])
def test_comment_checkboxes_are_hidden_even_with_malformed_metadata(tmp_path, opening, closed, at_start):
    comment = f"{opening}\n- [ ] Metadata taskman:id={ANCHOR}\n- [ ] Still hidden\n"
    suffix = "-->\n- [ ] After comment\n" if closed else ""
    prefix = "\ufeff" if at_start else "- [ ] Before comment\n"
    path = _write(tmp_path, prefix + comment + suffix)
    before = path.read_bytes()
    tasks = tm.load_all(tmp_path)
    expected = [] if at_start else [("Before comment", 1)]
    if closed:
        expected.append(("After comment", 5 if at_start else 6))
    assert [(t.description, t.lineno) for t in tasks] == expected
    assert tm.find_task_by_anchor(tasks, ANCHOR) is None
    hidden_lineno = 2 if at_start else 3
    fake = tm.parse_task_line(path.read_text(encoding="utf-8").splitlines()[hidden_lineno - 1],
                              "Notes/Reference.md", hidden_lineno)
    with pytest.raises(ValueError, match="changed"):
        tm.ensure_task_anchor(tmp_path, fake)
    assert path.read_bytes() == before


def test_indented_comments_remain_owned_note_text_without_creating_child_tasks(tmp_path):
    note = "  First paragraph.\n  <!-- Example\n  - [ ] Hidden example\n  -->\n  Last paragraph.\n"
    path = _write(tmp_path, "- [ ] Parent\n" + note + "  - [ ] Real child\n    Child note.\n")
    parent, child = tm.load_all(tmp_path)
    assert parent.note == "First paragraph.\n<!-- Example\n- [ ] Hidden example\n-->\nLast paragraph."
    assert parent.note_end == 6
    assert (child.lineno, child.parent_lineno, child.depth) == (7, 1, 1)
    assert child.note == "Child note." and child.note_end == 8

    anchored = tm.ensure_task_anchor(tmp_path, parent)
    assert anchored.note == parent.note and anchored.note_end == parent.note_end
    tm.set_status(tmp_path, anchored, "/")
    assert path.read_text(encoding="utf-8").splitlines(keepends=True)[1:] == (
        note + "  - [ ] Real child\n    Child note.\n").splitlines(keepends=True)


def test_comment_fences_do_not_hide_body_tasks_and_code_comments_do_not_open_blocks(tmp_path):
    path = _write(tmp_path,
                  "<!--\n```\n- [ ] Comment example\n-->\n"
                  "```markdown\n<!-- Unclosed code example\n- [ ] Code example\n```\n"
                  f"- [ ] Actual {MARKER}\n  Normal task note.\n")
    tasks = tm.parse_file(path, tmp_path)
    assert len(tasks) == 1 and tasks[0].lineno == 9
    assert tasks[0].description == "Actual" and tasks[0].anchor == ANCHOR
    assert tasks[0].note == "Normal task note." and tasks[0].note_end == 10


def test_body_rules_are_not_mistaken_for_initial_frontmatter(tmp_path):
    path = _write(tmp_path, "# Reference\n---\n- [ ] First\n+++\n- [ ] Second\n")
    assert [(t.description, t.lineno) for t in tm.parse_file(path, tmp_path)] == [
        ("First", 3), ("Second", 5)]
