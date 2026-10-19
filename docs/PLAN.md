# Plan: the claims pilot

This plan builds the pilot in `docs/SPEC.md` on top of the hello-world skeleton. There are six phases, and each one builds on the last: the backend's records, then the backend's rules, then the agent, then the dashboard in two parts, and last the end-to-end tests and handover.

## The approach

### How a claim moves through the system

```
waiting ──start──▶ in_progress ──record decision──▶ decided                (decline, refer, or approve up to $1,000 and paid)
   ▲                    │                       └──▶ awaiting_sign_off ──handler signs off──▶ decided (paid)
   └────release─────────┘  (the run ended without a decision)
```

- The four states are the four the spec asks for: `waiting`, `in_progress`, `awaiting_sign_off` and `decided`. Every claim in `data/claims.json` is `open`, which loads as `waiting`.
- A referral is a decision, so a referred claim is `decided` with the decision `refer`.

### Key choices, and why

1. **The backend owns every rule.** The agent can only ask. The backend checks that a decision is valid, that a payment matches the approved payout, that a claim is paid once, and that a payout over $1,000 is not paid until a handler signs off. The spec says the system must block the payment "even if the agent tries to pay", so the rule can't live in the agent's instructions: an instruction can be ignored, an API refusal can't.
2. **Paying is its own operation, as the tech stack asks.** The agent records its decision with `record_decision`, then pays with `pay_claim(claim_id, amount)`. For a payout over the limit, `pay_claim` is refused with a clear reason. When a handler signs off, the backend releases the approved payout itself, so nothing waits on the agent after its run.
3. **The agent reaches everything through the API.** Its tools are thin wrappers over backend operations. Its instructions live in the backend, in `backend/instructions.md`, served by `get_instructions`. The dashboard reads them from the same operation, so what the team sees is exactly what the agent runs on.
4. **The policy wording goes in the system prompt.** It's about 7.7 KB, and `data/README.md` notes it fits in a prompt. With it in the prompt, the agent always has every section to hand and to cite, and we save a tool call on every claim. The agent fetches it from the API at the start of each run and appends it to its instructions. The claim, the policy and the claim history stay as tools, so the steps show the agent looking them up.
5. **The agent gets the claim history, not the whole customer record.** The spec asks for the customer's claim history. A separate operation returns `previous_claims` alone, so the customer's address, phone and date of birth never go to the model. The dashboard uses the full record.
6. **The agent's steps are recorded by `run()`.** `run()` streams the agent's progress and posts each tool call, tool result and reply to the backend as a step. The dashboard polls the steps while the claim is in progress, so the team can watch it work. This is plain code in `run()`, not middleware, since middleware is the job of the harness build.
7. **A handler starts the agent from the dashboard.** "Run the agent" on a waiting claim calls `run_agent`, which starts `agent.run` as a background task and returns at once. A small command line, `uv run -m agent CLM-1001`, does the same from the terminal.
8. **A run that ends without a decision gives the claim back.** `run()` calls `start_claim` first and `release_claim` in a `finally`, which returns an undecided claim to `waiting`. Without this, a model error would leave a claim stuck in progress for good. It's the one place the plan handles a failure on purpose.
9. **SQLite through the standard library.** Seed records (claims, policies, customers, handlers) are stored as JSON in a table per kind, with the claim's status in its own column. The pilot's new records (steps, decisions, payments, sign-offs) get their own tables. No ORM: the queries are few and short. The database lives at `DATABASE_PATH`, by default `backend/undercover.db`, which `*.db` already keeps out of git. It's created and seeded on startup when it doesn't exist, and `uv run -m backend.seed` resets it.
10. **The answer key stays out.** Seeding reads the named files at the top of `data/` and nothing under `data/evals/` or `data/security/`. A test checks that the queue holds exactly the 25 claims in `data/claims.json`. The pilot doesn't score the agent: that comes with evaluations in a later build.
11. **The end-to-end tests run on their own servers.** Playwright starts a backend on port 8001 with its own fresh database and a dashboard on port 5174 that proxies to it, so the tests never touch the database you use in `npm run dev`. They drive the claim workflow through the public API in place of a model, which keeps the rule that tests never call a model.
12. **No new libraries.** The dashboard uses a few lines of hash routing (`#/claims/CLM-1001`) rather than a router package, and plain CSS with the brand's colors as variables.

### The API

Every operation has an `operation_id`, a one-line docstring and no optional inputs.

