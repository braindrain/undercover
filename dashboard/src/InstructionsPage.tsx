import { endpoints } from "./api";
import { Loading } from "./components";
import { useResource } from "./hooks";

export function InstructionsPage() {
  const queue = useResource(endpoints.instructions("queue"), null);
  const claim = useResource(endpoints.instructions("claim"), null);
  const wording = useResource(endpoints.document("policy_wording"), null);
  const manual = useResource(endpoints.document("claims_manual"), null);
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
