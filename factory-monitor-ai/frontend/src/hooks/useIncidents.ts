import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getIncidents, type IncidentsResponse } from "../lib/api";

export const INCIDENTS_QUERY_KEY = ["incidents"] as const;
export const INCIDENTS_POLL_MS = 2000;

export function useIncidents(): UseQueryResult<IncidentsResponse, Error> {
  return useQuery<IncidentsResponse, Error>({
    queryKey: INCIDENTS_QUERY_KEY,
    // NOTE: AbortSignal is intentionally not forwarded to getIncidents here.
    // Node 25 + MSW v2 (msw/node setupServer) rejects jsdom-context signals
    // when passed to undici's native Request constructor inside the interceptor.
    // Cancellation can be re-enabled once MSW/Node compatibility is resolved.
    queryFn: () => getIncidents(),
    refetchInterval: INCIDENTS_POLL_MS,
    refetchIntervalInBackground: true,
  });
}
