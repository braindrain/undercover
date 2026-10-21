# Plan: the agent's harness

This plan builds `docs/HARNESS.md` on top of the pilot. Today our code organizes the work: `uv run -m agent` loops over the queue in Python, and each claim gets the whole policy wording in its prompt. After this build, one agent runs the queue. It plans a to-do list, hands each claim to a fresh subagent, looks up the sections of the wording and the claims manual it needs, and puts its own failures right.

There are five phases, each building on the last: the backend's new records and rules, then the claim agent's harness, then the queue agent, then the dashboard, and last the end-to-end tests, a live run and the handover.

## The approach

### Two agents, one harness

```
uv run -m agent --cap 5
        │
        ▼
 the queue agent ── list_waiting_claims, write_todos, work_claim
        │                                    │
        │                                    ▼  one at a time, each from a clean start
        │                            the claim agent ── get_claim, get_policy, get_claim_history,
        │                                    │          list_sections, read_section,
        │                                    │          record_decision, pay_claim
        │◀── "CLM-1001: approved, $420.00, paid." ──┘
        ▼
 a short summary for the handlers
```

- **The queue agent** (new) runs the queue. It lists the waiting claims, writes its to-do list, works through it with `work_claim`, keeps the list up to date, checks it hasn't missed a claim, and finishes with a summary.
- **The claim agent** is the pilot's agent, `run(claim_id, thread_id)`, with a harness around it. `work_claim` runs it as the subagent. It's still what "Run the agent" on the claim page and `uv run -m agent CLM-1001` run.
- Both are LangChain `create_agent` agents. The harness is their tools, their middleware and their instructions. We don't add Deep Agents or any other agent framework, as the brief asks.

### How a claim moves through the system

```
waiting ──start──▶ in_progress ──record decision──▶ decided
   ▲                    │                       └──▶ awaiting_sign_off ──sign off──▶ decided (paid)
   └──release (1st)─────┤
                        └──release (2nd)──▶ left_for_handler
```

The new state is `left_for_handler`, shown as "Left for a handler". A release is a failed attempt: the claim went in progress and came out without a decision.

### Key choices, and why

1. **The backend counts the failures.** `release_claim` adds one to the claim's failures. The first release puts the claim back to `waiting`; the second puts it in `left_for_handler`, where `start_claim` refuses it. As in the pilot, the rule lives where it can't be skipped: a model that forgets, or a crashed run, can't retry a claim a third time.
2. **Nothing is decided or paid twice, by the backend's existing rules.** `record_decision` needs a claim in progress with no decision, and `pay_claim` refuses a second payment. That's what makes the retries below safe: if a timeout hides a `record_decision` that actually landed, the retry gets a `409` saying the claim is decided, not a second decision.
3. **Retries happen at three levels, each in one place.**
   - A model call that times out or errors is retried by LangChain's `ModelRetryMiddleware`, three times with backoff. The model client gets a 120-second timeout and no retries of its own, so there's one retry policy and a test can see it work.
   - A tool call that can't reach the backend is retried by `ToolRetryMiddleware`, and if it still fails the model is told the error rather than the run crashing.
   - A claim whose run still fails is released (one failure) and goes back in the queue. The queue agent works it again later in the run. A second failure leaves it for a handler.
   - `ModelCallLimitMiddleware` caps each claim at 30 model calls, so a claim agent that loops ends and counts as a failure, rather than spending without limit.
