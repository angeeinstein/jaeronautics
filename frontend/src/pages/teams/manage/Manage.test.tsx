import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { Providers } from '../../../app/Providers';
import { teamSidebar } from '../../../frame/TeamManageLayout';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { QueryClient } from '@tanstack/react-query';
import { Applications } from './People';
import { Person } from './Person';
import { RolesPage } from './Settings';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/teams/rocket/manage';

const manage: Schemas['ManageOut'] = {
  slug: 'rocket',
  name: 'Rocket',
  logo_url: null,
  labels: { singular: 'Team', plural: 'Teams' },
  permissions: [
    'team.view_members',
    'team.review_applications',
    'team.edit_settings',
    'team.appoint_treasurer',
  ],
  applications: 2,
  has_lead_in_force: true,
  access_list_enabled: false,
};

function show(element: React.ReactNode, answers: Answers, route: string, path: string) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: manage },
    ...answers,
  });
  renderPage(element, { route, path });
  return fetched;
}

describe('the team sidebar', () => {
  it('what this person may do, and nothing else', () => {
    const groups = teamSidebar(manage).groups;
    expect(groups.map((group) => group.items.map((item) => item.label))).toEqual([
      ['Applications', 'Members', 'Former members'],
      ['Team page', 'Applying', 'Roles'],
      ['Team page'],
    ]);
    expect(groups[0]?.items[0]?.count).toBe(2);

    const treasurerLike = teamSidebar({ ...manage, permissions: ['team.view_members', 'team.view_money'] });
    expect(treasurerLike.groups[1]?.items.map((item) => item.label)).toEqual(['Roles']);
    expect(treasurerLike.groups[2]?.items.map((item) => item.label)).toEqual(['Team page', 'Money']);
  });
});

describe('applications', () => {
  it('each a way to the person', async () => {
    show(
      <Applications />,
      {
        [`${API}/applications`]: {
          body: {
            applications: [
              {
                user_id: 7,
                name: 'Anna Berger',
                status: 'approved',
                status_label: 'Approved, payment open',
                payment: 'Payment on its way',
                applied_on: '2026-10-01',
              },
            ],
          },
        },
      },
      '/teams/rocket/manage',
      '/teams/:slug/manage',
    );

    const table = await screen.findByRole('table', { name: 'Applications' });
    expect(within(table).getByRole('link', { name: 'Anna Berger' })).toHaveAttribute(
      'href',
      '/teams/rocket/manage/people/7',
    );
    expect(table).toHaveTextContent('Payment on its way');
  });

  it('an old link to a tab opens its page', async () => {
    function Where() {
      return <p>{useLocation().pathname}</p>;
    }
    mockFetch({ [API]: { body: manage } });
    const router = createMemoryRouter(
      [
        { path: '/teams/:slug/manage', element: <Applications /> },
        { path: '/teams/:slug/manage/:section', element: <Where /> },
      ],
      { initialEntries: ['/teams/rocket/manage#manage-applying'] },
    );
    render(
      <Providers client={new QueryClient()} env="test">
        <RouterProvider router={router} />
      </Providers>,
    );

    expect(await screen.findByText('/teams/rocket/manage/applying')).toBeInTheDocument();
  });
});

const applicant: Schemas['TeamPersonOut'] = {
  user_id: 7,
  name: 'Anna Berger',
  details: {
    university_email: 'anna@edu.example',
    private_email: 'anna@example.com',
    phone: null,
    cohort: 'LAV24',
  },
  now: {
    membership_id: 3,
    status: 'applied',
    status_label: 'Application received',
    question: 'Why?',
    answer: 'I like rockets.',
    meeting: null,
  },
  history: [],
  notes: [],
  may_review: true,
  may_remove: true,
  may_write_notes: true,
};

describe('a person', () => {
  it('invited with a time and place, approved after asking again', async () => {
    const { calls } = show(
      <Person />,
      {
        [`${API}/people/7`]: { body: applicant },
        [`POST ${API}/memberships/3/invite`]: {
          body: { ...applicant, now: { ...applicant.now, status: 'invited', meeting: 'Tuesday 18:00' } },
        },
        [`POST ${API}/memberships/3/approve`]: {
          body: { ...applicant, now: { ...applicant.now, status: 'active', status_label: 'Member' } },
        },
      },
      '/teams/rocket/manage/people/7',
      '/teams/:slug/manage/people/:userId',
    );

    expect(await screen.findByText('I like rockets.')).toBeInTheDocument();
    await userEvent.type(screen.getByRole('textbox', { name: 'Invite to a meeting' }), 'Tuesday 18:00');
    await userEvent.click(screen.getByRole('button', { name: 'Send invitation' }));
    expect(await screen.findByText('Invitation sent.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    expect(calls.some((call) => new URL(call.url).pathname.endsWith('/approve'))).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, approve' }));
    expect(await screen.findByText('Approved.')).toBeInTheDocument();
  });
});

describe('roles', () => {
  it('a member appointed treasurer', async () => {
    const { calls } = show(
      <RolesPage />,
      {
        [`${API}/roles`]: {
          body: {
            leads: [{ user_id: 1, name: 'Lena Lead', in_force: true }],
            treasurers: [],
            may_appoint: true,
            candidates: [{ user_id: 7, name: 'Anna Berger' }],
          },
        },
        [`POST ${API}/treasurer`]: {
          body: {
            leads: [{ user_id: 1, name: 'Lena Lead', in_force: true }],
            treasurers: [{ user_id: 7, name: 'Anna Berger', in_force: true }],
            may_appoint: true,
            candidates: [],
          },
        },
      },
      '/teams/rocket/manage/roles',
      '/teams/:slug/manage/roles',
    );

    await userEvent.click(await screen.findByRole('combobox', { name: 'Appoint a member' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Anna Berger' }));
    await userEvent.click(screen.getByRole('button', { name: 'Appoint' }));

    expect(await screen.findByText('Treasurer appointed.')).toBeInTheDocument();
    await waitFor(async () => {
      const sent = calls.find((call) => call.method === 'POST');
      expect(await sent?.clone().json()).toEqual({ user_id: 7 });
    });
  });
});
