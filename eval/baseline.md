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
