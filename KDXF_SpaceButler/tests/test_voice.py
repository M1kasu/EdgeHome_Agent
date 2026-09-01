from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import gzip
import json
from pathlib import Path
import queue
import struct
import threading
import time
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from spacebutler.voice import VoiceConfig, VoiceError, VoiceService, VoiceSession, client_packet, parse_response
from spacebutler.workbench import serve_workbench


def reply(text="", *, final=False, definite=False, compressed=True, sequence=True):
    payload = json.dumps({"result": {"text": text, "utterances": [{"text": text, "definite": definite}]}}).encode()
    if compressed:
        payload = gzip.compress(payload)
    flags = (2 if final else 0) | int(sequence)
    head = bytes((0x11, 0x90 | flags, 0x11 if compressed else 0x10, 0))
    return head + (struct.pack(">i", -2 if final else 1) if sequence else b"") + struct.pack(">I", len(payload)) + payload


class FakeSocket:
    def __init__(self, final_text="打开客厅灯，空调设为26度。", auto_final=True):
        self.incoming = queue.Queue()
        self.sent = []
        self.closed = threading.Event()
        self.released = threading.Event()
        self.final_text = final_text
        self.auto_final = auto_final

    def send(self, packet):
        self.sent.append(packet)
        if packet[1] & 2:
            self.released.set()
            if self.auto_final:
                self.incoming.put(reply(self.final_text, final=True, definite=True))

    def recv(self, timeout):
        if self.closed.is_set():
            raise OSError("closed")
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None

    def close(self):
        self.closed.set()


def wait_text(session, text):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if session.snapshot()["text"] == text:
            return
        time.sleep(0.01)
    pytest.fail("transcript did not arrive")


@pytest.mark.parametrize("compressed,sequence", [(True, True), (False, True), (True, False)])
def test_parse_partial_and_final_frames(compressed, sequence):
    data, final = parse_response(reply("打开灯", compressed=compressed, sequence=sequence))
    assert data["result"]["text"] == "打开灯" and not final
    assert parse_response(reply("打开灯。", final=True, compressed=compressed, sequence=sequence))[1]


def test_client_packets_use_correct_format_and_final_flag():
    pcm = b"\x01\x00" * 3200
    packet = client_packet(pcm, audio=True, final=True)
    assert packet[:4] == b"\x11\x22\x01\x00"
    assert struct.unpack(">I", packet[4:8])[0] == len(packet[8:])
    assert gzip.decompress(packet[8:]) == pcm


def test_protocol_errors_are_bounded_and_do_not_leak_provider_data():
    message = b"sensitive account details"
    packet = b"\x11\xf0\x10\x00" + struct.pack(">II", 45000001, len(message)) + message
    with pytest.raises(VoiceError, match="45000001") as error:
        parse_response(packet)
    assert "sensitive" not in str(error.value)
    for malformed in (b"", reply("hello")[:-1], b"\x11\x90\x10\x00"):
        with pytest.raises(VoiceError):
            parse_response(malformed)


def test_resource_permission_and_concurrency_failures_are_actionable():
    response = Response(403, "Forbidden", Headers(), body=b'{"error":"requested resource not granted"}')
    session = VoiceSession(VoiceConfig("test-secret"), Mock(side_effect=InvalidStatus(response)))
    with pytest.raises(VoiceError, match="未获授权") as error:
        session.open()
    assert "test-secret" not in str(error.value)
    message = b'{"error":"quota exceeded for types: concurrency"}'
    packet = b"\x11\xf0\x10\x00" + struct.pack(">II", 45000292, len(message)) + message
    with pytest.raises(VoiceError, match="并发额度"):
        parse_response(packet)


def test_utterance_definite_does_not_finish_session_and_final_waits_for_release():
    socket = FakeSocket()
    connector = Mock(return_value=socket)
    session = VoiceSession(VoiceConfig("test-only-key"), connector)
    session.open()
    try:
        session.send_audio(bytes(6400), 0)
        socket.incoming.put(reply("打开客厅灯。", definite=True))
        wait_text(session, "打开客厅灯。")
        assert session.snapshot()["state"] == "recording"
        assert not socket.released.is_set()
        session.send_audio(bytes(6400), 1)
        result = session.finish()
        assert result["state"] == "final"
        assert result["text"] == socket.final_text
        assert "test-only-key" not in json.dumps(result)
        assert connector.call_args.kwargs["additional_headers"]["X-Api-Key"] == "test-only-key"
        assert "test-only-key" not in repr(session._config)
    finally:
        session.cancel()


def test_empty_final_must_not_reuse_previous_partial():
    socket = FakeSocket(final_text="")
    session = VoiceSession(VoiceConfig("test-only"), lambda *a, **kw: socket)
    session.open()
    socket.incoming.put(reply("打开门"))
    wait_text(session, "打开门")
    assert session.finish()["text"] == ""
    session.cancel()


def test_cancel_during_finalization_prevents_late_result():
    socket = FakeSocket(auto_final=False)
    session = VoiceSession(VoiceConfig("test-only"), lambda *a, **kw: socket)
    session.open()
    with ThreadPoolExecutor() as pool:
        finishing = pool.submit(session.finish)
        assert socket.released.wait(1)
        session.cancel()
        socket.incoming.put(reply("打开门", final=True))
        assert finishing.result(timeout=2)["state"] == "cancelled"
        assert session.snapshot()["text"] == ""


