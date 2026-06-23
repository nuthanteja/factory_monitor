import type { Incident } from "../lib/api";
import { IncidentCard } from "./IncidentCard";

export function IncidentList({
  incidents,
}: {
  incidents: Incident[];
}): JSX.Element {
  if (incidents.length === 0) {
    return (
      <p data-testid="incident-empty" role="status">
        No active incidents
      </p>
    );
  }
  return (
    <section data-testid="incident-list">
      {incidents.map((incident) => (
        <IncidentCard key={incident.id} incident={incident} />
      ))}
    </section>
  );
}
