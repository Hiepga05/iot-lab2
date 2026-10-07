"""MQTT gateway: validate Wokwi telemetry and store it in InfluxDB 2.x."""

import json
import math
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

MQTT_HOST = os.getenv("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "ptit/iot/nhom15")
MQTT_TELEMETRY_TOPIC = os.getenv(
    "MQTT_TELEMETRY_TOPIC", f"{MQTT_BASE_TOPIC}/+/telemetry"
)
MQTT_STATUS_TOPIC = os.getenv(
    "MQTT_STATUS_TOPIC", f"{MQTT_BASE_TOPIC}/+/status"
)
MQTT_PONG_TOPIC = os.getenv("MQTT_PONG_TOPIC", f"{MQTT_BASE_TOPIC}/+/pong")
MQTT_DEVICE_ID = os.getenv("MQTT_DEVICE_ID", "esp32-01")

INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN")
INFLUX_ORG = os.getenv("INFLUX_ORG", "iot_lab")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "iot_bucket")

if not INFLUX_TOKEN:
    raise SystemExit("Thiếu INFLUX_TOKEN trong file .env")

influx_client = InfluxDBClient(
    url=INFLUX_URL,
    token=INFLUX_TOKEN,
    org=INFLUX_ORG,
)
write_api = influx_client.write_api(write_options=SYNCHRONOUS)

stats = defaultdict(int)
last_sequence = {}
recent_packets = defaultdict(lambda: deque(maxlen=300))
pending_pings = {}
stop_event = threading.Event()


def optional_number(data, name):
    """Return a finite float or None for a JSON null/missing field."""
    value = data.get(name)
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def validate_telemetry(data):
    """Validate structure, types, ranges, and business rules."""
    required = ["device_id", "seq", "ts", "uptime_s"]
    missing = [name for name in required if name not in data]
    if missing:
        raise ValueError("Thiếu trường: " + ", ".join(missing))

    clean = {
        "device_id": str(data["device_id"]).strip(),
        "seq": int(data["seq"]),
        "ts": int(data["ts"]),
        "uptime_s": int(data["uptime_s"]),
        "temperature": optional_number(data, "temperature"),
        "humidity": optional_number(data, "humidity"),
        "distance_cm": optional_number(data, "distance_cm"),
        "light_lux": optional_number(data, "light_lux"),
        "rssi": optional_number(data, "rssi"),
    }

    if not clean["device_id"]:
        raise ValueError("device_id rỗng")
    if clean["seq"] < 0 or clean["uptime_s"] < 0:
        raise ValueError("seq hoặc uptime_s âm")

    ranges = {
        "temperature": (-40, 85),
        "humidity": (0, 100),
        "distance_cm": (2, 400),
        "light_lux": (0, 200000),
        "rssi": (-120, 0),
    }
    invalid_fields = []
    for field, (minimum, maximum) in ranges.items():
        value = clean[field]
        if value is not None and not minimum <= value <= maximum:
            invalid_fields.append(field)

    if invalid_fields:
        raise ValueError("Ngoài miền hợp lệ: " + ", ".join(invalid_fields))

    if all(clean[name] is None for name in ("temperature", "humidity", "distance_cm", "light_lux")):
        raise ValueError("Tất cả giá trị cảm biến đều thiếu")

    return clean


def write_rejected(topic, payload, reason):
    point = (
        Point("rejected_messages")
        .tag("topic", topic)
        .field("reason", str(reason))
        .field("payload", payload[:1500])
        .time(datetime.now(timezone.utc), WritePrecision.MS)
    )
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)


def write_metrics(device_id, **fields):
    point = Point("ingest_metrics").tag("device_id", device_id)
    for name, value in fields.items():
        if value is not None:
            point.field(name, value)
    point.time(datetime.now(timezone.utc), WritePrecision.MS)
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)


