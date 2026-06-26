import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getZones, type Zone } from "../lib/api";

export const ZONES_QUERY_KEY = ["zones"] as const;

export function useZones(): UseQueryResult<Zone[], Error> {
  return useQuery<Zone[], Error>({
    queryKey: ZONES_QUERY_KEY,
    queryFn: ({ signal }) => getZones(signal),
    staleTime: 60_000,
  });
}
