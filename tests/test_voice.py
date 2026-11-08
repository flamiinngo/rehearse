import base64
import json

import pytest

from rehearse import voice


class FakeCtx:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def dispatch_tool(self, name, args):
        self.calls.append((name, args))
        if not self.ok:
            return json.dumps({"success": False, "error": "TTS is disabled"})
        with open(args["output_path"], "wb") as f:
            f.write(b"ID3fake-mp3")
        return json.dumps({"success": True, "file_path": args["output_path"]})


def test_questions_are_spoken_by_hermes_tts_and_cached(tmp_path):
    ctx = FakeCtx()
    first = voice.speak(ctx, tmp_path, "Tell me about yourself.")
    again = voice.speak(ctx, tmp_path, "  Tell me about yourself.  ")
    assert base64.b64decode(first["audio"]) == b"ID3fake-mp3" and first["mime"] == "audio/mpeg"
    assert again == first and len(ctx.calls) == 1 and ctx.calls[0][0] == "text_to_speech"


def test_unavailable_tts_is_a_readable_error_for_the_fallback(tmp_path):
    with pytest.raises(ValueError, match="disabled"):
        voice.speak(FakeCtx(ok=False), tmp_path, "Hello")
    with pytest.raises(ValueError):
        voice.speak(FakeCtx(), tmp_path, "   ")
