"""Tests for taskman core (stdlib only — no Textual needed). Run: pytest tui/"""

import datetime as dt
from pathlib import Path

try:
    from tui import taskman as tm
    from tui.taskman import Task
except ImportError:  # running from inside tui/
    import taskman as tm
    from taskman import Task


def _vault(tmp_path: Path) -> Path:
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Projects").mkdir()
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        "---\ntype: inbox\n---\n\n# Inbox\n\n- [ ] Undated thing #project/Alpha\n",
        encoding="utf-8",
    )
    (tmp_path / "Projects" / "Alpha.md").write_text(
        "---\ntype: project\nstatus: active\n---\n\n# Alpha\n\n## Tasks\n\n"
        "- [ ] Ship it 🔺 📅 2026-09-04 #project/Alpha\n"
        "- [ ] Later ⏬ 📅 2026-09-20\n"
        "- [x] Done ✅ 2026-09-01\n",
        encoding="utf-8",
    )
    return tmp_path


def test_parse_basic():
    t = tm.parse_task_line("- [ ] Hello #tag", file="a.md", lineno=1)
    assert t and t.description == "Hello" and t.tags == ("tag",) and t.open


def test_parse_full_syntax():
    t = tm.parse_task_line("- [ ] Ship it 🔺 📅 2026-09-04 🛫 2026-09-01 #project/Alpha")
    assert t and t.priority == 5 and t.priority_name == "highest"
    assert t.due == dt.date(2026, 9, 4) and t.start == dt.date(2026, 9, 1)
    assert t.description == "Ship it" and "project/Alpha" in t.tags


def test_parse_quickcapture_line():
    line = "- [x] Work on income statement mapping #important #project/Automation 🔼 🛫 2026-08-26 08:00 ⏳ 2026-08-27 08:00 📅 2026-08-31 08:00 ✅ 2026-08-27"
    t = tm.parse_task_line(line)
    assert t and t.done and t.priority == 3
    assert t.due == dt.date(2026, 8, 31) and t.done_date == dt.date(2026, 8, 27)
    assert "Work on income statement mapping" in t.description


def test_templates_not_scanned(tmp_path):
    root = _vault(tmp_path)
    (root / "Templates").mkdir()
    (root / "Templates" / "Project.md").write_text("# x\n\n## Tasks\n\n- [ ]\n")
    assert not any("Templates" in str(p) for p in tm.iter_markdown_files(root))


def test_views(tmp_path):
    root = _vault(tmp_path)
    tasks = tm.load_all(root)
    day = dt.date(2026, 9, 4)
    assert len(tm.view_tasks(tasks, "inbox", day)) == 1
    assert len(tm.view_tasks(tasks, "today", day)) == 1
    assert tm.view_tasks(tasks, "overdue", dt.date(2026, 9, 5))[0].description == "Ship it"
    assert len(tm.view_tasks(tasks, "next7", day)) == 1
    assert len(tm.view_tasks(tasks, "completed", day)) == 1
    assert len(tm.view_tasks(tasks, "priority", day)) == 1


def test_toggle_roundtrip(tmp_path):
    root = _vault(tmp_path)
    tasks = tm.load_all(root)
    open_task = next(t for t in tasks if t.description == "Undated thing")
    tm.toggle(root, open_task, dt.date(2026, 9, 4))
    again = tm.load_all(root)
    done = next(t for t in again if t.description == "Undated thing")
    assert done.done and done.done_date == dt.date(2026, 9, 4)


