import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getIncidents, type IncidentsResponse } from "../lib/api";

export const INCIDENTS_QUERY_KEY = ["incidents"] as const;
export const INCIDENTS_POLL_MS = 2000;

export function useIncidents(): UseQueryResult<IncidentsResponse, Error> {
  return useQuery<IncidentsResponse, Error>({
    queryKey: INCIDENTS_QUERY_KEY,
    queryFn: ({ signal }) => getIncidents(signal),
    refetchInterval: INCIDENTS_POLL_MS,
    refetchIntervalInBackground: true,
  });
}
