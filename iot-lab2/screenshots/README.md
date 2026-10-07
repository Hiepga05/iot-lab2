# Danh sách ảnh cần chụp để nộp bài

1. Mạch Wokwi có ESP32, DHT22, HC-SR04, LDR và LED.
2. Terminal Wokwi có JSON và dòng `publish=OK`.
3. Terminal `mqtt_to_influx.py` có `ĐÃ LƯU`, `LOẠI`, `TRÙNG GÓI` và RTT.
4. InfluxDB Data Explorer hiển thị `sensor_raw`.
5. File `processing_summary.csv` hoặc terminal chạy `process_data.py`.
6. Grafana hiển thị cả raw và processed.
7. Panel độ trễ mạng, ghi database và RTT.

Nên đặt tên lần lượt `01-wokwi.png` đến `07-latency.png` để chèn báo cáo dễ hơn.
