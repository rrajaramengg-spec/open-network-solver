import { useEffect } from 'react';
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import { useSearchStore } from '@/store';
import { rankColor } from '@/utils/rankColors';
import {
  ensureCategoryIcon,
  iconIdForCategory,
  registerCategoryIcons,
} from '@/utils/categoryIcon';
import type { FacilityResult } from '@/types/routing';
import { buildFacilityPopupHTML, type FacilityPopupData } from './FacilityPopup';

export interface FacilityLayerProps {
  mapRef: React.RefObject<MapLibreMap | null>;
}

const SOURCE_ID = 'facilities-source';
const CIRCLE_LAYER_ID = 'facilities-circle';
const ICON_LAYER_ID = 'facilities-icons';

/** A facility marker feature with the properties the map + popup need. */
type FacilityPointFeature = GeoJSON.Feature<GeoJSON.Point, Record<string, unknown>>;

/**
 * Build the GeoJSON marker features from result rows, sourced from each row's
 * real `facility_geojson` Point geometry. Falls back to the route's terminal
 * coordinate only when the facility geometry is unavailable (older payloads).
 *
 * @param results The ranked closest-facility results.
 * @returns One Point feature per result that has a usable coordinate.
 */
export function buildFacilityFeatures(results: FacilityResult[]): FacilityPointFeature[] {
  const features: FacilityPointFeature[] = [];
  for (const r of results) {
    const coord = facilityCoord(r);
    if (coord === null) continue;
    const category = r.category ?? 'other';
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: coord },
      properties: {
        facility_id: r.facility_id,
        rank: r.rank,
        color: rankColor(r.rank),
        category,
        icon: iconIdForCategory(category),
        name: r.name ?? '',
        total_distance_m: r.total_distance_m,
        total_time_s: r.total_time_s,
        tags: JSON.stringify(r.tags ?? {}),
      },
    });
  }
  return features;
}

/** Resolve a facility marker coordinate: real geometry first, route-end fallback. */
function facilityCoord(r: FacilityResult): [number, number] | null {
  const geo = r.facility_geojson;
  if (geo != null && Array.isArray(geo.geometry?.coordinates)) {
    const [lon, lat] = geo.geometry.coordinates;
    if (typeof lon === 'number' && typeof lat === 'number') return [lon, lat];
  }
  const coords = r.route_geojson?.coordinates;
  if (coords != null && coords.length > 0) {
    return coords[coords.length - 1] as [number, number];
  }
  return null;
}

/** Translate a clicked feature's properties into popup data. */
function popupDataFromFeature(props: Record<string, unknown>): FacilityPopupData {
  let tags: Record<string, unknown> = {};
  if (typeof props.tags === 'string') {
    try {
      tags = JSON.parse(props.tags) as Record<string, unknown>;
    } catch {
      tags = {};
    }
  }
  const name = typeof props.name === 'string' && props.name.length > 0 ? props.name : null;
  return {
    facilityId: Number(props.facility_id),
    rank: Number(props.rank),
    name,
    category: typeof props.category === 'string' ? props.category : null,
    totalDistanceM: Number(props.total_distance_m),
    totalTimeS: Number(props.total_time_s),
    tags,
  };
}

/**
 * Renders ranked facility markers at their real geometry: a rank-coloured disc
 * (matching the route + results-list colour for that rank) with the
 * category-specific Maki glyph in white on top. Clicking a marker selects the
 * rank and opens a MapLibre popup with the facility details.
 */
export function FacilityLayer({ mapRef }: FacilityLayerProps): null {
  const results = useSearchStore((s) => s.results);

  // Data effect — register icons + (re)build the source and layers.
  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    const features = buildFacilityFeatures(results);

    const apply = () => {
      void registerCategoryIcons(map);
      const src = map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
      if (src === undefined) {
        map.addSource(SOURCE_ID, {
          type: 'geojson',
          data: { type: 'FeatureCollection', features },
        });
        // Base disc — always renders (no image dependency), colour-keyed to rank.
        map.addLayer({
          id: CIRCLE_LAYER_ID,
          type: 'circle',
          source: SOURCE_ID,
          paint: {
            'circle-radius': 13,
            'circle-color': ['get', 'color'],
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2,
          },
        });
        // Category glyph (white) centred on the disc.
        map.addLayer({
          id: ICON_LAYER_ID,
          type: 'symbol',
          source: SOURCE_ID,
          layout: {
            'icon-image': ['get', 'icon'],
            'icon-size': 0.9,
            'icon-anchor': 'center',
            'icon-allow-overlap': true,
            'icon-ignore-placement': true,
          },
        });
      } else {
        src.setData({ type: 'FeatureCollection', features });
      }
    };

    if (map.isStyleLoaded()) apply();
    else map.once('load', apply);

    return undefined;
  }, [results, mapRef]);

  // Interaction effect — wire click/hover + lazy icon resolution once.
  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    const onMissing = (e: { id: string }): void => {
      ensureCategoryIcon(map, e.id);
    };
    const onClick = (e: maplibregl.MapLayerMouseEvent): void => {
      const feature = e.features?.[0];
      if (feature === undefined) return;
      const props = feature.properties ?? {};
      const rank = Number(props.rank);
      useSearchStore.getState().setSelectedRank(rank);
      const geom = feature.geometry as GeoJSON.Point;
      new maplibregl.Popup({ closeButton: true, offset: 16 })
        .setLngLat(geom.coordinates as [number, number])
        .setHTML(buildFacilityPopupHTML(popupDataFromFeature(props)))
        .addTo(map);
    };
    const onEnter = (): void => {
      map.getCanvas().style.cursor = 'pointer';
    };
    const onLeave = (): void => {
      map.getCanvas().style.cursor = '';
    };

    map.on('styleimagemissing', onMissing);
    map.on('click', CIRCLE_LAYER_ID, onClick);
    map.on('mouseenter', CIRCLE_LAYER_ID, onEnter);
    map.on('mouseleave', CIRCLE_LAYER_ID, onLeave);

    return () => {
      map.off('styleimagemissing', onMissing);
      map.off('click', CIRCLE_LAYER_ID, onClick);
      map.off('mouseenter', CIRCLE_LAYER_ID, onEnter);
      map.off('mouseleave', CIRCLE_LAYER_ID, onLeave);
    };
  }, [mapRef]);

  return null;
}
