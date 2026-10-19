"""The UnderCover Agent."""

import os
from collections.abc import Iterator
from typing import Literal

import httpx
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

load_dotenv(override=True)

BACKEND_URL = "http://localhost:8000"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"


def backend() -> httpx.Client:
    """Return a client for the UnderCover backend."""
    return httpx.Client(base_url=BACKEND_URL, timeout=30)


def reply(response: httpx.Response) -> str:
    """Return the backend's reply, or its reason for refusing."""
    if response.is_success:
        return response.text
    return f"Refused: {response.json()['detail']}"


@tool
def get_claim(claim_id: str) -> str:
    """Look up a claim: the story, the items claimed and the documents."""
    with backend() as client:
        return reply(client.get(f"/api/claims/{claim_id}"))


@tool
def get_policy(policy_id: str) -> str:
    """Look up a policy: the plan, the add-ons, the trip and the travelers."""
    with backend() as client:
        return reply(client.get(f"/api/policies/{policy_id}"))


@tool
def get_claim_history(customer_id: str) -> str:
    """Look up a customer's earlier claims."""
    with backend() as client:
        return reply(client.get(f"/api/customers/{customer_id}/claims"))


@tool
def record_decision(
    claim_id: str,
    decision: Literal["approve", "decline", "refer"],
    payout: float,
    reasons: str,
    policy_section: str,
) -> str:
    """Record the decision on a claim, with the payout (0 unless approved), the
    reasons in two or three sentences and the relevant section of the policy."""
    with backend() as client:
        return reply(
            client.post(
                f"/api/claims/{claim_id}/decision",
                json={
                    "decision": decision,
                    "payout": payout,
                    "reasons": reasons,
                    "policy_section": policy_section,
                },
            )
        )


@tool
def pay_claim(claim_id: str, amount: float) -> str:
    """Pay an approved claim its payout."""
    with backend() as client:
        return reply(
            client.post(f"/api/claims/{claim_id}/payments", json={"amount": amount})
        )


TOOLS = [get_claim, get_policy, get_claim_history, record_decision, pay_claim]


def model() -> ChatOpenAI:
    """Return the model named in MODEL_NAME, or GPT-6 Luna by default."""
    name = os.getenv("MODEL_NAME")
    return ChatOpenAI(
        model=name or DEFAULT_MODEL,
        base_url=OPENROUTER_BASE_URL,
        api_key=SecretStr(os.environ["OPENROUTER_API_KEY"]),
        reasoning_effort=None if name else "low",
    )


def steps(message: BaseMessage) -> list[tuple[str, str]]:
    """Describe one message from the agent as steps for the dashboard."""
    text = str(message.text)
    if isinstance(message, ToolMessage):
        return [("tool_result", text)]
    described = [("reply", text)] if text else []
    if isinstance(message, AIMessage):
        for call in message.tool_calls:
            args = ", ".join(
                f"{name}={value!r}" for name, value in call["args"].items()
            )
            described.append(("tool_call", f"{call['name']}({args})"))
    return described


def work(
    llm: BaseChatModel, system_prompt: str, claim_id: str, thread_id: str
) -> Iterator[BaseMessage]:
    """Run the agent on a claim, yielding each new message as it works."""
    agent = create_agent(llm, tools=TOOLS, system_prompt=system_prompt)
    for update in agent.stream(
        {"messages": [{"role": "user", "content": f"Decide claim {claim_id}."}]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="updates",
    ):
        for node in update.values():
            yield from node["messages"]


def run(claim_id: str, thread_id: str, llm: BaseChatModel | None = None) -> str:
    """Run the agent on one claim, recording its steps, and return its final reply."""
    with backend() as client:
        instructions = client.get("/api/instructions").text
        wording = client.get("/api/policy-wording").text
        client.post(f"/api/claims/{claim_id}/start").raise_for_status()

        def record(kind: str, text: str) -> None:
            client.post(
                f"/api/claims/{claim_id}/steps", json={"kind": kind, "text": text}
            )

        final = ""
        try:
            for message in work(
                llm or model(), f"{instructions}\n\n{wording}", claim_id, thread_id
            ):
                for kind, text in steps(message):
                    record(kind, text)
                final = str(message.text)
        except Exception as error:
            record("error", f"The agent stopped: {error}")
            raise
        finally:
            client.post(f"/api/claims/{claim_id}/release")
        return final
