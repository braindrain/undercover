import { vi } from "vitest";
import type { ClaimSummary, Evaluation, RunOptions } from "./api";

export type Routes = Record<string, unknown>;

export const options: RunOptions = {
  fewest_agents: 1,
  most_agents: 25,
  starting_agents: 3,
};

export function stubFetch(routes: Routes) {
  const fetchMock = vi.fn<
    (path: string, init?: RequestInit) => Promise<Response>
  >((path) => {
    if (!(path in routes)) {
      return Promise.resolve(
        new Response(JSON.stringify({ detail: `No route ${path}` }), {
          status: 404,
        }),
      );
    }
    const body = routes[path];
    if (body instanceof Response) return Promise.resolve(body);
    return Promise.resolve(
      new Response(typeof body === "string" ? body : JSON.stringify(body)),
    );
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function summary(changes: Partial<ClaimSummary>): ClaimSummary {
  return {
    claim_id: "CLM-1001",
    customer_name: "Maya Patel",
    claim_type: "trip_delay",
    summary: "Flight home canceled",
    submitted_on: "2026-06-24",
    amount_claimed: 420,
    status: "waiting",
    decision: null,
    payout: null,
    ...changes,
  };
}

export function evaluation(changes: Partial<Evaluation>): Evaluation {
  return {
    run_id: "evaluation-3f9a2c1d",
    agents_at_once: 5,
    claim_set: "held_out",
    tiny: true,
    model: "openai/gpt-6-luna",
    reasoning_effort: "low",
    status: "finished",
    started_at: "2026-11-05T10:00:00+00:00",
    finished_at: "2026-11-05T10:02:00+00:00",
    claim_count: 5,
    measures: {
      decision_accuracy: 0.8,
      accuracy: 0.6,
      cost_of_mistakes: 1550,
      straight_through_rate: 0.75,
      cost_per_claim: 0.0123,
      seconds_per_claim: 72,
      run_seconds: 95,
    },
    ...changes,
  };
}
