import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FacilityTypeSelect } from '@/components/Widget/FacilityTypeSelect';
import type { FacilityCategory } from '@/types/routing';

const categories: FacilityCategory[] = [
  { category: 'hospital', count: 12 },
  { category: 'fire_station', count: 5 },
  { category: 'pharmacy', count: 30 },
];

describe('<FacilityTypeSelect>', () => {
  it('always offers the "All facilities" sentinel option', () => {
    render(<FacilityTypeSelect value="all" onChange={vi.fn()} categories={[]} />);
    expect(screen.getByRole('option', { name: 'All facilities' })).toBeInTheDocument();
  });

  it('renders one option per ingested category with its count (data-driven)', () => {
    render(<FacilityTypeSelect value="all" onChange={vi.fn()} categories={categories} />);
    expect(screen.getByRole('option', { name: 'Hospital (12)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Fire Station (5)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Pharmacy (30)' })).toBeInTheDocument();
    // 3 categories + the "all" sentinel.
    expect(screen.getAllByRole('option')).toHaveLength(4);
  });

  it('emits the selected category value on change', () => {
    const onChange = vi.fn();
    render(<FacilityTypeSelect value="all" onChange={onChange} categories={categories} />);
    fireEvent.change(screen.getByLabelText('Facility type'), {
      target: { value: 'hospital' },
    });
    expect(onChange).toHaveBeenCalledWith('hospital');
  });
});
