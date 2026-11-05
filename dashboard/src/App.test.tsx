import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { parseRoute } from "./hooks";
import { options, stubFetch, summary } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

it("parses routes", () => {
  expect(parseRoute("")).toEqual({ page: "queue" });
  expect(parseRoute("#/")).toEqual({ page: "queue" });
  expect(parseRoute("#/instructions")).toEqual({ page: "instructions" });
  expect(parseRoute("#/claims/CLM-1001")).toEqual({
    page: "claim",
    claimId: "CLM-1001",
  });
  expect(parseRoute("#/scoreboard")).toEqual({ page: "scoreboard" });
  expect(parseRoute("#/scoreboard/evaluation-3f9a2c1d")).toEqual({
    page: "evaluation",
    runId: "evaluation-3f9a2c1d",
  });
});

it("shows the queue by default", async () => {
  stubFetch({
    "/api/claims": [summary({})],
    "/api/dispatches/latest": null,
    "/api/run-options": options,
  });
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Claims queue" }),
  ).toBeDefined();
  expect(
    screen.getByRole("link", { name: "Queue" }).getAttribute("aria-current"),
  ).toBe("page");
});

it("follows the hash to the scoreboard", async () => {
  stubFetch({ "/api/evaluations": [], "/api/run-options": options });
  window.location.hash = "#/scoreboard";
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Scoreboard" }),
  ).toBeDefined();
  expect(
    screen
      .getByRole("link", { name: "Scoreboard" })
      .getAttribute("aria-current"),
  ).toBe("page");
});

it("follows the hash to an evaluation", async () => {
  stubFetch({});
  window.location.hash = "#/scoreboard/evaluation-3f9a2c1d";
  render(<App />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "No route /api/evaluations/evaluation-3f9a2c1d",
  );
  expect(
    screen
      .getByRole("link", { name: "Scoreboard" })
      .getAttribute("aria-current"),
  ).toBe("page");
});

it("follows the hash to the instructions", async () => {
  stubFetch({
    "/api/instructions": "# Working a claim",
    "/api/documents/policy_wording": "# Policy Wording",
    "/api/documents/claims_manual": "# Claims Manual",
  });
  window.location.hash = "#/instructions";
  render(<App />);
  expect(await screen.findByText("# Working a claim")).toBeDefined();
});
