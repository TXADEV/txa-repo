"""Build tiny but structurally valid PE files for tests."""
from __future__ import annotations

import struct

FILE_ALIGN = 0x200
SECTION_ALIGN = 0x1000


def _block(key: str, value: bytes = b"", children: bytes = b"", text: bool = False) -> bytes:
    name = (key + "\0").encode("utf-16-le")
    pad1 = (-(6 + len(name))) % 4
    body = name + b"\0" * pad1 + value
    pad2 = (-(6 + len(body))) % 4 if children else 0
    body += b"\0" * pad2 + children
    value_length = len(value) // 2 if text else len(value)
    block = struct.pack("<HHH", 6 + len(body), value_length, 1 if text else 0) + body
    return block + b"\0" * ((-len(block)) % 4)


def version_resource(strings: dict[str, str], product_version=(1, 4, 2, 0)) -> bytes:
    entries = b"".join(_block(k, (v + "\0").encode("utf-16-le"), text=True) for k, v in strings.items())
    table = _block("040904b0", children=entries)
    string_file_info = _block("StringFileInfo", children=table)
    ms = (product_version[0] << 16) | product_version[1]
    ls = (product_version[2] << 16) | product_version[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x10000, ms, ls, ms, ls, 0x3F, 0, 0x40004, 1, 0, 0, 0)
    return _block("VS_VERSION_INFO", fixed, string_file_info)


def _rsrc(rva: int, data: bytes) -> bytes:
    def directory(entry_id: int, target: int) -> bytes:
        return struct.pack("<IIHHHH", 0, 0, 0, 0, 0, 1) + struct.pack("<II", entry_id, target)

    blob = directory(16, 0x80000000 | 24) + directory(1, 0x80000000 | 48) + directory(1033, 72)
    blob += struct.pack("<IIII", rva + 88, len(data), 0, 0)
    return blob + data


def build_pe(machine: int = 0x14C, plus: bool = False, sections=(".text",), overlay: bytes = b"",
             os_version=(6, 0), subsystem_version=(6, 0), strings: dict[str, str] | None = None) -> bytes:
    names = list(sections) + ([".rsrc"] if strings else [])
    opt_size = 0xF0 if plus else 0xE0
    e_lfanew = 0x80
    header_end = e_lfanew + 24 + opt_size + 40 * len(names)
    first_raw = (header_end + FILE_ALIGN - 1) // FILE_ALIGN * FILE_ALIGN

    raws, table = [], b""
    raw_ptr = first_raw
    resource_dir = (0, 0)
    for index, name in enumerate(names):
        rva = SECTION_ALIGN * (index + 1)
        content = _rsrc(rva, version_resource(strings)) if name == ".rsrc" else b"\xC3" * 16
        if name == ".rsrc":
            resource_dir = (rva, len(content))
        raw = content + b"\0" * ((-len(content)) % FILE_ALIGN)
        table += struct.pack("<8sIIIIIIHHI", name.encode()[:8], len(content), rva, len(raw), raw_ptr, 0, 0, 0, 0, 0x60000020)
        raws.append(raw)
        raw_ptr += len(raw)

    dos = bytearray(e_lfanew)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, e_lfanew)
    coff = struct.pack("<4sHHIIIHH", b"PE\0\0", machine, len(names), 0, 0, 0, opt_size, 0x0102)
    opt = bytearray(opt_size)
    struct.pack_into("<H", opt, 0, 0x20B if plus else 0x10B)
    struct.pack_into("<HH", opt, 40, *os_version)
    struct.pack_into("<HH", opt, 48, *subsystem_version)
    struct.pack_into("<H", opt, 68, 2)
    n_dirs_at = 108 if plus else 92
    struct.pack_into("<I", opt, n_dirs_at, 16)
    struct.pack_into("<II", opt, n_dirs_at + 4 + 2 * 8, *resource_dir)
    head = bytes(dos) + coff + bytes(opt) + table
    head += b"\0" * (first_raw - len(head))
    return head + b"".join(raws) + overlay
