import json
import os
import sys
from dataclasses import dataclass

import paho.mqtt.client as mqtt


@dataclass
class MqttSettings:
    host: str
    port: int
    username: str | None
    password: str | None
    client_id: str
    optional: bool


class MqttService:
    def __init__(self, settings: MqttSettings):
        self._settings = settings
        self._client = mqtt.Client(client_id=settings.client_id, clean_session=True)
        if settings.username:
            self._client.username_pw_set(settings.username, settings.password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._connected = False

    def _on_connect(self, client, userdata, flags, rc):
        self._connected = rc == 0

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False

    def connect(self) -> bool:
        try:
            self._client.connect(self._settings.host, self._settings.port, keepalive=60)
            self._client.loop_start()
            return True
        except Exception as exc:
            self._connected = False
            sys.stderr.write(
                "[missioncontrol-api] mqtt_connect_failed "
                f"host={self._settings.host} port={self._settings.port} optional={self._settings.optional} error={exc}\n"
            )
            sys.stderr.flush()
            if not self._settings.optional:
                raise RuntimeError(
                    f"MQTT connection failed for {self._settings.host}:{self._settings.port} and MQTT_OPTIONAL is false"
                ) from exc
            return False

    def disconnect(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        finally:
            self._connected = False

    def publish(self, topic: str, payload: dict, qos: int = 1) -> bool:
        if not self._connected:
            return False
        body = json.dumps(payload, separators=(",", ":"))
        info = self._client.publish(topic, payload=body, qos=qos)
        return info.rc == mqtt.MQTT_ERR_SUCCESS


def build_mqtt_service() -> MqttService | None:
    host = os.getenv("MQTT_HOST")
    if not host:
        return None
    port_raw = os.getenv("MQTT_PORT", "1883")
    try:
        port = int(port_raw)
    except ValueError:
        port = 1883
    settings = MqttSettings(
        host=host,
        port=port,
        username=os.getenv("MQTT_USERNAME"),
        password=os.getenv("MQTT_PASSWORD"),
        client_id=os.getenv("MQTT_CLIENT_ID", "missioncontrol-api"),
        optional=_as_bool(os.getenv("MQTT_OPTIONAL"), default=True),
    )
    return MqttService(settings)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
