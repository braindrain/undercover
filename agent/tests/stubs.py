"""Stub models and scripted replies, so that tests never call a real model."""

from uuid import uuid4

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage


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


class StoppingModel(StubModel):
    """A chat model that stops the process, as Ctrl-C or a kill would, once it
    has given `replies` scripted replies."""

    replies: int = 0

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if len(self.prompts) >= self.replies:
            raise KeyboardInterrupt
        return super()._generate(messages, stop, run_manager, **kwargs)


def call(name, **args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": str(uuid4())}]
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
