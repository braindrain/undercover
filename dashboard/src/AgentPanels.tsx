import { useState } from "react";
import {
  formatMoney,
  getJson,
  post,
  type Claim,
  type Handler,
  type Step,
  type StepKind,
} from "./api";
import { Money, VerdictChip } from "./components";
import { useResource } from "./hooks";

const APPROVAL_LIMIT = 1000;

const STEP_LABELS: Record<StepKind, string> = {
  tool_call: "Tool call",
  tool_result: "Result",
  reply: "Agent",
  error: "Error",
};

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function useHandlers() {
  return useResource<Handler[]>("/api/handlers", getJson, null).data ?? [];
}

function Payment({ claim }: Readonly<{ claim: Claim }>) {
  const handlers = useHandlers();
  if (claim.payment) {
    const handler = handlers.find(
      (candidate) => candidate.handler_id === claim.sign_off?.handler_id,
    );
    return (
      <p>
        Paid <Money amount={claim.payment.amount} /> on{" "}
        {formatTime(claim.payment.paid_at)}
        {handler && `, signed off by ${handler.name}`}.
      </p>
    );
  }
  if (claim.status === "awaiting_sign_off") {
    return (
      <p className="alert">
        Payment blocked: the payout is over the {formatMoney(APPROVAL_LIMIT)}{" "}
        approval limit and waits for a handler's sign-off.
      </p>
    );
  }
  return <p className="muted">Not paid.</p>;
}

export function DecisionPanel({ claim }: Readonly<{ claim: Claim }>) {
  const { decision } = claim;
  return (
    <section className="panel" aria-label="Decision">
      <h2>The agent's decision</h2>
      {decision ? (
        <>
          <p className="decision-line">
            <VerdictChip verdict={decision.decision} />
            <Money amount={decision.payout} />
          </p>
          <p>{decision.reasons}</p>
          <p className="muted">{decision.policy_section}</p>
          {decision.decision === "approve" && <Payment claim={claim} />}
        </>
      ) : (
        <p className="muted">No decision yet.</p>
      )}
    </section>
  );
}

export function SignOffForm({
  claimId,
  onDone,
}: Readonly<{ claimId: string; onDone: () => void }>) {
  const handlers = useHandlers().filter(
    (handler) => handler.can_approve_over_limit,
  );
  const [handlerId, setHandlerId] = useState("");
  const [error, setError] = useState<string>();
  const chosen = handlerId || handlers[0]?.handler_id;

  const signOff = () => {
    if (!chosen) return;
    post(`/api/claims/${claimId}/sign-off`, { handler_id: chosen }).then(
      onDone,
      (reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
  };

  return (
    <section className="panel" aria-label="Sign-off">
      <h2>Sign-off</h2>
      <div className="form-row">
        <label>
          Handler{" "}
          <select
            value={chosen}
            onChange={(event) => {
              setHandlerId(event.target.value);
            }}
          >
            {handlers.map((handler) => (
              <option key={handler.handler_id} value={handler.handler_id}>
                {handler.name}
              </option>
            ))}
          </select>
        </label>
        <button className="button" type="button" onClick={signOff}>
          Sign off and pay
        </button>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export function StepsPanel({
  claimId,
  live,
}: Readonly<{ claimId: string; live: boolean }>) {
  const { data: steps } = useResource<Step[]>(
    `/api/claims/${claimId}/steps`,
    getJson,
    live ? 2000 : null,
  );
  return (
    <section className="panel" aria-label="Steps">
      <h2>What the agent did</h2>
      {steps?.length ? (
        <ol className="steps">
          {steps.map((step) => (
            <li key={step.step_id} className={`step step-${step.kind}`}>
              <span className="step-label">{STEP_LABELS[step.kind]}</span>
              {step.kind === "tool_result" ? (
                <details>
                  <summary className="mono step-preview">{step.text}</summary>
                  <pre className="mono step-text">{step.text}</pre>
                </details>
              ) : (
                <span className={step.kind === "tool_call" ? "mono" : ""}>
                  {step.text}
                </span>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">The agent hasn't worked on this claim yet.</p>
      )}
    </section>
  );
}
