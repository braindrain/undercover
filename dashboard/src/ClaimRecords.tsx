import { formatLabel, getJson, type Customer, type Policy } from "./api";
import { Loading, Money } from "./components";
import { useResource } from "./hooks";

export function PolicyPanel({ policyId }: Readonly<{ policyId: string }>) {
  const { data: policy, error } = useResource<Policy>(
    `/api/policies/${policyId}`,
    getJson,
    null,
  );
  return (
    <section className="panel" aria-label="Policy">
      <h2>
        Policy <span className="mono muted">{policyId}</span>
      </h2>
      {policy ? (
        <dl className="facts">
          <dt>Plan</dt>
          <dd>{formatLabel(policy.plan)}</dd>
          <dt>Add-ons</dt>
          <dd>
            {policy.add_ons.length
              ? policy.add_ons.map(formatLabel).join(", ")
              : "None"}
          </dd>
          <dt>Bought</dt>
          <dd className="mono">{policy.purchased_on}</dd>
          <dt>Trip</dt>
          <dd>
            {policy.trip.destination},{" "}
            <span className="mono">
              {policy.trip.departs_on} to {policy.trip.returns_on}
            </span>
          </dd>
          <dt>Trip cost</dt>
          <dd>
            <Money amount={policy.trip.trip_cost} />
          </dd>
          <dt>Travelers</dt>
          <dd>{policy.travelers.join(", ")}</dd>
        </dl>
      ) : (
        <Loading error={error} />
      )}
    </section>
  );
}

export function CustomerPanel({
  customerId,
}: Readonly<{ customerId: string }>) {
  const { data: customer, error } = useResource<Customer>(
    `/api/customers/${customerId}`,
    getJson,
    null,
  );
  if (!customer) return <Loading error={error} />;

  return (
    <section className="panel" aria-label="Customer">
      <h2>
        {customer.name} <span className="mono muted">{customerId}</span>
      </h2>
      <p className="muted">
        Customer since <span className="mono">{customer.customer_since}</span>
      </p>
      <h3>Earlier claims</h3>
      {customer.previous_claims.length ? (
        <table className="table">
          <tbody>
            {customer.previous_claims.map((claim) => (
              <tr key={claim.claim_id}>
                <td className="mono">{claim.claim_id}</td>
                <td className="mono">{claim.filed_on}</td>
                <td>{claim.summary}</td>
                <td>{formatLabel(claim.outcome)}</td>
                <td className="number">
                  <Money amount={claim.amount_paid} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">None</p>
      )}
    </section>
  );
}
