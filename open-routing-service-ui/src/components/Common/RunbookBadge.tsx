import { useEffect, useState } from 'react';
import { fetchReadyz } from '@/api';
import type { ReadyzResponse } from '@/types/routing';

export interface RunbookBadgeProps {
  /** Routing-service base URL (no trailing slash). */
  routingApiUrl: string;
  /** Poll interval in ms (default 30 000). */
  intervalMs?: number;
}

/** Periodic `/readyz` poller surfacing service health in the header. */
export function RunbookBadge({
  routingApiUrl,
  intervalMs = 30_000,
}: RunbookBadgeProps): JSX.Element {
  const [state, setState] = useState<ReadyzResponse | null | 'loading'>('loading');

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const poll = async (): Promise<void> => {
      const result = await fetchReadyz(routingApiUrl, { signal: controller.signal });
      if (!cancelled) setState(result);
    };
    void poll();
    const handle = setInterval(poll, intervalMs);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(handle);
    };
  }, [routingApiUrl, intervalMs]);

  const color =
    state === 'loading'
      ? 'bg-slate-600'
      : state === null
        ? 'bg-rose-600'
        : state.status === 'ok'
          ? 'bg-emerald-600'
          : 'bg-amber-600';

  const label =
    state === 'loading'
      ? 'checking…'
      : state === null
        ? 'unreachable'
        : state.status;

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="runbook-badge"
      className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold text-white ${color}`}
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-pulse rounded-full bg-white"
      />
      service: {label}
      {state !== null && state !== 'loading' ? (
        <span className="ml-1 font-normal opacity-80">
          db {state.primary_pg}/{state.replica_pg} · redis {state.redis}
        </span>
      ) : null}
    </div>
  );
}
