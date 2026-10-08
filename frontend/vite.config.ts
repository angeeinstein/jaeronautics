// The front end's build. The result goes into the Flask app's static folder,
// which nginx serves under /static/; Flask hands index.html to each address
// the app draws (src/app/paths.json, blueprints/app_shell.py).
//
// `npm run dev` serves the app with hot reloading on :5173 and passes every
// other address (the API, the pages not moved yet, static files) on to Flask
// on :5000, so both sides can be worked on at once.
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';
import { matchPath } from 'react-router';

import appPaths from './src/app/paths.json' with { type: 'json' };

const FLASK = process.env.FLASK_URL ?? 'http://127.0.0.1:5000';

function isAppPage(url: string): boolean {
  const path = url.split('?')[0] ?? '/';
  return appPaths.paths.some((pattern) => matchPath({ path: pattern, end: true }, path) !== null);
}

export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/app/' : '/',
  plugins: [react()],
  build: {
    outDir: '../aeronautics_members/static/app',
    emptyOutDir: true,
    // The polyfill would be an inline script, which the security policy forbids;
    // every browser the portal supports preloads modules itself.
    modulePreload: { polyfill: false },
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Everything except Vite's own files and the app's pages goes to Flask.
      '^/(?!@vite/|@react-refresh|@id/|@fs/|src/|node_modules/|__vite)': {
        target: FLASK,
        changeOrigin: false,
        bypass: (request) => (request.url && isAppPage(request.url) ? '/index.html' : undefined),
      },
    },
  },
  test: {
    // Component tests only; e2e/ is Playwright's.
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    restoreMocks: true,
  },
}));
