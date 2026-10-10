/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend (FastAPI) runs on BACKEND_PORT (default 8010); the dev server proxies API and WebSocket
// calls to it so the app always uses same-origin relative URLs (also true when FastAPI serves the
// production build).
const backendPort = process.env.BACKEND_PORT ?? '8010'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': `http://127.0.0.1:${backendPort}`,
      '/ws': { target: `ws://127.0.0.1:${backendPort}`, ws: true },
    },
  },
  test: {
    environment: 'node',
  },
})
