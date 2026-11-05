import { expect, test } from "@playwright/test";

// These tests stand in for the dispatcher: they drive the run through the API.

test("the queue shows every claim", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(26);
});

test("the queue shows the latest dispatch and its claims", async ({
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
  await request.post(`/api/runs/${runId}/finish`, {
    data: { status: "finished" },
  });
  await expect(run.getByText("Finished", { exact: true })).toBeVisible();
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
