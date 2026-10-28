# Công cụ `txacli` & Các Lệnh Mở Rộng `txapm` / `txacreate`

Toàn bộ công cụ [`scripts/winget-import.ps1`](../scripts/winget-import.ps1) và [`scripts/winget-import.py`](../scripts/winget-import.py) đã được nâng cấp thành lệnh CLI toàn cục **`txacli`** (và tích hợp trực tiếp vào **`txapm`** và **`txacreate`**), có thể chạy nhanh ở bất kỳ thư mục nào mà không cần gọi script PowerShell.

---

## 1. Lệnh `txacli` (Thay thế hoàn toàn `winget-import.ps1`)

### Tìm kiếm phần mềm trên WinGet
```powershell
txacli -s "PowerToys"
txacli -s "Microsoft.PowerToys"
# Hoặc dùng subcommand:
txacli search "GitHub CLI"
```

### Quét & Tự động Import phần mềm đang cài trên máy (`-i` / `scan`)
```powershell
# Chỉ xem danh sách phần mềm đang cài trên máy có sẵn trên WinGet (chưa có trong TXA)
txacli scan

# Quét và tự động import tất cả vào kho manifests/
txacli -i -y

# Chạy chế độ chọn từng số (1,3,5 hoặc all)
txacli -i
```

### Import nhanh bất kỳ phần mềm nào theo Tên hoặc WinGet ID
```powershell
# Import vào kho hiện tại
txacli Microsoft.PowerToys "GitHub CLI" "Steam"

# Import ra kho riêng, tự động build REST API txa.v1 và thêm luôn vào txapm source
txacli Postman.Postman -o C:\my-txa-repo --build-api --api-out C:\my-txa-api --add-source myrepo
```

### Submit PR từ thư mục Release, thư mục Repo gốc hoặc thư mục API (`submit`)
`txacreate submit` và `txacli submit` hiện hỗ trợ truyền trực tiếp **thư mục gốc của repo** (`C:\my-txa-repo`), **thư mục API đã build** (`C:\my-txa-api`), hoặc **thư mục từng release**:
```powershell
txacli submit C:\my-txa-repo
txacli submit C:\my-txa-api
txacreate submit C:\my-txa-repo
```

### Validate & Build API nhanh
```powershell
txacli validate C:\my-txa-repo --offline
txacli build-api -r C:\my-txa-repo -o C:\my-txa-api
```

---

## 2. Các lệnh mới trên `txapm`

Ngoài các lệnh cơ bản (`install`, `upgrade`, `uninstall`, `list`, `search`, `show`, `source`), `txapm` đã được bổ sung các lệnh tiện ích mới:

### `txapm winget` (viết tắt: `txapm wg`)
Làm việc trực tiếp với kho WinGet ngay trong `txapm`:
```powershell
# Tìm gói trên WinGet
txapm wg search "PowerToys"

# Cài đặt trực tiếp một gói từ WinGet thông qua cơ chế kiểm duyệt SHA-256 của txapm (kể cả khi chưa có trên kho TXA)
txapm wg install Microsoft.PowerToys

# Quét các phần mềm trên máy chưa có trong kho TXA
txapm wg scan

# Import gói từ WinGet vào kho local và tự gắn vào source
txapm wg import Postman.Postman -o C:\my-txa-repo --add-source myrepo
```

### `txapm doctor` (viết tắt: `txapm check` / `txapm status`)
Kiểm tra sức khỏe toàn diện hệ thống `txapm` (Windows build, kiến trúc CPU, trạng thái kết nối các `source`, WinGet CLI, tài khoản GitHub, dung lượng cache):
```powershell
txapm doctor
```

### `txapm clean` (viết tắt: `txapm cache` / `txapm clear`)
Dọn dẹp bộ nhớ đệm (các file bộ cài tải tạm và file log cài đặt):
```powershell
txapm clean          # Xoá các bộ cài tải tạm trong thư mục downloads
txapm clean --all    # Xoá cả bộ cài tạm lẫn toàn bộ file log cài đặt
```

### `txapm log` (viết tắt: `txapm logs`)
Xem danh sách hoặc đọc nội dung log cài đặt của từng phần mềm:
```powershell
txapm log                  # Liệt kê các file log cài đặt gần nhất
txapm log Postman -n 40    # Xem 40 dòng cuối log cài đặt của Postman
```

### `txapm which` (viết tắt: `txapm where`)
Xem chi tiết thông tin bản ghi cài đặt trên máy (phiên bản, kiến trúc, engine, nguồn cài, thư mục portable, PATH, đường dẫn file log):
```powershell
txapm which Git
```
