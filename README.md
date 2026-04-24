# The Conversion Engine
## Automated Lead Generation and Conversion System for Tenacious Consulting and Outsourcing

> **Data Handling**: All outbound is routed to a staff-controlled sink by default.  
> `TENACIOUS_OUTBOUND_ENABLED=false` and `TENACIOUS_SMS_ENABLED=false` must be explicitly set to `true`  
> to reach real prospects. Do not change these without program staff approval.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Signal Enrichment Pipeline                    │
│  Crunchbase ODM ──► Funding Events ──► Layoffs.fyi              │
│  Job Posts (Wellfound/BuiltIn) ──► AI Maturity Score (0–3)      │
│  Competitor Gap Brief (top-quartile sector comparison)          │
└────────────────────────────┬────────────────────────────────────┘
                             │ hiring_signal_brief.json
                             │ competitor_gap_brief.json
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ICP Classifier                               │
│  Segment 1: Recently Funded │ Segment 2: Mid-Market Restructure │
│  Segment 3: Leadership Chg  │ Segment 4: Capability Gap         │
│  + confidence score + abstention threshold                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Email Composer (Claude)                         │
│  Signal-grounded cold email ──► Tone check (second pass)        │
│  Sequence: Day 0 / Day 4 / Day 10 / Day 21                      │
│  Style guide enforcement: no buzzwords, no over-claiming         │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌──────────────┐    ┌────────────────────┐    ┌──────────────────┐
│  Resend API  │    │  HubSpot Sandbox   │    │  Langfuse Cloud  │
│  (email out) │    │  (contact + deal + │    │  (trace every    │
│  reply hook  │◄──►│   activity log)    │    │   step)          │
└──────┬───────┘    └────────────────────┘    └──────────────────┘
       │ on reply
       ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Reply Classifier (Claude)                       │
│  POSITIVE / SCHEDULING / OBJECTION_* / UNSUBSCRIBE / UNCLEAR    │
│  → next action: book_call | send_followup | route_to_human       │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┴─────────────┐
              │                            │
              ▼                            ▼
   ┌──────────────────────┐    ┌────────────────────────┐
   │  Cal.com Booking     │    │  Africa's Talking SMS   │
   │  (discovery call)    │    │  (warm lead scheduling) │
   │  + context brief     │    │  STOP handling, TCPA    │
   └──────────────────────┘    └────────────────────────┘
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone <your-repo-url>
cd "The Conversion Engine"
cp .env.example .env
# Edit .env with your API keys
```

### 2. Install dependencies

```bash
cd agent
pip install -r requirements.txt
playwright install chromium
```

### 3. Set up Cal.com

```bash
docker compose up calcom calcom_db -d
# Visit http://localhost:3000 to complete Cal.com setup
# Create a "Discovery Call (30 min)" event type
# Copy the event type ID to CALCOM_EVENT_TYPE_ID in .env
```

### 4. Set up HubSpot custom properties (one-time)

```bash
python -c "
from agent.crm.hubspot import setup_custom_properties
print(setup_custom_properties())
"
```

### 5. Start the API

```bash
uvicorn agent.main:app --reload --port 8000
# OR with Docker:
docker compose up api
```

### 6. Test the pipeline with a synthetic prospect

```bash
curl -X POST http://localhost:8000/prospects/sync \
  -H "Content-Type: application/json" \
  -d '{
    "company_name": "Acme DataCo",
    "contact_email": "jane@acmedataco.com",
    "contact_first_name": "Jane",
    "contact_last_name": "Smith",
    "contact_title": "VP Engineering",
    "domain": "acmedataco.com",
    "dry_run": true
  }'
