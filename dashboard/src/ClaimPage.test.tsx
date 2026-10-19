import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Claim, Customer, Handler, Policy, Step } from "./api";
import { ClaimPage } from "./ClaimPage";
import { stubFetch, type Routes } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const decision = {
  decision: "approve" as const,
  payout: 420,
  reasons: "The delay is covered.",
  policy_section: "Section 3, Trip Delay",
  decided_at: "2026-10-19T17:49:51+00:00",
};

function claim(changes: Partial<Claim>): Claim {
  return {
    claim_id: "CLM-1001",
    policy_id: "POL-2001",
    customer_id: "CUS-3001",
    claim_type: "trip_delay",
    submitted_on: "2026-06-24",
    incident_date: "2026-06-18",
    incident_location: "Rome, Italy",
    summary: "Flight home canceled",
    story: "My flight home from Rome was canceled.",
    items: [
      { description: "Airport hotel", category: "accommodation", amount: 245 },
      { description: "Dinner", category: "meals", amount: 175 },
    ],
    amount_claimed: 420,
    documents: [
      { name: "Hotel receipt", type: "receipt", content: "Total: $245.00." },
    ],
    status: "waiting",
    decision: null,
    payment: null,
    sign_off: null,
    ...changes,
  };
}

const policy: Policy = {
  policy_id: "POL-2001",
  plan: "premium",
  add_ons: ["electronics_plus"],
  purchased_on: "2026-04-22",
  trip: {
    destination: "Rome, Italy",
    departs_on: "2026-06-10",
    returns_on: "2026-06-18",
    trip_cost: 3100,
  },
  travelers: ["Maya Patel"],
};

const customer: Customer = {
  customer_id: "CUS-3001",
  name: "Maya Patel",
  email: "maya.patel@example.com",
  customer_since: "2023-04-11",
  previous_claims: [
    {
      claim_id: "CLM-0412",
      filed_on: "2024-08-19",
      claim_type: "baggage_delay",
      summary: "Checked bag arrived 30 hours late",
      outcome: "approved",
      amount_paid: 140,
    },
  ],
};

const handlers: Handler[] = [
  {
    handler_id: "HND-01",
    name: "Dana Whitfield",
    role: "Senior Claims Handler",
    can_approve_over_limit: true,
  },
  {
    handler_id: "HND-02",
    name: "Marcus Oyelaran",
    role: "Claims Handler",
    can_approve_over_limit: true,
  },
];

const steps: Step[] = [
  {
    step_id: 1,
    kind: "tool_call",
    text: "get_claim(claim_id='CLM-1001')",
    created_at: "2026-10-19T17:49:40+00:00",
  },
  {
    step_id: 2,
    kind: "tool_result",
    text: '{"claim_id":"CLM-1001"}',
    created_at: "2026-10-19T17:49:41+00:00",
  },
  {
    step_id: 3,
    kind: "reply",
    text: "Approved and paid $420.00.",
    created_at: "2026-10-19T17:49:52+00:00",
  },
];

function routes(current: Claim, claimSteps: Step[] = []): Routes {
  return {
    "/api/claims/CLM-1001": current,
    "/api/claims/CLM-1001/steps": claimSteps,
    "/api/policies/POL-2001": policy,
    "/api/customers/CUS-3001": customer,
    "/api/handlers": handlers,
  };
}

function posts(fetchMock: ReturnType<typeof stubFetch>): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => init?.method === "POST")
    .map(([path]) => path);
}

it("shows a waiting claim in full", async () => {
  stubFetch(routes(claim({})));
  render(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText("Flight home canceled")).toBeDefined();
  expect(
    screen.getByText("My flight home from Rome was canceled."),
  ).toBeDefined();
  expect(screen.getByText("Airport hotel")).toBeDefined();
  expect(screen.getByText("$420.00")).toBeDefined();
  expect(screen.getByText("Total: $245.00.")).toBeDefined();
  const policyPanel = await screen.findByRole("region", { name: "Policy" });
  expect(within(policyPanel).getByText("Premium")).toBeDefined();
  expect(within(policyPanel).getByText("Electronics plus")).toBeDefined();
  const customerPanel = await screen.findByRole("region", { name: "Customer" });
  expect(within(customerPanel).getByText("CLM-0412")).toBeDefined();
  expect(screen.getByText("No decision yet.")).toBeDefined();
  expect(
    screen.getByText("The agent hasn't worked on this claim yet."),
  ).toBeDefined();
});

it("starts the agent on a waiting claim", async () => {
  const fetchMock = stubFetch({
    ...routes(claim({})),
    "/api/claims/CLM-1001/run": {},
  });
  render(<ClaimPage claimId="CLM-1001" />);
  fireEvent.click(await screen.findByRole("button", { name: "Run the agent" }));
  expect(await screen.findByText("Starting")).toBeDefined();
  expect(posts(fetchMock)).toEqual(["/api/claims/CLM-1001/run"]);
});

