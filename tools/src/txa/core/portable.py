"""Portable packages: a ZIP that is unpacked, never executed by the installer.

Shared by the validator (inspect + safety checks), txacreate (derive strip/path) and txapm
(unpack, PATH). Everything that touches an archive goes through ``entries()`` so the same
zip-slip / zip-bomb rules apply everywhere.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

MAX_ENTRIES = 20_000
MAX_UNPACKED = 8 * 1024 ** 3
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class ZipProblem(ValueError):
    pass


@dataclass
class Entry:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]  # path inside the archive, already validated

    @property
    def is_dir(self) -> bool:
        return self.info.is_dir()


@dataclass
class Layout:
    entries: int
    files: int
    unpacked: int
    strip: int
    exe_dirs: list[str] = field(default_factory=list)  # after strip, "." = root
    suggested_path: list[str] = field(default_factory=list)


def _parts(name: str) -> tuple[str, ...]:
    if "\0" in name:
        raise ZipProblem(f"entry name contains a NUL byte: {name!r}")
    normalised = name.replace("\\", "/")
    if normalised.startswith("/") or (len(normalised) > 1 and normalised[1] == ":"):
        raise ZipProblem(f"absolute path inside the archive: {name!r}")
    parts = tuple(p for p in normalised.split("/") if p not in ("", "."))
    if any(p == ".." for p in parts):
        raise ZipProblem(f"path escapes the install folder: {name!r}")
    for part in parts:
        if part.split(".")[0].lower() in _RESERVED:
            raise ZipProblem(f"reserved Windows device name in path: {name!r}")
        if part.endswith((" ", ".")) or any(c in part for c in '<>:"|?*'):
            raise ZipProblem(f"path component not allowed on Windows: {name!r}")
    return parts


def entries(zf: zipfile.ZipFile, max_unpacked: int = MAX_UNPACKED) -> list[Entry]:
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ZipProblem(f"{len(infos)} entries (limit {MAX_ENTRIES})")
    total = sum(i.file_size for i in infos)
    if total > max_unpacked:
        raise ZipProblem(f"unpacks to {total / 1048576:.0f} MiB (limit {max_unpacked // 1048576} MiB)")
    out = []
    for info in infos:
        parts = _parts(info.filename)
        if parts:
            out.append(Entry(info, parts))
    return out


def analyse(path: Path | str, max_unpacked: int = MAX_UNPACKED) -> Layout:
    """Describe an archive and propose the ``strip`` / ``path`` values for its build card."""
    try:
        with zipfile.ZipFile(path) as zf:
            found = entries(zf, max_unpacked)
    except zipfile.BadZipFile as exc:
        raise ZipProblem(f"not a valid ZIP archive ({exc})") from None
    files = [e for e in found if not e.is_dir]
    strip = 0
    if files:
        tops = {e.parts[0] for e in found}
        if len(tops) == 1 and all(len(e.parts) > 1 or e.is_dir for e in found):
            strip = 1
    exe_dirs = list(_exes_by_folder(files, strip))
    suggested = ["bin"] if "bin" in exe_dirs else exe_dirs[:1]
    return Layout(len(found), len(files), sum(i.info.file_size for i in files), strip, exe_dirs, suggested)


def _exes_by_folder(files: list[Entry], strip: int) -> dict[str, list[str]]:
    """{folder after strip ('.' = root): [program names]}, shallowest folders first."""
    found: dict[str, list[str]] = {}
    for entry in files:
        parts = entry.parts[strip:]
        if parts and parts[-1].lower().endswith(".exe"):
            found.setdefault("/".join(parts[:-1]) or ".", []).append(parts[-1])
    return dict(sorted(found.items(), key=lambda kv: (kv[0].count("/") if kv[0] != "." else 0, kv[0])))


def exes(path: Path | str, strip: int) -> dict[str, list[str]]:
    """Programs inside an archive, grouped by folder, for a given ``strip``."""
    with zipfile.ZipFile(path) as zf:
        return _exes_by_folder([e for e in entries(zf) if not e.is_dir], strip)


def find_exe(path: Path | str, strip: int, folder: str) -> Entry | None:
    """First .exe directly inside ``folder`` (relative, after strip)."""
    with zipfile.ZipFile(path) as zf:
        for entry in entries(zf):
            parts = entry.parts[strip:]
            if entry.is_dir or not parts or not parts[-1].lower().endswith(".exe"):
                continue
            if ("/".join(parts[:-1]) or ".") == folder:
                return entry
    return None


def read_member(path: Path | str, entry: Entry, limit: int = 256 * 1024 * 1024) -> Path:
    """Copy one member to a private temp folder (caller deletes it) - used to read PE headers."""
    tmp = Path(tempfile.mkdtemp(prefix="txa-zip-"))
    target = tmp / entry.parts[-1]
    with zipfile.ZipFile(path) as zf, zf.open(entry.info) as src, target.open("wb") as dst:
        written = 0
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            written += len(chunk)
            if written > limit:
                break
            dst.write(chunk)
    return target


def extract(path: Path | str, dest: Path, strip: int = 0, on_progress: Callable[[int, int], None] | None = None,
            max_unpacked: int = MAX_UNPACKED) -> int:
    """Unpack into ``dest`` (created fresh). Returns the number of files written."""
    dest = Path(dest).resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    written_files = 0
    done = 0
    with zipfile.ZipFile(path) as zf:
        found = entries(zf, max_unpacked)
        total = sum(e.info.file_size for e in found if not e.is_dir) or 1
        for entry in found:
            parts = entry.parts[strip:]
            if not parts:
                continue
            target = (dest.joinpath(*parts)).resolve()
            if dest != target and dest not in target.parents:  # belt and braces after _parts()
                raise ZipProblem(f"refusing to write outside the install folder: {entry.info.filename!r}")
            if entry.is_dir:
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry.info) as src, target.open("wb") as dst:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
                    done += len(chunk)
                    if on_progress:
                        on_progress(done, total)
            written_files += 1
    return written_files


def folder_exists_with_exe(root: Path, folder: str) -> bool:
    base = root if folder == "." else root.joinpath(*PurePosixPath(folder).parts)
    return base.is_dir() and any(p.suffix.lower() == ".exe" for p in base.iterdir())
