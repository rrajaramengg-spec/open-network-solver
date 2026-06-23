# open-routing-service-ui

React 19 + Vite + MapLibre GL JS single-page app for the closest-facility workflow. Interactive map + side widget for inputs + ranked result rendering. Calls Nominatim **directly from the browser** (design D6) — never proxied through the API.

See [`docs/phases/phase-4-ui.md`](../docs/phases/phase-4-ui.md) for the full Phase 4 walkthrough and [`docs/architecture.md`](../docs/architecture.md) for the consolidated architecture brief.

## Components

| Component | Purpose |
|-----------|---------|
| `<MapView>` | MapLibre GL JS canvas + basemap + child layers via `useMapLibre` hook |
| `<IncidentLayer>` | Incident marker + click-to-set handler |
| `<RouteLayer>` | Top-K route polylines; selected route in a distinct stroke via filter mutation |
| `<FacilityLayer>` | Ranked facility markers with 1..K badge labels |
| `<SearchWidget>` | Address search + buffer + K + facility-type + cost-mode + Find button |
| `<AddressSearch>` | Debounced Nominatim autocomplete (300 ms, AbortController) |
| `<BufferDistanceInput>` | Slider with ft↔m toggle, default 500 ft (152.4 m) |
| `<FacilityCountInput>` | Stepper, default K=1, range 1–10 |
| `<FacilityTypeSelect>` | Maps "all/fire/police/EMS/hospital" → `{amenity: ...}` filter |
| `<CostModeToggle>` | Distance vs. travel-time toggle |
| `<ResultsList>` | Ranked card list with click-to-zoom / click-to-isolate, cache-hit indicator |
| `<RunbookBadge>` | Polls `/readyz` every 30 s and surfaces service + ETL freshness |
| `<ErrorToast>` | Single-toast surface for the `lastError` slice |

## Layout

```
open-routing-service-ui/
├── package.json            # Vite 6, React 19, TS 5.7, Tailwind v4, Vitest 2.1, Playwright 1.49
├── src/
│   ├── api/                # closestFacilityClient.ts, nominatimClient.ts
│   ├── components/         # Map/, Widget/, ResultsList/, Common/ — each w/ index.ts barrel
│   ├── hooks/              # useMapLibre.ts — owns Map instance lifecycle
│   ├── store/              # searchStore.ts — headless Zustand v5 w/ configureSearchStore(client)
│   ├── index.css           # Tailwind v4 @theme block (CSS-first, no JS theme)
│   ├── vite-env.d.ts       # Critical: re-augments global JSX namespace for React 19
│   └── main.tsx
├── tests/
│   ├── unit/               # Vitest + jsdom + RTL — 55 tests pass at HEAD
│   ├── e2e/                # Playwright
│   ├── a11y/               # axe-core/playwright
│   └── perf/               # @lhci/cli (Lighthouse-CI)
├── Dockerfile              # Multi-stage: Vite build → nginx alpine, non-root, gzip+cache headers
└── nginx.conf
```

## Environment variables

All `VITE_*` vars are baked at build time (Vite convention).

| Name | Default | Purpose |
|------|---------|---------|
| `VITE_API_BASE_URL` | `http://localhost:58000` | open-routing-service URL |
| `VITE_NOMINATIM_BASE_URL` | `http://localhost:7070` | Nominatim — browser-direct |
| `VITE_TILE_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | Basemap tiles |
| `VITE_DEFAULT_CENTER` | `32.7157,-117.1611` | Initial map center (lat,lon) |
| `VITE_DEFAULT_ZOOM` | `12` | Initial zoom |

## Dev quick start

```sh
cp .env.example .env
npm install
npm run dev                 # → http://localhost:5173
npm test                    # Vitest unit tests
npm run typecheck           # tsc --noEmit
npm run test:e2e            # Playwright (requires API + UI running)
npm run build               # tsc --noEmit && vite build → dist/
```

Production image (built SPA served by nginx):

```sh
docker compose --env-file infra/.env -f infra/docker-compose.yml \
  --profile service --profile ui up -d
# → http://localhost:58081
```
