"""Bilingual (English / Vietnamese) localization for TXA CLI tools (txapm, txacli, txacreate, txaci).

Respects the language chosen during `install.ps1`, `txapm lang <en|vi>`, `txacreate config set lang <en|vi>`,
or the `TXA_LANG` environment variable. Defaults to 'en' when unset so unit tests remain deterministic.
"""
from __future__ import annotations

import re
from txa.core import config


def is_vi() -> bool:
    return config.lang() == "vi"


def tr(en: str, vi: str) -> str:
    return vi if is_vi() else en


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Resolved (.+?) from source "), r"Đã phân giải \1 từ nguồn "),
    (re.compile(r"^Resolved "), "Đã phân giải "),
    (re.compile(r"\(via GitHub fallback\)"), "(qua GitHub dự phòng)"),
    (re.compile(r"is already installed \(use --force to reinstall\)"), "đã được cài đặt (thêm --force để cài lại)"),
    (re.compile(r"Downloading (.+?) from (.+)"), r"Đang tải \1 từ \2"),
    (re.compile(r"Verifying SHA-256"), "Đang kiểm tra SHA-256"),
    (re.compile(r"Verified SHA-256 (.+?) matches the manifest"), r"Đã xác thực SHA-256 \1 khớp với manifest"),
    (re.compile(r"✓ saved to (.+)"), r"✓ đã lưu tại \1"),
    (re.compile(r"Unpacking the portable archive and adding it to PATH"), "Đang giải nén gói portable và thêm vào PATH"),
    (re.compile(r"Running the (.+?) installer \((.+?)\)"), r"Đang chạy bộ cài \1 (\2)"),
    (re.compile(r" - approve the UAC prompt"), " - hãy bấm Yes ở hộp thoại UAC"),
    (re.compile(r"Recorded in the TXA install list"), "Đã lưu vào danh sách cài đặt TXA"),
    (re.compile(r"installed in (.+?) \((.+?)\)$"), r"đã cài đặt trong \1 (\2)"),
    (re.compile(r"Looking for (.+?) in the installed programs"), r"Đang tìm \1 trong danh sách phần mềm đã cài"),
    (re.compile(r"Running the uninstaller for (.+)"), r"Đang chạy trình gỡ cài đặt cho \1"),
    (re.compile(r"uninstalled in (.+)"), r"đã gỡ cài đặt trong \1"),
    (re.compile(r"✓ everything is up to date"), "✓ mọi phần mềm đều đã ở bản mới nhất"),
    (re.compile(r"is up to date$"), "đã ở bản mới nhất"),
    (re.compile(r"! (\d+) upgrade\(s\) available - run (.+?) to apply"), r"! có \1 bản cập nhật mới - chạy \2 để nâng cấp"),
    (re.compile(r"upgraded (\d+) of (\d+) package\(s\)"), r"đã nâng cấp \1/\2 gói"),
    (re.compile(r"! nothing installed through txapm yet - try (.+)"), r"! chưa có gói nào được cài qua txapm - thử \1"),
    (re.compile(r"! no packages found for '([^']+)' - the repository may be empty or the name differs"),
     r"! không tìm thấy gói nào khớp với '\1' - kho có thể trống hoặc tên gói khác"),
    (re.compile(r"hint: found in other source\(s\): (.+?) — omit '([^']+)' to search all sources"),
     r"gợi ý: tìm thấy ở kho khác: \1 — hãy bỏ '\2' để tìm trên tất cả các kho"),
    (re.compile(r"package\(s\) - install with"), "gói - cài đặt bằng"),
    (re.compile(r"package (.+?) not found in source '([^']+)'"), r"không tìm thấy gói \1 trong kho '\2'"),
    (re.compile(r"package (.+?) not found in (.+)"), r"không tìm thấy gói \1 trong kho \2"),
    (re.compile(r"no source named '([^']+)' — did you mean '([^']+)'\? \(see `txapm source list`\)"),
     r"không có kho nguồn nào tên '\1' — có phải bạn muốn gõ '\2'? (xem `txapm source list`)"),
    (re.compile(r"no source named '([^']+)'"), r"không có kho nguồn nào tên '\1'"),
    (re.compile(r"✓ source '([^']+)' added - (\d+) package\(s\)"), r"✓ đã thêm kho nguồn '\1' - \2 gói"),
    (re.compile(r"✓ source '([^']+)' removed"), r"✓ đã xoá kho nguồn '\1'"),
    (re.compile(r"✓ source '([^']+)' enabled"), r"✓ đã bật kho nguồn '\1'"),
    (re.compile(r"✓ source '([^']+)' disabled"), r"✓ đã tắt kho nguồn '\1'"),
    (re.compile(r"✓ sources reset to the official txa source only"), r"✓ đã đặt lại về mặc định (chỉ giữ kho chính txa)"),
    (re.compile(r"✓ cleaned (\d+) file\(s\) \((.+?) freed\)"), r"✓ đã dọn dẹp \1 file (giải phóng \2)"),
    (re.compile(r"! no installer logs yet"), "! chưa có file log cài đặt nào"),
    (re.compile(r"no log found matching '([^']+)'"), r"không tìm thấy file log nào khớp với '\1'"),
    (re.compile(r"'([^']+)' is not recorded as installed through txapm \(try `txapm list --all`\)"),
     r"'\1' chưa được ghi nhận cài qua txapm (thử `txapm list --all`)"),
    (re.compile(r"→ found (\d+) release\(s\) under (.+?):"), r"→ tìm thấy \1 bản phát hành trong \2:"),
    (re.compile(r"Quick check of (.+?) \(the bot runs all 14 steps on the PR\)"),
     r"Kiểm tra nhanh \1 (bot sẽ chạy đủ 14 bước trên Pull Request)"),
    (re.compile(r"✓ cards look valid \(encoding, YAML, naming, version, license, switches\)"),
     r"✓ các thẻ manifest hợp lệ (bảng mã, YAML, định danh, phiên bản, giấy phép, tham số cài)"),
    (re.compile(r"validated \(--dry-run: skipped opening pull request\)"),
     r"đã kiểm tra hợp lệ (--dry-run: bỏ qua bước mở Pull Request)"),
    (re.compile(r"Forking the repository and opening the pull request"),
     r"Đang fork kho chứa và mở Pull Request"),
    (re.compile(r"program\(s\)$"), "chương trình"),
]


def translate(text: str) -> str:
    """Translate standard TXA CLI output into Vietnamese when config.lang() == 'vi'."""
    if not is_vi() or not text:
        return text
    out = text
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out

