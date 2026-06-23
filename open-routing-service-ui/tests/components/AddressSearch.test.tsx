import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AddressSearch } from '@/components/Widget/AddressSearch';
import type { NominatimClient } from '@/api';

function fakeClient(results: { display_name: string; lat: string; lon: string }[]): NominatimClient {
  return { search: vi.fn().mockResolvedValue(results) };
}

describe('<AddressSearch>', () => {
  it('does not call the API for queries shorter than 3 chars', async () => {
    const client = fakeClient([]);
    render(<AddressSearch client={client} onSelect={() => {}} debounceMs={0} />);
    await userEvent.type(screen.getByLabelText(/address search/i), 'ab');
    await new Promise((r) => setTimeout(r, 10));
    expect(client.search).not.toHaveBeenCalled();
  });

  it('opens the dropdown with results after a debounced query', async () => {
    const client = fakeClient([
      { display_name: 'San Diego, CA', lat: '32.7', lon: '-117.1' },
    ]);
    render(<AddressSearch client={client} onSelect={() => {}} debounceMs={0} />);
    await userEvent.type(screen.getByLabelText(/address search/i), 'san');
    await waitFor(() => {
      expect(screen.getByRole('option')).toBeInTheDocument();
    });
    expect(screen.getByText(/san diego/i)).toBeInTheDocument();
  });

  it('calls onSelect with parsed lat/lon when an option is clicked', async () => {
    const client = fakeClient([
      { display_name: 'San Diego, CA', lat: '32.7', lon: '-117.1' },
    ]);
    const onSelect = vi.fn();
    render(<AddressSearch client={client} onSelect={onSelect} debounceMs={0} />);
    await userEvent.type(screen.getByLabelText(/address search/i), 'san');
    await waitFor(() => screen.getByRole('option'));
    fireEvent.click(screen.getByRole('option').querySelector('button')!);
    expect(onSelect).toHaveBeenCalledWith(32.7, -117.1, 'San Diego, CA');
  });
});