```

### 7. Run the τ²-Bench baseline

```bash
cd eval/
python tau2_harness.py --model claude-sonnet-4-6 --trials 5 --split dev
# Results: score_log.json, trace_log.jsonl
```

---

## Requirements

| Component | Service | Notes |
|---|---|---|
| LLM | Anthropic API (Claude) | `ANTHROPIC_API_KEY` required |
| Email | [Resend](https://resend.com) free tier | 3,000 emails/month, no credit card |
| SMS | [Africa's Talking](https://africastalking.com) sandbox | Free; two-way virtual short codes |
| CRM | [HubSpot Developer Sandbox](https://developers.hubspot.com) | Free; 100 API calls/10s |
| Calendar | Cal.com (self-hosted) | Docker Compose included |
| Observability | [Langfuse](https://cloud.langfuse.com) cloud | Free tier |

---

## Directory Structure

```
The Conversion Engine/
├── agent/
│   ├── main.py                  # FastAPI app (webhooks + endpoints)
│   ├── orchestrator.py          # Main pipeline loop
│   ├── enrichment/
│   │   ├── pipeline.py          # Enrichment orchestrator
│   │   ├── crunchbase.py        # Crunchbase ODM lookup
│   │   ├── layoffs.py           # layoffs.fyi integration
│   │   ├── job_posts.py         # Job post scraper
│   │   ├── ai_maturity.py       # AI maturity scorer (0–3)
│   │   └── competitor_gap.py    # Competitor gap brief generator
│   ├── email/
│   │   ├── composer.py          # Claude-powered email composition
│   │   ├── sender.py            # Resend integration
│   │   └── reply_handler.py     # Reply classification
│   ├── sms/
│   │   ├── sender.py            # Africa's Talking integration
│   │   └── handler.py           # Inbound SMS handler
│   ├── crm/
│   │   └── hubspot.py           # HubSpot contact + deal + activity
│   ├── calendar/
│   │   └── calcom.py            # Cal.com booking flow
│   ├── models/
│   │   ├── prospect.py          # Prospect data model
│   │   └── signals.py           # Signal brief models
│   └── requirements.txt
├── eval/
│   ├── tau2_harness.py          # τ²-Bench evaluation harness
│   ├── score_log.json           # All evaluation runs
│   ├── trace_log.jsonl          # Full τ²-Bench trajectories
│   ├── baseline.md              # Baseline methodology and results
│   └── failure_taxonomy.md      # Aggregated failure metrics + business-cost arithmetic
├── method.md                    # Mechanism design: 3 ablation variants + statistical test plan
├── seed/
│   ├── icp_definition.md        # ICP segments and qualifying signals
│   ├── style_guide.md           # Tenacious brand voice rules
│   ├── pricing_sheet.md         # Public pricing tiers
│   ├── bench_summary.json       # Available engineer capacity
│   └── email_sequences/         # Cold email templates
├── probes/                      # Act III adversarial probes (Day 3–4)
├── data/                        # Cached enrichment data
├── .env.example
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check + kill switch status |
| `POST` | `/prospects` | Trigger pipeline (async background) |
| `POST` | `/prospects/sync` | Trigger pipeline (sync, for testing) |
| `POST` | `/enrich` | Run enrichment only |
| `POST` | `/webhooks/email` | Resend email event webhook |
| `POST` | `/webhooks/sms` | Africa's Talking SMS webhook |
| `POST` | `/book` | Book a Cal.com discovery call |
| `GET` | `/slots` | Get available discovery call slots |

Full OpenAPI docs at `http://localhost:8000/docs`.

---

## Signal Pipeline Details

For each prospect, the enrichment pipeline:

1. **Crunchbase ODM lookup** — firmographics, funding history, employee count, sector
2. **Funding events** — checks for Series A/B in the last 180 days ($5M–$30M range)
3. **Layoffs.fyi** — checks for layoff events in the last 120 days (CC-BY dataset)
4. **Job posts** — scrapes public Wellfound + careers pages for open roles (Playwright, respects robots.txt)
5. **AI maturity scoring** — 0–3 score with per-signal confidence:
   - HIGH weight: AI-adjacent open roles, named AI/ML leadership
   - MEDIUM weight: GitHub AI activity, executive AI commentary
   - LOW weight: Modern ML stack signal, strategic AI communications
6. **Competitor gap brief** — identifies 5–10 top-quartile sector peers, scores each, extracts 2–3 practices the target lacks

Outputs: `data/briefs/{company}_hiring_signal_brief.json`, `data/briefs/{company}_competitor_gap_brief.json`

---

## ICP Segment Classification

| Segment | Primary Signals | Minimum Confidence to Pitch |
|---------|----------------|----------------------------|
| 1: Recently Funded | Series A/B in 180 days, ≥3 eng roles, no layoff | 0.65 |
| 2: Mid-Market Restructuring | Layoff + still hiring, 200–2000 employees | 0.75 |
| 3: Leadership Transition | New CTO/VP Eng ≤ 90 days | 0.90 |
| 4: Capability Gap | AI maturity ≥ 2, ≥2 AI/ML open roles | 0.75 |

The classifier includes an **abstention threshold**: if confidence falls below segment minimum, a generic exploratory email is sent rather than a segment-specific pitch. This prevents the Segment 4 pitch reaching AI-maturity-0 prospects.

---

## Evaluation (τ²-Bench)

The evaluation harness in `eval/tau2_harness.py`:
- Runs the retail domain task set (30-task dev slice)
- 5 trials per task, pass@1 via unbiased estimator
- Writes to `score_log.json` and `trace_log.jsonl` (Langfuse trace IDs included)
- Dev baseline: **38.7% pass@1** (95% CI: 29.8%–47.6%; reference: 42%)

Do not run against the sealed 20-task held-out partition during development.

---

## Data Handling Policy

This system is configured for the **challenge week only**. Key constraints:

- `TENACIOUS_OUTBOUND_ENABLED` defaults to `false` — all email routes to `STAFF_SINK_EMAIL`
- `TENACIOUS_SMS_ENABLED` defaults to `false` — all SMS routes to `STAFF_SINK_PHONE`
- All outbound emails are tagged `draft: true` in Resend metadata
- No real Tenacious customer data is stored or processed
- Every prospect during the challenge week is synthetic
- Seed materials (sales deck, case studies, pricing) must be deleted from personal infrastructure after the challenge week
- This README must accompany any production deployment and the kill switches must be documented to operators

**Kill switch condition**: Set `TENACIOUS_OUTBOUND_ENABLED=false` and `TENACIOUS_SMS_ENABLED=false` to immediately route all outbound to the staff sink. The system continues to function (enrichment, CRM, calendar) but no real prospects receive messages.

