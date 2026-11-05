"""Stub models and scripted replies, so that tests never call a real model."""

from threading import Event, Lock
from time import sleep
from typing import Any
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    prompts: list[list[BaseMessage]] = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.prompts.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


class BrokenModel(StubModel):
    """A chat model that always fails, as a real one can."""

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise RuntimeError("The model is unavailable.")


class FlakyModel(StubModel):
    """A chat model that fails on its first call, then gives scripted replies."""

    failed: bool = False

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if not self.failed:
            self.failed = True
            raise TimeoutError("The model timed out.")
        return super()._generate(messages, stop, run_manager, **kwargs)


class FailingModel(StubModel):
    """A chat model that gives its scripted replies, then always fails."""

    replies: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if len(self.prompts) >= self.replies:
            raise RuntimeError("The model is unavailable.")
        return super()._generate(messages, stop, run_manager, **kwargs)


class StoppingModel(StubModel):
    """A chat model that stops the process, as Ctrl-C or a kill would, once it
    has given `replies` scripted replies."""

    replies: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if len(self.prompts) >= self.replies:
            raise KeyboardInterrupt
        return super()._generate(messages, stop, run_manager, **kwargs)


class Gauge:
    """Counts the model calls running at once, and holds each one for a moment
    until `together` are running, so that a test can see the most at once."""

    def __init__(self, together):
        self.together = together
        self.active = 0
        self.peak = 0
        self.lock = Lock()
        self.full = Event()

    def enter(self):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active >= self.together:
                self.full.set()
        self.full.wait(1)
        sleep(0.01)

    def leave(self):
        with self.lock:
            self.active -= 1


class SwarmModel(StubModel):
    """A chat model for many claims at once. Each claim follows its own script of
    replies, and an exception in a script is raised in its turn. A claim with no
    script declines."""

    scripts: dict = {}
    gauge: Any = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        claim_id = str(messages[1].content).split()[-1].rstrip(".")
        self.prompts.append(messages)
        self.gauge.enter()
        script = self.scripts.setdefault(claim_id, declines(claim_id))
        reply = script.pop(0)
        self.gauge.leave()
        if isinstance(reply, BaseException):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=reply)])


def swarm_model(scripts=None, together=1):
    return SwarmModel(messages=iter([]), scripts=scripts or {}, gauge=Gauge(together))


def usage(prompt_tokens=1000, completion_tokens=100, cost=0.002):
    """The usage OpenRouter reports on a reply, as `ChatOpenAI` passes it on."""
    return {
        "token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": cost,
        }
    }


def call(name, **args):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": str(uuid4())}],
        response_metadata=usage(),
    )


def stub(*replies):
    return StubModel(messages=iter(replies))


def decision(claim_id, verdict, payout):
    return call(
        "record_decision",
        claim_id=claim_id,
        decision=verdict,
        payout=payout,
        reasons="The reasons.",
        policy_section="Section 3, Trip Delay",
    )


def approve(claim_id, payout):
    return [
        decision(claim_id, "approve", payout),
        AIMessage(content=f"Approved {claim_id}."),
    ]


def declines(claim_id):
    return [decision(claim_id, "decline", 0), AIMessage(content="Declined.")]
