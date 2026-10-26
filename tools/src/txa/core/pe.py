"""Minimal, dependency-free Portable Executable reader.

Reads only what TXA needs: CPU machine, OS / subsystem versions, section table,
overlay offset, certificate table and the VS_VERSIONINFO string table.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MACHINES = {0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0x01C4: "arm", 0x0200: "ia64"}
_RT_VERSION = 16
_DIR_RESOURCE = 2
_DIR_SECURITY = 4


class PEError(ValueError):
    pass


@dataclass
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_pointer: int
    raw_size: int


@dataclass
class PEInfo:
    machine: str
    machine_raw: int
    pe32_plus: bool
    subsystem: int
    os_version: tuple[int, int]
    subsystem_version: tuple[int, int]
    sections: list[Section]
    overlay_offset: int
    security: tuple[int, int]  # (file offset, size) of the Authenticode blob
    resource: tuple[int, int]  # (rva, size)
    file_size: int
    version_info: dict[str, str] = field(default_factory=dict)
    fixed_product_version: str | None = None

    @property
    def has_embedded_signature(self) -> bool:
        offset, size = self.security
        return size > 8 and offset + size <= self.file_size

    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]


def read_pe(path: Path | str, with_version: bool = True) -> PEInfo:
    path = Path(path)
    with path.open("rb") as fh:
        file_size = path.stat().st_size
        dos = fh.read(64)
        if len(dos) < 64 or dos[:2] != b"MZ":
            raise PEError("missing MZ header")
        (e_lfanew,) = struct.unpack_from("<I", dos, 0x3C)
        if e_lfanew <= 0 or e_lfanew > min(file_size - 24, 0x10000000):
            raise PEError("invalid e_lfanew")
        fh.seek(e_lfanew)
        nt = fh.read(24)
        if len(nt) < 24 or nt[:4] != b"PE\0\0":
            raise PEError("missing PE signature")
        machine, n_sections, _, _, _, opt_size, _ = struct.unpack_from("<HHIIIHH", nt, 4)
        opt = fh.read(opt_size)
        if len(opt) < opt_size or opt_size < 70:
            raise PEError("truncated optional header")
        (magic,) = struct.unpack_from("<H", opt, 0)
        if magic not in (0x10B, 0x20B):
            raise PEError(f"unknown optional header magic 0x{magic:x}")
        plus = magic == 0x20B
        os_ver = struct.unpack_from("<HH", opt, 40)
        sub_ver = struct.unpack_from("<HH", opt, 48)
        (subsystem,) = struct.unpack_from("<H", opt, 68)
        n_dirs_at, dirs_at = (108, 112) if plus else (92, 96)
        (n_dirs,) = struct.unpack_from("<I", opt, n_dirs_at) if len(opt) >= n_dirs_at + 4 else (0,)
        dirs = []
        for index in range(min(n_dirs, 16)):
            at = dirs_at + index * 8
            if at + 8 > len(opt):
                break
            dirs.append(struct.unpack_from("<II", opt, at))
        raw_sections = fh.read(40 * n_sections)
        sections = []
        for index in range(len(raw_sections) // 40):
            chunk = raw_sections[index * 40 : index * 40 + 40]
            name = chunk[:8].rstrip(b"\0").decode("latin-1")
            vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", chunk, 8)
            sections.append(Section(name, vaddr, vsize, rptr, rsize))
        overlay = max((s.raw_pointer + s.raw_size for s in sections if s.raw_size), default=fh.tell())
        info = PEInfo(
            machine=MACHINES.get(machine, f"0x{machine:04x}"),
            machine_raw=machine,
            pe32_plus=plus,
            subsystem=subsystem,
            os_version=(os_ver[0], os_ver[1]),
            subsystem_version=(sub_ver[0], sub_ver[1]),
            sections=sections,
            overlay_offset=min(overlay, file_size),
            security=dirs[_DIR_SECURITY] if len(dirs) > _DIR_SECURITY else (0, 0),
            resource=dirs[_DIR_RESOURCE] if len(dirs) > _DIR_RESOURCE else (0, 0),
            file_size=file_size,
        )
        if with_version:
            try:
                _read_version_info(fh, info)
            except (PEError, struct.error, UnicodeDecodeError):
                pass  # version info is optional; a malformed one must not break analysis
        return info


# --------------------------------------------------------------------------- resources


def _rva_to_offset(info: PEInfo, rva: int) -> int:
    for section in info.sections:
        size = max(section.virtual_size, section.raw_size)
        if section.virtual_address <= rva < section.virtual_address + size:
            return rva - section.virtual_address + section.raw_pointer
    raise PEError(f"rva 0x{rva:x} is outside every section")


def _read_version_info(fh, info: PEInfo) -> None:
    rva, size = info.resource
    if not rva or not size or size > 64 * 1024 * 1024:
        return
    base = _rva_to_offset(info, rva)
    fh.seek(base)
    blob = fh.read(size)

    def entries(offset: int):
        if offset + 16 > len(blob):
            return []
        named, ids = struct.unpack_from("<HH", blob, offset + 12)
        out = []
        for index in range(min(named + ids, 4096)):
            at = offset + 16 + index * 8
            if at + 8 > len(blob):
                break
            name, target = struct.unpack_from("<II", blob, at)
            out.append((name, target))
        return out

    def first_leaf(offset: int, depth: int = 0):
        for _, target in entries(offset):
            if target & 0x80000000:
                if depth < 3:
                    leaf = first_leaf(target & 0x7FFFFFFF, depth + 1)
                    if leaf is not None:
                        return leaf
            else:
                return target
        return None

    for name, target in entries(0):
        if name == _RT_VERSION and target & 0x80000000:
            leaf = first_leaf(target & 0x7FFFFFFF)
            if leaf is None or leaf + 16 > len(blob):
                return
            data_rva, data_size = struct.unpack_from("<II", blob, leaf)
            fh.seek(_rva_to_offset(info, data_rva))
            _parse_vs_version(fh.read(min(data_size, 1024 * 1024)), info)
            return


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _parse_block(data: bytes, offset: int):
    """Return (key, value_bytes, value_type, children, length) of one version block."""
    if offset + 6 > len(data):
        raise PEError("truncated version block")
    length, value_length, value_type = struct.unpack_from("<HHH", data, offset)
    if length < 6:
        raise PEError("zero-length version block")
    end = min(offset + length, len(data))
    cursor = offset + 6
    key_end = cursor
    while key_end + 1 < end and data[key_end : key_end + 2] != b"\0\0":
        key_end += 2
    key = data[cursor:key_end].decode("utf-16-le", errors="replace")
    cursor = _align4(key_end + 2)
    value_size = value_length * 2 if value_type == 1 else value_length
    value = data[cursor : min(cursor + value_size, end)]
    cursor = _align4(cursor + value_size)
    children = []
    while cursor + 6 <= end:
        child = _parse_block(data, cursor)
        children.append(child)
        cursor = _align4(cursor + child[4])
    return key, value, value_type, children, length


def _parse_vs_version(data: bytes, info: PEInfo) -> None:
    key, value, _, children, _ = _parse_block(data, 0)
    if key != "VS_VERSION_INFO":
        return
    if len(value) >= 52 and struct.unpack_from("<I", value, 0)[0] == 0xFEEF04BD:
        ms, ls = struct.unpack_from("<II", value, 16)
        info.fixed_product_version = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    for child_key, _, _, tables, _ in children:
        if child_key != "StringFileInfo":
            continue
        for _, _, _, strings, _ in tables:
            for name, raw, _, _, _ in strings:
                text = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0].strip()
                if text and name not in info.version_info:
                    info.version_info[name] = text
