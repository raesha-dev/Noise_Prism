from types import SimpleNamespace

import pytest

try:
    from . import gnani_client
except ImportError:
    import gnani_client

FatalAPIError = gnani_client.FatalAPIError
GnaniClient = gnani_client.GnaniClient


def test_synthesize_sends_documented_multilingual_wav_request(monkeypatch):
    calls = []
    audio = b"RIFF" + b"\x00" * 8 + b"WAVE"
    monkeypatch.setenv("GNANI_BASE_URL", "https://example.test/stt/v3")
    monkeypatch.setattr(
        gnani_client.requests,
        "post",
        lambda *args, **kwargs: (
            calls.append((args, kwargs))
            or SimpleNamespace(
                status_code=200,
                ok=True,
                headers={"Content-Type": "audio/wav"},
                content=audio,
            )
        ),
    )
    client = GnaniClient(api_key="not-for-logs", retries=0)

    result, media_type = client.synthesize("नमस्ते, hello", language="auto")

    assert result == audio
    assert media_type == "audio/wav"
    assert calls[0][0][0] == "https://example.test/api/v1/tts/inference"
    assert calls[0][1]["headers"] == {"X-API-Key-ID": "not-for-logs"}
    assert calls[0][1]["json"] == {
        "text": "नमस्ते, hello",
        "voice": "Shivani",
        "model": "timbre-v3.0",
        "language": "auto",
        "speed": 1.0,
        "audio_config": {
            "sample_rate": 24000,
            "num_channels": 1,
            "sample_width": 2,
            "encoding": "linear_pcm",
            "container": "wav",
        },
    }


def test_synthesize_rejects_non_audio_success_response(monkeypatch):
    monkeypatch.setattr(
        gnani_client.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(
            status_code=200,
            ok=True,
            headers={"Content-Type": "application/json"},
            content=b'{"error":"not audio"}',
        ),
    )
    client = GnaniClient(api_key="not-for-logs", retries=0)

    with pytest.raises(RuntimeError, match="GNANI_TTS_INVALID_RESPONSE"):
        client.synthesize("test")


def test_synthesize_treats_credentials_error_as_fatal(monkeypatch):
    monkeypatch.setattr(
        gnani_client.requests,
        "post",
        lambda *args, **kwargs: SimpleNamespace(status_code=401, ok=False, headers={}, content=b""),
    )
    client = GnaniClient(api_key="not-for-logs", retries=0)

    with pytest.raises(FatalAPIError, match="HTTP 401") as error:
        client.synthesize("test")
    assert "not-for-logs" not in str(error.value)
