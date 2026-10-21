import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueuePage } from "./QueuePage";
import { stubFetch, summary } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const claims = [
  summary({}),
  summary({ claim_id: "CLM-1002", status: "in_progress" }),
  summary({ claim_id: "CLM-1004", status: "left_for_handler" }),
  summary({
    claim_id: "CLM-1003",
    status: "decided",
    decision: "approve",
    payout: 1250,
  }),
];

const routes = { "/api/claims": claims, "/api/runs/latest": null };

it("counts the claims in each state", async () => {
  stubFetch(routes);
  render(<QueuePage />);
  const tiles = await screen.findByRole("region", { name: "Claims by state" });
  expect(
    within(tiles).getAllByText(/./, { selector: ".tile > span:first-child" }),
  ).toHaveLength(5);
  expect(within(tiles).getByText("Waiting").nextSibling?.textContent).toBe("1");
  expect(
    within(tiles).getByText("Waiting for sign-off").nextSibling?.textContent,
  ).toBe("0");
  expect(
    within(tiles).getByText("Left for a handler").nextSibling?.textContent,
  ).toBe("1");
});

it("shows a claim left for a handler", async () => {
  stubFetch(routes);
  render(<QueuePage />);
  const row = (await screen.findByRole("link", { name: "CLM-1004" })).closest(
    "tr",
  );
  expect(row).not.toBeNull();
  if (!row) return;
  expect(within(row).getByText("Left for a handler").className).toBe(
    "chip chip-left_for_handler",
  );
});

it("shows the agent's run above the claims", async () => {
  stubFetch(routes);
  render(<QueuePage />);
  expect(
    await screen.findByRole("region", { name: "The agent's run" }),
  ).toBeDefined();
});

it("lists every claim with its decision and payout", async () => {
  stubFetch(routes);
  render(<QueuePage />);
  const row = (await screen.findByRole("link", { name: "CLM-1003" })).closest(
    "tr",
  );
  expect(row).not.toBeNull();
  if (!row) return;
  expect(within(row).getByText("Approved")).toBeDefined();
  expect(within(row).getByText("$1,250.00")).toBeDefined();
  expect(within(row).getByText("Trip delay")).toBeDefined();
  expect(screen.getAllByRole("row")).toHaveLength(5);
});

it("refreshes the queue", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = stubFetch(routes);
  render(<QueuePage />);
  await screen.findByRole("link", { name: "CLM-1001" });
  await vi.advanceTimersByTimeAsync(5000);
  const queueFetches = fetchMock.mock.calls.filter(
    ([path]) => path === "/api/claims",
  );
  expect(queueFetches).toHaveLength(2);
});

it("shows an error", async () => {
  stubFetch({});
  render(<QueuePage />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "No route /api/claims",
  );
});
