import { useIncidents } from "./hooks/useIncidents";
import { IncidentList } from "./components/IncidentList";

export default function App(): JSX.Element {
  const { data, isPending, isError, error } = useIncidents();

  return (
    <main>
      <h1>Factory Monitor — Command Center</h1>
      {isPending && <p role="status">Loading incidents…</p>}
      {isError && (
        <p role="alert">Failed to load incidents: {error.message}</p>
      )}
      {data && <IncidentList incidents={data.incidents} />}
    </main>
  );
}