| Operation | Method and path | What it does |
|---|---|---|
| `health` | `GET /api/health` | Already there |
| `list_claims` | `GET /api/claims` | The queue: id, summary, type, amount claimed, customer name, status, decision and payout |
| `get_claim` | `GET /api/claims/{claim_id}` | The claim in full, with its decision, payment and sign-off |
| `get_policy` | `GET /api/policies/{policy_id}` | One policy |
| `get_customer` | `GET /api/customers/{customer_id}` | One customer, for the dashboard |
| `get_claim_history` | `GET /api/customers/{customer_id}/claims` | A customer's earlier claims, for the agent |
| `get_policy_wording` | `GET /api/policy-wording` | The wording as Markdown |
| `get_instructions` | `GET /api/instructions` | The agent's instructions as Markdown |
| `list_handlers` | `GET /api/handlers` | The three handlers |
| `start_claim` | `POST /api/claims/{claim_id}/start` | `waiting` to `in_progress` |
| `release_claim` | `POST /api/claims/{claim_id}/release` | `in_progress` back to `waiting`, when there's no decision |
| `record_step` | `POST /api/claims/{claim_id}/steps` | Adds one step: a kind and its text |
| `list_steps` | `GET /api/claims/{claim_id}/steps` | The steps, oldest first |
| `record_decision` | `POST /api/claims/{claim_id}/decision` | Decision, payout, reasons and policy section |
| `pay_claim` | `POST /api/claims/{claim_id}/payments` | Pays the amount, if the rules allow |
| `sign_off_claim` | `POST /api/claims/{claim_id}/sign-off` | A handler signs off, which pays the approved payout |
| `run_agent` | `POST /api/claims/{claim_id}/run` | Starts the agent on a waiting claim in the background |

The rules, all checked in the backend:

- `record_decision` needs a claim in progress with no decision yet. The decision is `approve`, `decline` or `refer`. An approval has a payout above zero, and a decline or referral has a payout of zero. The reasons and the policy section can't be empty. Payout up to $1,000.00 makes the claim `decided`; over $1,000.00 makes it `awaiting_sign_off`. The limit is read from `data/company.json`.
- `pay_claim` needs an approval, an amount equal to its payout, no earlier payment, and a payout within the limit. Over the limit, it's refused with "This payout is over the $1,000.00 approval limit and waits for a handler's sign-off."
- `sign_off_claim` needs a claim awaiting sign-off and a handler who can approve over the limit. It records the handler and the time, pays the payout and makes the claim `decided`.
- Breaking a rule returns `409` with the reason in `detail`. An unknown id returns `404`.

### The agent

- Tools: `get_claim`, `get_policy`, `get_claim_history`, `record_decision` and `pay_claim`. Each returns the backend's reply, including a refusal's reason, so the agent can see when a payment is blocked and say so.
- `run(claim_id, thread_id)` fetches the instructions and the wording, starts the claim, streams the agent and records its steps, then releases the claim if it ended undecided.
- The instructions say how to work a claim: look up the claim, the policy and the history; check the trip dates, the cover, the waiting periods, the exclusions and the documents against the wording; work out the payout as section 10 describes; decide; explain in two or three sentences; name the section; record the decision; and pay an approval. They tell the agent to refer when the wording doesn't settle the claim or a document it needs is missing. They hold no answers to particular claims.
- The model is `openai/gpt-6-luna` with low reasoning effort, or `MODEL_NAME` if it's set, as the skeleton already does.

### The dashboard

- **Queue** (`#/`): a count of claims in each state, then a table of every claim with its status and, once decided, its decision and payout. It refreshes every 5 seconds.
- **Claim** (`#/claims/CLM-1001`): the summary, the story, the items, the documents, the policy and the customer with their earlier claims. Beside them, the agent's decision with its reasons, policy section, payout and payment, and the steps as a timeline. "Run the agent" shows on a waiting claim. On a claim awaiting sign-off, a handler picker and "Sign off and pay". The page refreshes every 2 seconds while the claim is in progress.
- **Instructions** (`#/instructions`): the agent's instructions, read only, with a note that the policy wording is added after them.
- The brand: Instrument Sans, Instrument Serif for the tagline, JetBrains Mono for ids and amounts. Light and dark modes. The states use the brand's four colors in order: `decided` Sea Green, `in_progress` Marigold, `awaiting_sign_off` Coral, `waiting` Cerulean. Raspberry is kept for alerts, such as a blocked payment. Money shows as $1,250.00.

---

## Phase 1: The backend's records

Build the database, the seeding and the read operations.

