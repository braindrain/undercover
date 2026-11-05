# Plan: a swarm of claim agents and a scoreboard

This plan builds `docs/SWARM.md` on top of the durable build. Today a queue agent hands out claims one at a time, so a queue of 25 takes over ten minutes, and nobody can see how well the agent does without reading traces. After this build, a dispatcher with no model works the queue with up to N claim agents at once, every model call and every minute of work is on record, and a Scoreboard page measures the agent against the handlers' decisions.

SIDENOTE: For consistency with the course, please read `labs/week2day4.ipynb` and build the dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief. Include this sidenote in your plan too.

There are six phases, each building on the last, in the order the brief asks: the default model with cost and time; the dispatcher in place of the queue agent; starting it from the dashboard; the evaluations; the scoreboard; and last the live runs and the handover.

## The approach

### Where the brief and the code disagree

Six things needed a decision before planning. The first is the one to review most closely.

| The brief says | What's actually there | What this plan does |
|---|---|---|
| The worker has a `RetryPolicy` and an `error_handler`, "so no error stops the dispatch" | On our locked LangGraph 1.2.12 this doesn't hold. I tested it: the handler runs and every other claim finishes, but `invoke` then raises the handled error at the very end, because `BackgroundExecutor.__exit__` ignores the list of handled errors. It happens whenever several claims run at once, and not with one claim, which takes a fast path. LangGraph 1.2.16 and 1.2.17 do the same, and 1.2.18, 1.2.19 and 1.2.20 are fixed | Keep the brief's design and raise LangGraph to `>=1.2.18` in Phase 2, as a direct dependency, locked at 1.2.20. `uv lock` also moves `langgraph-checkpoint` to 4.3.1 and `langgraph-sdk` to 0.4.13. The 61 agent tests pass on 1.2.20 today. A test works several claims at once, because a test with one claim would hide the bug |
| The worker calls "one function in the harness" | `agent/harness.py` knows nothing about claims (rule 13), and `agents.py` imports it, so a harness function that runs the claim agent would make two modules import each other (rule 8) | The function goes in `agents.py`, as `agents.work(claim_id, run)`. It's still the one place the dispatcher touches the claim agent, and a test checks that |
| Update `docs/CODE_STANDARDS.md` "rules 10 and 13" | The student's review renumbered the standards, so those numbers point at the wrong rules here. Rule 10 needs no change | Follow the intent: update every rule this build makes untrue, which is 9, 12, 13, 14 and 15 (Phase 6) |
| The dispatcher is "a fifth part of the agent, beside the four in `docs/CODE_STANDARDS.md`" | `docs/TECH_STACK.md` already counts five parts, with `service.py` | `dispatcher.py` is added, and `docs/TECH_STACK.md` says six |
| End-to-end tests play out a run "as `e2e/run.spec.ts` does" | There's no such file. That test is in `e2e/queue.spec.ts` | Follow the intent, in `e2e/queue.spec.ts` and a new `e2e/scoreboard.spec.ts` |
| Leave the Rules in `docs/SPEC.md` alone | One rule still ends "within its cap", and the cap goes in this build | Leave it, as the brief says, and flag it in the handover for the head of claims operations to decide |

### How a dispatch runs

```
dashboard ──start──▶ backend ──N──▶ agent's service
                                       │  409 if a dispatch or evaluation is running
                                       │  202 at once, then in a background thread:
                                       ▼
                     create the dispatch through the backend (which chooses its claims)
                                       ▼
START ──▶ list_claims ══ one Send per claim ══▶ work_claim ──▶ END
                                                 │ max_concurrency = N
                                                 │ RetryPolicy: rate limits and timeouts
                                                 │ error_handler: leave it, or Send it once more
                                                 ▼
                                agents.work(claim_id, run), the only link to the claim agent
                                       ▼
                     finish the run through the backend
```

This follows the lab's graph: the same `list_claims` node, the same `fan_out` conditional edge with one `Send` per claim, the same `work_claim` worker, `max_concurrency` in the config and a `TypedDict` for the state. Where the lab and the brief differ:

| | The lab | This build, and why |
|---|---|---|
| Threads | One graph on one thread collects every result | Each claim keeps its own thread. A claim can wait days for a sign-off, and an `interrupt()` on a shared thread would pause the whole dispatch |
| The dispatcher's state | A `results` list with `operator.add` | No results in the state, and no checkpointer. Everything is in the backend's records, and the next dispatch finishes what a stopped one left |
| Retries | `RetryPolicy(max_attempts=3)` on anything, working the whole claim again | `retry_on` rate limits and timeouts only, and a retry carries on from the last saved step |
| A failure after the retries | Stops the run | `error_handler`, so no error stops the dispatch |
| Tracing | One trace for the whole run | One session per claim, as today, with each trace tagged with its run |
| The model client | `max_retries=5` | Stays at 0, so the graph's retries are the one policy |

The claim graph runs inside the worker in a fresh `Context()`, as it does today. That keeps it a separate graph, not a subgraph of the dispatcher. I tested this: a claim's `interrupt()` stays on its own thread, and the dispatcher carries on.

### The one function

`agents.work(claim_id, run)` reads the claim and acts on where it stands:

