"""Licence identifiers accepted by TXA, shared by the repository bot (STEP 05) and txacreate.

A licence is either an SPDX identifier, or one of the non-open terms in ``NEEDS_URL`` which must
come with a link to the actual terms (``license.url``).
"""
from __future__ import annotations

import difflib
import re

SPDX = {
    "0BSD", "AFL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later", "Apache-2.0", "Artistic-2.0", "BSD-2-Clause",
    "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "CDDL-1.0", "EPL-1.0", "EPL-2.0",
    "EUPL-1.2", "GPL-2.0-only", "GPL-2.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later", "ISC",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only", "LGPL-3.0-or-later", "MIT", "MIT-0", "MPL-2.0",
    "OFL-1.1", "PostgreSQL", "Python-2.0", "Unlicense", "Unicode-3.0", "WTFPL", "Zlib",
}
NEEDS_URL = {"Proprietary", "Freeware", "Shareware", "Commercial"}

# (identifier, one-line explanation) shown as a numbered menu by txacreate
COMMON = [
    ("MIT", "permissive, very short"),
    ("Apache-2.0", "permissive, with a patent grant"),
    ("BSD-3-Clause", "permissive"),
    ("BSD-2-Clause", "permissive, simpler"),
    ("ISC", "permissive, MIT-like"),
    ("GPL-3.0-or-later", "copyleft: derived work must stay open (v3 or newer)"),
    ("GPL-2.0-or-later", "copyleft (v2 or newer)"),
    ("LGPL-3.0-or-later", "weak copyleft, usable as a library"),
    ("AGPL-3.0-or-later", "copyleft that also covers network use"),
    ("MPL-2.0", "file-level copyleft"),
    ("Unlicense", "public domain"),
    ("CC0-1.0", "public domain dedication"),
    ("Freeware", "closed source, free to use (needs a link to the terms)"),
    ("Proprietary", "closed source / commercial (needs a link to the terms)"),
]

_ALIASES = {
    "mit": "MIT", "mit license": "MIT", "the mit license": "MIT", "expat": "MIT",
    "apache": "Apache-2.0", "apache 2": "Apache-2.0", "apache 2.0": "Apache-2.0", "apache2": "Apache-2.0",
    "apache license": "Apache-2.0", "apache license 2.0": "Apache-2.0", "apache license version 2.0": "Apache-2.0",
    "bsd": "BSD-3-Clause", "bsd 3": "BSD-3-Clause", "bsd 3 clause": "BSD-3-Clause", "new bsd": "BSD-3-Clause",
    "bsd 2": "BSD-2-Clause", "bsd 2 clause": "BSD-2-Clause", "simplified bsd": "BSD-2-Clause",
    "gpl3": "GPL-3.0-or-later", "gplv3": "GPL-3.0-or-later", "gpl 3": "GPL-3.0-or-later", "gnu gpl v3": "GPL-3.0-or-later",
    "gpl2": "GPL-2.0-or-later", "gplv2": "GPL-2.0-or-later", "gpl 2": "GPL-2.0-or-later", "gnu gpl v2": "GPL-2.0-or-later",
    # deprecated SPDX spellings: the bare id means "this version only"
    "gpl 3.0": "GPL-3.0-only", "gpl 2.0": "GPL-2.0-only", "lgpl 2.1": "LGPL-2.1-only", "lgpl 3.0": "LGPL-3.0-only",
    "agpl 3.0": "AGPL-3.0-only", "gpl 3.0 or later": "GPL-3.0-or-later", "gpl 2.0 or later": "GPL-2.0-or-later",
    "lgpl": "LGPL-3.0-or-later", "lgpl3": "LGPL-3.0-or-later", "lgplv3": "LGPL-3.0-or-later",
    "agpl": "AGPL-3.0-or-later", "agpl3": "AGPL-3.0-or-later", "agplv3": "AGPL-3.0-or-later",
    "mpl": "MPL-2.0", "mpl 2": "MPL-2.0", "mpl 2.0": "MPL-2.0", "mozilla public license 2.0": "MPL-2.0",
    "public domain": "Unlicense", "cc0": "CC0-1.0", "free": "Freeware", "commercial": "Commercial",
    "closed source": "Proprietary", "eula": "Proprietary",
}


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", text.lower()).strip()


_CANONICAL = {name.lower(): name for name in SPDX | NEEDS_URL}


def normalize(text: str) -> str | None:
    """'MIT License' -> 'MIT', 'apache 2.0' -> 'Apache-2.0'. None when it cannot be recognised."""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower() in _CANONICAL:
        return _CANONICAL[text.lower()]
    folded = _fold(text)
    if folded in _ALIASES:
        return _ALIASES[folded]
    if folded.endswith(" license") and folded[: -len(" license")] in _ALIASES:
        return _ALIASES[folded[: -len(" license")]]
    return None


def suggest(text: str) -> list[str]:
    return difflib.get_close_matches(text.strip(), sorted(SPDX | NEEDS_URL), n=3, cutoff=0.5)


def needs_url(name: str) -> bool:
    return name in NEEDS_URL
