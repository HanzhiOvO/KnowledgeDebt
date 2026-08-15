import Link from "next/link";

export function EmptyState({
  eyebrow,
  title,
  detail,
  actionHref,
  actionLabel,
}: {
  eyebrow: string;
  title: string;
  detail: string;
  actionHref?: string;
  actionLabel?: string;
}) {
  return (
    <section className="empty-state panel">
      <span className="eyebrow">{eyebrow}</span>
      <h1>{title}</h1>
      <p>{detail}</p>
      {actionHref && actionLabel ? (
        <Link className="button primary" href={actionHref}>
          {actionLabel}
        </Link>
      ) : null}
    </section>
  );
}
