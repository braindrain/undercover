import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { InstructionsPage } from "./InstructionsPage";
import { stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("shows the instructions and the policy wording, read only", async () => {
  stubFetch({
    "/api/instructions": "# The UnderCover Agent\n\nDecide claims.",
    "/api/policy-wording": "# Policy Wording",
  });
  render(<InstructionsPage />);
  expect(await screen.findByText(/Decide claims\./)).toBeDefined();
  expect(await screen.findByText("# Policy Wording")).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
});
