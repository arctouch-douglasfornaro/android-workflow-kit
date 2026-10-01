# Worked Example: Making a README Demo Section Impossible to Scroll Past

A real session. The target was a design problem with a stale convention baked into it:

> "How do we make this repo's README demo section impossible to scroll past?"

Six provocations were generated across four of the five operations (Escape, Reversal, Exaggeration, Distortion, Wishful thinking). Four produced movement; one was abandoned after two honest attempts; one was a half-hit. The abandonment is shown, because it is the point.

---

## Opening framing

State briefly why the technique works, once, before the first provocation:

> The phrase "demo section" already contains every assumption that makes README demos forgettable: a block of markdown, below the intro, that the reader scrolls at their own pace and past at their own pace. Staying inside that frame only produces "make the GIF nicer". A provocation breaks the frame by asserting something false about it — no demo, reader-runs-it, ten thousand demos — and the useful part is never the false statement itself. It is the movement the statement forces: the principle underneath, what would happen moment to moment, and what that makes different from the README we have.

---

## Surfacing the rules

The things so obvious nobody writes them down:

1. The demo is static — markdown rendered on a page, a GIF or a code block.
2. It sits in reading order: intro, then badges, then demo, then install.
3. It shows the tool working — one representative example.
4. The reader controls the pace and can scroll past at any moment.
5. It is generic — the same demo for every visitor.
6. The reader is skimming, deciding in seconds whether to keep going.

Each of these is a wall to break in the next step.

---

## The provocations, with movement

### Po: the README has no demo section. (Escape)

Drops rule 1 and 3 entirely.

- **Principle:** if there is no demo *block*, then the demonstration has to live somewhere else — either everywhere or nowhere. "Everywhere" is the interesting branch.
- **Moment-to-moment:** the reader lands on the README and there is no GIF to scroll to. Instead the first code fence *is already the tool doing something real* — the very first thing under the title is output, not prose.
- **Difference:** today the demo is a destination you scroll to and can therefore scroll past. With no section, the demonstration is dissolved into the intro itself — the reader is inside the demo before they know one was coming.

**Idea: dissolve the demo into the first screen.** No "## Demo" heading at all. The headline is replaced by a single line of real output — the most striking thing the tool produces — rendered before any description of what the tool is. You cannot scroll past a demo that occupies the position of the title. Strong: it removes the scroll-past problem by removing the thing that gets scrolled past.

### Po: the reader performs the demo and the README watches. (Reversal)

Inverts rule 4 — the reader is normally the passive audience.

- **Principle:** engagement that the reader *produces* cannot be skimmed the way engagement that is *served* can. If the reader's own action is the demo, there is nothing to passively scroll past.
- **Moment-to-moment:** the reader reaches the demo and it asks them for one input — paste your own file, your own URL, your own snippet — and the README section shows the tool's result *on their thing*, not on a canned example.
- **Difference:** today every visitor sees the same generic example and correctly assumes it was cherry-picked. A demo run on the reader's own input is not cherry-picked, and the reader has now spent effort, which is the strongest anti-scroll force there is.

**Idea: a "paste yours" line.** A README cannot execute code, but it can carry a single copy-paste one-liner positioned exactly where the demo GIF would be: "Run this on *your* repo: `<one command>`". The demo the reader can't scroll past is the one they run in their own terminal ten seconds later. Strong, and it reframes the whole section from "watch ours" to "try yours".

### Po: the demo takes zero screen space. (Exaggeration → zero)

Pushes rule 1's footprint to nothing.

- **Principle:** a demo with zero height cannot be scrolled past because there is no scrolling involved — it has to communicate in the glance the reader already gives the top of the page.
- **Moment-to-moment:** in the half-second before the reader decides to scroll, they have already seen the entire demo, because it is one line wide and zero paragraphs tall.
- **Difference:** today the demo asks for a scroll and a play. A zero-space demo asks for nothing and lands in the pre-scroll glance.

**Idea: the one-line before/after.** A single line: `input → output`, the most dramatic transformation the tool makes, rendered as one code line at the very top. It costs no scroll. Lands, but it is really the same insight as the Escape idea — dissolve the demo up into the glance — arriving from a different direction. Kept, noted as convergent.

### Po: the reader sees the demo's result before the demo runs. (Distortion)

Scrambles the order in rule 2 — result normally comes after setup.

- **Principle:** leading with the outcome and withholding the mechanism creates a gap the reader wants closed, and a reader who wants something cannot skim past it.
- **Moment-to-moment:** first thing on the page is a surprising finished result with no explanation. The reader's next thought is "wait, how" — and the *how* is the rest of the README.
- **Difference:** today the README explains, then demonstrates, so by the demo the curiosity is already spent. Reversing it spends nothing up front and makes the demo the hook rather than the reward.

