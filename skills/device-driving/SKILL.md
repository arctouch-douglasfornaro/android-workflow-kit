# Device driving — Maestro-first playbook

**Maestro is the required driver** for reaching a screen, interacting, asserting state, and capturing screenshots/video on a connected device. Whenever a device is connected you install Maestro if it is missing (§9) and drive every navigation / interaction / capture step through a Maestro flow — selectors by **text / resource-id**, never raw coordinates.

`adb` is **not** the default driver. It is kept for exactly two roles:
1. **What Maestro cannot do** — chiefly **`logcat`** (§6), plus build/install, read-only device-state / UI inspection (§0, §2), and the token-cheap intermediate checks in §8.5. `logcat` evidence always uses adb; that is expected, not a fallback. (The fallback rule below is about *driving* the UI — navigation + interaction + formal evidence capture — not these read-only utilities.)
2. **Fallback driver** — only when Maestro genuinely cannot run in this environment (no network / sandboxed CI / permissions) or a specific capability has no Maestro equivalent, and only with the reason recorded per the fallback rule in §9.

The hand-driving adb primitives (input, screenshot, screenrecord) live in **[`adb-fallback.md`](./adb-fallback.md)** — a sibling file, so they cost nothing to have on hand and nothing to load when they are not needed. Reach for them to *drive the UI* only when the §9 fallback rule applies.

`adb` is at `~/Library/Android/sdk/platform-tools/adb`; the Maestro CLI is at `~/.maestro/bin/maestro` (§9). Both are usually on PATH.

If `android` (Android CLI) is on PATH, use it for **stills and layout dumps** (`android screen capture`, `android layout`) per `android-cli` / `android-screenshots`. It does **not** replace Maestro as the UI driver.

---

## 0. Detect the device first (always)

```bash
adb devices -l        # confirm exactly one device is connected & authorized
```
**Resolve a serial once and use it everywhere.** "Exactly one device" is an assumption that breaks the moment an emulator is running alongside a phone, and every subsequent `adb` call then fails with `more than one device/emulator` — which, piped through `2>/dev/null`, looks like an empty screen rather than an error. Capture `SERIAL=$(adb devices | awk '/device$/{print $1}' | head -1)` at the start of the session and pass `adb -s "$SERIAL"` from then on.
If none is connected, do not fail the phase — degrade per your agent's rules (recipe `unverified` / manual-test `BLOCKED` / video `pending`). Multiple devices → target one with `adb -s <serial> ...`.

Once a device is confirmed, **ensure Maestro is installed (§9) before driving** — it is the required driver, so the install/availability check is part of setup, not an afterthought.

Useful state:
```bash
adb shell dumpsys activity activities | grep -iE "topResumedActivity|mResumedActivity"   # current screen
adb shell pm list packages | grep <name>                                                 # is the app installed
adb shell dumpsys package <pkg> | grep -iE "versionName|lastUpdateTime"                   # which build
```

---

## 1. Launch the app

```bash
# Launch the default launcher activity. NOTE: the debug build also has a LeakCanary launcher,
# a debug build may ship extra launchers (LeakCanary and friends), so `monkey`/generic LAUNCHER
# can open the wrong app — prefer starting the real entry activity by name:
adb shell am start -n <debug-package>/<entry-activity>     # both from the project profile

# Deep link into a screen via the EXPORTED router, forcing the package (this reaches many
# NON-exported screens, because the exported interstitial/router activity resolves the URL
# internally). The trailing <pkg> is what makes it hit the app, not the browser:
adb shell am start -W -a android.intent.action.VIEW -d "<web-deep-link>" <pkg>
# the app's own scheme works too: -d "<app-scheme>://..."   (both from the project profile)
```
**Gotcha:** `am start -n <pkg>/<activity>` on a **non-exported** activity fails with a `SecurityException`. Prefer the **deep link with the package forced** above (routes through the exported interstitial). Only when a screen has *no* route fall back to launching the app and navigating in-app, or through an in-app developer console if the project has one (see § 7), and record that gap. Note these adb `am start` commands are primitives — for navigate-and-capture you drive through **Maestro (§ 9)** (`launchApp` + in-app taps); use raw adb launch only under the §9 fallback rule.

---

## 2. Inspect the UI to find tap targets (do this instead of guessing coordinates)

**The dump is a single line of XML.** This matters more than anything else in this section: `grep`, `head`, and `tail` are all line-oriented, so piping the raw dump through them returns the *entire tree* — tens of thousands of tokens for one screen. Split it into one node per line **before** filtering, and extract only the node you need:

```bash
# Find one node and print just its bounds + identifying attributes:
adb exec-out uiautomator dump /dev/tty 2>/dev/null \
  | tr '<' '\n' \
  | grep -iE 'devMode|Continue' \
  | grep -oE '(text|resource-id|content-desc)="[^"]*"|bounds="[^"]*"'
```

The `tr '<' '\n'` is what makes the filter work at all. Without it every pipeline below returns the whole document. Measured on one real screen: the same lookup returned **43,873 chars (~11k tokens) without the split, and 57 chars (~14 tokens) with it**.

**Two silent-failure traps around this command:**

- The multi-device trap from §0 bites hardest here: `2>/dev/null` swallows `more than one device/emulator`, so the dump comes back **empty** and reads as "nothing on screen". Pass `adb -s "$SERIAL"` before concluding a screen is empty.
- An empty dump also means the screen is off or locked. `adb -s <serial> shell input keyevent KEYCODE_WAKEUP` before concluding anything from an empty result.

```bash
# Raw dump (only when you genuinely need the whole tree — expect a very large result):
adb exec-out uiautomator dump /dev/tty
# or: adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml ui.xml
```
Find the target node, read its `bounds="[x1,y1][x2,y2]"`, and tap the center `((x1+x2)/2, (y1+y2)/2)`. Match nodes by `resource-id`, `text`, or `content-desc` so the step survives screen-size changes.

**Prefer Maestro's `assertVisible`/`tapOn` by text or id over dumping at all** (§9) — a dump is a debugging tool for when a selector does not match, not the normal way to drive a screen.

---

## 3–5. adb input, screenshot and video primitives → `adb-fallback.md`

Driving the UI by hand — `input tap/text/swipe/keyevent`, `screencap`, `screenrecord` — is **fallback-only** (§9), so it lives in a sibling file instead of loading on every run: **[`adb-fallback.md`](./adb-fallback.md)**. Open it when, and only when, the §9 fallback rule actually fires (Maestro genuinely cannot run here) or you need `screenrecord` because a Maestro recording failed. In the normal Maestro path none of it is needed: `tapOn`/`swipe`/`inputText` drive the UI, `takeScreenshot`/`startRecording` capture it.

---

## 6. Logs — logcat (adb; Maestro has no equivalent)

Reading device logs is the one driving-adjacent capability Maestro does not provide, so `logcat` **always** stays on adb — even in an otherwise 100%-Maestro run. This is not a fallback; it is the expected division of labor (Maestro drives the UI, adb reads the log). It is often the strongest objective evidence a manual test has (e.g. observing a network request fire at a specific moment), so keep using it.

```bash
adb logcat -c                                                              # clear before the action
# ... perform the action ...
adb logcat -d | grep -iE "exception|fatal|http|401|403|<feature-keyword>" | tail -60
adb logcat -d --pid $(adb shell pidof -s <pkg>) | tail -80                 # scope to the app's process
```

---

## 7. Project specifics — read them, don't assume them

Everything concrete about *which* app you are driving — debug package, entry activity, deep-link scheme
and host, an in-app developer console and what it can launch, known test data — is **project data, not
skill content**. Read it from the project's `project-profile` skill when it has one.

When the project ships no profile, derive the same facts and record how:

| Fact | Where to derive it |
|---|---|
| Debug package | `adb shell pm list packages -3`, or the Gradle `applicationId` + debug suffix |
| Entry activity | `AndroidManifest.xml` → the `LAUNCHER` intent filter |
| Deep-link scheme / host | `AndroidManifest.xml` → `VIEW` intent filters and their `data` elements |
| Reachable-only-in-app screens | activities with no exported route; look for an in-app debug console |
| Test data | ask; never fabricate credentials or account data |

Two build lessons that are **not** project-specific, and bite anywhere:

- **A worktree build needs its submodules populated** — `git worktree add` does not initialize them, and
  `git submodule update` can fail on an SSH-vs-HTTPS remote mismatch. Copying the content from the primary
  checkout is the reliable fallback.
- **Verify the install landed; don't trust the log.** Piping the build into `tail` hides its exit code, so
  a failed build reads as a success. Check the device instead:
  `adb shell dumpsys package <pkg> | grep lastUpdateTime` and confirm the timestamp is *now*.

---

## 8. Gotchas (learned the hard way)

Only what is not already stated where you would hit it — the `am start` / non-exported trap is in §1, the dump-vs-coordinates rule in §2, the `screenrecord` blocking behaviour in [`adb-fallback.md`](./adb-fallback.md) §3.

