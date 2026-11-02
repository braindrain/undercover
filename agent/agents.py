"""The UnderCover Agent: a queue agent that runs the queue, and a claim agent that
works one claim from a clean start."""

from collections.abc import Iterator
from functools import partial

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel

from agent import api, harness
from agent.tools import CLAIM_TOOLS, QUEUE_TOOLS, RunContext

CLAIM_CALL_LIMIT = 30


def queue_call_limit(cap: int) -> int:
    """Return the queue agent's limit of model calls for a run with this cap."""
    return 10 + 5 * cap


def work_claim(claim_id: str, llm: BaseChatModel | None = None) -> str:
    """Work one claim with a fresh claim agent, recording its steps on the claim,
    and return its final reply. A claim left undecided is released, which counts
    a failure on it."""
    agent = create_agent(
        llm or harness.model(),
        tools=CLAIM_TOOLS,
        system_prompt=api.get_instructions("claim"),
        middleware=[
            harness.StepRecorder(partial(api.record_step, claim_id)),
            *harness.middleware(CLAIM_CALL_LIMIT),
        ],
    )
    thread_id = api.start_claim(claim_id)["thread_id"]
    try:
        task = {"role": "user", "content": f"Decide claim {claim_id}."}
        result = agent.invoke({"messages": [task]}, harness.tracing(thread_id))
        return str(result["messages"][-1].text)
    except Exception as error:
        api.record_step(claim_id, "error", f"The agent stopped: {error}")
        raise
    finally:
        api.release_claim(claim_id)


def missed_claims(run: RunContext) -> str | None:
    """Remind the queue agent of the waiting claims it can still work, if any:
    those it tried that are back in the queue, and new ones within its cap."""
    waiting = [claim["claim_id"] for claim in api.waiting_claims()]
    tried = [claim_id for claim_id in waiting if claim_id in run.worked]
    untried = [claim_id for claim_id in waiting if claim_id not in run.worked]
    missed = tried + untried[: run.cap - len(run.worked)]
    if not missed:
        return None
    verb = "is" if len(missed) == 1 else "are"
    return (
        f"Before you finish: {', '.join(missed)} {verb} still waiting and within "
        "this run's cap. Work them and keep your to-do list up to date, then "
        "finish with your summary."
    )


def run_queue(cap: int, llm: BaseChatModel | None = None) -> Iterator[str]:
    """Run the queue agent over at most `cap` claims, yielding each claim's report
    as it comes and then the summary, which also finishes the run."""
    llm = llm or harness.model()
    run = RunContext(api.start_run(cap), cap, partial(work_claim, llm=llm))
    agent = create_agent(
        llm,
        tools=QUEUE_TOOLS,
        system_prompt=api.get_instructions("queue"),
        context_schema=RunContext,
        middleware=[
            harness.FinishCheck(missed_claims),
            *harness.middleware(queue_call_limit(cap)),
        ],
    )
    task = {"role": "user", "content": f"Work the queue, capped at {cap} claims."}
    updates = agent.stream({"messages": [task]}, context=run, stream_mode="updates")
    summary = ""
    try:
        for update in updates:
            for message in update.get("tools", {}).get("messages", []):
                if message.name == "work_claim":
                    yield str(message.text)
            for message in update.get("model", {}).get("messages", []):
                summary = str(message.text)
    except Exception as error:
        summary = f"The run stopped: {error}"
    summary = summary or "The run finished without a summary."
    api.finish_run(run.run_id, summary)
    yield summary
