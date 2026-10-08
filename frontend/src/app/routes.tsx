/**
 * The app's pages. Every path here is also in paths.json, which Flask and
 * the links read (src/app/routes.test.ts keeps the two the same).
 *
 * The frame of an area loads with the app; each page's own code is loaded
 * when it is first opened, so the app does not grow with every page moved.
 */
import type { RouteObject } from 'react-router';

import { AccountLayout } from '../frame/AccountLayout';
import { AdminLayout } from '../frame/AdminLayout';
import { PublicLayout } from '../frame/PublicLayout';
import { TeamLayout } from '../frame/TeamLayout';
import { TeamsLayout } from '../frame/TeamsLayout';
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
        element: <PublicLayout wide />,
        children: [
          {
            path: '/legal',
            lazy: async () => ({ Component: (await import('../pages/public/Legal')).LegalTexts }),
          },
          // The two languages by name: /legal/<slug>/pdf is the PDF, Flask's.
          ...[
            '/legal/:slug',
            '/legal/:slug/de',
            '/legal/:slug/en',
            '/legal/:slug/de/:version',
            '/legal/:slug/en/:version',
          ].map((path) => ({
            path,
            lazy: async () => ({ Component: (await import('../pages/public/Legal')).LegalText }),
          })),
        ],
      },
      {
        element: <PublicLayout />,
        children: [
          {
            path: '/',
            lazy: async () => ({ Component: (await import('../pages/public/Landing')).Landing }),
          },
          {
            path: '/join',
            lazy: async () => ({ Component: (await import('../pages/public/Join')).Join }),
          },
          {
            path: '/thank-you',
            lazy: async () => ({ Component: (await import('../pages/public/Payment')).ThankYou }),
          },
          {
            path: '/cancel',
            lazy: async () => ({ Component: (await import('../pages/public/Payment')).Cancel }),
          },
          {
            path: '/login',
            lazy: async () => ({ Component: (await import('../pages/public/SignIn')).SignIn }),
          },
          {
            path: '/forgot-password',
            lazy: async () => ({
              Component: (await import('../pages/public/ForgotPassword')).ForgotPassword,
            }),
          },
          {
            path: '/reset-password/:token',
            lazy: async () => ({ Component: (await import('../pages/public/ResetPassword')).ResetPassword }),
          },
          // Any address that is no page: in the public frame, signed in or not.
          { path: '*', element: <NotFound /> },
        ],
      },
      {
        path: '/account',
        element: <AccountLayout />,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import('../pages/account/Account')).Account }),
          },
          {
            path: 'profile',
            lazy: async () => ({ Component: (await import('../pages/account/Profile')).Profile }),
          },
          {
            path: 'membership',
            lazy: async () => ({
              Component: (await import('../pages/account/MembershipPage')).MembershipPage,
            }),
          },
          {
            path: 'forum',
            lazy: async () => ({ Component: (await import('../pages/account/Forum')).Forum }),
          },
          {
            path: 'data',
            lazy: async () => ({ Component: (await import('../pages/account/Data')).Data }),
          },
          {
            path: 'create-membership',
            lazy: async () => ({
              Component: (await import('../pages/account/CreateMembership')).CreateMembership,
            }),
          },
          {
            path: 'delete/:token',
            lazy: async () => ({
              Component: (await import('../pages/account/DeleteAccount')).DeleteAccount,
            }),
          },
        ],
      },
      {
        path: '/change-password',
        element: <AccountLayout />,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import('../pages/account/Password')).Password }),
          },
        ],
      },
      {
        path: '/forum',
        element: <AccountLayout />,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import('../pages/account/Forum')).Forum }),
          },
        ],
      },
      {
        path: '/teams',
        element: <TeamsLayout />,
        children: [
          { index: true, lazy: async () => ({ Component: (await import('../pages/teams/Teams')).Teams }) },
        ],
      },
      {
        // One team's pages, in its frame: its side menu for whoever is in it or runs part of it.
        path: '/teams/:slug',
        element: <TeamLayout />,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import('../pages/teams/TeamPage')).TeamPage }),
          },
          {
            path: 'about',
            lazy: async () => ({ Component: (await import('../pages/teams/About')).About }),
          },
          {
            path: 'leave',
            lazy: async () => ({ Component: (await import('../pages/teams/Leave')).Leave }),
          },
          // The two languages by name: /rules/pdf is the PDF, Flask's.
          ...['rules', 'rules/de', 'rules/en', 'rules/de/:version', 'rules/en/:version'].map((path) => ({
            path,
            lazy: async () => ({ Component: (await import('../pages/teams/Rules')).Rules }),
          })),
          {
            path: 'money',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/Money')).Money }),
          },
          {
            path: 'manage',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/People')).Applications }),
          },
          {
            path: 'manage/members',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/People')).Members }),
          },
          {
            path: 'manage/former',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/People')).FormerMembers }),
          },
          {
            path: 'manage/people/:userId',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/Person')).Person }),
          },
          {
            path: 'manage/page',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/Settings')).PageSettings }),
          },
          {
            path: 'manage/applying',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/Settings')).Applying }),
          },
          {
            path: 'manage/access-list',
            lazy: async () => ({
              Component: (await import('../pages/teams/manage/Settings')).AccessListPage,
            }),
          },
          {
            path: 'manage/roles',
            lazy: async () => ({ Component: (await import('../pages/teams/manage/Settings')).RolesPage }),
          },
        ],
      },
      {
        path: '/admin',
        element: <AdminLayout />,
        children: [
          {
            index: true,
            lazy: async () => ({ Component: (await import('../pages/admin/AdminDashboard')).AdminDashboard }),
          },
          {
            path: 'accounts',
            lazy: async () => ({ Component: (await import('../pages/admin/Accounts')).Accounts }),
          },
          {
            path: 'accounts/:userId',
            lazy: async () => ({ Component: (await import('../pages/admin/account/Account')).Account }),
          },
          {
            path: 'reviews',
            lazy: async () => ({ Component: (await import('../pages/admin/reviews/Reviews')).Reviews }),
          },
          {
            path: 'teams',
            lazy: async () => ({ Component: (await import('../pages/admin/teams/Teams')).Teams }),
          },
          {
            path: 'teams/new',
            lazy: async () => ({ Component: (await import('../pages/admin/teams/NewTeam')).NewTeam }),
          },
          {
            path: 'teams/:slug',
            lazy: async () => ({ Component: (await import('../pages/admin/teams/Team')).Team }),
          },
          {
            path: 'settings',
            lazy: async () => ({
              Component: (await import('../pages/admin/settings/SettingsIndex')).SettingsIndex,
            }),
          },
          {
            path: 'settings/general',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/General')).General }),
          },
          {
            path: 'settings/notifications',
            lazy: async () => ({
              Component: (await import('../pages/admin/settings/Notifications')).Notifications,
            }),
          },
          {
            path: 'settings/billing',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/Billing')).Billing }),
          },
          {
            path: 'settings/forum',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/Forum')).Forum }),
          },
          {
            path: 'settings/mail',
            lazy: async () => ({
              Component: (await import('../pages/admin/settings/MailAccounts')).MailAccounts,
            }),
          },
          {
            path: 'settings/test-email',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/TestEmail')).TestEmail }),
          },
          {
            path: 'settings/health',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/Health')).Health }),
          },
          {
            path: 'settings/updates',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/Updates')).Updates }),
          },
          {
            path: 'settings/backup',
            lazy: async () => ({ Component: (await import('../pages/admin/settings/Backup')).Backup }),
          },
          {
            path: 'legal',
            lazy: async () => ({ Component: (await import('../pages/admin/LegalTexts')).LegalTexts }),
          },
          {
            path: 'logs',
            lazy: async () => ({ Component: (await import('../pages/admin/Logs')).Logs }),
          },
          {
            path: 'money',
            lazy: async () => ({ Component: (await import('../pages/admin/money/Money')).Money }),
          },
          {
            path: 'money/:slug',
            lazy: async () => ({ Component: (await import('../pages/admin/money/TeamMoney')).TeamMoney }),
          },
        ],
      },
    ],
  },
];
