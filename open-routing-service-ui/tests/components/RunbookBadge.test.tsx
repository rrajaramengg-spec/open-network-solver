import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { RunbookBadge } from '@/components/Common/RunbookBadge';

describe('<RunbookBadge>', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows "checking…" before the first poll resolves', () => {
    fetchMock.mockReturnValue(new Promise(() => {}));
    render(<RunbookBadge routingApiUrl="http://x" intervalMs={60_000} />);
    expect(screen.getByTestId('runbook-badge')).toHaveTextContent(/checking/i);
  });

  it('shows "ok" when /readyz returns 200 ok', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'ok',
          primary_pg: 'ok',
          replica_pg: 'ok',
          pgr_version: 'ok',
          redis: 'ok',
        }),
        { status: 200 },
      ),
    );
    render(<RunbookBadge routingApiUrl="http://x" intervalMs={60_000} />);
    await waitFor(() => {
      expect(screen.getByTestId('runbook-badge')).toHaveTextContent(/service: ok/);
    });
  });

  it('shows "unreachable" on fetch failure', async () => {
    fetchMock.mockRejectedValue(new Error('boom'));
    render(<RunbookBadge routingApiUrl="http://x" intervalMs={60_000} />);
    await waitFor(() => {
      expect(screen.getByTestId('runbook-badge')).toHaveTextContent(/unreachable/i);
    });
  });

  it('calls fetch at least once with the right URL', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          status: 'ok',
          primary_pg: 'ok',
          replica_pg: 'ok',
          pgr_version: 'ok',
          redis: 'ok',
        }),
        { status: 200 },
      ),
    );
    render(<RunbookBadge routingApiUrl="http://x" intervalMs={60_000} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toContain('/readyz');
  });
});
