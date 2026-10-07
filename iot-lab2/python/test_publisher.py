"""Gửi dữ liệu mẫu để kiểm tra validation, outlier và duplicate."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")

MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "ptit/iot/nhom15")
DEVICE_ID = "test-device"
TOPIC = f"{BASE_TOPIC}/{DEVICE_ID}/telemetry"


def make_payload(seq: int, temperature: float | None = 28.0) -> dict[str, object]:
    return {
        "device_id": DEVICE_ID,
        "seq": seq,
        "ts": int(time.time() * 1000),
        "uptime_s": seq * 10,
        "temperature": temperature,
        "humidity": 65.0,
        "distance_cm": 120.0,
        "light_lux": 500.0,
        "rssi": -45,
    }


def main() -> None:
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="iot-lab2-test")
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()

    test_cases: list[tuple[str, dict[str, object]]] = []
    valid = make_payload(1)
    duplicate = dict(valid)
    invalid_range = make_payload(2, temperature=999.0)
    missing_value = make_payload(3, temperature=None)
    recovered = make_payload(4, temperature=29.0)
    test_cases.extend(
        [
            ("hợp lệ", valid),
            ("trùng seq + ts", duplicate),
            ("nhiệt độ ngoài miền", invalid_range),
            ("thiếu nhiệt độ", missing_value),
            ("hợp lệ sau lỗi", recovered),
        ]
    )

    print(f"Broker: {MQTT_HOST}:{MQTT_PORT}")
    print(f"Topic : {TOPIC}")
    for label, payload in test_cases:
        info = client.publish(TOPIC, json.dumps(payload), qos=1)
        info.wait_for_publish()
        print(f"Đã gửi [{label}]: {json.dumps(payload, ensure_ascii=False)}")
        time.sleep(1)

    client.loop_stop()
    client.disconnect()
    print("Hoàn tất. Kiểm tra terminal mqtt_to_influx.py và Grafana.")


if __name__ == "__main__":
    main()
