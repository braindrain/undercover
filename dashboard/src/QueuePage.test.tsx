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
  summary({
    claim_id: "CLM-1003",
    status: "decided",
    decision: "approve",
    payout: 1250,
  }),
];

it("counts the claims in each state", async () => {
  stubFetch({ "/api/claims": claims });
  render(<QueuePage />);
  const tiles = await screen.findByRole("region", { name: "Claims by state" });
  expect(within(tiles).getByText("Waiting").nextSibling?.textContent).toBe("1");
  expect(
    within(tiles).getByText("Waiting for sign-off").nextSibling?.textContent,
  ).toBe("0");
});

it("lists every claim with its decision and payout", async () => {
  stubFetch({ "/api/claims": claims });
  render(<QueuePage />);
  const row = (await screen.findByRole("link", { name: "CLM-1003" })).closest(
    "tr",
  );
  expect(row).not.toBeNull();
  if (!row) return;
  expect(within(row).getByText("Approved")).toBeDefined();
  expect(within(row).getByText("$1,250.00")).toBeDefined();
  expect(within(row).getByText("Trip delay")).toBeDefined();
  expect(screen.getAllByRole("row")).toHaveLength(4);
});

it("refreshes the queue", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = stubFetch({ "/api/claims": claims });
  render(<QueuePage />);
  await screen.findByRole("link", { name: "CLM-1001" });
  await vi.advanceTimersByTimeAsync(5000);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("shows an error", async () => {
  stubFetch({});
  render(<QueuePage />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "No route /api/claims",
  );
});