4. **One bad claim never stops the run.** `work_claim` is the one place that catches a claim's failure on purpose. It turns the error into its one-line report and returns, so the queue agent carries on. The claim agent's `finally` still releases the claim, so it's never left in progress.
5. **The subagent reports back in a line, written by our code.** After the claim agent finishes, `work_claim` reads the claim from the backend and builds the line from the record: "CLM-1001: approved, $420.00, paid.", "CLM-1002: approved, $1,150.00, waiting for sign-off.", "CLM-1004: failed (the model timed out), back in the queue.", or "... failed twice, left for a handler." Building it from the record means the queue agent's view is the truth, whatever the claim agent said. The claim agent's own final reply is still recorded on the claim.
6. **Each claim starts clean.** `work_claim` builds a new claim agent with an empty conversation for every claim. The queue agent's context only grows by one short line per claim, so the 25th claim costs about what the first does.
7. **The to-do list is ours, stored in the backend.** The queue agent's `write_todos` tool replaces the run's list in the backend, and the dashboard reads it from there. We write this tool ourselves rather than use LangChain's `TodoListMiddleware`, because the brief asks for four states (to do, in progress, done, left for a handler) and that middleware has three, and because it keeps the list in the agent's memory, where the dashboard can't see it. Each item is a line of text and a status. The tool replies "Saved." rather than echoing the list, to keep the queue agent's context short.
8. **The cap is enforced in code.** `uv run -m agent --cap 5` starts a run capped at five claims. `work_claim` counts the distinct claims it has handed out and refuses a sixth with a clear reason; working the same claim again after a failure doesn't count twice. With no cap, the run's cap is the number of claims waiting when it starts.
9. **The check for missed claims is middleware, with the instructions as the first line.** The queue agent's instructions tell it to list the waiting claims again before it finishes. Behind that, an `after_model` middleware looks at the queue agent's final reply: if a claim is still waiting and the run can still work it (it's within the cap, or it's one this run has already tried), the middleware adds a message naming the claims and sends the agent back to work. As with the payment rule in the pilot, an instruction can be skipped and code can't.
10. **The documents are looked up by section.** The backend splits the policy wording and the claims manual at their numbered `##` headings. `list_sections` gives a document's contents (its title, its opening lines and its numbered sections), and `read_section` gives one section. The wording leaves the system prompt, so the claim agent works the way a handler does: contents first, then the sections the claim needs. The opening lines come with the contents because the wording's version is there, and the manual's section 13 says the version matters.
11. **Steps are recorded by middleware.** The pilot recorded steps in `run()`'s loop and said middleware was for this build. A step-recording middleware now does it: `after_model` records the reply and each tool call, and `wrap_tool_call` records each result. A `read_section` call is recorded as a step of a new kind, `read`, with the section's heading, such as "Claims manual: 9. Repeat claims", followed by the section's text. The claim page lists the `read` steps as "What the agent read". The step recorder only sees successful model calls, so a retried call doesn't record twice.
12. **The model is DeepSeek V4.1 Flash.** `deepseek/deepseek-v4.1-flash` on OpenRouter with low reasoning effort is the default, as the brief asks, with the same settings as the Day 5 lab. `MODEL_NAME` still overrides it. Both agents use the same model.
13. **Two sets of instructions, in the backend.** `backend/instructions/queue.md` and `backend/instructions/claim.md`, served by `get_instructions(name)`. The instructions page shows both. The claim instructions follow the manual's order for working a claim (section 4) and its three decisions (section 2), and say which sections every claim needs: the benefit's section of the wording, its general exclusions (9) and how payment is worked out (10), and in the manual the documents (5), the payout (6), the interpretation notes (7) and repeat claims (9), plus any other section the claim calls for. They come from the wording and the manual alone; I won't open `data/evals/`, and they name no claims.
14. **Claims are worked one at a time.** Subagents in parallel would be faster, but the to-do list would be harder to follow, the logs would interleave, and a trial of five claims doesn't need it.

### What this plan leaves out

- **A button to start a run.** The brief starts the run from the terminal and asks the dashboard to watch it, so the queue page shows the run but doesn't start one. "Run the agent" on a claim stays.
- **Recovering from a killed process.** Every failure the agent can see releases its claim, but if the process itself is killed mid-claim, that claim stays in progress until the database is reset. Durable state is the Week 2 Day 3 build.
- **A way for a handler to put a claim left for them back in the queue.** The brief doesn't ask for one; `uv run -m backend.seed` resets the database.

### The API changes

Every operation keeps an `operation_id`, a one-line docstring and no optional inputs.

| Operation | Method and path | What it does |
|---|---|---|
| `release_claim` | `POST /api/claims/{claim_id}/release` | Changed: counts a failure; `waiting` after the first, `left_for_handler` after the second |
| `get_claim` | `GET /api/claims/{claim_id}` | Changed: adds `failures` |
| `get_instructions` | `GET /api/instructions/{name}` | Changed: `queue` or `claim` |
| `get_document` | `GET /api/documents/{document}` | Replaces `get_policy_wording`: the wording or the manual in full, for the instructions page |
| `list_sections` | `GET /api/documents/{document}/sections` | `policy_wording` or `claims_manual`: title, opening lines, numbered sections |
| `read_section` | `GET /api/documents/{document}/sections/{number}` | One section as Markdown, from its heading |
| `start_run` | `POST /api/runs` | Starts a run with a cap of at least 1 |
| `get_latest_run` | `GET /api/runs/latest` | The latest run with its to-do list, or `null` before the first run |
| `set_todos` | `PUT /api/runs/{run_id}/todos` | Replaces the run's to-do list |
| `finish_run` | `POST /api/runs/{run_id}/finish` | Finishes a run with its summary |

The rules, checked in the backend:

