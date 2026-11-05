import { expect, test } from "@playwright/test";

// These tests stand in for the dispatcher: they drive the run through the API.

test("the queue shows every claim", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(26);
});

test("starting a dispatch reaches the agent's service with N", async ({
  page,
  request,
}) => {
  await page.goto("/");
  const run = page.getByRole("region", { name: "The agent's run" });
  await expect(run.getByLabel("Claim agents at once")).toHaveValue("3");
  await run.getByLabel("Claim agents at once").selectOption("7");
  await run.getByRole("button", { name: "Start a dispatch" }).click();
  await expect
    .poll(async () => {
      const calls = await request.get("http://127.0.0.1:8003/calls");
      return (await calls.json()) as string[];
    })
    .toContain("dispatch 7");
});

test("a dispatch shows its claims, its cost and time, and its summary", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/dispatches", {
    data: {
      agents_at_once: 3,
      model: "openai/gpt-6-luna",
      reasoning_effort: "low",
    },
  });
  const runId = ((await created.json()) as { run_id: string }).run_id;
  await page.goto("/");
  const run = page.getByRole("region", { name: "The agent's run" });
  await expect(run.getByText("Running")).toBeVisible();
  const row = run.getByRole("listitem").filter({ hasText: "CLM-1020" });
  await expect(row.getByText("Waiting")).toBeVisible();
  expect((await request.post("/api/claims/CLM-1020/start")).ok()).toBe(true);
  await expect(row.getByText("In progress")).toBeVisible();
  await request.post("/api/claims/CLM-1020/model-calls", {
    data: {
      run_id: runId,
      input_tokens: 1000,
      output_tokens: 100,
      cost: 0.0125,
      seconds: 4,
    },
  });
  await request.post("/api/claims/CLM-1020/work", {
    data: {
      run_id: runId,
      started_at: "2026-11-05T10:00:00+00:00",
      finished_at: "2026-11-05T10:01:12+00:00",
      seconds: 72,
    },
  });
  await request.post("/api/claims/CLM-1020/decision", {
    data: {
      decision: "decline",
      payout: 0,
      reasons: "Not covered.",
      policy_section: "Section 9, General exclusions",
    },
  });
  await request.post(`/api/runs/${runId}/finish`, {
    data: { status: "finished" },
  });
  await expect(run.getByText("Finished", { exact: true })).toBeVisible();
  await expect(run.getByText("$0.0125").first()).toBeVisible();
  await expect(run.getByText("1m 12s").first()).toBeVisible();
  const summary = run.getByRole("region", { name: "Summary" });
  await expect(summary.getByText("Declined")).toBeVisible();
});

test("a claim that failed twice is left for a handler", async ({
  page,
  request,
}) => {
  for (let attempt = 0; attempt < 2; attempt++) {
    expect((await request.post("/api/claims/CLM-1005/start")).ok()).toBe(true);
    await request.post("/api/claims/CLM-1005/release");
  }
  await page.goto("/");
  const row = page.getByRole("row").filter({ hasText: "CLM-1005" });
  await expect(row.getByText("Left for a handler")).toBeVisible();
  await row.getByRole("link", { name: "CLM-1005" }).click();
  await expect(
    page.getByText(
      "The agent failed on this claim twice, so it's left for a handler.",
    ),
  ).toBeVisible();
});