def handle_telemetry(topic, payload_text):
    received_ms = int(time.time() * 1000)
    raw = json.loads(payload_text)
    data = validate_telemetry(raw)

    device_id = data["device_id"]
    packet_key = (data["seq"], data["ts"])
    duplicate = packet_key in recent_packets[device_id]
    if duplicate:
        stats["duplicate_total"] += 1
        write_metrics(
            device_id,
            duplicate_total=int(stats["duplicate_total"]),
            duplicate_detected=1,
        )
        print(f"TRÙNG GÓI: device={device_id}, seq={data['seq']}")
        return
    recent_packets[device_id].append(packet_key)

    lost_now = 0
    previous = last_sequence.get(device_id)
    if previous is not None and data["seq"] > previous + 1:
        lost_now = data["seq"] - previous - 1
        stats["lost_total"] += lost_now
    if previous is None or data["seq"] > previous:
        last_sequence[device_id] = data["seq"]

    device_ts = data["ts"]
    network_delay_ms = None
    if device_ts > 0:
        network_delay_ms = float(received_ms - device_ts)
        point_time = datetime.fromtimestamp(device_ts / 1000, tz=timezone.utc)
    else:
        point_time = datetime.now(timezone.utc)

    point = (
        Point("sensor_raw")
        .tag("device_id", device_id)
        .tag("source", "wokwi")
        .field("seq", int(data["seq"]))
        .field("uptime_s", int(data["uptime_s"]))
    )
    for field in ("temperature", "humidity", "distance_cm", "light_lux", "rssi"):
        if data[field] is not None:
            point.field(field, float(data[field]))
    if network_delay_ms is not None:
        point.field("network_delay_ms", network_delay_ms)
    point.time(point_time, WritePrecision.MS)

    start = time.perf_counter()
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    database_write_ms = (time.perf_counter() - start) * 1000

    stats["valid_total"] += 1
    write_metrics(
        device_id,
        valid_total=int(stats["valid_total"]),
        rejected_total=int(stats["rejected_total"]),
        duplicate_total=int(stats["duplicate_total"]),
        lost_total=int(stats["lost_total"]),
        lost_detected=int(lost_now),
        network_delay_ms=network_delay_ms,
        database_write_ms=float(database_write_ms),
    )

    print(
        f"ĐÃ LƯU device={device_id} seq={data['seq']} "
        f"delay={network_delay_ms} ms db={database_write_ms:.2f} ms"
    )


def handle_status(topic, payload_text):
    parts = topic.split("/")
    device_id = parts[-2] if len(parts) >= 2 else "unknown"
    point = (
        Point("device_status")
        .tag("device_id", device_id)
        .field("status", payload_text.strip())
        .time(datetime.now(timezone.utc), WritePrecision.MS)
    )
    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    print(f"TRẠNG THÁI {device_id}: {payload_text.strip()}")


def handle_pong(topic, payload_text):
    parts = topic.split("/")
    device_id = parts[-2] if len(parts) >= 2 else "unknown"
    try:
        data = json.loads(payload_text)
        ping_id = str(data.get("ping_id", ""))
        sent_ms = int(data.get("sent_ms", pending_pings.get(ping_id, 0)))
    except (json.JSONDecodeError, TypeError, ValueError):
        return
    if sent_ms <= 0:
        return
    rtt_ms = float(int(time.time() * 1000) - sent_ms)
    pending_pings.pop(ping_id, None)
    write_metrics(device_id, rtt_ms=rtt_ms)
    print(f"RTT {device_id}: {rtt_ms:.2f} ms")


def on_connect(client, userdata, flags, reason_code, properties):
    print(f"MQTT connect: {reason_code}")
    if reason_code == 0:
        client.subscribe(
            [
                (MQTT_TELEMETRY_TOPIC, 0),
                (MQTT_STATUS_TOPIC, 0),
                (MQTT_PONG_TOPIC, 0),
            ]
        )
        print(f"Subscribe: {MQTT_TELEMETRY_TOPIC}")


def on_message(client, userdata, message):
    payload_text = message.payload.decode("utf-8", errors="replace")
    try:
        if message.topic.endswith("/telemetry"):
            handle_telemetry(message.topic, payload_text)
        elif message.topic.endswith("/status"):
            handle_status(message.topic, payload_text)
        elif message.topic.endswith("/pong"):
            handle_pong(message.topic, payload_text)
    except (json.JSONDecodeError, ValueError, TypeError) as error:
        stats["rejected_total"] += 1
        print(f"LOẠI [{message.topic}]: {error}")
        try:
            write_rejected(message.topic, payload_text, error)
        except Exception as db_error:
            print(f"Không ghi được rejected_messages: {db_error}")
    except Exception as error:
        print(f"LỖI HỆ THỐNG [{message.topic}]: {error}")


def ping_worker(client):
    ping_topic = f"{MQTT_BASE_TOPIC}/{MQTT_DEVICE_ID}/ping"
    while not stop_event.wait(30):
        sent_ms = int(time.time() * 1000)
        ping_id = str(sent_ms)
        pending_pings[ping_id] = sent_ms
        payload = json.dumps({"ping_id": ping_id, "sent_ms": sent_ms})
        client.publish(ping_topic, payload)


mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"python-gateway-{int(time.time())}",
)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

try:
    print(f"Đang kết nối MQTT {MQTT_HOST}:{MQTT_PORT}")
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=90)
    threading.Thread(target=ping_worker, args=(mqtt_client,), daemon=True).start()
    mqtt_client.loop_forever()
except KeyboardInterrupt:
    print("\nDừng gateway")
finally:
    stop_event.set()
    mqtt_client.disconnect()
    write_api.close()
    influx_client.close()
