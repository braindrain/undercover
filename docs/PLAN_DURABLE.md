# Plan: approvals that wait, and work that survives a restart

This plan builds `docs/DURABLE.md` on top of the harness build. Today a claim agent's work lives only in its process: stop the process and the claim's work is lost, and once a payout over the limit is approved the agent never comes back to it. After this build, the claim agent is a LangGraph graph that saves its progress in Supabase after every step. It stops before a payout over the limit, waits for a handler's sign-off, and pays as soon as the sign-off comes. If it is stopped partway, the next run carries on where it got to.

SIDENOTE: For consistency with the course, please read `labs/week2day3.ipynb` and build the solution in the same style as the lab. Where the lab and this brief differ, follow the brief. Include this sidenote in your plan too.

There are six phases, each building on the last: the payout rules and tracing first, as the brief asks; then the database on Supabase; then the backend's rules for work that pauses and repeats; then the claim agent as a graph; then the agent's service with the dashboard; and last a live run and the handover.

## The approach

### How a claim moves through the system

```
waiting ──start──▶ in_progress ──decline or refer──────────────────────────────▶ decided
   ▲                 │   │ ▲
   │                 │   │ └──────────── sign off ─────────┐
   │                 │   ├──approve, over the limit──▶ awaiting_sign_off
   │                 │   └──approve, within the limit──┐
   │                 │                                  ▼
   └──release (1st)──┤                    the agent pays ──▶ decided (paid)
                     └──release (2nd)──▶ left_for_handler
```

Two changes from today:

- **An approval stays in progress until it is paid.** Today an approval within the limit is `decided` the moment it's recorded, and paid by a separate tool call. If the agent stopped between the two, the claim would look finished but be unpaid, and nothing would find it again. Now `in_progress` always means "the agent still has work to do on this claim", so finding unfinished work is one query: the claims in progress.
- **A sign-off moves the claim back to `in_progress`**, as the brief asks, and the agent pays it. The sign-off no longer pays.

### The claim agent's graph

```
START ──▶ agent ⇄ tools
            │ (no more tool calls)
            ▼
         outcome ──awaiting sign-off──▶ sign_off ──▶ pay ──▶ END
            │      ──approved, unpaid─────────────▶ pay
            └──────otherwise──────────────────────────────▶ END
```

This follows the lab's graph: an agent node and a tools node that loop until the model stops asking for tools, then a node to wait for a handler and a node to pay. Where the lab and the brief differ:

| | The lab | This build, and why |
|---|---|---|
| Recording the decision | A `decide` node asks the model for a structured decision | The model records its decision with the `record_decision` tool, as today, so the backend checks it and a refusal goes back to the model. The `outcome` node then reads the claim from the backend to choose the next step |
| When to wait | The graph compares the payout with the limit | The graph waits when the backend says the claim is `awaiting_sign_off`. The brief says the graph never applies the limit itself, and the limit is a business rule that lives in the backend once |
| Paying | The pay node writes to a table with `ON CONFLICT DO NOTHING` | The pay node calls `pay_claim` on the backend, which treats a repeat of the same payment as done. The same idea, in the place that owns the rule |
| The thread | The claim id | A thread per attempt, such as `CLM-1008-3f9a2c1d`, made by the backend when a claim starts. A claim that fails starts afresh on a new thread, and reseeding never meets an old thread |
| The checkpointer | `PostgresSaver` on one connection | `PostgresSaver` on a connection pool of at most 4, with the lab's three settings (`autocommit`, `prepare_threshold=0`, `dict_row`) and `search_path=undercover` in place of `lab`. Tests use LangGraph's in-memory saver, since they need no network |

The nodes:

- **agent:** calls the model with the claim instructions and the conversation so far, and records the reply and each tool call as a step. It counts its model calls in the graph's state and stops the claim with an error at 30, so the limit holds across a restart. A failed model call is retried three times with LangGraph's `RetryPolicy`.
- **tools:** runs the tool calls with LangGraph's `ToolNode`, which passes a refusal back to the model as "Refused: …" through `ToolNode`'s own error handling, then records each result as a step, with `read_section`'s results as `read` steps as today. A tool call that can't reach the backend is retried twice with a `RetryPolicy`.
- **outcome:** reads the claim from the backend and keeps its status and payout in the state.
- **sign_off:** calls `interrupt()` with the claim and its payout. When the thread is resumed with the handler's sign-off, it keeps the sign-off in the state. As the lab shows, LangGraph runs this node again from the top on resume, so it does nothing but wait.
- **pay:** calls `pay_claim` with the payout. It has a node of its own so that a restart can only repeat the payment, which the backend treats as done.

