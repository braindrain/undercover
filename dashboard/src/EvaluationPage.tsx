import {
  CLAIM_SET_LABELS,
  endpoints,
  formatPercent,
  formatTime,
  type EvaluationClaim,
  type EvaluationDetails,
  type Verdict,
} from "./api";
import {
  Cost,
  Duration,
  Loading,
  Money,
  RunChip,
  VerdictChip,
} from "./components";
import { useResource } from "./hooks";

function Answer({
  verdict,
  payout,
}: Readonly<{ verdict: Verdict | null; payout: number | null }>) {
  if (verdict === null) return <span className="muted">No decision</span>;
  return (
    <span className="answer">
      <VerdictChip verdict={verdict} />
      {payout !== null && <Money amount={payout} />}
    </span>
  );
}

function ClaimRow({ claim }: Readonly<{ claim: EvaluationClaim }>) {
  return (
    <tr>
      <td>
        <a className="mono" href={`#/claims/${claim.claim_id}`}>
          {claim.claim_id}
        </a>
        <div className="muted mono">{claim.source_claim_id}</div>
      </td>
      <td>
        {claim.summary}
        {claim.reason && <p className="muted reason">{claim.reason}</p>}
      </td>
      <td>
        <Answer
          verdict={claim.handler_decision}
          payout={claim.handler_payout}
        />
      </td>
      <td>
        <Answer verdict={claim.agent_decision} payout={claim.agent_payout} />
      </td>
      <td>{claim.miss && <span className="chip chip-miss">Miss</span>}</td>
      <td className="number">
        <Money amount={claim.mistake_cost} />
      </td>
      <td className="number">
        <Cost amount={claim.cost} />
      </td>
      <td className="number">
        <Duration seconds={claim.seconds} />
      </td>
    </tr>
  );
}

function Measures({ run }: Readonly<{ run: EvaluationDetails }>) {
  const { measures } = run;
  return (
    <dl className="facts">
      <dt>Decision accuracy</dt>
      <dd className="mono">{formatPercent(measures.decision_accuracy)}</dd>
      <dt>Accuracy</dt>
      <dd className="mono">{formatPercent(measures.accuracy)}</dd>
      <dt>Cost of mistakes</dt>
      <dd>
        <Money amount={measures.cost_of_mistakes} />
      </dd>
      <dt>Straight through</dt>
      <dd className="mono">{formatPercent(measures.straight_through_rate)}</dd>
      <dt>Cost per claim</dt>
      <dd>
        <Cost amount={measures.cost_per_claim} />
      </dd>
      <dt>Time per claim</dt>
      <dd>
        <Duration seconds={measures.seconds_per_claim} />
      </dd>
      <dt>Whole run</dt>
      <dd>
        <Duration seconds={measures.run_seconds} />
      </dd>
    </dl>
  );
}

export function EvaluationPage({ runId }: Readonly<{ runId: string }>) {
  const { data: run, error } = useResource(endpoints.evaluation(runId), 5000);
  if (!run) return <Loading error={error} />;
  const misses = run.claims.filter((claim) => claim.miss).length;

  return (
    <>
      <h1>
        <span className="mono claim-id">{run.run_id}</span>
      </h1>
      <section className="panel" aria-label="This evaluation">
        <dl className="facts">
          <dt>State</dt>
          <dd>
            <RunChip status={run.status} />
          </dd>
          <dt>Set</dt>
          <dd>
            {CLAIM_SET_LABELS[run.claim_set]}
            {run.tiny ? ", tiny" : ""}
          </dd>
          <dt>At once</dt>
          <dd>
            <span className="mono">{run.agents_at_once}</span> claim agents
          </dd>
          <dt>Model</dt>
          <dd className="mono">{run.model}</dd>
          <dt>Reasoning effort</dt>
          <dd className="mono">{run.reasoning_effort}</dd>
          <dt>Started</dt>
          <dd>{formatTime(run.started_at)}</dd>
        </dl>
      </section>
      <section className="panel" aria-label="Measures">
        <h2>Measures</h2>
        <Measures run={run} />
      </section>
      <section className="panel table-scroll" aria-label="Claims">
        <h2>
          Claims{" "}
          <span className="muted">
            {misses} of {run.claims.length} missed
          </span>
        </h2>
        <table className="table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Summary</th>
              <th>Handler</th>
              <th>Agent</th>
              <th>Miss</th>
              <th className="number">Mistake</th>
              <th className="number">Cost</th>
              <th className="number">Time</th>
            </tr>
          </thead>
          <tbody>
            {run.claims.map((claim) => (
              <ClaimRow key={claim.claim_id} claim={claim} />
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
