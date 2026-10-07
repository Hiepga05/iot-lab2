"""Tiền xử lý dữ liệu IoT trong InfluxDB và lưu sang measurement mới.

Pipeline:
1. Đọc sensor_raw theo khoảng thời gian.
2. Khử bản ghi trùng thời gian, resample về chu kỳ đều 10 giây.
3. Phát hiện ngoại lệ bằng IQR, coi ngoại lệ là giá trị thiếu.
4. Nội suy các giá trị thiếu.
5. Tạo rolling mean, delta và giá trị chuẩn hóa Z-score.
6. Ghi kết quả vào sensor_processed và xuất CSV minh chứng.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from sklearn.preprocessing import StandardScaler


ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT_DIR / "results"
load_dotenv(ROOT_DIR / ".env")

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_lab")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "iot_bucket")

SENSOR_COLUMNS = ["temperature", "humidity", "distance_cm", "light_lux"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tiền xử lý dữ liệu IoT Lab 2")
    parser.add_argument(
        "--hours", type=int, default=24, help="Đọc dữ liệu trong N giờ gần nhất"
    )
    parser.add_argument(
        "--interval", default="10s", help="Chu kỳ resample, ví dụ: 10s hoặc 1min"
    )
    return parser.parse_args()


def load_raw_data(client: InfluxDBClient, hours: int) -> pd.DataFrame:
    flux = f'''
from(bucket: "{INFLUX_BUCKET}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r["_measurement"] == "sensor_raw")
  |> filter(fn: (r) =>
      r["_field"] == "temperature" or
      r["_field"] == "humidity" or
      r["_field"] == "distance_cm" or
      r["_field"] == "light_lux" or
      r["_field"] == "network_delay_ms" or
      r["_field"] == "rssi" or
      r["_field"] == "seq")
  |> pivot(rowKey: ["_time", "device_id"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
'''
    result = client.query_api().query_data_frame(flux)
    if isinstance(result, list):
        frames = [frame for frame in result if not frame.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return result


def iqr_mask(series: pd.Series) -> pd.Series:
    """Trả về mask True tại các điểm ngoại lệ theo quy tắc 1.5 x IQR."""
    valid = series.dropna()
    if len(valid) < 4:
        return pd.Series(False, index=series.index)

    q1 = valid.quantile(0.25)
    q3 = valid.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series(False, index=series.index)

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    return (series < lower) | (series > upper)


def process_device(
    device_frame: pd.DataFrame, device_id: str, interval: str
) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = device_frame.copy()
    frame["_time"] = pd.to_datetime(frame["_time"], utc=True)
    frame = frame.sort_values("_time")

    duplicate_count = int(frame.duplicated(subset=["_time"], keep="last").sum())
    frame = frame.drop_duplicates(subset=["_time"], keep="last").set_index("_time")

    available = [name for name in SENSOR_COLUMNS if name in frame.columns]
    if not available:
        raise ValueError(f"Thiết bị {device_id} không có trường cảm biến hợp lệ")

    numeric_columns = available + [
        name for name in ["network_delay_ms", "rssi", "seq"] if name in frame.columns
    ]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    # Chuẩn hóa dữ liệu về cùng trục thời gian. mean() cũng gộp các mẫu cùng cửa sổ.
    resampled = frame[numeric_columns].resample(interval).mean()
    missing_before = {name: int(resampled[name].isna().sum()) for name in available}

    outlier_counts: dict[str, int] = {}
    for column in available:
        mask = iqr_mask(resampled[column])
        outlier_counts[column] = int(mask.sum())
        resampled[f"{column}_outlier"] = mask.astype(int)
        resampled.loc[mask, column] = np.nan

    # Nội suy theo thời gian; ffill/bfill xử lý thiếu ở đầu hoặc cuối chuỗi.
    resampled[available] = (
        resampled[available].interpolate(method="time", limit_direction="both").ffill().bfill()
    )

    for column in available:
        resampled[f"{column}_rolling_mean"] = resampled[column].rolling(
            window=3, min_periods=1
        ).mean()
        resampled[f"{column}_delta"] = resampled[column].diff().fillna(0.0)

    complete_columns = [name for name in available if not resampled[name].isna().any()]
    if complete_columns:
        scaler = StandardScaler()
        scaled = scaler.fit_transform(resampled[complete_columns])
        for index, column in enumerate(complete_columns):
            resampled[f"{column}_zscore"] = scaled[:, index]

    resampled["device_id"] = device_id
    resampled = resampled.reset_index()

    summary: dict[str, object] = {
        "device_id": device_id,
        "raw_rows": int(len(device_frame)),
        "processed_rows": int(len(resampled)),
        "duplicate_timestamps": duplicate_count,
    }
    for column in available:
        summary[f"{column}_missing_before"] = missing_before[column]
        summary[f"{column}_outliers"] = outlier_counts[column]
    return resampled, summary


def write_processed(client: InfluxDBClient, frame: pd.DataFrame) -> int:
    write_api = client.write_api(write_options=SYNCHRONOUS)
    points: list[Point] = []

    ignored = {"_time", "device_id", "result", "table", "_start", "_stop"}
    for _, row in frame.iterrows():
        point = (
            Point("sensor_processed")
            .tag("device_id", str(row["device_id"]))
            .time(row["_time"].to_pydatetime(), WritePrecision.NS)
        )
        has_field = False
        for column, value in row.items():
            if column in ignored or pd.isna(value):
                continue
            point = point.field(column, float(value))
            has_field = True
        if has_field:
            points.append(point)

    if points:
        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=points)
    write_api.close()
    return len(points)


def main() -> None:
    args = parse_args()
    if not INFLUX_TOKEN:
        raise SystemExit("Thiếu INFLUX_TOKEN trong file .env")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Đang đọc sensor_raw trong {args.hours} giờ gần nhất...")

    with InfluxDBClient(
        url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG, timeout=30_000
    ) as client:
        raw = load_raw_data(client, args.hours)
        if raw.empty:
            raise SystemExit(
                "Chưa có dữ liệu sensor_raw. Hãy chạy mqtt_to_influx.py và Wokwi trước."
            )

        if "device_id" not in raw.columns:
            raw["device_id"] = "unknown"

        processed_frames: list[pd.DataFrame] = []
        summaries: list[dict[str, object]] = []
        for device_id, device_frame in raw.groupby("device_id"):
            processed, summary = process_device(device_frame, str(device_id), args.interval)
            processed_frames.append(processed)
            summaries.append(summary)

        final = pd.concat(processed_frames, ignore_index=True)
        point_count = write_processed(client, final)

    processed_path = RESULTS_DIR / "processed_data.csv"
    summary_path = RESULTS_DIR / "processing_summary.csv"
    final.to_csv(processed_path, index=False)
    pd.DataFrame(summaries).to_csv(summary_path, index=False)

    print(f"Đã ghi {point_count} điểm vào measurement sensor_processed")
    print(f"CSV dữ liệu: {processed_path}")
    print(f"CSV thống kê: {summary_path}")


if __name__ == "__main__":
    main()
