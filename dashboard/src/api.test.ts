import { afterEach, expect, it, vi } from "vitest";
import {
  formatCost,
  formatDuration,
  formatLabel,
  formatMoney,
  formatPercent,
  signOff,
  startDispatch,
  startEvaluation,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("formats money in US dollars", () => {
  expect(formatMoney(1250)).toBe("$1,250.00");
  expect(formatMoney(0)).toBe("$0.00");
});

it("formats model cost to a hundredth of a cent", () => {
  expect(formatCost(0.0123)).toBe("$0.0123");
  expect(formatCost(1.5)).toBe("$1.5000");
});

it("formats a duration", () => {
  expect(formatDuration(8.4)).toBe("8s");
  expect(formatDuration(72)).toBe("1m 12s");
  expect(formatDuration(0)).toBe("0s");
});

it("formats a share as a percentage", () => {
  expect(formatPercent(0.5)).toBe("50.0%");
  expect(formatPercent(2 / 3)).toBe("66.7%");
});

it("formats labels", () => {
  expect(formatLabel("emergency_medical")).toBe("Emergency medical");
});

it("posts JSON", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetchMock);
  await signOff("CLM-1002", "HND-002");
  expect(fetchMock).toHaveBeenCalledWith("/api/claims/CLM-1002/sign-off", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: '{"handler_id":"HND-002"}',
  });
});

it("starts a dispatch with N", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetchMock);
  await startDispatch(7);
  expect(fetchMock).toHaveBeenCalledWith("/api/dispatches/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: '{"agents_at_once":7}',
  });
});

it("starts an evaluation with its settings", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetchMock);
  await startEvaluation(5, "held_out", true);
  expect(fetchMock).toHaveBeenCalledWith("/api/evaluations/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: '{"agents_at_once":5,"claim_set":"held_out","tiny":true}',
  });
});

it("raises the backend's reason for refusing", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response('{"detail":"CLM-1001 has already been paid."}', {
        status: 409,
      }),
    ),
  );
  await expect(signOff("CLM-1001", "HND-002")).rejects.toThrow(
    "CLM-1001 has already been paid.",
  );
});
