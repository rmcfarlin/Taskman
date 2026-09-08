"""Ordinary task tags can be edited and filtered without changing projects."""

import datetime as dt
from dataclasses import replace

import pytest

from tui import taskman as tm
from tui.history import History


ANCHOR = "0123456789abcdef0123456789abcdef"
NEXT = "fedcba9876543210fedcba9876543210"


def task_file(root, text, name="Tasks/Inbox.md"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.mark.parametrize(("value", "expected"), [
    ("", ()),
    (" , \t\n", ()),
    ("#person/Ariann #waiting", ("person/Ariann", "waiting")),
    ("person/Ariann, waiting, #PERSON/ariann", ("person/Ariann", "waiting")),
    ("#José #JOSÉ staff/王 planning-v2 a_b.c", ("José", "staff/王", "planning-v2", "a_b.c")),
    (["#person/Ariann", "waiting, #PERSON/ariann"], ("person/Ariann", "waiting")),
])
def test_normalize_task_tags(value, expected):
    assert tm.normalize_task_tags(value) == expected


@pytest.mark.parametrize("value", ["#", "##person/Ariann", "person:ariann", "a;b", "@ariann",
                                  "tag!", "tag\\name", "tag[1]", "😀", ["valid", 123]])
def test_invalid_input_is_rejected_instead_of_truncated(value):
    with pytest.raises(ValueError, match="Invalid tag|Tags must be text"):
        tm.normalize_task_tags(value)


@pytest.mark.parametrize("value", ["project/Alpha", "#PROJECT/Alpha", "valid #project/Alpha"])
def test_project_assignment_is_not_accepted_as_an_ordinary_tag(value):
    with pytest.raises(ValueError, match="Use Project"):
        tm.normalize_task_tags(value)


def test_edit_tags_preserves_metadata_ids_subtasks_notes_and_unrelated_bytes(tmp_path):
    line = ("  2. [/] Review  **budget** #old 🛫 2026-09-01 08:30 #project/Alpha "
            "🔁 every week ⏳ 2026-09-07 13:00 ⏫ 📅 2026-09-10 16:45 #old-two "
            "✅ 2026-09-02 ❌ 2026-09-03 #PROJECT/Beta "
            f"<!-- taskman:id={ANCHOR} --> <!-- taskman:next={NEXT} --> ^budget")
    prefix = "# Project notes\r\n- [ ] Parent\r\n"
    suffix = "\r\n    Keep  note.\n    - [ ] Child #own\r\n\r\nUnrelated bytes"
    path = task_file(tmp_path, prefix + line + suffix)
    task = next(t for t in tm.load_all(tmp_path) if "budget" in t.description)
    result = tm.set_task_tags(tmp_path, task, "#person/Ariann, #waiting #PERSON/ariann")
    expected = line.replace("#old ", "#person/Ariann #waiting ").replace(" #old-two", "")
    assert path.read_bytes() == (prefix + expected + suffix).encode("utf-8")
    assert result is task
    assert task.plain_tags == ("person/Ariann", "waiting")
    assert task.tags == ("person/Ariann", "waiting", "project/Alpha", "PROJECT/Beta")
    assert task.anchor == ANCHOR and task.recurrence_next == NEXT
    assert task.indent == "  " and task.status == "/" and task.priority == 4
    assert task.parent_lineno == 2 and task.note == "Keep  note."


@pytest.mark.parametrize("ending", ["", f" <!-- taskman:id={ANCHOR} -->", " ^block-id",
                                    f" <!-- taskman:id={ANCHOR} --> ^block-id"])
def test_first_tags_are_inserted_before_trailing_ids(tmp_path, ending):
    path = task_file(tmp_path, "- [ ] A #project/Alpha" + ending)
    task = tm.load_all(tmp_path)[0]
    tm.set_task_tags(tmp_path, task, "person/Ann")
    assert path.read_text(encoding="utf-8") == "- [ ] A #project/Alpha #person/Ann" + ending
    assert task.plain_tags == ("person/Ann",)


def test_clear_removes_only_ordinary_tags_and_undo_redo_restores_exact_bytes(tmp_path):
    path = task_file(tmp_path, "- [ ] A #person/Ann #project/Alpha #waiting\r\n  Keep.\n")
    before = path.read_bytes()
    task = tm.load_all(tmp_path)[0]
    history = History(tmp_path)
    with history.record("Edit tags", [task.file]):
        tm.set_task_tags(tmp_path, task, "")
    after = path.read_bytes()
    assert after == b"- [ ] A #project/Alpha\r\n  Keep.\n"
    assert task.plain_tags == () and task.project == "Alpha"
    history.undo()
    assert path.read_bytes() == before
    history.redo()
    assert path.read_bytes() == after


def test_same_tags_do_not_rewrite_file_or_record_history(tmp_path, monkeypatch):
    path = task_file(tmp_path, "- [ ] A  #person/Ann   #project/Alpha #waiting\n")
    task = tm.load_all(tmp_path)[0]
    before = path.read_bytes()
    monkeypatch.setattr(tm.os, "replace", lambda *args: pytest.fail("Unchanged tags were rewritten"))
    history = History(tmp_path)
    with history.record("Edit tags", [task.file]):
        tm.set_task_tags(tmp_path, task, task.plain_tags)
    assert path.read_bytes() == before and not history.can_undo


@pytest.mark.parametrize("value", ["valid #oops!", "#project/Changed"])
def test_invalid_tags_never_mutate_task_file_or_handle(tmp_path, value):
    path = task_file(tmp_path, "- [ ] A #old #project/Alpha\n")
    task = tm.load_all(tmp_path)[0]
    snapshot = replace(task)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        tm.set_task_tags(tmp_path, task, value)
    assert path.read_bytes() == before and task == snapshot
    assert not (tmp_path / ".taskman").exists()


def test_edit_follows_stable_id_after_move_but_rejects_external_edits(tmp_path):
    path = task_file(tmp_path, f"- [ ] A #old <!-- taskman:id={ANCHOR} -->\n")
    task = tm.load_all(tmp_path)[0]
    moved = tmp_path / "Moved.md"
    path.rename(moved)
    moved.write_bytes(b"# Heading\n" + moved.read_bytes())
    tm.set_task_tags(tmp_path, task, "person/Ann")
    assert task.file == "Moved.md" and task.lineno == 2
    moved.write_bytes(moved.read_bytes().replace(b"person/Ann", b"person/External"))
    snapshot = replace(task)
    before = moved.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        tm.set_task_tags(tmp_path, task, "person/Other")
    assert moved.read_bytes() == before and task == snapshot


def test_filter_tag_is_exact_case_insensitive_and_combines_with_project_query_view(tmp_path):
    task_file(tmp_path, "- [ ] Alpha budget #person/Ann #urgent\n"
              "- [ ] Alpha planning #person/Anna\n"
              "- [x] Alpha done #person/Ann\n", "Projects/Alpha.md")
    task_file(tmp_path, "- [ ] Beta budget #PERSON/ann 📅 2026-09-07\n"
              "- [ ] Beta no person\n", "Projects/Beta.md")
    tasks = tm.load_all(tmp_path)
    descriptions = lambda **kw: [t.description for t in tm.view_tasks(tasks, **kw)]
    assert set(descriptions(view="all", tag="#PERSON/Ann")) == {"Alpha budget", "Beta budget"}
    assert descriptions(view="project", project="Alpha", tag="person/ann") == ["Alpha budget", "Alpha done"]
    assert descriptions(view="all", tag="person/Ann", query="beta") == ["Beta budget"]
    assert descriptions(view="completed", tag="person/Ann") == ["Alpha done"]
    assert descriptions(view="overdue", tag="person/Ann", day=dt.date(2026, 9, 8)) == ["Beta budget"]
    assert descriptions(view="all", tag="Ann") == []
    assert descriptions(view="all", tag="person") == []
    assert descriptions(view="all", tag="Alpha") == []
    assert descriptions(view="all", tag="project/Alpha") == []


def test_filtered_children_keep_ancestors_as_context_and_counts_exclude_context(tmp_path):
    task_file(tmp_path, "- [ ] Parent #person/Other\n"
              "  - [ ] Matched child #person/Ann\n"
              "  - [ ] Other child #person/Other\n", "Projects/Alpha.md")
    task_file(tmp_path, "- [ ] Independent #person/Ann\n", "Projects/Beta.md")
    tasks = tm.load_all(tmp_path)
    filtered = tm.view_tasks(tasks, "all", tag="person/ann")
    assert {t.description: t.context for t in filtered} == {
        "Parent": True, "Matched child": False, "Independent": False,
    }
    assert sum(not t.context for t in filtered) == 2
    assert len(tm.view_tasks(tasks, "all", tag="person/ann", with_parents=False)) == 2
    assert all(not t.context for t in tm.view_tasks(tasks, "all"))


def test_hashtag_query_terms_are_exact_and_combine_with_each_other_text_and_tag(tmp_path):
    task_file(tmp_path, "- [ ] Review budget #person/Ann #waiting\n"
              "- [ ] Review planning #person/Ann\n"
              "- [ ] Review budget #person/Anna #waiting\n"
              "- [ ] Other budget #person/Ann #waiting\n")
    tasks = tm.load_all(tmp_path)
    rows = lambda query, **kw: tm.view_tasks(tasks, "all", query=query, **kw)
    matched = rows("#PERSON/ANN #WAITING review")
    assert len(matched) == 1 and matched[0].description == "Review budget"
    assert matched[0].plain_tags == ("person/Ann", "waiting")
    assert len(rows("#person/Ann")) == 3
    assert len(rows("#person/ann", tag="waiting")) == 2
    assert rows("#ann") == []
    assert rows("#person/Ann", tag="person/Anna") == []
    assert len(rows("person/Ann")) == 4  # Plain text retains substring search.
    assert len(rows("review budget")) == 2


def test_hashtag_query_does_not_match_project_or_inferred_file_tags(tmp_path):
    task_file(tmp_path, "- [ ] Project task #project/Alpha\n", "Projects/Alpha.md")
    tasks = tm.load_all(tmp_path)
    assert tm.view_tasks(tasks, "all", query="#project/Alpha") == []
    assert tm.view_tasks(tasks, "all", query="#Alpha") == []


@pytest.mark.parametrize("literal", [
    "[section](#budget)",
    '[section](Other.md?find=#budget "#title")',
    '[section](Other.md "A closing ) and #title")',
    '[section](<Other).md?find=#budget> "#title")',
    "[section](other_(v2).md?find=#budget)",
    r"[section](other\).md?find=#budget)",
    "![diagram](#budget)",
    "https://example.com/?find=#budget",
    "www.example.com/?find=#budget",
    "WWW.example.com/?find=#budget",
    "<https://example.com/?find=#budget>",
    '<a href="#budget">section</a>',
    "`#budget`",
    "``some `code` #budget``",
    r"\#budget",
    "[[#Budget]]",
    "[[#Budget|label]]",
    "![[#section]]",
    "[[Other note#Heading]]",
    "[guide][#budget]",
    "[#caption][budget]",
    "[#caption][#budget]",
    "[#budget][]",
    "[#budget]",
    "![#caption][#budget]",
    "![#budget][]",
    "[#caption](Notes/Budget.md)",
    "![#caption](Notes/Budget.png)",
    "[Cost [#nested] totals](Notes/Budget.md)",
    r"[Cost \] #caption](Notes/Budget.md)",
    r"[Cost \[ #caption](Notes/Budget.md)",
    r"[Cost \\ #caption](Notes/Budget.md)",
    r"[guide][#budget\]]",
    "[#caption `]`](Notes/Budget.md)",
    "[#caption `[`](Notes/Budget.md)",
    "[#caption https://example.com](Notes/Budget.md)",
])
def test_literal_markdown_hashes_survive_parse_add_and_tag_edit(tmp_path, literal):
    text = f"Read {literal} #project/Alpha"
    parsed = tm.parse_task_line(f"- [ ] {text}")
    assert parsed.tags == ("project/Alpha",)
    assert parsed.description == f"Read {literal}"
    task = tm.add_task(tmp_path, text)
    path = tmp_path / task.file
    assert literal in task.raw and task.plain_tags == ()
    tm.set_task_tags(tmp_path, task, "person/Ann, review")
    assert literal in path.read_text(encoding="utf-8")
    assert task.description == f"Read {literal}"
    assert task.plain_tags == ("person/Ann", "review")
    tm.set_task_tags(tmp_path, task, "")
    assert literal in path.read_text(encoding="utf-8")
    assert task.description == f"Read {literal}" and task.plain_tags == ()


def test_inline_code_and_fragment_links_are_not_replaced_with_existing_tags(tmp_path):
    line = "- [ ] Read [section](#budget) and `#sample` #old #project/Alpha\n"
    path = task_file(tmp_path, line)
    task = tm.load_all(tmp_path)[0]
    assert task.plain_tags == ("old",)
    tm.set_task_tags(tmp_path, task, "person/Ann")
    assert path.read_text(encoding="utf-8") == line.replace("#old", "#person/Ann")
    assert tm.view_tasks([task], "all", query="#budget") == []
    assert tm.view_tasks([task], "all", query="#person/Ann") == [task]


def test_wikilink_destinations_and_aliases_are_not_replaced_with_existing_tags(tmp_path):
    line = "- [ ] Read [[#Budget|#Label]] and ![[#section]] #old #project/Alpha\n"
    path = task_file(tmp_path, line)
    task = tm.load_all(tmp_path)[0]
    assert task.plain_tags == ("old",)
    tm.set_task_tags(tmp_path, task, "person/Ann")
    assert path.read_text(encoding="utf-8") == line.replace("#old", "#person/Ann")
    assert tm.view_tasks([task], "all", query="#Budget") == []
    assert tm.view_tasks([task], "all", query="#person/Ann") == [task]


def test_reference_link_ids_definitions_and_captions_survive_tag_edits_and_history(tmp_path):
    line = ("- [ ] Read [guide][#budget], [#budget][], and [#budget] "
            "#old #project/Alpha\r\n")
    definitions = '\r\n[#budget]: Notes/Budget.md "Planning"\n'
    path = task_file(tmp_path, line + definitions)
    before = path.read_bytes()
    task = tm.load_all(tmp_path)[0]
    assert task.plain_tags == ("old",)
    assert task.description == "Read [guide][#budget], [#budget][], and [#budget]"
    history = History(tmp_path)
    with history.record("Edit task tags", [task.file]):
        tm.set_task_tags(tmp_path, task, "person/Amy, follow-up")
    after = path.read_bytes()
    assert after == (line.replace("#old", "#person/Amy #follow-up") + definitions).encode("utf-8")
    assert task.plain_tags == ("person/Amy", "follow-up")
    assert tm.view_tasks([task], "all", query="#budget") == []
    assert tm.view_tasks([task], "all", query="#person/amy") == [task]
    history.undo()
    assert path.read_bytes() == before
    history.redo()
    assert path.read_bytes() == after


@pytest.mark.parametrize("literal", [
    "[Cost [#nested] totals](Notes/Budget.md)",
    r"[Cost \] #caption](Notes/Budget.md)",
    "[#caption `]`](Notes/Budget.md)",
    "[#caption https://example.com](Notes/Budget.md)",
    "[guide][#budget]",
])
def test_link_labels_do_not_absorb_adjacent_ordinary_tags(tmp_path, literal):
    line = f"- [ ] #before Read {literal} #after #project/Alpha\n"
    path = task_file(tmp_path, line)
    task = tm.load_all(tmp_path)[0]
    assert task.plain_tags == ("before", "after")
    tm.set_task_tags(tmp_path, task, "person/Amy")
    assert path.read_text(encoding="utf-8") == line.replace("#before", "#person/Amy").replace(" #after", "")
    assert task.description == f"Read {literal}"


def test_escaped_opening_bracket_does_not_create_a_link_label(tmp_path):
    line = r"- [ ] Read \[plain #ordinary] and keep #after" + "\n"
    path = task_file(tmp_path, line)
    task = tm.load_all(tmp_path)[0]
    assert task.plain_tags == ("ordinary", "after")
    tm.set_task_tags(tmp_path, task, "person/Amy")
    assert path.read_text(encoding="utf-8") == line.replace("#ordinary", "#person/Amy").replace(" #after", "")
