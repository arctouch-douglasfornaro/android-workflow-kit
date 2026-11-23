---
name: investigate-first
description: Find the cause of a failure before anyone edits code. Use for a bug report, a crash or stack trace, a flaky test, intermittent behavior or a performance regression whose cause is unknown; it ranks hypotheses by evidence and reports the cause with file:line, or the exact blocker. Never edits product code.
---

# Investigate first

Find why something fails before changing anything. The output is a cause with its proof, or the one
fact that is still missing; a fix comes later, and only when the task asks for one.

## How

1. **Write down the symptom as observed**: what happens, where, on which build, device or input, and
   what was expected. Keep it apart from any guess about the cause; a report's own diagnosis is a
   hypothesis like any other.
2. **Trace the path** from the input to the failure: the entry point (screen, intent, API call, test),
   the state it reads and changes, the boundaries it crosses (thread, process, module, network, storage)
   and where the failure shows (exception, log, wrong value, frame). Read the code along it; cite
   `file:line`.
3. **List the hypotheses** that could explain every observed fact, and rank them by how much evidence
   supports each and how cheap it is to rule out. Run the cheap checks first: a log line, a `git log`
   or `git blame` on the path, a unit test that should fail, one run on a device.
4. **Stop** when one mechanism explains all the evidence and the others are ruled out, or when the next
   step needs something you cannot get (a log from the user's device, an account, a build). Do not keep
   exploring past that point.

Never edit product code while investigating. A test or a script that reproduces the failure is fine
when it lives outside the product code or the task allows it.

## Report

- **Symptom**: one or two sentences, as observed.
- **Cause**: the mechanism, with `file:line`, or "unknown" with the leading hypothesis.
- **Evidence**: what proves it (the reproduction, the log line, the failing test, the commit that
  introduced it), and what ruled out each other hypothesis, one line each.
- **Blocker**: the missing fact and who can give it, when the cause is not proven.
- **Next step**: the smallest fix or the next check. Propose it; do not apply it unless asked.

Keep personal data out of the report: redact emails, tokens and account ids from logs before quoting
them.
