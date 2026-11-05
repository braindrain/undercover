import {
  formatCost,
  formatDuration,
  formatMoney,
  RUN_LABELS,
  STATUS_LABELS,
  VERDICT_LABELS,
  type RunOptions,
  type RunStatus,
  type Status,
  type Verdict,
} from "./api";

export function StatusChip({ status }: Readonly<{ status: Status }>) {
  return <span className={`chip chip-${status}`}>{STATUS_LABELS[status]}</span>;
}

export function RunChip({ status }: Readonly<{ status: RunStatus }>) {
  return <span className={`chip chip-${status}`}>{RUN_LABELS[status]}</span>;
}

export function VerdictChip({ verdict }: Readonly<{ verdict: Verdict }>) {
  return (
    <span className={`chip chip-${verdict}`}>{VERDICT_LABELS[verdict]}</span>
  );
}

export function Money({ amount }: Readonly<{ amount: number }>) {
  return <span className="mono">{formatMoney(amount)}</span>;
}

export function Cost({ amount }: Readonly<{ amount: number }>) {
  return <span className="mono">{formatCost(amount)}</span>;
}

export function Duration({ seconds }: Readonly<{ seconds: number }>) {
  return <span className="mono">{formatDuration(seconds)}</span>;
}

export function AgentsSelect({
  options,
  value,
  onChange,
}: Readonly<{
  options: RunOptions;
  value: number;
  onChange: (value: number) => void;
}>) {
  const { fewest_agents: fewest, most_agents: most } = options;
  const choices = Array.from(
    { length: most - fewest + 1 },
    (_, index) => fewest + index,
  );
  return (
    <label>
      Claim agents at once{" "}
      <select
        value={value}
        onChange={(event) => {
          onChange(Number(event.target.value));
        }}
      >
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    </label>
  );
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
