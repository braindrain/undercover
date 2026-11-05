import { endpoints } from "./api";
import { Loading } from "./components";
import { useResource } from "./hooks";

export function InstructionsPage() {
  const claim = useResource(endpoints.instructions, null);
  const wording = useResource(endpoints.document("policy_wording"), null);
  const manual = useResource(endpoints.document("claims_manual"), null);
  if (claim.data === undefined) return <Loading error={claim.error} />;

  return (
    <>
      <h1>The agent's instructions</h1>
      <p className="muted">
        Read only. Each claim agent works one claim from these instructions, and
        looks up what it needs in the policy wording and the claims manual
        below.
      </p>
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
