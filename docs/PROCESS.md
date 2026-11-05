# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts three servers: the backend, the agent's service and the dashboard. It needs `.env`, and the backend and the agent use Supabase through its `DATABASE_URL`. `npm run seed` reseeds Supabase, after which `npm run dev` is restarted; `uv run -m backend.seed` reseeds the local SQLite database.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests, on their own servers and SQLite database, with a stand-in for the agent's service

The checks need no `.env`: they never reach Supabase, Langfuse or OpenRouter.

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Split the work into phases, usually 5 or 6, each building on the last.
- Include a section on the code's structure: each module, its one job, and which modules it imports. Check it against `docs/CODE_STANDARDS.md` before you hand the plan over. A plan that names files without saying what each owns is not finished.
- Write agent instructions as a goal, what good work looks like and the rules, as `docs/CODE_STANDARDS.md` says, not as a script of steps.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Review the phase's diff as a reviewer would before a pull request, and fix what you find. Passing checks are not a review: they test behavior, not structure. Go through `docs/CODE_STANDARDS.md` and look in particular for:
   - a URL, path or business rule written in more than one place
   - a module that reaches into another, or two modules that import each other
   - two ways of doing the same thing
   - business logic in the harness, or tools spread across modules
   - instructions that script steps in place of a goal
   - a check silenced inline
4. Tick off its criteria in `docs/PLAN.md` and update `docs/STATUS.md`.
5. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, run `npm run check` and report the results.

## Rules

- Add tests with each change. Tests never call a model; use a stub model. They never reach the agent's service either; the backend's tests replace its client.
- For a live run with the real model, reseed first and keep to the runs the brief asks for. A dispatch works the whole queue, and an evaluation a whole set or its five `tiny` claims.
- Where a rule of structure can be checked, check it in a test, as the tests that only `api` modules call the backend do.
- Outside a plan, commit each change once you have checked it.
- Run `npm run check` before you hand work over.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run, and that includes `noqa` and the like on one line. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to. Seeding reads its three named files into tables of their own, and `backend/evaluations.py` scores from them.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files.
