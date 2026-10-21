import { getText } from "./api";
import { Loading } from "./components";
import { useResource } from "./hooks";

export function InstructionsPage() {
  const queue = useResource("/api/instructions/queue", getText, null);
  const claim = useResource("/api/instructions/claim", getText, null);
  const wording = useResource("/api/documents/policy_wording", getText, null);
  const manual = useResource("/api/documents/claims_manual", getText, null);
  if (queue.data === undefined || claim.data === undefined)
    return <Loading error={queue.error ?? claim.error} />;

  return (
    <>
      <h1>The agent's instructions</h1>
      <p className="muted">
        Read only. The agent runs the queue with the first set of instructions
        and hands each claim to a fresh claim agent, which works from the
        second. It looks up what it needs in the policy wording and the claims
        manual below.
      </p>
      <section className="panel" aria-label="Running the queue">
        <pre className="prose">{queue.data}</pre>
      </section>
      <section className="panel" aria-label="Working a claim">
        <pre className="prose">{claim.data}</pre>
      </section>
      <details className="panel">
        <summary>The policy wording</summary>
        <pre className="prose">{wording.data}</pre>
      </details>
      <details className="panel">
        <summary>The claims manual</summary>
        <pre className="prose">{manual.data}</pre>
      </details>
    </>
  );
}
