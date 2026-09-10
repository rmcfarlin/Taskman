"""Small editable Markdown note templates; discovery never creates vault files."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from .taskman import vault_write_lock
from .vaults import is_reserved_windows_name, normalize_folder, vault_path


TEMPLATE_DIR = ".taskman/templates/notes"


class TemplateConflict(ValueError):
    """The template changed after the editor loaded it."""


@dataclass(frozen=True, slots=True)
class Template:
    name: str
    body: str
    file: str
    revision: str | None = None
    builtin: bool = False


MEETING_TEMPLATE = Template(
    "Meeting",
    "Date: {{date}}\n\n## Attendees\n\n- \n\n## Discussion\n\n"
    "- \n\n## Decisions\n\n- \n\n## Actions\n\n- Action — owner — due date\n",
    f"{TEMPLATE_DIR}/Meeting.md",
    builtin=True,
)


def _body(text: str) -> str:
    if not isinstance(text, str) or "\x00" in text:
        raise ValueError("Template body must be text without NUL characters")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _path(root: Path, file: str) -> Path:
    if not isinstance(file, str):
        raise ValueError("Template file must be a vault-relative Markdown path")
    relative = Path(file.replace("\\", "/"))
    if (relative.parent.as_posix() != TEMPLATE_DIR or relative.suffix.casefold() != ".md"
            or is_reserved_windows_name(relative.name)
            or re.search(r'[<>:"|?*\x00-\x1f]', relative.name)):
        raise ValueError(f"Templates must be portable Markdown files directly inside {TEMPLATE_DIR}/")
    return vault_path(root, relative)


def _read(root: Path, file: str) -> bytes:
    path = _path(root, file)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Template must be a regular Markdown file")
    return path.read_bytes()


def _loaded(root: Path, file: str, raw: bytes) -> Template:
    try:
        body = _body(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        raise ValueError(f"Template {Path(file).name} is not UTF-8 Markdown") from error
    path = _path(root, file)
    return Template(path.stem, body, path.relative_to(root).as_posix(),
                    hashlib.sha256(raw).hexdigest())


def list_templates(vault: str | Path) -> list[Template]:
    """Read custom templates, with a built-in Meeting fallback and no writes."""
    root = normalize_folder(vault)
    folder = vault_path(root, TEMPLATE_DIR)
    templates: dict[str, Template] = {}
    if folder.exists():
        if not folder.is_dir():
            raise ValueError(f"{TEMPLATE_DIR} must be a folder")
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.casefold() != ".md":
                continue
            key = path.name.casefold()
            if key in templates:
                raise ValueError("Template filenames must be unique ignoring letter case")
            relative = path.relative_to(root).as_posix()
            templates[key] = _loaded(root, relative, _read(root, relative))
    templates.setdefault("meeting.md", MEETING_TEMPLATE)
    return sorted(templates.values(), key=lambda item: (item.name.casefold(), item.file.casefold()))


def save_template(vault: str | Path, template: Template, body: str) -> Template:
    """Save an explicitly edited template after checking its revision/absence.

    Like note saves, the final byte check detects observed external edits;
    external programs do not participate in the application writer lock.
    """
    root = normalize_folder(vault)
    body = _body(body)
    path = _path(root, template.file)
    with vault_write_lock(root):
        try:
            before = _read(root, template.file)
        except FileNotFoundError:
            before = None
        if template.revision is None:
            if not template.builtin or before is not None:
                raise TemplateConflict("Template appeared or changed; reopen it before saving")
        elif before is None or hashlib.sha256(before).hexdigest() != template.revision:
            raise TemplateConflict("Template changed or was removed; reopen it before saving")
        # Avoid duplicate case variants when creating the built-in override on
        # case-sensitive systems; these files must remain portable to Windows.
        if before is None and path.parent.exists():
            if any(item.name.casefold() == path.name.casefold() for item in path.parent.iterdir()):
                raise TemplateConflict("Template appeared with this name; reopen it before saving")
        newline = "\r\n" if before is not None and b"\r\n" in before else "\n"
        raw = body.replace("\n", newline).encode("utf-8")
        if before is not None and before.startswith(b"\xef\xbb\xbf"):
            raw = b"\xef\xbb\xbf" + raw
        if raw == before:
            return _loaded(root, template.file, raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        path = _path(root, template.file)
        if before is None:
            created = None
            try:
                with path.open("xb") as stream:
                    created = os.fstat(stream.fileno())
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError as error:
                raise TemplateConflict("Template appeared; reopen it before saving") from error
            except BaseException:
                if created is not None:
                    try:
                        path = _path(root, template.file)
                        if os.path.samestat(created, path.stat()):
                            path.unlink()
                    except (OSError, ValueError):
                        pass
                raise
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".taskman-template-",
                                                 dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
                try:
                    current = _read(root, template.file)
                except FileNotFoundError as error:
                    raise TemplateConflict("Template was removed; reopen it before saving") from error
                if current != before:
                    raise TemplateConflict("Template changed while saving; reopen it before saving")
                os.replace(temporary, _path(root, template.file))
                temporary = None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return _loaded(root, template.file, raw)


def instantiate(template: Template, today: dt.date | None = None) -> str:
    """Copy the body, substituting only {{date}}; never execute template code."""
    return template.body.replace("{{date}}", (today or dt.date.today()).isoformat())
