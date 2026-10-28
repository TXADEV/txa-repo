"""The step-by-step questions of ``txacreate new`` / ``update`` (interactive terminals only).

Every question shows a suggested answer taken from the installer or the GitHub project: Enter
accepts it, typing replaces it. Nothing here touches the network or the disk - it only fills in
the ``Draft`` and the ``Item`` list, so the flow can be tested by scripting the answers.
"""
from __future__ import annotations

import re

from txa.core import config, console, licenses, semver, winos
from txa.core.manifest import LANG_RE, ManifestError, split_id
from txa.create.draft import Draft, Item

_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
_MIN_BUILDS = ["10.0.17763", "10.0.19044", "10.0.19045", "10.0.22000", "10.0.22621", "10.0.26100"]
ARCHES = [
    console.Option("x64", "X64", "64-bit Intel/AMD software"),
    console.Option("x86", "X86", "32-bit Intel/AMD software"),
    console.Option("arm64", "Arm64", "software built for Windows on ARM"),
    console.Option("arm", "Arm", "32-bit Windows on ARM"),
    console.Option("neutral", "Neutral", "architecture independent"),
]


def docs_url() -> str:
    return "https://aka.ms/winget-manifest-schema"


def intro() -> None:
    paint = console.paint
    print(f"\nFor information about the restrictions for each field, visit {paint(docs_url(), 'cyan')}")
    print(f"Press ENTER to submit the value for each question including accepting the {paint('(default)', 'gray')} value.")
    print("Please enter values for the following fields:\n")


# --------------------------------------------------------------------------- validators


def _check_id(value: str, namespaces: list[str]) -> str | None:
    try:
        namespace, _ = split_id(value)
    except ManifestError as exc:
        return str(exc)
    if namespaces and namespace not in namespaces:
        return f"namespace '{namespace}' is not registered in this repository (allowed: {', '.join(namespaces)})"
    return None


def _check_version(value: str) -> str | None:
    return None if semver.is_valid(value) else "use exactly three numbers, like 1.2.3"


def _check_lang(value: str) -> str | None:
    return None if LANG_RE.match(value) else "use a language tag such as en-US or vi-VN"


def _check_summary(value: str) -> str | None:
    return None if 10 <= len(value) <= 160 else f"between 10 and 160 characters (now {len(value)})"


def _https(value: str) -> str | None:
    return None if value.startswith("https://") else "the link must start with https://"


def _optional_https(value: str) -> str | None:
    return None if not value else _https(value)


def _names(value: str) -> list[str]:
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def _check_names(value: str) -> str | None:
    bad = [n for n in _names(value) if not _TAG.match(n)]
    return f"not allowed: {', '.join(bad)} (lower-case letters, digits and - only, 2-32 characters)" if bad else None


# --------------------------------------------------------------------------- questions


def ask_arch(item: Item) -> str:
    values = [o.value for o in ARCHES]
    cur_arch = (item.arch or "x64").lower()
    options = [console.Option(o.value, o.label, o.note + ("  <- detected from the file" if item.arch_known and o.value == cur_arch else ""))
               for o in ARCHES]
    default_idx = values.index(cur_arch) if cur_arch in values else 0
    return console.choose("The installer target architecture", "Architecture", options, default_idx).value


