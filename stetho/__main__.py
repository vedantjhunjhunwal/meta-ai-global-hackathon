"""Command line.

  python -m stetho analyze VIDEO [--machine NAME] [--mode check|baseline|before_repair|after_repair]
                                 [--lang en|hi] [--rpm N] [--db PATH] [--json OUT]
  python -m stetho simulate OUT.mp4 [--fault none|bearing|imbalance|cavitation] [--fps 120] [--seed 0]
  python -m stetho demo [--lang en|hi]      # full SIMULATED story: baselines -> fault -> repair
  python -m stetho serve [--port 8000]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print_report(r: dict) -> None:
    a = r["assessment"]
    tag = "  [SIMULATED INPUT]" if r["simulated_input"] else ""
    print(f"\n== {r['machine']['name']} | mode={r['mode']} | decision={a['decision'].upper()}{tag}")
    print(f"   quality={r['quality']['verdict']}  analysis={r['timing']['total_ms'] / 1000:.1f}s "
          f"(target {r['timing']['target_ms'] / 1000:.0f}s)")
    au = r.get("audio") or {}
    if au.get("shaft_hz"):
        print(f"   shaft ~{au['shaft_hz']:.2f} Hz ({au['shaft_status']}), HF kurtosis {au['hf_kurtosis']:.1f}, "
              f"envelope peak {au['envelope_peak_hz']:.1f} Hz @ {au['envelope_peak_prominence_db']:.1f} dB")
    mo = r.get("motion") or {}
    if mo.get("ok"):
        print(f"   video: {mo['fps']:.0f} fps, shake {mo['camera_shake_rms_px']:.1f} px, peak {mo['peak_hz']:.1f} Hz "
              f"({mo['peak_amp_px']:.2f} px, {mo['peak_snr_db']:.0f} dB over static reference)")
    c = a.get("comparison") or {}
    if c:
        print(f"   vs own baseline ({c['n_baselines']}): change={c['change']} distance={c['distance']}")
    for f in a["findings"]:
        print(f"   finding: {f['id']} [{f['strength']}] basis={f.get('basis')}")
    if a.get("repair"):
        print(f"   repair: {a['repair']}")
    if (a.get("spot") or {}).get("status") not in (None, "not_available"):
        print(f"   same-spot check: {a['spot']['status']}")
    if (a.get("ambient") or {}).get("status") not in (None, "not_available"):
        print(f"   background reference: {a['ambient']['status']}, ignored {len(a['ambient'].get('excluded', []))} feature(s)")
    if (a.get("trend") or {}).get("status") in ("rising", "steady"):
        print(f"   trend: {a['trend']['status']} {a['trend']['values']}")
    print("   --- message ---")
    for line in r["explanation"]["chat_text"].splitlines():
        print("   " + line)
    for k, v in r["artifacts"].items():
        print(f"   {k}: {v}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stetho")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("video")
    a.add_argument("--machine", default="pump")
    a.add_argument("--mode", default="check")
    a.add_argument("--lang", default="en")
    a.add_argument("--rpm", type=float)
    a.add_argument("--db", default=None, help="SQLite path for machine baselines (default: data dir)")
    a.add_argument("--json", default=None)
    si = sub.add_parser("simulate")
    si.add_argument("out")
    si.add_argument("--fault", default="none")
    si.add_argument("--fps", type=float, default=120)
    si.add_argument("--seed", type=int, default=0)
    si.add_argument("--shake", type=float, default=3.0)
    d = sub.add_parser("demo")
    d.add_argument("--lang", default="en")
    d.add_argument("--out", default="var/demo")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)

    from .config import get_settings
    from .store import Store

    if args.cmd == "simulate":
        from .synth import SimSpec, make_recording
        truth = make_recording(SimSpec(fault=args.fault, fps=args.fps, seed=args.seed, shake_px=args.shake), args.out)
        print(json.dumps({k: v for k, v in truth.items()}, indent=1))
        return 0
    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("stetho.server:app", host=args.host, port=args.port)
        return 0

    from .pipeline import analyze
    s = get_settings()
    if args.cmd == "analyze":
        store = Store(args.db or s.data_dir / "stetho.db")
        r = analyze(args.video, store=store, uid="cli", machine_name=args.machine, mode=args.mode,
                    lang=args.lang, rpm_hint=args.rpm, settings=s)
        _print_report(r)
        if args.json:
            Path(args.json).write_text(json.dumps(r, indent=1, default=str))
        return 0

    # demo: a complete SIMULATED maintenance story on one machine.
    from .synth import SimSpec, make_recording
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = Store()
    story = [
        ("baseline", "Day 1: healthy reference #1", dict(seed=1)),
        ("baseline", "Day 2: healthy reference #2", dict(seed=2)),
        ("baseline", "Day 3: healthy reference #3", dict(seed=3)),
        ("check", "Week 2: routine check", dict(seed=4)),
        ("check", "Week 3: shaky hand-held recording in the dark", dict(seed=5, brightness=0.08, shake_px=30)),
        ("check", "Week 4: recorded from too close (phone moved)", dict(seed=6, view_scale=1.3)),
        ("check", "Week 5: slight wear begins (simulated, severity 0.1)", dict(seed=7, fault="bearing", fault_severity=0.1)),
        ("check", "Week 6: wear (0.2)", dict(seed=8, fault="bearing", fault_severity=0.2)),
        ("check", "Week 7: wear (0.3)", dict(seed=9, fault="bearing", fault_severity=0.3)),
        ("check", "Week 8: wear (0.4)", dict(seed=10, fault="bearing", fault_severity=0.4)),
        ("before_repair", "Week 9: clear knocking (simulated outer-race bearing defect)", dict(seed=11, fault="bearing")),
        ("after_repair", "Week 9: after bearing replacement", dict(seed=12)),
        ("check", "Week 10: a generator starts nearby - check WITHOUT a background reference",
         dict(seed=13, ambient_hiss=0.05)),
        ("ambient", "Week 10: background reference (pump switched off)", dict(seed=14, ambient_hiss=0.05, machine_on=False)),
        ("check", "Week 10: same check WITH the background reference", dict(seed=15, ambient_hiss=0.05)),
    ]
    print("STETHO DEMO - every recording below is SIMULATED (synthetic video + audio with known ground truth).")
    print("It demonstrates the software flow, not real-world accuracy.\n")
    timings = []
    for i, (mode, title, kw) in enumerate(story):
        p = out / f"{i}_{mode}.mp4"
        make_recording(SimSpec(fps=60, duration_s=10, **kw), p)
        print(f"\n##### {title}")
        r = analyze(p, store=store, uid="demo", machine_name="Field pump", mode=mode, lang=args.lang, settings=s,
                    make_visual=mode in ("before_repair",))
        _print_report(r)
        timings.append(r["timing"]["total_ms"])
    print(f"\nAnalysis time per recording (this machine): min {min(timings) / 1000:.1f}s, "
          f"max {max(timings) / 1000:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
