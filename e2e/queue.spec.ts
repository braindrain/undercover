import { expect, test } from "@playwright/test";

// These tests stand in for the queue agent: they drive the run through the API.

test("the queue shows every claim", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Claims queue" }),
  ).toBeVisible();
  await expect(page.getByRole("row")).toHaveCount(26);
});

test("the queue shows the agent's run, its to-do list and its summary", async ({
  page,
  request,
}) => {
  const started = await request.post("/api/runs", { data: { cap: 2 } });
  const runId = String(((await started.json()) as { run_id: number }).run_id);
  const setTodos = (statuses: string[]) =>
    request.put(`/api/runs/${runId}/todos`, {
      data: {
        todos: ["CLM-1001 Flight home canceled", "CLM-1002 Laptop damaged"].map(
          (text, index) => ({ text, status: statuses[index] }),
        ),
      },
    });
  await setTodos(["in_progress", "todo"]);
  await page.goto("/");
  const run = page.getByRole("region", { name: "The agent's run" });
  await expect(run.getByText("Running")).toBeVisible();
  await expect(run.getByText("0 of 2 worked")).toBeVisible();
  const first = run.getByRole("listitem").first();
  await expect(first.getByText("In progress")).toBeVisible();
  await setTodos(["done", "left_for_handler"]);
  await expect(first.getByText("Done")).toBeVisible();
  await expect(run.getByText("2 of 2 worked")).toBeVisible();
  await request.post(`/api/runs/${runId}/finish`, {
    data: { summary: "Worked two claims: one done, one left for a handler." },
  });
  await expect(
    run.getByText("Worked two claims: one done, one left for a handler."),
  ).toBeVisible();
  await expect(
    run.getByText("Finished", { exact: true }).first(),
  ).toBeVisible();
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
