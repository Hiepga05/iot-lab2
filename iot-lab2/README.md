# IoT Lab 2 - Thu thập, lưu trữ và tiền xử lý dữ liệu IoT

Project mô phỏng ESP32 trên Wokwi trong VS Code, gửi dữ liệu cảm biến qua MQTT,
kiểm tra dữ liệu bằng Python, lưu vào InfluxDB 2.x, tiền xử lý và hiển thị trên Grafana.

Thông tin đang cấu hình:

- Sinh viên: `B23DCAT316`
- Nhóm topic: `nhom15`
- Thiết bị: `esp32-01`
- MQTT broker: `broker.hivemq.com:1883`
- Topic dữ liệu: `ptit/iot/nhom15/esp32-01/telemetry`
- Chu kỳ gửi: 5 giây

## 1. Cây thư mục chuẩn

```text
iot-lab2/
├── src/
│   └── main.cpp                       # Firmware ESP32
├── python/
│   ├── mqtt_to_influx.py              # MQTT subscriber, validation, lưu DB
│   ├── process_data.py                # Missing/outlier/resample/feature/scale
│   ├── test_publisher.py              # Tạo dữ liệu kiểm thử
│   └── requirements.txt
├── grafana/
│   ├── dashboards/
│   │   └── iot-lab2.json              # Dashboard tự động nạp
│   └── provisioning/
│       ├── dashboards/dashboard.yml
│       └── datasources/influxdb.yml
├── report/
│   └── NOI_DUNG_BAO_CAO.md            # Khung báo cáo 4-6 trang
├── results/
│   └── README.md                       # Nơi script tạo CSV kết quả
├── screenshots/
│   └── README.md                       # Danh sách ảnh cần chụp
├── .env                               # Cấu hình chạy trên máy
├── .env.example                       # Mẫu cấu hình để nộp/GitHub
├── .gitignore
├── diagram.json                       # Sơ đồ Wokwi
├── docker-compose.yml                 # InfluxDB + Grafana
├── platformio.ini                     # Cấu hình PlatformIO
├── wokwi.toml                         # Firmware và terminal Wokwi
└── README.md
```

Không tạo hoặc chép thư mục `.pio` từ project cũ. PlatformIO sẽ tự sinh lại thư
mục này ở lần Build đầu tiên.

## 2. Phần mềm cần có

1. VS Code.
2. Extension **PlatformIO IDE**.
3. Extension **Wokwi Simulator**.
4. Docker Desktop, chọn chế độ Linux containers.
5. Python 3.10 trở lên.

Mở đúng thư mục `iot-lab2`, tức là thư mục nhìn thấy ngay file
`platformio.ini`. Không mở thư mục cha chứa nhiều project.

## 3. Build firmware sạch

Trong thanh PlatformIO, chọn **Build**, hoặc mở terminal tại thư mục project:

```powershell
pio run
```

Sau khi thành công phải có:

```text
.pio/build/esp32dev/firmware.bin
.pio/build/esp32dev/firmware.elf
```

Nếu vẫn gặp lỗi `ar.exe: CreateProcess`, lỗi nằm ở bộ toolchain PlatformIO trên
máy chứ không phải code. Hãy đóng VS Code, mở lại, vào **PlatformIO Home >
Platforms > Espressif 32**, gỡ rồi cài lại platform, sau đó mở project mới này và
Build. Project nộp không cần chứa `.pio`.

## 4. Khởi động InfluxDB và Grafana

Mở Docker Desktop trước. Trong terminal của project chạy:

```powershell
docker compose up -d
docker compose ps
```

Hai container cần có trạng thái `Up`:

- InfluxDB: <http://localhost:8086>
- Grafana: <http://localhost:3000>

Tài khoản InfluxDB: `admin` / `Admin@123456`.
Tài khoản Grafana: `admin` / `admin` (lần đầu Grafana có thể yêu cầu đổi mật khẩu).

Bucket `iot_bucket` có retention 168 giờ, tương đương 7 ngày.

## 5. Tạo môi trường Python

Trong terminal PowerShell tại thư mục `iot-lab2`:

```powershell
py -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r python\requirements.txt
```

Mỗi lần mở terminal mới, chỉ cần chạy lại:

```powershell
.\.venv\Scripts\Activate.ps1
```

## 6. Chạy chương trình nhận MQTT

Mở một terminal riêng, kích hoạt `.venv`, rồi chạy:

