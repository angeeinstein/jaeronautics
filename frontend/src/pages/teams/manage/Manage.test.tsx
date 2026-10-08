import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryRouter, RouterProvider, useLocation } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { Providers } from '../../../app/Providers';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { QueryClient } from '@tanstack/react-query';
import { Applications, Members } from './People';
import { Person } from './Person';
import { Applying, PageSettings, RolesPage } from './Settings';

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
  const lena = { user_id: 1, name: 'Lena Lead', picture_url: null, in_force: true };
  const anna = { user_id: 7, name: 'Anna Berger', detail: null, in_team: true };
  const roles = (overrides: Partial<Schemas['TeamRolesOut']> = {}): Schemas['TeamRolesOut'] => ({
    leads: [lena],
    treasurers: [],
    may_appoint: true,
    candidates: [{ user_id: 7, name: 'Anna Berger' }],
    may_appoint_leads: true,
    lead_candidates: [anna],
    searches_everyone: false,
    ...overrides,
  });
  const at = ['/teams/rocket/manage/roles', '/teams/:slug/manage/roles'] as const;

  it('who holds which role, in one table', async () => {
    show(<RolesPage />, { [`${API}/roles`]: { body: roles() } }, ...at);

    const table = await screen.findByRole('table', { name: 'Roles' });
    expect(table).toHaveTextContent('Lena Lead');
    expect(table).toHaveTextContent('Lead');
  });

  it('a lead makes another member a lead: who, as what, and the plus', async () => {
    const { calls } = show(
      <RolesPage />,
      { [`${API}/roles`]: { body: roles() }, [`POST ${API}/leads`]: { body: roles() } },
      ...at,
    );

    await userEvent.click(await screen.findByRole('combobox', { name: 'Give a role' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Anna Berger' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText('Lead appointed.')).toBeInTheDocument();
    await waitFor(async () => {
      const sent = calls.find((call) => call.method === 'POST');
      expect(sent && new URL(sent.url).pathname).toBe(`${API}/leads`);
      expect(await sent?.clone().json()).toEqual({ user_id: 7 });
    });
  });

  it('or treasurer', async () => {
    const { calls } = show(
      <RolesPage />,
      { [`${API}/roles`]: { body: roles() }, [`POST ${API}/treasurer`]: { body: roles() } },
      ...at,
    );

    await userEvent.click(await screen.findByRole('combobox', { name: 'Role' }));
    await userEvent.click(screen.getByRole('option', { name: 'Treasurer' }));
    await userEvent.click(screen.getByRole('combobox', { name: 'Give a role' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Anna Berger' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText('Treasurer appointed.')).toBeInTheDocument();
    expect(calls.some((call) => new URL(call.url).pathname === `${API}/treasurer`)).toBe(true);
  });

  it('removing the last lead is confirmed as that', async () => {
    const { calls } = show(
      <RolesPage />,
      { [`${API}/roles`]: { body: roles() }, [`DELETE ${API}/leads/1`]: { body: roles({ leads: [] }) } },
      ...at,
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Remove' }));
    await userEvent.click(screen.getByRole('button', { name: 'Yes, remove the last lead' }));

    await waitFor(() => {
      const sent = calls.find((call) => call.method === 'DELETE');
      expect(sent && new URL(sent.url).search).toBe('?confirmed=true');
    });
  });

  it('a site admin can choose somebody from outside the team, by name', async () => {
    const { calls } = show(
      <RolesPage />,
      {
        [`${API}/roles`]: { body: roles({ searches_everyone: true }) },
        [`${API}/lead-candidates`]: {
          body: { items: [{ user_id: 9, name: 'Tom Neu', detail: 'LAV24', in_team: false }] },
        },
      },
      ...at,
    );

    await userEvent.type(await screen.findByRole('combobox', { name: 'Give a role' }), 'Tom');

    expect(await screen.findByRole('option', { name: 'Tom Neu · LAV24' })).toBeInTheDocument();
    expect(calls.some((call) => new URL(call.url).search === '?q=Tom')).toBe(true);
  });

  it('nothing to change for somebody who may not', async () => {
    show(
      <RolesPage />,
      { [`${API}/roles`]: { body: roles({ may_appoint: false, may_appoint_leads: false }) } },
      ...at,
    );

    expect(await screen.findByText('Lena Lead')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Give a role' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
  });
});

describe('roles from the member list', () => {
  const members = (overrides: Partial<Schemas['MembersOut']> = {}): Schemas['MembersOut'] => ({
    max_members: null,
    charges: false,
    may_export: true,
    may_appoint_leads: true,
    may_appoint_treasurer: true,
    leads: 1,
    members: [
      {
        user_id: 7,
        name: 'Anna Berger',
        picture_url: null,
        is_lead: false,
        is_treasurer: false,
        university_email: null,
        cohort: 'LAV25',
        since: '2026-10-01',
        paid_until: null,
        notes: [],
      },
    ],
    ...overrides,
  });

  it('a ⋯ beside each member makes them lead or treasurer', async () => {
    const { calls } = show(
      <Members />,
      { [`${API}/members`]: { body: members() }, [`POST ${API}/treasurer`]: { body: {} } },
      '/teams/rocket/manage/members',
      '/teams/:slug/manage/members',
    );

    await userEvent.click(await screen.findByRole('button', { name: 'Roles of Anna Berger' }));
    expect(screen.getByRole('menuitem', { name: 'Make lead' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('menuitem', { name: 'Make treasurer' }));

    expect(await screen.findByText('Treasurer appointed.')).toBeInTheDocument();
    await waitFor(async () => {
      const sent = calls.find((call) => call.method === 'POST');
      expect(await sent?.clone().json()).toEqual({ user_id: 7 });
    });
  });

  it('none for somebody who may not give roles', async () => {
    show(
      <Members />,
      { [`${API}/members`]: { body: members({ may_appoint_leads: false, may_appoint_treasurer: false }) } },
      '/teams/rocket/manage/members',
      '/teams/:slug/manage/members',
    );

    expect(await screen.findByText('Anna Berger')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Roles of Anna Berger' })).toBeNull();
  });
});

describe('joining', () => {
  it('the leads set how people join and how many', async () => {
    const applying: Schemas['ApplyingOut'] = {
      admission_mode: 'approval',
      max_members: null,
      applications_open: true,
      application_prompt: null,
      rules: null,
    };
    const { calls } = show(
      <Applying />,
      { [`${API}/applying`]: { body: applying }, [`PUT ${API}/applying`]: { body: applying } },
      '/teams/rocket/manage/applying',
      '/teams/:slug/manage/applying',
    );

    await userEvent.click(await screen.findByRole('combobox', { name: 'How people join' }));
    await userEvent.click(screen.getByRole('option', { name: 'Open to every member' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Most members' }), '12');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(async () => {
      const sent = calls.find((call) => call.method === 'PUT');
      expect(await sent?.clone().json()).toMatchObject({ admission_mode: 'open', max_members: 12 });
    });
  });
});

describe('the short description', () => {
  it('counts towards one line, and a longer one is said and not sent', async () => {
    show(
      <PageSettings />,
      {
        [`${API}/page`]: {
          body: {
            description: 'x'.repeat(170),
            description_max: 160,
            about: null,
            picture_url: null,
            logo_url: null,
            photos: [],
            photos_max: 8,
          },
        },
      },
      '/teams/rocket/manage/page',
      '/teams/:slug/manage/page',
    );

    expect(await screen.findByText(/170 \/ 160/)).toBeInTheDocument();
    expect(screen.getByText(/Too long by 10 characters/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Save' })[0]).toBeDisabled();
  });
});

describe('the team page', () => {
  const photo = (id: number, caption: string | null): Schemas['PhotoOut'] => ({
    id,
    url: `/teams/photo/p${String(id)}`,
    caption,
    width: 1200,
    height: 900,
  });
  const page = (photos: Schemas['PhotoOut'][]): Schemas['PageOut'] => ({
    description: 'We build rockets.',
    description_max: 160,
    about: '# Who we are',
    picture_url: null,
    logo_url: null,
    photos,
    photos_max: 8,
  });

  it('the text seen as it will be shown, before it is saved', async () => {
    const { calls } = show(
      <PageSettings />,
      {
        [`${API}/page`]: { body: page([]) },
        [`POST ${API}/page/preview`]: { body: { html: '<h2>Who we are</h2>\n' } },
      },
      '/teams/rocket/manage/page',
      '/teams/:slug/manage/page',
    );

    await userEvent.click(await screen.findByRole('tab', { name: 'Preview' }));

    expect(await screen.findByRole('heading', { level: 2, name: 'Who we are' })).toBeInTheDocument();
    const sent = calls.find((call) => call.method === 'POST');
    expect(await sent?.clone().json()).toEqual({ about: '# Who we are' });
  });

  it('photos put in order and captioned, then saved together', async () => {
    const { calls } = show(
      <PageSettings />,
      {
        [`${API}/page`]: { body: page([photo(1, 'Launch day'), photo(2, null)]) },
        [`PUT ${API}/page/photos`]: { body: page([photo(2, 'Workshop'), photo(1, 'Launch day')]) },
      },
      '/teams/rocket/manage/page',
      '/teams/:slug/manage/page',
    );

    const save = await screen.findByRole('button', { name: 'Save order and captions' });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByRole('textbox', { name: 'Caption of photo 2' }), 'Workshop');
    await userEvent.click(screen.getByRole('button', { name: 'Move photo 2 up' }));
    await userEvent.click(save);

    expect(await screen.findByText('Saved.')).toBeInTheDocument();
    const sent = calls.find((call) => call.method === 'PUT');
    expect(await sent?.clone().json()).toEqual({
      photos: [
        { id: 2, caption: 'Workshop' },
        { id: 1, caption: 'Launch day' },
      ],
    });
    expect(screen.getByRole('textbox', { name: 'Caption of photo 1' })).toHaveValue('Workshop');
  });

  it('several photos added at once, one after the other', async () => {
    const { calls } = show(
      <PageSettings />,
      {
        [`${API}/page`]: { body: page([]) },
        [`POST ${API}/page/photos`]: { status: 201, body: page([photo(1, null), photo(2, null)]) },
      },
      '/teams/rocket/manage/page',
      '/teams/:slug/manage/page',
    );

    await screen.findByText('No photos');
    const input = document.querySelector<HTMLInputElement>('input[type="file"][multiple]');
    if (!input) throw new Error('No file input');
    await userEvent.upload(input, [
      new File(['a'], 'one.png', { type: 'image/png' }),
      new File(['b'], 'two.png', { type: 'image/png' }),
    ]);
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));

    expect(await screen.findByText('Added 2 photos.')).toBeInTheDocument();
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(2);
    expect(screen.getAllByRole('textbox', { name: /^Caption of photo/ })).toHaveLength(2);
  });

  it('a photo removed after a second click', async () => {
    const { calls } = show(
      <PageSettings />,
      {
        [`${API}/page`]: { body: page([photo(1, 'Launch day'), photo(2, null)]) },
        [`DELETE ${API}/page/photos/1`]: { body: page([photo(2, null)]) },
      },
      '/teams/rocket/manage/page',
      '/teams/:slug/manage/page',
    );

    const [first] = await screen.findAllByRole('listitem');
    if (!first) throw new Error('No photo');
    await userEvent.click(within(first).getByRole('button', { name: 'Remove' }));
    await userEvent.click(within(first).getByRole('button', { name: 'Yes, remove' }));

    expect(await screen.findByText('Removed.')).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'DELETE')).toBe(true);
    expect(screen.getAllByRole('textbox', { name: /^Caption of photo/ })).toHaveLength(1);
  });
});