- A freshly installed `.debug` build is a **separate install** with its own login/state — a feature needing auth/data may need login first; record that as a prerequisite rather than assuming it.
- A green Maestro run that produced no files is `--flatten-debug-output` (§9), not a device fault; a Maestro run that drove the wrong hardware is a missing `--device` (§9).

---

## 8.5. Keep device sessions token-cheap

Device-driving agents are the most expensive part of `feature-workflow` runs in both time and tokens — on one measured run the three separate device agents this replaced (`feature-navigator` + `manual-tester` + `visual-evidence`) were ~62% of total agent time and ~65% of total tokens, almost entirely because every `uiautomator dump`, `screencap`, and `logcat -d` call injects a large blob back into context, and that happens dozens of times per run. None of these are needed at full size most of the time — trim before it becomes a habit, not after the context is already bloated:

- **Reuse recorded bounds instead of re-dumping.** If `07-device.md`'s recipe section already has a node's `bounds="[x1,y1][x2,y2]"` for a tap target you need again, tap it directly — don't re-run `uiautomator dump` to rediscover coordinates you (or a prior phase) already found. Only re-dump when the UI has genuinely changed (a new screen, a different app state) or the recorded bounds fail.
- **Filter `uiautomator dump` at the source, splitting it into lines first** — the §2 pipeline, not a bare `grep`. Unsplit, the dump is one line and every filter returns the whole tree: measured, 11k tokens versus 14 for the same lookup.
- **Prefer a small state check over a full screenshot for intermediate verification.** `adb shell dumpsys activity activities | grep -iE "topResumedActivity|mResumedActivity"` (a few lines) confirms "did navigation land where expected" just as well as a screenshot + visual inspection, for a fraction of the tokens. Reserve actual `screencap` calls for moments that are genuinely evidence-worthy (the final state recorded in `07-device.md`), not every intermediate tap.
  **But only when the hop crosses an activity.** `topResumedActivity` is blind to anything that happens *inside* one — a Compose destination, a bottom sheet, a dialog, a server-driven modal all leave it unchanged, so it will happily report success for a navigation that never occurred. For an in-activity hop the cheap check is the §2 pipeline reduced to text (`… | tr '<' '\n' | grep -oE 'text="[^"]+"'`), which is still two orders of magnitude smaller than a screenshot; a Maestro `assertVisible` on the expected string is cheaper still, since it costs nothing beyond the run's one-line output.
  Cheapest first, for the same question: `assertVisible` in the flow → filtered text dump → `dumpsys` (cross-activity only) → screenshot (evidence only).
- **Scope `logcat` reads.** Always `adb logcat -c` before the action, then read with a targeted filter (`grep -iE "exception|fatal|<feature-keyword>"`) and a `tail -N` cap — never read an unscoped, unfiltered log back into context.
- **Batch related adb calls into one Bash invocation** (e.g. a tap, a short sleep, then the next tap, chained with `&&`/`;` in one command) rather than one tool call per micro-step — fewer round trips means less repeated context overhead across a long device session.
- **One continuous recording beats many discrete screenshots** when a flow needs to be shown or verified across several steps — see the single `device-pass` session in [`feature-workflow/device.md`](../../workflows/feature-workflow/device.md).

---

## 9. Maestro (the required driver — install if missing)

Maestro drives the device with a declarative YAML **flow** — built-in implicit waits (no hand-rolled `sleep`/poll), taps by text/id (no coordinates), and screenshots/video in-flow. It is faster and far more deterministic than hand-driven adb, so it is **the driver you use** for reaching a screen, asserting state, and capturing evidence — not a preference to weigh against adb each time. Whenever a device is connected: check availability, **install it if missing**, and run the flow. Maestro still needs the app **installed** on the device (it does not remove the build+install step — for a *static* UI change with no reachable screen, a screenshot may not even be needed; capture from the closest reachable state).

**Setup — check availability before the first Maestro call of a run:**
```bash
command -v maestro || ls ~/.maestro/bin/maestro 2>/dev/null   # on PATH, or at the default install path?
```
If neither resolves, install it once (official installer, no separate download step):
```bash
curl -Ls "https://get.maestro.mobile.dev" | bash                # installs to ~/.maestro/bin
~/.maestro/bin/maestro --version                                 # confirm it landed
```
**Fallback rule (the only time you drive with adb instead).** Installing Maestro is mandatory whenever a device is connected — a device being present but Maestro simply "not tried" is not acceptable. The single permitted reason to drive the UI with the [`adb-fallback.md`](./adb-fallback.md) primitives instead is that Maestro **genuinely cannot run here**: the install truly fails (no network, sandboxed CI, permissions) or a required capability has no Maestro equivalent. In that case record `Tool used: adb (Maestro unavailable — <reason>)` with the specific failure, and drive the rest with adb. This is an escape hatch for a real blocker, not a shortcut. Do the check/install **once per run** — `device-pass` owns recipe, manual AC and evidence in one session, so it probes once and reuses the result (installed path, or the recorded unavailability reason) for the whole pass. (`logcat` on adb per §6 is **not** covered by this rule — it always stays adb regardless of Maestro.)

