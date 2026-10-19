import { getText } from "./api";
import { Loading } from "./components";
import { useResource } from "./hooks";

export function InstructionsPage() {
  const instructions = useResource("/api/instructions", getText, null);
  const wording = useResource("/api/policy-wording", getText, null);
  if (instructions.data === undefined)
    return <Loading error={instructions.error} />;

  return (
    <>
      <h1>The agent's instructions</h1>
      <p className="muted">
        Read only. The agent works from these instructions, followed by the
        policy wording.
      </p>
      <section className="panel">
        <pre className="prose">{instructions.data}</pre>
      </section>
      <details className="panel">
        <summary>The policy wording</summary>
        <pre className="prose">{wording.data}</pre>
      </details>
    </>
  );
}
