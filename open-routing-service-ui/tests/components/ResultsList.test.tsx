import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ResultsList } from '@/components/ResultsList/ResultsList';
import { useSearchStore } from '@/store';

function reset(state: Partial<ReturnType<typeof useSearchStore.getState>>) {
  useSearchStore.setState({
    incident: null,
    bufferMeters: 152.4,
    k: 1,
    costMode: 'distance',
    facilityType: 'all',
    facilityFilter: {},
    isLoading: false,
    lastError: null,
    results: [],
    cacheHit: false,
    selectedRank: null,
    ...state,
  });
}

describe('<ResultsList>', () => {
  beforeEach(() => reset({}));

  it('shows the empty-state copy when there are no results', () => {
    render(<ResultsList />);
    expect(screen.getByTestId('results-empty')).toBeInTheDocument();
  });

  it('renders one card per result', () => {
    reset({
      results: [
        {
          facility_id: 1, rank: 1, total_cost: 100,
          total_distance_m: 100, total_time_s: 10, route_geojson: null,
        },
        {
          facility_id: 2, rank: 2, total_cost: 200,
          total_distance_m: 200, total_time_s: 20, route_geojson: null,
        },
      ],
    });
    render(<ResultsList />);
    expect(screen.getByTestId('result-1')).toBeInTheDocument();
    expect(screen.getByTestId('result-2')).toBeInTheDocument();
  });

  it('selects a rank when the card is clicked', () => {
    const setSelectedRank = vi.fn();
    reset({
      results: [
        {
          facility_id: 1, rank: 1, total_cost: 100,
          total_distance_m: 100, total_time_s: 10, route_geojson: null,
        },
      ],
      setSelectedRank,
    } as never);
    render(<ResultsList />);
    fireEvent.click(screen.getByTestId('result-1'));
    expect(setSelectedRank).toHaveBeenCalledWith(1);
  });

  it('badges "served from cache" when cache_hit is true', () => {
    reset({
      cacheHit: true,
      results: [
        {
          facility_id: 1, rank: 1, total_cost: 100,
          total_distance_m: 100, total_time_s: 10, route_geojson: null,
        },
      ],
    });
    render(<ResultsList />);
    expect(screen.getByText(/served from cache/i)).toBeInTheDocument();
  });
});
