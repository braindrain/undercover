import { expect, test, type APIRequestContext } from "@playwright/test";

// These tests stand in for the dispatcher: they drive an evaluation through the
// API, deciding every copy the same way, so that some are misses.

async function playEvaluation(request: APIRequestContext): Promise<string> {
  const created = await request.post("/api/evaluations", {
    data: {
      agents_at_once: 5,
      claim_set: "held_out",
      tiny: true,
      model: "openai/gpt-6-luna",
      reasoning_effort: "low",
    },
  });
  const runId = ((await created.json()) as { run_id: string }).run_id;
  const listed = await request.get(`/api/runs/${runId}/claims`);
  for (const claimId of (await listed.json()) as string[]) {
    await request.post(`/api/claims/${claimId}/start`);
    await request.post(`/api/claims/${claimId}/model-calls`, {
      data: {
        run_id: runId,
        input_tokens: 1000,
        output_tokens: 100,
        cost: 0.0125,
        seconds: 4,
      },
    });
    await request.post(`/api/claims/${claimId}/work`, {
      data: {
        run_id: runId,
        started_at: "2026-11-05T10:00:00+00:00",
        finished_at: "2026-11-05T10:01:12+00:00",
        seconds: 72,
      },
    });
    await request.post(`/api/claims/${claimId}/decision`, {
      data: {
        decision: "approve",
        payout: 1150,
        reasons: "The loss is covered by the policy wording.",
        policy_section: "Section 5, Baggage and Personal Effects",
      },
    });
  }
  await request.post(`/api/runs/${runId}/finish`, {
    data: { status: "finished" },
  });
  return runId;
}

test("starting an evaluation reaches the agent's service with its settings", async ({
  page,
  request,
}) => {
  await page.goto("/#/scoreboard");
  await page.getByLabel("Claim agents at once").selectOption("5");
  await page.getByLabel(/^Set/).selectOption("held_out");
  await page.getByLabel(/^Claims/).selectOption("tiny");
  await page.getByRole("button", { name: "Start an evaluation" }).click();
  await expect
    .poll(async () => {
      const calls = await request.get("http://127.0.0.1:8003/calls");
      return (await calls.json()) as string[];
    })
    .toContain("evaluation 5 held_out tiny");
});

test("the scoreboard shows an evaluation, its misses and a copy's page", async ({
  page,
  request,
}) => {
  const runId = await playEvaluation(request);
  await page.goto("/#/scoreboard");
  const row = page.getByRole("row").filter({ hasText: runId });
  await expect(row.getByText("Finished")).toBeVisible();
  await expect(row.getByText("$0.0125")).toBeVisible();
  await expect(row.getByText("1m 12s").first()).toBeVisible();

  await row.getByRole("link", { name: runId }).click();
  const claims = page.getByRole("region", { name: "Claims" });
  await expect(claims.getByText("Miss", { exact: true }).first()).toBeVisible();
  await expect(claims.getByRole("row")).toHaveCount(6);

  await claims.getByRole("row").nth(1).getByRole("link").click();
  await expect(page.getByRole("link", { name: runId })).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign off" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run the agent" })).toHaveCount(
    0,
  );
});

test("the queue doesn't show an evaluation's copies", async ({
  page,
  request,
}) => {
  await playEvaluation(request);
  await page.goto("/");
  await expect(
    page.getByRole("region", { name: "Claims by state" }),
  ).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
});
