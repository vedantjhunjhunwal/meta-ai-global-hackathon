"""WhatsApp intake against a MOCKED Graph API (no live account is used)."""
import hashlib
import hmac
import time

import httpx
import pytest

from stetho.store import Store
from stetho.whatsapp import Bot, GraphClient, parse_messages, verify_signature


class FakeGraph:
    def __init__(self, media=b"video-bytes", fail_download=False):
        self.sent, self.media, self.fail = [], media, fail_download

    def send_text(self, to, body):
        self.sent.append(("text", to, body))

    def send_media(self, to, path, kind, mime, caption=None):
        self.sent.append((kind, to, str(path)))

    def download_media(self, media_id, max_bytes):
        if self.fail:
            raise httpx.ConnectError("down")
        return self.media, "video/mp4"


def fake_report(decision="no_change"):
    return {"assessment": {"decision": decision},
            "explanation": {"chat_text": "Result text", "technician_summary": "TECH SUMMARY"},
            "artifacts": {"voice": "/tmp/voice.ogg"}}


def msg(i, text=None, type_="text", **kw):
    return {"id": f"wamid.{i}", "from": "919800000001", "type": type_, "text": text,
            "media_id": kw.get("media_id"), "mime": kw.get("mime"), "filename": None}


@pytest.fixture()
def bot(settings):
    settings.analysis_timeout_s = 2.0
    calls = []

    def analyze(path, **kw):
        calls.append(kw)
        return fake_report()

    b = Bot(settings, Store(), FakeGraph(), analyze)
    b.calls = calls
    return b


def consent(b):
    b.handle(msg("c0", "hello"))
    b.handle(msg("c1", "YES"))


def test_signature_verification():
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, "secret")
    assert not verify_signature(body, sig, "other")
    assert not verify_signature(body, None, "secret")
    assert not verify_signature(body, sig, "")


def test_parse_cloud_api_payload():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.A", "from": "91", "type": "video", "video": {"id": "M1", "mime_type": "video/mp4"}},
        {"id": "wamid.B", "from": "91", "type": "text", "text": {"body": "BASELINE"}}]}}]}]}
    out = parse_messages(payload)
    assert [m["type"] for m in out] == ["video", "text"]
    assert out[0]["media_id"] == "M1" and out[1]["text"] == "BASELINE"
    assert parse_messages({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []


def test_consent_required_before_any_analysis(bot):
    assert bot.handle(msg(1, type_="video", media_id="M", mime="video/mp4")) == "consent_requested"
    assert bot.calls == []
    assert "YES" in bot.graph.sent[-1][2]
    assert bot.handle(msg(2, "YES")) == "consented"


def test_duplicate_messages_are_ignored(bot):
    consent(bot)
    m = msg(3, type_="video", media_id="M", mime="video/mp4")
    assert bot.handle(m).startswith("analysed")
    assert bot.handle(m) == "duplicate"
    assert len(bot.calls) == 1


def test_video_flow_sends_voice_text_and_tech(bot):
    consent(bot)
    bot.handle(msg(4, "BASELINE"))
    bot.handle(msg(5, type_="video", media_id="M", mime="video/mp4"))
    assert bot.calls[-1]["mode"] == "baseline"
    kinds = [s[0] for s in bot.graph.sent[-3:]]
    assert kinds == ["text", "audio", "text"]  # ack, voice note, text result
    bot.handle(msg(6, "TECH"))
    assert bot.graph.sent[-1][2] == "TECH SUMMARY"


def test_phone_number_is_not_stored(bot):
    consent(bot)
    dump = "\n".join(bot.store.db.iterdump())
    assert "919800000001" not in dump


def test_commands_language_machine_rpm_delete(bot):
    consent(bot)
    assert bot.handle(msg(10, "HINDI")) == "lang"
    assert "हिंदी" in bot.graph.sent[-1][2]
    assert bot.handle(msg(11, "MACHINE well pump")) == "machine"
    assert bot.handle(msg(12, "RPM 2880")) == "rpm"
    assert bot.handle(msg(13, "RPM 99999")) == "guide"  # out of range is not saved
    bot.handle(msg(14, type_="video", media_id="M", mime="video/mp4"))
    assert bot.calls[-1]["machine_name"] == "well pump" and bot.calls[-1]["lang"] == "hi"
    assert bot.handle(msg(15, "DELETE")) == "deleted"
    assert bot.handle(msg(16, type_="video", media_id="M", mime="video/mp4")) == "consent_requested"


def test_unsupported_media_and_audio(bot):
    consent(bot)
    assert bot.handle(msg(20, type_="document", media_id="D", mime="application/pdf")) == "unsupported_type"
    assert bot.handle(msg(21, type_="audio", media_id="A", mime="audio/ogg")) == "unsupported_type"


def test_download_failure_tells_user(settings):
    b = Bot(settings, Store(), FakeGraph(fail_download=True), lambda *a, **k: fake_report())
    consent(b)
    assert b.handle(msg(30, type_="video", media_id="M", mime="video/mp4")) == "download_failed"


def test_timeout_tells_user(settings):
    settings.analysis_timeout_s = 0.2
    b = Bot(settings, Store(), FakeGraph(), lambda *a, **k: (time.sleep(1.0), fake_report())[1])
    consent(b)
    assert b.handle(msg(31, type_="video", media_id="M", mime="video/mp4")) == "timeout"


def test_analysis_crash_still_replies(settings):
    def boom(*a, **k):
        raise RuntimeError("bug")
    b = Bot(settings, Store(), FakeGraph(), boom)
    consent(b)
    assert b.handle(msg(32, type_="video", media_id="M", mime="video/mp4")) == "error"
    assert "wrong" in b.graph.sent[-1][2]


def test_media_deleted_after_analysis(bot, settings):
    consent(bot)
    bot.handle(msg(40, type_="video", media_id="M", mime="video/mp4"))
    assert list((settings.data_dir / "media").iterdir()) == []


def test_graph_client_retries_then_succeeds(settings):
    settings.wa_access_token, settings.wa_phone_number_id = "tok", "PNID"
    hits = []

    def handler(request):
        hits.append(request.url.path)
        if len(hits) == 1:
            return httpx.Response(500)
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"messages": [{"id": "x"}]})

    g = GraphClient(settings, httpx.MockTransport(handler), sleep=lambda s: None)
    g.send_text("91", "hi")
    assert len(hits) == 2 and hits[-1].endswith("/PNID/messages")


def test_graph_client_rejects_oversized_media(settings):
    from stetho.media import MediaError

    def handler(request):
        return httpx.Response(200, json={"url": "https://x/y", "mime_type": "video/mp4", "file_size": 10**9})

    g = GraphClient(settings, httpx.MockTransport(handler), sleep=lambda s: None)
    with pytest.raises(MediaError):
        g.download_media("M", 64 * 1024 * 1024)
