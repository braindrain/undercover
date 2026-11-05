# Code standards

1. Keep it simple, do not overcomplicate. Simple means easy to read and change, not the fewest files: a module with one clear job beats the same code repeated.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Never silence a check inline (`noqa`, `type: ignore`, `eslint-disable`, `@ts-ignore`). Fix the code.

## Structure

8. Each module has one job, and imports point one way. Two modules never import each other, and no module reaches into another's internals.
9. Every call to a service goes through one client module, which holds the base URL and every path: `agent/api.py` for the agent, `backend/agent_client.py` for the backend's calls to the agent's service, and `dashboard/src/api.ts` for the dashboard. Nothing else builds a URL or makes a request. A test checks this.
10. One way to do each thing within a program: one way to call the backend, one way to look up a record, one way to report a refusal.
11. Business rules live in the backend, once. The agent and the dashboard never repeat them, such as the approval limit.
12. Use the framework's own way before writing your own, such as FastAPI's dependencies, and LangGraph's retry policies and runtime context.

## Agents

13. Keep the harness, the tools and the business logic apart. `agent/harness.py` is everything around the model, and knows nothing about claims. `agent/tools.py` holds every tool. `agent/agents.py` builds and runs the claim agent. `agent/dispatcher.py` works a run's claims with no model, and reaches the claim agent only through `agents.work`.
14. State that a graph's nodes share during a run goes in LangGraph's runtime context (`StateGraph(..., context_schema=...)`, with nodes taking a `runtime: Runtime[...]` argument), not in closures or globals. Only what must survive a restart goes in the graph's state.
15. Instructions give an agent its goal, what good work looks like and the rules it must keep, not a script of steps. A tool's docstring says how to use the tool. A step that must happen in a fixed order is code, and a rule that must never be broken is enforced in code (the backend or the graph) as well as written down.
