# Three-minute demo script

**Setup before going on stage:** `python -m stetho serve`, open `http://127.0.0.1:8000`. Click **Healthy** once beforehand so the three simulated baselines are already built (the first run takes ~30–40 s on a laptop CPU). Keep the terminal visible for the CLI segment. Everything shown uses **simulated recordings**. Say so out loud, and leave the on-screen "SIMULATED INPUT" badge visible.

---

**0:00–0:20 · The problem**
> "A farmer's pump fails in the middle of the irrigation season. Usually it was making a different noise for weeks, but nobody had a way to tell whether 'different' meant 'call someone now'. Vibration analysers exist. They cost more than the pump, and the apps need you to press a phone against a running machine. We won't ask anyone to do that."

**0:20–0:45 · The insight**
> "A phone is a poor vibration instrument in absolute terms: uncalibrated mic, aliasing camera, compression. But the same phone, from the same marked spot, recording the same pump is far more repeatable *relative to itself*. So Stetho doesn't ask 'what fault is this?'. It asks: **is this pump different from its own healthy normal?**"

Show the **Record safely** card: phone rested on a brick, machine centred, background visible, same spot, never touch.

**0:45–1:25 · A check that finds something** (click **Bearing knock**)
> "This is a simulated pump with a seeded outer-race bearing defect. Stetho checks quality, cancels camera shake using the still background, then measures sound features and visible motion."

Point at:
- the **red banner**: "Ask a technician to inspect";
- the **voice note** (play 5 seconds; switch language to हिंदी on the next run if there's time);
- the **z-score chart**: impulsiveness and knock regularity far beyond the dashed ±3 line, compared with *this machine's* references;
- "about 3.6 knocks per shaft turn" (a non-integer order is *consistent with* a rolling-element defect; we say "possible causes to check", never a diagnosis).

**1:25–1:50 · Visible evidence, honestly labelled**
Open **See the movement**.
> "Here's the movement amplified twenty times and slowed down. It helps a mechanic see where to look. It's a visual aid, not a measurement. The motion chart is in pixels, and the grey line is the noise floor measured on background points in the same video."

**1:50–2:05 · Abstention** (click **Too dark**)
> "When the recording is bad, Stetho doesn't guess. It says exactly how to re-record: more light, hold still. No diagnosis comes out of a bad recording."

**2:05–2:15 · Harder to fool** (click **New background hiss**, then **Save background (machine off)**, then **New background hiss** again)
> "Same recording twice. Without a background reference, a new generator nearby looks like a change, and Stetho tells you how to check. With a ten-second machine-off reference, the drowned sounds are ignored and the answer is 'no change'. It also notices if you recorded from the wrong spot, and flags slow wear as a rising trend before any single check crosses the clear-change line."

**2:15–2:40 · Repair verification** (tick "treat as after repair", click **Healthy**)
> "After the bearing is replaced, the owner sends another video and says AFTER REPAIR. Stetho compares it with the healthy reference and with the before-repair recording: back close to its own normal. Note the wording: it doesn't claim the cause is fixed. It says the machine sounds and moves like its healthy self again, and to check again in a week."

**2:40–3:00 · Honesty and what's next**
> "All of this is simulated today. We built a validation protocol with accelerometer references, held-out machines and acceptance criteria, and we found real limitations while building: phone video compression suppresses sub-pixel motion and shifts its frequency, so we measure and report that. Next step: run the protocol on a test rig and real farm pumps, over WhatsApp."

---

*Backup if the network or browser fails:* `python -m stetho demo` runs the whole story (3 baselines → routine check → bad recording → bearing fault → repair) in the terminal in about a minute.