These nodes take the place of the claim agent's middleware: the step recorder, the refusals, the retries and the call limit. The queue agent stays on `create_agent` with its middleware.

### Key choices, and why

1. **The backend owns the thread.** `start_claim` gives the claim a new thread id and returns it with the claim. To carry a claim on, the agent asks the backend for the claims in progress and resumes each one's thread. Nothing else needs to be stored, and the dashboard and Langfuse can name the thread.
2. **Carrying on is one function, `carry_on(claim)`.** It reads the thread's saved state and picks how to go on: with the handler's sign-off if the thread is waiting at `sign_off` (`Command(resume=...)`), with nothing if it was stopped partway (`invoke(None, ...)`, which LangGraph resumes from the last checkpoint), or from the start of the same thread if it was stopped before its first checkpoint. Every run is invoked with `durability="sync"`, so each step is saved before the next starts.
3. **A failure releases the claim, a stop doesn't.** Today `work_claim` releases its claim in a `finally`, which also runs on Ctrl-C. Now it releases only when the claim fails with an error or the graph ends without a decision. Ctrl-C and a killed process leave the claim in progress with its thread saved, so the next run carries it on without counting a new attempt. A claim that fails with an error is released as today, counts a failure, and starts afresh on a new thread.
4. **Nothing happens twice, by the backend's rules.** A step can run again after a restart. `record_decision` with the same decision and payout as the one recorded returns the claim; a different decision is refused, as today. `pay_claim` with the same amount as the payment recorded returns the claim; a different amount is refused. `release_claim` leaves a claim that has a decision as it is, so a claim whose payment failed waits to be carried on rather than going back in the queue with a decision.
5. **A run carries on first.** `uv run -m agent` first carries on every claim in progress (stopped, or signed off while the agent was away) and prints a report for each, as the queue agent's reports look. These don't count against the run's cap, since the queue agent didn't hand them out. Then, if the latest queue run is still marked running, it was stopped, so the new run finishes it with a summary saying so. Then it starts its own run.
6. **The agent's service.** A small FastAPI app, `agent/service.py`, on `UNDERCOVER_AGENT` or `http://127.0.0.1:8002`, which `npm run dev` starts beside the backend and the dashboard. It has two operations: work a waiting claim, and carry on a claim in progress. Each replies `202` at once and does the work in the background. It follows the backend's API rules (an `operation_id`, a one-line docstring, no optional inputs).
7. **The backend calls the agent through one client.** `backend/agent_client.py` holds the service's address and paths, as `agent/api.py` does for the backend. After a sign-off is saved, the backend asks the service to carry the claim on, in a background task, without waiting for the payment. If the service can't be reached, the sign-off stands and the next `uv run -m agent` picks the claim up. "Run the agent" on the claim page now asks the service too, so the backend no longer imports the agent at all; if the service isn't running, the button says so.
8. **The sign-off is committed before the agent hears of it.** The request's database dependency uses FastAPI's `scope="function"`, which commits when the operation returns, before the response and its background task. A test checks the agent's stand-in sees the sign-off when it is called.
9. **The payment records the sign-off.** A payment has `signed_off_by` and `signed_off_at`, copied from the sign-off by the backend when it pays, so the payment shows who signed it off and when without the agent passing anything. `pay_claim` keeps one input, the amount.
10. **SQLAlchemy Core, one set of queries.** The tables are defined once with SQLAlchemy and queried with its expression language, which writes the right SQL for SQLite and Postgres. The one SQLite-only query today, the join on `json_extract`, goes: claims get `customer_id` and `policy_id` columns at seeding. The record bodies stay as JSON columns.
11. **Supabase only with `DATABASE_URL`.** The backend uses Supabase when `DATABASE_URL` is set and SQLite otherwise. `npm run dev` starts the backend and the agent's service with `uv run --env-file .env`, and `uv run -m agent` loads `.env` itself. `.env` is no longer loaded when the agent is imported, so importing the agent in a test can't switch the tests to Supabase; the tests also set `DATABASE_URL` and the Langfuse keys to empty as a guard.
12. **The `undercover` schema.** Every table, the checkpointer's included, goes in a schema of its own, set with `search_path` on each Postgres connection so the queries match SQLite. The backend owns the schema: it creates it on first use, and reseeding drops and recreates it, which also clears old checkpoints. After reseeding Supabase, restart `npm run dev`. `npm run seed` reseeds Supabase; `uv run -m backend.seed` still reseeds SQLite, as the end-to-end tests do.
13. **At most 4 connections per process.** The backend's SQLAlchemy pool is 4 with no overflow, and the agent's psycopg pool opens 1 and grows to at most 4. With the backend, the service and one `uv run -m agent`, that's at most 12. Supabase's own `pg_net` worker holds one more connection as `postgres`, so the live check also closes the lab's notebook kernel, which holds one through the pooler. The Supabase MCP server connects as its own read-only user, so it isn't counted.
14. **Tracing in Langfuse, one session per claim thread.** Each graph run is traced with Langfuse's `CallbackHandler`, with the thread id as the session (`langfuse_session_id` in the run's metadata), so a claim's work before and after a pause sits in one session. Tracing is on when the Langfuse keys are set, and off in the tests. The command flushes the traces before it exits. The queue agent isn't traced; the brief asks for the claims.
15. **The payout rules in the backend.** `labs/week2day1_payout.py` becomes `backend/payout.py`, with the approval limit read from `data/company.json`, which the backend's existing rules also use, so there is one limit. A new operation works out a payout under a policy, and the claim agent's `calculate_payout` tool calls it. The agent still judges which lines are covered and what counts as one item; the rules do the arithmetic.

