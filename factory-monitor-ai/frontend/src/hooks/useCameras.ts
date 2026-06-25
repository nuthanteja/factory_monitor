import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getCameras, type Camera } from "../lib/api";

export const CAMERAS_QUERY_KEY = ["cameras"] as const;

export function useCameras(): UseQueryResult<Camera[], Error> {
  return useQuery<Camera[], Error>({
    queryKey: CAMERAS_QUERY_KEY,
    queryFn: ({ signal }) => getCameras(signal),
    staleTime: 60_000,
  });
}
