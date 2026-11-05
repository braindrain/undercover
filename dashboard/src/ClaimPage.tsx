import { useState } from "react";
import {
  endpoints,
  errorMessage,
  formatLabel,
  runAgent,
  type Claim,
  type ClaimEvaluation,
} from "./api";
import {
  DecisionPanel,
  ReadPanel,
  SignOffForm,
  StepsPanel,
} from "./AgentPanels";
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
    runAgent(claimId).then(onStarted, (reason: unknown) => {
      setStarting(false);
      setError(errorMessage(reason));
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

function CopyNote({ copy }: Readonly<{ copy: ClaimEvaluation }>) {
  return (
    <section className="panel" aria-label="Evaluation">
      <p>
        This is a copy of <span className="mono">{copy.source_claim_id}</span>{" "}
        in{" "}
        <a className="mono" href={`#/scoreboard/${copy.evaluation_id}`}>
          {copy.evaluation_id}
        </a>
        . It's worked by the evaluation alone, so it can't be run or signed off.
      </p>
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
  } = useResource(endpoints.claim(claimId), 2000);
  const live = claim?.status === "waiting" || claim?.status === "in_progress";
  const steps =
    useResource(endpoints.steps(claimId), live ? 2000 : null).data ?? [];
  const { data: copy } = useResource(endpoints.claimEvaluation(claimId), null);
  if (!claim) return <Loading error={error} />;
  const inQueue = copy === null;

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
          {copy && <CopyNote copy={copy} />}
          {inQueue && claim.status === "waiting" && (
            <RunButton claimId={claimId} onStarted={reload} />
          )}
          <DecisionPanel claim={claim} />
          {inQueue && claim.status === "awaiting_sign_off" && (
            <SignOffForm claimId={claimId} onDone={reload} />
          )}
          <ReadPanel steps={steps} />
          <StepsPanel steps={steps} />
        </div>
      </div>
    </>
  );
}
