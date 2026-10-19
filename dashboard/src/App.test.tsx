import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { App } from "./App";
import { parseRoute } from "./hooks";
import { stubFetch, summary } from "./testing";

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
});

it("shows the queue by default", async () => {
  stubFetch({ "/api/claims": [summary({})] });
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "Claims queue" }),
  ).toBeDefined();
  expect(
    screen.getByRole("link", { name: "Queue" }).getAttribute("aria-current"),
  ).toBe("page");
});

it("follows the hash to the instructions", async () => {
  stubFetch({
    "/api/instructions": "# The UnderCover Agent",
    "/api/policy-wording": "# Policy Wording",
  });
  window.location.hash = "#/instructions";
  render(<App />);
  expect(await screen.findByText("# The UnderCover Agent")).toBeDefined();
});