- `waiting`: `work_claim`, on a new thread.
- `in_progress`: `carry_on`, from its last saved step.
- Anything else, such as a claim the service already paid: nothing to do.

It sits on top of `work_claim` and `carry_on`, which keep their jobs. "Run the agent", a sign-off and `uv run -m agent CLM-1001` still call those directly, with no run.

### What happens when a claim goes wrong

| What goes wrong | The claim agent | The worker | Where the claim ends |
|---|---|---|---|
| A rate limit or timeout that clears | The `agent` node's own retries go first. If it's still failing, `run_thread` records an error step and lets the error through, without a release | `RetryPolicy` waits, then calls `agents.work` again, which carries on | The same thread and attempt, with one decision and its failures unchanged |
| A rate limit or timeout that doesn't clear | As above | `error_handler` does nothing | `in_progress`, for the next dispatch |
| Any other error | An error step and a release, as today: its first failure returns it to `waiting`, its second leaves it `left_for_handler` | `error_handler` sends it to `work_claim` once more, marked `retried`, so it happens only once | A second try on a new thread, or left for a handler |
| The agent finishes with no decision | The same release, which now raises an error, so that it takes the row above | As above | As above |

Three details matter here:

- The handler sends the retry without asking the backend, because an error inside a handler does stop the run. If the claim is already `left_for_handler`, `agents.work` finds nothing to do.
- The retried claim runs after the first wave, as LangGraph runs each round of `Send`s in turn, and still counts towards N. The wait in a `RetryPolicy` also holds its slot. I tested this: 8 claims with failures at N=3 never went over 3.
- The worker's waits are 5, 10 and 20 seconds, with jitter, so that 25 agents hitting a limit together don't all retry at the same moment.

### Key choices, and why

1. **The run's id is in the runtime context, never the graph's state.** A claim stopped in one dispatch and carried on in the next must charge the next one, so each run passes `run_id` in `ClaimContext`, the way the model is passed. The graph's state is saved, so a run id there would be the old run's.
2. **Cost is recorded per model call, and time per stretch of work.** The `agent` node records each reply's tokens and `usage.cost` as it arrives, so failed attempts count. `run_thread` records one row of work for each time the graph runs: its start, finish and seconds, measured with a monotonic clock. A wait for a sign-off falls between two such rows, so it's left out with no special code. Each model call also records its own seconds. The brief asks where the time goes if claims slow down at N=25, and the live runs can't be repeated to find out.
3. **A run's id is a new string, such as `dispatch-3f9a2c1d`.** It's the tag in Langfuse. Plain numbers would start again after `npm run seed`, and Langfuse isn't reseeded, so a tag would find old traces too. `now()` keeps its microseconds, so runs still sort newest first.
4. **The backend chooses the claims when it creates the run.** `create_dispatch` stores the claims in progress and waiting, oldest submitted first, in `run_claims`. `create_evaluation` stores its copies the same way. So `list_claims` just asks for the run's claims, and code picks them. `submitted_on` becomes a column, as `customer_id` did, because the order is a rule and rules live in the backend.
5. **One set of claims this process is working.** `work_claim` and `carry_on` both check a set in `agents.py`, behind a lock, and do nothing if the claim is already in it. That's how a dispatch skips a claim the service is resuming after a sign-off. It also stops the reverse, and a press of "Run the agent" during a dispatch. It works because everything runs in the service's one process.
6. **A saver per claim run, on one shared pool.** `PostgresSaver` holds a lock around every read and write (`with self.lock, ...`), so `harness.checkpointer()` now returns a new saver on the process's pool each time. The in-memory saver stays shared, since the tests rely on it.
7. **The first use of a pool is behind a lock.** `functools.cache` doesn't stop threads racing: 25 threads built the cached value 25 times in my test. In the service that would be 25 pools of up to 4 connections, on a plan that allows 15. `harness` and the backend's `engine()` both take a lock around first use.
8. **Waiting for a connection, not failing.** Each default is 30 seconds, which 25 agents on 4 connections could reach: psycopg's pool, SQLAlchemy's pool and our `httpx` client. The pools wait 120 seconds and the client 150, so a slow moment is a wait and not an error. The pools stay at 4.
9. **The service's run slot is a lock taken without waiting.** A start takes it or gets a 409, and the background thread releases it in a `finally`. It lives in the process, so a restart frees it, and the start of the service marks old runs as stopped.
10. **A plain daemon thread for the background work.** A dispatch runs for minutes. `BackgroundTasks` would hold up uvicorn's shutdown and reloads until it finished, and a plain thread dies with the process, which is what a stop should do. It's synchronous, so it works on Windows, where async Postgres doesn't.
11. **The service waits for the backend when it starts.** `npm run dev` starts them together, so the backend may not be up when the service marks old runs as stopped. The service tries for up to a minute, then fails with a clear error, and until then a start says the service isn't running.
12. **A refusal stays a refusal.** Today any `httpx.HTTPError` from the service becomes "The agent's service isn't running." A 409 is one of those, so the backend now passes a refusal on as a 409 with its reason, and only a connection failure becomes a 503. `run_agent` shares the same path.
13. **The summary is built when it's read.** The backend builds it from the run's claims and their records. A stored summary would go stale: a payout listed as waiting would still say so after a handler signed it off.
14. **A fixed number of queries per poll.** The panel refreshes every 2 seconds on the same 4 connections as 25 agents. `load_claim` per claim would be 100 queries a poll, so a run loads in three: its claims with their decisions, its model calls grouped by claim, and its work grouped by claim.
15. **The agent can't tell a copy from a claim.** A copy's id looks like any other, such as `CLM-3F9A2C1D`, written into its record too, and `get_claim` returns the same fields. If the record said "evaluation", the model could act differently and the scores wouldn't reflect real work. The dashboard learns a claim is a copy from a separate operation, `get_claim_evaluation`, which no tool calls. The link back to the original is in columns outside the record.
16. **The answer key stays in its tables.** `handler_decisions` and `held_out_claims` are named only in `db.py` and `evaluations.py`, and a test checks that. The agent's `api.py` has no call that reads them.
17. **The limits on N are served by the backend.** 1, 25 and the starting value of 3 are rules, and rule 11 says the dashboard never repeats a rule, so `get_run_options` serves them and the form offers them in a `select`.
18. **Live runs only at the end.** The brief says to reseed once and keep to its runs, so unlike earlier plans no phase has a live check of its own, and Phases 1 to 5 stand on tests. That means OpenRouter's `usage.cost` is first checked live in Phase 6, though the lab has already shown its shape.

