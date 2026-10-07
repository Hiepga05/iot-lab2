# Kết quả thí nghiệm

Chạy lệnh sau để sinh hai file CSV dùng làm minh chứng và lấy số liệu báo cáo:

```powershell
python python\process_data.py --hours 24 --interval 10s
```

Kết quả:

- `processed_data.csv`: dữ liệu sau resample, xử lý thiếu/outlier và tạo feature.
- `processing_summary.csv`: số dòng, số timestamp trùng, missing và outlier.

Hai file sinh tự động được bỏ qua trong Git để tránh nộp dữ liệu thử quá lớn.