- A to-do item has text that isn't empty and a status of `todo`, `in_progress`, `done` or `left_for_handler`.
- `set_todos` and `finish_run` need a run that's still running; otherwise `409`.
- `read_section` returns `404` for a section that doesn't exist, and an unknown document is refused by the path's type.
- `release_claim` leaves a claim that isn't in progress as it is, as in the pilot.

New tables: `runs` (id, cap, status `running` or `finished`, summary, started and finished times) and `todos` (run, position, text, status). The claims table gets a `failures` column.

### The agent's code

- `agent/tools.py`: the backend client and the claim agent's seven tools.
- `agent/middleware.py`: step recording, and the retry and limit middleware the two agents share.
- `agent/main.py`: the model and the claim agent's `run(claim_id, thread_id)`.
- `agent/queue.py`: the queue agent's `run_queue(cap, thread_id)`, its three tools and the check for missed claims. It yields each claim's one-line report as it comes, then the summary.
- `agent/__main__.py`: `uv run -m agent`, `uv run -m agent --cap 5` and `uv run -m agent CLM-1001`.

### The dashboard

- **Queue** (`#/`): a "The agent's run" panel above the table, with the run's state, its cap, when it started, its to-do list with a chip for each item's state, and the summary once it finishes. It refreshes every 2 seconds. Before the first run it says so. A fifth tile and chip for "Left for a handler".
- **Claim** (`#/claims/CLM-1001`): a "What the agent read" panel listing the sections read, and the `read` steps in the timeline labeled "Read". A claim left for a handler says the agent failed on it twice.
- **Instructions** (`#/instructions`): "Running the queue" and "Working a claim", read only, with the policy wording and the claims manual below, closed, as the documents the agent looks things up in.
- **Colors**, following `docs/BRAND.md`'s order: the four states keep their colors, and `left_for_handler` takes Raspberry, the fifth color and the brand's alert color. The to-do states match: done Sea Green, in progress Marigold, to do Cerulean, left for a handler Raspberry.

---

## Phase 1: The backend's records and rules

Build the documents by section, the failure count, the runs and their to-do lists, and the two sets of instructions. Bring the spec up to date first, since it's the requirement the later phases build to.

- `docs/SPEC.md`: the claims manual moves out of "Not for now"; decisions follow the wording and the manual; one run works the queue with a cap; the to-do list and what the agent read join "What the team needs to see"; a claim that fails twice is left for a handler; and the instructions page shows both sets of instructions.
- `backend/db.py`: the `failures` column and the `runs` and `todos` tables.
- `backend/documents.py`: splitting a document into its sections.
- `backend/main.py` and `backend/models.py`: the operations above.
- `backend/instructions/queue.md` and `backend/instructions/claim.md`: first versions, replacing `backend/instructions.md`.
- `agent/main.py` and `dashboard/src/InstructionsPage.tsx`: read the instructions from `/api/instructions/claim`, so the pilot keeps working until phases 2 and 4 replace them.
- Tests for every new operation and rule.

Success criteria:

- [x] `docs/SPEC.md` describes the manual, the queue run, the to-do list, what the agent read and claims left for a handler, and nothing it no longer is
- [x] Tests pass for `list_sections` on both documents: the wording has sections 1 to 12 and the manual 1 to 14, with their titles, and the wording's opening lines include "Version 2026.1"
- [x] Tests pass for `read_section`: manual section 9 starts with `## 9. Repeat claims` and ends before section 10; an unknown section returns `404`; an unknown document is refused
- [x] Tests pass for the failure count: a claim started and released once is `waiting` with 1 failure; started and released again it's `left_for_handler` with 2; starting it then returns `409`; releasing a decided claim leaves it as it is and counts nothing
- [x] Tests pass for runs: `start_run` with cap 5 returns a running run with an empty list; a cap of 0 is refused; `set_todos` replaces the list in order; an empty item or an unknown status is refused; `finish_run` records the summary and time; `set_todos` and `finish_run` on a finished run return `409`; `get_latest_run` returns `null` before any run and the newest run after two
- [x] Tests pass for `get_instructions` with `queue` and `claim`, and an unknown name is refused
- [x] The test that every route has an `operationId`, a summary and no optional inputs still passes with the new routes
- [x] With `npm run dev:backend` running, `curl -s localhost:8000/api/documents/claims_manual/sections` lists the 14 sections and `curl -s localhost:8000/api/documents/claims_manual/sections/10` returns "Meals without receipts"
- [x] `npm run check:python` passes with coverage at or above 80%, and `npm run check:ts` still passes

## Phase 2: The claim agent's harness

