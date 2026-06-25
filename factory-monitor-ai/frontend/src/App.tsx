import { useState } from "react";
import { useLiveIncidents, type WsFactory } from "./hooks/useLiveIncidents";
import { useIncidentActions } from "./hooks/useIncidentActions";
import { LiveIncidentCard } from "./components/LiveIncidentCard";
import { CameraWall } from "./components/CameraWall";

type View = "incidents" | "cameras";

export default function App({
  wsFactory,
}: {
  wsFactory?: WsFactory;
} = {}): JSX.Element {
  const [view, setView] = useState<View>("incidents");
  const live = useLiveIncidents(wsFactory ? { wsFactory } : {});
  const { acknowledge, resolve } = useIncidentActions();

  const busy = acknowledge.isPending || resolve.isPending;

  return (
    <main>
      <h1>Factory Monitor — Command Center</h1>

      <div role="tablist" aria-label="View">
        <button
          role="tab"
          aria-selected={view === "incidents"}
          onClick={() => setView("incidents")}
          data-testid="tab-incidents"
        >
          Incidents
        </button>
        <button
          role="tab"
          aria-selected={view === "cameras"}
          onClick={() => setView("cameras")}
          data-testid="tab-cameras"
        >
          Cameras
        </button>
      </div>

      {view === "incidents" && (
        <>
          <span
            data-testid="connection-pill"
            data-connected={live.connected}
            role="status"
          >
            {live.connected ? "LIVE" : "RECONNECTING…"}
          </span>

          {live.incidents.length === 0 ? (
            <p data-testid="incident-empty" role="status">
              No active incidents
            </p>
          ) : (
            <section data-testid="live-incident-list">
              {live.incidents.map((incident) => (
                <LiveIncidentCard
                  key={incident.incident_id}
                  incident={incident}
                  clock={live.clock}
                  onAcknowledge={(id) => acknowledge.mutate({ id })}
                  onResolve={(id) => resolve.mutate({ id })}
                  busy={busy}
                />
              ))}
            </section>
          )}
        </>
      )}

      {view === "cameras" && <CameraWall />}
    </main>
  );
}
