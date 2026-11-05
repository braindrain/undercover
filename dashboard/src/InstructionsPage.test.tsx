import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { InstructionsPage } from "./InstructionsPage";
import { stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the claim agent's instructions and the documents, read only", async () => {
  stubFetch({
    "/api/instructions": "# Working a claim\n\nDecide claims.",
    "/api/documents/policy_wording": "# Policy Wording",
    "/api/documents/claims_manual": "# Claims Manual",
  });
  render(<InstructionsPage />);
  const claim = await screen.findByRole("region", { name: "Working a claim" });
  expect(claim.textContent).toContain("Decide claims.");
  expect(await screen.findByText("# Policy Wording")).toBeDefined();
  expect(await screen.findByText("# Claims Manual")).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByText(/Running the queue/)).toBeNull();
});

it("shows an error", async () => {
  stubFetch({});
  render(<InstructionsPage />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    "No route /api/instructions",
  );
});
