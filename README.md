# txa-repo

Kho gói phần mềm Windows của TXA — **một repo duy nhất** chứa manifest, bot kiểm định 16 bước,
công cụ `txapm` / `txacreate` và script cài đặt.

```
txa-repo/
├── manifests/t/TXAVL/<AppName>/<X.Y.Z>/   các release (core / build / about card)
├── examples/manifests/…                   card mẫu có chú thích
├── policy.txa.yaml                        giới hạn & chính sách (đọc từ commit base của PR)
├── install.ps1                            cài txapm + txacreate vào máy, thêm PATH
├── scripts/setup-github.ps1               gh CLI: nhãn, CODEOWNERS, branch protection, Pages
├── tools/                                 package Python `txa` (txapm, txacreate, txaci) + tests
└── .github/
    ├── CODEOWNERS                         moderator duyệt manifests/, admin duyệt phần còn lại
    └── workflows/
        ├── txa-validate.yml               STEP 01-14 (token chỉ đọc, tools lấy từ commit base)
        ├── txa-gate.yml                   STEP 15-16 (nhãn, status txa/verified, auto-merge squash)
        ├── txa-publish-api.yml            REST API tĩnh txa.v1 lên GitHub Pages
        ├── txa-tools-ci.yml               pytest cho tools/
        └── txa-release.yml                tag vX.Y.Z → txapm.exe / txacreate.exe (x64, arm64)
```

## Người dùng — `txapm`

```powershell
irm https://raw.githubusercontent.com/TXAVL/txa-repo/main/install.ps1 | iex
```

| Lệnh | Việc |
|---|---|
| `txapm install <id\|alias> [--version] [--arch] [--progress\|--interactive]` | tải về `%TEMP%\txa`, kiểm SHA-256, cài ẩn (tự xin UAC khi `scope: machine`) |
| `txapm upgrade [<id>] [--all]` | liệt kê / nâng cấp gói có bản mới |
| `txapm uninstall <id> [--interactive]` | gỡ ẩn (msiexec /x, QuietUninstallString, hoặc cờ theo engine) |
| `txapm list [--upgrade-available]` | gói đã cài kèm cột bản mới |
| `txapm show <id> [--versions]` · `txapm search <text>` | xem chi tiết / tìm kiếm |
| `txapm export <file>` · `txapm import <file> [--exact-versions]` | sao lưu và cài lại cả bộ gói |

Alias: `add`, `update`, `remove`/`rm`, `ls`, `view`, `find`.

## Người đóng gói — `txacreate`

| Lệnh | Việc |
|---|---|
| `txacreate login` / `login --with-token` | GitHub Device Code Flow hoặc PAT (lưu mã hoá DPAPI) |
| `txacreate new <url> [<url-arch-khác>…]` | tải, băm, phân tích PE/MSI, viết 3 card, chạy 14 bước cục bộ, fork → branch `TXAVL.<App>-<Ver>` → commit → PR |
| `txacreate update <id> <url>…` | bản mới của gói đã có, giữ nguyên metadata cũ |
| `txacreate hash <file\|url>` | SHA-256, kích thước, engine, kiến trúc |
| `txacreate validate <thư-mục>` | chạy 14 bước của bot trên máy |
| `--dry-run` | chỉ sinh card vào `./txa-out`, không gọi GitHub |

## Định dạng manifest TXA v1

| File | Nội dung |
|---|---|
| `core.txa.yaml` | `package.id` (`Namespace.AppName`), `package.version` (X.Y.Z), `channel`, `alias`, `locale.default`, `tags` |
| `build.txa.yaml` | `ref: <id>@<version>`, `platform.min_build`, `scope`, `targets[]`: `arch`, `engine` (inno/nsis/msi/wix/exe), `source{url, sha256, size, mirrors}`, `switches{silent, progress, custom}`, `success_codes` |
| `about.<lang>.txa.yaml` | `ref`, `lang`, `name`, `publisher`, `author`, `summary`, `description`, `homepage`, `license{name, url}` |

Mẫu: [examples/manifests/t/TXAVL/HelloTool/1.4.2](examples/manifests/t/TXAVL/HelloTool/1.4.2/build.txa.yaml).

## Bot kiểm định 16 bước

01 Encoding & BOM · 02 YAML strict & thụt lề · 03 Namespace/ID/đường dẫn · 04 SemVer & upgrade path ·
05 Localization & License · 06 HTTPS & redirect loop (chặn IP nội bộ) · 07 Dung lượng & header server ·
08 CDN 6 vùng (DoH + EDNS Client Subnet) & mirror · 09 Tải stream, resume, toàn vẹn · 10 Authenticode ·
11 SHA-256 hai lượt · 12 Kiến trúc PE & engine · 13 Windows tối thiểu · 14 Switch cài ẩn ·
15 Nhãn `txa:*` + status `txa/verified` · 16 Auto-merge squash, khoá bắt buộc ≥1 code owner duyệt.

PR chạm file ngoài `manifests/` (kể cả `tools/`) bị gắn `txa:infra-change`, không bao giờ auto-merge.

## Triển khai

```bash
pwsh ./scripts/setup-github.ps1 -Owner TXAVL -Moderators alice,bob -Admins carol
```

Sau đó: tạo OAuth App bật **Device Flow** rồi `gh variable set TXA_GITHUB_CLIENT_ID --repo TXAVL/txa-repo --body <id>`;
(tuỳ chọn) `gh secret set TXA_BOT_TOKEN --repo TXAVL/txa-repo`; phát hành công cụ bằng `git tag v1.0.0` + `git push origin v1.0.0`.

## Phát triển

```bash
cd tools && python -m pip install -e ".[dev]" && python -m pytest -q
```
