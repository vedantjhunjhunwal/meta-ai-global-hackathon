#!/usr/bin/env python3
"""Timed 3-minute Stetho demo (SIMULATED recordings only).

Two ways to present:

  # 1. The script drives everything and prints results live (also the backup plan):
  python scripts/stage_demo.py

  # 2. You click in the browser; the script prepares the server, prints the link,
  #    and acts as your teleprompter + stopwatch:
  python scripts/stage_demo.py --drive browser

Options:
  --url URL        Stetho server (default http://127.0.0.1:8000). Started for you if
                   it's local and not running (unless --no-start).
  --lang en|hi     Reply language for voice notes.
  --fast           Don't wait between segments (rehearsal / CI check of the flow).
  --out DIR        Where to save the voice notes and magnified clip (default var/stage-demo).

Preparation (generating simulated clips and the 3 healthy references) happens
BEFORE the 3-minute clock starts, so nothing slow happens on stage. Every
result is labelled SIMULATED; say so out loud.
"""

from __future__ import annotations

import argparse
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

TOTAL_S = 180

# (start_s, end_s, title, narration, action, what to point at)
# action: (scenario, mode) or a list of them, or None.
SEGMENTS = [
    (0, 15, "The problem",
     "A farmer's pump fails mid-season. Usually it sounded different for weeks, but nobody could tell whether "
     "'different' meant 'call someone now'. Vibration analysers cost more than the pump, and phone apps want you to "
     "press the phone against a running machine. We never ask anyone to do that.",
     None, "Title area: 'Is your pump different from its own normal?'"),
    (15, 35, "The insight",
     "A phone is a poor vibration instrument in absolute terms. But the same phone, at the same marked spot, on the "
     "same pump, is far more consistent relative to itself. So Stetho asks one question: is this pump different "
     "from its own healthy normal? First, you record safely: phone on a brick, machine centred, same spot, never touch.",
     None, "Card 1 'Record safely'"),
    (35, 75, "A check that finds something",
     "This simulated pump has a seeded bearing defect. Stetho checks quality, cancels hand shake using the still "
     "background, measures the sound and the visible motion, and compares with this pump's own three healthy "
     "recordings. Red banner: ask a technician. The bars show impulsiveness and knock regularity far past the "
     "dashed line. About 3.6 knocks per shaft turn is consistent with a rolling-element defect, so we list possible "
     "causes to check, never a diagnosis. And the farmer gets this as a voice note.",
     [("bearing", "before_repair")], "Banner -> voice note (play 5 s) -> z-score bars -> 'knocks per turn'"),
    (75, 95, "Visible evidence, honestly labelled",
     "Here's the movement amplified twenty times and slowed down. It helps a mechanic see where to look. It's a "
     "visual aid, not a measurement. The motion chart is in pixels, and the grey line is the noise floor measured "
     "on the background in the same video.",
     None, "'See the movement' clip -> motion spectrum with grey noise floor"),
    (95, 110, "It refuses to guess",
     "When the recording is bad, Stetho doesn't guess. It says exactly how to record again: more light, hold still. "
     "Nothing is diagnosed from a bad recording, and it is not saved as evidence.",
     [("dark", "check")], "Blue 'Please record again' banner and the specific advice"),
    (110, 135, "Did the repair work?",
     "The bearing is replaced. The owner sends another video and says AFTER REPAIR. Stetho compares it with the "
     "healthy reference and with the before-repair recording: back close to its own normal. We don't claim the "
     "cause is fixed. We say it sounds and moves like its healthy self again, and to check again in a week.",
     [("healthy", "after_repair")], "Repair banner 'Back close to its healthy reference' and the timeline chart"),
    (135, 165, "Harder to fool",
     "Now a generator starts nearby. Without a background reference, that looks like change, and Stetho says what "
     "to try. The owner switches the pump off and sends ten seconds of background. Same check again: the drowned "
     "sounds are ignored, and the answer is no change. It also notices if you recorded from the wrong spot, and "
     "flags slow wear as a rising trend.",
     [("noisy_background", "check"), ("ambient", "ambient"), ("noisy_background", "check")],
     "First result's advice line -> 'Background noise saved' -> green result + 'Same spot and background' panel"),
    (165, 180, "Honest status and next step",
     "Everything you saw is simulated, and we label it that way. We've written a validation protocol with "
     "accelerometer references, held-out machines and acceptance criteria. Next: run it on a test rig and on real "
     "farm pumps, over WhatsApp.",
     None, "Footer disclaimer, or docs/VALIDATION_PROTOCOL.md"),
]

BUTTON = {"bearing": "Bearing knock", "dark": "Too dark", "healthy": "Healthy",
          "noisy_background": "New background hiss", "ambient": "Save background (machine off)"}
MODE_LABEL = {"check": "a routine check", "before_repair": "just before a repair", "after_repair": "just after a repair",
              "ambient": "(any; this button always saves a background reference)"}


def say(msg: str = "", end: str = "\n") -> None:
    print(msg, end=end, flush=True)


def clock(t0: float) -> str:
    e = time.perf_counter() - t0
    return f"{int(e // 60)}:{int(e % 60):02d}"


def wrap(text: str, width: int = 92, indent: str = "    ") -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    return "\n".join(indent + ln for ln in lines)


