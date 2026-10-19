# Status

The claims pilot, built in phases from `docs/PLAN.md`.

## Built so far

- `backend/`: FastAPI on SQLite. The database is created from `data/` on first use, at `DATABASE_PATH` or by default `backend/undercover.db`. Read operations for the claims queue, a claim, a policy, a customer, a customer's claim history, the policy wording, the agent's instructions (`backend/instructions.md`) and the handlers. Operations that move a claim from `waiting` to `in_progress` to `decided`, or to `awaiting_sign_off` for an approval over $1,000.00, with the steps, the decision, the payment and the sign-off. The backend refuses a payment over the limit until a handler signs off, and signing off pays it.
- `agent/`: the UnderCover Agent. `run(claim_id, thread_id)` takes its instructions and the policy wording from the backend, works the claim with five tools over the API (`get_claim`, `get_policy`, `get_claim_history`, `record_decision`, `pay_claim`) and records each step. A run that ends without a decision returns the claim to waiting. `run_agent` in the backend starts it in the background.
- `dashboard/`: the claims desk in the UnderCover brand, light and dark. The queue (`#/`), with counts by state, refreshing every 5 seconds, and the agent's instructions with the policy wording, read only (`#/instructions`).
- `e2e/`: tests of the queue and the instructions page

## Running it

- `npm run dev` starts the backend on port 8000 and the dashboard on port 5173.
- `uv run -m backend.seed` resets the database to the data in `data/`.
- `uv run -m agent CLM-1001` runs the agent on one claim, with the backend running.
- `npm run check` runs every check.