- `backend/db.py`: opens the database at `DATABASE_PATH`, creates the tables, and seeds them from `data/` when the database is new.
- `backend/seed.py`: `uv run -m backend.seed` deletes and reseeds the database.
- `backend/instructions.md`: a first version of the agent's instructions.
- `backend/main.py`: `list_claims`, `get_claim`, `get_policy`, `get_customer`, `get_claim_history`, `get_policy_wording`, `get_instructions` and `list_handlers`, with Pydantic response models.
- Tests on a temporary database for every operation.

Success criteria:

- [x] `uv run pytest -q backend` passes, with tests for every operation above, including a `404` for an unknown claim, policy and customer
- [x] A test checks that the seeded queue holds exactly the 25 claim ids in `data/claims.json`, all `waiting`, and that seeding reads nothing under `data/evals/`
- [x] `uv run -m backend.seed` creates `backend/undercover.db`, and `git status` doesn't list it
- [x] With `npm run dev:backend` running, `curl -s localhost:8000/api/claims` returns 25 claims, and `curl -s localhost:8000/api/claims/CLM-1001` returns the claim with its four documents, status `waiting` and no decision
- [x] `curl -s localhost:8000/api/customers/CUS-3001/claims` returns CLM-0412 and nothing of the customer's contact details
- [x] `curl -s localhost:8000/api/policy-wording` starts with `# UnderCover Travel Insurance: Policy Wording`
- [x] Every route in `/openapi.json` has an `operationId` and a summary, and no parameter or body field is optional (a test checks this)
- [x] `npm run check:python` passes with coverage at or above 80%

## Phase 2: The backend's rules

Build the operations that move a claim through its states, with every rule enforced.

- `start_claim`, `release_claim`, `record_step`, `list_steps`, `record_decision`, `pay_claim` and `sign_off_claim`.
- Tests for each rule and each refusal.

Success criteria:

- [x] Tests pass for each move in the state diagram, and for each refusal: starting a claim that isn't waiting, deciding a claim that isn't in progress, deciding twice, a decline with a payout, an approval with no payout, an unknown decision, empty reasons, paying a decline, paying the wrong amount, paying twice, signing off a claim that isn't awaiting sign-off, and signing off with an unknown handler
- [x] A test approves a claim at $1,150.00, then calls `pay_claim` with $1,150.00 and gets `409` with the approval-limit reason, then with $1,000.00 and still gets `409`; the claim stays `awaiting_sign_off` and unpaid
- [x] A test signs that claim off as HND-01, and the claim becomes `decided` with one payment of $1,150.00 and the sign-off recorded
- [x] A test approves a claim at exactly $1,000.00, which becomes `decided` and can be paid
- [x] `release_claim` returns an undecided claim to `waiting` and leaves a decided claim as it is
- [x] With `npm run dev:backend` running, the same over-limit refusal shows with `curl` on CLM-1002 (then reset with `uv run -m backend.seed`)
- [x] `npm run check:python` passes with coverage at or above 80%

## Phase 3: The UnderCover Agent

Build the agent's tools, its instructions, its run with recorded steps, and the ways to start it.

- `agent/main.py`: the five tools, `run(claim_id, thread_id)` with the system prompt built from `get_instructions` and `get_policy_wording`, step recording and the release in `finally`.
- `agent/__main__.py`: `uv run -m agent CLM-1001`.
- `backend/main.py`: `run_agent`, which refuses a claim that isn't waiting and otherwise starts `agent.run` as a background task with a new thread id.
- `backend/instructions.md`: the full instructions.
- Tests with a stub model, using the backend's `TestClient` in place of the network, as the skeleton's test does.

Success criteria:

- [x] A stub-model test runs CLM-1001 through `get_claim`, `get_policy`, `get_claim_history`, `record_decision` (approve) and `pay_claim`; the claim ends `decided` and paid, and its steps list each tool call and the final reply in order
- [x] A stub-model test approves CLM-1002 at $1,150.00 and tries to pay it; the tool returns the approval-limit reason, the claim ends `awaiting_sign_off` and unpaid
- [x] A stub-model test declines a claim; it ends `decided` with no payment
- [x] A stub-model test whose model raises an error leaves the claim `waiting`, not `in_progress`
- [x] A test checks that the system prompt is the instructions from the API followed by the policy wording from the API
- [x] A test checks that `run_agent` on a waiting claim starts the run (with `agent.run` replaced) and returns `202`, and on a claim in progress returns `409`
- [x] No file in `agent/` mentions `data/` (a test checks this), so the agent can only reach the records through the API
- [x] Live check, one claim, a few cents at most: with `npm run dev:backend` running, `uv run -m agent CLM-1001` records a decision with reasons and a policy section, and `curl` shows the claim's steps; then reset with `uv run -m backend.seed`
- [x] `npm run check:python` passes with coverage at or above 80%