def test_add_to_project_and_inbox(tmp_path):
    root = _vault(tmp_path)
    t1 = tm.add_task(root, "Inbox item")
    t2 = tm.add_task(root, "Proj item 🔼", project="Alpha")
    assert (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8").count("Inbox item") == 1
    text = (root / "Projects" / "Alpha.md").read_text(encoding="utf-8")
    assert "Proj item" in text and "#project/Alpha" in text
    assert t1.open and t2.priority == 3


def test_delete_and_edit(tmp_path):
    root = _vault(tmp_path)
    tasks = tm.load_all(root)
    victim = next(t for t in tasks if t.description == "Later")
    tm.edit_text(root, victim, "Later renamed")
    assert "Later renamed" in (root / "Projects" / "Alpha.md").read_text(encoding="utf-8")
    tm.delete_task(root, victim)
    assert "Later renamed" not in (root / "Projects" / "Alpha.md").read_text(encoding="utf-8")


def test_build_line_readable():
    t = Task(file="x.md", lineno=1, status=" ", description="Do it",
             priority=4, due=dt.date(2026, 9, 5), tags=("a",))
    assert tm.build_line(t) == "- [ ] Do it ⏫ 📅 2026-09-05 #a"


def test_fenced_code_ignored(tmp_path):
    root = _vault(tmp_path)
    (root / "Notes" / "Doc.md").write_text(
        "# Doc\n\n```markdown\n- [ ] Not a real task 🔺 📅 2026-09-04\n```\n",
        encoding="utf-8",
    )
    tasks = tm.load_all(root)
    assert not any(t.file == "Notes/Doc.md" for t in tasks)


def _tree_vault(tmp_path):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Tasks" / "Inbox.md").write_text(
        "# Inbox\n\n"
        "- [ ] Parent 📅 2026-09-04\n"
        "  - [ ] Kid one\n"
        "  - [ ] Kid two 🔼\n"
        "    - [ ] Grandkid 📅 2026-09-04\n"
        "- [ ] Solo\n",
        encoding="utf-8",
    )
    return tmp_path


def _by_desc(tasks, desc):
    return next(t for t in tasks if t.description == desc)


def test_depth_and_parents(tmp_path):
    tasks = tm.load_all(_tree_vault(tmp_path))
    got = {t.description: (t.depth, t.parent_lineno) for t in tasks}
    assert got["Parent"] == (0, 0)
    assert got["Kid one"][0] == 1 and got["Kid two"][0] == 1
    parent_line = _by_desc(tasks, "Parent").lineno
    assert got["Kid one"][1] == parent_line == got["Kid two"][1]
    assert got["Grandkid"][0] == 2
    assert got["Grandkid"][1] == _by_desc(tasks, "Kid two").lineno
    assert got["Solo"] == (0, 0)
    kids = tm.children_of(tasks, _by_desc(tasks, "Parent"))
    assert [k.description for k in kids] == ["Kid one", "Kid two"]
    assert len(tm.descendants_of(tasks, _by_desc(tasks, "Parent"))) == 3


def test_ancestors_join_view_as_context(tmp_path):
    tasks = tm.load_all(_tree_vault(tmp_path))
    day = dt.date(2026, 9, 4)
    rows = tm.view_tasks(tasks, "today", day)
    descs = {t.description: t for t in rows}
    # Matches: Parent + Grandkid. Kid two joins as dimmed context.
    assert set(descs) == {"Parent", "Kid two", "Grandkid"}
    assert not descs["Parent"].context and not descs["Grandkid"].context
    assert descs["Kid two"].context
    assert "Kid one" not in descs
    assert tm.counts(tasks, day)["today"] == 2  # contexts never inflate counts


def test_add_subtask_nests_and_positions(tmp_path):
    root = _tree_vault(tmp_path)
    tasks = tm.load_all(root)
    kid = tm.add_subtask(root, _by_desc(tasks, "Kid one"), "New kid 🔽")
    assert kid.depth == 2 and kid.parent_lineno == _by_desc(tasks, "Kid one").lineno
    lines = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8").splitlines()
    assert kid.anchor
    assert tm.TASK_ANCHOR_RE.sub("", lines[kid.lineno - 1]).rstrip() == "    - [ ] New kid 🔽"
    # Inserted directly after Kid one (which had no children yet).
    assert lines[kid.lineno - 2] == "  - [ ] Kid one"


def test_indent_outdent_with_subtree(tmp_path):
    root = _tree_vault(tmp_path)
    (root / "Tasks" / "P.md").write_text(
        "- [ ] P1\n- [ ] P2\n  - [ ] C\n", encoding="utf-8")
    tasks = tm.load_all(root)
    p2 = _by_desc(tasks, "P2")
    assert tm.indent_task(root, p2) is not None
    lines = (root / "Tasks" / "P.md").read_text(encoding="utf-8").splitlines()
    assert lines == ["- [ ] P1", "  - [ ] P2", "    - [ ] C"], lines
    # First task has no previous sibling: no-op.
    assert tm.indent_task(root, _by_desc(tm.load_all(root), "P1")) is None
    # Outdent restores.
    tasks = tm.load_all(root)
    assert tm.outdent_task(root, _by_desc(tasks, "P2")) is not None
    lines = (root / "Tasks" / "P.md").read_text(encoding="utf-8").splitlines()
    assert lines == ["- [ ] P1", "- [ ] P2", "  - [ ] C"], lines
    # Already top-level: no-op.
    assert tm.outdent_task(root, _by_desc(tm.load_all(root), "P1")) is None


