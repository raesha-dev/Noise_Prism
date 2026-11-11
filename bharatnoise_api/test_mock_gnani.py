import io
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .mock_gnani import create_app


def wav_bytes(seconds: float = 2.0, voiced: bool = False) -> bytes:
    sample_rate = 8000
    samples = np.zeros(int(seconds * sample_rate), dtype=np.float32)
    if voiced:
        time_axis = np.arange(len(samples)) / sample_rate
        samples = (0.25 * np.sin(2 * np.pi * 440 * time_axis)).astype(np.float32)
    stream = io.BytesIO()
    sf.write(stream, samples, sample_rate, format="WAV", subtype="PCM_16")
    return stream.getvalue()


def rest_request(client, audio: bytes | None = None, **fields):
    form = {"language_code": "hi-IN", "model": "prisma-v2.5", "format": "verbatim", **fields}
    files = {"audio_file": ("clip.wav", audio if audio is not None else wav_bytes(), "audio/wav")}
    return client.post("/stt/v3", headers={"X-API-Key-ID": "mock-key"}, data=form, files=files)


def test_rest_mock_contract_and_validation():
    with TestClient(create_app()) as client:
        missing_key = client.post(
            "/stt/v3",
            data={"language_code": "hi-IN", "model": "prisma-v2.5", "format": "verbatim"},
            files={"audio_file": ("clip.wav", wav_bytes(), "audio/wav")},
        )
        assert missing_key.status_code == 401
        assert missing_key.headers["X-Mock-Gnani"] == "1"

        invalid_model = rest_request(client, model="prisma-v9")
        assert invalid_model.status_code == 400
        assert invalid_model.json()["error_code"] == "INVALID_MODEL"
        assert invalid_model.headers["X-Mock-Gnani"] == "1"

        invalid_language = rest_request(client, language_code="xx-YY")
        assert invalid_language.status_code == 422
        assert invalid_language.json()["error_code"] == "INVALID_LANGUAGE"

        invalid_auto = rest_request(client, language_code="indic-auto", model="prisma-v2.5")
        assert invalid_auto.status_code == 422

        bad_audio = rest_request(client, b"not a decodable audio file")
        assert bad_audio.status_code == 400

        too_long = rest_request(client, wav_bytes(30.1))
        assert too_long.status_code == 400
        assert too_long.json()["error_code"] == "AUDIO_TOO_LONG"

        automatic = rest_request(client, language_code="indic-auto", model="prisma-v3.0")
        assert automatic.status_code == 200
        assert automatic.json()["detected_language_code"] is None
        assert automatic.headers["X-Mock-Gnani"] == "1"

        response = rest_request(client)
        body = response.json()
        assert response.status_code == 200
        assert set(body) == {
            "success", "request_id", "transcript", "model", "detected_language_code",
            "processing_time", "end_to_end_latency", "truncated",
        }
        assert body["success"] is True
        assert body["model"].startswith("mock-")
        assert body["transcript"].startswith("[MOCK] ")
        assert len(body["transcript"].split()[1]) == 8
        assert response.headers["X-Mock-Gnani"] == "1"


def test_rest_mock_limits_concurrent_requests():
    with TestClient(create_app(processing_delay_s=0.15)) as client, ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(rest_request, client)
        time.sleep(0.03)
        second = rest_request(client)
        first_response = first.result(timeout=3)
    assert first_response.status_code == 200
    assert second.status_code == 429
    assert second.json()["error_code"] == "RATE_LIMITED"
    assert second.headers["X-Mock-Gnani"] == "1"


def test_streaming_mock_validates_frames_and_emits_mock_transcript():
    with TestClient(create_app()) as client:
        headers = {
            "x-api-key-id": "mock-key",
            "language_code": "hi-IN",
            "model": "prisma-v3.0",
            "x-sample-rate": "8000",
        }
        with client.websocket_connect("/stt/v3/stream", headers=headers) as socket:
            connected = socket.receive_json()
            assert connected["type"] == "connected"
            assert connected["config"]["model"] == "mock-prisma-v3.0"
            socket.send_bytes(np.full(512, 7000, dtype="<i2").tobytes())
            assert socket.receive_json()["type"] == "speech_start"
            for _ in range(8):
                time.sleep(0.064)
                socket.send_bytes(bytes(1024))
            assert socket.receive_json()["type"] == "processing"
            transcript = socket.receive_json()
            assert transcript["type"] == "transcript"
            assert transcript["text"].startswith("[MOCK] segment ")


def test_streaming_mock_rejects_bad_size_and_bursts():
    headers = {"x-api-key-id": "mock-key", "language_code": "hi-IN", "x-sample-rate": "8000"}
    with TestClient(create_app()) as client:
        with client.websocket_connect("/stt/v3/stream", headers=headers) as socket:
            assert socket.receive_json()["type"] == "connected"
            socket.send_bytes(bytes(512))
            assert "exactly 1,024 bytes" in socket.receive_json()["message"]
            with pytest.raises(WebSocketDisconnect):
                socket.receive_text()

        with client.websocket_connect("/stt/v3/stream", headers=headers) as socket:
            assert socket.receive_json()["type"] == "connected"
            voice = np.full(512, 7000, dtype="<i2").tobytes()
            socket.send_bytes(voice)
            assert socket.receive_json()["type"] == "speech_start"
            socket.send_bytes(voice)
            assert "cadence was violated" in socket.receive_json()["message"]
            with pytest.raises(WebSocketDisconnect):
                socket.receive_text()
