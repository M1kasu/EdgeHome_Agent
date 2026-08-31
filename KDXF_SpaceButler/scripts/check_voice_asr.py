"""Probe real streaming ASR without ever calling the Agent or controlling devices.

With --wav, sends a local mono PCM16/16kHz WAV in real time. Without it, sends
one second of silence to verify authentication and the end-of-stream protocol.
Credentials are loaded server-side; neither headers nor audio are logged.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spacebutler.voice import CHUNK_BYTES, VoiceConfig, VoiceError, VoiceService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", type=Path)
    parser.add_argument("--second-pass", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args()
    if args.wav:
        with wave.open(str(args.wav), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, "NONE"):
                parser.error("WAV must be mono PCM16, 16000 Hz")
            if wav.getnframes() > 16000 * 90:
                parser.error("WAV must be at most 90 seconds")
            audio = wav.readframes(wav.getnframes())
    else:
        audio = bytes(32000)
    config = VoiceConfig.from_env()
    if args.second_pass is not None:
        config = replace(config, enable_nonstream=args.second_pass)
    service = VoiceService(config)
    connected = False
    try:
        created = service.create()
        connected = True
        print(json.dumps({"connected": True, "request_id": created["request_id"],
                          "resource_id": config.resource_id, "enable_nonstream": config.enable_nonstream}), flush=True)
        session = service.get(created["session_id"])
        started = time.monotonic()
        partial_count = 0
        previous_text = ""
        for sequence, offset in enumerate(range(0, len(audio), CHUNK_BYTES)):
            session.send_audio(audio[offset:offset + CHUNK_BYTES], sequence)
            time.sleep(0.2)
            partial = session.snapshot()
            if partial["text"] and partial["text"] != previous_text:
                previous_text = partial["text"]
                partial_count += 1
                print(json.dumps({"partial": previous_text, "elapsed_seconds": round(time.monotonic() - started, 2)},
                                 ensure_ascii=False), flush=True)
        released = time.monotonic()
        result = session.finish()
        result.update(partial_updates=partial_count, final_wait_seconds=round(time.monotonic() - released, 3))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result["state"] == "final" else 1
    except VoiceError as error:
        print(json.dumps({"connected": connected, "error": str(error)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
