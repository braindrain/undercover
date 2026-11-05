"""The measures from our team's labs, as pure functions: how the agent's answers
compare with the handlers', and what the mistakes cost."""

from typing import NamedTuple

from backend.models import Measures

WRONGFUL_DECLINE = 1500.0
NEEDLESS_REFERRAL = 50.0
PAYOUT_TOLERANCE = 1.0


class Answer(NamedTuple):
    decision: str
    payout: float


def agent_answer(decision: str | None, payout: float | None) -> Answer:
    """Return the agent's answer. A claim it didn't decide counts as a referral."""
    return Answer(decision or "refer", payout or 0.0)


def difference(agent: Answer, handler: Answer) -> float:
    """Return how far apart two payouts are, to the cent, so that a float can't push
    a difference of exactly a dollar over the line."""
    return round(abs(agent.payout - handler.payout), 2)


def is_accurate(agent: Answer, handler: Answer) -> bool:
    """Return whether the decision matches and the payout is within a dollar."""
    return (
        agent.decision == handler.decision
        and difference(agent, handler) <= PAYOUT_TOLERANCE
    )


def mistake_cost(agent: Answer, handler: Answer) -> float:
    """Return what the agent's answer costs UnderCover against the handler's."""
    if agent.decision == handler.decision:
        return difference(agent, handler) if agent.decision == "approve" else 0.0
    if agent.decision == "decline":
        return WRONGFUL_DECLINE
    if agent.decision == "approve":
        return agent.payout
    return NEEDLESS_REFERRAL


def measures(
    answers: list[tuple[Answer, Answer]],
    model_cost: float,
    work_seconds: float,
    run_seconds: float,
) -> Measures:
    """Return the measures for a run, from each claim's agent and handler answers
    and the run's totals."""
    claims = len(answers)
    right = [agent.decision == handler.decision for agent, handler in answers]
    return Measures(
        decision_accuracy=sum(right) / claims,
        accuracy=sum(is_accurate(*pair) for pair in answers) / claims,
        cost_of_mistakes=round(sum(mistake_cost(*pair) for pair in answers), 2),
        straight_through_rate=sum(a.decision != "refer" for a, _ in answers) / claims,
        cost_per_claim=model_cost / claims,
        seconds_per_claim=work_seconds / claims,
        run_seconds=run_seconds,
    )
