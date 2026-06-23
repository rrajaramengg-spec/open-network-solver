import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FacilityTypeSelect } from '@/components/Widget/FacilityTypeSelect';
import type { FacilityCategory } from '@/types/routing';

const categories: FacilityCategory[] = [
  { category: 'hospital', count: 12 },
  { category: 'fire_station', count: 5 },
  { category: 'pharmacy', count: 30 },
];

describe('<FacilityTypeSelect>', () => {
  it('shows the selected value label in the combobox input', () => {
    render(<FacilityTypeSelect value="all" onChange={vi.fn()} categories={categories} />);
    expect(screen.getByLabelText('Facility type')).toHaveValue('All facilities');
  });

  it('opens a data-driven list: the sentinel + one option per ingested category', async () => {
    const user = userEvent.setup();
    render(<FacilityTypeSelect value="all" onChange={vi.fn()} categories={categories} />);
    await user.click(screen.getByLabelText('Facility type'));

    expect(screen.getByRole('option', { name: 'All facilities' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Hospital (12)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Fire Station (5)' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Pharmacy (30)' })).toBeInTheDocument();
    // 3 categories + the "all" sentinel.
    expect(screen.getAllByRole('option')).toHaveLength(4);
  });

  it('filters the options as the user types (searchable)', async () => {
    const user = userEvent.setup();
    render(<FacilityTypeSelect value="all" onChange={vi.fn()} categories={categories} />);
    const input = screen.getByLabelText('Facility type');
    await user.click(input);
    await user.clear(input);
    await user.type(input, 'pharm');

    expect(screen.getByRole('option', { name: 'Pharmacy (30)' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Hospital (12)' })).not.toBeInTheDocument();
  });

  it('emits the selected category value when an option is picked', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FacilityTypeSelect value="all" onChange={onChange} categories={categories} />);
    await user.click(screen.getByLabelText('Facility type'));
    await user.click(screen.getByRole('option', { name: 'Hospital (12)' }));

    expect(onChange).toHaveBeenCalledWith('hospital');
  });
});