### The records

New columns on `claims`: `submitted_on`, `evaluation_id` and `source_claim_id`, the last two empty on the queue's claims.

| Table | What it holds |
|---|---|
| `runs` (replaced) | `run_id`, `kind` (`dispatch` or `evaluation`), `agents_at_once`, `claim_set` and `tiny` (evaluations only), `model`, `reasoning_effort` (`none` when not set), `status` (`running`, `finished` or `stopped`), `started_at`, `finished_at` |
| `run_claims` | `run_id`, `claim_id`, `position` |
| `model_calls` | `claim_id`, `run_id` (empty with no run), `input_tokens`, `output_tokens`, `cost`, `seconds`, `created_at` |
| `work_times` | `claim_id`, `run_id` (empty with no run), `started_at`, `finished_at`, `seconds` |
| `held_out_claims` | The held-out claims, as records, out of the queue |
| `handler_decisions` | `claim_id`, `claim_set`, `decision`, `payout`, `tiny` |

`todos` goes. `DROP SCHEMA` already removes it on Supabase, and once it's out of the metadata it never comes back. After pulling this build, `npm run seed` is needed, since `engine_for` seeds only a database with no `claims` table.

### The API changes

Every operation keeps an `operation_id`, a one-line docstring and no optional inputs. `run_id` on the two recording operations is required and may be null.

| Operation | Method and path | What it does |
|---|---|---|
| `record_model_call` | `POST /api/claims/{claim_id}/model-calls` | New: one call's tokens, cost and seconds, with its run |
| `record_work` | `POST /api/claims/{claim_id}/work` | New: one stretch of work, with its run |
| `get_run_options` | `GET /api/run-options` | New: the lowest, highest and starting N |
| `start_dispatch` | `POST /api/dispatches/start` | New: asks the service to start; `202`, or `409` or `503` with the reason |
| `create_dispatch` | `POST /api/dispatches` | New: for the service. Records the dispatch and chooses its claims |
| `get_latest_dispatch` | `GET /api/dispatches/latest` | New: the run with its claims, totals and summary, or null |
| `list_run_claims` | `GET /api/runs/{run_id}/claims` | New: the run's claim ids in order, for the dispatcher |
| `finish_run` | `POST /api/runs/{run_id}/finish` | Changed: takes `finished` or `stopped`, with no summary |
| `stop_running_runs` | `POST /api/runs/stop` | New: marks every running run as stopped |
| `start_evaluation` | `POST /api/evaluations/start` | New: as `start_dispatch`, with N, the set and `tiny` |
| `create_evaluation` | `POST /api/evaluations` | New: for the service. Records the evaluation and makes its copies |
| `list_evaluations` | `GET /api/evaluations` | New: newest first, with every measure |
| `get_evaluation` | `GET /api/evaluations/{run_id}` | New: its measures, and each claim beside the handler's decision |
| `get_claim_evaluation` | `GET /api/claims/{claim_id}/evaluation` | New: the evaluation a copy belongs to, or null |
| `get_instructions` | `GET /api/instructions` | Changed: there's now one set, so the name goes |
| `list_claims` | `GET /api/claims` | Changed: leaves out copies |
| `sign_off_claim`, `run_agent` | as today | Changed: refused with `409` on a copy |
| `start_run`, `get_latest_run`, `set_todos` | | Removed |

The agent's service gains `POST /dispatches` with `agents_at_once`, and `POST /evaluations` with `agents_at_once`, `claim_set` and `tiny`, each replying `202` or `409`.

### Evaluations

`create_evaluation` copies the set's claims from their source: the queue's records for the evaluation set, which hold no decisions or status, so a claim the agent has already decided copies clean; `held_out_claims` for the held-out set. Each copy starts `waiting`, with a new id in its row and its record.

From there it runs through the same dispatcher and claim agent, with these differences, each one enforced in one place:

