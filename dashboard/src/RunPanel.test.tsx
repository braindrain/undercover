import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Run } from "./api";
import { RunPanel } from "./RunPanel";
import { stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const running: Run = {
  run_id: "dispatch-3f9a2c1d",
  agents_at_once: 3,
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  status: "running",
  started_at: "2026-10-21T16:00:00+00:00",
  finished_at: null,
  claims: [
    {
      claim_id: "CLM-1001",
      summary: "Flight home canceled",
      status: "decided",
      decision: "approve",
      payout: 420,
    },
    {
      claim_id: "CLM-1002",
      summary: "Laptop damaged",
      status: "in_progress",
      decision: null,
      payout: null,
    },
    {
      claim_id: "CLM-1003",
      summary: "Bag stolen",
      status: "waiting",
      decision: null,
      payout: null,
    },
  ],
};

it("says when the agent hasn't worked the queue yet", async () => {
  stubFetch({ "/api/dispatches/latest": null });
  render(<RunPanel />);
  expect(
    await screen.findByText("The agent hasn't worked the queue yet."),
  ).toBeDefined();
});

it("shows a running dispatch with each of its claims", async () => {
  stubFetch({ "/api/dispatches/latest": running });
  render(<RunPanel />);
  const panel = await screen.findByRole("region", { name: "The agent's run" });
  expect(await within(panel).findByText("Running")).toBeDefined();
  expect(within(panel).getByText("3")).toBeDefined();
  const items = within(panel).getAllByRole("listitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "DecidedCLM-1001Flight home canceled",
    "In progressCLM-1002Laptop damaged",
    "WaitingCLM-1003Bag stolen",
  ]);
  expect(
    within(items[1])
      .getByRole("link", { name: "CLM-1002" })
      .getAttribute("href"),
  ).toBe("#/claims/CLM-1002");
});

it("says when a dispatch had nothing to work", async () => {
  stubFetch({ "/api/dispatches/latest": { ...running, claims: [] } });
  render(<RunPanel />);
  expect(
    await screen.findByText("This dispatch had no claims to work."),
  ).toBeDefined();
});

it("shows a finished dispatch", async () => {
  stubFetch({
    "/api/dispatches/latest": {
      ...running,
      status: "finished",
      finished_at: "2026-10-21T16:05:00+00:00",
    },
  });
  render(<RunPanel />);
  expect(
    await screen.findByText("Finished", { selector: ".chip" }),
  ).toBeDefined();
  expect(screen.getByText("Ended")).toBeDefined();
});

it("refreshes every 2 seconds and shows a claim move on", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const routes: Record<string, unknown> = {
    "/api/dispatches/latest": running,
  };
  const fetchMock = stubFetch(routes);
  render(<RunPanel />);
  await screen.findByText("Waiting");
  routes["/api/dispatches/latest"] = {
    ...running,
    claims: running.claims.map((claim) => ({ ...claim, status: "decided" })),
  };
  await vi.advanceTimersByTimeAsync(2000);
  await vi.waitFor(() => {
    expect(screen.queryByText("Waiting")).toBeNull();
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