def ensure_server(url: str, start: bool) -> subprocess.Popen | None:
    try:
        httpx.get(f"{url}/healthz", timeout=3).raise_for_status()
        return None
    except httpx.HTTPError:
        pass
    host = urlparse(url)
    if not start or host.hostname not in ("127.0.0.1", "localhost"):
        sys.exit(f"Stetho server not reachable at {url}. Start it with: python -m stetho serve")
    say(f"Starting Stetho server on port {host.port or 8000} ...")
    proc = subprocess.Popen([sys.executable, "-m", "stetho", "serve", "--port", str(host.port or 8000)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        time.sleep(0.5)
        try:
            httpx.get(f"{url}/healthz", timeout=2).raise_for_status()
            return proc
        except httpx.HTTPError:
            continue
    proc.terminate()
    sys.exit("Server did not start in 30 s.")


def summarise(r: dict) -> list[str]:
    a = r["assessment"]
    out = [f"DECISION: {a['decision'].upper()}   [SIMULATED INPUT]   server {r['timing']['total_ms'] / 1000:.1f}s"]
    if a.get("repair"):
        out.append(f"repair check: {a['repair'].get('status')}")
    c = a.get("comparison") or {}
    if c.get("notable"):
        top = sorted(c["notable"], key=lambda n: -abs(n["z"]))[:3]
        out.append("biggest changes vs own baseline: " + ", ".join(f"{n['feature']} z={n['z']}" for n in top))
    for f in a["findings"][:2]:
        out.append(f"finding: {f['id']} ({f['strength']})")
    amb = a.get("ambient") or {}
    if amb.get("status") == "applied":
        out.append(f"background reference applied: ignored {len(amb['excluded'])} drowned measurement(s)")
    if a.get("spot", {}).get("status") not in (None, "not_available"):
        out.append(f"same-spot check: {a['spot']['status']}")
    msg = r["explanation"]["chat_text"].splitlines()
    out.append("message: " + " ".join(msg[:2]))
    if "check_background_noise" in a["next_steps"]:
        out.append("advice includes: save a background reference (AMBIENT)")
    return out


def download(client: httpx.Client, url: str, r: dict, out: Path, tag: str) -> list[str]:
    saved = []
    for kind, path in r.get("artifacts", {}).items():
        ext = ".ogg" if kind == "voice" else ".mp4"
        dest = out / f"{tag}_{kind}{ext}"
        resp = client.get(url + path)
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            saved.append(str(dest))
    return saved


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--drive", choices=("api", "browser"), default="api")
    ap.add_argument("--lang", default="en", choices=("en", "hi"))
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--out", default="var/stage-demo")
    ap.add_argument("--session", default=None)
    args = ap.parse_args(argv)
    url = args.url.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    session = args.session or "stage-" + secrets.token_hex(6)

    proc = ensure_server(url, not args.no_start)
    client = httpx.Client(timeout=120)
    try:
        say("=" * 96)
        say("STETHO 3-MINUTE DEMO   |   every recording is SIMULATED (synthetic video + sound, known faults)")
        say("=" * 96)
        say("Preparing (not part of the 3 minutes): simulated clips + 3 healthy references ...", end=" ")
        t = time.perf_counter()
        prep = client.post(f"{url}/api/demo/prepare", data={"session": session, "lang": args.lang})
        prep.raise_for_status()
        say(f"done in {time.perf_counter() - t:.0f}s.")
        if args.drive == "browser":
            say(f"\nOPEN THIS IN THE BROWSER NOW:  {url}/?session={session}")
            say("Scroll so card 2 ('Check a recording') is visible.")
        if not args.fast:
            input("\nPress Enter to start the 3-minute clock ... ")

        t0 = time.perf_counter()
        server_times, overruns = [], []
        for i, (start, end, title, narration, action, point) in enumerate(SEGMENTS, 1):
            if not args.fast:
                while time.perf_counter() - t0 < start:
                    time.sleep(0.05)
            say(f"\n[{clock(t0)}] ({start // 60}:{start % 60:02d}-{end // 60}:{end % 60:02d})  {i}. {title.upper()}")
            say("  SAY:")
            say(wrap(narration))
            actions = action or []
            for j, (scenario, mode) in enumerate(actions, 1):
                if args.drive == "browser":
                    say(f"  CLICK: set 'Treat the simulated recording as' = {MODE_LABEL[mode]}, "
                        f"then press [{BUTTON[scenario]}]")
                    continue
                say(f"  RUN: {scenario} ({mode}) ...", end=" ")
                r = client.post(f"{url}/api/demo/{scenario}", data={"session": session, "lang": args.lang,
                                                                  "mode": mode})
                r.raise_for_status()
                rep = r.json()
                server_times.append(rep["timing"]["total_ms"] / 1000)
                say("")
                for line in summarise(rep):
                    say("    " + line)
                for f in download(client, url, rep, out, f"{i:02d}{chr(96 + j)}_{scenario}"):
                    say(f"    saved: {f}")
            say(f"  POINT AT: {point}")
            elapsed = time.perf_counter() - t0
            if elapsed > end + 1:
                overruns.append((title, round(elapsed - end, 1)))
                say(f"  ! running {elapsed - end:.0f}s behind schedule")
        if not args.fast:
            while time.perf_counter() - t0 < TOTAL_S:
                time.sleep(0.05)
        total = time.perf_counter() - t0
        say("\n" + "=" * 96)
        say(f"Demo finished at {clock(t0)} (target 3:00).")
        if server_times:
            say(f"Server analysis per recording: min {min(server_times):.1f}s, max {max(server_times):.1f}s "
                f"over {len(server_times)} simulated recordings.")
        say("On schedule." if not overruns else f"Overran in: {overruns}")
        say(f"Browser view of this demo machine: {url}/?session={session}")
        return 0 if (args.fast or total <= TOTAL_S + 2) else 1
    finally:
        client.close()
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