- `list_claims` leaves copies out, so they never show in the queue. `create_dispatch` never chooses one, so a dispatch never carries one on.
- `sign_off_claim` and `run_agent` refuse a copy, and the claim page shows neither form.
- `agents.work` clears a copy's thread with `delete_thread` when it finishes normally, so an approval held at `interrupt()` counts as finished and leaves no saved progress. The dispatcher never waits for it.
- A copy within the limit is "paid" by the graph as usual, since it's the same claim agent. It's only a record on the copy, which is why the brief scores a wrongful approval whether or not it was paid.

### The measures

`backend/scoring.py` has pure functions with the lab's rules, taking the agent's answer and the handler's. A claim with no decision counts as `refer` with no payout. Amounts compare to the cent, so that a float can't push a difference of exactly a dollar over the line.

| Measure | How it's worked out |
|---|---|
| `decision_accuracy` | Share of claims whose decision matches the handler's |
| `accuracy` | Share whose decision matches and payout is within a dollar |
| Cost of mistakes | Wrongful decline $1,500, wrongful approval its payout, needless referral $50, wrong amount on a right approval the difference, as the lab's `mistake_cost` |
| Straight-through rate | Share of claims not referred |
| Model cost per claim | The run's model cost, failed attempts included, over its claims |
| Time per claim | The mean of each claim's work in this run |
| Time for the whole run | First start to last finish in `work_times`, as the lab does, so a stopped run still gives an honest number |

The test worked out by hand uses six claims:

| Handler | Agent | Right decision | Accurate | Mistake |
|---|---|---|---|---|
| Approve $500.00 | Approve $500.50 | Yes | Yes | $0.50 |
| Approve $1,000.00 | Approve $900.00 | Yes | No | $100.00 |
| Approve $800.00 | Decline | No | No | $1,500.00 |
| Decline | Approve $300.00 | No | No | $300.00 |
| Decline | No decision | No | No | $50.00 |
| Refer | Refer | Yes | Yes | $0.00 |

That gives `decision_accuracy` 0.5, `accuracy` 2/6, a cost of mistakes of $1,950.50 and a straight-through rate of 4/6.

### The dashboard

- **The run panel, on the queue page:** a `select` for N and "Start a dispatch". Below it, the latest dispatch's state (Running, Finished or Stopped), its model cost and time, and each claim with a link and a status chip. When it ends, the summary: counts of approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress. A refusal or a stopped service shows as an alert.
- **Scoreboard (`#/scoreboard`):** the form (N, the set, whole or tiny), then a table of the evaluations, newest first, with every measure.
- **An evaluation (`#/scoreboard/evaluation-3f9a2c1d`):** its set, N, model and effort, then a row per claim with the handler's decision and payout beside the agent's, its cost and time, and a link to its page. A miss has a Raspberry "Miss" chip, so it doesn't rely on color alone.
- **A copy's page:** a note that it's an evaluation's copy, with a link back, and no "Run the agent" or sign-off form.
- **Instructions:** the claim agent's only, with the text rewritten.
- **Brand:** a `stopped` chip, numbers in JetBrains Mono, money as $1,250.00, and time as `1m 12s`.

### The claim agent's instructions

These don't change, as the brief says. `backend/instructions/queue.md` goes, and `claim.md` stays as it is, so there's nothing new to write as goals and rules.

### What this plan leaves out

- **Stopping a dispatch from the dashboard.** The brief doesn't ask for it.
- **Clearing the saved progress of copies in a stopped evaluation, or of a copy that failed.** The brief asks for it on held approvals. The rows do no harm, nothing carries them on, and a reseed clears them.
- **The time of work cut off by a kill.** Its row is written when the work ends, so `kill -9` loses the time of the stretches in flight. Their cost is safe, since each call is recorded as it happens. The live comparison uses claims worked from the start, so it isn't affected.
- **`uv run -m agent CLM-1001` during a dispatch.** The set of claims being worked is per process, so the command must not run beside a dispatch, as two queue runs at once were never supported. `docs/STATUS.md` will say so.
- **Scores in Langfuse.** The lab sends them, but the brief puts them on the Scoreboard.
- **Rejecting a held payout.** It's still for a later build.

---

## The code's structure

Each module has one job, and imports point one way. Nothing imports `service.py` or `__main__.py`, and the backend never imports the agent.

### `backend/`

