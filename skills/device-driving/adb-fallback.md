# adb fallback primitives — driving the UI by hand

**You should not be reading this file in a normal run.** Maestro is the required driver
([`SKILL.md`](./SKILL.md) §9); these primitives exist for the two cases §9 allows:

1. Maestro **genuinely cannot run** in this environment (install fails: no network, sandboxed CI, permissions) — record `Tool used: adb (Maestro unavailable — <reason>)` and drive with what follows.
2. A specific capability has **no working Maestro equivalent right now** — in practice, video: Maestro's `startRecording` shells out to `adb screenrecord` and can fail (`UNASSIGNED_LAYER_STACK`) depending on device/display state, so §3 below is the documented recovery.

Everything else in the playbook — detecting the device (§0), launching / deep-linking (§1), `uiautomator` inspection (§2), `logcat` (§6), project specifics (§7) — stays in `SKILL.md`, because it is used in the Maestro path too. Resolve a serial once per §0 and pass `adb -s "$SERIAL"` on every command below.

---

## 1. Input (tap / type / swipe / keys)

```bash
adb shell input tap <x> <y>
adb shell input text "hello%sworld"                 # %s = space; escape special chars
adb shell input swipe <x1> <y1> <x2> <y2> <ms>      # e.g. dismiss a bottom sheet: swipe down
adb shell input keyevent KEYCODE_BACK               # back; also KEYCODE_ENTER, KEYCODE_TAB
```
Put a short `sleep` between actions when recording video so the flow is watchable and the UI settles.

---

## 2. Screenshot

```bash
adb exec-out screencap -p > shot.png                # preferred: straight to a local file
# or: adb shell screencap -p /sdcard/s.png && adb pull /sdcard/s.png shot.png
```
Then `Read` the PNG to inspect it. **Scaling note:** a screenshot shown scaled in your tool (e.g. displayed 891×2000 for a 1080×2424 screen) needs a multiply factor to map displayed→real coords — but you should be tapping from `uiautomator` bounds (real pixels), which sidesteps this entirely.

---

## 3. Record video

`screenrecord` **blocks**, so background it, drive the UI, then stop it so the mp4 finalizes:

```bash
# start (background); --time-limit max 180s, default 180:
adb shell screenrecord --time-limit 50 --bit-rate 8000000 /sdcard/demo.mp4 &
# ... perform the flow with input taps + small sleeps ...
adb shell pkill -INT screenrecord        # graceful stop so the file finalizes (or let --time-limit elapse)
sleep 2
adb pull /sdcard/demo.mp4 demo.mp4
ls -la demo.mp4                          # sanity-check it's non-trivial in size
```
Navigate to the starting screen **before** starting the recording, so the clip is short and focused on the change.
