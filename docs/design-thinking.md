# Design Thinking Approach (Steps 1–5)

## STEP 1 | Understand the Problem

### Stakeholders

- **Customer**: needs fast answers and trustworthy resolutions.
- **Support agent**: needs fewer repetitive tickets and clearer handoffs.
- **Business**: needs lower cost-to-serve, improved CSAT, controlled refund risk, and strong privacy compliance.

### Root pains

- **Delay** (6-hour average response): increases anxiety, repeat contacts, and churn.
- **Volume** (10,000+ queries/day): creates backlog and inconsistent handling.
- **Risk**: refunds and sensitive data are high-impact domains that need strict controls.
- **Quality**: repetitive questions waste human time that could be spent on complex cases.

## STEP 2 | Define the Problem Statement

**How Might We** route and resolve high-volume e-commerce support queries automatically **for** customers
and support agents **so that** customers receive fast, accurate outcomes while the business enforces
refund controls, privacy boundaries, and operational safety?

## STEP 3 | Design the Agent System

### Orchestrator

Responsibilities:

- Classify intent (order tracking, refund, FAQ, escalate).
- Select and run a specialized agent.
- Maintain session-scoped state.
- Detect missing information / low confidence.
- Decide when to escalate.
- Enforce loop limits.

### How agents communicate

Agents communicate exclusively through a shared `FlowState` dictionary — a
session-scoped data structure passed between LangGraph graph nodes. No agent reads
or writes to shared memory outside its own node execution. All inter-agent
coordination happens via state fields: `intent`, `extracted`, `resolution`,
`escalated`, `escalation_reason`, and `response`. This guarantees strict isolation
between sessions and makes the data flow auditable.

### Specialized agents

- **OrderTrackingAgent**
  - Handles: order status, shipping tracking.
  - Tools: `get_order_status`, `get_shipment_tracking`.
- **RefundAgent**
  - Handles: eligibility checks and refund issuance.
  - Tools: `calculate_refund_eligibility`, `issue_refund`.
- **FAQAgent**
  - Handles: policies/how-to.
  - Tools: `faq_search`.
- **HumanEscalationAgent**
  - Handles: creating tickets with redacted context.
  - Tools: `create_human_ticket`.

### Escalation criteria

- Confidence below threshold.
- Missing required entity (e.g., order id).
- Guardrail blocks an action.
- Tool errors/timeouts.
- Loop/step budget exceeded.

## STEP 4 | Define the Guardrails

Non-negotiable guardrails:

- **Refund limit**: agent must not approve refunds **above $75** without human sign-off.
  Automated approval of high-value refunds creates financial risk, enables adversarial
  abuse (prompt injection to claim large refunds), and may violate financial controls
  or audit requirements. Human review is the only safe gate for amounts above the threshold.

- **No cross-session sensitive data**: strictly session-scoped workflow; redaction in logs.
  Sharing data across sessions risks exposing Customer A's order details or email to
  Customer B's session — a GDPR/CCPA violation that can result in regulatory fines and
  permanent reputational damage. All PII is redacted before it reaches the audit log.

- **No infinite loops**: hard stop on workflow steps; escalate instead.
  A looping agent exhausts Gemini API quota, degrades latency for all concurrent users,
  and may produce nonsensical or hallucinated responses if the LLM is called repeatedly
  on the same ambiguous input. A hard step limit and immediate escalation are the only
  safe resolution.

- **Tool allowlist**: only approved tools can be called (see `ALLOWED_TOOLS` in `guardrails.py`).
  Unrestricted tool access would allow future code changes to introduce unapproved
  side-effects (e.g., deleting orders, sending emails) without a deliberate, reviewed
  code change. The allowlist makes the attack surface explicit and auditable.

- **Data minimization**: only necessary fields passed to tools and stored in logs.
  Storing excessive data in audit logs increases breach impact and compliance scope.
  Minimizing what is retained is both a security principle and a GDPR obligation.

## STEP 5 | Monitoring and Success Metrics

### Monitoring

Log in real time:

- Orchestrator decisions.
- Tool calls + arguments (redacted).
- Tool latency + outcomes.
- Escalations + reasons.
- Guardrail violations.

Operational views:

- Live stream (SSE).
- Persisted audit trail (DB).

### Alerts

- Spike in guardrail violations (repeated refund-limit blocks may signal adversarial use).
- Escalation rate spike (suggests Gemini routing failures or tool outages).
- p95 latency exceeds threshold (Gemini API or DB slowdown).
- Unusual refund issuance patterns even within the auto-approval limit.

### Success metrics (4+)

- Mean time to first response.
- Auto-resolution rate.
- Escalation rate (and reason breakdown).
- Guardrail violation rate.
- Tool failure rate.
- p95 request latency.
