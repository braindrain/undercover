import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ScoreboardPage } from "./ScoreboardPage";
import { evaluation, options, stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const routes = { "/api/evaluations": [], "/api/run-options": options };

it("says when no evaluation has run", async () => {
  stubFetch(routes);
  render(<ScoreboardPage />);
  expect(await screen.findByText("No evaluation has run yet.")).toBeDefined();
});

it("offers the form with N, the set and whole or tiny", async () => {
  stubFetch(routes);
  render(<ScoreboardPage />);
  const form = await screen.findByRole("region", { name: "New evaluation" });
  expect(
    within(form).getByLabelText<HTMLSelectElement>("Claim agents at once")
      .value,
  ).toBe("3");
  expect(within(form).getByLabelText<HTMLSelectElement>("Set").value).toBe(
    "evaluation",
  );
  expect(within(form).getByLabelText<HTMLSelectElement>("Claims").value).toBe(
    "whole",
  );
});

it("starts an evaluation with what was chosen", async () => {
  const fetchMock = stubFetch({
    ...routes,
    "/api/evaluations/start": { status: "accepted" },
  });
  render(<ScoreboardPage />);
  fireEvent.change(await screen.findByLabelText("Claim agents at once"), {
    target: { value: "5" },
  });
  fireEvent.change(screen.getByLabelText("Set"), {
    target: { value: "held_out" },
  });
  fireEvent.change(screen.getByLabelText("Claims"), {
    target: { value: "tiny" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start an evaluation" }));
  await vi.waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/evaluations/start",
      expect.objectContaining({
        body: '{"agents_at_once":5,"claim_set":"held_out","tiny":true}',
      }),
    );
  });
  await vi.waitFor(() => {
    const lists = fetchMock.mock.calls.filter(
      ([path]) => path === "/api/evaluations",
    );
    expect(lists).toHaveLength(2);
  });
});

it.each([
  [409, "A dispatch or an evaluation is already running."],
  [503, "The agent's service isn't running."],
])("shows a %i as an alert", async (status, detail) => {
  stubFetch({
    ...routes,
    "/api/evaluations/start": new Response(JSON.stringify({ detail }), {
      status,
    }),
  });
  render(<ScoreboardPage />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Start an evaluation" }),
  );
  expect((await screen.findByRole("alert")).textContent).toBe(detail);
});

it("lists each evaluation with every measure, in the order it's given", async () => {
  stubFetch({
    ...routes,
    "/api/evaluations": [
      evaluation({}),
      evaluation({
        run_id: "evaluation-0a1b2c3d",
        claim_set: "evaluation",
        tiny: false,
        claim_count: 25,
        agents_at_once: 25,
        status: "stopped",
      }),
    ],
  });
  render(<ScoreboardPage />);
  const table = await screen.findByRole("region", { name: "Evaluations" });
  const [, newest, older] = within(table).getAllByRole("row");
  const cells = within(newest)
    .getAllByRole("cell")
    .map((cell) => cell.textContent);
  expect(cells.slice(2)).toEqual([
    "Finished",
    "80.0%",
    "60.0%",
    "$1,550.00",
    "75.0%",
    "$0.0123",
    "1m 12s",
    "1m 35s",
  ]);
  expect(cells[1]).toBe("Held-out set, tiny5 claims, 5 at once");
  expect(
    within(newest)
      .getByRole("link", { name: "evaluation-3f9a2c1d" })
      .getAttribute("href"),
  ).toBe("#/scoreboard/evaluation-3f9a2c1d");
  expect(within(older).getByText("Stopped").className).toBe(
    "chip chip-stopped",
  );
  expect(within(older).getByText(/Evaluation set/).textContent).not.toContain(
    "tiny",
  );
});

it("refreshes the list", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetchMock = stubFetch(routes);
  render(<ScoreboardPage />);
  await screen.findByText("No evaluation has run yet.");
  await vi.advanceTimersByTimeAsync(5000);
  const lists = fetchMock.mock.calls.filter(
    ([path]) => path === "/api/evaluations",
  );
  expect(lists).toHaveLength(2);
});

it("shows an error", async () => {
  stubFetch({ "/api/run-options": options });
  render(<ScoreboardPage />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "No route /api/evaluations",
  );
});
