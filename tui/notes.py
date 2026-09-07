"""Standalone Markdown notes with optimistic conflict checks and portable paths.

Notes own their content independently of linked tasks. An edit replaces one file
atomically after checking its revision again; arbitrary external programs cannot
be locked out of the final check/replace interval by a portable stdlib API.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Iterable

from .vaults import is_linked, normalize_folder, vault_path


class NoteConflict(RuntimeError):
    """The editor's version no longer matches the Markdown on disk."""


class NoteFormatError(ValueError):
    """Taskman metadata cannot be safely interpreted; leave the file alone."""


@dataclass(frozen=True, slots=True)
class TaskLink:
    id: str
    title: str


@dataclass(frozen=True, slots=True)
class Note:
    file: str
    title: str
    body: str
    category: str = "Unfiled"
    tags: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()
    tasks: tuple[TaskLink, ...] = ()
    revision: str = ""
    modified_ns: int = field(default=0, compare=False)


@dataclass
class _Document:
    note: Note
    prefix: str
    newline: str
    metadata: dict
    heading: bool


_DEFAULT = object()
_MARKER = "<!-- taskman-note"
_COMMENT = re.compile(r"\A\s*<!-- taskman-note:\s*(.*?)-->", re.DOTALL)
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


def _line(value: str, label: str, default: str = "") -> str:
    if not isinstance(value, str) or any(ord(ch) < 32 or ord(ch) == 127 or ch in "\x85\u2028\u2029" for ch in value):
        raise NoteFormatError(f"{label} must be a single line of text")
    value = value.strip() or default
    if not value:
        raise NoteFormatError(f"{label} is required")
    return value


def _labels(values: Iterable[str], label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, dict)):
        raise NoteFormatError(f"{label} must be a list of labels")
    result, seen = [], set()
    try:
        for value in values:
            value = _line(value, label)
            if label == "Tags":
                value = value.lstrip("#")
                if not value:
                    raise NoteFormatError("A tag needs a name")
            if value.casefold() not in seen:
                seen.add(value.casefold())
                result.append(value)
    except TypeError as error:
        raise NoteFormatError(f"{label} must be a list of labels") from error
    return tuple(result)


def _canonical(note: Note) -> Note:
    if not isinstance(note.body, str) or "\x00" in note.body:
        raise NoteFormatError("Note body must be text without NUL characters")
    links, seen = [], set()
    try:
        for link in note.tasks:
            if not isinstance(link, TaskLink):
                raise NoteFormatError("Task links need an ID and title")
            link = TaskLink(_line(link.id, "Task ID"), _line(link.title, "Task title"))
            if link.id not in seen:
                seen.add(link.id)
                links.append(link)
    except TypeError as error:
        raise NoteFormatError("Task links must be a list") from error
    return replace(note, title=_line(note.title, "Title"),
                   body=note.body.replace("\r\n", "\n").replace("\r", "\n"),
                   category=_line(note.category, "Category", "Unfiled"),
                   tags=_labels(note.tags, "Tags"), projects=_labels(note.projects, "Projects"),
                   tasks=tuple(links))


def _frontmatter(text: str) -> tuple[str, str]:
    """Keep existing YAML/TOML headers verbatim without interpreting their keys."""
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text[len(bom):]
    lines = text.splitlines(keepends=True)
    if lines and lines[0].strip() in ("---", "+++"):
        closing = {lines[0].strip()}
        if "---" in closing:
            closing.add("...")
        for index in range(1, len(lines)):
            if lines[index].strip() in closing:
                return bom + "".join(lines[:index + 1]), "".join(lines[index + 1:])
    return bom, text


