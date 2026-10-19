import { expect, test, type APIRequestContext } from "@playwright/test";

// These tests stand in for the model: they drive each claim through the API.

async function approve(
  request: APIRequestContext,
  claimId: string,
  payout: number,
) {
  expect((await request.post(`/api/claims/${claimId}/start`)).ok()).toBe(true);
  const decision = await request.post(`/api/claims/${claimId}/decision`, {
    data: {
      decision: "approve",
      payout,
      reasons: "The loss is covered by the policy wording.",
      policy_section: "Section 5, Baggage and Personal Effects",
    },
  });
  expect(decision.ok()).toBe(true);
}

test("a claim is shown in full", async ({ page }) => {
  await page.goto("/#/claims/CLM-1001");
  await expect(
    page.getByRole("heading", { name: /Flight home canceled by the airline/ }),
  ).toBeVisible();
  await expect(page.getByText("Airport hotel, one night")).toBeVisible();
  await expect(
    page.getByText("Meridian Air cancellation notice"),
  ).toBeVisible();
  const policy = page.getByRole("region", { name: "Policy" });
  await expect(policy.getByText("Premium")).toBeVisible();
  const customer = page.getByRole("region", { name: "Customer" });
  await expect(customer.getByText("Maya Patel")).toBeVisible();
  await expect(customer.getByText("CLM-0412")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run the agent" }),
  ).toBeVisible();
});

test("the agent's steps are shown as it works", async ({ page, request }) => {
  await request.post("/api/claims/CLM-1003/start");
  await page.goto("/#/claims/CLM-1003");
  await expect(page.getByText("In progress", { exact: true })).toBeVisible();
  await request.post("/api/claims/CLM-1003/steps", {
    data: { kind: "tool_call", text: "get_claim(claim_id='CLM-1003')" },
  });
  await expect(page.getByText("get_claim(claim_id='CLM-1003')")).toBeVisible();
});

test("an approval under the limit is paid", async ({ page, request }) => {
  await approve(request, "CLM-1004", 150);
  const payment = await request.post("/api/claims/CLM-1004/payments", {
    data: { amount: 150 },
  });
  expect(payment.ok()).toBe(true);
  await page.goto("/#/claims/CLM-1004");
  const decision = page.getByRole("region", { name: "Decision" });
  await expect(decision.getByText("Approved")).toBeVisible();
  await expect(decision.getByText(/^Paid \$150\.00/)).toBeVisible();
  await page.goto("/");
  const row = page.getByRole("row").filter({ hasText: "CLM-1004" });
  await expect(row.getByText("Decided")).toBeVisible();
});

test("an approval over the limit waits for a handler's sign-off", async ({
  page,
  request,
}) => {
  await approve(request, "CLM-1002", 1150);
  const payment = await request.post("/api/claims/CLM-1002/payments", {
    data: { amount: 1150 },
  });
  expect(payment.status()).toBe(409);
  await page.goto("/#/claims/CLM-1002");
  await expect(page.getByText("Waiting for sign-off")).toBeVisible();
  await expect(page.getByText(/Payment blocked/)).toBeVisible();
  await page.getByLabel("Handler").selectOption({ label: "Dana Whitfield" });
  await page.getByRole("button", { name: "Sign off and pay" }).click();
  await expect(
    page.getByText(/^Paid \$1,150\.00 .* signed off by Dana Whitfield\.$/),
  ).toBeVisible();
  await expect(page.getByText("Decided", { exact: true })).toBeVisible();
});
