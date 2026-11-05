import { useState } from "react";
import {
  CLAIM_SET_LABELS,
  endpoints,
  errorMessage,
  formatPercent,
  formatTime,
  startEvaluation,
  type ClaimSet,
  type Evaluation,
  type RunOptions,
} from "./api";
import {
  AgentsSelect,
  Cost,
  Duration,
  Loading,
  Money,
  RunChip,
} from "./components";
import { useResource } from "./hooks";

const CLAIM_SETS = Object.keys(CLAIM_SET_LABELS) as ClaimSet[];

function EvaluationForm({
  options,
  onStarted,
}: Readonly<{ options: RunOptions; onStarted: () => void }>) {
  const [agents, setAgents] = useState(options.starting_agents);
  const [claimSet, setClaimSet] = useState<ClaimSet>("evaluation");
  const [tiny, setTiny] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string>();

  const start = () => {
    setStarting(true);
    setError(undefined);
    startEvaluation(agents, claimSet, tiny)
      .then(onStarted, (reason: unknown) => {
        setError(errorMessage(reason));
      })
      .finally(() => {
        setStarting(false);
      });
  };

  return (
    <section className="panel" aria-label="New evaluation">
      <h2>New evaluation</h2>
      <div className="form-row">
        <AgentsSelect options={options} value={agents} onChange={setAgents} />
        <label>
          Set{" "}
          <select
            value={claimSet}
            onChange={(event) => {
              setClaimSet(event.target.value as ClaimSet);
            }}
          >
            {CLAIM_SETS.map((name) => (
              <option key={name} value={name}>
                {CLAIM_SET_LABELS[name]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Claims{" "}
          <select
            value={tiny ? "tiny" : "whole"}
            onChange={(event) => {
              setTiny(event.target.value === "tiny");
            }}
          >
            <option value="whole">The whole set</option>
            <option value="tiny">Its five tiny claims</option>
          </select>
        </label>
        <button
          className="button"
          type="button"
          onClick={start}
          disabled={starting}
        >
          Start an evaluation
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

function EvaluationRow({ run }: Readonly<{ run: Evaluation }>) {
  const { measures } = run;
  return (
    <tr>
      <td>
        <a className="mono" href={`#/scoreboard/${run.run_id}`}>
          {run.run_id}
        </a>
        <div className="muted">{formatTime(run.started_at)}</div>
      </td>
      <td>
        {CLAIM_SET_LABELS[run.claim_set]}
        {run.tiny ? ", tiny" : ""}
        <div className="muted">
          <span className="mono">{run.claim_count}</span> claims,{" "}
          <span className="mono">{run.agents_at_once}</span> at once
        </div>
      </td>
      <td>
        <RunChip status={run.status} />
      </td>
      <td className="number mono">
        {formatPercent(measures.decision_accuracy)}
      </td>
      <td className="number mono">{formatPercent(measures.accuracy)}</td>
      <td className="number">
        <Money amount={measures.cost_of_mistakes} />
      </td>
      <td className="number mono">
        {formatPercent(measures.straight_through_rate)}
      </td>
      <td className="number">
        <Cost amount={measures.cost_per_claim} />
      </td>
      <td className="number">
        <Duration seconds={measures.seconds_per_claim} />
      </td>
      <td className="number">
        <Duration seconds={measures.run_seconds} />
      </td>
    </tr>
  );
}

function EvaluationList({ runs }: Readonly<{ runs: Evaluation[] }>) {
  if (!runs.length)
    return (
      <section className="panel">
        <p className="muted">No evaluation has run yet.</p>
      </section>
    );
  return (
    <section className="panel table-scroll" aria-label="Evaluations">
      <table className="table">
        <thead>
          <tr>
            <th>Evaluation</th>
            <th>Set</th>
            <th>State</th>
            <th className="number">Decision accuracy</th>
            <th className="number">Accuracy</th>
            <th className="number">Cost of mistakes</th>
            <th className="number">Straight through</th>
            <th className="number">Cost per claim</th>
            <th className="number">Time per claim</th>
            <th className="number">Whole run</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <EvaluationRow key={run.run_id} run={run} />
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function ScoreboardPage() {
  const {
    data: runs,
    error,
    reload,
  } = useResource(endpoints.evaluations, 5000);
  const options = useResource(endpoints.runOptions, null);
  if (!runs || !options.data) return <Loading error={error ?? options.error} />;

  return (
    <>
      <h1>Scoreboard</h1>
      <EvaluationForm options={options.data} onStarted={reload} />
      <EvaluationList runs={runs} />
    </>
  );
}
