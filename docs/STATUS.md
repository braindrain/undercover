# Status

The claims pilot, built in phases from `docs/PLAN.md`.

## Built so far

- `backend/`: FastAPI on SQLite. The database is created from `data/` on first use, at `DATABASE_PATH` or by default `backend/undercover.db`. Read operations for the claims queue, a claim, a policy, a customer, a customer's claim history, the policy wording, the agent's instructions (`backend/instructions.md`) and the handlers.
- `agent/`: the skeleton's entry point `run(claim_id, thread_id)`, with one tool that calls the health route
- `dashboard/`: the skeleton's page that shows whether the backend is up
- `e2e/`: one test of that page

## Running it

- `npm run dev` starts the backend on port 8000 and the dashboard on port 5173.
- `uv run -m backend.seed` resets the database to the data in `data/`.
- `npm run check` runs every check.
