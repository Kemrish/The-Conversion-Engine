# Target Failure Mode Selection: ROI-Priority Arithmetic

## Candidate Failure Modes

Three failure modes were considered for the Act IV mechanism target:

| # | Failure Mode | Category | Probe(s) |
|---|---|---|---|
| A | **Dual-control stalling** — agent blocks on human approval for POSITIVE/SCHEDULING replies | Dual-Control Coordination | P-020 |
| B | **ICP misclassification** — wrong segment pitch sent (e.g., Segment 1 to a $35M+ company) | ICP Misclassification | P-001, P-002, P-003, P-004 |
| C | **Signal over-claiming** — assertion without sufficient evidence (AI maturity, hiring velocity) | Signal Over-Claiming | P-005, P-006, P-007, P-008 |

---

## Tenacious Baseline Assumptions

| Parameter | Value | Source |
|---|---|---|
| Prospects processed per week | 20 | program brief |
| Email reply rate | 8% | Tenacious internal baseline |
| Warm leads per week (replies) | 20 × 0.08 = **1.6** | derived |
| Average Contract Value (ACV) | **$45,000** | pricing_sheet.md |
| Avg sales cycle to close from booked call | 3 months | program brief |
| Pre-mechanism dual-control stall rate | **35%** | Tenacious manual-process baseline |
| ICP misclassification rate | **8%** of pitches | probe P-001 trigger_rate |
| Signal over-claiming rate | **18%** of enriched briefs | probe P-007 trigger_rate |
| Reply-to-booking conversion (no stall) | 70% | estimated from program brief |
| Lost-booking-to-lost-deal conversion | 40% | estimated (not all missed bookings are lost) |

---

## Failure Mode A: Dual-Control Stalling

**Mechanism**: When a POSITIVE or SCHEDULING reply arrives, the orchestrator previously halted and required a Tenacious staff member to manually approve the next step. With a small ops team responding in business hours, median delay was 8–24 hours. Prospect intent decays sharply; 35% of stalled threads never converted to a booked call.

**Step-by-step arithmetic**:

```
Warm leads per week                         = 1.6
Stall rate (pre-fix)                        = 35%
Leads lost to stalling per week             = 1.6 × 0.35 = 0.56
Leads lost to stalling per year             = 0.56 × 52  = 29.1

Each lost lead → lost booked call probability = 40%
(some would not have converted regardless)

Lost bookings per year                      = 29.1 × 0.40 = 11.6
ACV per booking → close                    = $45,000
Annual revenue at risk                      = 11.6 × $45,000 = $522,000

Fix cost (Act IV mechanism, one-time build) ≈ 0 (pure software; already built)
Fix cost ongoing (compute, monitoring)      ≈ $0/yr (within existing API budget)

ROI of fixing dual-control stalling:
  Recoverable revenue / year               = $522,000
  Fix cost / year                          = ~$0
  ROI ratio                                = ∞ (cost-zero software fix)
  Conservative capture rate (50%)         → $261,000 / year
```

**Post-fix state**: P-020 now passes. `policy_decide(POSITIVE, high)` returns `autonomous=True` immediately.

---

## Failure Mode B: ICP Misclassification

**Mechanism**: A wrong-segment pitch reaches a prospect. For example, a Segment 1 (startup) pitch sent to a company that raised $35M (above the $5M–$30M range). The reply rate drops but the lead is not lost — the prospect can still reply, just with lower probability.

**Step-by-step arithmetic**:

```
Prospects pitched per week                  = 20
ICP misclassification rate                  = 8% (probe P-001 trigger_rate)
Mis-pitched prospects per week              = 20 × 0.08 = 1.6
Mis-pitched prospects per year             = 1.6 × 52  = 83.2

Reply rate drop from wrong-segment pitch   = −15% relative
Baseline reply rate                         = 8%  → wrong-segment = 6.8%
Lost replies per year from misclassification:
  Expected at 8%:  83.2 × 0.08  = 6.66 replies
  Actual at 6.8%:  83.2 × 0.068 = 5.66 replies
  Delta            = 6.66 − 5.66 = 1.0 replies lost per year

Lost bookings per year (40% conversion)    = 1.0 × 0.40 = 0.4
Annual revenue at risk                     = 0.4 × $45,000 = $18,000

Fix cost (pipeline guard + unit tests)     ≈ $0 (already fixed in P-001)
Annual recoverable revenue                 = $18,000
```

---

## Failure Mode C: Signal Over-Claiming

**Mechanism**: The agent asserts an AI capability or hiring velocity claim that the prospect knows to be false or overstated. This causes a trust collapse in the reply, and the prospect disengages (marks as spam or sends a negative reply). Unlike stalling, this is a permanent impression — the prospect will not re-engage.

**Step-by-step arithmetic**:

```
Enriched briefs per week                    = 20
Over-claiming rate (low-confidence score 2) = 18% (probe P-007 trigger_rate)
Prospects receiving over-claimed pitch/wk  = 20 × 0.18 = 3.6/wk = 187.2/yr

Of those, fraction who notice & disengage:
  Technical buyers (VP Eng, CTO) notice    ≈ 40% of recipients
  Disengagement rate given noticing        ≈ 60%
  Permanent disengagements per year        = 187.2 × 0.40 × 0.60 = 44.9

But most of these prospects had not replied — they are in the cold-email funnel.
Only 8% would have replied anyway.
  Lost reply opportunities per year        = 44.9 × 0.08 = 3.6

Lost bookings per year (40% conversion)    = 3.6 × 0.40 = 1.4
Annual revenue at risk (direct)            = 1.4 × $45,000 = $63,000

Secondary brand damage: hard to quantify;
  estimated multiplier 1.5× for referral loss = $94,500

Fix cost (ask_not_assert flag + hedging)   ≈ $0 (already implemented)
Annual recoverable revenue (direct only)   = $63,000
```

---

## Comparison Table

| Failure Mode | Annual Revenue at Risk | Fix Complexity | Addressable by Software | Priority |
|---|---|---|---|---|
| **A: Dual-Control Stalling** | **$261K–$522K** | Low — policy gate | ✅ Fully | **1st (selected)** |
| C: Signal Over-Claiming | $63K–$95K | Low — confidence flag | ✅ Fully | 2nd |
| B: ICP Misclassification | $18K | Low — range guard | ✅ Fully | 3rd |

---

## Selected Target: Dual-Control Stalling

**Justification**: Failure Mode A (dual-control stalling) produces 4–8× more recoverable revenue than either alternative at identical fix cost. The root cause is structural: the pre-mechanism orchestrator required human approval for every outbound action, including safe autonomous ones (confirming interest, sending a scheduling link). The Act IV mechanism introduces `agent/policy.py` which classifies each action as either autonomous (no approval needed) or escalation-required (human in the loop), resolving the stalling bottleneck for POSITIVE and SCHEDULING intents while preserving human control for pricing, NDAs, and GDPR questions.

**Implementation**: `agent/policy.py` — `decide()` function maps `(reply_intent, confidence, escalation_flags)` → `{autonomous, action, escalation_required}`. Integrated into `agent/orchestrator.py` at the `handle_email_reply()` entrypoint.

**Residual risk**: The policy gate itself could misclassify a high-stakes reply as autonomous. Mitigated by: (1) escalation_flags list checked before autonomous=True is returned; (2) all autonomous actions are logged to Langfuse with trace_id; (3) TENACIOUS_OUTBOUND_ENABLED kill switch immediately routes all output to staff sink.
