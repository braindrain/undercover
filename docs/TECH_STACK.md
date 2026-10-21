# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount.
- `agent/`: two agents built with LangChain's `create_agent` on OpenRouter, with no other agent framework. The queue agent, `run_queue(cap)`, runs the queue and hands each claim to the claim agent, `work_claim(claim_id)`, which works one claim from a clean start. The code is in four parts: `api.py`, the only client of the backend; `harness.py`, the model and the middleware, with LangChain's `ModelRetryMiddleware`, `ToolRetryMiddleware` and `ModelCallLimitMiddleware`, plus our own step recorder, refusals passed to the model and check before finishing; `tools.py`, every tool, with the queue agent's state in LangChain's runtime context (`context_schema` and `ToolRuntime`); and `agents.py`, which builds and runs the two agents. Both reach claims, documents and instructions only through the backend's API. They use `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
