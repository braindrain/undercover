# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount.
- `agent/`: two agents built with LangChain's `create_agent` on OpenRouter, with no other agent framework. The queue agent, `run_queue(cap, thread_id)`, runs the queue and hands each claim to the claim agent, `run(claim_id, thread_id)`, which works one claim from a clean start. Their harness is their tools, their instructions from the backend, and middleware: LangChain's `ModelRetryMiddleware`, `ToolRetryMiddleware` and `ModelCallLimitMiddleware`, plus our own step recorder and check for missed claims. Both reach claims, documents and instructions only through the backend's API. They use `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
