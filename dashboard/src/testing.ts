import { vi } from "vitest";
import type { ClaimSummary } from "./api";

export type Routes = Record<string, unknown>;

export function stubFetch(routes: Routes) {
  const fetchMock = vi.fn((path: string) => {
    if (!(path in routes)) {
      return Promise.resolve(
        new Response(JSON.stringify({ detail: `No route ${path}` }), {
          status: 404,
        }),
      );
    }
    const body = routes[path];
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
