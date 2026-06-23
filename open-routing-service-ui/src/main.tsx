import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { bootstrapClientFromEnv } from '@/store';
import './styles/globals.css';

bootstrapClientFromEnv(import.meta.env);

const rootEl = document.getElementById('root');
if (rootEl === null) {
  throw new Error('Root element #root not found in index.html.');
}

createRoot(rootEl).render(
  <React.StrictMode>
    <App
      routingApiUrl={import.meta.env.VITE_ROUTING_API_URL}
      photonUrl={import.meta.env.VITE_PHOTON_URL ?? 'https://photon.komoot.io'}
      tileUrl={import.meta.env.VITE_TILE_URL}
    />
  </React.StrictMode>,
);
