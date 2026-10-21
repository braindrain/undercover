"""The UnderCover Agent's claim agent, which works one claim."""

import os
from collections.abc import Iterator
from uuid import uuid4

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from agent import tools
from agent.middleware import StepRecorder, resilience

load_dotenv(override=True)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
CLAIM_CALL_LIMIT = 30


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or DeepSeek V4.1 Flash by default.

    The client doesn't retry, so that ModelRetryMiddleware is the one retry policy.
    """
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
        timeout=120,
        max_retries=0,
    )


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Work one claim from a clean start, recording its steps, and return the
    agent's final reply. A run that ends undecided releases the claim."""
    with tools.backend() as client:
        instructions = client.get("/api/instructions/claim").text

        def record(kind: str, text: str) -> None:
            client.post(
                f"/api/claims/{claim_id}/steps", json={"kind": kind, "text": text}
            )

        agent = create_agent(
            llm or model(),
            tools=tools.CLAIM_TOOLS,
            system_prompt=instructions,
            middleware=[StepRecorder(record), *resilience(CLAIM_CALL_LIMIT)],
        )
        task = {"role": "user", "content": f"Decide claim {claim_id}."}
        client.post(f"/api/claims/{claim_id}/start").raise_for_status()
        try:
            result = agent.invoke(
                {"messages": [task]},
                config={"configurable": {"thread_id": thread_id}},
            )
            return str(result["messages"][-1].text)
        except Exception as error:
            record("error", f"The agent stopped: {error}")
            raise
        finally:
            client.post(f"/api/claims/{claim_id}/release")


def waiting_claims() -> list[str]:
    """Return the ids of the claims waiting in the queue."""
    return [claim["claim_id"] for claim in tools.waiting_claims()]


def run_queue(
    claim_ids: list[str], llm: BaseChatModel | None = None
) -> Iterator[tuple[str, str]]:
    """Run the agent on each claim in turn, yielding each claim's final reply.

    A claim that fails is reported and the queue carries on with the next one.
    """
    for claim_id in claim_ids:
        try:
            yield claim_id, run(claim_id, str(uuid4()), llm)
        except Exception as error:
            yield claim_id, f"Failed: {error}"
