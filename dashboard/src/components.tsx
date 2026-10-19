import {
  formatMoney,
  STATUS_LABELS,
  VERDICT_LABELS,
  type Status,
  type Verdict,
} from "./api";

export function StatusChip({ status }: Readonly<{ status: Status }>) {
  return <span className={`chip chip-${status}`}>{STATUS_LABELS[status]}</span>;
}

export function VerdictChip({ verdict }: Readonly<{ verdict: Verdict }>) {
  return (
    <span className={`chip chip-${verdict}`}>{VERDICT_LABELS[verdict]}</span>
  );
}

export function Money({ amount }: Readonly<{ amount: number }>) {
  return <span className="mono">{formatMoney(amount)}</span>;
}

export function Loading({ error }: Readonly<{ error: string | undefined }>) {
  return error ? (
    <p className="alert" role="alert">
      {error}
    </p>
  ) : (
    <p className="muted">Loading</p>
  );
}
