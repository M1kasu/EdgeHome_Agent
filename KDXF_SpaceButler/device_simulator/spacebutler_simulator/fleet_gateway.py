"""Aggregate and provision isolated SpaceButler device containers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
import secrets
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from .app import _normalize_device_definition


_LOGGER = logging.getLogger(__name__)
_RUNTIME_LABEL = "spacebutler.device-runtime"
_DEVICE_ID_LABEL = "spacebutler.device-id"
_DEVICE_SOURCE_LABEL = "spacebutler.device-source"
_DEVICE_TYPE_LABEL = "spacebutler.device-type"
_DEVICE_NAME_LABEL = "spacebutler.device-name"
_DEVICE_ROOM_LABEL = "spacebutler.device-room"
_VOLUME_LABEL = "spacebutler.state-volume"


class FleetError(RuntimeError):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class RuntimeEndpoint:
    device_id: str
    source: str
    device_type: str
    name: str
    room: str
    container_name: str
    status: str
    url: str | None
    volume_name: str | None


class DockerRuntimeCatalog:
    """Expose only allowlisted Docker operations needed by device runtimes."""

    def __init__(self) -> None:
        try:
            import docker
        except ImportError as error:
            raise RuntimeError("the Docker SDK is required by the fleet gateway") from error

        self._docker = docker
        self._client = docker.from_env()
        self._image = os.environ.get("DEVICE_RUNTIME_IMAGE", "spacebutler-device-runtime:local")
        self._network = os.environ.get("DEVICE_RUNTIME_NETWORK", "spacebutler")
        self._mqtt_host = os.environ.get("MQTT_HOST", "spacebutler-mqtt")
        self._mqtt_port = os.environ.get("MQTT_PORT", "1883")
        self._lock = threading.RLock()

    def runtimes(self) -> list[RuntimeEndpoint]:
        containers = self._client.containers.list(all=True, filters={"label": f"{_RUNTIME_LABEL}=true"})
        endpoints: list[RuntimeEndpoint] = []
        for container in containers:
            container.reload()
            labels = container.labels
            device_id = labels.get(_DEVICE_ID_LABEL, "")
            if not device_id:
                continue
            status = str(container.status)
            address = None
            if status == "running":
                network = container.attrs.get("NetworkSettings", {}).get("Networks", {}).get(self._network, {})
                ip_address = network.get("IPAddress")
                if ip_address:
                    address = f"http://{ip_address}:8090"
            endpoints.append(
                RuntimeEndpoint(
                    device_id=device_id,
                    source=labels.get(_DEVICE_SOURCE_LABEL, "configured"),
                    device_type=labels.get(_DEVICE_TYPE_LABEL, "unknown"),
                    name=labels.get(_DEVICE_NAME_LABEL, device_id),
                    room=labels.get(_DEVICE_ROOM_LABEL, "home"),
                    container_name=container.name,
                    status=status,
                    url=address,
                    volume_name=labels.get(_VOLUME_LABEL),
                )
            )
        return sorted(endpoints, key=lambda item: item.device_id)

    def require(self, device_id: str) -> RuntimeEndpoint:
        endpoint = next((item for item in self.runtimes() if item.device_id == device_id), None)
        if endpoint is None:
            raise FleetError(HTTPStatus.NOT_FOUND, f"unknown device: {device_id}")
        return endpoint

    def create(self, payload: dict[str, Any]) -> RuntimeEndpoint:
        device_id, definition = _normalize_device_definition(payload)
        with self._lock:
            if any(item.device_id == device_id for item in self.runtimes()):
                raise FleetError(HTTPStatus.CONFLICT, f"device already exists: {device_id}")

            volume_name = f"spacebutler-device-data-{device_id}"
            container_name = f"spacebutler-device-{device_id}"
            labels = {
                _RUNTIME_LABEL: "true",
                _DEVICE_ID_LABEL: device_id,
                _DEVICE_SOURCE_LABEL: "runtime",
                _DEVICE_TYPE_LABEL: str(definition["type"]),
                _DEVICE_NAME_LABEL: str(definition["name"]),
                _DEVICE_ROOM_LABEL: str(definition["room"]),
                _VOLUME_LABEL: volume_name,
            }
            volume = self._client.volumes.create(name=volume_name, labels=labels)
            try:
                self._client.containers.run(
                    self._image,
                    detach=True,
                    name=container_name,
                    hostname=device_id.replace("_", "-"),
                    network=self._network,
                    environment={
                        "MQTT_HOST": self._mqtt_host,
                        "MQTT_PORT": self._mqtt_port,
                        "DEVICE_ID": device_id,
                        "DEVICE_SECRET": secrets.token_urlsafe(24),
                        "DEVICE_DEFINITION_JSON": json.dumps(
                            {**definition, "device_id": device_id},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "STATE_DB": "/app/data/device_state.db",
                        "ADMIN_HOST": "0.0.0.0",
                        "ADMIN_PORT": "8090",
                    },
                    labels=labels,
                    volumes={volume.name: {"bind": "/app/data", "mode": "rw"}},
                    restart_policy={"Name": "unless-stopped"},
                    mem_limit="96m",
                    nano_cpus=250_000_000,
                    pids_limit=64,
                    read_only=True,
                    tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                )
            except Exception:
                try:
                    volume.remove(force=True)
                except Exception:
                    pass
                raise
        return self._wait_for_runtime(device_id)

    def remove(self, device_id: str) -> dict[str, str]:
        with self._lock:
            endpoint = self.require(device_id)
            if endpoint.source != "runtime":
                raise FleetError(HTTPStatus.BAD_REQUEST, "configured seed devices cannot be deleted")
            container = self._client.containers.get(endpoint.container_name)
            if endpoint.url is not None:
                try:
                    _runtime_request(endpoint.url, "DELETE", f"/admin/devices/{device_id}")
                except (FleetError, OSError):
                    pass
            container.remove(force=True)
            if endpoint.volume_name:
                try:
                    self._client.volumes.get(endpoint.volume_name).remove(force=True)
                except self._docker.errors.NotFound:
                    pass
            return {"device_id": device_id, "container": endpoint.container_name}

    def _wait_for_runtime(self, device_id: str, timeout_seconds: float = 30) -> RuntimeEndpoint:
        deadline = time.monotonic() + timeout_seconds
        last_error = "runtime did not become ready"
        while time.monotonic() < deadline:
            try:
                endpoint = self.require(device_id)
                if endpoint.url is not None:
                    _runtime_request(endpoint.url, "GET", "/health", timeout=2)
                    return endpoint
            except (FleetError, OSError) as error:
                last_error = str(error)
            time.sleep(0.25)
        raise FleetError(HTTPStatus.GATEWAY_TIMEOUT, f"{device_id}: {last_error}")


class FleetGateway:
    def __init__(self, catalog: DockerRuntimeCatalog) -> None:
        self._catalog = catalog

    def health(self) -> dict[str, Any]:
        runtimes = self._catalog.runtimes()
        running = sum(item.url is not None for item in runtimes)
        healthy = 0
        with ThreadPoolExecutor(max_workers=max(1, min(len(runtimes), 16))) as executor:
            futures = [
                executor.submit(_runtime_request, item.url, "GET", "/health", timeout=2)
                for item in runtimes
                if item.url is not None
            ]
            for future in as_completed(futures):
                try:
                    future.result()
                except (FleetError, OSError):
                    continue
                healthy += 1
        return {
            "status": "ok" if runtimes and healthy == len(runtimes) else "degraded",
            "runtime_total": len(runtimes),
            "runtime_running": running,
            "runtime_healthy": healthy,
            "isolation": "one-device-one-container",
        }

    def devices(self) -> dict[str, Any]:
        runtimes = self._catalog.runtimes()
        records: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(len(runtimes), 16))) as executor:
            futures = {
                executor.submit(self._runtime_devices, endpoint): endpoint
                for endpoint in runtimes
                if endpoint.url is not None
            }
            for future in as_completed(futures):
                endpoint = futures[future]
                try:
                    records.extend(future.result())
                except (FleetError, OSError):
                    records.append(_offline_record(endpoint))
        online_ids = {str(item.get("device_id")) for item in records}
        records.extend(_offline_record(item) for item in runtimes if item.device_id not in online_ids)
        records.sort(key=lambda item: (str(item.get("room", "")), str(item.get("name", ""))))
        return {"devices": records, "runtime_total": len(runtimes)}

    def events(self, limit: int) -> dict[str, Any]:
        runtimes = [item for item in self._catalog.runtimes() if item.url is not None]
        events: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(len(runtimes), 16))) as executor:
            futures = [executor.submit(_runtime_request, item.url, "GET", f"/events?limit={limit}") for item in runtimes]
            for future in as_completed(futures):
                try:
                    payload = future.result()
                except (FleetError, OSError):
                    continue
                items = payload.get("events", [])
                if isinstance(items, list):
                    events.extend(item for item in items if isinstance(item, dict))
        events.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return {"events": events[:limit]}

    def create_device(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = self._catalog.create(payload)
        response = _runtime_request(endpoint.url, "GET", f"/devices/{endpoint.device_id}")
        return _with_runtime(response, endpoint)

    def remove_device(self, device_id: str) -> dict[str, Any]:
        return {"removed": self._catalog.remove(device_id)}

    def forward(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        device_id = _device_id_from_path(path)
        endpoint = self._catalog.require(device_id)
        if endpoint.url is None:
            raise FleetError(HTTPStatus.SERVICE_UNAVAILABLE, f"device runtime is {endpoint.status}: {device_id}")
        response = _runtime_request(endpoint.url, method, path, payload)
        return _with_runtime(response, endpoint)

    @staticmethod
    def _runtime_devices(endpoint: RuntimeEndpoint) -> list[dict[str, Any]]:
        if endpoint.url is None:
            return [_offline_record(endpoint)]
        payload = _runtime_request(endpoint.url, "GET", "/devices")
        devices = payload.get("devices", [])
        if not isinstance(devices, list):
            raise FleetError(HTTPStatus.BAD_GATEWAY, "device runtime returned an invalid inventory")
        return [_with_runtime(item, endpoint) for item in devices if isinstance(item, dict)]


def _runtime_request(
    base_url: str | None,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 10,
) -> dict[str, Any]:
    if base_url is None:
        raise FleetError(HTTPStatus.SERVICE_UNAVAILABLE, "device runtime is not running")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"} if body is not None else {}
    try:
        with urlopen(Request(f"{base_url}{path}", data=body, headers=headers, method=method), timeout=timeout) as response:
            decoded = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        try:
            decoded_error = json.loads(detail)
        except json.JSONDecodeError:
            decoded_error = {}
        message = decoded_error.get("error") if isinstance(decoded_error, dict) else None
        raise FleetError(HTTPStatus(error.code), str(message or f"device runtime returned HTTP {error.code}")) from error
    except URLError as error:
        raise FleetError(HTTPStatus.BAD_GATEWAY, f"device runtime is unreachable: {error.reason}") from error
    if not isinstance(decoded, dict):
        raise FleetError(HTTPStatus.BAD_GATEWAY, "device runtime returned a non-object response")
    return decoded


def _device_id_from_path(path: str) -> str:
    parsed_path = urlparse(path).path
    if parsed_path.startswith("/devices/"):
        suffix = parsed_path.removeprefix("/devices/")
    elif parsed_path.startswith("/admin/devices/"):
        suffix = parsed_path.removeprefix("/admin/devices/")
    else:
        raise FleetError(HTTPStatus.NOT_FOUND, "not_found")
    device_id = unquote(suffix.split("/", 1)[0])
    if not device_id:
        raise FleetError(HTTPStatus.NOT_FOUND, "not_found")
    return device_id


def _with_runtime(record: dict[str, Any], endpoint: RuntimeEndpoint) -> dict[str, Any]:
    return {
        **record,
        "runtime": {
            "container": endpoint.container_name,
            "status": endpoint.status,
            "isolation": "dedicated",
        },
    }


def _offline_record(endpoint: RuntimeEndpoint) -> dict[str, Any]:
    domains = {
        "light": "light",
        "curtain": "cover",
        "climate": "climate",
        "switch": "switch",
        "presence": "binary_sensor",
        "contact": "binary_sensor",
        "illuminance": "sensor",
    }
    legacy_ids = {
        "living_room_main_light": "living_room_main",
        "living_room_curtain": "living_room_curtain",
        "living_room_ac": "living_room_ac",
        "living_room_tv": "living_room_tv",
    }
    object_id = legacy_ids.get(endpoint.device_id, endpoint.device_id)
    domain = domains.get(endpoint.device_type, "sensor")
    return _with_runtime(
        {
            "device_id": endpoint.device_id,
            "type": endpoint.device_type,
            "name": endpoint.name,
            "room": endpoint.room,
            "entity_id": f"{domain}.spacebutler_{object_id}",
            "feedback_entity_id": f"sensor.spacebutler_{object_id}_feedback",
            "state": {},
            "online": False,
            "source": endpoint.source,
            "fault_mode": "container_unavailable",
        },
        endpoint,
    )


def _handler(gateway: FleetGateway) -> type[BaseHTTPRequestHandler]:
    class GatewayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/health":
                    self._write_json(HTTPStatus.OK, gateway.health())
                elif parsed.path == "/devices":
                    self._write_json(HTTPStatus.OK, gateway.devices())
                elif parsed.path == "/events":
                    limit = min(max(int(parse_qs(parsed.query).get("limit", ["100"])[0]), 1), 500)
                    self._write_json(HTTPStatus.OK, gateway.events(limit))
                elif parsed.path.startswith("/devices/"):
                    self._write_json(HTTPStatus.OK, gateway.forward("GET", self.path))
                else:
                    raise FleetError(HTTPStatus.NOT_FOUND, "not_found")
            except (FleetError, ValueError) as error:
                self._write_error(error)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/admin/devices":
                    self._write_json(HTTPStatus.CREATED, gateway.create_device(self._read_json()))
                elif parsed.path.startswith("/admin/devices/"):
                    self._write_json(HTTPStatus.OK, gateway.forward("POST", self.path, self._read_json()))
                else:
                    raise FleetError(HTTPStatus.NOT_FOUND, "not_found")
            except (FleetError, ValueError) as error:
                self._write_error(error)

        def do_DELETE(self) -> None:  # noqa: N802
            try:
                device_id = _device_id_from_path(self.path)
                self._write_json(HTTPStatus.OK, gateway.remove_device(device_id))
            except (FleetError, ValueError) as error:
                self._write_error(error)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _write_error(self, error: Exception) -> None:
            status = error.status if isinstance(error, FleetError) else HTTPStatus.BAD_REQUEST
            self._write_json(status, {"error": str(error)})

        def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            _LOGGER.info("fleet-gateway %s - %s", self.address_string(), format % args)

    return GatewayHandler


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    gateway = FleetGateway(DockerRuntimeCatalog())
    host = os.environ.get("ADMIN_HOST", "0.0.0.0")
    port = int(os.environ.get("ADMIN_PORT", "8090"))
    server = ThreadingHTTPServer((host, port), _handler(gateway))
    _LOGGER.info("Fleet Gateway listening on %s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
