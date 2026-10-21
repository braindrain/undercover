import { endpoints, formatTime, type Run } from "./api";
import { Loading, TodoChip } from "./components";
import { useResource } from "./hooks";

function TodoList({ run }: Readonly<{ run: Run }>) {
  if (!run.todos.length)
    return (
      <p className="muted">The agent hasn't written its to-do list yet.</p>
    );
  const worked = run.todos.filter(
    (todo) => todo.status === "done" || todo.status === "left_for_handler",
  ).length;
  return (
    <>
      <h3>
        To-do list{" "}
        <span className="muted">
          {worked} of {run.todos.length} worked
        </span>
      </h3>
      <ol className="todos">
        {run.todos.map((todo, position) => (
          <li className="todo" key={position}>
            <TodoChip status={todo.status} />
            <span>{todo.text}</span>
          </li>
        ))}
      </ol>
    </>
  );
}

function RunDetails({ run }: Readonly<{ run: Run | null }>) {
  if (run === null)
    return (
      <p className="muted">
        The agent hasn't run on the queue yet. A run starts from the terminal,
        with <span className="mono">uv run -m agent --cap 5</span>.
      </p>
    );
  return (
    <>
      <dl className="facts">
        <dt>State</dt>
        <dd>
          <span className={`chip chip-${run.status}`}>
            {run.status === "running" ? "Running" : "Finished"}
          </span>
        </dd>
        <dt>Cap</dt>
        <dd>
          <span className="mono">{run.cap}</span> claims
        </dd>
        <dt>Started</dt>
        <dd>{formatTime(run.started_at)}</dd>
        {run.finished_at && (
          <>
            <dt>Finished</dt>
            <dd>{formatTime(run.finished_at)}</dd>
          </>
        )}
      </dl>
      <TodoList run={run} />
      {run.summary && (
        <>
          <h3>Summary</h3>
          <p className="prose">{run.summary}</p>
        </>
      )}
    </>
  );
}

export function RunPanel() {
  const { data: run, error } = useResource(endpoints.latestRun, 2000);
  return (
    <section className="panel" aria-label="The agent's run">
      <h2>The agent's run</h2>
      {run === undefined ? <Loading error={error} /> : <RunDetails run={run} />}
    </section>
  );
}
