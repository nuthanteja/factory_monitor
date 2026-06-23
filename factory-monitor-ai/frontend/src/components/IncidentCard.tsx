import type { Incident } from "../lib/api";

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return d.toLocaleString();
}

export function IncidentCard({
  incident,
}: {
  incident: Incident;
}): JSX.Element {
  return (
    <article data-testid="incident-card" data-severity={incident.severity}>
      <header>
        <span data-testid="incident-camera">{incident.camera_id}</span>
        <span data-testid="incident-severity">{incident.severity}</span>
      </header>
      <p data-testid="incident-anomaly">{incident.anomaly_type}</p>
      <p data-testid="incident-status">{incident.status}</p>
      <time dateTime={incident.created_at}>
        {formatTimestamp(incident.created_at)}
      </time>
    </article>
  );
}