- CLI: `~/.maestro/bin/maestro` (or wherever the setup check above resolves it — no version pinned, whatever the installer lands is fine). No Maestro MCP — agents shell out via `Bash`.
- Flows: reusable ones live in **`.maestro/`** (committed; see `.maestro/README.md` + `.maestro/_template.yaml`); one-off flows can live in the run folder `.ai/workflow/<feature-id>/`.

**Run:**
```bash
~/.maestro/bin/maestro --device <serial> test .maestro/<flow>.yaml        # ALWAYS name the device — see below
~/.maestro/bin/maestro --device <serial> test -e SET_ID=415 .maestro/<flow>.yaml      # params → ${SET_ID} in the flow
~/.maestro/bin/maestro --device <serial> test --format junit --output result.xml .maestro/<flow>.yaml   # JUnit → PASS/FAIL
```
**`--device` is not optional when more than one device is attached: Maestro ignores `ANDROID_SERIAL`.** Exporting it (the way you would for gradle/adb) does not steer Maestro — with a phone and an emulator both attached it picks its own target, observed choosing the **physical phone**, so a flow silently drives the user's real device instead of the emulator you built for. Take the serial from §0 and pass `--device "$SERIAL"` (alias `--udid`) on every invocation. Maestro prints the device it chose on the first line of its output (`Running on …`) — read that line before trusting a result.

**Flow vocabulary (verified):**
```yaml
appId: <debug-package>        # from the project profile
---
- launchApp                                          # most reliable entry
- tapOn: "Library"                                   # tap by text / id / index — never coordinates
- assertVisible: "Today's spaced repetition plan"    # implicit wait; fails the flow if absent (drives PASS/FAIL)
- takeScreenshot: srs_start_modal                    # → <run-dir>/takeScreenshot/srs_start_modal.png (NOT the CWD)
- startRecording: srs_flow                           # video; MUST be paired with stopRecording
- stopRecording                                      # finalizes srs_flow.mp4
- runFlow: common/login.yaml                         # compose reusable subflows (login, dismiss dialogs)
```

**Output & evidence — captured media does NOT land in the working directory** (verified on 2.8.0):
- One run writes **everything** under a single per-run folder, `~/.maestro/tests/<timestamp>/<flow-name>/`:
  `takeScreenshot/<name>.png`, `startRecording/<name>.mp4`, `logs/maestro.log`, `logs/device-logcat.txt`, `commands.json`, `manifest.json`. Looking for `<name>.png` next to the flow file finds nothing — that is the layout, not a failed capture.
- To collect them somewhere you choose, pass **`--test-output-dir <dir>`**; the same `<timestamp>/<flow>/…` tree is created inside `<dir>` rather than under `~/.maestro/tests/`.
- **Never add `--flatten-debug-output`.** Combined with `--test-output-dir` it produces **no artifacts anywhere** — every step still prints `COMPLETED`, the output dir is never created, and nothing lands under `~/.maestro/tests/` either (reproduced twice on 2.8.0). A green run with no files is this flag, not the device.
- Whichever path they came from, copy the captured media into `.ai/workflow/<feature-id>/media/` — that is the canonical home for evidence (see the workflow's § Device-based verification).

**Caveats (verified on this repo/device):**
- **`openLink: "https://…"` opens the system browser**, not the app (App Links aren't verified for the debug build). To deep-link into an in-app screen, use adb with the package forced (§1) or `launchApp` + in-app taps — don't rely on Maestro `openLink` for app navigation.
- **Video recording uses `adb screenrecord`** under the hood and can fail with `UNASSIGNED_LAYER_STACK` depending on the device/display state. **Screenshots are reliable; recording is not guaranteed.** Keep the screen on/unlocked; if recording fails, fall back to a screenshot (or `adb screenrecord`, [`adb-fallback.md`](./adb-fallback.md) §3) and record the gap.
- A failing `assertVisible` aborts the flow **before** any later `stopRecording`, so a video isn't saved on failure — assert after you've captured what you need, or capture in a separate flow.
