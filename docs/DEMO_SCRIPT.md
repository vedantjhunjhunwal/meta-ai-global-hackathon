# Three-minute demo

Everything shown uses **simulated recordings**. Say so out loud. The on-screen "SIMULATED INPUT" badge stays visible.

## Run it

Start the server (or let the script start it):

```bash
python -m stetho serve
```

**Option A: the script drives everything** (terminal demo, and your backup if the browser fails). It prints what to say, runs each step, shows the results, and keeps time:

```bash
python scripts/stage_demo.py
```

**Option B: you click in the browser.** The script prepares the server, prints a link, then acts as teleprompter and stopwatch, telling you which button to press and when:

```bash
python scripts/stage_demo.py --drive browser
```

Rehearse the flow without waiting between segments:

```bash
python scripts/stage_demo.py --fast
```

Preparation (simulated clips plus 3 healthy references, about 20 s once the clips are cached, about 70 s the first time) happens **before** the clock starts. Press Enter to start the 3 minutes. Voice notes and clips are saved to `var/stage-demo/`.

**Measured on the development container:** a real-time run finished at 3:00 with every segment on schedule. Server analysis per recording was 4.7–10.0 s. A slower laptop may need a few extra seconds in segments 3 and 7; the script prints a warning if it falls behind.

## Running order

| Time | Segment | On screen (browser mode: set the selector, then click) | Result to point at |
|------|---------|--------------------------------------------------------|--------------------|
| 0:00–0:15 | **The problem** | Title area | |
| 0:15–0:35 | **The insight** | Card 1 "Record safely" | |
| 0:35–1:15 | **A check that finds something** | *just before a repair* → **Bearing knock** | Red "Ask a technician" banner → voice note (play 5 s) → z-score bars → "about 3.6 knocks per turn" |
| 1:15–1:35 | **Visible evidence, honestly labelled** | Open "See the movement" | Amplified clip, motion spectrum with grey noise floor |
| 1:35–1:50 | **It refuses to guess** | *a routine check* → **Too dark** | "Please record again" with specific advice |
| 1:50–2:15 | **Did the repair work?** | *just after a repair* → **Healthy** | "Back close to its healthy reference", timeline chart |
| 2:15–2:45 | **Harder to fool** | *a routine check* → **New background hiss**, then **Save background (machine off)**, then **New background hiss** again | False alarm with "save a background reference" advice → "Background noise saved" → green "No change" plus the "Same spot and background" panel |
| 2:45–3:00 | **Honest status and next step** | Footer disclaimer | |

## What to say

**The problem.** "A farmer's pump fails mid-season. Usually it sounded different for weeks, but nobody could tell whether 'different' meant 'call someone now'. Vibration analysers cost more than the pump, and phone apps want you to press the phone against a running machine. We never ask anyone to do that."

**The insight.** "A phone is a poor vibration instrument in absolute terms. But the same phone, at the same marked spot, on the same pump, is far more consistent relative to itself. So Stetho asks one question: is this pump different from its own healthy normal? First, you record safely: phone on a brick, machine centred, same spot, never touch."

**A check that finds something.** "This simulated pump has a seeded bearing defect. Stetho checks quality, cancels hand shake using the still background, measures the sound and the visible motion, and compares with this pump's own three healthy recordings. Red banner: ask a technician. The bars show impulsiveness and knock regularity far past the dashed line. About 3.6 knocks per shaft turn is consistent with a rolling-element defect, so we list possible causes to check, never a diagnosis. And the farmer gets this as a voice note."

**Visible evidence, honestly labelled.** "Here's the movement amplified twenty times and slowed down. It helps a mechanic see where to look. It's a visual aid, not a measurement. The motion chart is in pixels, and the grey line is the noise floor measured on the background in the same video."

**It refuses to guess.** "When the recording is bad, Stetho doesn't guess. It says exactly how to record again: more light, hold still. Nothing is diagnosed from a bad recording, and it is not saved as evidence."

**Did the repair work?** "The bearing is replaced. The owner sends another video and says AFTER REPAIR. Stetho compares it with the healthy reference and with the before-repair recording: back close to its own normal. We don't claim the cause is fixed. We say it sounds and moves like its healthy self again, and to check again in a week."

**Harder to fool.** "Now a generator starts nearby. Without a background reference, that looks like change, and Stetho says what to try. The owner switches the pump off and sends ten seconds of background. Same check again: the drowned sounds are ignored, and the answer is no change. It also notices if you recorded from the wrong spot, and flags slow wear as a rising trend."

**Honest status and next step.** "Everything you saw is simulated, and we label it that way. We've written a validation protocol with accelerometer references, held-out machines and acceptance criteria. Next: run it on a test rig and on real farm pumps, over WhatsApp."

## If something goes wrong

- Browser or network fails: switch to the terminal with `python scripts/stage_demo.py --fast --no-start` and keep talking.
- Longer story (wear trend, moved phone): `python -m stetho demo` takes about a minute.