```powershell
python python\mqtt_to_influx.py
```

Khi kết nối thành công sẽ thấy dòng dạng:

```text
MQTT connect: Success
Subscribe: ptit/iot/nhom15/+/telemetry
```

Giữ terminal này chạy trong suốt thí nghiệm.

## 7. Chạy ESP32 bằng Wokwi trong VS Code

1. Build PlatformIO thành công trước.
2. Nhấn `Ctrl+Shift+P`.
3. Chọn **Wokwi: Start Simulator**.
4. Quan sát mạch và tab terminal của Wokwi.

Terminal phải hiện Wi-Fi, NTP, MQTT và từng JSON đã gửi. Ví dụ:

```text
Connecting WiFi... connected
Connecting MQTT... connected
Publish topic: ptit/iot/nhom15/esp32-01/telemetry
{"device_id":"esp32-01",...} | publish=OK
```

Nếu terminal Wokwi vẫn trống, mở terminal thứ hai và dùng cổng RFC2217 đã cấu
hình trong `wokwi.toml`:

```powershell
python -m serial.tools.miniterm socket://localhost:4000 115200
```

Chỉ chạy lệnh này sau khi simulator đã khởi động.

Nhấp vào từng cảm biến trên mạch Wokwi để thay đổi nhiệt độ, độ ẩm, khoảng cách
và ánh sáng. Nên thu dữ liệu ít nhất 5-10 phút.

## 8. Kiểm tra validation và dữ liệu lỗi

Firmware đang đặt `FAULT_INJECTION 1`, nên thỉnh thoảng sẽ tự tạo:

- spike để kiểm thử IQR;
- giá trị `null` để kiểm thử missing value;
- nhiệt độ `999` để gateway loại;
- gói lặp để kiểm thử duplicate.

Có thể mở terminal thứ ba và chạy thêm bộ test cố định:

```powershell
python python\test_publisher.py
```

Terminal gateway sẽ báo `ĐÃ LƯU`, `LOẠI` hoặc `TRÙNG GÓI`.

## 9. Tiền xử lý dữ liệu

Sau khi đã có dữ liệu, chạy:

```powershell
python python\process_data.py --hours 24 --interval 10s
```

Script thực hiện:

1. Đọc measurement `sensor_raw`.
2. Sắp xếp và loại timestamp trùng.
3. Resample theo cửa sổ 10 giây.
4. Phát hiện outlier bằng quy tắc IQR 1.5.
5. Nội suy missing/outlier theo thời gian.
6. Tạo rolling mean 3 mẫu, delta và Z-score.
7. Ghi sang measurement `sensor_processed`.
8. Xuất `results/processed_data.csv` và `results/processing_summary.csv`.

## 10. Mở dashboard

Truy cập <http://localhost:3000>, đăng nhập và vào:

```text
Dashboards > IoT > IoT Lab 2 - ESP32 MQTT
```

Dashboard được nạp tự động, hiển thị:

- số gói hợp lệ, bị loại, trùng và ước lượng mất;
- nhiệt độ raw so với processed/rolling mean;
- độ ẩm, khoảng cách và ánh sáng;
- network delay, database write time và MQTT RTT.

Nếu chưa thấy đường processed, chạy lại bước 9 rồi Refresh dashboard.

## 11. Thiết kế dữ liệu InfluxDB

| Measurement | Tags | Fields chính | Mục đích |
|---|---|---|---|
| `sensor_raw` | `device_id`, `source` | sensor, seq, uptime, delay | Dữ liệu gốc hợp lệ |
| `sensor_processed` | `device_id` | clean, rolling, delta, zscore | Dữ liệu sau xử lý |
| `ingest_metrics` | `device_id` | valid/rejected/duplicate/lost, RTT | Đánh giá pipeline |
| `rejected_messages` | `topic` | reason, payload | Lưu vết bản tin lỗi |
| `device_status` | `device_id` | status | Online/offline qua LWT |

`device_id` dùng làm tag vì thường được dùng để lọc. Giá trị cảm biến dùng làm
field vì thay đổi liên tục. Cách này tránh cardinality cao không cần thiết.

## 12. Kết thúc và chạy lại

Dừng Python bằng `Ctrl+C`. Dừng container nhưng giữ dữ liệu:

```powershell
docker compose stop
```

Chạy lại:

```powershell
docker compose start
```

Chỉ khi muốn xóa toàn bộ dữ liệu thí nghiệm mới dùng `docker compose down -v`.
