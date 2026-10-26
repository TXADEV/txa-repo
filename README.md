# TXA

Cài phần mềm Windows bằng một dòng lệnh. TXA tải bộ cài từ trang chính thức của nhà phát hành,
kiểm tra chữ ký số và mã SHA-256, rồi cài **im lặng**, không phải bấm Next.

```powershell
txapm install TXAVL.HelloTool
```

[![PyPI](https://img.shields.io/pypi/v/txa)](https://pypi.org/project/txa/)
[![Release](https://img.shields.io/github/v/release/TXAVL/txa-repo)](https://github.com/TXAVL/txa-repo/releases)

---

## Cài đặt

Yêu cầu: Windows 10 (1809) trở lên, x64 hoặc ARM64.

### Cách 1 — PowerShell (không cần Python)

Mở PowerShell (không cần quyền admin) và chạy:

```powershell
irm https://raw.githubusercontent.com/TXAVL/txa-repo/main/install.ps1 | iex
```

Script tải bản mới nhất, kiểm tra SHA-256, cài vào `%LOCALAPPDATA%\TXA` và tự thêm vào PATH.
Mở cửa sổ terminal mới là dùng được.

### Cách 2 — pipx (nếu đã có Python 3.10+)

```powershell
pipx install txa
```

Chưa có pipx thì chạy `python -m pip install --user pipx` rồi `python -m pipx ensurepath`, sau đó mở terminal mới.

Kiểm tra:

```powershell
txapm --version
```

---

## Dùng `txapm`

| Muốn làm gì | Lệnh |
|---|---|
| Tìm phần mềm | `txapm search notepad` |
| Xem thông tin một gói | `txapm show TXAVL.HelloTool` |
| Xem mọi phiên bản | `txapm show TXAVL.HelloTool --versions` |
| Cài bản mới nhất | `txapm install TXAVL.HelloTool` |
| Cài một phiên bản cụ thể | `txapm install TXAVL.HelloTool --version 1.4.2` |
| Cài nhưng vẫn xem tiến trình | `txapm install TXAVL.HelloTool --progress` |
| Cài với giao diện gốc của bộ cài | `txapm install TXAVL.HelloTool --interactive` |
| Chỉ tải về, không cài | `txapm install TXAVL.HelloTool --download-only` |
| Xem gói đã cài | `txapm list` |
| Xem gói có bản mới | `txapm list --upgrade-available` |
| Nâng cấp một gói | `txapm upgrade TXAVL.HelloTool` |
| Nâng cấp tất cả | `txapm upgrade --all` |
| Gỡ cài đặt | `txapm uninstall TXAVL.HelloTool` |
| Sao lưu danh sách gói | `txapm export pkgs.json` |
| Cài lại cả bộ trên máy mới | `txapm import pkgs.json` |
| Xem **mọi** phần mềm trên máy, đánh dấu cái nào có trong TXA | `txapm list --all` |
| Giữ nguyên phiên bản, không cho `upgrade --all` đụng vào | `txapm pin add TXAVL.HelloTool` |
| Bỏ giữ / xem danh sách đang giữ | `txapm pin remove TXAVL.HelloTool` · `txapm pin list` |
| Tải bộ cài + manifest về thư mục (mặc định Downloads) | `txapm download TXAVL.HelloTool -d D:\setup` |
| Xem các kho nguồn | `txapm source` (xem mục [Kho nguồn riêng](#kho-nguồn-riêng)) |
| Thông tin cài đặt, đường dẫn, tài khoản | `txapm --info` |

Gói nào có tên ngắn (alias) thì gõ tên ngắn cũng được, ví dụ `txapm install hellotool`.
Một số lệnh có tên viết tắt: `add` = install, `rm` = uninstall, `ls` = list, `find` = search.

### Khi cài, TXA làm gì?

1. Lấy thông tin gói từ kho `txa-repo` (đã được kiểm định 16 bước và có người duyệt).
2. Chọn đúng bộ cài cho máy bạn (ARM64 → x64 → x86).
3. Kiểm tra phiên bản Windows tối thiểu.
4. Tải về `%TEMP%\txa\…` và so mã SHA-256. Sai một byte là **xoá file và dừng**.
5. Cài im lặng. Gói cài cho toàn máy sẽ hiện hộp thoại UAC xin quyền admin.
6. Ghi log vào `%LOCALAPPDATA%\TXA\logs`.

---

## Kho nguồn riêng

Ngoài kho chính `txa`, bạn có thể thêm kho riêng: phần mềm nội bộ công ty, bản build riêng của nhóm…
Kho riêng chỉ cần phục vụ cùng REST API `txa.v1` (hai file JSON tĩnh), hoặc đơn giản là một thư mục chứa `manifests/`.

| Muốn làm gì | Lệnh |
|---|---|
| Xem các kho | `txapm source` |
| Thêm kho HTTPS | `txapm source add congty https://pkgs.congty.vn/api/txa/v1` |
| Thêm kho là thư mục (ổ mạng, máy cá nhân) | `txapm source add noibo \\server\share\txa` |
| Kho riêng tư cần token | `txapm source add congty <url> --token-env CONGTY_TOKEN` |
| Cho phép tải bộ cài từ máy chủ LAN | `txapm source add congty <url> --allow-lan` |
| Tắt / bật một kho | `txapm source disable txa` · `txapm source enable txa` |
| Xoá kho | `txapm source remove congty` |
| Làm mới / sao lưu cấu hình | `txapm source update` · `txapm source export` |
| Về mặc định (chỉ kho `txa`) | `txapm source reset` |
| Chỉ tìm / cài từ một kho | `txapm search app -s congty` · `txapm install App -s congty` |

- Kho được hỏi theo thứ tự: `txa` trước, rồi các kho thêm sau. Nếu cùng một Id có ở nhiều kho,
  TXA dừng và yêu cầu chọn bằng `-s <kho>` để tránh cài nhầm.
- Gói nhớ nó được cài từ kho nào, nên `txapm upgrade` luôn lấy bản mới từ đúng kho đó.
- Token không bao giờ được ghi vào file cấu hình: TXA chỉ lưu **tên biến môi trường**, và chỉ gửi
  token tới đúng máy chủ của kho đó.
- Kho phải dùng HTTPS (ngoại trừ `http://localhost` để thử nghiệm).

### Tự dựng một kho

1. Tạo thư mục có `manifests/<chữ-cái>/<Namespace>/<AppName>/<X.Y.Z>/` (soạn card bằng
   `txacreate new <url> --dry-run --out <thư-mục>` cho nhanh).
2. Dùng ngay dạng thư mục: `txapm source add noibo D:\kho-txa`. Hoặc sinh REST API tĩnh:
   ```powershell
   txaci build-api --root D:\kho-txa --out D:\kho-txa-site
   ```
3. Đưa thư mục `D:\kho-txa-site` lên bất kỳ máy chủ HTTPS nào (IIS, nginx, GitHub Pages, S3…), rồi
   `txapm source add congty https://<máy-chủ>/api/txa/v1`.

Muốn có cả bot kiểm định 16 bước như kho chính: fork repo này, thay namespace trong `policy.txa.yaml`,
chạy `scripts/setup-github.ps1 -Owner <tổ-chức>`.

---

## Đóng góp một phần mềm vào kho — `txacreate`

Ai có tài khoản GitHub cũng gửi được, không cần biết viết YAML.

**1. Gửi bộ cài** (link tải `.exe`/`.msi` chính thức, phải là HTTPS) — không cần đăng nhập trước:

```powershell
txacreate new https://vendor.example/app-2.1.0-x64.exe
```

TXA tự tải file, đọc tên phần mềm, phiên bản, nhà phát hành và loại bộ cài, hỏi thêm vài thông tin
còn thiếu (mô tả ngắn, giấy phép) và chạy thử 14 bước kiểm tra.

**2. Cấp quyền GitHub — chỉ ở bước cuối.** Khi sắp fork kho và mở Pull Request, TXA mới hiện:

```
GitHub access is needed to fork the repository and open the pull request:
  1. Open   https://github.com/login/device
  2. Enter  XXXX-XXXX
```

Trình duyệt tự mở, bạn nhập mã 8 ký tự rồi bấm **Authorize**. Lệnh chờ bạn duyệt xong rồi tự fork, tạo
branch, commit và **mở Pull Request**. Quyền được mã hoá và nhớ trên máy, lần sau không hỏi lại.

Có bản cho nhiều kiến trúc thì đưa nhiều link: `txacreate new <link-x64> <link-arm64>`.

**3. Phần mềm đã có trong kho, giờ có bản mới:**

```powershell
txacreate update TXAVL.HelloTool https://vendor.example/app-2.2.0-x64.exe
```

Mọi thông tin cũ được giữ nguyên, chỉ thay link và phiên bản.

**Lệnh tiện ích:**

| Lệnh | Việc |
|---|---|
| `txacreate new <link> --dry-run` | chỉ tạo file vào `./txa-out` để xem trước, không gửi gì |
| `txacreate hash setup.exe` | xem SHA-256, kiến trúc, loại bộ cài của một file |
| `txacreate submit <thư-mục>` | gửi PR cho một gói tự viết tay (đã có 3 file YAML) |
| `txacreate show TXAVL.HelloTool` | in nội dung manifest đang có trong kho |
| `txacreate validate <thư-mục>` | tự kiểm tra một gói viết tay |
| `txacreate token` | xem tài khoản GitHub đang dùng (không bắt buộc) |
| `txacreate token --store` | cấp quyền GitHub trước, không đợi tới lúc gửi |
| `txacreate token --clear` | quên quyền đã lưu (đăng xuất) |
| `txacreate new <link> -t <PAT>` | dùng Personal Access Token cho lần này, không lưu (hoặc đặt biến `TXA_TOKEN`) |

### Sau khi gửi Pull Request

Bot TXA kiểm tra tự động **16 bước**: mã hoá file, cú pháp, tên gói, phiên bản, giấy phép, HTTPS,
dung lượng, CDN ở 6 khu vực, tải về, chữ ký số, SHA-256, kiến trúc, phiên bản Windows, tham số cài im lặng.
Kết quả hiện ngay trong PR.

| Nhãn bot gắn | Nghĩa |
|---|---|
| `txa:verified-build` | qua hết kiểm tra tự động |
| `txa:awaiting-review` | đang chờ moderator duyệt |
| `txa:fix-required` | có lỗi — đọc comment của bot, sửa rồi push lại, bot tự chạy lại |
| `txa:new-app` / `txa:new-version` | gói mới / phiên bản mới |

Moderator duyệt xong, PR tự gộp, vài phút sau mọi người cài được bằng `txapm install`.

---

## Câu hỏi thường gặp

**Gõ `txapm` báo "not recognized"?** Mở cửa sổ terminal mới để PATH được nạp lại.

**Cài báo "SHA-256 mismatch"?** File trên máy chủ nhà phát hành đã thay đổi so với lúc duyệt.
TXA đã xoá file đó để bảo vệ bạn. Hãy báo lỗi ở [Issues](https://github.com/TXAVL/txa-repo/issues).

**Cài báo mã thoát 1618?** Windows đang cài một phần mềm khác, đợi xong rồi chạy lại.

**Cài báo mã thoát 3010?** Cài thành công, chỉ cần khởi động lại máy.

**Gỡ TXA khỏi máy?**

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/TXAVL/txa-repo/main/install.ps1))) -Uninstall
```

Cài bằng pipx thì gỡ bằng `pipx uninstall txa`.

---

<details>
<summary>Dành cho người phát triển / quản trị kho</summary>

- `manifests/t/TXAVL/<AppName>/<X.Y.Z>/` — mỗi phiên bản gồm `core.txa.yaml`, `build.txa.yaml`,
  `about.<lang>.txa.yaml` (mẫu có chú thích: [examples](examples/manifests/t/TXAVL/HelloTool/1.4.2/build.txa.yaml)).
- `tools/` — mã nguồn Python của `txapm`, `txacreate`, `txaci`
  (`cd tools && python -m pip install -e ".[dev]" && python -m pytest -q`).
- `.github/workflows/` — bot kiểm định (`txa-validate`, `txa-gate`), API (`txa-publish-api`),
  phát hành (`txa-release`, `txa-pypi` — chạy khi push tag `vX.Y.Z`).
- `policy.txa.yaml` — giới hạn dung lượng, namespace, vùng kiểm tra CDN.
- `scripts/setup-github.ps1` — cấu hình nhãn, CODEOWNERS, branch protection.
- API công khai: `https://txavl.github.io/txa-repo/api/txa/v1/index.json`.

</details>