| Module | Its one job | Imports |
|---|---|---|
| `db.py` | The database: the engine, behind a lock on first use, the tables, seeding (now with the answer key) and reset | `sqlalchemy` |
| `models.py` | The shapes the API sends and receives | `pydantic` |
| `records.py` | New: looking up one record (`lookup` and `find`, moved from `main.py` so there's still one way) and `now` | `db` |
| `documents.py`, `payout.py` | Unchanged | `db`, `models` |
| `scoring.py` | New: the measures, as pure functions | `models` |
| `runs.py` | New: a run's records: creating a dispatch, loading a run with its claims and totals, and its summary | `db`, `models`, `records` |
| `evaluations.py` | New: making the copies, and reading the answer key to score a run. The only module beside `db.py` that names it | `db`, `models`, `records`, `runs`, `scoring` |
| `agent_client.py` | Every call to the agent's service, now with a JSON body | `httpx` |
| `main.py` | The operations and the claim rules | `agent_client`, `db`, `documents`, `evaluations`, `models`, `payout`, `records`, `runs` |
| `seed.py` | `uv run -m backend.seed` | `db` |

### `agent/`

```
                            __main__.py
                                 │
                                 ▼
service.py ──▶ dispatcher.py ──▶ agents.py ──▶ tools.py ──▶ api.py
                                 │                           ▲
                                 └──▶ harness.py ────────────┘ (Refused only)
```

The arrows show the main line. The table gives each module's full list.

| Module | Its one job | Imports |
|---|---|---|
| `api.py` | Every call to the backend. Adds the recording and run calls; loses the to-do and queue calls | `httpx` |
| `harness.py` | Everything around the model that knows nothing about claims: the model and its settings, reading a reply's usage, tracing with an optional tag, the shared pool, a saver per run, clearing a thread, the retry policies, steps, and the refusal message. The middleware goes | `api` (for `Refused`) |
| `tools.py` | The claim agent's seven tools, unchanged. The queue agent's tools, `RunContext` and `report` go | `api` |
| `agents.py` | Builds and runs the claim agent: the graph, `work_claim`, `carry_on`, the set of claims being worked, and `work` | `api`, `harness`, `tools` |
| `dispatcher.py` | New: the graph with no model, and `dispatch` and `evaluate`, which create the run, invoke the graph and finish the run | `agents`, `api`, `harness` |
| `service.py` | The agent's web service: the run slot, the background thread and marking old runs as stopped | `agents`, `api`, `dispatcher` |
| `__main__.py` | `uv run -m agent CLM-1001`. A claim id is now required | `agents`, `harness` |

### `dashboard/` and `e2e/`

- `dashboard/src/api.ts`: the new types and endpoints, `startDispatch`, `startEvaluation` and `formatDuration`. The to-do types go.
- `dashboard/src/RunPanel.tsx`: the panel above, with a `RunSummary` component.
- `dashboard/src/ScoreboardPage.tsx` and `dashboard/src/EvaluationPage.tsx`: new, one page each.
- `dashboard/src/components.tsx`: a `RunChip`, and an `AgentsSelect` shared by both forms. `TodoChip` goes.
- `dashboard/src/hooks.ts` and `App.tsx`: the two routes and the nav link.
- `dashboard/src/ClaimPage.tsx`, `InstructionsPage.tsx`: as above.
- `e2e/agent-stand-in.ts`: reads a body, records `dispatch 7` or `evaluation 5 held_out tiny`, and handles the two new paths.
- `e2e/queue.spec.ts`: the to-do test goes, and a dispatch played out through the API takes its place. `e2e/scoreboard.spec.ts` is new.

### Checked against `docs/CODE_STANDARDS.md`

- **Rule 8:** `service → dispatcher → agents → tools → api`, with `harness` beside them, and `main → evaluations → runs → records → db` in the backend. No two modules import each other, which is why `agents.work` isn't in the harness.
- **Rules 9 and 10:** the agent's calls stay in `api.py`, the backend's in `agent_client.py` and the dashboard's in `api.ts`, and the existing tests check all three. `lookup` and `find` move and stay the one way to read a record.
- **Rule 11:** the order of the queue, the limits on N, the scoring and every refusal on a copy are in the backend. The one rule outside it is "one run at a time", which the brief gives to the service, since the service is what runs them.
- **Rule 12:** LangGraph's `Send`, `max_concurrency`, `RetryPolicy`, `error_handler` and runtime context, and FastAPI's lifespan. No hand-written retry loop or semaphore.
- **Rule 13:** the harness learns about usage and tags, not claims. The dispatcher knows nothing of the model, and a test checks it imports no chat model.
- **Rule 15:** the instructions don't change. Choosing claims, retrying and scoring are all code.

---

## Phase 1: The default model, and cost and time

Move to GPT-6 Luna, and record what each claim costs and how long it takes, while the queue agent is still in place.

- `agent/harness.py`: `DEFAULT_MODEL`, `model_settings()` for the name and the effort, and `usage(reply)`.
- `backend/db.py`, `models.py`, `main.py`: `model_calls`, `work_times`, `record_model_call` and `record_work`; `now()` with microseconds. `backend/records.py` takes `lookup`, `find` and `now`.
- `agent/api.py`: the two recording calls.
- `agent/agents.py`: `ClaimContext` gains `run_id`; the `agent` node records each call and its seconds; `run_thread` records each stretch of work in a `finally`; `work_claim` and `carry_on` take a `run_id`, with none by default.
- `agent/tests/stubs.py`: replies that carry OpenRouter's usage.

Success criteria:

- [x] Tests check the model: `openai/gpt-6-luna` with `low` effort by default; with `MODEL_NAME` set, that model with no reasoning effort sent, and `model_settings()` gives `none`
- [x] A stub-model test works a claim whose replies carry OpenRouter's `token_usage` and `cost`, and the backend has one `model_calls` row per reply with those tokens and that cost
- [x] A reply with no usage, as a free model may give, records zero tokens and zero cost and doesn't fail the claim
- [x] A claim worked with a run id records its calls and work under that run; worked without one, they're recorded with no run
- [x] A claim stopped partway under one run id and carried on under another has its calls split between the two, which shows the run id isn't kept in the graph's state
- [x] A claim that fails keeps the rows for its calls and its work, and the attempt after it adds its own under the same run
- [x] With a fake clock, a claim held for sign-off for an hour, then signed off and carried on, records only the two stretches of work, and neither includes the hour
- [x] The test that every route has an `operationId`, a summary and no optional inputs passes with the two new operations
- [x] `npm run check:python` passes with coverage at or above 80%

## Phase 2: The dispatcher in place of the queue agent

Build the dispatcher, make the connections fit 25 agents, and remove the queue agent and the to-do list.

- `pyproject.toml` and `uv.lock`: `langgraph>=1.2.18`, locked at 1.2.20.
- `backend/`: the new `runs`, `run_claims` and `submitted_on`; `runs.py`; `create_dispatch`, `list_run_claims`, `finish_run`, `stop_running_runs`, `get_latest_dispatch`. The pool waits 120 seconds, and `engine()` takes a lock. `todos` and its operations go.
- `agent/harness.py`: the shared pool behind a lock with a 120-second wait, a saver per run, `thread_config` with a tag, `clear_thread`, the worker's retry policy and the errors it retries. The middleware goes.
- `agent/agents.py`: `work`, the set of claims being worked, and `run_thread` letting rate limits and timeouts through. `run_queue`, `carry_on_stopped`, `missed_claims` and `queue_call_limit` go.
- `agent/dispatcher.py`: the graph, `dispatch(agents_at_once)`.
- `agent/api.py`: the client's timeout goes to 150 seconds.
- `agent/tools.py`, `agent/__main__.py`, `backend/instructions/queue.md`, `agent/tests/test_queue.py`: the queue agent goes. `agent/tests/test_dispatcher.py` is new.
- `dashboard/`: the to-do list goes, the run panel shows the latest dispatch's claims and state, and the instructions page shows the claim agent's alone, so each phase stays green. Phase 3 adds starting a dispatch.

Success criteria:

- [ ] A test holds each claim open and counts: with 8 claims at N=3 the most at once is exactly 3, and at N=1 it's 1
- [ ] A dispatch works the claims in progress and waiting, oldest submitted first with ties by id, and leaves the claims held for sign-off and the decided ones alone
- [ ] A dispatch with nothing to work finishes at once
- [ ] A rate limit: a stub model that raises `ModelRateLimitError` partway through one claim of several, then recovers, ends with one decision on the same thread, failures still 0, and the prompt after the retry holds the earlier tool results. The same test passes for `ModelTimeoutError`
- [ ] Retries run out: with several claims in the dispatch, one that always hits a rate limit stays `in_progress` with failures 0, the others finish, and `dispatch` returns normally with the run `finished`. This is the regression test for the LangGraph bug, and it fails on 1.2.12
- [ ] The next dispatch carries that claim on as the same attempt, to one decision
- [ ] A claim that fails with any other error goes back to `waiting`, the same dispatch tries it once more on a new thread, and it ends decided with 1 failure
- [ ] A claim that fails twice ends `left_for_handler`, and the dispatch finishes. A claim that already had one failure is left after its next failure, with no further try
- [ ] A claim that ends with no decision counts as a failure and is tried once more
- [ ] A claim held for sign-off in a dispatch waits on its own thread and doesn't pause the dispatch, and a sign-off after the dispatch still pays it once
- [ ] A claim the service is carrying on after a sign-off is skipped by a dispatch, with one payment; a sign-off's `carry_on` during a dispatch's work on that claim does nothing, with one payment
- [ ] A claim signed off while the service was down is paid by the next dispatch
- [ ] Each claim is a trace of its own inside the dispatcher, with its session and its run's tag (`langfuse_tags`), and `thread_config` adds no tag with no run
- [ ] Tests check the checkpointer: each call in Postgres mode returns a new saver on one shared pool, `setup()` runs once, the pool has at most 4 connections and a 120-second wait, and 25 threads at once open one pool. In memory, the saver is shared
- [ ] Tests check the backend's engine: a pool of 4, no overflow, a 120-second wait, and one engine from 25 threads at once
- [ ] Structure tests: `dispatcher.py` uses only `agents.work` from `agents`, imports no chat model, and compiles with no checkpointer. `run_queue`, `write_todos`, `create_agent` and the middleware's names appear nowhere in `agent/`
- [ ] `uv run -m agent CLM-1001` works one claim, and `uv run -m agent` with no claim id exits with a usage message (tested with a stub)
- [ ] Vitest tests pass for the run panel with a dispatch's claims, and for the instructions page; `npm run e2e` passes with a dispatch played out through the API, and with the to-do test gone
- [ ] `npm run check` passes with coverage at or above 80%

## Phase 3: Starting a dispatch from the dashboard

Let a handler start a dispatch and see it through, with the summary, the cost and the time.

- `agent/service.py`: `POST /dispatches`, the run slot, the daemon thread, and marking old runs as stopped in the lifespan, after waiting for the backend.
- `agent/dispatcher.py`: a run that fails outright is marked `stopped`.
- `backend/agent_client.py`: a body on each call, and `start_dispatch`.
- `backend/main.py`: `start_dispatch` and `get_run_options`, with one path for reaching the service that passes a 409 on and turns a connection failure into a 503.
- `backend/runs.py` and `models.py`: a run's totals, each claim's cost, time and start, and the summary.
- `dashboard/`: the form, the totals and the summary.
- `e2e/agent-stand-in.ts`, `e2e/queue.spec.ts`.

Success criteria:

- [ ] Tests pass for the service: a start replies `202` at once, before the run is created; a second start replies `409` while the first is working; the slot is free again after a run finishes and after one fails
- [ ] Tests pass for the service's start: it marks any running dispatch as stopped; it keeps trying while the backend can't be reached; and it fails with a clear error when the backend never comes
- [ ] A failure inside the background thread marks the run as `stopped`, not `running`
- [ ] Tests pass for the backend: `start_dispatch` passes N to the service and writes nothing to the database; with a stand-in that refuses, it returns `409` with the service's reason; with the service unreachable, `503` with "The agent's service isn't running."; N of 0 or 26 returns `422`
- [ ] The existing tests for `run_agent` and the sign-off pass on the shared path
- [ ] A test builds the summary from a run's records, with claims approved, declined and referred, one held at a payout, one left for a handler and one in progress, and checks each count and list
- [ ] A test checks a run's totals are its own: two runs and a claim worked with no run, with the failed attempt's cost included in its run
- [ ] A test counts the queries `get_latest_dispatch` makes: the same with 25 claims as with 2
- [ ] The test on the service's API rules passes with the new operations, now that some have a body
- [ ] Structure tests: the claim agent's tools are the same seven; `dashboard/` names no address but the backend's; `signal`, `os.fork` and async Postgres appear nowhere in `agent/`. Windows is covered by these and by `package.json` being unchanged, since this build can't be run on Windows here
- [ ] Vitest tests pass for the form (N offered from the options, starting at 3), a refusal and a stopped service showing as alerts, a running dispatch, a finished one with its summary, and a stopped one
- [ ] `npm run e2e` passes, with a test that choosing 7 and starting a dispatch reaches the stand-in as `dispatch 7`
- [ ] `npm run e2e` passes a second time in a row, and with `npm run dev` running at the same time
- [ ] In the browser with the Playwright MCP server, on the end-to-end servers: the form, a dispatch played out through the API and its summary match the brand in light and dark mode, and the console shows no errors
- [ ] `npm run check` passes with coverage at or above 80%

## Phase 4: The evaluations

Seed the answer key and work copies of a labelled set through the same dispatcher, leaving the queue untouched.

- `backend/db.py`: `held_out_claims`, `handler_decisions`, the two new claim columns, and seeding from the three named files in `data/evals/`.
- `backend/evaluations.py`: making the copies.
- `backend/main.py`: `create_evaluation`, `start_evaluation`, `get_claim_evaluation`, and the refusals on a copy; `list_claims` leaves copies out.
- `backend/agent_client.py`: `start_evaluation`.
- `agent/agents.py`: `work` clears a copy's thread.
- `agent/dispatcher.py`: `evaluate`. `agent/service.py`: `POST /evaluations`.
- `dashboard/src/ClaimPage.tsx`: a copy's page.

Success criteria:

- [ ] Seeding: the queue still holds exactly the 25 claims in `data/claims.json`; `held_out_claims` holds 25; `handler_decisions` holds 50, with five `tiny` in each set; and seeding reads only `decisions.json`, `held_out_claims.json` and `held_out_decisions.json` under `data/evals/`, never `harder/` or `even_harder/`
- [ ] Tests pass for the copies: each set gives 25, and `tiny` gives its five; each copy has a new id in its row and in its record, so `get_claim` returns the copy's id; and a queue claim already decided copies clean
- [ ] `get_claim` on a copy has exactly the same fields as on a queue claim, and holds nothing from the handlers' decisions: no reason, calculation, tags or handler, and no field that names the evaluation
- [ ] A structure test: `handler_decisions` and `held_out_claims` are named only in `backend/db.py` and `backend/evaluations.py`, and `agent/api.py` calls neither `get_evaluation` nor `list_evaluations`
- [ ] A stub-model evaluation at N=5 leaves `list_claims` exactly as it was, with the same statuses and counts, and the run `finished`
- [ ] In that evaluation, a copy approved over the limit counts as finished; the run doesn't wait for it; its thread has no saved state; and it has no payment
- [ ] A later dispatch never picks up a copy, including one left in progress by a stopped evaluation
- [ ] `sign_off_claim` and `run_agent` on a copy return `409` and change nothing
- [ ] A rate limit and a failure behave in an evaluation as in a dispatch, and cost and time are recorded under the evaluation
- [ ] The service refuses an evaluation while a dispatch runs, and a dispatch while an evaluation runs; an evaluation still running when the service starts shows as stopped; an evaluation records its set, N, model and effort
- [ ] Vitest tests pass for a copy's page: a note with the link back, and no "Run the agent" or sign-off form, even at `awaiting_sign_off`
- [ ] `npm run check` passes with coverage at or above 80%

## Phase 5: The scoreboard

Score each evaluation and show it on a new page.

- `backend/scoring.py`: the measures.
- `backend/evaluations.py` and `main.py`: `list_evaluations` and `get_evaluation`.
- `dashboard/src/ScoreboardPage.tsx`, `EvaluationPage.tsx`, `hooks.ts`, `App.tsx`, `styles.css`.
- `e2e/scoreboard.spec.ts`, `e2e/agent-stand-in.ts`.

Success criteria:

- [ ] Tests pass for each mistake on its own, including a wrongful approval that was never paid, an agent's decline where the handler referred, and a payout exactly a dollar away counting as accurate
- [ ] The six claims worked out by hand above give `decision_accuracy` 0.5, `accuracy` 2/6, a cost of mistakes of $1,950.50 and a straight-through rate of 4/6
- [ ] Cost per claim, time per claim and whole-run time match figures worked out by hand from a set of `model_calls` and `work_times` rows, with a failed attempt's cost included
- [ ] A claim with no decision counts as a referral in every measure
- [ ] `list_evaluations` is newest first, with the same number of queries for 2 evaluations as for 10; a stopped evaluation shows as stopped with measures from what it did
- [ ] `get_evaluation` has each claim's handler and agent decision, payout, reason, cost, time and a miss flag, in the set's order; an unknown id returns `404`
- [ ] Vitest tests pass for the form, the list, an evaluation's page with its misses marked and its claims linked, and the routes
- [ ] `npm run e2e` passes, with tests that starting an evaluation on the page reaches the stand-in as `evaluation 5 held_out tiny`, and that an evaluation played out through the API shows on the scoreboard with its measures, opens to its claims with a miss marked, and links to a copy's page
- [ ] `npm run e2e` passes a second time in a row, and with `npm run dev` running at the same time
- [ ] In the browser with the Playwright MCP server, on the end-to-end servers: both pages match the brand in light and dark mode, with no edge borders, and the console shows no errors
- [ ] `npm run check` passes with coverage at or above 80%

## Phase 6: The live runs and the handover

Run the whole product on Supabase with the real model, once, and bring the docs up to date. This is the only phase that calls a model, and it reseeds once and keeps to the brief's runs. That's about 55 claims. My rough estimate is a dollar at most on GPT-6 Luna, and the first real figure comes from these runs.

Before starting: `MODEL_NAME` is unset, and any lab notebook's kernel is closed, since each holds a connection.

- `docs/SPEC.md`: the sections on working the queue and on what the team needs to see. The Rules stay.
- `docs/TECH_STACK.md`: the dispatcher, the six parts, no queue agent, the default model, the shared pool and a saver per run, and the LangGraph floor with why.
- `docs/PROCESS.md`: these runs replace the five-claim cap; seeding reads the answer key.
- `docs/CODE_STANDARDS.md`: rule 9 adds `backend/agent_client.py`; rule 12 loses the middleware; rule 13 adds the dispatcher and says what the harness is now; rule 14 moves from tools in `create_agent` to LangGraph's runtime context; rule 15 loses the middleware.
- `docs/STATUS.md`: what's built, how to run it, and the live results, including that `npm run seed` is needed after pulling.

Success criteria:

- [ ] After one `npm run seed`, through the Supabase MCP server, `undercover.todos` is gone and the new tables are there, with 25 claims all `waiting`
- [ ] A stop: with `npm run dev` running, a dispatch at N=3 started on the dashboard is stopped partway with `kill -9` on every process from `lsof -ti tcp:8002`, and `npm run dev` stops the others. After starting it again, the panel shows that dispatch as Stopped
- [ ] The next dispatch: at N=25 it carries on the claims left in progress on the same threads with their failures unchanged, each with one decision, and works the rest. The claims' start times in `get_latest_dispatch` show them starting together
- [ ] The run takes well under the sum of its claims' times, both read from `get_latest_dispatch`
- [ ] Comparing claims worked from the start, each takes about as long as at N=3; if slower, `docs/STATUS.md` reports where the time goes, from model seconds against the rest, keeping to 4 connections
- [ ] Claims held for sign-off wait. Signing one off on the dashboard through the Playwright MCP server during the dispatch shows it paid within five seconds
- [ ] No claim fails for want of a database connection: no such error step on any claim, and none in the service's log
- [ ] During the dispatch, sampled repeatedly through the Supabase MCP server, `pg_stat_activity` shows no more than 12 connections whose user is `postgres`
- [ ] When the dispatch finishes, the panel shows its summary, model cost and time, and each figure matches the records
- [ ] An evaluation of the evaluation set at N=25, and one of the held-out set's five `tiny` claims at N=5, both started on the Scoreboard page, appear there with every measure, and the queue page's counts are the same before and after
- [ ] Opening an evaluation through the Playwright MCP server shows its misses and links to a copy's page, which has no sign-off form
- [ ] Through Langfuse's API, the claims of the N=25 dispatch are found together by its tag. The legacy traces API isn't available on this account, so this uses the v2 observations API's `tags` filter, as `docs/STATUS.md` notes
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` and `docs/STATUS.md` describe the product as it now is, and `docs/SPEC.md`'s Rules are unchanged
- [ ] `npm run check` passes in full with `.env` moved out of the way: ruff, pyright, pytest at 80% or more, ESLint, Prettier, tsc, vitest at 80% or more, and Playwright