def _parse(file: str, raw: bytes) -> _Document:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NoteFormatError("Note is not UTF-8 Markdown") from error
    prefix, body = _frontmatter(text)
    newline = "\r\n" if "\r\n" in text else "\n"
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    metadata, heading = {}, False
    if body.lstrip().startswith(_MARKER):
        match = _COMMENT.match(body)
        if match is None:
            raise NoteFormatError("Malformed taskman-note metadata comment")
        try:
            metadata = json.loads(match[1])
        except (ValueError, TypeError) as error:
            raise NoteFormatError("Malformed taskman-note JSON") from error
        if not isinstance(metadata, dict) or type(metadata.get("version")) is not int or metadata["version"] != 1:
            raise NoteFormatError("Unsupported taskman-note metadata version")
        heading = metadata.get("heading", False)
        if not isinstance(heading, bool):
            raise NoteFormatError("Invalid managed heading flag")
        body = body[match.end():]
        body = body[2:] if body.startswith("\n\n") else body[1:] if body.startswith("\n") else body
        title = _line(metadata.get("title"), "Title")
        if heading:
            generated = f"# {title}\n\n"
            if not body.startswith(generated):
                raise NoteFormatError("Managed title heading differs from taskman-note metadata")
            body = body[len(generated):]
        for key in ("tags", "projects", "tasks"):
            if not isinstance(metadata.get(key, []), list):
                raise NoteFormatError(f"Invalid {key} list in taskman-note metadata")
        links = []
        for item in metadata.get("tasks", []):
            if not isinstance(item, dict):
                raise NoteFormatError("Invalid task link in taskman-note metadata")
            links.append(TaskLink(item.get("id"), item.get("title")))
    else:
        title_match = re.search(r"^#[ \t]+(.+?)[ \t]*$", body, re.MULTILINE)
        title = re.sub(r"[ \t]+#+$", "", title_match[1]) if title_match else Path(file).stem
        links = []
    note = _canonical(Note(file, title, body, metadata.get("category", "Unfiled"),
                           metadata.get("tags", ()), metadata.get("projects", ()), tuple(links),
                           hashlib.sha256(raw).hexdigest()))
    return _Document(note, prefix, newline, metadata, heading)


def _encode(note: Note, document: _Document) -> bytes:
    metadata = dict(document.metadata)
    old_links = {item["id"]: item for item in metadata.get("tasks", [])}
    metadata.update(version=1, title=note.title, category=note.category, tags=list(note.tags),
                    projects=list(note.projects), heading=document.heading,
                    tasks=[{**old_links.get(link.id, {}), "id": link.id, "title": link.title}
                           for link in note.tasks])
    payload = json.dumps(metadata, ensure_ascii=False, separators=(", ", ": ")).replace("--", "\\u002d\\u002d")
    content = f"<!-- taskman-note: {payload} -->\n\n"
    if document.heading:
        content += f"# {note.title}\n\n"
    content += note.body
    prefix = document.prefix
    if prefix and not prefix.endswith(("\n", "\r", "\ufeff")):
        prefix += document.newline
    return (prefix + content.replace("\n", document.newline)).encode("utf-8")


