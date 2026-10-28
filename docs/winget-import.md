# Hướng dẫn Sử dụng `txacli`, `txapm` & `txacreate` (Dành cho Người dùng cuối)

Tài liệu này hướng dẫn người dùng cuối cách **tìm kiếm, cài đặt, quản lý phần mềm** bằng **`txapm`**, **nhập phần mềm tự động từ WinGet hoặc từ máy cá nhân** bằng **`txacli`**, và **tạo kho phần mềm riêng / đóng góp lên kho chính** bằng **`txacreate`**.

---

## Tổng quan 3 công cụ dòng lệnh

Khi cài đặt TXA, bạn có sẵn các lệnh toàn cục chạy được ở bất kỳ cửa sổ PowerShell hoặc Command Prompt nào:

| Công cụ | Dành cho việc gì? | Ví dụ nhanh |
|---|---|---|
| **`txapm`** | Tìm kiếm, cài đặt im lặng, cập nhật, gỡ phần mềm, kiểm tra sức khỏe và dọn bộ nhớ đệm | `txapm install chrome`<br>`txapm upgrade --all`<br>`txapm doctor` |
| **`txacli`** | Tìm phần mềm trên WinGet, quét phần mềm đang cài trên máy để tạo gói TXA tự động, tạo kho riêng & gửi PR | `txacli -s PowerToys`<br>`txacli -i -y`<br>`txacli submit C:\my-txa-repo` |
| **`txacreate`** | Đóng góp phần mềm lên kho TXA qua Pull Request (từ link tải, tên phần mềm hoặc thư mục có sẵn) | `txacreate new "GitHub CLI"`<br>`txacreate submit C:\my-txa-repo` |

---

## 1. Quản lý & Cài đặt phần mềm hàng ngày với `txapm`

### Tìm kiếm và cài đặt phần mềm
Bạn có thể cài bằng **ID đầy đủ** (`TXAVL.Chrome`) hoặc **tên ngắn / alias** (`chrome`, `vscode`, `7zip`, `git`...):

```powershell
# Tìm kiếm phần mềm trong kho TXA
txapm search chrome

# Cài đặt im lặng (tự tải, kiểm tra SHA-256 và cài không cần bấm Next)
txapm install chrome
txapm install vscode

# Cài đặt nhưng hiển thị thanh tiến trình của bộ cài
txapm install obs --progress
```

### Cài đặt phần mềm chưa có trên kho TXA (trực tiếp qua cầu nối WinGet)
Nếu một phần mềm chưa kịp có trên kho TXA, bạn vẫn có thể dùng `txapm wg` để lấy từ WinGet, tự động kiểm tra SHA-256 và cài qua `txapm`:

```powershell
# Tìm kiếm trên WinGet ngay trong txapm
txapm wg search "PowerToys"

# Cài đặt trực tiếp gói từ WinGet thông qua txapm
txapm wg install Microsoft.PowerToys
```

### Kiểm tra phần mềm trên máy & Cập nhật
```powershell
# Xem các gói đã cài qua txapm
txapm list

# Xem TOÀN BỘ phần mềm đang cài trên Windows (đánh dấu gói nào đã có trong TXA)
txapm list --all

# Kiểm tra và nâng cấp tất cả phần mềm lên bản mới nhất
txapm upgrade --all
```

### Các lệnh tiện ích mới trên `txapm`

| Lệnh | Viết tắt | Công dụng cho người dùng |
|---|---|---|
| `txapm doctor` | `txapm check`, `status` | Kiểm tra toàn diện: phiên bản Windows, trạng thái kết nối các kho (`source`), WinGet CLI, tài khoản GitHub và dung lượng bộ nhớ đệm |
| `txapm clean` | `txapm cache`, `clear` | Xoá các file bộ cài tải tạm trong `%TEMP%\txa` để giải phóng ổ cứng (`txapm clean --all` xoá cả log cũ) |
| `txapm log` | `txapm logs` | Xem danh sách các file log cài đặt gần nhất; gõ `txapm log <tên-app>` để đọc nội dung log khi cần kiểm tra lỗi |
| `txapm which <app>` | `txapm where <app>` | Xem chi tiết vị trí cài đặt trên máy, phiên bản, kiến trúc CPU, thư mục portable, biến `PATH` và đường dẫn file log |

---

## 2. Tự động lấy phần mềm từ máy hoặc WinGet với `txacli`

Không cần mở trình duyệt đi tìm link `.exe`/`.msi` hay tính mã SHA-256 thủ công — chỉ cần dùng **`txacli`**.

### Bước 1: Tìm kiếm phần mềm trên WinGet
Bạn có thể tìm bằng từ khóa bất kỳ hoặc mã ID chính xác:

```powershell
txacli -s "PowerToys"
txacli -s "Microsoft.PowerToys"
# Hoặc:
txacli search "Unity Hub"
```

Kết quả trả về bảng danh sách rõ ràng gồm `Id`, `Version` và `Name`:
```text
Found 1 package(s):
  Id                                         Version          Name
  ------------------------------------------------------------------------------
  Microsoft.PowerToys                        0.101.2362.0     PowerToys

To import a package, run:
  txacli Microsoft.PowerToys
```

### Bước 2: Tạo gói chuẩn TXA từ tên phần mềm bất kỳ
Chỉ cần truyền tên hiển thị (như trong `txapm list --all`) hoặc WinGet ID:

