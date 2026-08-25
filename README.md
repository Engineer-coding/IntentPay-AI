# IntentPay AI

**Agentic Payment Authorization Platform**

IntentPay AI is a sandbox platform that converts natural-language spending instructions into enforceable payment mandates. It evaluates simulated AI-agent transactions with deterministic policy checks, risk scoring, signed authorization tokens, and an auditable decision chain.

> All transactions are simulated. The project does not process real card data, connect to a bank, or execute live payments.

## Why It Exists

AI agents can increasingly search, compare, and act on behalf of users. Payment execution requires a separate trust layer that answers four questions before an agent is allowed to proceed:

1. What did the user authorize?
2. Does the transaction satisfy those rules?
3. Is the request risky or anomalous?
4. Can the final decision be explained and audited?

IntentPay AI demonstrates that layer end to end.

## Core Flow

```text
Natural-language instruction
        ↓
Structured mandate
        ↓
User review and approval
        ↓
Agent transaction request
        ↓
Policy engine + risk model
        ↓
Approve / Step-up / Review / Decline
        ↓
Signed token + audit trail + analytics
```

## Main Features

- Natural-language instruction parsing with an LLM option and deterministic fallback
- Editable mandate generation before user approval
- Per-transaction and total spending limits
- Allowed and blocked category controls
- Approved-vendor and new-merchant policies
- Deterministic policy comparison for every transaction
- Risk scoring with strict, balanced, and lenient profiles
- Approve, step-up, review, and decline decisions
- HMAC-signed, single-use authorization tokens
- Replay and token-tampering protection
- Prompt-injection detection and mandate sanitization
- Event-sourced audit chain with decision explanations
- Live analytics derived from audit events
- SQLite and PostgreSQL persistence adapters
- Guided full-demo flow for portfolio review

## Product Views

### Overview

Create an instruction, review the generated mandate, approve it, and run transaction scenarios from one guided workspace.

### Decision Workspace

Shows policy differences, risk output, decision reasons, and the authorization token produced for approved requests.

### Analytics

Summarizes decision volume, approval rate, risk distribution, transaction amounts, and recent activity from the audit log.

### Audit Trail

Presents the complete event chain for each transaction, including policy checks, risk evidence, decision metadata, and signature status.

## Architecture

```text
Browser UI
   │
   ▼
FastAPI application
   ├── Intent parser
   ├── Mandate service
   ├── Policy engine
   ├── Risk engine
   ├── Token service
   ├── Audit / analytics service
   └── Persistence facade
          ├── SQLite adapter
          └── PostgreSQL adapter
```

The final payment decision is not delegated to the LLM. LLM output is treated as structured input; deterministic policy and risk components produce the final authorization result.

## Technology Stack

| Layer | Technologies |
|---|---|
| Frontend | React, JSX, HTML, CSS |
| API | FastAPI, Uvicorn, Pydantic |
| Core logic | Python policy engine and risk model |
| Persistence | SQLite, PostgreSQL adapter |
| Security | HMAC signatures, nonce and replay protection |
| Testing | Python integration and behavior tests |

## Quick Start

### 1. Prepare the environment

```bash
cp .env.example .env
```

The default configuration uses SQLite and requires no external database service.

### 2. Start the project

```bash
chmod +x run.sh
./run.sh
```

Open:

```text
http://localhost:8787
```

FastAPI documentation:

```text
http://localhost:8787/docs
```

## Configuration

Default local configuration:

```env
PERSISTENCE_BACKEND=sqlite
RESET_DB=0
```

Optional PostgreSQL configuration:

```env
PERSISTENCE_BACKEND=postgres
DATABASE_URL=postgresql://intentpay:intentpay@localhost:5432/intentpay
RESET_DB=0
```

`.env` and runtime database files are intentionally excluded from version control.

## Demo Walkthrough

1. Enter a natural-language spending instruction.
2. Convert it into a structured mandate.
3. Review or edit the generated limits and category rules.
4. Approve the mandate.
5. Select an agent transaction scenario.
6. Inspect policy checks and the risk score.
7. Complete step-up verification when required.
8. Open Analytics and Audit Trail to inspect the resulting evidence.

Example instruction:

```text
Bu hafta en fazla 5.000 TL ofis sandalyesi satın al.
Yalnızca onaylı satıcılardan alışveriş yap. Elektronik alma.
```

## Included Demo Scenarios

| Scenario | Expected behavior |
|---|---|
| Safe purchase | Approve and issue a signed token |
| Slight limit overrun | Require step-up verification |
| Large limit violation | Decline |
| Blocked category | Decline |
| New or unapproved merchant | Review or decline |
| MCC mismatch | Step-up or decline |
| Token replay | Reject the reused token |
| Prompt injection | Block or sanitize the instruction |

## Security Model

- The LLM does not make the final authorization decision.
- Approved mandates are evaluated by deterministic rules.
- Authorization tokens are HMAC-signed and single-use.
- Nonce tracking prevents token replay.
- Audit events preserve the evidence behind each decision.
- The application is a sandbox and makes no claim of production compliance certification.

## Tests

Run the complete test suite:

```bash
bash run.sh test
```

Expected result:

```text
SONUÇ: 23/23 test geçti
```

The tests cover safe approvals, limit violations, category restrictions, MCC mismatch, step-up flows, injection detection, mandate editing, token verification, replay prevention, persistence, analytics responses, and the audit event chain.

## Project Structure

```text
IntentPay-AI/
├── backend/
│   ├── server_fastapi.py
│   ├── server.py
│   ├── services/
│   └── run_tests.py
├── frontend/
│   ├── index.html
│   └── app.jsx
├── data/
├── .env.example
├── requirements.txt
└── run.sh
```

## Current Scope

This repository is a portfolio-grade proof of concept focused on authorization logic, explainability, risk evaluation, and auditability. It intentionally does not include:

- Real banking or card-network integration
- Live payment processing
- Production identity infrastructure
- Compliance certification
- Mobile applications
- Distributed microservices or orchestration infrastructure

## License

See [LICENSE](LICENSE).
