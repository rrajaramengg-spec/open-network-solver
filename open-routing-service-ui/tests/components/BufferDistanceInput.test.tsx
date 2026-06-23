import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BufferDistanceInput } from '@/components/Widget/BufferDistanceInput';

describe('<BufferDistanceInput>', () => {
  it('renders 500 ft for the spec default of 152.4 m', () => {
    render(
      <BufferDistanceInput metres={152.4} unit="ft" onChange={() => {}} onUnitChange={() => {}} />,
    );
    const input = screen.getByLabelText(/buffer distance/i) as HTMLInputElement;
    expect(parseFloat(input.value)).toBeCloseTo(500, 0);
  });

  it('renders metres when unit=m', () => {
    render(
      <BufferDistanceInput metres={500} unit="m" onChange={() => {}} onUnitChange={() => {}} />,
    );
    const input = screen.getByLabelText(/buffer distance/i) as HTMLInputElement;
    expect(parseFloat(input.value)).toBeCloseTo(500, 0);
  });

  it('toggles unit when ft/m chip is clicked', () => {
    const onUnitChange = vi.fn();
    render(
      <BufferDistanceInput
        metres={152.4}
        unit="ft"
        onChange={() => {}}
        onUnitChange={onUnitChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /m/i, pressed: false }));
    expect(onUnitChange).toHaveBeenCalledWith('m');
  });

  it('converts ft input back to metres on change', () => {
    const onChange = vi.fn();
    render(
      <BufferDistanceInput metres={152.4} unit="ft" onChange={onChange} onUnitChange={() => {}} />,
    );
    const input = screen.getByLabelText(/buffer distance/i);
    fireEvent.change(input, { target: { value: '1000' } });
    // 1000 ft ≈ 304.8 m
    expect(onChange.mock.calls[0][0]).toBeCloseTo(304.8, 1);
  });
});
