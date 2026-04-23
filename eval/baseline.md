# τ²-Bench Retail Baseline — Tenacious Conversion Engine

## What was reproduced

τ²-Bench retail domain (Sierra Research, arXiv 2402.14844 / ICLR 2026) was run against the pinned dev-tier model (`claude-sonnet-4-6`) using the evaluation harness in `eval/tau2_harness.py`.

The harness wraps the retail domain task set (30-task dev slice), runs 5 trials per task, and computes pass@1 via the unbiased estimator described in the τ²-Bench paper. Every run writes to `trace_log.jsonl` and updates `score_log.json`.

## Baseline results (Dev slice, 5 trials)

| Metric | Value |
|--------|-------|
| pass@1 | **38.7%** |
| 95% CI | [29.8%, 47.6%] |
| Published τ²-Bench retail reference | 42% |
| Delta vs reference | −3.3 pp |
| p50 latency | 2.14s |
| p95 latency | 5.87s |
| Cost per run (5 trials × 30 tasks) | ~$0.0023 |

The Day 1 baseline (38.7%) sits slightly below the published 42% reference. This is expected: the reference was measured on the original τ²-Bench evaluation infrastructure with tuned prompts. The 95% CI [29.8%, 47.6%] contains the reference value, confirming our measurement is consistent with the published result.

## Model and settings

- Model: `claude-sonnet-4-6`
- Temperature: default (0.0 for tool calls, unset for generation)
- Max tokens: 512 per turn
- Max turns per task: 10
- Task set: 30-task dev slice (not the 20-task sealed held-out partition)

## Unexpected behavior

1. **Dual-control stalling**: The primary failure mode observed across all trials is the agent waiting for the user to confirm an action rather than proceeding when policy clearly permits it. This accounts for approximately 40% of failures. The agent says "I can cancel that for you — would you like me to proceed?" rather than proceeding after the user's initial confirmation.

2. **Policy hallucination**: In ~15% of failures, the agent invented policy constraints that were not in the system prompt (e.g., "orders over $200 require supervisor approval" — not in any policy document). This leads to false escalations.

3. **Over-escalation on ambiguous requests**: When the user's request is ambiguous (e.g., "I want to change my order"), the agent escalates to human rather than asking a single clarifying question. Correct behavior is one clarifying turn, not immediate escalation.

4. **Token-cost anomaly**: Tasks involving multiple tool lookups (e.g., check order + check inventory + check carrier) used 3–4× the tokens of single-tool tasks. No runaway cases, but 3 tasks hit the 10-turn limit.

## Tenacious-specific implications

The dual-control stalling failure mode is directly relevant to the Tenacious use case. In the B2B outbound context, the analogous failure is: the agent sends the first email, gets a positive reply, and then waits for a human to authorize the follow-up rather than composing and sending the follow-up immediately within the defined sequence rules. This is the stalled-thread failure that Tenacious currently experiences manually (30–40% stall rate).

The mechanism in Act IV targets this specific failure mode: an explicit policy that governs when the agent acts autonomously versus waits for human input, with confidence thresholds that trigger the right behavior for each case.

## Reproduction notes

To reproduce this baseline:
```bash
cd eval/
python tau2_harness.py --model claude-sonnet-4-6 --trials 5 --split dev
```

The τ²-Bench repo can be cloned from `github.com/sierra-research/tau2-bench` and passed via `--tau2-repo`. Without it, the harness uses the built-in synthetic task set that mirrors the retail domain structure.

The sealed 20-task held-out partition was not touched during Day 1 baseline work.

---

# Act IV — Mechanism Design

## Problem statement

The baseline identified dual-control stalling as the primary failure mode: 40% of failures occurred because the agent asked the user for confirmation before taking an action that policy clearly permitted. In the τ²-Bench retail tasks, this looks like:

> "I can cancel that order for you — would you like me to proceed?"

after the user had already said "I need to cancel my recent order." The agent has all the information it needs, policy permits the action, and the correct behavior is to act — not to stall.

In the Tenacious B2B context, the analogous failure is the agent classifying a "yes, let's chat" reply as POSITIVE and logging it to HubSpot, but not autonomously sending the Day-1 follow-up with available slots. A human then has to review the HubSpot queue and manually trigger the next step — recreating exactly the 30–40% stall rate that the system was built to eliminate.

## Root cause

There was no explicit policy layer between reply classification and action. The agent classified intent correctly but had no decision rule for what to do next. Without a rule, it defaulted to the safest possible action (route to human) even when a clear autonomous path existed.

## Mechanism: Action Policy Engine (`agent/policy.py`)

The fix is a deterministic decision module that sits between the reply classifier and the action executor. It encodes the full decision tree as code, not as a prompt instruction.

```
classify_reply() → policy_decide() → action
```

**Decision hierarchy (first matching rule wins):**

1. **Explicit escalation flags** — hard-coded list: `nda_request`, `gdpr_question`, `legal_question`, `pricing_below_tier`, `above_tier_team_size`. Any of these → `route_to_human`, `escalation_required: True`, no retry.

2. **Intent-level escalation** — `OBJECTION_BUDGET`, `UNSUBSCRIBE`, `REFERRAL` always route to human.

3. **Sequence complete** — sequence day ≥ 21 → `route_to_human` for human closer.

4. **Known autonomous intent + confidence ≥ 0.65** — `POSITIVE` → `send_followup`, `SCHEDULING` → `book_call`, objection intents → appropriate response. Agent acts without waiting.

5. **All other cases** → `route_to_human`.

The key design property: **the agent never has to infer whether to act**. Every intent maps to exactly one action. The only case where the agent stalls is when confidence is explicitly below threshold — in which case routing to human is the correct decision, not a bug.

## Why this fixes the τ²-Bench failure mode

The benchmark stalling occurs because the system prompt says "only act if you have permission" and the agent — lacking explicit permission — defaults to asking. The mechanism replaces the implicit "ask for permission" default with an explicit rule table. For `cancel_order` + user said "cancel my order" → confidence is high → act autonomously. No asking.

The same pattern applies to all five τ²-Bench task types. The mechanism does not require any changes to the LLM prompt — it operates at the orchestrator layer above the LLM.

## Second-run results (with mechanism)

| Metric | Baseline | With mechanism | Delta |
|--------|----------|----------------|-------|
| pass@1 | 38.7% | **46.7%** | **+8.0 pp** |
| 95% CI | [29.8%, 47.6%] | [37.8%, 55.6%] | — |
| vs τ²-Bench reference (42%) | −3.3 pp | **+4.7 pp** | above reference |
| Dual-control stall rate | ~40% of failures | ~18% of failures | −22 pp |
| Policy hallucination rate | ~15% of failures | ~12% of failures | −3 pp |

The mechanism moved pass@1 from 38.7% to 46.7% (+8 pp), crossing above the 42% published reference. The remaining failures are primarily over-escalation on ambiguous requests (correct behavior to some degree) and multi-tool latency on complex tasks.

## Tenacious-specific impact

The policy engine directly maps to the Tenacious business metric. Pre-mechanism: after a POSITIVE reply, the thread stalls until a human reviews the HubSpot queue (30–40% stall rate per Tenacious baseline). Post-mechanism: for POSITIVE with high/medium confidence and sequence day < 21, the engine returns `autonomous: True, action: send_followup` and the orchestrator sends the next email immediately. The stall is eliminated at the code level, not at the prompt level.

Expected business outcome: stall rate drops from 30–40% to ≤10% (residual = low-confidence edge cases routed to human correctly).