def ask_license(draft: Draft) -> None:
    """Pick the license from a list with an explanation next to each entry; Freeware/Proprietary also need a link."""
    license_options = [
        console.Option("MIT", "MIT", "MIT License - Permissive, limited liability"),
        console.Option("Apache-2.0", "Apache-2.0", "Apache 2.0 - Permissive with patent grant"),
        console.Option("GPL-3.0-or-later", "GPL-3.0-or-later", "GNU GPL v3 - Copyleft, requires source code"),
        console.Option("Proprietary", "Proprietary", "Proprietary - Closed-source, commercial (requires terms URL)"),
        console.Option("Freeware", "Freeware", "Freeware - Free to use, closed-source"),
        console.Option("BSD-3-Clause", "BSD-3-Clause", "BSD 3-Clause - Permissive with attribution"),
        console.Option("MPL-2.0", "MPL-2.0", "Mozilla Public License 2.0 - File-level copyleft"),
        console.Option("other", "Other...", "any other SPDX identifier, for example Zlib or EPL-2.0"),
    ]
    current = licenses.normalize(draft.license_name) if draft.license_name else None
    names = [o.value for o in license_options]
    if current and current not in names:
        license_options.insert(0, console.Option(current, current, "found for this project"))
        names.insert(0, current)
    default = names.index(current) if current in names else 0
    picked = console.choose("The package license", "License", license_options, default).value
    if picked == "other":
        def known(value: str) -> str | None:
            if licenses.normalize(value):
                return None
            hint = licenses.suggest(value)
            return f"'{value}' is not a known license" + (f" - did you mean {', '.join(hint)}?" if hint else "")

        picked = licenses.normalize(console.field("The license identifier", "Zlib", "License", "", validate=known))
    draft.license_name = picked
    if licenses.needs_url(picked):
        draft.license_url = console.field("The link to the license terms", "https://example.com/terms", "LicenseUrl",
                                          draft.license_url or "", validate=_https)


def _installer_questions(item: Item) -> None:
    item.arch = ask_arch(item)
    if item.engine == "exe" and not item.switches.get("silent"):
        item.switches["silent"] = console.field("The silent install switches", "/S", "switches.silent", "")
        item.switches["progress"] = console.field("The switches that show progress", "/S", "switches.progress",
                                                  item.switches["silent"])
    if item.engine == "portable":
        spec = item.portable
        shown = ", ".join(spec.get("path", [])) or "-"
        print(f"{console.label('portable', 11)}unpacked into the TXA apps folder; PATH gets: {console.paint(shown, 'cyan')}")


def optional_locale(draft: Draft) -> None:
    free = lambda v: None  # noqa: E731 - every optional question may be left empty
    description = console.field("The full package description", "What the software does, in a few sentences",
                                "description", draft.description or "",
                                validate=lambda v: None if len(v) <= 5000 else "5000 characters at most")
    draft.description = description or None
    draft.homepage = console.field("The package home page", "https://example.com", "homepage", draft.homepage or "",
                                   validate=_optional_https) or None
    draft.author = console.field("The author", "Jane Doe", "author", draft.author or draft.publisher, validate=free) or draft.publisher
    draft.license_url = console.field("The link to the license text", "https://example.com/LICENSE", "license.url",
                                      draft.license_url or "", validate=_optional_https) or None
    draft.tags = _names(console.field("Tags, separated by commas", "editor,text", "tags", ",".join(draft.tags),
                                      validate=lambda v: _check_names(v) if v else None))
    names = _names(console.field("Short names for txapm, separated by commas", "hello,hi", "alias",
                                 ",".join(filter(None, [draft.alias, *draft.aliases])),
                                 validate=lambda v: _check_names(v) if v else None))
    draft.alias, draft.aliases = (names[0] if names else None), names[1:]


