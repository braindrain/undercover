import { afterEach, expect, it, vi } from "vitest";
import { formatLabel, formatMoney, post } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("formats money in US dollars", () => {
  expect(formatMoney(1250)).toBe("$1,250.00");
  expect(formatMoney(0)).toBe("$0.00");
});

it("formats labels", () => {
  expect(formatLabel("emergency_medical")).toBe("Emergency medical");
});

it("posts JSON", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response("{}"));
  vi.stubGlobal("fetch", fetchMock);
  await post("/api/claims/CLM-1001/payments", { amount: 420 });
  expect(fetchMock).toHaveBeenCalledWith("/api/claims/CLM-1001/payments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: '{"amount":420}',
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
  await expect(post("/api/claims/CLM-1001/payments")).rejects.toThrow(
    "CLM-1001 has already been paid.",
  );
});
