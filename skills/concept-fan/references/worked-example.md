# Worked Example: What Is a README a Way of Doing?

A real session. The target was a current solution someone wanted to widen before committing more work to it:

> "A README is a way of doing... what? Fan out alternatives for making this repo's value graspable in 10 seconds."

Context: the repo is a free, MIT-licensed collection of eight lateral-thinking technique skills plus a router, built for AI coding agents, installable via `npx skills add`. The current solution is "write a good README." The brief already suspects the README might be answering the wrong question — which is exactly the case the fan is for.

---

## Step 1: Confirm the target

Valid. There is a concrete current solution ("a README") to climb from, and the ten-second constraint is real. Not a blank page, not analytical work. Proceed.

---

## Step 2: Climb

**Climb 1 — "A README is a way of doing... what?"**

A README is a way of *orienting someone who just arrived and doesn't yet know what they're looking at*. It answers "where am I, what is this, is it for me." So the direction one rung up is: **orient a newcomer at the moment of arrival.**

**Climb 2 — "Orienting a newcomer is a way of doing... what?"**

You only orient people you want to keep. The ten-second grasp isn't the goal; it's the gate. What it really does is get a stranger to spend the *next* two minutes. So the direction two rungs up is: **convert borrowed attention into a decision to try.**

Stopping here. A third climb would land on "grow adoption" or "deliver value" — true, but nothing concrete fans off it. Two rungs keeps contact with the ground.

The two directions:
- **Level 1:** orient a newcomer at arrival.
- **Level 2:** convert borrowed attention into a decision to try.

---

## Step 3: Fan out

### Fan at Level 1 — other ways to *orient a newcomer at arrival*

Alternative concepts that also orient, besides "a document that explains":

- **A1. Show, don't tell** — a demo the newcomer watches instead of reads.
- **A2. Let them touch it** — orientation by doing one real thing immediately.
- **A3. Orient by resemblance** — anchor to something they already understand ("it's `npx create-*`, but for thinking moves").
- **A4. A better README** — write a tighter, clearer README. *(Marked obvious — see Step 5.)*

### Fan at Level 2 — other ways to *convert borrowed attention into a decision to try*

Alternative concepts that also convert, besides "orient them first":

- **B1. Borrow trust from someone they already trust** — the decision is made by proxy before they read anything.
- **B2. Remove the decision entirely** — make trying cost so little there is nothing to decide.
- **B3. Convert on outcome, not on pitch** — let a result they can see do the persuading.
- **B4. "Faster, punchier README copy"** — sharper hero line, better tagline. *(Pruned — see below.)*

**Pruning B4:** "faster, punchier README copy" is the original solution with a coat of paint — it is still a README doing README things, just louder. It does not serve Level 2 by a *different* mechanism; it is Level-1 A4 wearing a marketing hat. Pruned.

---

## Step 4: Drop down

Concrete implementations for the promising concepts.

**A2 — Let them touch it (orientation by doing):**
- Ship a single copy-paste line that runs one technique on a toy problem right in the terminal, output and all, before any install.
- A `try` command that picks a random skill and runs it on a prompt the user types — orientation *is* the first use.

**A3 — Orient by resemblance:**
- Lead with one line that maps the unfamiliar onto the familiar: "eight thinking moves your coding agent can pull off the shelf, installed like any other package."
- A one-row comparison table: "linter is to code style as this is to idea generation."

**B1 — Borrow trust from someone they already trust:**
- Each skill names its source method and its originator up front, so the newcomer trusts the *method's* pedigree, not the repo's.
- Show the router deciding which technique fits a situation — trust transfers from "this thing reasons about when to use itself" rather than from a claim.

**B3 — Convert on outcome, not on pitch:**
- A ten-second before/after: the same prompt answered flatly, then answered after a technique ran. The gap is the entire pitch.
- A gallery of real yields (a reframed metric, a pruned assumption) with zero prose selling them.

---

## Step 5: Mark the obvious branches

- **A4 ("a better README")** is what anyone would have said without the technique. It is the original solution, refined. Marked obvious. It validates the fan — a tighter README genuinely is one real branch — but it is not the yield.
- **A1 ("show, don't tell" / a demo)** is half-obvious: everybody reaches for "add a GIF." Marked partially obvious; it only becomes interesting when it merges with A2 (touch it) into "the demo is something you *run*, not watch."

The yield is the branches you would not have reached from "a README": **B1** (trust by proxy, before reading) and **B3** (convert on a visible outcome, not on copy).

---

## The meta-pattern

Scanning the branches that landed — A2, A3, B1, B3 — one structure recurs:

**Every strong branch moves the persuasion off the README and onto something the newcomer does or sees for themselves.** A2 makes them run it. B1 makes a trusted source vouch. B3 makes an outcome argue. The README, the original solution, is the *one* branch where the repo has to talk about itself — and it was the weakest surviving branch at both levels.

So the structural insight: **the ten-second constraint is not a writing problem, it is a "don't make them read" problem.** The brief asked how to make the README graspable in ten seconds; the fan says the ten-second grasp happens fastest when there is no README to grasp — when the first contact is a run, a borrowed trust signal, or a visible result. That is exactly what climbing to Level 2 exposed and what staying at "a README" concealed.

It also explains the pruned branch: B4 ("punchier copy") failed because it kept the persuasion *on* the README, which is the assumption every strong branch discarded.

---

## Step 7: Honest ranking

Branches worth pursuing, in order:

1. **A2 + B3 merged — a runnable ten-second outcome.** One copy-paste line that shows a flat answer, then the answer after a technique ran. It orients (Level 1) and converts on outcome (Level 2) in the same motion. Strongest branch; it is the only one that satisfies both directions at once.
2. **B1 — borrow trust from the named methods and their originators.** Cheap, honest, and it works before the reader evaluates the repo at all. Lower ceiling than #1 but almost free.
3. **A3 — orient by resemblance** (`npx create-*`, but for thinking). A strong opening *line*, not a strategy; it makes any of the above land faster rather than standing alone.

Weakest that survived: **A4, the better README itself** — kept only because pretending a clear README has no value would be dishonest, but it is the branch the meta-pattern argues *against* leaning on.

Failed nothing: both climbs produced fannable directions, so the fan did not collapse. If Level 2 had landed on "grow adoption" and refused to fan, the honest move would have been to declare the fan failed and reach for the `analogy` technique instead.

Next moves, your call: drop down further on the runnable-outcome branch, re-climb starting from a *different* current solution (say, "the landing page" instead of "the README"), fan wider at Level 2, or stop here.