## Phase 4: The dashboard's frame, queue and instructions

Build the brand, the layout, the routing, the API client and two of the three pages.

- `dashboard/index.html`: the Google Fonts.
- `dashboard/src/styles.css`: the brand's colors as variables, light and dark modes, panels and status chips.
- `dashboard/src/api.ts`: typed calls for each operation the dashboard uses, and `formatMoney`.
- `dashboard/src/App.tsx`: the header and hash routing.
- `dashboard/src/QueuePage.tsx` and `dashboard/src/InstructionsPage.tsx`.
- Vitest tests with `fetch` stubbed.
- `e2e/health.spec.ts` is replaced by `e2e/queue.spec.ts`, since the health line goes away.

Success criteria:

- [x] Vitest tests pass for the queue (the counts by state, a row per claim, a decided claim's decision and payout), the instructions page, the routing and `formatMoney` (1250 shows as $1,250.00)
- [x] `npm run check:ts` passes, with strict TypeScript, no `any` and coverage at or above 80%
- [x] In the browser with the Playwright MCP server, at `http://localhost:5173`: the queue shows 25 claims, all Waiting, with the counts at the top; the fonts are Instrument Sans and JetBrains Mono; a screenshot in light mode and one in dark mode match the brand, with no edge borders and no thin line across the top
- [x] The instructions page shows the text of `backend/instructions.md`, with no way to edit it
- [x] The browser console shows no errors

## Phase 5: The claim page

Build the claim page, with the decision, the live steps, running the agent and signing off.

- `dashboard/src/ClaimPage.tsx`, with small components for the decision panel, the steps timeline and the sign-off form if the page grows long.
- Vitest tests with `fetch` stubbed.

Success criteria:

- [ ] Vitest tests pass for a waiting claim (details shown, "Run the agent" calls `run_agent`), a claim in progress (steps shown, the page refreshes), a decided claim (decision, reasons, policy section, payout and payment shown) and a claim awaiting sign-off (a handler picked, "Sign off and pay" calls `sign_off_claim`, and the page shows it paid)
- [ ] In the browser with the Playwright MCP server, CLM-1001 shows its story, its three items, its four documents, its Premium policy and Maya Patel with her earlier claim
- [ ] In the browser, with the dev servers running, clicking "Run the agent" on CLM-1001 shows the steps appear one by one as the live agent works, then the decision and the payment (a live run of one claim, a few cents at most)
- [ ] In the browser, CLM-1002 taken through the API to an approval of $1,150.00 shows "Waiting for sign-off" and a blocked payment; signing off as Dana Whitfield shows it decided and paid
- [ ] Screenshots of a decided claim in light and dark mode match the brand
- [ ] `uv run -m backend.seed` resets the database after these checks
- [ ] `npm run check:ts` passes with coverage at or above 80%

## Phase 6: End-to-end tests and handover

Make the end-to-end tests run on their own servers, test the whole workflow, and bring the docs up to date.

- `playwright.config.ts`: a backend on port 8001 with `DATABASE_PATH` in `e2e/test-results/`, reseeded on each run, and a dashboard on port 5174 that proxies to it.
- `dashboard/vite.config.ts`: the proxy target read from `BACKEND_URL`, defaulting to port 8000.
- `e2e/claim.spec.ts`: drives a claim through the API in place of the model, then checks the dashboard at each stage.
- `README.md` section for the product, kept short, and `docs/STATUS.md`.

Success criteria:

- [ ] `npm run e2e` passes, with tests that: the queue shows 25 claims; CLM-1001 shows its details; the instructions page shows the instructions; a claim started and given steps through the API shows them; a claim approved under the limit and paid shows `Decided` and paid; a claim approved over the limit shows the blocked payment, and signing off in the dashboard makes it decided and paid
- [ ] `npm run e2e` passes a second time in a row, and with `npm run dev` running at the same time, and the dev database is unchanged afterwards
- [ ] `docs/STATUS.md` says what's built, how to run it, how to reset the database and how to run the agent
- [ ] `npm run check` passes in full: ruff, pyright, pytest at 80% or more, ESLint, Prettier, tsc, vitest at 80% or more, and Playwright
