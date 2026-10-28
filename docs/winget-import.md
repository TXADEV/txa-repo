# Import Package từ WinGet vào TXA (`winget-import`)

Công cụ [`scripts/winget-import.ps1`](../scripts/winget-import.ps1) (và [`scripts/winget-import.py`](../scripts/winget-import.py)) cho phép lấy metadata và link tải chính thức từ kho WinGet, chuẩn hóa theo schema của TXA, và tự động tạo kho source cho `txapm`.

---

## 1. Tìm kiếm package trên WinGet

```powershell
.\scripts\winget-import.ps1 -s "chrome"
.\scripts\winget-import.ps1 -s "vscode"
```

Lệnh sẽ liệt kê danh sách kết quả gồm `Id`, `Version`, `Name`.

---

## 2. Xem thử trước (Dry-Run)

Không ghi file vào ổ đĩa, chỉ in ra màn hình các card YAML đã chuẩn hóa:

```powershell
.\scripts\winget-import.ps1 Google.Chrome --dry-run
```

---

## 3. Import trực tiếp vào kho `manifests/` của repo

```powershell
.\scripts\winget-import.ps1 Google.Chrome
# Hoặc import nhiều gói cùng lúc:
.\scripts\winget-import.ps1 Google.Chrome Microsoft.VisualStudioCode Brave.Brave
```

File sẽ được lưu vào:
`manifests/<chữ-cái>/TXAVL/<AppName>/<Version>/`
- `core.txa.yaml`
- `build.txa.yaml`
- `about.en-US.txa.yaml`

---

## 4. Tạo kho riêng và thêm vào `txapm`

Nếu muốn import ra một thư mục riêng để làm kho source nội bộ/cá nhân:

```powershell
# Import app và sinh luôn REST API txa.v1
.\scripts\winget-import.ps1 Google.Chrome -o D:\my-txa-repo --build-api --api-out D:\my-txa-api
```

Thêm kho vừa tạo vào `txapm`:

```powershell
# Cách 1: Thêm trực tiếp thư mục local
txapm source add myrepo D:\my-txa-repo

# Cách 2: Host D:\my-txa-api lên web server / GitHub Pages
txapm source add myrepo https://my-server.com/api/txa/v1
```

Kiểm tra và cài thử:

```powershell
txapm search chrome -s myrepo
txapm install chrome -s myrepo
```

---

## Các tham số hỗ trợ

| Tham số | Ý nghĩa | Mặc định |
|---|---|---|
| `[packages ...]` | Một hoặc nhiều WinGet Package ID (vd `7zip.7zip`, `Git.Git`) | |
| `-s`, `--search <từ-khóa>` | Tìm kiếm package trên WinGet | |
| `-n`, `--namespace <tên>` | Namespace cho TXA package | `TXAVL` |
| `--id <ID-cụ-thể>` | Đặt tên Package ID riêng (vd `TXAVL.Chrome`) | Tự sinh |
| `-o`, `--out <thư-mục>` | Thư mục chứa manifests đầu ra | `./manifests` |
| `--build-api` | Tự động chạy `txaci build-api` sau khi import | |
| `--api-out <thư-mục>` | Thư mục chứa API tĩnh đầu ra | `dist/api` |
| `--dry-run` | Xem trước, không ghi file | |

