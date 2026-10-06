/**
 * The app's pages. Every path here is also in paths.json, which Flask and
 * the links read (src/app/routes.test.ts keeps the two the same).
 *
 * The frame of an area loads with the app; each page's own code is loaded
 * when it is first opened, so the app does not grow with every page moved.
 */
import type { RouteObject } from 'react-router';

import { AdminLayout } from '../frame/AdminLayout';
import { LoadingState } from '../components/States';
import { NotFound } from '../pages/NotFound';
import { RouteError } from '../pages/RouteError';

export const routes: RouteObject[] = [
  {
    errorElement: <RouteError />,
    // While the first page's own code is still on its way.
    hydrateFallbackElement: <LoadingState />,
    children: [
      {
        path: '/admin',
        element: <AdminLayout />,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import('../pages/admin/AdminDashboard')).AdminDashboard }),
          },
        ],
      },
      { path: '*', element: <NotFound /> },
    ],
  },
];
