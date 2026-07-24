import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// In dev, /api is proxied to the FastAPI backend. In production the built
// files in dist/ are served by the same origin as the API.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // FastAPI's interactive docs and the schema they load.
      '/docs': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/openapi.json': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
