import type { ServerClock } from "../lib/serverClock";
import type { IncidentView } from "../lib/wsContract";
import { CountdownTimer } from "./CountdownTimer";

const TERMINAL = new Set(["RESOLVED", "CRITICAL_UNRESOLVED", "ACK"]);

export function LiveIncidentCard({
  incident,
  clock,
  onAcknowledge,
  onResolve,
  busy = false,
}: {
  incident: IncidentView;
  clock: ServerClock;
  onAcknowledge: (id: string) => void;
  onResolve: (id: string) => void;
  busy?: boolean;
}): JSX.Element {
  const terminal = TERMINAL.has(incident.status);
  const disabled = terminal || busy;

  return (
    <article
      data-testid="live-incident-card"
      data-severity={incident.severity}
      data-status={incident.status}
    >
      <header>
        <span data-testid="tier-label" data-tier={incident.current_tier}>
          {incident.tier_label}
        </span>
        <span data-testid="incident-camera">{incident.camera_id}</span>
        {incident.zone_id && (
          <span data-testid="incident-zone">{incident.zone_id}</span>
        )}
        <span data-testid="incident-severity">{incident.severity}</span>
      </header>
      <p data-testid="incident-anomaly">{incident.anomaly_type}</p>
      <p data-testid="incident-status">{incident.status}</p>
      <CountdownTimer
        deadlineAt={incident.deadline_at}
        clock={clock}
        terminal={terminal}
      />
      <div>
        <button
          type="button"
          data-testid="ack-button"
          disabled={disabled}
          onClick={() => onAcknowledge(incident.incident_id)}
        >
          Acknowledge
        </button>
        <button
          type="button"
          data-testid="resolve-button"
          disabled={disabled}
          onClick={() => onResolve(incident.incident_id)}
        >
          Resolve
        </button>
      </div>
    </article>
  );
}
