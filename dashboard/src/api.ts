export type Status =
  | "waiting"
  | "in_progress"
  | "awaiting_sign_off"
  | "decided"
  | "left_for_handler";
export type Verdict = "approve" | "decline" | "refer";
export type StepKind = "tool_call" | "tool_result" | "read" | "reply" | "error";
export type RunStatus = "running" | "finished" | "stopped";

export interface ClaimSummary {
  claim_id: string;
  customer_name: string;
  claim_type: string;
  summary: string;
  submitted_on: string;
  amount_claimed: number;
  status: Status;
  decision: Verdict | null;
  payout: number | null;
}

export interface Item {
  description: string;
  category: string;
  amount: number;
}

export interface ClaimDocument {
  name: string;
  type: string;
  content: string;
}

export interface Decision {
  decision: Verdict;
  payout: number;
  reasons: string;
  policy_section: string;
  decided_at: string;
}

export interface Claim {
  claim_id: string;
  policy_id: string;
  customer_id: string;
  claim_type: string;
  submitted_on: string;
  incident_date: string;
  incident_location: string;
  summary: string;
  story: string;
  items: Item[];
  amount_claimed: number;
  documents: ClaimDocument[];
  status: Status;
  failures: number;
  thread_id: string | null;
  decision: Decision | null;
  payment: {
    amount: number;
    paid_at: string;
    signed_off_by: string | null;
    signed_off_at: string | null;
  } | null;
  sign_off: { handler_id: string; signed_off_at: string } | null;
}

export interface ClaimEvaluation {
  evaluation_id: string;
  source_claim_id: string;
}

export interface Policy {
  policy_id: string;
  plan: string;
  add_ons: string[];
  purchased_on: string;
  trip: {
    destination: string;
    departs_on: string;
    returns_on: string;
    trip_cost: number;
  };
  travelers: string[];
}

export interface PreviousClaim {
  claim_id: string;
  filed_on: string;
  claim_type: string;
  summary: string;
  outcome: string;
  amount_paid: number;
}

export interface Customer {
  customer_id: string;
  name: string;
  email: string;
  customer_since: string;
  previous_claims: PreviousClaim[];
}

export interface Handler {
  handler_id: string;
  name: string;
  role: string;
  can_approve_over_limit: boolean;
}

export interface Step {
  step_id: number;
  kind: StepKind;
  text: string;
  created_at: string;
}

export interface RunClaim {
  claim_id: string;
  summary: string;
  status: Status;
  decision: Verdict | null;
  payout: number | null;
  cost: number;
  seconds: number;
  model_seconds: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface RunSummary {
  approved: number;
  declined: number;
  referred: number;
  awaiting_sign_off: { claim_id: string; payout: number }[];
  left_for_handler: string[];
  in_progress: string[];
}

export interface Run {
  run_id: string;
  agents_at_once: number;
  model: string;
  reasoning_effort: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  claims: RunClaim[];
  model_cost: number;
  work_seconds: number;
  run_seconds: number;
  summary: RunSummary;
}

export interface RunOptions {
  fewest_agents: number;
  most_agents: number;
  starting_agents: number;
}

export const STATUS_LABELS: Record<Status, string> = {
  waiting: "Waiting",
  in_progress: "In progress",
  awaiting_sign_off: "Waiting for sign-off",
  decided: "Decided",
  left_for_handler: "Left for a handler",
};

export const RUN_LABELS: Record<RunStatus, string> = {
  running: "Running",
  finished: "Finished",
  stopped: "Stopped",
};

export const VERDICT_LABELS: Record<Verdict, string> = {
  approve: "Approved",
  decline: "Declined",
  refer: "Referred",
};

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

export function formatMoney(amount: number): string {
  return money.format(amount);
}

const cost = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 4,
});

export function formatCost(amount: number): string {
  return cost.format(amount);
}

export function formatDuration(seconds: number): string {
  const whole = Math.round(seconds);
  const minutes = Math.floor(whole / 60);
  return minutes
    ? `${String(minutes)}m ${String(whole % 60)}s`
    : `${String(whole)}s`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatLabel(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export type DocumentName = "policy_wording" | "claims_manual";

export interface Endpoint<T> {
  path: string;
  read: (response: Response) => Promise<T>;
}

const readJson = (response: Response): Promise<unknown> => response.json();
const readText = (response: Response): Promise<string> => response.text();

function json<T>(path: string): Endpoint<T> {
  return { path, read: readJson as (response: Response) => Promise<T> };
}

function text(path: string): Endpoint<string> {
  return { path, read: readText };
}

export const endpoints = {
  claims: json<ClaimSummary[]>("/api/claims"),
  claim: (claimId: string) => json<Claim>(`/api/claims/${claimId}`),
  steps: (claimId: string) => json<Step[]>(`/api/claims/${claimId}/steps`),
  claimEvaluation: (claimId: string) =>
    json<ClaimEvaluation | null>(`/api/claims/${claimId}/evaluation`),
  policy: (policyId: string) => json<Policy>(`/api/policies/${policyId}`),
  customer: (customerId: string) =>
    json<Customer>(`/api/customers/${customerId}`),
  handlers: json<Handler[]>("/api/handlers"),
  latestDispatch: json<Run | null>("/api/dispatches/latest"),
  runOptions: json<RunOptions>("/api/run-options"),
  instructions: text("/api/instructions"),
  document: (name: DocumentName) => text(`/api/documents/${name}`),
};

export function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

async function check(response: Response): Promise<Response> {
  if (!response.ok) {
    const body = (await response.json()) as { detail: unknown };
    throw new Error(
      typeof body.detail === "string" ? body.detail : response.statusText,
    );
  }
  return response;
}

export async function load<T>(endpoint: Endpoint<T>): Promise<T> {
  return endpoint.read(await check(await fetch(endpoint.path)));
}

async function post(path: string, body?: object): Promise<void> {
  await check(
    await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}

export function runAgent(claimId: string): Promise<void> {
  return post(`/api/claims/${claimId}/run`);
}

export function startDispatch(agentsAtOnce: number): Promise<void> {
  return post("/api/dispatches/start", { agents_at_once: agentsAtOnce });
}

export function signOff(claimId: string, handlerId: string): Promise<void> {
  return post(`/api/claims/${claimId}/sign-off`, { handler_id: handlerId });
}
