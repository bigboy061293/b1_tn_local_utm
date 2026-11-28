"""Cầu MQTT 5 → I-07 (kênh ưu tiên 2). B1 đăng ký `b1/v1/telemetry/+` trên hệ thống truyền bản tin (D2), QoS 1.

Tải là một `TelemetrySample` hoặc `{"samples": [...]}` dạng JSON. Máy chủ MQTT xác thực khách bằng chứng chỉ hai chiều
và chỉ cho mỗi khách đăng lên chủ đề chuyến của mình (ACL); B1 vẫn kiểm lại lược đồ, chuyến, UAS ID, trạng thái chuyến như
kênh HTTPS. Thư viện paho-mqtt (EPL-2.0 / EDL-1.0).
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.parse
import uuid

log = logging.getLogger("b1.mqtt")
TOPIC = "b1/v1/telemetry/+"


class MqttBridge:
    def __init__(self, telemetry, url: str, client_id: str = "b1-telemetry-bridge", ca_file: str | None = None,
                 cert_file: str | None = None, key_file: str | None = None):
        import paho.mqtt.client as mqtt

        self.telemetry = telemetry
        u = urllib.parse.urlparse(url)  # mqtt://host:1883 hoặc mqtts://host:8883
        self.host, self.port = u.hostname or "localhost", u.port or (8883 if u.scheme == "mqtts" else 1883)
        self.stats = {"messages": 0, "samples": 0, "rejected": 0, "errors": 0}
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv5)
        if u.username:
            self.client.username_pw_set(urllib.parse.unquote(u.username), urllib.parse.unquote(u.password or ""))
        if u.scheme == "mqtts":
            self.client.tls_set(ca_certs=ca_file, certfile=cert_file, keyfile=key_file, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        log.info("MQTT kết nối %s:%s (%s)", self.host, self.port, reason_code)
        client.subscribe(TOPIC, qos=1)

    def _on_message(self, client, userdata, msg):
        self.stats["messages"] += 1
        try:
            ack = self.handle(msg.topic, msg.payload)
            self.stats["samples"] += ack.get("accepted", 0)
            self.stats["rejected"] += len(ack.get("rejected", []))
        except Exception as e:  # noqa: BLE001
            self.stats["errors"] += 1
            log.info("MQTT %s: %s", msg.topic, e)

    def handle(self, topic: str, payload: bytes) -> dict:
        fid = uuid.UUID(topic.rsplit("/", 1)[-1])
        body = json.loads(payload)
        samples = body["samples"] if isinstance(body, dict) and "samples" in body else [body]
        target = self.telemetry.check_target(self.telemetry.target(fid), None, backfill=False)
        return self.telemetry.ingest(target, samples[:200], "mqtt")

    def start(self) -> None:
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