### The API changes

Every operation keeps an `operation_id`, a one-line docstring and no optional inputs.

| Operation | Method and path | What it does |
|---|---|---|
| `work_out_payout` | `POST /api/policies/{policy_id}/payout` | New: the benefit, the lines and what others paid; returns the calculation, the payout and whether it needs a sign-off |
| `start_claim` | `POST /api/claims/{claim_id}/start` | Changed: gives the claim a new thread id |
| `get_claim` | `GET /api/claims/{claim_id}` | Changed: adds `thread_id`, and the payment adds `signed_off_by` and `signed_off_at` |
| `record_decision` | `POST /api/claims/{claim_id}/decision` | Changed: an approval within the limit stays in progress until paid; a repeat of the recorded decision returns the claim |
| `pay_claim` | `POST /api/claims/{claim_id}/payments` | Changed: makes the claim `decided` and copies any sign-off into the payment; a repeat of the recorded payment returns the claim |
| `sign_off_claim` | `POST /api/claims/{claim_id}/sign-off` | Changed: records the handler and the time, moves the claim to `in_progress` and asks the agent to carry it on. It no longer pays |
| `release_claim` | `POST /api/claims/{claim_id}/release` | Changed: also leaves a claim with a decision as it is |
| `run_agent` | `POST /api/claims/{claim_id}/run` | Changed: asks the agent's service to work the claim; `503` if the service can't be reached |

Every line of a payout has its description, amount, quantity, kind, nights and hours, all required, with 1, 0 and 0 where they don't apply; the tool's descriptions tell the model so.

The agent's service:

| Operation | Method and path | What it does |
|---|---|---|
| `health` | `GET /health` | The service is running |
| `work_claim` | `POST /claims/{claim_id}/work` | Works a waiting claim in the background; `202` |
| `carry_on_claim` | `POST /claims/{claim_id}/carry-on` | Carries on a claim in progress in the background; `202` |

### The claim agent's instructions

The goal stays the same, less the payment: reach the decision an experienced handler would, and record it with reasons the team can follow. What changes:

- A good payout is the one the payout tool works out from the lines the agent judges covered, and is 0 for a decline or a referral.
- Recording the decision is the agent's last act on a claim. The system pays an approval, after a handler's sign-off when the payout is over the approval limit.
- The rule about the refused payment goes, since the agent no longer pays.

They still name no claims, and come from the wording and the manual alone.

### What this plan leaves out

- **Rejecting a held payout.** The brief leaves it for later.
- **The service carrying on claims when it starts.** The brief has the next `uv run -m agent` do it. Doing it in the service as well would let two processes work the same claim at once.
- **Two queue runs at once.** A run carries on every claim in progress, so it assumes no other run is working at the time, as today.
- **Changes to `docs/CODE_STANDARDS.md`.** The brief says how the new pieces fit its three parts of the agent: the service is a fourth, and the graph's nodes take the place of the middleware.

