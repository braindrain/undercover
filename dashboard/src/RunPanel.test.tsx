import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Run, RunClaim } from "./api";
import { RunPanel } from "./RunPanel";
import { options, stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

function claim(changes: Partial<RunClaim>): RunClaim {
  return {
    claim_id: "CLM-1001",
    summary: "Flight home canceled",
    status: "waiting",
    decision: null,
    payout: null,
    cost: 0,
    seconds: 0,
    model_seconds: 0,
    started_at: null,
    finished_at: null,
    ...changes,
  };
}

const running: Run = {
  run_id: "dispatch-3f9a2c1d",
  agents_at_once: 3,
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  status: "running",
  started_at: "2026-10-21T16:00:00+00:00",
  finished_at: null,
  claims: [
    claim({
      status: "decided",
      decision: "approve",
      payout: 420,
      cost: 0.0123,
      seconds: 72,
    }),
    claim({
      claim_id: "CLM-1002",
      summary: "Laptop damaged",
      status: "in_progress",
    }),
    claim({ claim_id: "CLM-1003", summary: "Bag stolen" }),
  ],
  model_cost: 0.0123,
  work_seconds: 72,
  run_seconds: 72,
  summary: {
    approved: 1,
    declined: 0,
    referred: 0,
    awaiting_sign_off: [],
    left_for_handler: [],
    in_progress: ["CLM-1002"],
  },
};

const finished: Run = {
  ...running,
  status: "finished",
  finished_at: "2026-10-21T16:05:00+00:00",
  summary: {
    approved: 3,
    declined: 2,
    referred: 1,
    awaiting_sign_off: [{ claim_id: "CLM-1008", payout: 2240 }],
    left_for_handler: ["CLM-1009"],
    in_progress: ["CLM-1010"],
  },
};

const routes = { "/api/dispatches/latest": null, "/api/run-options": options };

it("says when the agent hasn't worked the queue yet", async () => {
  stubFetch(routes);
  render(<RunPanel />);
  expect(
    await screen.findByText("The agent hasn't worked the queue yet."),
  ).toBeDefined();
});

it("offers N from the options, starting at 3", async () => {
  stubFetch(routes);
  render(<RunPanel />);
  const select = await screen.findByLabelText<HTMLSelectElement>(
    "Claim agents at once",
  );
  expect(select.value).toBe("3");
  const offered = within(select)
    .getAllByRole("option")
    .map((option) => option.textContent);
  expect(offered).toHaveLength(25);
  expect(offered[0]).toBe("1");
  expect(offered[24]).toBe("25");
});

it("starts a dispatch with the chosen N", async () => {
  const fetchMock = stubFetch({
    ...routes,
    "/api/dispatches/start": { status: "accepted" },
  });
  render(<RunPanel />);
  fireEvent.change(await screen.findByLabelText("Claim agents at once"), {
    target: { value: "7" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start a dispatch" }));
  await vi.waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/dispatches/start",
      expect.objectContaining({ body: '{"agents_at_once":7}' }),
    );
  });
  await vi.waitFor(() => {
    expect(
      screen
        .getByRole<HTMLButtonElement>("button", { name: "Start a dispatch" })
        .hasAttribute("disabled"),
    ).toBe(false);
  });
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each([
  [409, "A dispatch or an evaluation is already running."],
  [503, "The agent's service isn't running."],
])("shows a %i as an alert", async (status, detail) => {
  stubFetch({
    ...routes,
    "/api/dispatches/start": new Response(JSON.stringify({ detail }), {
      status,
    }),
  });
  render(<RunPanel />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start a dispatch" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(detail);
});

it("shows a running dispatch with each of its claims and no summary", async () => {
  stubFetch({ ...routes, "/api/dispatches/latest": running });
  render(<RunPanel />);
  const panel = await screen.findByRole("region", { name: "The agent's run" });
  expect(await within(panel).findByText("Running")).toBeDefined();
  const items = within(panel).getAllByRole("listitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "DecidedCLM-1001Flight home canceled$0.01231m 12s",
    "In progressCLM-1002Laptop damaged$0.00000s",
    "WaitingCLM-1003Bag stolen$0.00000s",
  ]);
  expect(
    within(items[1])
      .getByRole("link", { name: "CLM-1002" })
      .getAttribute("href"),
  ).toBe("#/claims/CLM-1002");
  expect(within(panel).queryByRole("region", { name: "Summary" })).toBeNull();
});

it("shows the dispatch's model cost and time", async () => {
  stubFetch({ ...routes, "/api/dispatches/latest": running });
  render(<RunPanel />);
  expect((await screen.findByText("Model cost")).nextSibling?.textContent).toBe(
    "$0.0123",
  );
  expect(screen.getByText("Time").nextSibling?.textContent).toBe("1m 12s");
});

it("says when a dispatch had nothing to work", async () => {
  stubFetch({
    ...routes,
    "/api/dispatches/latest": { ...running, claims: [] },
  });
  render(<RunPanel />);
  expect(
    await screen.findByText("This dispatch had no claims to work."),
  ).toBeDefined();
});

it("shows a finished dispatch with its summary", async () => {
  stubFetch({ ...routes, "/api/dispatches/latest": finished });
  render(<RunPanel />);
  const summary = await screen.findByRole("region", { name: "Summary" });
  const facts = within(summary);
  expect(facts.getByText("Approved").nextSibling?.textContent).toBe("3");
  expect(facts.getByText("Declined").nextSibling?.textContent).toBe("2");
  expect(facts.getByText("Referred").nextSibling?.textContent).toBe("1");
  expect(facts.getByText("Waiting for sign-off").nextSibling?.textContent).toBe(
    "CLM-1008 $2,240.00",
  );
  expect(facts.getByText("Left for a handler").nextSibling?.textContent).toBe(
    "CLM-1009",
  );
  expect(facts.getByText("Still in progress").nextSibling?.textContent).toBe(
    "CLM-1010",
  );
  expect(screen.getByText("Ended")).toBeDefined();
});

it("leaves out what a summary doesn't have", async () => {
  stubFetch({
    ...routes,
    "/api/dispatches/latest": {
      ...finished,
      summary: {
        ...finished.summary,
        awaiting_sign_off: [],
        left_for_handler: [],
        in_progress: [],
      },
    },
  });
  render(<RunPanel />);
  const summary = await screen.findByRole("region", { name: "Summary" });
  expect(within(summary).queryByText("Waiting for sign-off")).toBeNull();
  expect(within(summary).queryByText("Left for a handler")).toBeNull();
  expect(within(summary).queryByText("Still in progress")).toBeNull();
});

it("shows a stopped dispatch with its summary", async () => {
  stubFetch({
    ...routes,
    "/api/dispatches/latest": { ...finished, status: "stopped" },
  });
  render(<RunPanel />);
  const chip = await screen.findByText("Stopped", { selector: ".chip" });
  expect(chip.className).toBe("chip chip-stopped");
  expect(screen.getByRole("region", { name: "Summary" })).toBeDefined();
});

it("refreshes every 2 seconds and shows a claim move on", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const live: Record<string, unknown> = {
    ...routes,
    "/api/dispatches/latest": running,
  };
  const fetchMock = stubFetch(live);
  render(<RunPanel />);
  await screen.findByText("Waiting");
  live["/api/dispatches/latest"] = {
    ...running,
    claims: running.claims.map((one) => ({ ...one, status: "decided" })),
  };
  await vi.advanceTimersByTimeAsync(2000);
  await vi.waitFor(() => {
    expect(screen.queryByText("Waiting")).toBeNull();
  });
  const polls = fetchMock.mock.calls.filter(
    ([path]) => path === "/api/dispatches/latest",
  );
  expect(polls).toHaveLength(2);
});