**Idea: result-first framing.** Open with the punchline output and a one-word caption, then "Here's how" as the pivot into everything else. This is a *sequencing* idea, and it composes with the two above rather than competing — it tells you where to put the dissolved demo (first, unexplained), not what the demo is. Lands as a structural rule for the section.

### Po: the demo section is ten thousand demos long. (Exaggeration → infinity)

Pushes rule 3's "one example" to an absurd count. This is the one I tried to make pay and could not.

- **First attempt — principle:** ten thousand demos means the reader always finds one matching their exact case, so... a gallery? But a gallery is *more* to scroll past, not less. That fights the target directly. The exaggeration pushed toward volume, and volume is the enemy here.
- **Second attempt — moment-to-moment:** what if the ten thousand are not shown but *searched*, one surfaced per reader? That is just the Reversal idea ("run it on yours") wearing a bigger number, or a personalization idea that a static README cannot deliver. Every fit either restates a stronger provocation or requires a runtime the README does not have.

`Po: the demo section is ten thousand demos long — no movement found after two attempts. Moving on.`

Honest reason: "impossible to scroll past" is a problem of *compression* — getting the demo into the glance before the scroll. Exaggerating toward *more* pushes the opposite way, and every attempt to rescue it either inverted back into compression (already covered) or leaned on personalization a rendered markdown file cannot do. It did not fail because the operation is weak; it failed because it pointed away from the target.

### Po: the README already knows the exact problem this reader came to solve. (Wishful thinking)

States rule 5's opposite — perfect per-visitor relevance — as accomplished fact.

- **Principle:** the most un-scrollable demo is the one showing the reader's own pain solved. Total personalization is impossible in static markdown, but the *fraction* that is reachable is choosing the single most common arriving pain and demoing exactly that.
- **Moment-to-moment:** the reader arrives carrying one specific frustration; the first line of the README names that frustration back to them and shows it gone.
- **Difference:** today the demo shows what the tool *can* do (broad, impressive, generic). This shows the one thing most readers *came for* (narrow, specific, recognized). Recognition beats impressiveness at stopping a scroll.

**Idea: demo the arriving pain, not the feature set.** Replace the representative demo with the single most common reason people land on this repo, stated as their words, then solved in one line. Half-hit — genuinely useful as a *selection rule* for what the demo should contain, but it produces a criterion rather than a mechanic, and it depends on actually knowing why people arrive. Kept as a weaker, contingent direction.

---

## The meta-pattern

Four landed, one was a half-hit, one was abandoned. Scanning only what landed — dissolve-into-first-screen, paste-yours, one-line before/after, result-first:

**Every direction that paid off made the demo *smaller and earlier*, never bigger.** The section became one line, or the title itself, or a copy-paste the reader runs, or the unexplained opening result. Not one strong idea added content to the section; all of them compressed it upward into the reader's first glance.

So the structural insight: **"impossible to scroll past" is not an attention problem, it is a position problem.** You cannot win a scroll you have already lost by making the thing you scroll to more compelling — you win by moving the demo *above* the scroll, into the half-second before the reader decides. That single reframe explains all four winners at once.

And it explains the abandonment exactly. `Po: ten thousand demos` was the only provocation that pushed toward *more and lower*, and it produced nothing, because it pointed the opposite way from the pattern the target actually rewards. The abandonment was not noise; it was the negative space that confirmed the shape of the answer.

---

## Honest ranking

Strongest, in order:

1. **Dissolve the demo into the first screen** — no "## Demo" heading; the most striking real output *is* the top of the page. Directly dissolves the scroll-past problem by removing the scrollable target. The before/after one-liner and result-first sequencing are really instructions for *how* to do this one, not rivals to it.
2. **Paste-yours** — a copy-paste one-liner where the GIF used to be, so the un-skippable demo is the one the reader runs on their own input. Highest engagement, lowest control over what the reader then sees.
3. **Result-first sequencing** — open with the unexplained punchline, pivot to "here's how". A structural rule that composes with #1 rather than standing alone.

Weaker:

4. **Demo the arriving pain** — a strong *selection rule* for the demo's content, but contingent on knowing why readers arrive, and it yields a criterion rather than a mechanic.

Abandoned: **ten thousand demos**, which pushed toward volume when the target rewards compression — shown rather than hidden, because pretending it worked would have hidden the very insight that makes the winners make sense.

Next moves, your call: run a fresh batch aimed only at the "paste-yours" branch, go deep on how to write the dissolved first line, switch to a convergent technique to pick between #1 and #2, or stop here.