---

## The code's structure

Each module has one job, and imports point one way. Nothing imports `service.py` or `__main__.py`, and the backend no longer imports the agent.

### `backend/`

| Module | Its one job | Imports |
|---|---|---|
| `db.py` | The database: the engine for SQLite or Supabase, the tables, seeding and reset | `sqlalchemy` |
| `models.py` | The shapes the API sends and receives, now with the payout's lines and calculation, the thread and the payment's sign-off | `pydantic` |
| `documents.py` | The wording and the manual by section (unchanged) | `db`, `models` |
| `payout.py` | New: the payout rules, and the approval limit from `data/company.json` | `db`, `models` |
| `agent_client.py` | New: every call the backend makes to the agent's service, with its address | `httpx` |
| `main.py` | The operations and their rules | `db`, `documents`, `models`, `payout`, `agent_client` |
| `seed.py` | `uv run -m backend.seed` | `db` |

### `agent/`

```
__main__.py ─┐
             ├──▶ agents.py ──▶ tools.py ──▶ api.py
service.py ──┘        │                       ▲
                      └──▶ harness.py ────────┘ (Refused only)
```

| Module | Its one job | Imports |
|---|---|---|
| `api.py` | Every call to the backend, with its address. Adds `calculate_payout` and `claims_in_progress`, and keeps `pay_claim` for the pay node | `httpx` |
| `harness.py` | Everything around the model that knows nothing about claims: the model; the retry policies for graph nodes; turning a reply or a tool result into steps; the one refusal message; the checkpointer (in memory, or `PostgresSaver` on a pool of at most 4 in the `undercover` schema); the tracing config for a session; and the queue agent's middleware | `api` (for `Refused`) |
| `tools.py` | Every tool. The claim agent's: `get_claim`, `get_policy`, `get_claim_history`, `list_sections`, `read_section`, `calculate_payout` (new) and `record_decision`; `pay_claim` goes. The queue agent's three, unchanged, and the one-line report | `api` |
| `agents.py` | Builds and runs the two agents: the claim graph (its state, nodes and edges), `work_claim`, `carry_on`, and `run_queue`, which carries on first | `api`, `harness`, `tools` |
| `service.py` | New: the agent's web service, its entry point in the cloud | `agents` |
| `__main__.py` | `uv run -m agent`: loads `.env`, runs, flushes the traces | `agents`, `api`, `harness` |

### `dashboard/` and `e2e/`

- `dashboard/src/api.ts`: the payment's sign-off fields. The sign-off call is unchanged.
- `dashboard/src/AgentPanels.tsx`: "Sign off" in place of "Sign off and pay"; a signed-off claim in progress says who signed it off and that the agent is paying it; a paid claim says who signed it off and when.
- `e2e/agent-stand-in.mjs`: new, a stand-in for the agent's service on port 8003. It records each call, and on a carry-on pays the claim through the backend's API as the agent would.
- `playwright.config.ts`: starts the stand-in, and the backend with `UNDERCOVER_AGENT` pointing at it and `DATABASE_URL` empty.

### Checked against `docs/CODE_STANDARDS.md`

- One client per service: the agent's calls to the backend stay in `agent/api.py`, the backend's calls to the agent go in `backend/agent_client.py`, and tests check both, as they do for `api.py` and `api.ts`. A test checks the backend never imports `agent`.
- Business rules once, in the backend: the approval limit (now read once, in `payout.py`), the payout rules, the repeat rules and the sign-off. The graph waits because the backend says so, and never compares a payout with the limit.
- One way to report a refusal: tools raise `Refused`, and both the queue agent's middleware and the graph's tool node turn it into a message with the same function in `harness.py`.
- The harness knows nothing about claims: it turns replies and results into steps, but the claim graph that records them is in `agents.py`.
- The framework's own way: LangGraph's `StateGraph`, `ToolNode`, `RetryPolicy`, `interrupt()`, `Command(resume=...)` and `PostgresSaver`; FastAPI's dependencies and background tasks; SQLAlchemy's pool.
- The tool's lines are described in `tools.py` for the model, and checked again by the backend's model. These are two shapes for two readers, not a rule in two places.

---