def test_disconnect_does_not_promote_partial_to_final():
    socket = FakeSocket(auto_final=False)
    session = VoiceSession(VoiceConfig("test-only"), lambda *a, **kw: socket)
    session.open()
    socket.incoming.put(reply("打开门"))
    wait_text(session, "打开门")
    socket.close()
    assert session.finish()["state"] == "error"


def test_bad_audio_and_wrong_sequence_are_rejected():
    socket = FakeSocket()
    session = VoiceSession(VoiceConfig("test-only"), lambda *a, **kw: socket)
    session.open()
    try:
        for audio, sequence in [(b"x", 0), (bytes(6402), 0), (bytes(6400), 1)]:
            with pytest.raises(VoiceError):
                session.send_audio(audio, sequence)
        session.send_audio(bytes(6400), 0)
        with pytest.raises(VoiceError):
            session.send_audio(bytes(6400), 0)
    finally:
        session.cancel()


def test_configuration_has_env_override_and_public_status_has_no_key(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("SPACEBUTLER_ASR_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("SPACEBUTLER_ASR_API_KEY", "env-key")
    config = VoiceConfig.from_env(path)
    assert config.api_key == "env-key"
    assert "env-key" not in json.dumps(VoiceService(config).status())


def test_second_pass_is_explicitly_configurable_and_sent_upstream(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("SPACEBUTLER_ASR_API_KEY=file-key\nSPACEBUTLER_ASR_ENABLE_NONSTREAM=true\n", encoding="utf-8")
    monkeypatch.delenv("SPACEBUTLER_ASR_ENABLE_NONSTREAM", raising=False)
    assert VoiceConfig.from_env(path).enable_nonstream is True
    monkeypatch.setenv("SPACEBUTLER_ASR_ENABLE_NONSTREAM", "false")
    config = VoiceConfig.from_env(path)
    assert config.enable_nonstream is False
    socket = FakeSocket()
    session = VoiceSession(config, lambda *a, **kw: socket)
    session.open()
    try:
        params = json.loads(gzip.decompress(socket.sent[0][8:]))
        assert params["request"]["enable_nonstream"] is False
    finally:
        session.cancel()
    monkeypatch.setenv("SPACEBUTLER_ASR_ENABLE_NONSTREAM", "typo")
    with pytest.raises(VoiceError, match="必须"):
        VoiceConfig.from_env(path)
    monkeypatch.delenv("SPACEBUTLER_ASR_ENABLE_NONSTREAM")
    path.write_text("SPACEBUTLER_ASR_ENABLE_NONSTREAM=typo\n", encoding="utf-8")
    with pytest.raises(VoiceError, match="必须"):
        VoiceConfig.from_env(path)


def test_missing_config_and_concurrency_limit_fail_before_opening_connections():
    with pytest.raises(VoiceError, match="尚未配置"):
        VoiceService(VoiceConfig()).create()
    service = VoiceService(VoiceConfig("test-only"), lambda *a, **kw: FakeSocket())
    try:
        service.create()
        service.create()
        with pytest.raises(VoiceError, match="会话已满"):
            service.create()
    finally:
        service.close()


def test_http_audio_to_final_is_independent_of_agent_and_checks_origin():
    socket = FakeSocket()
    service = VoiceService(VoiceConfig("test-only"), lambda *a, **kw: socket)
    controller = Mock()
    server = serve_workbench(controller, Path(__file__).resolve().parents[1] / "workbench", port=0, voice_service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, method="GET", data=None, headers=None):
        req = Request(base + path, data=data, method=method, headers={"Origin": base, **(headers or {})})
        with urlopen(req, timeout=3) as response:
            return json.load(response)

    try:
        assert request("/api/voice/status")["configured"]
        with pytest.raises(HTTPError) as error:
            request("/api/voice/sessions", "POST", headers={"Origin": "https://evil.example"})
        assert error.value.code == 403
        with pytest.raises(HTTPError):
            with urlopen(Request(base + "/api/voice/sessions", method="POST"), timeout=3):
                pass
        session = request("/api/voice/sessions", "POST")
        path = f"/api/voice/sessions/{session['session_id']}"
        request(path + "/audio", "POST", bytes(6400), {"Content-Type": "application/octet-stream", "X-Audio-Sequence": "0"})
        socket.incoming.put(reply("打开灯。", definite=True))
        wait_text(service.get(session["session_id"]), "打开灯。")
        assert request(path)["state"] == "recording"
        assert request(path + "/finish", "POST")["state"] == "final"
        controller.chat.assert_not_called()
        assert request(path, "DELETE")["state"] == "cancelled"
        with pytest.raises(HTTPError):
            request(path)
        for asset in ("/voice-input.js", "/voice-capture.js"):
            with urlopen(base + asset, timeout=3) as response:
                assert response.status == 200
                assert response.headers["Cache-Control"] == "no-store"
        for secret_path in ("/.env", "/../.env", "/%2e%2e/.env", "/deployment/voice.local.json"):
            with pytest.raises(HTTPError):
                with urlopen(base + secret_path, timeout=3):
                    pass
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
