import pytest

from backend import scoring
from backend.scoring import Answer, agent_answer, is_accurate, mistake_cost


@pytest.mark.parametrize(
    ("agent", "handler", "cost"),
    [
        (Answer("approve", 500.0), Answer("approve", 500.0), 0.0),
        (Answer("approve", 900.0), Answer("approve", 1000.0), 100.0),
        (Answer("approve", 1100.0), Answer("approve", 1000.0), 100.0),
        (Answer("decline", 0.0), Answer("approve", 800.0), 1500.0),
        (Answer("decline", 0.0), Answer("refer", 0.0), 1500.0),
        (Answer("approve", 300.0), Answer("decline", 0.0), 300.0),
        (Answer("approve", 300.0), Answer("refer", 0.0), 300.0),
        (Answer("refer", 0.0), Answer("approve", 800.0), 50.0),
        (Answer("refer", 0.0), Answer("decline", 0.0), 50.0),
        (Answer("decline", 0.0), Answer("decline", 0.0), 0.0),
        (Answer("refer", 0.0), Answer("refer", 0.0), 0.0),
    ],
)
def test_each_mistake_on_its_own(agent, handler, cost):
    assert mistake_cost(agent, handler) == cost


def test_a_payout_a_dollar_away_is_accurate():
    assert is_accurate(Answer("approve", 501.0), Answer("approve", 500.0))
    assert is_accurate(Answer("approve", 100.1), Answer("approve", 99.1))


def test_a_payout_more_than_a_dollar_away_isnt():
    assert not is_accurate(Answer("approve", 501.01), Answer("approve", 500.0))


def test_the_right_payout_on_the_wrong_decision_isnt_accurate():
    assert not is_accurate(Answer("decline", 0.0), Answer("refer", 0.0))


def test_no_decision_counts_as_a_referral():
    assert agent_answer(None, None) == Answer("refer", 0.0)
    assert agent_answer("approve", 420.0) == Answer("approve", 420.0)


def test_the_six_claims_worked_out_by_hand():
    answers = [
        (agent_answer("approve", 500.5), Answer("approve", 500.0)),
        (agent_answer("approve", 900.0), Answer("approve", 1000.0)),
        (agent_answer("decline", 0.0), Answer("approve", 800.0)),
        (agent_answer("approve", 300.0), Answer("decline", 0.0)),
        (agent_answer(None, None), Answer("decline", 0.0)),
        (agent_answer("refer", 0.0), Answer("refer", 0.0)),
    ]
    result = scoring.measures(answers, 0.6, 120.0, 45.0)
    assert result.decision_accuracy == 0.5
    assert result.accuracy == pytest.approx(2 / 6)
    assert result.cost_of_mistakes == 1950.5
    assert result.straight_through_rate == pytest.approx(4 / 6)
    assert result.cost_per_claim == pytest.approx(0.1)
    assert result.seconds_per_claim == 20
    assert result.run_seconds == 45
