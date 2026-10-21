import { useState } from "react";
import {
  endpoints,
  errorMessage,
  formatTime,
  signOff as sendSignOff,
  type Claim,
  type Step,
  type StepKind,
} from "./api";
import { Money, VerdictChip } from "./components";
import { useResource } from "./hooks";

const STEP_LABELS: Record<StepKind, string> = {
  tool_call: "Tool call",
  tool_result: "Result",
  read: "Read",
  reply: "Agent",
  error: "Error",
};

function useHandlers() {
  return useResource(endpoints.handlers, null).data ?? [];
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
        Payment blocked: the payout is over the approval limit and waits for a
        handler's sign-off.
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
        <NoDecision claim={claim} />
      )}
    </section>
  );
}

function NoDecision({ claim }: Readonly<{ claim: Claim }>) {
  if (claim.status === "left_for_handler")
    return (
      <p className="alert">
        The agent failed on this claim twice, so it's left for a handler.
      </p>
    );
  if (claim.failures)
    return (
      <p className="muted">
        No decision yet. The agent failed on this claim once, and it's back in
        the queue.
      </p>
    );
  return <p className="muted">No decision yet.</p>;
}

function heading(step: Step): string {
  return step.text.split("\n", 1)[0] ?? "";
}

export function ReadPanel({ steps }: Readonly<{ steps: Step[] }>) {
  const reads = steps.filter((step) => step.kind === "read");
  return (
    <section className="panel" aria-label="What the agent read">
      <h2>What the agent read</h2>
      {reads.length ? (
        <ul className="reads">
          {reads.map((step) => (
            <li key={step.step_id}>{heading(step)}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">The agent hasn't read any sections yet.</p>
      )}
    </section>
  );
}

function StepText({ step }: Readonly<{ step: Step }>) {
  if (step.kind === "tool_result")
    return (
      <details>
        <summary className="mono step-preview">{step.text}</summary>
        <pre className="mono step-text">{step.text}</pre>
      </details>
    );
  if (step.kind === "read")
    return (
      <details>
        <summary className="step-preview">{heading(step)}</summary>
        <pre className="prose step-text">
          {step.text.slice(heading(step).length).trim()}
        </pre>
      </details>
    );
  return (
    <span className={step.kind === "tool_call" ? "mono" : ""}>{step.text}</span>
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
    sendSignOff(claimId, chosen).then(onDone, (reason: unknown) => {
      setError(errorMessage(reason));
    });
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

export function StepsPanel({ steps }: Readonly<{ steps: Step[] }>) {
  return (
    <section className="panel" aria-label="Steps">
      <h2>What the agent did</h2>
      {steps.length ? (
        <ol className="steps">
          {steps.map((step) => (
            <li key={step.step_id} className={`step step-${step.kind}`}>
              <span className="step-label">{STEP_LABELS[step.kind]}</span>
              <StepText step={step} />
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">The agent hasn't worked on this claim yet.</p>
      )}
    </section>
  );
}
