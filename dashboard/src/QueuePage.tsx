import {
  formatLabel,
  getJson,
  STATUS_LABELS,
  type ClaimSummary,
  type Status,
} from "./api";
import { Loading, Money, StatusChip, VerdictChip } from "./components";
import { useResource } from "./hooks";

const STATES = Object.keys(STATUS_LABELS) as Status[];

export function QueuePage() {
  const { data: claims, error } = useResource<ClaimSummary[]>(
    "/api/claims",
    getJson,
    5000,
  );
  if (!claims) return <Loading error={error} />;

  return (
    <>
      <h1>Claims queue</h1>
      <section className="tiles" aria-label="Claims by state">
        {STATES.map((status) => (
          <div className="panel tile" key={status}>
            <span>
              <span className={`dot dot-${status}`} aria-hidden="true" />
              {STATUS_LABELS[status]}
            </span>
            <span className="tile-count">
              {claims.filter((claim) => claim.status === status).length}
            </span>
          </div>
        ))}
      </section>
      <section className="panel">
        <table className="table">
          <thead>
            <tr>
              <th>Claim</th>
              <th>Customer</th>
              <th>Type</th>
              <th>Summary</th>
              <th className="number">Claimed</th>
              <th>Status</th>
              <th>Decision</th>
              <th className="number">Payout</th>
            </tr>
          </thead>
          <tbody>
            {claims.map((claim) => (
              <tr key={claim.claim_id}>
                <td>
                  <a className="mono" href={`#/claims/${claim.claim_id}`}>
                    {claim.claim_id}
                  </a>
                </td>
                <td>{claim.customer_name}</td>
                <td>{formatLabel(claim.claim_type)}</td>
                <td>{claim.summary}</td>
                <td className="number">
                  <Money amount={claim.amount_claimed} />
                </td>
                <td>
                  <StatusChip status={claim.status} />
                </td>
                <td>
                  {claim.decision && <VerdictChip verdict={claim.decision} />}
                </td>
                <td className="number">
                  {claim.payout !== null && <Money amount={claim.payout} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