Give the claim agent its lookup tools, its middleware, its new instructions and its new model, with the wording out of its prompt.

- `agent/tools.py`: the pilot's five tools, moved, plus `list_sections` and `read_section`.
- `agent/middleware.py`: the step recorder, with `read` steps; `ModelRetryMiddleware`, `ToolRetryMiddleware` and `ModelCallLimitMiddleware`, with their settings in one place. The retry delays are a module constant that tests set to zero.
- `agent/main.py`: the default model `deepseek/deepseek-v4.1-flash` with low reasoning effort, a 120-second timeout and no client retries; `run()` with the claim instructions alone as its system prompt and the middleware in place of the recording loop.
- `backend/models.py`: the `read` step kind.
- `backend/instructions/claim.md`: the full claim instructions.
- Tests on a stub model, as in the pilot.

Success criteria:

- [x] A stub-model test runs CLM-1001 through `get_claim`, `get_policy`, `get_claim_history`, `list_sections`, `read_section` on the wording's section 3, `record_decision` and `pay_claim`; the claim ends `decided` and paid, and its steps are each tool call and result in order, with the read recorded as a `read` step "Policy wording: 3. Trip Delay" followed by the section's text
- [x] A test checks the system prompt is exactly the claim instructions from the API, with no policy wording in it
- [x] A stub-model test whose model fails once, then succeeds, still decides the claim, and records each step once
- [x] A stub-model test whose model always fails leaves the claim `waiting` with 1 failure and an error step; a second such run leaves it `left_for_handler`
- [x] A stub-model test whose tool call can't reach the backend once (a stubbed `httpx` error) retries it and carries on
- [x] A stub-model test that never decides stops at the model call limit, and the claim is released with a failure
- [x] The pilot's tests still pass where their behavior hasn't changed: approve and pay, the refused payment over the limit, a decline, `run_agent`, and no file in `agent/` mentions `data/`
- [x] `main.model()` is `deepseek/deepseek-v4.1-flash` by default and `openrouter/free` when `MODEL_NAME` is set
- [x] `backend/instructions/claim.md` names no claim, customer or policy id (a test checks there's no `CLM-`, `CUS-` or `POL-` in it)
- [x] Live check, one claim, a few cents at most: after `uv run -m backend.seed`, with `npm run dev:backend` running, `uv run -m agent CLM-1001` records a decision with reasons and a section, and `curl` shows its steps with at least one `read` step; then reset with `uv run -m backend.seed`
- [x] `npm run check:python` passes with coverage at or above 80%

## Phase 3: The queue agent

Build the agent that runs the queue: its tools, its to-do list, the subagent, the cap, the check for missed claims and the command.

- `agent/queue.py`: `run_queue(cap, thread_id)`, which starts a run in the backend, runs the queue agent with `list_waiting_claims`, `write_todos` and `work_claim`, finishes the run with the agent's final reply as its summary, and finishes it with the error if the queue agent itself fails; the missed-claim middleware.
- `agent/__main__.py`: `uv run -m agent` works the whole queue, `uv run -m agent --cap 5` caps it, and `uv run -m agent CLM-1001` works one claim. It prints each claim's report as it comes, then the summary.
- `backend/instructions/queue.md`: the full queue instructions: list the waiting claims, choose the order, write the to-do list, mark an item in progress before working it and done or left for a handler after, retry a claim that comes back to the queue, check for waiting claims before finishing, and finish with a summary of a few lines for the handlers.
- Tests on a stub model. Most replace the claim agent with a stand-in, so each test scripts only the queue agent; one runs both agents for real on one scripted model.

Success criteria:

- [ ] The to-do list is stored: a stub-model test calls `write_todos` twice, and `get_latest_run` shows the second list, in order, with its statuses
- [ ] The cap: a stub-model run capped at 2 works CLM-1001 and CLM-1002, and `work_claim` on CLM-1003 is refused with the cap in the reason; CLM-1003 and the other 22 claims stay `waiting`
- [ ] A failed claim doesn't stop the run: with a claim agent that fails on CLM-1001, the run goes on to decide CLM-1002, and finishes with a summary
- [ ] A failed claim is tried again: the claim agent fails on CLM-1001 once; the report says it's back in the queue; the queue agent works it again, which doesn't count against a cap of 2, and it ends `decided` with 1 failure
- [ ] A claim that fails twice is left for a handler: it ends `left_for_handler`, the report says so, and the missed-claim check doesn't ask for it again
- [ ] The check for missed claims: a queue agent that tries to finish with a waiting claim still within its cap is sent back with a message naming that claim, works it, then finishes; one that has reached its cap finishes at once
- [ ] The reports: tests check the line for an approval paid, an approval waiting for sign-off, a decline, a referral, a failure back in the queue, a claim left for a handler and a refusal
- [ ] One stub-model test runs both agents for real on CLM-1001: the claim's steps are recorded on the claim, the queue agent's conversation holds only the one-line report from it, and the run finishes with its summary
- [ ] A queue agent whose model always fails still finishes the run, with the error as its summary, and no claim is left `in_progress`
- [ ] Tests for the command: no arguments caps the run at the number of waiting claims, `--cap 5` passes 5, and a claim id runs that claim alone
- [ ] `backend/instructions/queue.md` names no claim, customer or policy id (the same test as phase 2)
- [ ] Live check, capped at five claims, a few cents at most: after `uv run -m backend.seed`, with `npm run dev:backend` running, `uv run -m agent --cap 5` prints five reports and a summary; `curl -s localhost:8000/api/runs/latest` shows the run finished with every item done or left for a handler; 20 claims are still `waiting`; then reset with `uv run -m backend.seed`
- [ ] `npm run check:python` passes with coverage at or above 80%

## Phase 4: The dashboard

Show the run and its to-do list on the queue page, what the agent read on the claim page, and both sets of instructions.

- `dashboard/src/api.ts`: the run, to-do and new status types and labels, and `failures` on a claim.
- `dashboard/src/RunPanel.tsx`: the agent's run on the queue page.
- `dashboard/src/QueuePage.tsx`, `dashboard/src/ClaimPage.tsx`, `dashboard/src/AgentPanels.tsx` and `dashboard/src/InstructionsPage.tsx`: the changes above.
- `dashboard/src/styles.css`: the Raspberry state and the to-do chips.
- Vitest tests with `fetch` stubbed.

Success criteria:

- [ ] Vitest tests pass for the run panel: no run yet; a running run with its cap and its to-do items, each with its state; a finished run with its summary; and it refreshes every 2 seconds
- [ ] Vitest tests pass for the queue's fifth tile and the "Left for a handler" chip
- [ ] Vitest tests pass for the claim page: "What the agent read" lists each `read` step's heading and the timeline labels them "Read"; a claim left for a handler says the agent failed on it twice
- [ ] Vitest tests pass for the instructions page showing both sets of instructions, read only
- [ ] `npm run check:ts` passes, with strict TypeScript, no `any` and coverage at or above 80%
- [ ] In the browser with the Playwright MCP server, with a run and a to-do list made through the API (as the queue agent would): the queue page shows the run panel with each item's state; marking an item done through the API shows it ticked off within a few seconds; finishing the run shows the summary
- [ ] In the browser, a claim released twice through the API shows "Left for a handler" in the queue and on its page, and a claim with `read` steps posted through the API shows them under "What the agent read"
- [ ] Screenshots of the queue page with a run, in light and dark mode, match the brand: no edge borders, no thin line across the top, and the to-do chips in the brand's colors
- [ ] The browser console shows no errors, and `uv run -m backend.seed` resets the database after these checks

## Phase 5: End-to-end tests, a live run and handover

Test the new screens end to end, watch a real capped run in the dashboard, and bring the docs up to date.

- `e2e/queue.spec.ts`, `e2e/claim.spec.ts` and `e2e/instructions.spec.ts` (if the instructions test grows): the run panel, a claim left for a handler, the sections read, and both sets of instructions, driven through the API in place of the model.
- `docs/TECH_STACK.md`: the two agents and their entry points, the middleware, and DeepSeek V4.1 Flash as the default model.
- `docs/STATUS.md`: what's built, how to run a capped run and a whole queue, and the live run's results.

Success criteria:

- [ ] `npm run e2e` passes, with the pilot's tests and new ones that: the queue page shows a run's to-do list and its summary once finished; a claim released twice shows "Left for a handler"; a claim's page lists the sections read; the instructions page shows both sets of instructions
- [ ] `npm run e2e` passes a second time in a row, and the dev database is unchanged afterwards
- [ ] Live run, capped at five claims: after `uv run -m backend.seed`, with `npm run dev` running, `uv run -m agent --cap 5` on the default model works five claims on its own. In the browser with the Playwright MCP server, the queue page shows the to-do list filling in and being ticked off, then the summary; each of the five claims' pages shows its steps and what the agent read; the other 20 claims stay `waiting`. The results go in `docs/STATUS.md`, then `uv run -m backend.seed` resets the database
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` describe the product as it now is
- [ ] `npm run check` passes in full: ruff, pyright, pytest at 80% or more, ESLint, Prettier, tsc, vitest at 80% or more, and Playwright
