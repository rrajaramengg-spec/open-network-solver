/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ROUTING_API_URL: string;
  readonly VITE_NOMINATIM_URL: string;
  readonly VITE_PHOTON_URL?: string;
  readonly VITE_TILE_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// React 19 dropped the global JSX namespace — re-export from React so older
// `JSX.Element` return-type annotations continue to compile.
import type { JSX as ReactJSX } from 'react';
declare global {
  namespace JSX {
    type Element = ReactJSX.Element;
    type IntrinsicElements = ReactJSX.IntrinsicElements;
  }
}

export {};
