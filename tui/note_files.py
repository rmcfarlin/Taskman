"""Preview and safely apply note filename changes and their local Markdown links."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import tempfile
from urllib.parse import quote, unquote, urlsplit

from .notes import Note, NoteConflict, NotesStore
from .taskman import vault_write_lock
from .vaults import is_linked, is_reserved_windows_name, vault_path


_TECHNICAL = {"node_modules", "vendor", "venv", "env", "__pycache__", "dist", "build",
              "artifacts", "target", "bin", "obj", "site-packages", "coverage", "htmlcov"}
_WIKI = re.compile(r"\[\[([^\]\n|]+)(?:\|[^\]\n]*)?\]\]")
_REFERENCE = re.compile(r"(?m)^ {0,3}\[[^\]\n]+\]:\s*(?:<([^>\n]+)>|([^\s]+))")
_HTML = re.compile(r"<\s*[A-Za-z](?:[^'\">]|\"[^\"]*\"|'[^']*')*>")
_ATTRIBUTE = re.compile(r"\b([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")
_ENTITY = re.compile(r"&(?:#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?)")


@dataclass(frozen=True, slots=True)
class RenamePlan:
    old_file: str
    new_file: str
    paths: tuple[str, ...]
    changed_files: int
    changed_links: int
    ambiguous_links: int
    root: str
    snapshots: tuple[tuple[str, bytes], ...]
    replacements: tuple[tuple[str, bytes], ...]


def _scan(root: Path) -> tuple[tuple[str, bytes], ...]:
    """Read every eligible document; an unreadable or linked document blocks planning."""
    found = {}
    folders = [root]
    templates = root / ".taskman/templates/notes"
    if templates.exists():
        vault_path(root, ".taskman/templates/notes")
        folders.append(templates)
    def failed(error):
        raise NoteConflict("Cannot scan Markdown links: " + str(error.filename)) from error
    for base in folders:
        for directory, dirs, files in os.walk(base, followlinks=False, onerror=failed):
            selected = []
            for name in sorted(dirs):
                if name.startswith(".") or name.casefold() in _TECHNICAL:
                    continue
                if is_linked(Path(directory) / name):
                    raise NoteConflict(f"Cannot scan linked folder: {Path(directory, name).relative_to(root)}")
                selected.append(name)
            dirs[:] = selected
            for name in sorted(files):
                if Path(name).suffix.casefold() != ".md":
                    continue
                relative = Path(directory, name).relative_to(root).as_posix()
                try:
                    path = vault_path(root, relative)
                    content = path.read_bytes()
                    content.decode("utf-8-sig")
                except (OSError, ValueError) as error:
                    raise NoteConflict(f"Cannot safely read Markdown links in {relative}") from error
                found[relative] = content
    return tuple(sorted(found.items()))


def _destination(store: NotesStore, old: str, new_name: str) -> str:
    if not isinstance(new_name, str) or not new_name or new_name != new_name.strip():
        raise ValueError("Enter a filename without leading or trailing spaces")
    if any(ord(char) < 32 or ord(char) == 127 or char in '<>:"/\\|?*\x85\u2028\u2029' for char in new_name):
        raise ValueError("Use a filename, without folders or reserved filename characters")
    if new_name.endswith((".", " ")) or is_reserved_windows_name(new_name):
        raise ValueError("This filename is reserved or ends with a dot or space")
    if not new_name.casefold().endswith(".md"):
        new_name += ".md"
    if new_name.casefold() == ".md":
        raise ValueError("A note filename needs a name before .md")
    relative = PurePosixPath(old).with_name(new_name).as_posix()
    store._path(relative)
    if relative.casefold() == old.casefold():
        raise ValueError("Choose a filename that differs by more than letter case")
    folder = store._path(old).parent
    if any(path.name.casefold() == new_name.casefold() for path in folder.iterdir()):
        raise NoteConflict("A file or folder already uses that filename")
    return relative


def _masked(text: str) -> str:
    """Mask examples and metadata while retaining offsets into the original bytes/text."""
    chars = list(text)
    def hide(start, end):
        chars[start:end] = ["\n" if char == "\n" else " " for char in text[start:end]]
    offset, fence, front = 0, None, None
    lists = []
    for index, line in enumerate(text.splitlines(keepends=True)):
        stripped = line.strip().lstrip("\ufeff")
        if index == 0 and stripped in ("---", "+++"):
            front = stripped
            hide(offset, offset + len(line))
        elif front:
            hide(offset, offset + len(line))
            if stripped == front or (front == "---" and stripped == "..."):
                front = None
        elif fence:
            hide(offset, offset + len(line))
            if re.match(re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$", line.lstrip()):
                fence = None
        else:
            listing = re.match(r"^(\s*)(?:[-+*]|\d+[.)])\s+", line)
            indentation = len(line) - len(line.lstrip(" \t"))
            if stripped:
                while lists and indentation < lists[-1]:
                    lists.pop()
            if listing:
                lists.append(listing.end())
            base = lists[-1] if lists and indentation >= lists[-1] else 0
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line[base:])
            if opening:
                fence = opening[1]
                hide(offset, offset + len(line))
            elif not listing and line.startswith(("    ", "\t")) and (not lists or indentation >= lists[-1] + 4):
                hide(offset, offset + len(line))
        offset += len(line)
    visible = "".join(chars)
    for pattern in (r"<!--[\s\S]*?(?:-->|\Z)", r"(?is)<(pre|code|script|style)\b[^>]*>.*?</\1\s*>",
                    r"(?s)(`+)(?!`)(.*?)\1(?!`)"):
        for match in re.finditer(pattern, visible):
            hide(match.start(), match.end())
        visible = "".join(chars)
    return visible


def _destinations(text: str):
    visible = _masked(text)
    spans = []
    for match in _WIKI.finditer(visible):
        if match.start() and visible[match.start() - 1] == "\\":
            continue
        spans.append((match.start(1), match.end(1), True))
    for match in _REFERENCE.finditer(visible):
        group = 1 if match[1] is not None else 2
        end = visible.find("\n", match.end())
        tail = visible[match.end():end if end >= 0 else len(visible)].strip()
        if tail and not re.fullmatch(r'''"[^"\n]*"|'[^'\n]*'|\([^\n)]*\)''', tail):
            continue
        spans.append((match.start(group), match.end(group), False))
    # Find inline link/image destinations with escaped and balanced parentheses.
    for match in re.finditer(r"(?<!\\)\]\(\s*", visible):
        # A closing bracket alone in prose is not a Markdown link.
        bracket, depth = match.start() - 1, 0
        while bracket >= 0:
            char = visible[bracket]
            if char == "\n" and re.match(r"[ \t\r]*\n", visible[bracket + 1:]):
                break
            escaped = bracket > 0 and visible[bracket - 1] == "\\"
            if not escaped and char == "]":
                depth += 1
            elif not escaped and char == "[":
                if depth == 0:
                    break
                depth -= 1
            bracket -= 1
        if bracket < 0 or visible[bracket] != "[":
            continue
        start, index = match.end(), match.end()
        if start >= len(visible):
            continue
        if visible[start] == "<":
            end = visible.find(">", start + 1)
            if end >= 0 and "\n" not in visible[start:end] and re.match(
                    r'''\s*(?:"[^"\n]*"|'[^'\n]*'|\([^\n)]*\))?\s*\)''', visible[end + 1:]):
                spans.append((start + 1, end, False))
            continue
        depth = 0
        while index < len(visible):
            char = visible[index]
            if char == "\\" and index + 1 < len(visible):
                index += 2
                continue
            if char.isspace() or (char == ")" and depth == 0):
                break
            depth += (char == "(") - (char == ")")
            index += 1
        if index > start and index < len(visible) and re.match(
                r'''\s*(?:"[^"\n]*"|'[^'\n]*'|\([^\n)]*\))?\s*\)''', visible[index:]):
            spans.append((start, index, False))
    for match in re.finditer(r"<((?:file:|/|\.{1,2}/)[^<>\n]+)>", visible, re.I):
        spans.append((match.start(1), match.end(1), False))
    for tag in _HTML.finditer(visible):
        for match in _ATTRIBUTE.finditer(tag[0]):
            if match[1].casefold() not in ("href", "src"):
                continue
            group = next(index for index in (2, 3, 4) if match[index] is not None)
            spans.append((tag.start() + match.start(group), tag.start() + match.end(group), False))
    # Angle link destinations may also match the autolink recognizer.
    yield from sorted(set(spans))


def _wiki_candidates(decoded, source, names):
    target = decoded if decoded.casefold().endswith(".md") else decoded + ".md"
    if "/" not in target:
        return [name for name in names if PurePosixPath(name).name.casefold() == target.casefold()]
    options = {posixpath.normpath(target.lstrip("/")),
               posixpath.normpath(posixpath.join(posixpath.dirname(source), target))}
    return [name for name in names if name.casefold() in {item.casefold() for item in options}]


def _unescape_with_spans(value):
    """Decode entities once, retaining raw spans for URL separators and suffixes."""
    pieces, spans, cursor = [], [], 0
    for match in _ENTITY.finditer(value):
        pieces.append(value[cursor:match.start()])
        spans.extend((index, index + 1) for index in range(cursor, match.start()))
        original = match[0]
        decoded = html.unescape(original)
        # HTML permits a recognized entity prefix followed by literal text,
        # e.g. &amp?raw=1. Keep offsets for that unconsumed suffix exact.
        shared = 0
        while shared < min(len(original), len(decoded)) and original[-shared - 1] == decoded[-shared - 1]:
            shared += 1
        entity_end = match.end() - shared
        spans.extend([(match.start(), entity_end)] * (len(decoded) - shared))
        spans.extend((index, index + 1) for index in range(entity_end, match.end()))
        pieces.append(decoded)
        cursor = match.end()
    pieces.append(value[cursor:])
    spans.extend((index, index + 1) for index in range(cursor, len(value)))
    return "".join(pieces), spans


def _replacement(raw, source, old, new, names, root, wiki):
    leading, trailing = len(raw) - len(raw.lstrip()), len(raw) - len(raw.rstrip())
    value = raw.strip()
    unescaped, spans = _unescape_with_spans(value)
    cut = min((index for index in (unescaped.find("#"), unescaped.find("?")) if index >= 0), default=len(unescaped))
    path_value = unescaped[:cut]
    suffix_start = spans[cut][0] if cut < len(spans) else len(value)
    path_text, suffix = value[:suffix_start], value[suffix_start:]
    if not path_value or path_value.startswith(("//", "\\\\")):
        return None
    decoded = unquote(re.sub(r"\\([\[\]()<> !#])", r"\1", path_value))
    if decoded.endswith("/"):
        return None  # A directory URL cannot resolve to this Markdown file.
    parsed = urlsplit(path_value)
    if parsed.scheme and parsed.scheme.casefold() != "file":
        return None
    if parsed.scheme:
        if parsed.netloc not in ("", "localhost"):
            return None
        absolute = unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", absolute):
            absolute = absolute[1:]
        try:
            resolved = Path(absolute).resolve().relative_to(root).as_posix()
        except ValueError:
            return None
        candidates = [name for name in names if name.casefold() == resolved.casefold()]
    elif wiki:
        candidates = _wiki_candidates(decoded, source, names)
    else:
        resolved = posixpath.normpath(decoded.lstrip("/") if decoded.startswith("/") else
                                      posixpath.join(posixpath.dirname(source), decoded))
        candidates = [name for name in names if name.casefold() == resolved.casefold()]
    if old not in candidates:
        return None
    if len(candidates) != 1:
        raise NoteConflict(f"Ambiguous link in {source}: {value}. Use a full vault path before renaming")
    base = PurePosixPath(new).name
    if wiki and not decoded.casefold().endswith(".md"):
        base = base[:-3]
    encoded = (base if wiki and "%" not in path_text and not any(char in base for char in "#[]%&")
               else quote(base, safe="-._~"))
    separators = list(re.finditer(r"/|%2[fF]", path_value))
    decoded_prefix_end = separators[-1].end() if separators else 0
    prefix_end = spans[decoded_prefix_end - 1][1] if decoded_prefix_end else 0
    replacement = path_text[:prefix_end] + encoded + suffix
    if wiki:
        target = unquote(path_value[:decoded_prefix_end] + encoded)
        renamed_names = tuple(new if name == old else name for name in names)
        if _wiki_candidates(target, source, renamed_names) != [new]:
            raise NoteConflict(f"Rename would make a link ambiguous in {source}: {value}. Use a full vault path before renaming")
    return raw[:leading] + replacement + (raw[-trailing:] if trailing else "")


def plan_rename(store: NotesStore, note: Note, new_name: str) -> RenamePlan:
    current = store.load(note.file)
    if not note.revision or current.revision != note.revision:
        raise NoteConflict("Note changed outside this editor; reload before renaming")
    new = _destination(store, note.file, new_name)
    snapshots = _scan(store.root)
    names = tuple(name for name, _ in snapshots)
    if note.file not in names:
        raise NoteConflict("The note is not in the scanned Markdown files")
    replacements, count = [], 0
    for name, content in snapshots:
        text = content.decode("utf-8")
        edits = []
        for start, end, wiki in _destinations(text):
            changed = _replacement(text[start:end], name, note.file, new, names, store.root, wiki)
            if changed is not None and changed != text[start:end]:
                edits.append((start, end, changed))
        for start, end, changed in reversed(edits):
            text = text[:start] + changed + text[end:]
        if edits:
            replacements.append((name, text.encode("utf-8")))
            count += len(edits)
    if hashlib.sha256(dict(snapshots)[note.file]).hexdigest() != note.revision:
        raise NoteConflict("Note changed while scanning links; preview again")
    paths = tuple(dict.fromkeys((note.file, new, *(name for name, _ in replacements))))
    return RenamePlan(note.file, new, paths, len(replacements), count, 0, str(store.root), snapshots, tuple(replacements))


def _stage(path: Path, content: bytes, mode: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=".taskman-rename-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read(root, name):
    path = vault_path(root, name)
    return path.read_bytes() if path.exists() else None


def _unchanged_write(root, name, content, identity):
    path = vault_path(root, name)
    return (path.exists() and os.path.samestat(path.stat(), identity)
            and path.read_bytes() == content)


def _install_new(root, name, temporary):
    # Same-directory hard-link publication is atomic and refuses an existing
    # destination on Windows and POSIX; unlinking the staging name leaves one file.
    path = vault_path(root, name)
    os.link(temporary, path)


def apply_rename(store: NotesStore, plan: RenamePlan) -> Note:
    if str(store.root) != plan.root:
        raise ValueError("Rename preview belongs to a different vault")
    before = dict(plan.snapshots)
    after = dict(plan.replacements)
    note_content = after.pop(plan.old_file, before[plan.old_file])
    stages, identities, applied, published = {}, {}, [], False
    with vault_write_lock(store.root):
        _destination(store, plan.old_file, PurePosixPath(plan.new_file).name)
        if _scan(store.root) != plan.snapshots:
            raise NoteConflict("Markdown files changed since the preview; preview the rename again")
        try:
            for name, content in {plan.new_file: note_content, **after}.items():
                source = plan.old_file if name == plan.new_file else name
                path = vault_path(store.root, name)
                stages[name] = _stage(path, content, stat.S_IMODE(vault_path(store.root, source).stat().st_mode))
                identities[name] = stages[name].stat()
            if _scan(store.root) != plan.snapshots:
                raise NoteConflict("Markdown files changed while preparing the rename; preview again")
            _destination(store, plan.old_file, PurePosixPath(plan.new_file).name)
            _install_new(store.root, plan.new_file, stages[plan.new_file])
            published = True
            for name, content in after.items():
                if _read(store.root, name) != before[name]:
                    raise NoteConflict("A linked document changed during rename: " + name)
                os.replace(stages[name], vault_path(store.root, name))
                applied.append(name)
            if _read(store.root, plan.old_file) != before[plan.old_file]:
                raise NoteConflict("The original note changed during rename")
            vault_path(store.root, plan.old_file).unlink()
            if not _unchanged_write(store.root, plan.new_file, note_content, identities[plan.new_file]):
                raise NoteConflict("The renamed note changed during rename")
            result = store.load(plan.new_file)
        except BaseException as error:
            incomplete = []
            for name in reversed(applied):
                try:
                    if not _unchanged_write(store.root, name, after[name], identities[name]):
                        raise NoteConflict("External edit")
                    temporary = _stage(vault_path(store.root, name), before[name],
                                       stat.S_IMODE(vault_path(store.root, name).stat().st_mode))
                    try:
                        if not _unchanged_write(store.root, name, after[name], identities[name]):
                            raise NoteConflict("External edit")
                        os.replace(temporary, vault_path(store.root, name))
                    finally:
                        temporary.unlink(missing_ok=True)
                except (OSError, ValueError, NoteConflict):
                    incomplete.append(name)
            if published:
                try:
                    if _read(store.root, plan.old_file) is None:
                        temporary = _stage(vault_path(store.root, plan.old_file), before[plan.old_file],
                                           stat.S_IMODE(vault_path(store.root, plan.new_file).stat().st_mode))
                        try:
                            _install_new(store.root, plan.old_file, temporary)
                        finally:
                            temporary.unlink(missing_ok=True)
                    if (_read(store.root, plan.old_file) != before[plan.old_file]
                            or not _unchanged_write(store.root, plan.new_file, note_content, identities[plan.new_file])):
                        raise NoteConflict("External edit")
                    vault_path(store.root, plan.new_file).unlink()
                except (OSError, ValueError, NoteConflict):
                    incomplete.append(plan.new_file)
            if incomplete:
                raise NoteConflict("Rename interrupted; external edits were preserved. Check: " + ", ".join(incomplete)) from error
            raise
        finally:
            for temporary in stages.values():
                temporary.unlink(missing_ok=True)
        store.notes = [item for item in store.notes if item.file != plan.old_file]
        store.errors.pop(plan.old_file, None)
        return store._remember(result)


def delete_note(store: NotesStore, note: Note) -> None:
    with vault_write_lock(store.root):
        content = store._read(note.file)
        if not note.revision or hashlib.sha256(content).hexdigest() != note.revision:
            raise NoteConflict("Note changed outside this editor; reload before deleting")
        if store._read(note.file) != content:
            raise NoteConflict("Note changed while deleting; reload and try again")
        store._path(note.file).unlink()
        store.notes = [item for item in store.notes if item.file != note.file]
        store.errors.pop(note.file, None)
