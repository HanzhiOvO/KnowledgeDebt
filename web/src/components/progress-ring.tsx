export function ProgressRing({ value, label }: { value: number; label: string }) {
  const normalized = Math.max(0, Math.min(100, value));
  return (
    <div
      className="progress-ring"
      style={{ "--progress": `${normalized * 3.6}deg` } as React.CSSProperties}
      aria-label={`${label} ${normalized}%`}
    >
      <span>{normalized}</span>
      <small>{label}</small>
    </div>
  );
}
