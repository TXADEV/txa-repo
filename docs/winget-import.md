# Import & Tự Động Tạo Package từ WinGet / Máy Cá Nhân (`txacreate` & `winget-import`)

Công cụ [`scripts/winget-import.ps1`](../scripts/winget-import.ps1) và lệnh `txacreate new` đã được tích hợp trực tiếp cầu nối WinGet, cho phép lấy bất kỳ phần mềm nào từ `txapm list --all` hoặc tên phần mềm bất kỳ để tự động tạo manifest chuẩn TXA mà không cần đi tìm link tải thủ công.

---

## 1. Tự động thêm toàn bộ phần mềm đang cài trên máy (`--from-installed`)

Quét toàn bộ phần mềm trên máy (tương tự `txapm list --all`), lọc ra các phần mềm chưa có trong kho TXA và tự động tạo manifest:

```powershell
# Quét và tự động import tất cả phần mềm trên máy vào kho manifests/
.\scripts\winget-import.ps1 --from-installed -y

# Hoặc chạy ở chế độ chọn từng số (1,3,5 hoặc all):
.\scripts\winget-import.ps1 -i
```

Nếu muốn chạy qua luồng `txacreate new` đầy đủ (tự động tải file `.exe`/`.msi` thật về máy, kiểm tra SHA-256 & PE header):

```powershell
txacreate new --from-installed --dry-run
```

---

## 2. Truyền thẳng Tên App bất kỳ từ `txapm list --all` vào `txacreate new`

Không cần tìm URL tải `.exe`/`.msi`, chỉ cần gõ tên hiển thị hoặc WinGet ID vào `txacreate new`:

```powershell
# Truyền tên phần mềm bất kỳ (hoặc nhiều phần mềm cùng lúc)
txacreate new "GitHub CLI" "Unity Hub" "Steam" --dry-run --out .

# Hoặc dùng script winget-import cực nhanh (không cần tải toàn bộ bộ cài nặng về máy)
.\scripts\winget-import.ps1 "GitHub CLI" "GitHub Desktop" "Steam" "Sunshine" "Unity Hub"
```

---

## 3. Tìm kiếm package trên WinGet

```powershell
.\scripts\winget-import.ps1 -s "chrome"
.\scripts\winget-import.ps1 -s "Unity Hub"
```

---

## 4. Tạo kho riêng và thêm vào `txapm`

Nếu muốn import ra một thư mục riêng để làm kho source nội bộ/cá nhân:

```powershell
# Import app và sinh luôn REST API txa.v1
.\scripts\winget-import.ps1 --from-installed -y -o D:\my-txa-repo --build-api --api-out D:\my-txa-api

# Thêm kho vừa tạo vào txapm
txapm source add myrepo D:\my-txa-repo
```

---

## Các tham số hỗ trợ (`winget-import.ps1`)

| Tham số | Ý nghĩa | Mặc định |
|---|---|---|
| `[packages ...]` | Tên phần mềm bất kỳ (copy từ `txapm list --all`) hoặc WinGet ID | |
| `-i`, `--from-installed` | Tự động quét phần mềm đã cài trên máy chưa có trong TXA | |
| `-y`, `--yes` | Tự động chạy toàn bộ không hỏi lại | |
| `--txacreate` | Chuyển tiếp sang `txacreate new` (tải file thật & phân tích PE) | |
| `-s`, `--search <từ-khóa>` | Tìm kiếm WinGet ID theo tên phần mềm | |
| `-n`, `--namespace <tên>` | Namespace cho TXA package | `TXAVL` |
| `--id <ID-cụ-thể>` | Đặt tên Package ID riêng (vd `TXAVL.Chrome`) | Tự sinh |
| `-o`, `--out <thư-mục>` | Thư mục chứa manifests đầu ra | `./manifests` |
| `--build-api` | Tự động chạy `txaci build-api` sau khi import | |
| `--api-out <thư-mục>` | Thư mục chứa API tĩnh đầu ra | `dist/api` |
| `--dry-run` | Xem trước, không ghi file | |