def test_toggle_cascades_branch(tmp_path):
    root = _tree_vault(tmp_path)
    tasks = tm.load_all(root)
    tm.toggle(root, _by_desc(tasks, "Parent"), dt.date(2026, 9, 4))
    again = tm.load_all(root)
    branch = [t for t in again if t.description in
              ("Parent", "Kid one", "Kid two", "Grandkid")]
    assert all(t.done and t.done_date == dt.date(2026, 9, 4) for t in branch)
    assert _by_desc(again, "Solo").open  # outside the branch: untouched
    tm.toggle(root, _by_desc(again, "Parent"), dt.date(2026, 9, 4))
    reopened = tm.load_all(root)
    assert all(_by_desc(reopened, d).open for d in
               ("Parent", "Kid one", "Kid two", "Grandkid"))


def test_delete_removes_branch(tmp_path):
    root = _tree_vault(tmp_path)
    tasks = tm.load_all(root)
    n = tm.delete_task(root, _by_desc(tasks, "Parent"))
    assert n == 4
    rest = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    assert "Parent" not in rest and "Grandkid" not in rest
    assert "- [ ] Solo" in rest


def test_short_shows_indent(tmp_path):
    tasks = tm.load_all(_tree_vault(tmp_path))
    assert _by_desc(tasks, "Grandkid").short().startswith("    [ ]")


def test_cancelled_date_survives_rewrite(tmp_path):
    root = _tree_vault(tmp_path)
    (root / "Tasks" / "C.md").write_text(
        "- [>] Fluids ❌ 2026-09-03 🛫 2026-09-03 #project/Ariann 🔼\n", encoding="utf-8")
    t = next(x for x in tm.load_all(root) if x.description == "Fluids")
    assert t.cancelled_date == dt.date(2026, 9, 3) and t.status == ">" and t.open
    assert t.status_label == "forwarded" and t.project == "Ariann"
    tm.edit_text(root, t, "Fluids (renamed)")
    line = (root / "Tasks" / "C.md").read_text(encoding="utf-8").strip()
    assert line == "- [>] Fluids (renamed) 🔼 🛫 2026-09-03 ❌ 2026-09-03 #project/Ariann"


def test_closed_states_and_completed_view():
    open_, prog, done, canc = (tm.parse_task_line(f"- [{c}] x") for c in (" ", "/", "x", "-"))
    assert open_.open and prog.open and not done.open and not canc.open
    assert canc.cancelled and canc.closed and not canc.done
    rows = tm.view_tasks([open_, prog, done, canc], "completed")
    assert {t.status for t in rows} == {"x", "-"}          # cancelled counts as finished
    assert len(tm.view_tasks([open_, prog, done, canc], "all")) == 2


