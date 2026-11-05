import { vi } from "vitest";
import type { ClaimSummary, RunOptions } from "./api";

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