it("shows why the agent could not start", async () => {
  stubFetch({
    ...routes(claim({})),
    "/api/claims/CLM-1001/run": new Response(
      '{"detail":"CLM-1001 is in progress."}',
      { status: 409 },
    ),
  });
  render(<ClaimPage claimId="CLM-1001" />);
  fireEvent.click(await screen.findByRole("button", { name: "Run the agent" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "CLM-1001 is in progress.",
  );
});

it("follows the steps of a claim in progress", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const current = routes(claim({ status: "in_progress" }), steps.slice(0, 1));
  stubFetch(current);
  render(<ClaimPage claimId="CLM-1001" />);
  expect(
    await screen.findByText("get_claim(claim_id='CLM-1001')"),
  ).toBeDefined();
  current["/api/claims/CLM-1001/steps"] = steps;
  await vi.advanceTimersByTimeAsync(2000);
  expect(await screen.findByText("Approved and paid $420.00.")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Run the agent" })).toBeNull();
});

it("shows a decided claim with its decision and payment", async () => {
  stubFetch(
    routes(
      claim({
        status: "decided",
        decision,
        payment: { amount: 420, paid_at: "2026-10-19T17:49:52+00:00" },
      }),
      steps,
    ),
  );
  render(<ClaimPage claimId="CLM-1001" />);
  const panel = await screen.findByRole("region", { name: "Decision" });
  expect(within(panel).getByText("Approved")).toBeDefined();
  expect(within(panel).getByText("The delay is covered.")).toBeDefined();
  expect(within(panel).getByText("Section 3, Trip Delay")).toBeDefined();
  expect(within(panel).getByText(/^Paid/).textContent).toMatch(
    /^Paid \$420\.00 on Oct 19, 2026/,
  );
  expect(await screen.findByText("Result")).toBeDefined();
});

it("shows a decline without a payment", async () => {
  stubFetch(
    routes(
      claim({
        status: "decided",
        decision: { ...decision, decision: "decline", payout: 0 },
      }),
    ),
  );
  render(<ClaimPage claimId="CLM-1001" />);
  const panel = await screen.findByRole("region", { name: "Decision" });
  expect(within(panel).getByText("Declined")).toBeDefined();
  expect(within(panel).queryByText(/Paid/)).toBeNull();
});

it("signs off a payout over the limit", async () => {
  const awaiting = claim({
    status: "awaiting_sign_off",
    decision: { ...decision, payout: 1150 },
  });
  const current: Routes = {
    ...routes(awaiting),
    "/api/claims/CLM-1001/sign-off": {},
  };
  const fetchMock = stubFetch(current);
  render(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText(/Payment blocked/)).toBeDefined();
  fireEvent.change(await screen.findByLabelText("Handler"), {
    target: { value: "HND-02" },
  });
  current["/api/claims/CLM-1001"] = {
    ...awaiting,
    status: "decided",
    payment: { amount: 1150, paid_at: "2026-10-19T17:56:43+00:00" },
    sign_off: { handler_id: "HND-02", signed_off_at: "2026-10-19T17:56:43" },
  };
  fireEvent.click(screen.getByRole("button", { name: "Sign off and pay" }));
  expect(
    await screen.findByText(/signed off by Marcus Oyelaran/),
  ).toBeDefined();
  expect(posts(fetchMock)).toEqual(["/api/claims/CLM-1001/sign-off"]);
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/claims/CLM-1001/sign-off",
    expect.objectContaining({ body: '{"handler_id":"HND-02"}' }),
  );
});

it("shows why a sign-off was refused", async () => {
  stubFetch({
    ...routes(
      claim({
        status: "awaiting_sign_off",
        decision: { ...decision, payout: 1150 },
      }),
    ),
    "/api/claims/CLM-1001/sign-off": new Response(
      '{"detail":"CLM-1001 is decided."}',
      { status: 409 },
    ),
  });
  render(<ClaimPage claimId="CLM-1001" />);
  await screen.findByRole("option", { name: "Dana Whitfield" });
  fireEvent.click(screen.getByRole("button", { name: "Sign off and pay" }));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "CLM-1001 is decided.",
  );
});

it("shows an error step", async () => {
  stubFetch(
    routes(claim({}), [
      {
        step_id: 1,
        kind: "error",
        text: "The agent stopped: The model is unavailable.",
        created_at: "2026-10-19T17:49:40+00:00",
      },
    ]),
  );
  render(<ClaimPage claimId="CLM-1001" />);
  expect(await screen.findByText("Error")).toBeDefined();
});

it("shows a customer with no earlier claims and a policy with no add-ons", async () => {
  stubFetch({
    ...routes(claim({})),
    "/api/policies/POL-2001": { ...policy, add_ons: [] },
    "/api/customers/CUS-3001": { ...customer, previous_claims: [] },
  });
  render(<ClaimPage claimId="CLM-1001" />);
  const policyPanel = await screen.findByRole("region", { name: "Policy" });
  expect(await within(policyPanel).findByText("None")).toBeDefined();
  const customerPanel = await screen.findByRole("region", { name: "Customer" });
  expect(within(customerPanel).getByText("None")).toBeDefined();
});

it("shows an unknown claim", async () => {
  stubFetch({});
  render(<ClaimPage claimId="CLM-1001" />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "No route /api/claims/CLM-1001",
  );
});
