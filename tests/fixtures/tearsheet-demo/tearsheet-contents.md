# Tearsheet contents — what is actually in each section

**Status: partly agreed.** Items marked AGREED were settled in session 1;
everything else is still my guess, chosen to be specific enough to be wrong.
Correct them rather than softening them — a vague spec here becomes an argument
at 07:20 on a bad morning.

Wireframe blocks deliberately carry only section *names*. This file carries the
contents. That split is why the blocks got shortened: a phone column is 320px
wide and a spec does not fit in it.

---

## A standing principle I want to argue for

**The tearsheet is a renderer, not a calculator — for anything that has an
owner elsewhere.**

If the risk system already publishes an official VaR, the tearsheet must
*fetch and display that number*, never recompute its own. Two independent
implementations of the same metric will diverge, and the morning they diverge
is the morning someone notices the tearsheet disagrees with the risk report —
at which point nobody trusts either. Recomputation buys nothing and costs the
product its credibility.

This is the same principle as "never make the agent spot what it can be told",
applied one layer down.

---

## Overnight & Pre-Market

| | |
|---|---|
| **Contents** | index levels, futures, FX, rates — the market backdrop |
| **"Overnight move" means** | prior official close -> last pre-market trade at the 07:00 Cutoff |
| **Source** | market data vendor |
| **Unavailable** | "Market data unavailable — vendor timeout at 07:00" |

**Open:** which indices and pairs, and whose close counts as "official" for
instruments trading in multiple venues.

---

## Your Book

| | |
|---|---|
| **Contents** | total P&L, then top 5 movers |
| **P&L means** | change in mark-to-market since the prior official close, at the official book mark |
| **"Top 5" ranked by** | **contribution to P&L in currency**, not percent move |
| **Source** | positions file + official marks |
| **Stale** | "Positions as of yesterday's close — today's file had not landed by 07:00" |

**Why contribution and not percent:** a 30% move in a 20bp position is noise
dressed up as news; a 2% move in your largest position is the actual story of
the morning. A percent ranking is what a screener shows someone who does not
own anything.

**The cost of that choice, which is real:** contribution ranking systematically
hides large percentage moves in small positions — often exactly the names being
built or exited, where something genuinely new is happening.

**AGREED — there are two lists.** Top 5 by contribution to P&L, then a second
list: any held name that moved more than **5%** regardless of position size, one
line each. The 5% threshold is explicitly a starting value to be tuned once
people see how noisy it is in practice.

---

## Risk & Exposure

| | |
|---|---|
| **Contents** | gross and net exposure, largest concentrations, VaR |
| **VaR means** | *whatever the nightly risk run says it means* — **fetched, never computed** |
| **Source** | the nightly risk run's published figure, available by 06:00, carrying its own as-of stamp |
| **Unavailable** | "Risk unavailable — vendor timeout at 07:00" |

**AGREED — fetch, never recompute.** An authoritative VaR already exists: the
nightly risk run produces it by 06:00, comfortably ahead of the 07:00 Cutoff. A
failed fetch is an ordinary "risk unavailable" morning, handled exactly like any
other missing input.

This closes the question of whether the tearsheet would become the firm's risk
calculator by accident. It will not. It renders someone else's number.

---

## Holdings News

| | |
|---|---|
| **Contents** | one line per held name that has news |
| **Ordering** | by position size, not by news volume |
| **On an earnings morning** | the reporting name still appears here, short — people scan this as a checklist of every held name |
| **Source** | news vendor, queried per Instrument (or per Issuer — see the domain diagram) |
| **Thin coverage** | the agent says so explicitly: "coverage thin on this one". Never guesses, never silently drops |

**Open:** do names *without* news appear at all? A checklist that lists only
names with news cannot be scanned for completeness — which was the entire
reason for keeping NVDA's line.

---

## Macro News

| | |
|---|---|
| **Contents** | Fed, CPI and other prints, major geopolitical developments |
| **Source** | news vendor, holdings-independent query |
| **Unavailable** | "Macro summary unavailable" |

**AGREED — a look-ahead exists.** See "Today", below.

---

## Top of Sheet

| | |
|---|---|
| **Contents** | three lines of agent-written synthesis |
| **Present on** | normal and degraded mornings |
| **Absent on** | earnings mornings — the earnings block takes its place |
| **Unavailable** | omitted; the sheet begins with Your Book |

**AGREED — the synthesis stays on top.** It is the product. The judgement rests
on two things behind it: the never-guess / always-flag-thin rule, and the
Fallback Renderer, which means a failure here costs prose rather than the sheet.
Pin closed.

---

## The earnings block

| | |
|---|---|
| **Contents** | headline figures vs consensus, pre-market reaction, our position in the name |
| **Trigger** | a held Issuer reported after yesterday's close, or reports before today's open |
| **Source** | earnings calendar (deterministic lookup) + market data + positions |

**Open:** on a morning when *three* names report, does the sheet get three lead
blocks, one block listing three, or a fallback to the normal layout with a
mention? Not hypothetical — that is most Thursdays in earnings season.

---

## Today  *(AGREED — new, not yet drawn)*

| | |
|---|---|
| **Contents** | scheduled economic prints ("CPI 08:30"), Fed speakers, and our own names reporting after today's close |
| **Direction** | the only forward-looking content on the sheet — everything else is a look-back |
| **Source** | economic calendar (**no pipeline step produces this yet**) + the existing earnings calendar, queried for the forward window |

**The unresolved thing, and the first thing to settle next session:** this was
described as possibly *the most-read line on the sheet*. It is currently
specified as living inside Macro News, which sits at the **bottom**, well below
the phone fold. Those two facts cannot both stand. If it is the most-read line
it belongs above the fold — plausibly folded into Top of Sheet, or as its own
strip directly beneath the header.

Note also that the earnings calendar step already on the canvas can serve this
with a different query window (names reporting *after today's close*, rather
than before today's open). The economic calendar is a genuinely new source with
no step to produce it.

---

## Fallback (no model available)

If the writing agent misses its slot, a deterministic template ships the
computed numbers with no prose: Your Book, Overnight & Pre-Market, Risk, and
raw headlines under each held name.

**Open:** should the fallback sheet announce itself at the top ("summary
unavailable — figures only"), or arrive silently as a plainer sheet? I would
say it must announce itself, on the same principle as every other unavailable
marker.
