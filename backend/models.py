"""The shapes of the records the API sends and receives."""

from typing import Literal

from pydantic import BaseModel, Field

Status = Literal[
    "waiting", "in_progress", "awaiting_sign_off", "decided", "left_for_handler"
]
StepKind = Literal["tool_call", "tool_result", "read", "reply", "error"]
RunKind = Literal["dispatch", "evaluation"]
RunEnd = Literal["finished", "stopped"]
RunStatus = Literal["running", "finished", "stopped"]
Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]
LineKind = Literal[
    "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
]

FEWEST_AGENTS = 1
MOST_AGENTS = 25


class Item(BaseModel):
    description: str
    category: str
    amount: float


class Document(BaseModel):
    name: str
    type: str
    content: str


class Decision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float
    reasons: str
    policy_section: str
    decided_at: str


class Payment(BaseModel):
    amount: float
    paid_at: str
    signed_off_by: str | None
    signed_off_at: str | None


class SignOff(BaseModel):
    handler_id: str
    signed_off_at: str


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    claim_type: str
    summary: str
    submitted_on: str
    amount_claimed: float
    status: Status
    decision: str | None
    payout: float | None


class Claim(BaseModel):
    claim_id: str
    policy_id: str
    customer_id: str
    claim_type: str
    submitted_on: str
    incident_date: str
    incident_location: str
    summary: str
    story: str
    items: list[Item]
    amount_claimed: float
    documents: list[Document]
    status: Status
    failures: int
    thread_id: str | None
    decision: Decision | None
    payment: Payment | None
    sign_off: SignOff | None


class Trip(BaseModel):
    destination: str
    departs_on: str
    returns_on: str
    trip_cost: float


class Policy(BaseModel):
    policy_id: str
    customer_id: str
    plan: str
    add_ons: list[str]
    purchased_on: str
    sold_through: str
    trip: Trip
    travelers: list[str]
    status: str


class PreviousClaim(BaseModel):
    claim_id: str
    filed_on: str
    incident_date: str
    claim_type: str
    summary: str
    outcome: str
    amount_paid: float


class Address(BaseModel):
    street: str
    city: str
    state: str
    zip: str


class Customer(BaseModel):
    customer_id: str
    name: str
    email: str
    phone: str
    date_of_birth: str
    address: Address
    customer_since: str
    previous_claims: list[PreviousClaim]


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str
    email: str
    can_approve_over_limit: bool


class Step(BaseModel):
    step_id: int
    kind: StepKind
    text: str
    created_at: str


class NewStep(BaseModel):
    kind: StepKind
    text: str


class NewModelCall(BaseModel):
    run_id: str | None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)
    seconds: float = Field(ge=0)


class NewWork(BaseModel):
    run_id: str | None
    started_at: str
    finished_at: str
    seconds: float = Field(ge=0)


class NewDecision(BaseModel):
    decision: Literal["approve", "decline", "refer"]
    payout: float = Field(ge=0)
    reasons: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)


class NewPayment(BaseModel):
    amount: float = Field(gt=0)


class NewSignOff(BaseModel):
    handler_id: str


class SectionHeading(BaseModel):
    number: int
    title: str


class Contents(BaseModel):
    title: str
    opening: str
    sections: list[SectionHeading]


class Run(BaseModel):
    run_id: str
    kind: RunKind
    agents_at_once: int
    claim_set: str | None
    tiny: bool | None
    model: str
    reasoning_effort: str
    status: RunStatus
    started_at: str
    finished_at: str | None


class RunClaim(BaseModel):
    claim_id: str
    summary: str
    status: Status
    decision: str | None
    payout: float | None


class RunDetails(Run):
    claims: list[RunClaim]


class NewDispatch(BaseModel):
    agents_at_once: int = Field(ge=FEWEST_AGENTS, le=MOST_AGENTS)
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)


class RunResult(BaseModel):
    status: RunEnd


class Line(BaseModel):
    description: str = Field(min_length=1)
    amount: float = Field(ge=0)
    quantity: int = Field(ge=1)
    kind: LineKind
    nights: int = Field(ge=0)
    hours: float = Field(ge=0)


class NewPayout(BaseModel):
    benefit: Benefit
    lines: list[Line]
    paid_by_others: float = Field(ge=0)


class PayoutStep(BaseModel):
    step: str
    amount: float


class Payout(BaseModel):
    calculation: list[PayoutStep]
    payout: float
    needs_sign_off: bool
