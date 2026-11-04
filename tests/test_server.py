import hashlib
import hmac
import json

import httpx
from fastapi.testclient import TestClient

from stetho.server import create_app
from stetho.store import Store

SESSION = "testsession01"


def client(settings, **kw):
    return TestClient(create_app(settings=settings, store=Store(), **kw))


def test_health(settings):
    h = client(settings).get("/healthz").json()
    assert h["ok"] and h["segmenter"] == "centre_prior" and h["whatsapp"] is False


def test_index_served(settings):
    r = client(settings).get("/")
    assert r.status_code == 200 and "Stetho" in r.text


def test_analyze_upload_and_artifacts(sim, settings):
    c = client(settings)
    path, _ = sim("bearing", seed=5, fault="bearing")
    with open(path, "rb") as fh:
        r = c.post("/api/analyze", data={"session": SESSION, "machine": "P1", "lang": "en"},
                   files={"video": ("v.mp4", fh, "video/mp4")})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["simulated_input"] is True and rep["assessment"]["decision"] == "technician"
    assert c.get(rep["artifacts"]["voice"]).status_code == 200
    assert list((settings.data_dir / "media").iterdir()) == []  # upload deleted
    assert c.get("/api/machines", params={"session": SESSION}).json()[0]["recordings"] == 1
    assert c.delete(f"/api/session/{SESSION}").json()["deleted_recordings"] == 1


def test_rejects_bad_inputs(settings):
    c = client(settings)
    r = c.post("/api/analyze", data={"session": SESSION}, files={"video": ("x.txt", b"not a video" * 10, "text/plain")})
    assert r.status_code == 415
    r = c.post("/api/analyze", data={"session": "bad id!"}, files={"video": ("x.mp4", b"x", "video/mp4")})
    assert r.status_code == 400
    settings.max_upload_mb = 1
    r = c.post("/api/analyze", data={"session": SESSION}, files={"video": ("x.mp4", b"0" * (2 << 20), "video/mp4")})
    assert r.status_code == 413
    assert c.get("/api/artifacts/../../etc/passwd").status_code == 404
    assert c.get("/api/artifacts/0123456789ab/secret.txt").status_code == 404


def test_webhook_disabled_without_credentials(settings):
    c = client(settings)
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}).status_code == 403
    assert c.post("/webhook/whatsapp", json={}).status_code == 503


def _wa_settings(settings):
    settings.wa_verify_token, settings.wa_app_secret = "vt", "appsecret"
    settings.wa_access_token, settings.wa_phone_number_id = "tok", "PNID"
    return settings


def test_webhook_verify_signature_and_reply(settings):
    sent = []

    def graph(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "out"}]})

    c = client(_wa_settings(settings), graph_transport=httpx.MockTransport(graph))
    ok = c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "42"})
    assert ok.text == "42"
    assert c.get("/webhook/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "1"}).status_code == 403
    body = json.dumps({"entry": [{"changes": [{"value": {"messages": [
        {"id": "wamid.1", "from": "919811111111", "type": "text", "text": {"body": "hi"}}]}}]}]}).encode()
    assert c.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": "sha256=bad"}).status_code == 401
    sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
    r = c.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sig, "content-type": "application/json"})
    assert r.status_code == 200
    assert sent and sent[0]["to"] == "919811111111" and "YES" in sent[0]["text"]["body"]


def test_demo_rejects_unknown_scenario_and_mode(settings):
    c = client(settings)
    assert c.post("/api/demo/nope", data={"session": SESSION}).status_code == 404
    assert c.post("/api/demo/healthy", data={"session": SESSION, "mode": "bogus"}).status_code == 400
