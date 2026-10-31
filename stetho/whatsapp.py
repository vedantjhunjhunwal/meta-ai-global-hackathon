"""WhatsApp Cloud API intake and conversation flow.

Endpoints and payload shapes follow Meta's WhatsApp Cloud API (Graph API):
  * webhook verification: GET ?hub.mode=subscribe&hub.verify_token=..&hub.challenge=..
  * webhook signature:    X-Hub-Signature-256: sha256=HMAC(app_secret, raw_body)
  * media download:       GET /{version}/{media-id} -> {url, mime_type, file_size}; GET url (Bearer)
  * media upload:         POST /{version}/{phone-number-id}/media (multipart)
  * send message:         POST /{version}/{phone-number-id}/messages
This module has been exercised against mocked Graph API responses in the
test suite, not against a live WhatsApp Business account. Check the Graph
API version (WHATSAPP_GRAPH_VERSION) against Meta's current docs.

Conversation commands (case-insensitive; Hindi keywords also accepted):
  YES / हाँ            consent to processing (required once)
  HELP / मदद           recording guide
  HINDI / ENGLISH      reply language
  MACHINE <name>       switch/create machine (e.g. "MACHINE well pump")
  BASELINE             next video is a healthy reference
  BEFORE REPAIR        next video is just before a repair
  AFTER REPAIR         next video is just after a repair
  RPM <n>              rated speed from the nameplate
  CONFIRM              accept nameplate values read from a photo
  TECH                 technician summary of the last result
  DELETE               delete all stored data for this number
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import hmac
import logging
import re
import time
from pathlib import Path

import httpx

from .config import Settings
from .media import MediaError
from .store import Store, pseudonymise

log = logging.getLogger(__name__)
VIDEO_MIMES = {"video/mp4", "video/3gpp", "video/quicktime", "video/x-matroska", "video/webm"}

MSG = {
    "en": {
        "consent": ("Hello, I am Stetho. Send me a 10-second video of a running pump or motor, and I will reply "
                    "with a voice note about how it sounds and moves.\n\nPrivacy: I analyse the video, keep only "
                    "measurements (not the video) linked to a code instead of your number, and you can send DELETE "
                    "at any time. This is a screening aid, not a diagnosis.\n\nReply YES to continue."),
        "guide": ("How to record (safely):\n1. Do not touch the running machine or remove covers. Stand where you "
                  "normally stand.\n2. Rest the phone on a brick or wall about 1 metre away, machine in the centre, "
                  "some floor or wall visible.\n3. Use the same spot every time (mark it).\n4. Record 10-15 seconds "
                  "with sound on. Slow-motion mode helps if your phone has it.\n5. Send as a document if you can "
                  "(less compression).\nFirst time? Reply BASELINE before sending, if the machine is working well."),
        "ack": "Got it. Checking {machine} now; this usually takes under a minute.",
        "mode_set": "OK. The next video for {machine} will be saved as: {mode}.",
        "machine_set": "Now using machine: {machine}.",
        "rpm_set": "Saved rated speed {rpm} rpm for {machine}.",
        "lang_set": "I will reply in English.",
        "deleted": "Deleted all your data ({n} recordings). Send a message any time to start again.",
        "send_video": "Please send a video (10-15 seconds) of the running machine, with sound.",
        "too_big": "This video is too large. Please record 10-15 seconds and send again.",
        "bad_media": "I could not read this file. Please send a normal phone video (MP4).",
        "download_failed": "I could not download the video. Please send it again.",
        "timeout": "Sorry, the check took too long. Please send a shorter video (10-15 seconds).",
        "error": "Sorry, something went wrong on my side. Please try again later.",
        "nameplate_read": "I read from the nameplate: {fields}. Reply CONFIRM if this is correct.",
        "nameplate_unread": "I could not read the nameplate clearly. You can type the speed, for example: RPM 2880",
        "nameplate_manual": "To save the nameplate speed, type it like: RPM 2880",
        "confirmed": "Saved nameplate details for {machine}.",
        "no_tech": "No recent result yet. Send a video first.",
        "follow_tech": "Reply TECH for a technician summary you can forward.",
    },
    "hi": {
        "consent": ("नमस्ते, मैं Stetho हूँ। चलते पंप या मोटर का 10 सेकंड का वीडियो भेजें, मैं आवाज़ में बताऊँगा कि "
                    "वह कैसा सुनाई देता और हिलता है।\n\nगोपनीयता: मैं वीडियो की जाँच करके सिर्फ़ माप रखता हूँ, वीडियो नहीं, "
                    "और आपके नंबर की जगह एक कोड इस्तेमाल करता हूँ। कभी भी DELETE भेजकर सब मिटा सकते हैं। यह शुरुआती "
                    "जाँच है, पक्का निदान नहीं।\n\nआगे बढ़ने के लिए YES या हाँ लिखें।"),
        "guide": ("सुरक्षित तरीके से रिकॉर्ड कैसे करें:\n1. चलती मशीन को न छुएँ, कवर न हटाएँ। जहाँ आमतौर पर खड़े होते हैं "
                  "वहीं खड़े रहें।\n2. फ़ोन को लगभग 1 मीटर दूर ईंट या दीवार पर टिकाएँ, मशीन बीच में हो, थोड़ी ज़मीन या "
                  "दीवार दिखे।\n3. हर बार उसी जगह से रिकॉर्ड करें (निशान लगा लें)।\n4. आवाज़ चालू रखकर 10-15 सेकंड रिकॉर्ड "
                  "करें। फ़ोन में स्लो-मोशन हो तो उसका इस्तेमाल करें।\n5. हो सके तो डॉक्यूमेंट की तरह भेजें।\nपहली बार? "
                  "अगर मशीन ठीक चल रही है तो वीडियो से पहले BASELINE लिखें।"),
        "ack": "मिल गया। {machine} की जाँच कर रहा हूँ; आमतौर पर एक मिनट से कम लगता है।",
        "mode_set": "ठीक है। {machine} का अगला वीडियो इस तरह सहेजा जाएगा: {mode}।",
        "machine_set": "अब यह मशीन: {machine}।",
        "rpm_set": "{machine} के लिए {rpm} rpm सहेज लिया।",
        "lang_set": "मैं हिंदी में जवाब दूँगा।",
        "deleted": "आपका सारा डेटा मिटा दिया ({n} रिकॉर्डिंग)। फिर शुरू करने के लिए कभी भी संदेश भेजें।",
        "send_video": "कृपया चलती मशीन का आवाज़ के साथ 10-15 सेकंड का वीडियो भेजें।",
        "too_big": "यह वीडियो बहुत बड़ा है। कृपया 10-15 सेकंड रिकॉर्ड करके फिर भेजें।",
        "bad_media": "मैं यह फ़ाइल नहीं पढ़ पाया। कृपया सामान्य फ़ोन वीडियो (MP4) भेजें।",
        "download_failed": "मैं वीडियो डाउनलोड नहीं कर पाया। कृपया फिर से भेजें।",
        "timeout": "माफ़ कीजिए, जाँच में बहुत समय लगा। कृपया छोटा वीडियो (10-15 सेकंड) भेजें।",
        "error": "माफ़ कीजिए, मेरी तरफ़ से कुछ गड़बड़ हुई। कृपया बाद में फिर कोशिश करें।",
        "nameplate_read": "नेमप्लेट से मैंने पढ़ा: {fields}। सही हो तो CONFIRM लिखें।",
        "nameplate_unread": "मैं नेमप्लेट साफ़ नहीं पढ़ पाया। आप गति लिख सकते हैं, जैसे: RPM 2880",
        "nameplate_manual": "नेमप्लेट की गति सहेजने के लिए ऐसे लिखें: RPM 2880",
        "confirmed": "{machine} की नेमप्लेट जानकारी सहेज ली।",
        "no_tech": "अभी कोई नतीजा नहीं है। पहले वीडियो भेजें।",
        "follow_tech": "मिस्त्री को भेजने लायक़ सारांश के लिए TECH लिखें।",
    },
}
MODE_NAMES = {"en": {"baseline": "healthy reference", "before_repair": "before repair", "after_repair": "after repair"},
              "hi": {"baseline": "ठीक हालत का संदर्भ", "before_repair": "मरम्मत से पहले", "after_repair": "मरम्मत के बाद"}}


def verify_signature(raw: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret:
        return False
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.split("=", 1)[1])


def parse_messages(payload: dict) -> list[dict]:
    out = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for m in value.get("messages", []) or []:
                t = m.get("type")
                item = {"id": m.get("id"), "from": m.get("from"), "type": t, "text": None,
                        "media_id": None, "mime": None, "filename": None}
                if t == "text":
                    item["text"] = (m.get("text") or {}).get("body", "")
                elif t in ("video", "image", "audio", "document"):
                    media = m.get(t) or {}
                    item.update(media_id=media.get("id"), mime=media.get("mime_type"),
                                filename=media.get("filename"))
                    item["text"] = media.get("caption")
                if item["id"] and item["from"]:
                    out.append(item)
    return out


class GraphClient:
    def __init__(self, s: Settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        self.s = s
        self.sleep = sleep
        self.http = httpx.Client(timeout=30.0, transport=transport,
                                 headers={"Authorization": f"Bearer {s.wa_access_token}"})
        self.base = f"{s.wa_graph_base}/{s.wa_graph_version}"

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        delay = 0.5
        for attempt in range(3):
            try:
                r = self.http.request(method, url, **kw)
                if r.status_code < 500 and r.status_code != 429:
                    r.raise_for_status()
                    return r
            except httpx.TransportError as exc:
                log.warning("Graph API transport error: %s", exc)
                if attempt == 2:
                    raise
            if attempt < 2:
                self.sleep(delay)
                delay *= 2
        r.raise_for_status()
        return r

    def download_media(self, media_id: str, max_bytes: int) -> tuple[bytes, str]:
        meta = self._req("GET", f"{self.base}/{media_id}").json()
        if int(meta.get("file_size") or 0) > max_bytes:
            raise MediaError("too_big")
        data = self._req("GET", meta["url"]).content
        if len(data) > max_bytes:
            raise MediaError("too_big")
        return data, meta.get("mime_type", "")

    def send_text(self, to: str, body: str):
        return self._req("POST", f"{self.base}/{self.s.wa_phone_number_id}/messages",
                         json={"messaging_product": "whatsapp", "to": to, "type": "text",
                               "text": {"body": body[:4000], "preview_url": False}})

    def send_media(self, to: str, path: str | Path, kind: str, mime: str, caption: str | None = None):
        with open(path, "rb") as fh:
            up = self._req("POST", f"{self.base}/{self.s.wa_phone_number_id}/media",
                           data={"messaging_product": "whatsapp", "type": mime},
                           files={"file": (Path(path).name, fh, mime)}).json()
        body = {"id": up["id"]}
        if caption and kind == "video":
            body["caption"] = caption[:1000]
        return self._req("POST", f"{self.base}/{self.s.wa_phone_number_id}/messages",
                         json={"messaging_product": "whatsapp", "to": to, "type": kind, kind: body})


class Bot:
    def __init__(self, s: Settings, store: Store, graph: GraphClient, analyze_fn, llm=None):
        self.s, self.store, self.graph, self.analyze_fn, self.llm = s, store, graph, analyze_fn, llm
        self.pool = cf.ThreadPoolExecutor(max_workers=2)
        self.last_tech: dict[str, str] = {}  # in-memory only

    def _say(self, to, lang, key, **kw):
        self.graph.send_text(to, MSG.get(lang, MSG["en"])[key].format(**kw))

    def handle(self, msg: dict) -> str:
        """Process one inbound message synchronously. Returns an outcome label (for logs/tests)."""
        if not self.store.mark_processed(msg["id"]):
            return "duplicate"
        to = msg["from"]
        uid = pseudonymise(to, self.s.id_salt)
        user = self.store.user(uid)
        lang = user.get("lang") or "en"
        text = (msg.get("text") or "").strip()
        cmd = text.upper()

        if not user.get("consent_at"):
            if cmd in ("YES", "OK", "AGREE", "हाँ", "हां", "HAAN"):
                self.store.update_user(uid, consent_at=time.time())
                self._say(to, lang, "guide")
                return "consented"
            if cmd in ("HINDI", "हिंदी"):
                self.store.update_user(uid, lang="hi")
                lang = "hi"
            self._say(to, lang, "consent")
            return "consent_requested"

        machine_name = user.get("current_machine") or "pump"
        machine = self.store.machine(uid, machine_name)
        try:
            if msg["type"] == "text":
                return self._command(to, uid, lang, cmd, text, machine)
            if msg["type"] == "image":
                return self._nameplate(to, lang, msg, machine)
            if msg["type"] == "video" or (msg["type"] == "document" and (msg.get("mime") or "") in VIDEO_MIMES):
                return self._video(to, uid, lang, msg, user, machine)
            self._say(to, lang, "send_video")
            return "unsupported_type"
        except httpx.HTTPError as exc:
            log.error("provider failure: %s", exc)
            return "provider_error"

    def _command(self, to, uid, lang, cmd, text, machine) -> str:
        name = machine["name"]
        if cmd in ("HELP", "GUIDE", "HI", "HELLO", "मदद"):
            self._say(to, lang, "guide")
            return "guide"
        if cmd in ("HINDI", "हिंदी", "ENGLISH"):
            lang = "en" if cmd == "ENGLISH" else "hi"
            self.store.update_user(uid, lang=lang)
            self._say(to, lang, "lang_set")
            return "lang"
        if cmd == "DELETE":
            n = self.store.delete_user(uid)
            self.last_tech.pop(uid, None)
            self._say(to, lang, "deleted", n=n)
            return "deleted"
        modes = {"BASELINE": "baseline", "BEFORE REPAIR": "before_repair", "AFTER REPAIR": "after_repair"}
        if cmd in modes:
            self.store.update_user(uid, pending_mode=modes[cmd])
            self._say(to, lang, "mode_set", machine=name, mode=MODE_NAMES[lang][modes[cmd]])
            return "mode"
        m = re.match(r"^MACHINE\s+(.{1,40})$", text, re.I)
        if m:
            self.store.update_user(uid, current_machine=m.group(1).strip())
            self._say(to, lang, "machine_set", machine=m.group(1).strip())
            return "machine"
        m = re.match(r"^RPM\s+(\d{3,4})$", cmd)
        if m and 300 <= int(m.group(1)) <= 3700:
            self.store.update_machine(machine["mid"], rated_rpm=float(m.group(1)))
            self._say(to, lang, "rpm_set", rpm=m.group(1), machine=name)
            return "rpm"
        if cmd == "CONFIRM" and machine.get("pending_nameplate"):
            import json
            plate = json.loads(machine["pending_nameplate"])
            self.store.update_machine(machine["mid"], nameplate=plate, rated_rpm=plate.get("rated_rpm"),
                                      pending_nameplate=None)
            self._say(to, lang, "confirmed", machine=name)
            return "nameplate_confirmed"
        if cmd == "TECH":
            tech = self.last_tech.get(uid)
            self.graph.send_text(to, tech) if tech else self._say(to, lang, "no_tech")
            return "tech"
        self._say(to, lang, "guide")
        return "guide"

    def _nameplate(self, to, lang, msg, machine) -> str:
        if self.llm is None:
            self._say(to, lang, "nameplate_manual")
            return "nameplate_manual"
        try:
            data, mime = self.graph.download_media(msg["media_id"], 8 * 1024 * 1024)
        except (httpx.HTTPError, MediaError, KeyError):
            self._say(to, lang, "download_failed")
            return "download_failed"
        plate = self.llm.read_nameplate(data, mime or "image/jpeg")
        if not plate:
            self._say(to, lang, "nameplate_unread")
            return "nameplate_unread"
        self.store.update_machine(machine["mid"], pending_nameplate=plate)
        fields = ", ".join(f"{k}={v}" for k, v in plate.items() if v is not None)
        self._say(to, lang, "nameplate_read", fields=fields)
        return "nameplate_pending"

    def _video(self, to, uid, lang, msg, user, machine) -> str:
        max_bytes = self.s.max_upload_mb * 1024 * 1024
        if msg.get("mime") and msg["mime"] not in VIDEO_MIMES:
            self._say(to, lang, "bad_media")
            return "bad_media"
        try:
            data, mime = self.graph.download_media(msg["media_id"], max_bytes)
        except MediaError:
            self._say(to, lang, "too_big")
            return "too_big"
        except (httpx.HTTPError, KeyError):
            self._say(to, lang, "download_failed")
            return "download_failed"
        path = self.s.data_dir / "media" / f"{msg['id'].replace('/', '_')[-40:]}.mp4"
        path.write_bytes(data)
        mode = user.get("pending_mode") or "check"
        self._say(to, lang, "ack", machine=machine["name"])
        try:
            fut = self.pool.submit(self.analyze_fn, path, store=self.store, uid=uid, machine_name=machine["name"],
                                   mode=mode, lang=lang, llm=self.llm)
            report = fut.result(timeout=self.s.analysis_timeout_s)
        except cf.TimeoutError:
            self._say(to, lang, "timeout")
            return "timeout"
        except MediaError:
            self._say(to, lang, "bad_media")
            return "bad_media"
        except Exception:  # noqa: BLE001 - never leave the user without a reply
            log.exception("analysis failed")
            self._say(to, lang, "error")
            return "error"
        finally:
            if self.s.retain_media_hours <= 0:
                path.unlink(missing_ok=True)
        if report["assessment"]["decision"] != "rerecord":
            self.store.update_user(uid, pending_mode=None)
        exp = report["explanation"]
        self.last_tech[uid] = exp["technician_summary"]
        voice = report["artifacts"].get("voice")
        if voice:
            self.graph.send_media(to, voice, "audio", "audio/ogg")
        footer = MSG[lang]["follow_tech"] if report["assessment"]["decision"] != "rerecord" else ""
        self.graph.send_text(to, exp["chat_text"] + ("\n\n" + footer if footer else ""))
        if report["artifacts"].get("magnified"):
            self.graph.send_media(to, report["artifacts"]["magnified"], "video", "video/mp4",
                                  caption="Motion made visible (visual aid, not a measurement)")
        return f"analysed:{report['assessment']['decision']}"
