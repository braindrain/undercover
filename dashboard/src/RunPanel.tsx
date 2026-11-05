import { useState } from "react";
import {
  endpoints,
  errorMessage,
  formatTime,
  startDispatch,
  type Run,
  type RunOptions,
  type RunSummary,
} from "./api";
import {
  AgentsSelect,
  Cost,
  Duration,
  Loading,
  Money,
  RunChip,
  StatusChip,
} from "./components";
import { useResource } from "./hooks";

function DispatchForm({ options }: Readonly<{ options: RunOptions }>) {
  const [agents, setAgents] = useState(options.starting_agents);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();

  const start = () => {
    setStarting(true);
    setError(undefined);
    startDispatch(agents)
      .catch((reason: unknown) => {
        setError(errorMessage(reason));
      })
      .finally(() => {
        setStarting(false);
      });
  };

  return (
    <div className="run-form">
      <div className="form-row">
        <AgentsSelect options={options} value={agents} onChange={setAgents} />
        <button
          className="button"
          type="button"
          onClick={start}
          disabled={starting}
        >
          Start a dispatch
        </button>
      </div>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

function ClaimLinks({ claimIds }: Readonly<{ claimIds: string[] }>) {
  return (
    <ul className="run-held">
      {claimIds.map((claimId) => (
        <li key={claimId}>
          <a className="mono" href={`#/claims/${claimId}`}>
            {claimId}
          </a>
        </li>
      ))}
    </ul>
  );
}

function Summary({ summary }: Readonly<{ summary: RunSummary }>) {
  return (
    <section aria-label="Summary">
      <h3>Summary</h3>
      <dl className="facts">
        <dt>Approved</dt>
        <dd className="mono">{summary.approved}</dd>
        <dt>Declined</dt>
        <dd className="mono">{summary.declined}</dd>
        <dt>Referred</dt>
        <dd className="mono">{summary.referred}</dd>
        {summary.awaiting_sign_off.length > 0 && (
          <>
            <dt>Waiting for sign-off</dt>
            <dd>
              <ul className="run-held">
                {summary.awaiting_sign_off.map((held) => (
                  <li key={held.claim_id}>
                    <a className="mono" href={`#/claims/${held.claim_id}`}>
                      {held.claim_id}
                    </a>{" "}
                    <Money amount={held.payout} />
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {summary.left_for_handler.length > 0 && (
          <>
            <dt>Left for a handler</dt>
            <dd>
              <ClaimLinks claimIds={summary.left_for_handler} />
            </dd>
          </>
        )}
        {summary.in_progress.length > 0 && (
          <>
            <dt>Still in progress</dt>
            <dd>
              <ClaimLinks claimIds={summary.in_progress} />
            </dd>
          </>
        )}
      </dl>
    </section>
  );
}

function RunClaims({ run }: Readonly<{ run: Run }>) {
  if (!run.claims.length)
    return <p className="muted">This dispatch had no claims to work.</p>;
  return (
    <ol className="run-claims">
      {run.claims.map((claim) => (
        <li className="run-claim" key={claim.claim_id}>
          <StatusChip status={claim.status} />
          <a className="mono" href={`#/claims/${claim.claim_id}`}>
            {claim.claim_id}
          </a>
          <span>{claim.summary}</span>
          <Cost amount={claim.cost} />
          <Duration seconds={claim.seconds} />
        </li>
      ))}
    </ol>
  );
}

function RunDetails({ run }: Readonly<{ run: Run | null }>) {
  if (run === null)
    return <p className="muted">The agent hasn't worked the queue yet.</p>;
  return (
    <>
      <dl className="facts">
        <dt>State</dt>
        <dd>
          <RunChip status={run.status} />
        </dd>
        <dt>At once</dt>
        <dd>
          <span className="mono">{run.agents_at_once}</span> claim agents
        </dd>
        <dt>Model cost</dt>
        <dd>
          <Cost amount={run.model_cost} />
        </dd>
        <dt>Time</dt>
        <dd>
          <Duration seconds={run.run_seconds} />
        </dd>
        <dt>Started</dt>
        <dd>{formatTime(run.started_at)}</dd>
        {run.finished_at && (
          <>
            <dt>Ended</dt>
            <dd>{formatTime(run.finished_at)}</dd>
          </>
        )}
      </dl>
      <RunClaims run={run} />
      {run.status !== "running" && <Summary summary={run.summary} />}
    </>
  );
}

export function RunPanel() {
  const { data: run, error } = useResource(endpoints.latestDispatch, 2000);
  const options = useResource(endpoints.runOptions, null);
  return (
    <section className="panel" aria-label="The agent's run">
      <h2>The agent's run</h2>
      {options.data ? (
        <DispatchForm options={options.data} />
      ) : (
        <Loading error={options.error} />
      )}
      {run === undefined ? <Loading error={error} /> : <RunDetails run={run} />}
    </section>
  );
}
