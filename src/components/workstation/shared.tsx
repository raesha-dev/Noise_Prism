import type { ReactNode } from "react";

export function WaveformPath({ peaks }: { peaks: number[] }) {
  const d = peaks
    .map((peak, index) => {
      const height = Math.max(1.5, Math.min(46, peak * 44));
      const x = (index / Math.max(1, peaks.length - 1)) * 900;
      return `M ${x.toFixed(1)} ${(24 - height / 2).toFixed(1)} v ${height.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg viewBox="0 0 900 48" preserveAspectRatio="none" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function SourceTag({
  kind,
}: {
  kind: "service" | "estimate" | "sample" | "demo" | "imported" | "empty";
}) {
  const label = {
    service: "Service result",
    estimate: "Browser estimate",
    sample: "Illustrative",
    demo: "Demo",
    imported: "Imported",
    empty: "No results",
  }[kind];
  return <span className={`source-tag source-${kind}`}>{label}</span>;
}

export function Metric({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  className = "",
  disabled = false,
}: {
  value: T;
  options: { value: T; label: ReactNode }[];
  onChange: (value: T) => void;
  label: string;
  className?: string | undefined;
  disabled?: boolean | undefined;
}) {
  return (
    <div className={`segmented ${className}`} role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          className={value === option.value ? "active" : ""}
          onClick={() => onChange(option.value)}
          disabled={disabled}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