## Phase 1: The payout rules and tracing

Bring the payout rules into the backend with a tool that uses them, give each claim a thread, and trace the claim agent in Langfuse, while the agent is still on `create_agent`.

- `backend/payout.py`: the rules from `labs/week2day1_payout.py`, reading the limit from `data/company.json`. `backend/main.py` takes its limit from here.
- `backend/models.py` and `backend/main.py`: `work_out_payout`; `thread_id` on a claim, made by `start_claim`.
- `backend/db.py`: the `thread_id` column.
- `agent/api.py` and `agent/tools.py`: `calculate_payout`.
- `agent/harness.py`: the tracing config, on when the Langfuse keys are set; `.env` is no longer loaded on import. `agent/__main__.py` loads it and flushes the traces.
- `agent/agents.py`: the claim agent runs with the tracing config, its session the claim's thread.
- `backend/instructions/claim.md`: the payout tool.

Success criteria:

- [x] Tests pass for `work_out_payout`: the lab's example (a suitcase and three T-shirts on Economy, $100 paid by the airline) gives the lab's calculation and payout; a hotel and meals without receipts on a trip delay are limited per room and per traveler per day; cash is limited to $100 in all; a total over the benefit's maximum is cut to it; a payout of $1,000.00 doesn't need a sign-off and $1,000.01 does; an unknown policy returns `404`
- [x] A test checks the approval limit in `payout.py` comes from `data/company.json`, and no other backend module holds a limit
- [x] Tests pass for `start_claim`: the claim gets a thread id that starts with its claim id, and a claim released and started again gets a new one
- [x] A stub-model test runs CLM-1001 through `calculate_payout` and `record_decision`; the tool's step shows the calculation from the backend
- [x] Tests check the tracing config: no callbacks without the Langfuse keys; with them, a Langfuse `CallbackHandler` and the thread id as `langfuse_session_id`
- [x] Importing `agent` doesn't load `.env` (a test checks `DATABASE_URL` stays unset)
- [x] The test that every route has an `operationId`, a summary and no optional inputs passes with `work_out_payout`
- [x] Live check, one claim, a few cents at most: after `uv run -m backend.seed`, with `npm run dev:backend` running, `uv run -m agent CLM-1001` decides the claim; through Langfuse's API, the session named after its thread has a trace with a `calculate_payout` call; then reset with `uv run -m backend.seed`
- [x] `npm run check:python` passes with coverage at or above 80%

## Phase 2: The records on Supabase

Move the backend to SQLAlchemy Core, on Supabase when `DATABASE_URL` is set, with the behavior unchanged.

- `backend/db.py`: the tables in SQLAlchemy; the engine for SQLite at `DATABASE_PATH` or for `DATABASE_URL` with the psycopg driver, `search_path=undercover` and a pool of 4 with no overflow; seeding into new tables on first use; reset that drops and recreates the schema on Postgres and deletes the file on SQLite.
- `backend/main.py`: every query in SQLAlchemy's expression language; the database dependency on an engine connection, committed when the operation returns.
- `pyproject.toml`: `sqlalchemy` and `psycopg-pool` move into the main dependencies.
- `package.json`: `dev:backend` with `uv run --env-file .env`; `npm run seed` to reseed Supabase.
- `backend/tests/conftest.py` and `agent/tests/conftest.py`: `DATABASE_URL` and the Langfuse keys set to empty.

Success criteria:

- [x] Every existing backend and agent test passes on SQLite, unchanged in what it checks
- [x] Tests check the engine: SQLite when `DATABASE_URL` is empty; with a Supabase URL (not connected), the psycopg driver, `search_path=undercover` and a pool of 4 with no overflow
- [x] No query in `backend/` is written as an SQL string, apart from creating and dropping the schema (a test checks there's no `SELECT`, `INSERT`, `UPDATE` or `DELETE` in a string)
- [x] `npm run check:python` passes with `.env` moved out of the way
- [x] `npm run seed` reseeds Supabase; through the Supabase MCP server, the `undercover` schema has the tables, with 25 claims all `waiting`, and the `lab` schema is untouched
- [x] With `npm run dev:backend` running on Supabase, `curl -s localhost:8000/api/claims` returns 25 claims, and the dashboard's queue shows them in the browser
- [x] Through the Supabase MCP server, `pg_stat_activity` shows at most 4 connections from the backend while the dashboard refreshes
- [x] `npm run e2e` still passes on SQLite, and `npm run seed` resets Supabase afterwards

## Phase 3: The backend's rules for work that waits and repeats

Change the rules so that unfinished work is always in progress, a repeat is done, and a sign-off records without paying.

- `backend/main.py`: an approval within the limit stays `in_progress` until paid; `pay_claim` makes it `decided` and copies the sign-off into the payment; a repeat decision or payment returns the claim; `release_claim` leaves a claim with a decision; `sign_off_claim` records and moves the claim to `in_progress`.
- `backend/db.py` and `backend/models.py`: the payment's sign-off columns.
- `dashboard/src/AgentPanels.tsx`: "Sign off", and what a signed-off claim in progress says, so the dashboard matches the backend from here on.

Success criteria:

- [ ] Tests pass for the new moves: an approval of $420.00 is `in_progress` until paid, then `decided`; an approval of $1,150.00 is `awaiting_sign_off`, signed off it's `in_progress` with the sign-off and no payment, and paid it's `decided` with the payment's `signed_off_by` and `signed_off_at`
- [ ] Tests pass for repeats: the same decision twice returns the claim with one decision; a different decision is refused with `409`; the same payment twice returns the claim with one payment; a different amount is refused
- [ ] Tests pass for the old refusals that still hold: paying over the limit before the sign-off, paying a decline, signing off a claim that isn't awaiting sign-off, signing off as a handler who can't
- [ ] A test releases an approved, unpaid claim and it stays `in_progress` with no failure counted
- [ ] Vitest tests pass for the sign-off form's "Sign off" button, a signed-off claim in progress, and a paid claim naming the handler and the time of the sign-off
- [ ] `npm run e2e` passes, with the sign-off test now expecting the claim in progress and signed off, not paid (Phase 5 brings the payment back through the agent's stand-in), and the payment test paying before the claim shows `Decided`
- [ ] `npm run check:python` and `npm run check:ts` pass with coverage at or above 80%

## Phase 4: The claim agent as a graph

Rebuild the claim agent as a LangGraph graph with a checkpointer, a wait for the sign-off and a pay step, and have each run carry on first.

- `agent/harness.py`: the checkpointer; the retry policies; turning replies and results into steps; the refusal message. The step recorder middleware goes.
- `agent/agents.py`: the claim graph; `work_claim(claim_id)`, which starts the claim and runs its thread; `carry_on(claim)`; `run_queue(cap)`, which carries on first and finishes a stopped run.
- `agent/tools.py`: `pay_claim` goes.
- `backend/instructions/claim.md`: the changes above.
- Tests on a stub model with the in-memory checkpointer, which a fixture empties for each test. A "restart" is a new graph on the same checkpointer.

Success criteria:

- [ ] A stub-model test approves CLM-1001 at $420.00; the graph pays it straight after, and the claim ends `decided` with one payment, with the steps recorded as today
- [ ] The pause: a stub-model test approves CLM-1002 at $1,150.00; the run returns with the thread waiting at `sign_off` (`get_state(...).next`), the claim is `awaiting_sign_off` with no payment, and nothing tried to pay it
- [ ] The resume: that claim signed off through the API, `carry_on` resumes its thread and pays it once, with the handler and the time in the payment; carrying it on a second time changes nothing
- [ ] Stopped partway: a stub model that stops the process (a `KeyboardInterrupt`) after `get_claim` and `get_policy` leaves the claim `in_progress` with no failure; `carry_on` with a new graph and model goes on from the last step on the same thread (the new model's first prompt holds the earlier tool results), and the claim ends with one decision, its failures still 0
- [ ] A failure starts afresh: a model that always fails releases the claim with 1 failure and an error step; worked again, it runs on a new thread whose first prompt holds only the task
- [ ] A repeated step counts as done: running the pay node again on a paid claim leaves one payment
- [ ] The harness's other jobs still hold: a model call that fails once is retried; a refusal goes back to the model as "Refused: …"; a claim agent that never decides stops at 30 model calls, counting across a restart
- [ ] A run carries on first: with one claim stopped partway and one signed off, `run_queue` reports both first, then starts its run, and neither counts against its cap; a latest run still marked running is finished with a summary saying it was stopped
- [ ] Tests check the checkpointer: in memory when `DATABASE_URL` is empty; otherwise a `PostgresSaver` on a pool (not opened) of at most 4 with the lab's settings and `search_path=undercover`
- [ ] `pay_claim` is no longer a claim tool, and the instructions don't mention paying (tests check both); they still name no claims
- [ ] Live check, one claim, a few cents at most: after `npm run seed`, with `npm run dev:backend` running, `uv run -m agent CLM-1001` decides and pays it; through the Supabase MCP server, `undercover.checkpoints` has rows for its thread; then `npm run seed`
- [ ] `npm run check:python` passes with coverage at or above 80%

## Phase 5: The agent's service, the sign-off and the dashboard

Start the agent's service with `npm run dev`, have a sign-off reach it, and test the whole loop end to end with a stand-in.

- `agent/service.py`: the service and its three operations.
- `backend/agent_client.py`: `work` and `carry_on`, with the address from `UNDERCOVER_AGENT`.
- `backend/main.py`: `sign_off_claim` asks the agent to carry on in a background task; `run_agent` asks the agent to work the claim.
- `package.json`: `dev:agent`, started by `npm run dev` beside the backend and the dashboard.
- `e2e/agent-stand-in.mjs`, `playwright.config.ts` and `e2e/claim.spec.ts`: the stand-in and the tests.

Success criteria:

- [ ] Tests pass for the service: `work_claim` and `carry_on_claim` reply `202` and run the agent's function in the background (replaced in the test); every operation has an `operationId`, a summary and no optional inputs
- [ ] Tests pass for the sign-off: the backend asks the agent's client to carry the claim on, and the stand-in client sees the sign-off already saved when it's called; with the agent unreachable, the sign-off still succeeds and is saved
- [ ] Tests pass for `run_agent`: it asks the agent to work a waiting claim and returns `202`; with the agent unreachable it returns `503` and the claim stays waiting; on a claim in progress it returns `409`
- [ ] Tests check that only `backend/agent_client.py` names the agent's paths or address, and that nothing in `backend/` imports `agent`
- [ ] `npm run e2e` passes, with new tests that: signing off CLM-1002 on the dashboard reaches the stand-in on port 8003, and the claim then shows paid with the handler and the time; "Run the agent" reaches the stand-in
- [ ] `npm run e2e` passes a second time in a row, and with `npm run dev` running at the same time
- [ ] `npm run dev` starts the backend on 8000, the agent's service on 8002 and the dashboard on 5173; `curl -s localhost:8002/health` replies
- [ ] `npm run check` passes with `.env` moved out of the way

## Phase 6: A live run, a restart and the handover

Run the whole product on Supabase with the real model, stop it partway, and bring the docs up to date.

- `docs/SPEC.md`: the section on the pilot's run of the queue says the agent waits for a sign-off and pays after it, a signed-off claim is in progress until paid, and a run carries on with a claim that was stopped. The Rules stay as they are.
- `docs/TECH_STACK.md`: the backend on SQLAlchemy with SQLite or Supabase; the claim agent as a LangGraph graph with the Postgres checkpointer; the agent's service; Langfuse.
- `docs/PROCESS.md`: `npm run dev` starts three servers and needs `.env`; `npm run seed`; the checks need no `.env`.
- `docs/STATUS.md`: what's built, how to run it and reseed, and the live results.

Success criteria:

- [ ] Live run, capped at five claims: after `npm run seed` and a fresh `npm run dev`, `uv run -m agent --cap 5` on the default model works five claims, and a claim over the limit is left waiting for a sign-off
- [ ] In the browser with the Playwright MCP server, signing off that claim on the dashboard shows it paid within five seconds, with the handler and the time
- [ ] During the run, through the Supabase MCP server, `pg_stat_activity` shows no more than 12 connections whose user is `postgres`
- [ ] Through Langfuse's API, the held claim's session has traces from before and after the sign-off
- [ ] A restart: after `npm run seed`, `uv run -m agent --cap 5` is killed once a claim has its first few steps; the claim is still `in_progress` with its thread; the next `uv run -m agent --cap 5` carries it on with the same thread, its failures unchanged, and it ends with one decision; then `npm run seed`
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md` and `docs/STATUS.md` describe the product as it now is
- [ ] `npm run check` passes in full with `.env` moved out of the way: ruff, pyright, pytest at 80% or more, ESLint, Prettier, tsc, vitest at 80% or more, and Playwright
