# Worked Example: Naming a Code-Churn Feature

A real session. The target was a naming and ideation problem:

> "Suggest names for a feature that shows users which parts of their codebase change most often."

Eight stimuli were drawn across six categories — Time & Cycles (×2), Geography & Landscape (×2), Insects & Microbes, Household Objects, Animals & Creatures, Art & Craft. Six concrete, two abstract. That clears the five-category floor and the two-concrete/one-abstract minimum, and no two same-pool draws sit adjacent. Each chain is labelled with its pool and its kind so you can check all three yourself rather than take this sentence's word for it.

Note that kind is a property of the stimulus, not of its pool: *erosion* sits in Time & Cycles beside *hourglass*, but a process is abstract where an object is concrete. *Heron stalking* is filed under Animals, yet the stimulus is the stillness, not the bird.

Six landed; two were abandoned. Both abandonments are shown, because they are the point.

---

## Opening framing

State briefly why the technique works, once per session, before the first chain:

> Staying inside the problem space — files, commits, diffs — routes every association through the same familiar paths, which is why the obvious name is "Hotspots" and why everyone lands there. A stimulus from outside the space breaks the routing. The first stimuli are usually trash; the third or fourth is where real ideas appear — which is why we draw a batch rather than squeeze one object. Look for a stimulus whose *structural property* the target could have but doesn't yet.

---

## The chains

### 🕰 The hourglass — Time & Cycles [concrete]

Properties: sand falls one way only; the neck is the constraint; you flip it to reset; you can always see how much remains.

→ Force-fit: a codebase's change flows through a neck too — the files everything else must pass through.

**Idea: "Hourglass"** — surface the narrow waist: files with *low* churn but *high* fan-in. Rename one and everything breaks.

Half-hit. It names a chokepoint, not churn. Kept as a weaker direction rather than dressed up as a strong one.

### 🌋 The fault line — Geography & Landscape [concrete]

Properties: two plates grind past each other; stress accumulates invisibly; release is sudden; the line is mapped precisely *because* it is dangerous.

→ Force-fit: files that change constantly aren't the danger. The danger is where two *teams* grind against the same file. Churn plus authorship equals accumulated stress.

**Idea: "Faultline"** — map files by churn × distinct authors. Not "what changes" but "what changes under contention." Seismologists don't map every tremor; they map where plates meet.

Strong. It reframes the metric and the name carries the warning.

### 🐜 The termite mound — Insects & Microbes [concrete]

Properties: built with no architect; each termite follows local rules; ventilation shafts nobody designed; constantly repaired.

→ Force-fit: nobody designed the churn hotspots either — they emerge from thousands of local commits.

→ Second attempt: termites repair continuously, so churn as *repair rate*? That is just churn again.

→ Third attempt: nothing new.

**Abandoned.** The emergent-structure insight is genuine, but it collapses into the fault-line idea and every name it yields is worse. Moving on.

### 🗄 The junk drawer — Household Objects [concrete]

Properties: everything without a home ends up here; nobody owns it; you dread opening it; it works, sort of.

→ Force-fit: every codebase has a `utils.ts`, and it churns precisely *because* it is where homeless code lands.

**Idea: "Junk Drawer"** — flag files whose churn comes from heterogeneity: many unrelated changes, low cohesion. A different signal from Faultline — not contention, but the absence of ownership.

Lands. Immediately legible to any engineer who has opened one.

### 🦩 The heron stalking — Animals & Creatures [abstract]

Properties: absolute stillness for minutes, then one strike. The stillness *is* the technique, not a pause between techniques.

→ Force-fit: then the stillest files are the interesting ones — untouched for three years, still imported everywhere.

**Idea: "Heron"** — invert the brief. Show what *hasn't* changed and ask whether that is maturity or abandonment. A stable file with heavy use is bedrock; a stable file nobody imports is dead code.

Lands, and it was the surprise of the batch: the stimulus inverted the target rather than serving it.

### 🏺 Kintsugi — Art & Craft [concrete]

Properties: broken pottery repaired with gold; the repair is highlighted, never hidden; the object is more valuable after breaking than before.

→ Force-fit: the highest-churn files are the most-repaired files. What if the dashboard rendered them gold-veined instead of red-alerted?

**Idea: "Kintsugi view"** — churn as accumulated care rather than accumulated risk. The most-changed file is the most-tended file.

Lands as a visual mechanic more than as a name. It reframes the tool's entire emotional register.

### 🌊 The river delta — Geography & Landscape [concrete]

Properties: one channel splits into many; sediment deposits where flow slows; the shape changes yearly; the map is obsolete the moment it prints.

→ Force-fit: commits flow from trunk into branches, depositing change downstream...

→ Second attempt: sediment as accumulated churn in leaf modules. But that is Faultline's measure, drawn prettier.

→ Third attempt: the delta's shifting map — a churn dashboard is stale the moment it renders? That is a caveat, not a product.

**Abandoned.** The sediment image is seductive and every fit reduces to a restatement of churn. Moving on.

### ⏳ Erosion — Time & Cycles [abstract]

Properties: slow, invisible day to day, total across years; the softest rock goes first; what resists is left standing as landmarks.

→ Force-fit: run churn across years rather than weeks. What is left standing is architecture; what eroded was scaffolding.

**Idea: "Bedrock"** — the dashboard as a geological cross-section. Whatever survived five years of churn is the real architecture, whatever the docs claim.

Lands, and it pairs naturally with Heron.

---

## The meta-pattern

Six landed, two were abandoned. Scanning only the ideas that landed — Faultline, Junk Drawer, Heron, Bedrock, Kintsugi, Hourglass:

**Four of the six invert the brief.** The request was "show which parts change most often," yet the strongest results surfaced what *doesn't* change (Heron, Bedrock, Hourglass) or recast change as a positive (Kintsugi). The two that stayed literal succeeded by adding a *second axis* to churn — authorship for Faultline, cohesion for Junk Drawer — rather than by measuring churn harder.

So: **raw churn is not the interesting signal.** Churn is only meaningful against a second axis. Against contention it means risk. Against ownership it means rot. Against time it means the difference between scaffolding and architecture. Against nothing, it means very little.

That also explains both abandonments. The termite mound and the river delta each restated churn as a prettier picture of itself and supplied no second axis. They didn't fail because the stimuli were weak; they failed because they were *redundant* — which is exactly the diagnostic the meta-pattern step exists to surface.

---

## Honest ranking

Strongest, in order:

1. **Faultline** — churn × authors. Reframes the metric, and the name does the warning for you.
2. **Heron** — inverts the brief entirely; the absence of change is the finding. Highest risk of confusing users who asked for churn.
3. **Junk Drawer** — churn × cohesion. Weakest name, sharpest diagnosis.

Two that surprised me:

4. **Bedrock** — churn over years, not weeks. Pairs with Heron; possibly the same product.
5. **Kintsugi view** — a visual language rather than a name. Would change how the tool *feels* more than what it *shows*.

Weakest: **Hourglass**, which answers a question nobody asked (chokepoints, not churn). Included because pretending it was strong would be dishonest.

Next moves, your call: pull a fresh batch from different pools, go deep on Faultline or Heron, switch to a convergent technique to narrow these, or stop here.
