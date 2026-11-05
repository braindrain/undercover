import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { EvaluationClaim, EvaluationDetails } from "./api";
import { EvaluationPage } from "./EvaluationPage";
import { evaluation, stubFetch } from "./testing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function claim(changes: Partial<EvaluationClaim>): EvaluationClaim {
  return {
    claim_id: "CLM-3F9A2C1D",
    source_claim_id: "CLM-1101",
    summary: "Typhoon canceled a flight",
    status: "decided",
    handler_decision: "approve",
    handler_payout: 580,
    agent_decision: "approve",
    agent_payout: 580,
    reason: "The delay is covered.",
    cost: 0.0123,
    seconds: 72,
    mistake_cost: 0,
    miss: false,
    ...changes,
  };
}

const details: EvaluationDetails = {
  ...evaluation({}),
  claims: [
    claim({}),
    claim({
      claim_id: "CLM-0A1B2C3D",
      source_claim_id: "CLM-1102",
      status: "in_progress",
      agent_decision: null,
      agent_payout: null,
      reason: null,
      mistake_cost: 50,
      miss: true,
    }),
  ],
};

const path = "/api/evaluations/evaluation-3f9a2c1d";

it("shows what was run and how it was measured", async () => {
  stubFetch({ [path]: details });
  render(<EvaluationPage runId="evaluation-3f9a2c1d" />);
  const facts = await screen.findByRole("region", { name: "This evaluation" });
  expect(within(facts).getByText("Held-out set, tiny")).toBeDefined();
  expect(within(facts).getByText("openai/gpt-6-luna")).toBeDefined();
  expect(within(facts).getByText("low")).toBeDefined();
  const measures = screen.getByRole("region", { name: "Measures" });
  expect(
    within(measures).getByText("Decision accuracy").nextSibling?.textContent,
  ).toBe("80.0%");
  expect(
    within(measures).getByText("Cost of mistakes").nextSibling?.textContent,
  ).toBe("$1,550.00");
  expect(within(measures).getByText("Whole run").nextSibling?.textContent).toBe(
    "1m 35s",
  );
});

it("shows the handler beside the agent, with the misses marked", async () => {
  stubFetch({ [path]: details });
  render(<EvaluationPage runId="evaluation-3f9a2c1d" />);
  const claims = await screen.findByRole("region", { name: "Claims" });
  expect(within(claims).getByText("1 of 2 missed")).toBeDefined();
  const [, right, missed] = within(claims).getAllByRole("row");
  expect(within(right).queryByText("Miss")).toBeNull();
  expect(within(right).getByText("The delay is covered.")).toBeDefined();
  expect(within(right).getAllByText("$580.00")).toHaveLength(2);
  expect(within(missed).getByText("Miss").className).toBe("chip chip-miss");
  expect(within(missed).getByText("No decision")).toBeDefined();
  expect(within(missed).getByText("$50.00")).toBeDefined();
});

it("links each claim to its page", async () => {
  stubFetch({ [path]: details });
  render(<EvaluationPage runId="evaluation-3f9a2c1d" />);
  const link = await screen.findByRole("link", { name: "CLM-0A1B2C3D" });
  expect(link.getAttribute("href")).toBe("#/claims/CLM-0A1B2C3D");
});

it("shows a whole set", async () => {
  stubFetch({
    [path]: { ...details, claim_set: "evaluation", tiny: false },
  });
  render(<EvaluationPage runId="evaluation-3f9a2c1d" />);
  expect(await screen.findByText("Evaluation set")).toBeDefined();
});

it("shows an unknown evaluation", async () => {
  stubFetch({});
  render(<EvaluationPage runId="evaluation-3f9a2c1d" />);
  expect((await screen.findByRole("alert")).textContent).toBe(
    `No route ${path}`,
  );
});
