import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import db, payout
from backend.main import app

client = TestClient(app)


def line(description, amount, kind="item", **counts):
    return {
        "description": description,
        "amount": amount,
        "kind": kind,
        "quantity": 1,
        "nights": 0,
        "hours": 0,
        **counts,
    }


def work_out(policy_id, benefit, lines, paid_by_others=0):
    return client.post(
        f"/api/policies/{policy_id}/payout",
        json={"benefit": benefit, "lines": lines, "paid_by_others": paid_by_others},
    )


def test_the_labs_example():
    response = work_out(
        "POL-2007",
        "baggage",
        [
            line("Hard-shell suitcase", 239.99),
            line("Three T-shirts", 74.97, quantity=3),
        ],
        paid_by_others=100,
    )
    assert response.json() == {
        "calculation": [
            {"step": "Hard-shell suitcase, limited to $150.00", "amount": 150.0},
            {"step": "Three T-shirts", "amount": 74.97},
            {"step": "Less what others have paid or refunded", "amount": -100.0},
            {"step": "Less the deductible", "amount": -100.0},
        ],
        "payout": 24.97,
        "needs_sign_off": False,
    }


def test_hotel_and_meals_on_a_trip_delay_are_limited_per_room_and_traveler():
    result = work_out(
        "POL-2015",
        "trip_delay",
        [
            line("Hotel", 2500.0, kind="hotel", nights=1),
            line("Meals", 1000.0, kind="meals_without_receipts", hours=30),
        ],
    ).json()
    assert result["calculation"][:2] == [
        {"step": "Hotel, limited to $1,050.00", "amount": 1050.0},
        {"step": "Meals, limited to $420.00", "amount": 420.0},
    ]


def test_cash_is_limited_in_all():
    result = work_out(
        "POL-2001",
        "baggage",
        [line("Wallet cash", 80.0, kind="cash"), line("Coat cash", 70.0, kind="cash")],
    ).json()
    assert result["calculation"][0] == {
        "step": "Cash, up to $100 in all",
        "amount": 100,
    }


def test_a_total_over_the_maximum_is_cut_to_it():
    result = work_out("POL-2001", "trip_delay", [line("Taxi", 900.0, "expense")]).json()
    assert result["calculation"][-1] == {
        "step": "Less the amount over the maximum of $800",
        "amount": -100.0,
    }
    assert result["payout"] == 800.0


@pytest.mark.parametrize(
    ("amount", "needs_sign_off"), [(1000.0, False), (1000.01, True)]
)
def test_sign_off_above_the_approval_limit(amount, needs_sign_off):
    result = work_out(
        "POL-2001", "trip_cancellation", [line("Fare", amount, "expense")]
    )
    assert result.json()["payout"] == amount
    assert result.json()["needs_sign_off"] is needs_sign_off


def test_an_unknown_policy():
    assert work_out("POL-9999", "baggage", [line("Bag", 10.0)]).status_code == 404


def test_every_line_field_is_required():
    incomplete = {"description": "Bag", "amount": 10.0, "kind": "item"}
    assert work_out("POL-2001", "baggage", [incomplete]).status_code == 422


def test_the_approval_limit_comes_from_the_company_settings():
    company = json.loads(db.read_data("company.json"))
    assert company["payout_approval_limit"] == payout.APPROVAL_LIMIT
    for source in Path(payout.__file__).parent.glob("*.py"):
        if source.name != "payout.py":
            text = source.read_text(encoding="utf-8")
            assert "payout_approval_limit" not in text, source
            assert "APPROVAL_LIMIT =" not in text, source


def test_electronics_plus_raises_the_per_item_limit():
    lines = [line("Laptop", 2000.0, "electronics")]
    with_add_on = work_out("POL-2009", "baggage", lines).json()
    without = work_out("POL-2001", "baggage", lines).json()
    assert with_add_on["calculation"][0]["amount"] == 1200.0
    assert without["calculation"][0]["amount"] == 300.0


def test_nothing_is_left_when_others_paid_more():
    result = work_out("POL-2001", "trip_delay", [line("Taxi", 50.0, "expense")], 80)
    assert result.json()["calculation"][-1] == {
        "step": "Nothing is left to pay",
        "amount": 30.0,
    }
    assert result.json()["payout"] == 0
