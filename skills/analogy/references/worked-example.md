# Worked Example: Growing an Open-Source Repo's Contributor Base

A real session. The target was a design problem, not an analytical one:

> "Growing an open-source repo's contributor base."

Five domains were drawn across families (Biological, Operational, Social/Cultural). Four transferred; one was abandoned. The abandonment is shown, because it is the point.

---

## Step 1: Confirm the target

Valid. This is a system-redesign problem — how to reshape the flow of people into a project — not a request to perform analysis. No clarifying question needed; the jam is well known.

## Step 2: State the structure in one sentence

Strip out the open-source vocabulary and look at the shape:

> A huge pool of passive beneficiaries, a tiny core doing all the work, and a steep, high-friction step for anyone crossing from one to the other.

The load-bearing facts: the beneficiary pool is enormous and cheap to grow, the crossing is rare, and the cost of helping someone cross is paid entirely by the already-overloaded core. Note what is *not* in the sentence: nothing about code, PRs, or GitHub. If it were, the domains I pick would just be more software.

## Step 3: Draw domains

Matching the sentence — an emergent structure with a scarce, high-friction conversion step — across three families:

- **Coral reef** (biological) — keystone builders create structure that others specialize into.
- **Mycorrhizal network** (biological) — surplus redistributed to deficit through standing infrastructure.
- **Restaurant kitchen brigade** (operational) — a rush routed through specialized stations. *(Included deliberately as a plausible-sounding operational candidate, to test it.)*
- **Potluck dinner** (social) — abundance assembled from uncoordinated contributions with no coordinator.
- **Guild masterwork** (social) — the right to practice gated behind produced, master-judged proof.

---

## The transplants

### 🪸 Coral reef

**Map roles:** maintainers = the keystone builders who lay down reef structure (the codebase, the docs, the scaffolding). Contributors = the diverse species that move in *after* structure exists, each occupying a niche.

**What does a reef DO that we don't?** It never *recruits* species. It builds physical structure that creates niches, and occupation follows automatically. The reef grows by making more places to live, not by advertising.

**Transfer:** stop recruiting contributors; manufacture *niches* instead. Carve the project into self-contained rooms someone can own outright — a whole plugin, a language translation, a docs section, a subsystem — rather than one-patch slots they submit into. The unit of entry becomes "own this space," not "get this patch accepted." Ownership of a niche is stickier than acceptance of a diff.

**Lands.** Reframes recruitment as habitat construction.

### 🍄 Mycorrhizal network

**Map roles:** the network moves carbon from sun-surplus trees to shaded seedlings underground, with neither tree deciding to. Surplus = maintainer expertise and attention. Deficit = the stuck newcomer.

**What does it DO that we don't?** It is *always-on infrastructure*. Surplus flows to deficit structurally, not as a per-case act of generosity.

**Transfer:** build a standing conduit that routes maintainer surplus to whoever is stuck automatically — a first-contribution auto-assigned a guide, attention that flows to the blocked newcomer rather than the loudest thread.

**Half-hit.** The transferable edge is the *automatic, infrastructural* framing; the underlying "mentor newcomers" idea is already common. Kept as a weaker direction rather than dressed up as a strong one.

### 🍳 Restaurant kitchen brigade

**Map roles:** orders = incoming contributions, stations = specialists, expediter = the maintainer synchronizing output.

**Testing the fit:** the brigade's structure is about routing a *rush* through specialists to synchronize a fast throughput. But my bottleneck is the opposite — first contributions are *scarce*, and the problem is the friction of crossing over, not congestion. To make the kitchen fit I already had to force one role (calling scarce contributions a "rush"). Then, to explain contributor *growth*, I'd need a second forced role: the diners would have to become cooks over time — and a kitchen has no mechanism for a diner joining the line. The one thing I care about, the crossing, is exactly what this domain is silent on.

**Abandoned.** The kitchen is *operationally seductive* — stations, mise en place, expediters all sound like onboarding — but that is shared vocabulary of organized work, not shared structure. My problem is a conversion funnel with a scarce top; the kitchen is a throughput problem with an overloaded line. It needed a second forced role precisely where my real question lives. Dropped.

### 🥘 Potluck dinner

**Map roles:** guests = contributors, the meal = the release, the empty spot on the table = a visible unmet need. No coordinator = nobody directs who brings what.

**What does it DO that we don't?** It makes contributing *additive and low-stakes*. You bring one dish, any dish, and it composes into a feast; no single dish is load-bearing, so nobody fears bringing the wrong thing.

**Transfer:** lower the height of the first step until a contribution is additive rather than gated. A public board of "small missing dishes," issues explicitly framed as "bring anything," and — the real mechanism — a norm where a mediocre first contribution still *adds* to the table instead of being rejected at the door. The empty spot on the table is a softer, more inviting signal than a stern "good first issue" label.

**Lands,** and it sits in direct tension with the guild idea below — which is useful.

### ⚒️ Guild masterwork

**Map roles:** becoming a trusted committer = achieving mastership. The accumulated body of merged work = the masterwork. Incumbent maintainers = the masters who judge.

**What does it DO that we don't?** It makes the path *visible and staged* — apprentice, journeyman, master — where each rung is earned by produced work and confers real, socially legible status.

**Transfer:** build an explicit public ladder of contributor standing, each rung unlocked by demonstrated work and granting genuine privileges — triage rights, then review rights, then merge rights. Progression itself becomes the motivator, and the ladder tells a newcomer that the far side of the chasm is a place you *climb*, not a door you knock on once.

**Lands.** Turns the vague "become a contributor" into a designed staircase.

---

## The meta-pattern

Four transplanted, one abandoned. Scanning only what landed — coral-reef niches, potluck entry, guild ladder, mycorrhizal support:

They split cleanly into two moves. **Lower the height of the first step** (potluck's additive low-stakes entry, mycorrhizal auto-routed help) and **build a visible staircase above it** (the guild ladder, ownable coral-reef niches). Not one of the useful domains treated the problem as "get more patches." Every one treated contributor growth as an *ownership and status* problem — where can I own a space, and how do I climb?

That also explains the abandonment exactly. The kitchen was the only domain that framed the problem as *throughput* — more work flowing through a line — and that is precisely why it needed a second forced role at the crossing. It failed not because kitchens are a weak domain but because its structure answered a question I wasn't asking, which is the diagnostic the meta-pattern step exists to surface.

So: **the first-PR chasm is not crossed by recruiting harder.** It is crossed by lowering the step *and* making the far side somewhere you can own and climb — not merely somewhere you submit to.

---

## Honest ranking

Strongest, in order:

1. **Coral-reef niches** — recruit by building ownable habitat, not by advertising. Reframes the whole growth model from funnel to ecosystem.
2. **Guild ladder** — a designed, visible progression of status and privilege. Pairs with the reef: niches are *what* you own, the ladder is *how you climb*.
3. **Potluck entry** — lower first-contribution stakes to additive, and let mediocre-but-additive land. The counterweight that keeps the guild ladder from feeling gate-kept.

One that half-landed:

4. **Mycorrhizal support** — auto-routed, infrastructural mentorship. The framing is sharp; the underlying practice is familiar. Weaker, kept honest.

Abandoned: **restaurant kitchen brigade**, which was operationally seductive but shared vocabulary rather than structure and needed a second forced role at the exact point that matters. Included here because pretending it worked would defeat the technique.

Next moves, your call: draw a fresh batch from Systems and Historical/Trades families (untouched so far), go deep on the reef-plus-ladder pairing, switch to a convergent technique to narrow these into one plan, or stop here.
