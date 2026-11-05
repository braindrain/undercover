import { endpoints, formatTime, type Run } from "./api";
import { Loading, RunChip, StatusChip } from "./components";
import { useResource } from "./hooks";

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
    </>
  );
}

export function RunPanel() {
  const { data: run, error } = useResource(endpoints.latestDispatch, 2000);
  return (
    <section className="panel" aria-label="The agent's run">
      <h2>The agent's run</h2>
      {run === undefined ? <Loading error={error} /> : <RunDetails run={run} />}
    </section>
  );
}
