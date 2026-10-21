# Status

The claims pilot from `docs/PLAN.md` is built: all six phases.

The agent's harness from `docs/PLAN_HARNESS.md` is built: all five phases. After pulling this build, reset the database with `uv run -m backend.seed`, since the tables have changed.

## What's built

- `backend/`: FastAPI on SQLite. The database is created from `data/` on first use, at `DATABASE_PATH` or by default `backend/undercover.db`, and never reads `data/evals/`. It serves the claims queue, each claim, policies, customers, a customer's claim history, the handlers, the policy wording and the claims manual (in full, or as a list of numbered sections and one section at a time), the agent's instructions for running the queue and for working a claim (`backend/instructions/`), and runs of the agent with their to-do lists. Claims move from `waiting` to `in_progress` to `decided`, or to `awaiting_sign_off` for an approval over $1,000.00. A release counts a failure: the first returns the claim to `waiting`, the second leaves it `left_for_handler`. The backend enforces the rules: a payment must match an approval, is made once, and over the limit is refused until a handler signs off, which pays it.
- `agent/`: the UnderCover Agent on `deepseek/deepseek-v4.1-flash` with low reasoning effort, or `MODEL_NAME`. The claim agent, `run(claim_id, thread_id)` in `agent/main.py`, takes the claim instructions from the backend as its system prompt and works the claim with seven tools over the API (`agent/tools.py`): `get_claim`, `get_policy`, `get_claim_history`, `list_sections`, `read_section`, `record_decision` and `pay_claim`. It looks up the sections of the wording and the manual it needs rather than being handed them. Its middleware (`agent/middleware.py`) records each reply, tool call and result as a step, with each section read as a `read` step; retries a failed model call three times and a tool call that can't reach the backend twice; and stops a claim at 30 model calls. A run that ends without a decision releases the claim, which counts a failure.
  - The queue agent, `run_queue(cap, thread_id)` in `agent/queue.py`, starts a run in the backend and works the queue with three tools: `list_waiting_claims`, `write_todos`, which stores its to-do list in the backend, and `work_claim`, which hands one claim to a fresh claim agent and returns a one-line report built from the claim's record, such as "CLM-1001: approved, $420.00, paid.". `work_claim` enforces the cap, counting each claim once however often it's retried, and turns a claim agent's failure into its report so the run carries on. A middleware sends the agent back to work if it tries to finish while a claim it can still work is waiting. The run finishes with the agent's summary, or with the error if the queue agent itself fails.
- `dashboard/`: the claims desk in the UnderCover brand, light and dark.
  - The queue (`#/`), with counts in each of the five states, refreshing every 5 seconds, and the agent's latest run above the table: its state, cap, start and finish, its to-do list with each item's state, and its summary, refreshing every 2 seconds.
  - Each claim (`#/claims/CLM-1001`): the story, items, documents, policy and customer, the agent's decision and payment, what the agent read, and its steps as they happen, with each section read labeled "Read". A claim left for a handler says the agent failed on it twice. "Run the agent" on a waiting claim, and "Sign off and pay" on a claim waiting for sign-off.
  - The agent's instructions for running the queue and for working a claim, with the policy wording and the claims manual below, read only (`#/instructions`).
- `e2e/`: Playwright tests of the queue, the agent's run with its to-do list and summary, a claim left for a handler, both sets of instructions, a claim in full, live steps, the sections read, long steps wrapping inside the page, a payment, and the sign-off of a payout over the limit. They run on their own servers (ports 8001 and 5174) with their own database, `e2e/undercover-e2e.db`, reseeded on each run.

## Running it

- `npm run dev` starts the backend on port 8000 and the dashboard at http://localhost:5173.
- "Run the agent" on a claim, or `uv run -m agent CLM-1001` with the backend running, runs the agent on one claim. Each run calls the model, a few cents with the default model.
- `uv run -m agent --cap 5`, with the backend running, runs the queue agent on at most five claims and leaves the rest waiting. It prints each claim's report as it comes, then the summary. `uv run -m agent` with no cap works every waiting claim, which is 25 claim runs on a fresh database.
- `uv run -m backend.seed` resets the database to the data in `data/`.
- `npm run check` runs every check.

## Notes

- A live run on CLM-1001 on DeepSeek V4.1 Flash read 14 sections of the wording and the manual, then approved $420.00 under Section 3, Trip Delay, and paid it, in about 15 seconds.
- Two live runs capped at five claims, in 3 minutes 26 seconds and 2 minutes 38 seconds, each worked the five oldest claims with the same results: CLM-1001 approved and paid $420.00, CLM-1008 approved at $2,240.00 and CLM-1011 at $1,150.00 waiting for sign-off, CLM-1018 referred and CLM-1010 declined. Each claim read 13 to 15 sections. Every to-do item ended done, the dashboard showed the list filling in and being ticked off, and the other 20 claims stayed waiting.
- The claim agent often asks for several tools at once, so a claim's steps can list a few tool calls and then their results in the order they finish.
- The agent isn't scored against the handlers' decisions in `data/evals/` yet; that comes with evaluations in a later build.
- A process killed mid-claim leaves that claim in progress until the database is reset; durable state comes in a later build.
