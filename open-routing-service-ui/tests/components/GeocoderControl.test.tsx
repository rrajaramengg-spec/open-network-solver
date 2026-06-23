import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';

// Capture the geocoder's `result` handler + the control wiring so we can drive
// the selection event the same way the library does.
let resultHandler: ((e: { result: unknown }) => void) | null = null;
const addControl = vi.fn();
const removeControl = vi.fn();

vi.mock('@maplibre/maplibre-gl-geocoder', () => ({
  default: class {
    on(type: string, fn: (e: { result: unknown }) => void): void {
      if (type === 'result') resultHandler = fn;
    }
  },
}));

// The component imports the maplibre default for `AttributionControl`.
vi.mock('maplibre-gl', () => ({
  default: { AttributionControl: class {} },
}));

vi.mock('@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css', () => ({}));

import { GeocoderControl } from '@/components/Map/GeocoderControl';
import { useSearchStore } from '@/store';

function fakeMapRef() {
  return { current: { addControl, removeControl } as never };
}

describe('<GeocoderControl>', () => {
  beforeEach(() => {
    resultHandler = null;
    addControl.mockClear();
    removeControl.mockClear();
    useSearchStore.setState({ incident: null });
  });

  it('registers the geocoder + attribution controls on the map', () => {
    render(<GeocoderControl mapRef={fakeMapRef()} mapReady photonUrl="https://photon.example" />);
    expect(addControl).toHaveBeenCalledTimes(2);
  });

  it('forwards a selected result to setIncident (geometry coordinates)', () => {
    render(<GeocoderControl mapRef={fakeMapRef()} mapReady photonUrl="https://photon.example" />);
    expect(resultHandler).not.toBeNull();
    resultHandler?.({
      result: { geometry: { type: 'Point', coordinates: [-71.06, 42.36] } },
    });
    expect(useSearchStore.getState().incident).toEqual({ lat: 42.36, lon: -71.06 });
  });

  it('falls back to the Carmen `center` when geometry is absent', () => {
    render(<GeocoderControl mapRef={fakeMapRef()} mapReady photonUrl="https://photon.example" />);
    resultHandler?.({ result: { center: [-100.5, 40.25] } });
    expect(useSearchStore.getState().incident).toEqual({ lat: 40.25, lon: -100.5 });
  });

  it('ignores a result with no usable coordinate', () => {
    render(<GeocoderControl mapRef={fakeMapRef()} mapReady photonUrl="https://photon.example" />);
    resultHandler?.({ result: {} });
    expect(useSearchStore.getState().incident).toBeNull();
  });
});
