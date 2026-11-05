import type { APIRequestContext } from "@playwright/test";

const agentUrl = "http://127.0.0.1:8003";

// What the stand-in for the agent's service has been asked to do so far.
export async function agentCalls(
  request: APIRequestContext,
): Promise<string[]> {
  const response = await request.get(`${agentUrl}/calls`);
  return (await response.json()) as string[];
}
