"""Server-only Doubao streaming ASR. Audio is forwarded in memory, never saved.

Protocol: https://www.volcengine.com/docs/6561/1354869
The local HTTP transport preserves the existing workbench port and origin.
Only a released recording with an upstream final packet may become Agent input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import io
import json
from pathlib import Path
import secrets
import struct
import threading
import time
from uuid import uuid4

from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from .configuration import project_environment

ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
SAMPLE_RATE = 16000
CHUNK_BYTES = 6400  # 200 ms, mono PCM16 little-endian
MAX_SECONDS = 90
MAX_PACKET_BYTES = 1024 * 1024


class VoiceError(RuntimeError):
    """A safe, user-facing failure; never include upstream headers or credentials."""


@dataclass(frozen=True)
class VoiceConfig:
    api_key: str = field(default="", repr=False)
    resource_id: str = "volc.seedasr.sauc.duration"
    enable_nonstream: bool = False

    @classmethod
    def from_env(cls, path: Path | None = None) -> VoiceConfig:
        settings = project_environment(path)
        second_pass = settings.get("SPACEBUTLER_ASR_ENABLE_NONSTREAM", "false").strip().lower()
        if second_pass not in {"true", "false", "1", "0"}:
            raise VoiceError("SPACEBUTLER_ASR_ENABLE_NONSTREAM 必须为 true 或 false。")
        return cls(
            api_key=settings.get("SPACEBUTLER_ASR_API_KEY", "").strip(),
            resource_id=settings.get("SPACEBUTLER_ASR_RESOURCE_ID", cls.resource_id).strip(),
            enable_nonstream=second_pass in {"true", "1"},
        )


def client_packet(payload: bytes, *, audio: bool = False, final: bool = False) -> bytes:
    packed = gzip.compress(payload)
    header = bytes((0x11, (0x20 if audio else 0x10) | (2 if final else 0), 0x01 if audio else 0x11, 0))
    return header + struct.pack(">I", len(packed)) + packed


def parse_response(packet: bytes) -> tuple[dict, bool]:
    if not isinstance(packet, bytes) or len(packet) < 8 or packet[0] >> 4 != 1:
        raise VoiceError("语音服务返回了无效协议数据，请重试。")
    offset = (packet[0] & 15) * 4
    kind, flags = packet[1] >> 4, packet[1] & 15
    compression, serialization = packet[2] & 15, packet[2] >> 4
    if offset < 4 or kind not in (9, 15) or compression not in (0, 1):
        raise VoiceError("语音服务返回了不支持的协议数据。")
    code = None
    if kind == 15 or flags & 1:
        if len(packet) < offset + 4:
            raise VoiceError("语音服务响应不完整。")
        code = struct.unpack_from(">I", packet, offset)[0] if kind == 15 else None
        offset += 4
    if len(packet) < offset + 4:
        raise VoiceError("语音服务响应不完整。")
    size = struct.unpack_from(">I", packet, offset)[0]
    payload = packet[offset + 4:]
    if size != len(payload) or size > MAX_PACKET_BYTES:
        raise VoiceError("语音服务响应长度异常。")
    if kind == 15:
        # Do not relay provider error text: it may contain account data.
        labels = {45000001: "请求参数有误", 45000002: "音频为空", 45000081: "录音等待超时",
                  45000292: "并发额度不足或已占满，请检查控制台并发额度并关闭其他识别会话"}
        raise VoiceError(f"语音识别失败（{code}）：{labels.get(code, '请检查服务权限、额度或稍后重试')}。")
    try:
        if compression == 1:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                payload = stream.read(MAX_PACKET_BYTES + 1)
        if len(payload) > MAX_PACKET_BYTES or serialization != 1:
            raise ValueError("invalid payload")
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("invalid result")
    except (OSError, EOFError, ValueError) as error:
        raise VoiceError("语音识别结果无法解析。") from error
    return decoded, bool(flags & 2)


class VoiceSession:
    def __init__(self, config: VoiceConfig, connector=connect) -> None:
        self.session_id = secrets.token_urlsafe(24)
        self.request_id = str(uuid4())
        self._config = config
        self._connector = connector
        self._ws = None
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._done = threading.Event()
        self._started = self._last_audio = time.monotonic()
        self._released_at: float | None = None
        self._bytes = 0
        self._sequence = 0
        self._text = ""
        self._state = "connecting"
        self._error = ""

    def open(self) -> None:
        try:
            self._ws = self._connector(
                ENDPOINT,
                additional_headers={
                    "X-Api-Key": self._config.api_key,
                    "X-Api-Resource-Id": self._config.resource_id,
                    "X-Api-Request-Id": self.request_id,
                    "X-Api-Connect-Id": self.request_id,
                    "X-Api-Sequence": "-1",
                },
                open_timeout=10, close_timeout=1, max_size=MAX_PACKET_BYTES,
                compression=None,
            )
            params = {
                "user": {"uid": "spacebutler"},
                "audio": {"format": "pcm", "codec": "raw", "rate": SAMPLE_RATE, "bits": 16, "channel": 1},
                "request": {
                    "model_name": "bigmodel", "enable_nonstream": self._config.enable_nonstream,
                    "enable_itn": True, "enable_punc": True, "show_utterances": True,
                    "result_type": "full",
                },
            }
            self._ws.send(client_packet(json.dumps(params).encode("utf-8")))
            with self._lock:
                if self._done.is_set():
                    self._ws.close()
                    return
                self._state = "recording"
            threading.Thread(target=self._receive, name="spacebutler-asr", daemon=True).start()
        except InvalidStatus as error:
            status = error.response.status_code
            reason = bytes(error.response.body).decode("utf-8", errors="replace")
            if "requested resource not granted" in reason:
                self._fail(f"语音 Key 未获授权使用 {self._config.resource_id}，请在火山控制台开通对应流式识别资源。")
            else:
                self._fail(f"语音服务鉴权或资源访问失败（HTTP {status}），请检查语音 API Key、资源开通与额度。")
            raise VoiceError(self._error) from None
        except Exception:
            self._fail("无法连接语音服务，请检查网络、代理和服务配置。")
            raise VoiceError(self._error) from None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "session_id": self.session_id, "request_id": self.request_id,
                "state": self._state, "text": self._text, "error": self._error,
                "audio_seconds": round(self._bytes / (SAMPLE_RATE * 2), 2),
            }

    def send_audio(self, audio: bytes, sequence: int) -> dict:
        if not audio or len(audio) > CHUNK_BYTES or len(audio) % 2:
            raise VoiceError("音频分片必须为不超过 200ms 的 PCM16 数据。")
        with self._send_lock:
            with self._lock:
                if self._state != "recording":
                    raise VoiceError(self._error or "录音已结束，请重新按住 V。")
                if sequence != self._sequence:
                    raise VoiceError("音频分片乱序，已停止本次识别，请重试。")
                if self._bytes + len(audio) > MAX_SECONDS * SAMPLE_RATE * 2:
                    raise VoiceError("单次语音请控制在 90 秒内。")
                self._bytes += len(audio)
                self._sequence += 1
                self._last_audio = time.monotonic()
            try:
                self._ws.send(client_packet(audio, audio=True))
            except Exception:
                self._fail("音频上传中断，本次任务未提交。")
                raise VoiceError(self._error) from None
        return self.snapshot()

    def finish(self) -> dict:
        with self._send_lock:
            with self._lock:
                if self._state == "recording":
                    self._released_at = time.monotonic()
                    self._state = "finishing"
                    send_final = True
                else:
                    send_final = False
            if send_final:
                try:
                    self._ws.send(client_packet(b"", audio=True, final=True))
                except Exception:
                    self._fail("语音收尾失败，本次任务未提交。")
        if not self._done.wait(15):
            self._fail("最终识别结果等待超时，本次任务未提交。")
        return self.snapshot()

    def cancel(self) -> None:
        with self._lock:
            self._state = "cancelled"
            self._text = ""
            self._done.set()
        if self._ws is not None:
            self._ws.close()

    def _fail(self, message: str) -> None:
        with self._lock:
            if self._done.is_set():
                return
            self._error = message
            self._state = "error"
            self._done.set()
        if self._ws is not None:
            self._ws.close()

    def _receive(self) -> None:
        try:
            while not self._done.is_set():
                now = time.monotonic()
                if self._released_at is None and (now - self._last_audio > 15 or now - self._started > MAX_SECONDS + 15):
                    raise VoiceError("录音已超时，本次任务未提交。")
                if self._released_at is not None and now - self._released_at > 15:
                    raise VoiceError("最终识别结果等待超时，本次任务未提交。")
                try:
                    packet = self._ws.recv(timeout=0.5)
                except TimeoutError:
                    continue
                payload, final = parse_response(packet)
                result = payload.get("result", {})
                if isinstance(result, list):
                    result = result[0] if result else {}
                with self._lock:
                    if self._done.is_set():
                        return
                    if isinstance(result, dict) and isinstance(result.get("text"), str):
                        self._text = result["text"]
                    # definite utterances only update subtitles. No task until release AND final packet.
                    if final:
                        if self._released_at is None:
                            raise VoiceError("语音服务提前结束录音，本次任务未提交。")
                        # Never promote a previous partial transcript when the final packet has no text.
                        self._text = result.get("text", "") if isinstance(result, dict) else ""
                        self._state = "final"
                        self._done.set()
        except VoiceError as error:
            self._fail(str(error))
        except Exception:
            self._fail("语音服务连接中断，本次任务未提交。")
        finally:
            if self._ws is not None:
                self._ws.close()


class VoiceService:
    def __init__(self, config: VoiceConfig | None = None, connector=connect) -> None:
        self.config = config if config is not None else VoiceConfig.from_env()
        self._connector = connector
        self._sessions: dict[str, VoiceSession] = {}
        self._lock = threading.Lock()

    def status(self) -> dict:
        return {
            "configured": bool(self.config.api_key), "provider": "豆包流式语音识别",
            "resource_id": self.config.resource_id, "max_seconds": MAX_SECONDS,
            "sample_rate": SAMPLE_RATE,
            "enable_nonstream": self.config.enable_nonstream,
        }

    def create(self) -> dict:
        if not self.config.api_key:
            raise VoiceError("尚未配置语音 API Key，请设置服务端 SPACEBUTLER_ASR_API_KEY。")
        with self._lock:
            # Completed results live for at most 120 seconds for final polling/retries.
            self._sessions = {key: value for key, value in self._sessions.items()
                              if time.monotonic() - value._started < MAX_SECONDS + 120}
            if sum(not item._done.is_set() for item in self._sessions.values()) >= 2:
                raise VoiceError("当前录音会话已满，请结束其他页面的录音后重试。")
            session = VoiceSession(self.config, self._connector)
            self._sessions[session.session_id] = session
        try:
            session.open()
        except VoiceError:
            with self._lock:
                self._sessions.pop(session.session_id, None)
            raise
        return session.snapshot()

    def get(self, session_id: str) -> VoiceSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise VoiceError("录音会话不存在或已过期，请重新按住 V。")
        return session

    def cancel(self, session_id: str) -> dict:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.cancel()
        return {"state": "cancelled"}

    def close(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            session.cancel()
