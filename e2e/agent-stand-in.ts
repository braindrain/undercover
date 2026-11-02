// A stand-in for the agent's service, so that the end-to-end tests never reach
// the one npm run dev starts. It records each call, and carries a signed-off
// claim on by paying it through the backend's API, as the agent would.
import { createServer } from "node:http";

const backendUrl = process.env.BACKEND_URL ?? "http://127.0.0.1:8001";
const port = Number(process.env.PORT ?? "8003");
const calls: string[] = [];

interface Claim {
  decision: { payout: number } | null;
}

async function pay(claimId: string): Promise<void> {
  const response = await fetch(`${backendUrl}/api/claims/${claimId}`);
  const claim = (await response.json()) as Claim;
  if (!claim.decision) return;
  await fetch(`${backendUrl}/api/claims/${claimId}/payments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount: claim.decision.payout }),
  });
}

createServer((request, response) => {
  const url = request.url ?? "";
  const match = /^\/claims\/([^/]+)\/(work|carry-on)$/.exec(url);
  if (request.method === "GET" && url === "/health") {
    response.writeHead(200).end('{"status":"ok"}');
  } else if (request.method === "GET" && url === "/calls") {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(calls));
  } else if (request.method === "POST" && match) {
    const [, claimId = "", operation = ""] = match;
    calls.push(`${operation} ${claimId}`);
    response.writeHead(202).end();
    if (operation === "carry-on") void pay(claimId);
  } else {
    response.writeHead(404).end();
  }
}).listen(port, "127.0.0.1");
