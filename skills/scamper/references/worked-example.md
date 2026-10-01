# Worked Example: SCAMPER-ing a toolkit's install experience

A real run. The target was an existing, working thing — the install experience of this very toolkit:

> "SCAMPER the install experience of this toolkit (`npx skills add danium/lateral-thinking`)."

Context taken as given: the toolkit is a free MIT-licensed collection of 8 lateral-thinking technique skills plus a router skill named `lateral`. It installs as one bundle because the router needs its sibling techniques present to hand off to them. Four install channels exist today: `npx skills add danium/lateral-thinking` for any agent; a plugin marketplace; a skill-installer; and manual clone-and-copy of `skills/*`.

All seven operations were run in order. Four bit; three came up empty and are shown empty, because the empties are what prove the run was honest.

---

## The target, confirmed

This is a valid target: an existing process that already works, with a part that is clearly fixed (the toolkit contents) and parts that are open (how it arrives, in what shape, in how many steps). Not a blank page, not an analytical task. Proceeding.

---

## The seven operations

### Substitute — mostly empty

Questions pulled: *What if a different actor did this step? Could a cheaper or more common material replace the expensive one? What if you substituted the input format?*

An install is user-initiated by its nature — someone decides to add the toolkit and types a command. There is no other actor to hand the trigger to, and no time-based or system-initiated version that means anything. The input format is already a single command line; there is nothing heavier to swap out for something lighter.

The one thing worth swapping — the transport itself, installer versus raw git — is already a channel the toolkit offers (manual clone-and-copy). So Substitute is not inventing anything new here.

> **Substitute:** nothing new. The command is already swapped down to one line, and the only meaningful substitution (git for installer) already ships as a channel. Moving on.

### Combine — bit

Questions pulled: *What if setup and first use were the same moment rather than two? Could you combine the separate channels into one? What if two steps happened in a single action?*

This is where the target opened up. Right now install and first use are two distinct moments: you run the install command, then later you invoke a technique. But a router-based toolkit could fuse them — a single command that installs *and* runs the router against a problem the user already has in hand, so the first thing the user sees is the toolkit working, not a success message.

Separately, four channels is four things to document and keep in sync. Combining them behind one detected entry point — one command that figures out whether it is in a plugin-aware agent, a skill-installer, or a bare shell, and does the right thing — collapses the surface.

**Variant A — "Install-and-run."** One command installs the bundle and immediately runs the router on a prompt the user passes in.
- *Changes:* the command takes an optional problem string; on success it hands off to the router instead of printing a receipt.
- *Costs:* the installer now depends on the router's runtime, not just the file copy; more to build and test.
- *Risks:* users who just want the files installed now get a technique run they did not ask for, unless it is opt-in.

**Variant B — "One detected entry point."** A single documented command that auto-detects the environment and dispatches to the right channel underneath.
- *Changes:* one command in the README; the four channels become internal implementation, not user-facing choices.
- *Costs:* detection logic to write and maintain across agents; a fallback path when detection fails.
- *Risks:* magic that guesses wrong is worse than an honest menu; a mis-detection strands the user with no obvious manual override.

### Adapt — bit

Questions pulled: *How does a neighboring tool your users already know solve this? What established standard could you conform to? What pattern from package managers maps onto this?*

The toolkit already half-implements a solved pattern without naming it. "It installs as one bundle because the router needs its siblings" is exactly the dependency-resolution problem every package manager solved decades ago. Today the toolkit resolves it by the blunt method of shipping everything together. The adaptable pattern: let the router *declare* its siblings as dependencies, and let the installer resolve them — the same shape as a manifest with a dependency list.

**Variant C — "Declared dependencies."** The router carries a manifest listing the sibling skills it requires; the installer reads it and fetches exactly those.
- *Changes:* add a dependency declaration to the router; the installer resolves and fetches per the declaration rather than copying a fixed bundle.
- *Costs:* a manifest format to define and a resolver to write, for a set that is currently small and static.
- *Risks:* real complexity added for a bundle that fits in one download today — this pays off only if the toolkit grows or techniques start shipping separately.

### Modify / Magnify — bit

Questions pulled: *What if you shrank the scope to one perfect case? What if this were the single most prominent thing? What would a "lite" version strip?*

Magnify the router's centrality until it is the *only* thing installed up front. Today all nine skills land together. Shrink that: install just the router, and let it pull each technique the first time it actually routes to it. The router becomes the product; the techniques become on-demand payload.

**Variant D — "Router-only install, lazy siblings."** The install places only the router; each technique is fetched the first time the router hands off to it.
- *Changes:* default install shrinks to one skill; techniques arrive just-in-time on first use.
- *Costs:* the router needs a fetch-on-demand capability and a place to put what it fetches; a first-use network dependency appears.
- *Risks:* offline or sandboxed environments break on first handoff; the very thing that made the bundle safe (everything present) is given up. This directly trades against the reason the bundle exists.