```powershell
# Tạo 1 hoặc nhiều gói cùng lúc vào thư mục manifests/
txacli Microsoft.PowerToys
txacli "GitHub CLI" "GitHub Desktop" "Steam" "Unity Hub"
```

### Bước 3: Quét toàn bộ phần mềm đang có sẵn trên máy của bạn (`scan` / `-i`)
Muốn biết trên máy tính của bạn đang cài những phần mềm nào chưa có trên kho TXA?

```powershell
# Chỉ xem danh sách các phần mềm trên máy có thể thêm vào TXA
txacli scan

# Quét và chọn từng phần mềm theo số thứ tự (ví dụ: 1,3,5 hoặc all)
txacli -i

# Quét và tự động thêm TẤT CẢ không cần hỏi lại
txacli -i -y
```

---

## 3. Tự tạo Kho Phần Mềm Riêng (`myrepo`) & Đóng góp lên Kho Chính

### Tạo kho riêng trên máy hoặc mạng nội bộ
Bạn có thể dùng `txacli` để gom các phần mềm mình thích vào một thư mục riêng (ví dụ `C:\my-txa-repo`), sinh REST API (`C:\my-txa-api`) và gắn vào `txapm` chỉ bằng **1 câu lệnh**:

```powershell
# Cách 1: Tự động tạo manifest + build API + gắn vào txapm source trong 1 lệnh duy nhất
txacli Postman.Postman -o C:\my-txa-repo --build-api --api-out C:\my-txa-api --add-source myrepo
```

Hoặc làm từng bước:
```powershell
# 1. Import gói vào thư mục kho riêng
txacli Postman.Postman -o C:\my-txa-repo --build-api --api-out C:\my-txa-api

# 2. Thêm kho riêng vào txapm
txapm source add myrepo C:\my-txa-repo

# 3. Tìm kiếm hoặc cài đặt từ kho riêng
txapm search postman -s myrepo
txapm install postman -s myrepo
```

> **Lưu ý khi dùng cờ `-s <tên-kho>`:**
> Khi bạn gõ `txapm search chrome -s myrepo`, `txapm` sẽ **chỉ tìm bên trong `myrepo`**. Nếu `myrepo` chỉ mới có mỗi `Postman`, lệnh sẽ báo không thấy trong `myrepo` và gợi ý cho bạn rằng `TXAVL.Chrome` đang nằm ở kho chính `txa`. Để tìm trên tất cả các kho cùng lúc, hãy bỏ `-s myrepo`:
> ```powershell
> txapm search chrome
> ```

### Gửi gói từ kho riêng lên kho chính TXA (`submit`)
Sau khi tạo gói ở `C:\my-txa-repo` (hoặc `C:\my-txa-api`), bạn có thể gửi Pull Request đóng góp lên kho chính bất kỳ lúc nào. Lệnh `submit` tự động tìm toàn bộ các gói nằm bên trong thư mục bạn truyền vào:

```powershell
# Truyền thẳng thư mục gốc của kho hoặc thư mục API đều được!
txacli submit C:\my-txa-repo
# Hoặc:
txacreate submit C:\my-txa-repo
txacreate submit C:\my-txa-api

# Nếu chỉ muốn kiểm tra hợp lệ (không mở Pull Request trên GitHub):
txacreate submit C:\my-txa-repo --dry-run
```

---

## 4. Bảng tra cứu tham số `txacli`

| Lệnh / Tham số | Ý nghĩa | Ví dụ |
|---|---|---|
| `txacli <tên/ID ...>` | Chuyển đổi 1 hoặc nhiều gói từ WinGet sang manifest TXA | `txacli Postman.Postman "GitHub CLI"` |
| `txacli -s <từ-khóa>` | Tìm kiếm phần mềm trên kho WinGet | `txacli -s PowerToys` |
| `txacli scan` | Liệt kê phần mềm đã cài trên máy chưa có trong TXA | `txacli scan` |
| `txacli -i [-y]` | Quét phần mềm trên máy và import tự động vào kho | `txacli -i -y` |
| `-o <thư-mục>` | Chọn thư mục chứa `manifests/` đầu ra | `txacli Git.Git -o C:\my-txa-repo` |
| `--build-api` | Tự động sinh REST API tĩnh `txa.v1` sau khi import | `txacli Git.Git --build-api` |
| `--api-out <thư-mục>` | Thư mục chứa REST API tĩnh (mặc định: `dist/api`) | `--api-out C:\my-txa-api` |
| `--add-source <tên>` | Tự động đăng ký thư mục kho vào `txapm source` | `--add-source myrepo` |
| `--submit` | Tự động mở Pull Request lên GitHub sau khi import | `txacli Postman.Postman --submit` |
| `--txacreate` | Chạy luồng `txacreate new` đầy đủ (tải file thật & kiểm tra PE) | `txacli "GitHub CLI" --txacreate` |
| `--dry-run` | Chạy thử nghiệm, kiểm tra hợp lệ nhưng không ghi file / không mở PR | `txacli Postman.Postman --dry-run` |
| `txacli submit <thư-mục>` | Mở Pull Request cho thư mục release, thư mục repo hoặc thư mục API | `txacli submit C:\my-txa-repo` |
| `txacli validate <thư-mục>` | Chạy 14 bước kiểm định cho thư mục bất kỳ | `txacli validate C:\my-txa-repo --offline` |
| `txacli build-api` | Sinh bộ REST API `txa.v1` từ thư mục repo | `txacli build-api -r C:\my-txa-repo -o C:\my-txa-api` |