def test_set_status_keeps_dates_truthful(tmp_path):
    root = _tree_vault(tmp_path)
    day = dt.date(2026, 9, 4)
    solo = _by_desc(tm.load_all(root), "Solo")
    tm.set_status(root, solo, "/", day)
    assert "- [/] Solo" in (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    tm.set_status(root, _by_desc(tm.load_all(root), "Solo"), "-", day)
    text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    assert "- [-] Solo ❌ 2026-09-04" in text
    tm.set_status(root, _by_desc(tm.load_all(root), "Solo"), "x", day)
    text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    assert "- [x] Solo ✅ 2026-09-04" in text and "❌" not in text
    tm.set_status(root, _by_desc(tm.load_all(root), "Solo"), " ", day)
    assert "- [ ] Solo\n" in (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")


def test_toggle_cancelled_task_completes_it(tmp_path):
    root = _tree_vault(tmp_path)
    (root / "Tasks" / "Inbox.md").write_text("- [-] Dropped ❌ 2026-09-01\n", encoding="utf-8")
    tm.toggle(root, _by_desc(tm.load_all(root), "Dropped"), dt.date(2026, 9, 4))
    assert (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8") == "- [x] Dropped ✅ 2026-09-04\n"


def test_clean_project_name():
    assert tm.clean_project_name("#project/Alpha") == "Alpha"
    assert tm.clean_project_name("  Q4 Plan (v2) ") == "Q4-Plan-v2"
    assert tm.clean_project_name("Work/Alpha") == "Work/Alpha"
    assert tm.clean_project_name("R&D") == "R-D"
    assert tm.clean_project_name("///") == ""
    # Round-trips through a tag: whatever we produce, the tag parser reads back whole.
    for raw in ("Q4 Plan", "Client.Alpha", "Work/Alpha"):
        name = tm.clean_project_name(raw)
        t = tm.parse_task_line(f"- [ ] x #project/{name}")
        assert t and t.project == name


def test_set_project_replaces_and_removes(tmp_path):
    root = _tree_vault(tmp_path)
    (root / "Tasks" / "Inbox.md").write_text(
        "- [ ] Thing #project/Old #keep 📅 2026-09-10\n", encoding="utf-8")
    t = _by_desc(tm.load_all(root), "Thing")
    tm.set_project(root, t, "New Thing")
    text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    assert text == "- [ ] Thing 📅 2026-09-10 #keep #project/New-Thing\n"
    t = _by_desc(tm.load_all(root), "Thing")
    assert t.project == "New-Thing" and t.plain_tags == ("keep",)
    tm.set_project(root, t, "")
    text = (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    assert text == "- [ ] Thing 📅 2026-09-10 #keep\n"
    assert _by_desc(tm.load_all(root), "Thing").project == ""


def test_project_from_file_stem_and_counts(tmp_path):
    root = _vault(tmp_path)
    tasks = tm.load_all(root)
    later = _by_desc(tasks, "Later")                  # lives in Projects/Alpha.md, no tag
    assert later.project == "Alpha"
    assert tm.project_names(tasks, root) == ["Alpha"]
    assert tm.project_counts(tasks) == {"alpha": 3}   # Ship it, Later, Undated thing (tagged)
    path, created = tm.ensure_project_file(root, "Beta")
    assert created and path == root / "Projects" / "Beta.md"
    assert "## Tasks" in path.read_text(encoding="utf-8")
    assert tm.ensure_project_file(root, "Beta") == (path, False)
    assert tm.project_names(tasks, root) == ["Alpha", "Beta"]


def test_add_task_joins_project_task_list(tmp_path):
    root = _vault(tmp_path)
    (root / "Projects" / "Alpha.md").write_text(
        "# Alpha\n\n## Tasks\n\n- [ ] First\n\n## Notes\n\nprose\n", encoding="utf-8")
    t = tm.add_task(root, "Second", project="Alpha")
    lines = (root / "Projects" / "Alpha.md").read_text(encoding="utf-8").splitlines()
    assert t.anchor
    assert lines[4] == "- [ ] First" and tm.TASK_ANCHOR_RE.sub("", lines[5]).rstrip() == "- [ ] Second #project/Alpha"
    assert lines[6] == "" and lines[7] == "## Notes"     # blank line before next heading kept
    assert t.lineno == 6
    # Empty section: a blank line is kept under the heading.
    (root / "Projects" / "Gamma.md").write_text("# Gamma\n\n## Tasks\n", encoding="utf-8")
    only = tm.add_task(root, "Only", project="Gamma")
    assert only.anchor
    assert (root / "Projects" / "Gamma.md").read_text(encoding="utf-8") == (
        f"# Gamma\n\n## Tasks\n\n- [ ] Only #project/Gamma <!-- taskman:id={only.anchor} -->\n")


def test_tree_rows_guides_and_order(tmp_path):
    tasks = tm.load_all(_tree_vault(tmp_path))
    trees = tm.tree_rows(tasks)
    by_root = {t[0].task.description: t for t in trees}
    parent = by_root["Parent"]
    assert [(n.task.description, n.depth, n.prefix) for n in parent] == [
        ("Parent", 0, ""), ("Kid one", 1, "├ "), ("Kid two", 1, "└ "),
        ("Grandkid", 2, "  └ "),
    ]
    assert [n.task.description for n in by_root["Solo"]] == ["Solo"]
    # A child whose parent is not displayed becomes a root of its own.
    kid_only = [t for t in tasks if t.description == "Grandkid"]
    assert tm.tree_rows(kid_only)[0][0].depth == 0


def test_sections_bucket_trees_by_most_urgent_match(tmp_path):
    tasks = tm.load_all(_tree_vault(tmp_path))
    day = dt.date(2026, 9, 4)
    secs = tm.sections(tm.view_tasks(tasks, "all", day), "all", day)
    assert [s.title for s in secs] == ["Today", "No due date"]
    today = secs[0]
    assert [n.task.description for n in today.nodes] == ["Parent", "Kid one", "Kid two", "Grandkid"]
    assert today.count == 4 and secs[1].count == 1
    # Today view: only Parent + Grandkid match; Kid two rides along as context
    # and is not counted, but the whole tree sits under "Today".
    rows = tm.view_tasks(tasks, "today", day)
    secs = tm.sections(rows, "today", day)
    assert len(secs) == 1 and secs[0].key == "today" and secs[0].count == 2
    assert [n.task.description for n in secs[0].nodes] == ["Parent", "Kid two", "Grandkid"]
    assert secs[0].nodes[1].task.context and secs[0].nodes[1].prefix == "└ "


def test_sections_completed_by_done_date(tmp_path):
    root = _tree_vault(tmp_path)
    (root / "Tasks" / "Inbox.md").write_text(
        "- [x] Fresh ✅ 2026-09-04\n- [x] Old ✅ 2026-08-01\n- [-] Dropped ❌ 2026-09-03\n"
        "- [x] Undated\n", encoding="utf-8")
    day = dt.date(2026, 9, 4)
    tasks = tm.load_all(root)
    secs = tm.sections(tm.view_tasks(tasks, "completed", day), "completed", day)
    assert [(s.title, [n.task.description for n in s.nodes]) for s in secs] == [
        ("Completed today", ["Fresh"]), ("Yesterday", ["Dropped"]),
        ("Older", ["Old"]), ("No completion date", ["Undated"]),
    ]


def test_plain_output_marks_overdue_and_sections(tmp_path, capsys):
    root = _tree_vault(tmp_path)
    assert tm.cmd_plain(root, "all", day=dt.date(2026, 9, 10)) == 0
    out = capsys.readouterr().out
    assert "== Overdue (4) ==" in out and "OVERDUE" in out
    assert "== No due date (1) ==" in out
    assert tm.cmd_plain(root, "projects") == 0


def _note_vault(tmp_path):
    (tmp_path / "Tasks").mkdir()
    (tmp_path / "Tasks" / "N.md").write_text(
        "# N\n\n"
        "- [ ] Parent 📅 2026-09-04\n"
        "  First line of the parent note.\n"
        "\n"
        "  Second paragraph, after a blank.\n"
        "  - [ ] Kid one\n"
        "    Kid note.\n"
        "  - [ ] Kid two\n"
        "\n"
        "- [ ] Solo\n"
        "Prose at column 0 is not a note.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_notes_parse_with_blank_lines_and_nesting(tmp_path):
    tasks = tm.load_all(_note_vault(tmp_path))
    parent, kid1, kid2, solo = (_by_desc(tasks, d) for d in ("Parent", "Kid one", "Kid two", "Solo"))
    assert parent.note == "First line of the parent note.\n\nSecond paragraph, after a blank."
    assert parent.note_end == 6 and parent.block_end == 6
    assert kid1.note == "Kid note." and kid1.note_end == 8
    assert kid2.note == "" and kid2.note_end == 0 and kid2.block_end == kid2.lineno
    assert solo.note == ""                                    # column-0 prose is not attached
    # Hierarchy is unaffected by the note lines in between.
    assert kid1.parent_lineno == parent.lineno and kid2.parent_lineno == parent.lineno


def test_set_note_replace_add_and_clear(tmp_path):
    root = _note_vault(tmp_path)
    parent = _by_desc(tm.load_all(root), "Parent")
    tm.set_note(root, parent, "Replaced.\n\n  keeps inner indent\nlast line\n\n")
    lines = (root / "Tasks" / "N.md").read_text(encoding="utf-8").splitlines()
    assert lines[2:8] == ["- [ ] Parent 📅 2026-09-04", "  Replaced.", "",
                          "    keeps inner indent", "  last line", "  - [ ] Kid one"]
    again = tm.load_all(root)
    assert _by_desc(again, "Parent").note == "Replaced.\n\n  keeps inner indent\nlast line"
    assert _by_desc(again, "Kid one").note == "Kid note."          # untouched
    # Add a note to a task that had none (Kid two), then clear the parent's.
    tm.set_note(root, _by_desc(again, "Kid two"), "New kid-two note")
    text = (root / "Tasks" / "N.md").read_text(encoding="utf-8")
    assert "  - [ ] Kid two\n    New kid-two note\n\n- [ ] Solo\n" in text
    tm.set_note(root, _by_desc(tm.load_all(root), "Parent"), "")
    text = (root / "Tasks" / "N.md").read_text(encoding="utf-8")
    assert "- [ ] Parent 📅 2026-09-04\n  - [ ] Kid one\n" in text
    assert _by_desc(tm.load_all(root), "Parent").note == ""


def test_notes_move_and_delete_with_their_task(tmp_path):
    root = _note_vault(tmp_path)
    tasks = tm.load_all(root)
    # A new sub-task goes after the parent's whole block (notes + kids + their notes).
    kid3 = tm.add_subtask(root, _by_desc(tasks, "Parent"), "Kid three")
    lines = (root / "Tasks" / "N.md").read_text(encoding="utf-8").splitlines()
    assert kid3.anchor
    assert lines[kid3.lineno - 2] == "  - [ ] Kid two" and tm.TASK_ANCHOR_RE.sub("", lines[kid3.lineno - 1]).rstrip() == "  - [ ] Kid three"
    # Indenting Solo under Parent carries nothing extra; outdenting Kid one carries its note.
    tasks = tm.load_all(root)
    assert tm.outdent_task(root, _by_desc(tasks, "Kid one")) is not None
    text = (root / "Tasks" / "N.md").read_text(encoding="utf-8")
    assert "\n- [ ] Kid one\n  Kid note.\n" in text
    tasks = tm.load_all(root)
    assert tm.indent_task(root, _by_desc(tasks, "Kid one")) is not None
    text = (root / "Tasks" / "N.md").read_text(encoding="utf-8")
    assert "\n  - [ ] Kid one\n    Kid note.\n" in text
    # Deleting the parent removes its note, the kids, and their notes
    # (Parent + 3 note lines + Kid one + its note + Kid two + Kid three = 8).
    n = tm.delete_task(root, _by_desc(tm.load_all(root), "Parent"))
    text = (root / "Tasks" / "N.md").read_text(encoding="utf-8")
    assert n == 8 and "Parent" not in text and "Kid note" not in text
    assert text == "# N\n\n\n- [ ] Solo\nProse at column 0 is not a note.\n"


def test_plain_prints_notes(tmp_path, capsys):
    root = _note_vault(tmp_path)
    tm.cmd_plain(root, "all", day=dt.date(2026, 9, 4))
    out = capsys.readouterr().out
    assert "      First line of the parent note.\n" in out
    assert "        Kid note.\n" in out


def test_link_project_and_note(tmp_path):
    root = _tree_vault(tmp_path)
    tasks = tm.load_all(root)
    solo = _by_desc(tasks, "Solo")
    assert tm.link_project(root, solo, "Alpha") is not None
    assert tm.link_project(root, _by_desc(tm.load_all(root), "Solo"), "Alpha") is None
    assert "#project/Alpha" in (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
    assert tm.project_links(_by_desc(tm.load_all(root), "Solo")) == ["Alpha"]
    assert tm.link_note(root, _by_desc(tm.load_all(root), "Solo"), "Meeting") is not None
    again = tm.load_all(root)
    solo_now = next(t for t in again if t.description.startswith("Solo"))
    assert tm.note_links(solo_now) == ["Meeting"]
    assert tm.link_note(root, solo_now, "meeting") is None  # dup, any case
    assert "[[Meeting]]" in (root / "Tasks" / "Inbox.md").read_text(encoding="utf-8")
