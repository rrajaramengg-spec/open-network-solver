import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FacilityCountInput } from '@/components/Widget/FacilityCountInput';

describe('<FacilityCountInput>', () => {
  it('renders current K value', () => {
    render(<FacilityCountInput k={3} onChange={() => {}} />);
    expect(screen.getByLabelText(/number of facilities/i)).toHaveValue(3);
  });

  it('calls onChange with K-1 when decremented', () => {
    const onChange = vi.fn();
    render(<FacilityCountInput k={5} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/decrement/i));
    expect(onChange).toHaveBeenCalledWith(4);
  });

  it('calls onChange with K+1 when incremented', () => {
    const onChange = vi.fn();
    render(<FacilityCountInput k={5} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText(/increment/i));
    expect(onChange).toHaveBeenCalledWith(6);
  });

  it('disables decrement at K=1', () => {
    render(<FacilityCountInput k={1} onChange={() => {}} />);
    expect(screen.getByLabelText(/decrement/i)).toBeDisabled();
  });

  it('disables increment at K=10', () => {
    render(<FacilityCountInput k={10} onChange={() => {}} />);
    expect(screen.getByLabelText(/increment/i)).toBeDisabled();
  });

  it('clamps direct numeric input to [1, 10]', () => {
    const onChange = vi.fn();
    render(<FacilityCountInput k={5} onChange={onChange} />);
    const input = screen.getByLabelText(/number of facilities/i);
    fireEvent.change(input, { target: { value: '99' } });
    expect(onChange).toHaveBeenCalledWith(10);
    fireEvent.change(input, { target: { value: '0' } });
    expect(onChange).toHaveBeenCalledWith(1);
  });
});