def optional_installer(draft: Draft, items: list[Item]) -> None:
    scopes = [console.Option("machine", "machine", "for every user - Windows asks for administrator approval (UAC)"),
              console.Option("user", "user", "only for the current user - no administrator needed")]
    draft.scope = console.choose("The installation scope", "scope", scopes, 0 if draft.scope == "machine" else 1).value
    builds = [console.Option(b, b, winos.describe(winos.parse_build(b)) + ("  (the TXA minimum)" if b == _MIN_BUILDS[0] else ""))
              for b in _MIN_BUILDS]
    builds.append(console.Option("other", "Other...", "type a Windows build such as 10.0.19041"))
    current = draft.min_build if draft.min_build in _MIN_BUILDS else None
    picked = console.choose("The minimum Windows version", "platform.min_build", builds,
                            _MIN_BUILDS.index(current) if current else 0).value
    if picked == "other":
        def build_ok(value: str) -> str | None:
            try:
                winos.parse_build(value)
            except ValueError as exc:
                return str(exc)
            return None

        picked = console.field("The minimum Windows build", "10.0.19041", "platform.min_build", draft.min_build, validate=build_ok)
    draft.min_build = picked
    for item in items:
        if item.engine == "portable":
            continue
        defaults = {**{"silent": "", "progress": "", "custom": ""}, **item.switches}
        item.switches = {
            "silent": console.field("The silent install switches", "/S", "switches.silent", defaults["silent"]),
            "progress": console.field("The switches that show progress", "/S", "switches.progress",
                                      defaults["progress"] or defaults["silent"]),
            "custom": console.field("Extra switches that are always added", "/NORESTART", "switches.custom",
                                    defaults["custom"], validate=lambda v: None),
        }
        item.switches = {k: v for k, v in item.switches.items() if v}
    if len(items) == 1:
        raw = console.field("Extra download mirrors, separated by commas", "https://mirror.example.com/a.exe", "mirrors",
                            ",".join(items[0].mirrors), validate=lambda v: None if all(_https(m) is None for m in _urls(v)) else "every mirror must start with https://")
        items[0].mirrors = _urls(raw)


def _urls(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def recap(draft: Draft, items: list[Item]) -> None:
    paint = console.paint
    for item in items:
        print(f"\nAdditional metadata needed for installer from {paint(item.url, 'cyan')}")
        print(f"The installer target architecture [Architecture] value is: {paint(item.arch.upper() if item.arch != 'neutral' else 'Neutral', 'cyan', 'bold')}")
    for label, key, value in (("The publisher name", "Publisher", draft.publisher),
                              ("The package name", "PackageName", draft.name),
                              ("The package license", "License", draft.license_name),
                              ("The short package description", "ShortDescription", draft.summary)):
        print(f"{label} [{key}] value is: {paint(value, 'cyan', 'bold')}")
    print()


def collect(draft: Draft, items: list[Item], *, lock_id: bool = False, namespaces: list[str] | None = None,
            github: dict | None = None, first_time: bool = True) -> None:
    """Ask every question, using the current ``draft`` values as the suggested answers."""
    namespaces = namespaces or []
    if first_time:
        intro()
    if lock_id:
        print(f"The package unique identifier [PackageIdentifier] value is: {console.paint(draft.package_id, 'cyan', 'bold')}")
    else:
        draft.package_id = console.field("The package unique identifier", "Microsoft.VisualStudio", "PackageIdentifier", draft.package_id,
                                         validate=lambda v: _check_id(v, namespaces))
    if github and first_time:
        console.say("GitHub URL detected. The CLI has automatically filled some manifest fields.")
    draft.version = console.field("The package version", "1.2.3.4", "PackageVersion", draft.version, validate=_check_version)
    draft.lang = console.field("The package meta-data default locale", "en-US", "DefaultLocale", draft.lang, validate=_check_lang)
    for item in items:
        print(f"\nAdditional metadata needed for installer from {console.paint(item.url, 'cyan')}")
        _installer_questions(item)
    draft.publisher = console.field("The publisher name", "Microsoft", "Publisher", draft.publisher)
    draft.name = console.field("The package name", "Visual Studio", "PackageName", draft.name)
    ask_license(draft)
    draft.summary = console.field("The short package description", "", "ShortDescription",
                                  draft.summary, validate=_check_summary)
    draft.author = draft.author or draft.publisher
    recap(draft, items)
    if console.confirm("Would you like to modify the optional default locale fields?"):
        optional_locale(draft)
    if console.confirm("Would you like to modify the optional installer fields?"):
        optional_installer(draft, items)
