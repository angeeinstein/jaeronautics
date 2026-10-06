import '@fontsource-variable/inter/wght.css';
import '@fontsource-variable/archivo/wght.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/600.css';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './styles/global.css';

import { setNonce } from 'get-nonce';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router';

import { cspNonce, Providers } from './app/Providers';
import { routes } from './app/routes';

// Dialogs and drawers lock the page's scrolling with a <style> tag of their
// own (react-remove-scroll); it takes the page's nonce from here.
const nonce = cspNonce();
if (nonce) setNonce(nonce);

const router = createBrowserRouter(routes);
const root = document.getElementById('root');
if (!root) throw new Error('The page has no #root to draw into.');

createRoot(root).render(
  <StrictMode>
    <Providers>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