class NotesStore:
    def __init__(self, root: Path):
        self.root = normalize_folder(root)
        self.notes: list[Note] = []
        self.errors: dict[str, str] = {}

    def _path(self, file: str) -> Path:
        relative = Path(os.fspath(file).replace("\\", "/"))
        if len(relative.parts) < 2 or relative.parts[0] != "Notes" or relative.suffix.casefold() != ".md":
            raise ValueError("Notes must be Markdown files inside Notes/")
        if any(_RESERVED.match(part) or re.search(r'[<>:"|?*\x00-\x1f]', part)
               for part in relative.parts[1:]):
            raise ValueError("Use a portable note path without reserved names or characters")
        return vault_path(self.root, file)

    def _read(self, file: str) -> bytes:
        path = self._path(file)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Note must be a regular Markdown file")
        return path.read_bytes()

    def load(self, file: str) -> Note:
        path = self._path(file)
        with path.open("rb") as stream:
            observed = os.fstat(stream.fileno())
            if not stat.S_ISREG(observed.st_mode):
                raise ValueError("Note must be a regular Markdown file")
            raw = stream.read()
        return replace(_parse(path.relative_to(self.root).as_posix(), raw).note,
                       modified_ns=observed.st_mtime_ns)

    def refresh(self) -> list[Note]:
        self.notes, self.errors = [], {}
        try:
            folder = vault_path(self.root, "Notes")
            if not folder.exists():
                return []
            if not folder.is_dir():
                raise ValueError("Notes is not a folder")
            def failed(error):
                self.errors[str(error.filename)] = str(error)
            for directory, dirs, files in os.walk(folder, followlinks=False, onerror=failed):
                dirs[:] = sorted(name for name in dirs if not is_linked(Path(directory) / name))
                for name in sorted(files):
                    path = Path(directory) / name
                    if path.suffix.casefold() != ".md":
                        continue
                    relative = path.relative_to(self.root).as_posix()
                    try:
                        self.notes.append(self.load(relative))
                    except (OSError, ValueError) as error:
                        self.errors[relative] = str(error)
        except (OSError, ValueError) as error:
            self.errors["Notes"] = str(error)
        self.notes.sort(key=lambda note: (note.title.casefold(), note.file.casefold()))
        return list(self.notes)

    def new_path(self, title: str) -> str:
        title = _line(title, "Title")
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title).strip(" .")[:100].rstrip(" .") or "Note"
        if _RESERVED.match(name):
            name = "_" + name
        folder = vault_path(self.root, "Notes")
        existing = {path.name.casefold() for path in folder.iterdir()} if folder.exists() else set()
        for number in range(1, 10001):
            filename = f"{name}{'' if number == 1 else f' ({number})'}.md"
            if filename.casefold() not in existing:
                relative = f"Notes/{filename}"
                self._path(relative)
                return relative
        raise ValueError("Too many notes with the same title")

    def _remember(self, note: Note) -> Note:
        note = replace(note, modified_ns=self._path(note.file).stat().st_mtime_ns)
        self.notes = sorted([item for item in self.notes if item.file != note.file] + [note],
                            key=lambda item: (item.title.casefold(), item.file.casefold()))
        self.errors.pop(note.file, None)
        return note

    def create(self, title: str, body: str = "", category: str = "Unfiled", tags=(), projects=(),
               tasks=(), file: str | None = None) -> Note:
        if file is not None:
            self._path(file)
        prepared = _canonical(Note(file or "Notes/Untitled.md", title, body, category, tags, projects, tasks))
        from .taskman import vault_write_lock
        with vault_write_lock(self.root):
            return self._create_locked(prepared.title, prepared.body, prepared.category,
                                       prepared.tags, prepared.projects, prepared.tasks, file)

    def _create_locked(self, title, body, category, tags, projects, tasks, file) -> Note:
        for _attempt in range(100):
            relative = file if file is not None else self.new_path(title)
            path = self._path(relative)
            relative = path.relative_to(self.root).as_posix()
            note = _canonical(Note(relative, title, body, category, tags, projects, tasks))
            raw = _encode(note, _Document(note, "", "\n", {}, True))
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path(relative)
            try:
                stream = path.open("xb")
            except FileExistsError:
                if file is not None:
                    raise
                continue
            created = None
            try:
                with stream:
                    created = os.fstat(stream.fileno())
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                # Remove our partial creation, but never a file another process
                # replaced it with while the write was failing.
                try:
                    self._path(relative)
                    if created is not None and os.path.samestat(created, path.lstat()):
                        path.unlink()
                except (OSError, ValueError):
                    pass
                raise
            return self._remember(_parse(relative, raw).note)
        raise NoteConflict("A note with this name keeps appearing; choose another title")

    def save(self, note: Note, *, expected_revision=_DEFAULT) -> Note:
        note = _canonical(note)
        self._path(note.file)
        from .taskman import vault_write_lock
        with vault_write_lock(self.root):
            return self._save_locked(note, expected_revision=expected_revision)

    def _save_locked(self, note: Note, *, expected_revision=_DEFAULT) -> Note:
        note = _canonical(note)
        expected = note.revision if expected_revision is _DEFAULT else expected_revision
        path = self._path(note.file)
        try:
            before = self._read(note.file)
        except FileNotFoundError as error:
            raise NoteConflict("Note was removed or renamed; reload before saving") from error
        if not expected or hashlib.sha256(before).hexdigest() != expected:
            raise NoteConflict("Note changed outside this editor; reload before saving")
        document = _parse(note.file, before)
        if note == document.note:
            return self._remember(document.note)
        raw = _encode(note, document)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", prefix=".taskman-note-", dir=path.parent,
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            try:
                current = self._read(note.file)
            except FileNotFoundError as error:
                raise NoteConflict("Note was removed while saving") from error
            if current != before:
                raise NoteConflict("Note changed while saving; reload before saving")
            self._path(note.file)
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self._remember(_parse(note.file, raw).note)

    def categories(self) -> list[str]:
        return sorted(_labels((note.category for note in self.notes), "Categories"), key=str.casefold)

    def tags(self) -> list[str]:
        return sorted(_labels((tag for note in self.notes for tag in note.tags), "Tags"), key=str.casefold)


def filter_notes(notes: Iterable[Note], query: str = "", category: str = "", tag: str = "") -> list[Note]:
    """Match all text terms; a #tag term matches one complete tag, ignoring case."""
    terms = query.casefold().split()
    category, tag = category.strip().casefold(), tag.strip().lstrip("#").casefold()
    matches = []
    for note in notes:
        tags = {value.casefold() for value in note.tags}
        text = " ".join((note.title, note.body, note.category, *note.tags, *note.projects)).casefold()
        if ((not category or note.category.casefold() == category)
                and (not tag or tag in tags)
                and all(term[1:] in tags if term.startswith("#") else term in text for term in terms)):
            matches.append(note)
    return matches
