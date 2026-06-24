import { useEffect, useState } from "react";
import type { ServerClock } from "../lib/serverClock";

export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${pad(mm)}:${pad(ss)}`;
}

export function CountdownTimer({
  deadlineAt,
  clock,
  terminal = false,
  tickMs = 1000,
}: {
  deadlineAt: string | null;
  clock: ServerClock;
  terminal?: boolean;
  tickMs?: number;
}): JSX.Element {
  // `tick` exists only to force a re-render each second; the value comes
  // entirely from the (monotonic) ServerClock, never from local wall time.
  const [, setTick] = useState(0);

  useEffect(() => {
    if (terminal || deadlineAt === null) {
      return;
    }
    const id = setInterval(() => setTick((t) => t + 1), tickMs);
    return () => clearInterval(id);
  }, [terminal, deadlineAt, tickMs]);

  if (terminal || deadlineAt === null) {
    return (
      <span data-testid="countdown" data-state="terminal">
        —
      </span>
    );
  }

  const remainingMs =
    Date.parse(deadlineAt) - clock.estimatedServerNowMs();

  if (remainingMs <= 0) {
    return (
      <span data-testid="countdown" data-state="overdue" role="status">
        OVERDUE — awaiting server
      </span>
    );
  }

  return (
    <span data-testid="countdown" data-state="counting">
      {formatRemaining(remainingMs)}
    </span>
  );
}