### Put to other use — empty

Questions pulled: *What is the by-product of this install, and is it more valuable than the install? Could this serve an internal need, not just an external user? What adjacent job could it solve with zero changes?*

The by-product of installing is a directory of technique skills the agent can now enumerate — but that is just discovery of what was installed, not a genuine second use. There is no separate audience for "the act of installing," and the install mechanism does not obviously solve any adjacent job. Every attempt to repurpose it circled back to "it installs the toolkit."

> **Put to other use:** empty. The install does one job and generates no by-product worth repointing. Moving on.

### Eliminate — bit

Questions pulled: *What if there were no configuration — one opinionated default? Could you cut a whole channel? What is the smallest version that still delivers the core value?*

Four channels is the fattest part of this target. Three of them exist to cover environments the primary `npx` path may not reach. Eliminating channels — committing to one blessed path and demoting the rest to a documented footnote — removes documentation drift and decision fatigue at install time.

**Variant E — "One blessed channel."** Pick the single path that works in the most environments, feature it alone, and move the others to a "manual / advanced" note.
- *Changes:* the README presents one command; the marketplace, installer, and clone paths become fallback documentation.
- *Costs:* users in environments the blessed path misses now start on the slow road (reading the fallback note).
- *Risks:* choosing wrong strands whichever audience relied on a demoted channel; the toolkit's reach narrows to wherever the blessed path runs.

### Reverse / Rearrange — empty (as its own idea)

Questions pulled: *What if you delivered first and configured later? What if the sequence ran backward? What if the default and the exception traded places?*

The promising reversal here is "install the router first, fetch the techniques afterward" — deliver the entry point, defer the payload. But that is exactly Variant D from Modify/Magnify, reached from a different direction. Under the no-disguised-duplicates rule, it counts once, and it is already counted.

Beyond that, the install sequence is short and linear — fetch, copy, done. There is no meaningful order to flip, no configure-then-use step to reverse, because there is barely a sequence at all.

> **Reverse / Rearrange:** empty as a distinct idea. The one real reversal (router-first, siblings later) is Variant D under another name, so it counts once. Nothing else to flip. Moving on.

---

## The meta-pattern

Four operations bit — Combine, Adapt, Modify/Magnify, Eliminate — and three came up empty: Substitute, Put to other use, and Reverse/Rearrange (whose only hit was already a duplicate).

Scanning the four that bit, they cluster into two themes, and both point at the same soft spot:

1. **The bundle is doing crude work.** Adapt (declared dependencies) and Modify/Magnify (router-only, lazy siblings) both attack the same thing: the toolkit ships everything together because "the router needs its siblings," and neither the dependency relationship nor the fetch timing is expressed as a real mechanism — it is hardcoded as "put it all in one download." That is the softest part of the idea. Three of the four hits push on it.

2. **The channel count is unmanaged surface.** Combine (one detected entry point) and Eliminate (one blessed channel) both attack the four-channel spread from opposite directions — one unifies them behind detection, the other demotes all but one. Two hits, same target.

That Substitute and Put-to-other-use came up empty is itself informative: the *command* and the *purpose* of the install are already tight — there is nothing to swap and nothing to repurpose. The install experience is not soft at its surface. It is soft in its **middle**: the unstated dependency between the router and its siblings, and the unmanaged fan-out of channels. SCAMPER pointed straight at both without being told where to look.

---

## Honest ranking

Strongest first:

1. **Variant B — one detected entry point.** Highest leverage for lowest conceptual risk. It hides the four-channel mess behind one command without giving up any channel; the only real hazard is mis-detection, which a manual override defuses.
2. **Variant D — router-only install, lazy siblings.** The boldest idea and the one that most directly reframes the product (the router *is* the toolkit). But it trades away the exact safety the bundle was built to guarantee — everything present, works offline. High risk, high reward; ship only with a robust fallback.
3. **Variant E — one blessed channel.** Simplest to do, real reduction in drift. Weaker than B because it throws reach away rather than hiding complexity; strictly a subset of what B achieves, so it matters mainly if detection (B) proves too costly to build.

One that is real but premature:

4. **Variant C — declared dependencies.** The correct pattern in the abstract, and the right answer if the toolkit grows or techniques start shipping independently. Today it adds a manifest and a resolver to solve a problem a single download already solves. Hold it until the toolkit outgrows the bundle.

The weakest listed is **Variant A — install-and-run.** It is clever but answers a question the user may not have: someone running an install often wants the files placed and nothing more. Included because pretending it was strong would be dishonest; it belongs behind an opt-in flag if it ships at all.

Next moves, your call: go deeper on B or D, rerun Eliminate harder now that the channel fan-out looks like the soft spot, switch to a convergent technique to pick between B and D, or stop here.
