import { useState } from "react";
import { formatLabel, getJson, post, type Claim } from "./api";
import { DecisionPanel, SignOffForm, StepsPanel } from "./AgentPanels";
import { CustomerPanel, PolicyPanel } from "./ClaimRecords";
import { Loading, Money, StatusChip } from "./components";
import { useResource } from "./hooks";

function RunButton({
  claimId,
  onStarted,
}: Readonly<{ claimId: string; onStarted: () => void }>) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();

  const run = () => {
    setStarting(true);
    post(`/api/claims/${claimId}/run`).then(onStarted, (reason: unknown) => {
      setStarting(false);
      setError(reason instanceof Error ? reason.message : String(reason));
    });
  };

  return (
    <section className="panel" aria-label="Agent">
      <h2>Ready for the agent</h2>
      <div className="form-row">
        <button
          className="button"
          type="button"
          onClick={run}
          disabled={starting}
        >
          Run the agent
        </button>
        <span className="muted">
          {starting ? "Starting" : "The agent decides this claim."}
        </span>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function ClaimDetails({ claim }: Readonly<{ claim: Claim }>) {
  return (
    <>
      <section className="panel" aria-label="Claim">
        <dl className="facts">
          <dt>Type</dt>
          <dd>{formatLabel(claim.claim_type)}</dd>
          <dt>Incident</dt>
          <dd>
            <span className="mono">{claim.incident_date}</span>,{" "}
            {claim.incident_location}
          </dd>
          <dt>Submitted</dt>
          <dd className="mono">{claim.submitted_on}</dd>
        </dl>
        <h3>The customer's story</h3>
        <p className="prose">{claim.story}</p>
        <h3>Items claimed</h3>
        <table className="table">
          <tbody>
            {claim.items.map((item) => (
              <tr key={item.description}>
                <td>{item.description}</td>
                <td>{formatLabel(item.category)}</td>
                <td className="number">
                  <Money amount={item.amount} />
                </td>
              </tr>
            ))}
            <tr>
              <th scope="row" colSpan={2}>
                Total claimed
              </th>
              <td className="number">
                <Money amount={claim.amount_claimed} />
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <section className="panel" aria-label="Documents">
        <h2>Documents</h2>
        {claim.documents.map((document) => (
          <article className="document" key={document.name}>
            <h3>
              {document.name}{" "}
              <span className="muted">{formatLabel(document.type)}</span>
            </h3>
            <p>{document.content}</p>
          </article>
        ))}
      </section>
    </>
  );
}

export function ClaimPage({ claimId }: Readonly<{ claimId: string }>) {
  const {
    data: claim,
    error,
    reload,
  } = useResource<Claim>(`/api/claims/${claimId}`, getJson, 2000);
  if (!claim) return <Loading error={error} />;
  const live = claim.status === "waiting" || claim.status === "in_progress";

  return (
    <>
      <header className="claim-header">
        <h1>
          <span className="mono claim-id">{claim.claim_id}</span>{" "}
          {claim.summary}
        </h1>
        <StatusChip status={claim.status} />
      </header>
      <div className="claim-layout">
        <div className="column">
          <ClaimDetails claim={claim} />
          <PolicyPanel policyId={claim.policy_id} />
          <CustomerPanel customerId={claim.customer_id} />
        </div>
        <div className="column">
          {claim.status === "waiting" && (
            <RunButton claimId={claimId} onStarted={reload} />
          )}
          <DecisionPanel claim={claim} />
          {claim.status === "awaiting_sign_off" && (
            <SignOffForm claimId={claimId} onDone={reload} />
          )}
          <StepsPanel claimId={claimId} live={live} />
        </div>
      </div>
    </>
  );
}
