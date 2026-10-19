# Status

The claims pilot from `docs/PLAN.md` is built: all six phases.

## What's built

- `backend/`: FastAPI on SQLite. The database is created from `data/` on first use, at `DATABASE_PATH` or by default `backend/undercover.db`, and never reads `data/evals/`. It serves the claims queue, each claim, policies, customers, a customer's claim history, the policy wording, the handlers and the agent's instructions (`backend/instructions.md`). Claims move from `waiting` to `in_progress` to `decided`, or to `awaiting_sign_off` for an approval over $1,000.00. The backend enforces the rules: a payment must match an approval, is made once, and over the limit is refused until a handler signs off, which pays it.
- `agent/`: the UnderCover Agent on `openai/gpt-6-luna` with low reasoning effort, or `MODEL_NAME`. `run(claim_id, thread_id)` takes its instructions and the policy wording from the backend as its system prompt, works the claim with five tools over the API (`get_claim`, `get_policy`, `get_claim_history`, `record_decision`, `pay_claim`) and records each step. A run that ends without a decision returns the claim to waiting.
- `dashboard/`: the claims desk in the UnderCover brand, light and dark.
  - The queue (`#/`), with counts by state, refreshing every 5 seconds.
  - Each claim (`#/claims/CLM-1001`): the story, items, documents, policy and customer, the agent's decision and payment, and its steps as they happen. "Run the agent" on a waiting claim, and "Sign off and pay" on a claim waiting for sign-off.
  - The agent's instructions and the policy wording, read only (`#/instructions`).
- `e2e/`: Playwright tests of the queue, the instructions, a claim in full, live steps, a payment, and the sign-off of a payout over the limit. They run on their own servers (ports 8001 and 5174) with their own database, `e2e/undercover-e2e.db`, reseeded on each run.

## Running it

- `npm run dev` starts the backend on port 8000 and the dashboard at http://localhost:5173.
- "Run the agent" on a claim, or `uv run -m agent CLM-1001` with the backend running, runs the agent on one claim. Each run calls the model, a few cents with the default model.
- `uv run -m agent`, with the backend running, works every waiting claim in the queue in turn and prints each result as it finishes. A claim that fails is reported and returned to waiting, and the queue carries on. The full queue is 25 model runs.
- `uv run -m backend.seed` resets the database to the data in `data/`.
- `npm run check` runs every check.

## Notes

- A live run on CLM-1001 approved $420.00 under Section 3, Trip Delay, and paid it, in about 10 seconds.
- The pilot doesn't score the agent against the handlers' decisions in `data/evals/`; that comes with evaluations in a later build.
