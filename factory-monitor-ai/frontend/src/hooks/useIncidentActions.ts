import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import {
  acknowledgeIncident,
  resolveIncident,
  type ActionResponse,
  type IncidentsResponse,
} from "../lib/api";
import { INCIDENTS_QUERY_KEY } from "./useIncidents";

interface AckVars {
  id: string;
}
interface ResolveVars {
  id: string;
  note?: string;
}
interface OptimisticCtx {
  previous: IncidentsResponse | undefined;
}

function patchStatus(
  prev: IncidentsResponse | undefined,
  id: string,
  status: string,
): IncidentsResponse | undefined {
  if (!prev) {
    return prev;
  }
  return {
    ...prev,
    incidents: prev.incidents.map((i) =>
      i.id === id ? { ...i, status } : i,
    ),
  };
}

export interface IncidentActions {
  acknowledge: UseMutationResult<ActionResponse, Error, AckVars, OptimisticCtx>;
  resolve: UseMutationResult<ActionResponse, Error, ResolveVars, OptimisticCtx>;
}

export function useIncidentActions(): IncidentActions {
  const queryClient = useQueryClient();

  const optimistic = async (
    id: string,
    nextStatus: string,
  ): Promise<OptimisticCtx> => {
    // 1. Cancel in-flight refetches FIRST so a resolving poll cannot
    //    overwrite the optimistic patch we are about to apply.
    await queryClient.cancelQueries({ queryKey: INCIDENTS_QUERY_KEY });
    // 2. Snapshot current data.
    const previous = queryClient.getQueryData<IncidentsResponse>(
      INCIDENTS_QUERY_KEY,
    );
    // 3. Apply the optimistic patch.
    queryClient.setQueryData<IncidentsResponse>(
      INCIDENTS_QUERY_KEY,
      patchStatus(previous, id, nextStatus),
    );
    return { previous };
  };

  const rollback = (ctx: OptimisticCtx | undefined) => {
    if (ctx?.previous) {
      queryClient.setQueryData(INCIDENTS_QUERY_KEY, ctx.previous);
    }
  };

  const settle = () => {
    void queryClient.invalidateQueries({ queryKey: INCIDENTS_QUERY_KEY });
  };

  const acknowledge = useMutation<ActionResponse, Error, AckVars, OptimisticCtx>({
    mutationFn: ({ id }) => acknowledgeIncident(id),
    onMutate: ({ id }) => optimistic(id, "ACK"),
    onError: (_e, _v, ctx) => rollback(ctx),
    onSettled: settle,
  });

  const resolve = useMutation<ActionResponse, Error, ResolveVars, OptimisticCtx>({
    mutationFn: ({ id, note }) => resolveIncident(id, note),
    onMutate: ({ id }) => optimistic(id, "RESOLVED"),
    onError: (_e, _v, ctx) => rollback(ctx),
    onSettled: settle,
  });

  return { acknowledge, resolve };
}
