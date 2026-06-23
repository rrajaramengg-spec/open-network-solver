/**
 * `useMapLibre` — typed React hook that owns the MapLibre `Map` lifecycle.
 *
 * Per task 4.2 + design D7: a thin wrapper, no `react-map-gl` dependency.
 *
 * The hook:
 *   * Creates the `Map` instance on first render inside the supplied container.
 *   * Calls `resize()` on container size changes (ResizeObserver).
 *   * Calls `dispose()` on unmount.
 *   * Exposes `onClick(handler)` and `onMoveEnd(handler)` registration.
 *   * Re-exposes the underlying `Map` via a stable ref so layer components can
 *     attach sources/layers in their own `useEffect`s.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import maplibregl, { type Map as MapLibreMap, type MapMouseEvent } from 'maplibre-gl';

export interface UseMapLibreOptions {
  /** Initial centre [lng, lat]. */
  center?: [number, number];
  /** Initial zoom (default 4 — US-West fit). */
  zoom?: number;
  /** Tile URL template, e.g. ``https://tile.openstreetmap.org/{z}/{x}/{y}.png``. */
  tileUrl: string;
  /** Optional click handler (lat/lon delivered, not the raw event). */
  onClick?: (lat: number, lon: number, ev: MapMouseEvent) => void;
  /** Optional moveend handler. */
  onMoveEnd?: (map: MapLibreMap) => void;
}

export interface UseMapLibreResult {
  /** Attach this to the container `<div>` via `ref={containerRef}`. */
  containerRef: React.RefObject<HTMLDivElement | null>;
  /** The underlying Map; null before first render completes. */
  mapRef: React.RefObject<MapLibreMap | null>;
  /**
   * `true` once the map has fired `load`. Controls/layers that have no state
   * dependency to re-run on (e.g. the geocoder control) can depend on this so
   * they attach only after `mapRef.current` is populated.
   */
  mapReady: boolean;
}

export function useMapLibre(opts: UseMapLibreOptions): UseMapLibreResult {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const onClickRef = useRef(opts.onClick);
  const onMoveEndRef = useRef(opts.onMoveEnd);
  const [mapReady, setMapReady] = useState(false);

  // Keep handler refs in sync without re-creating the map.
  useEffect(() => {
    onClickRef.current = opts.onClick;
  }, [opts.onClick]);
  useEffect(() => {
    onMoveEndRef.current = opts.onMoveEnd;
  }, [opts.onMoveEnd]);

  useEffect(() => {
    if (containerRef.current === null) return;
    if (mapRef.current !== null) return; // strict-mode double-invoke guard

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          basemap: {
            type: 'raster',
            tiles: [opts.tileUrl],
            tileSize: 256,
            attribution: '© OpenStreetMap contributors',
          },
        },
        layers: [
          {
            id: 'basemap',
            type: 'raster',
            source: 'basemap',
          },
        ],
      },
      center: opts.center ?? [-117.0, 37.0],
      zoom: opts.zoom ?? 4,
    });

    map.on('click', (ev) => {
      onClickRef.current?.(ev.lngLat.lat, ev.lngLat.lng, ev);
    });
    map.on('moveend', () => {
      onMoveEndRef.current?.(map);
    });
    map.on('load', () => setMapReady(true));

    mapRef.current = map;

    const ro = new ResizeObserver(() => {
      map.resize();
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      map.remove();
      mapRef.current = null;
      setMapReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.tileUrl]);

  return { containerRef, mapRef, mapReady };
}

/** Convenience: fit the map to a list of [lng, lat] points with sane padding. */
export function fitToPoints(
  map: MapLibreMap,
  points: [number, number][],
  padding = 64,
): void {
  if (points.length === 0) return;
  if (points.length === 1) {
    map.flyTo({ center: points[0], zoom: 14 });
    return;
  }
  const lons = points.map((p) => p[0]);
  const lats = points.map((p) => p[1]);
  const bounds: [[number, number], [number, number]] = [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ];
  map.fitBounds(bounds, { padding, animate: true });
}

export const useMapLibreInternals = useCallback;
