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
  run_id: 1,
  cap: 5,
  status: "running",
  summary: null,
  started_at: "2026-10-21T16:00:00+00:00",
  finished_at: null,
  todos: [
    { text: "CLM-1001 Flight home canceled", status: "done" },
    { text: "CLM-1002 Laptop damaged", status: "in_progress" },
    { text: "CLM-1003 Bag stolen", status: "todo" },
    { text: "CLM-1004 Passport lost", status: "left_for_handler" },
  ],
};

it("says when the agent hasn't run yet", async () => {
  stubFetch({ "/api/runs/latest": null });
  render(<RunPanel />);
  expect(
    await screen.findByText(/The agent hasn't run on the queue yet\./),
  ).toBeDefined();
});

it("shows a running run with its cap and its to-do list", async () => {
  stubFetch({ "/api/runs/latest": running });
  render(<RunPanel />);
  const panel = await screen.findByRole("region", { name: "The agent's run" });
  expect(await within(panel).findByText("Running")).toBeDefined();
  expect(within(panel).getByText("5")).toBeDefined();
  expect(within(panel).getByText("2 of 4 worked")).toBeDefined();
  const items = within(panel).getAllByRole("listitem");
  expect(items.map((item) => item.textContent)).toEqual([
    "DoneCLM-1001 Flight home canceled",
    "In progressCLM-1002 Laptop damaged",
    "To doCLM-1003 Bag stolen",
    "Left for a handlerCLM-1004 Passport lost",
  ]);
  expect(within(items[3]).getByText("Left for a handler").className).toBe(
    "chip chip-left_for_handler",
  );
  expect(within(panel).queryByText("Summary")).toBeNull();
});

it("says when the to-do list isn't written yet", async () => {
  stubFetch({ "/api/runs/latest": { ...running, todos: [] } });
  render(<RunPanel />);
  expect(
    await screen.findByText("The agent hasn't written its to-do list yet."),
  ).toBeDefined();
});

it("shows a finished run with its summary", async () => {
  stubFetch({
    "/api/runs/latest": {
      ...running,
      status: "finished",
      summary: "Worked four claims.",
      finished_at: "2026-10-21T16:05:00+00:00",
    },
  });
  render(<RunPanel />);
  expect(
    await screen.findByText("Finished", { selector: ".chip" }),
  ).toBeDefined();
  expect(screen.getByText("Summary")).toBeDefined();
  expect(screen.getByText("Worked four claims.")).toBeDefined();
});

it("refreshes every 2 seconds and shows an item ticked off", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const routes: Record<string, unknown> = { "/api/runs/latest": running };
  const fetchMock = stubFetch(routes);
  render(<RunPanel />);
  await screen.findByText("2 of 4 worked");
  routes["/api/runs/latest"] = {
    ...running,
    todos: running.todos.map((todo) => ({ ...todo, status: "done" })),
  };
  await vi.advanceTimersByTimeAsync(2000);
  expect(await screen.findByText("4 of 4 worked")).toBeDefined();
  expect(fetchMock).toHaveBeenCalledTimes(2);
});
