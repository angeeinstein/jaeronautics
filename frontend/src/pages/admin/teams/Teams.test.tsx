import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { NewTeam } from './NewTeam';
import { Team } from './Team';
import { Teams } from './Teams';

type TeamsOut = Schemas['TeamsOut'];
type TeamOut = Schemas['TeamOut'];

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/teams';

function list(overrides: Partial<TeamsOut> = {}): TeamsOut {
  return {
    settings: { enabled: true, label_singular: 'Team', label_plural: 'Teams' },
    teams: [
      {
        slug: 'rocket',
        name: 'Rocket Team',
        status: 'active',
        logo_url: null,
        admission_mode: 'approval',
        applications_open: true,
        members: 4,
        fee: '€10.00 every 6 months',
        leads: ['Lena Lead'],
        has_lead_in_force: true,
      },
      {
        slug: 'glider',
        name: 'Glider Team',
        status: 'active',
        logo_url: null,
        admission_mode: 'open',
        applications_open: true,
        members: 0,
        fee: null,
        leads: [],
        has_lead_in_force: false,
      },
      {
        slug: 'balloon',
        name: 'Balloon Team',
        status: 'archived',
        logo_url: null,
        admission_mode: 'open',
        applications_open: false,
        members: 0,
        fee: null,
        leads: [],
        has_lead_in_force: false,
      },
    ],
    ...overrides,
  };
}

function team(overrides: Partial<TeamOut> = {}): TeamOut {
  return {
    slug: 'rocket',
    name: 'Rocket Team',
    status: 'active',
    logo_url: null,
    admission_mode: 'approval',
    max_members: 12,
    forum_group: null,
    access_list_enabled: false,
    fee: { payment_mode: 'none', stripe_price_id: null, period_starts: null, display: null },
    roles: [
      {
        user_id: 21,
        name: 'Lena Lead',
        email: 'lena@example.org',
        role: 'lead',
        role_label: 'Lead',
        in_force: true,
      },
    ],
    has_lead_in_force: true,
    members: 4,
    archived_at: null,
    manage_url: '/teams/rocket/manage',
    ...overrides,
  };
}

function show(element: React.ReactNode, route: string, path: string, answers: Answers = {}) {
  const fetched = mockFetch({
    [API]: { body: list() },
    [`${API}/rocket`]: { body: team() },
    '/api/v1/me': { body: makeMe() },
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    ...answers,
  });
  renderPage(element, { route, path });
  return fetched;
}

async function sent(calls: Request[], method: string, path: string): Promise<unknown> {
  await waitFor(() => {
    expect(calls.some((call) => call.method === method && new URL(call.url).pathname === path)).toBe(true);
  });
  const request = calls
    .filter((call) => call.method === method && new URL(call.url).pathname === path)
    .at(-1);
  return request ? request.clone().json() : undefined;
}

describe('the list', () => {
  it('each team with its members, fee and lead, opening the team', async () => {
    show(<Teams />, '/admin/teams', '/admin/teams');

    const table = await screen.findByRole('table', { name: 'Teams' });
    const rows = within(table).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('Rocket Team4€10.00 every 6 monthsLena Lead');
    expect(within(table).getByRole('link', { name: 'Rocket Team' })).toHaveAttribute(
      'href',
      '/admin/teams/rocket',
    );
    expect(rows[2]).toHaveTextContent('Glider Team0FreeNone');
    expect(rows[3]).toHaveTextContent('Balloon TeamArchived');
    expect(rows[3]).not.toHaveTextContent('None');
    expect(screen.getByRole('link', { name: 'New Team' })).toHaveAttribute('href', '/admin/teams/new');
  });

  it('calls teams what they are called', async () => {
    show(<Teams />, '/admin/teams', '/admin/teams', {
      [API]: {
        body: list({ settings: { enabled: true, label_singular: 'Project', label_plural: 'Projects' } }),
      },
    });

    expect(await screen.findByRole('heading', { name: 'Projects', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'New Project' })).toBeInTheDocument();
  });

  it('saves the switch and the names', async () => {
    const { calls } = show(<Teams />, '/admin/teams', '/admin/teams', {
      'PUT /api/v1/admin/team-settings': { body: { changed: true } },
    });

    await userEvent.click(await screen.findByRole('switch', { name: /Switched on/ }));
    const plural = screen.getByRole('textbox', { name: 'Called (several)' });
    await userEvent.clear(plural);
    await userEvent.type(plural, 'Groups');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await sent(calls, 'PUT', '/api/v1/admin/team-settings')).toEqual({
      enabled: false,
      label_singular: 'Team',
      label_plural: 'Groups',
    });
    expect(await screen.findByText('Saved.')).toBeInTheDocument();
  });
});

describe('a new team', () => {
  it('free unless a fee is chosen; the short name only when given', async () => {
    const { calls } = show(<NewTeam />, '/admin/teams/new', '/admin/teams/new', {
      [`POST ${API}`]: { status: 201, body: team({ slug: 'glider', name: 'Glider' }) },
    });

    const create = await screen.findByRole('button', { name: 'Create' });
    expect(create).toBeDisabled();
    await userEvent.type(screen.getByRole('textbox', { name: 'Name' }), 'Glider');
    await userEvent.click(create);

    expect(await sent(calls, 'POST', API)).toEqual({
      name: 'Glider',
      admission_mode: 'approval',
      max_members: null,
      forum_group: null,
      access_list_enabled: false,
      slug: null,
      fee: null,
    });
    expect(await screen.findByText('Created. Give it a lead next.')).toBeInTheDocument();
  });

  it('a refusal by the server is shown', async () => {
    show(<NewTeam />, '/admin/teams/new', '/admin/teams/new', {
      [`POST ${API}`]: {
        status: 400,
        body: {
          error: {
            code: 'team_slug_reserved',
            message: 'That short name is taken.',
            fields: null,
            details: null,
          },
        },
      },
    });

    await userEvent.type(await screen.findByRole('textbox', { name: 'Name' }), 'New');
    await userEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(await screen.findByText('That short name is taken.')).toBeInTheDocument();
  });
});

describe('one team', () => {
  const route = ['/admin/teams/rocket', '/admin/teams/:slug'] as const;

  it('its cards, and the way to its own page', async () => {
    show(<Team />, ...route);

    expect(await screen.findByRole('heading', { name: 'Rocket Team', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Team page and settings' })).toHaveAttribute(
      'href',
      '/teams/rocket/manage',
    );
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('Rocket Team');
    expect(screen.getByRole('table', { name: 'Roles' })).toHaveTextContent(
      'Lena Leadlena@example.orgLeadYes',
    );
  });

  it('saves the details', async () => {
    const { calls } = show(<Team />, ...route, { [`PUT ${API}/rocket`]: { body: team() } });

    const size = await screen.findByRole('textbox', { name: 'Maximum size' });
    await userEvent.clear(size);
    await userEvent.click(screen.getByRole('checkbox', { name: /Has rooms that need an access list/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    expect(await sent(calls, 'PUT', `${API}/rocket`)).toEqual({
      name: 'Rocket Team',
      admission_mode: 'approval',
      max_members: null,
      forum_group: null,
      access_list_enabled: true,
    });
  });

  it('changes the fee only after a second click, and says what it did to the members', async () => {
    const { calls } = show(<Team />, ...route, {
      [`PUT ${API}/rocket/fee`]: { body: { moving: 0, stopping: 0, switched: 0, asked_to_pay: 3 } },
    });

    await userEvent.click(await screen.findByRole('combobox', { name: 'Payment' }));
    await userEvent.click(screen.getByRole('option', { name: 'Subscription' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Stripe price ID' }), 'price_123');
    await userEvent.type(screen.getByRole('textbox', { name: 'Periods start on' }), '01.10, 01.04');
    await userEvent.click(screen.getByRole('button', { name: 'Save fee' }));
    expect(calls.some((call) => call.method === 'PUT')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, change the fee' }));

    expect(await sent(calls, 'PUT', `${API}/rocket/fee`)).toEqual({
      payment_mode: 'subscription',
      stripe_price_id: 'price_123',
      period_starts: '01.10, 01.04',
    });
    expect(
      await screen.findByText(/3 member\(s\) stay free until the next period starts/),
    ).toBeInTheDocument();
  });

  it('gives a role by email address', async () => {
    const { calls } = show(<Team />, ...route, { [`POST ${API}/rocket/roles`]: { body: team() } });

    await userEvent.type(await screen.findByRole('textbox', { name: 'Email address' }), ' tom@example.org ');
    await userEvent.click(screen.getByRole('combobox', { name: 'Role' }));
    await userEvent.click(screen.getByRole('option', { name: 'Treasurer' }));
    await userEvent.click(screen.getByRole('button', { name: 'Give role' }));

    expect(await sent(calls, 'POST', `${API}/rocket/roles`)).toEqual({
      email: 'tom@example.org',
      role: 'treasurer',
    });
  });

  it('removing the last lead says so and is confirmed', async () => {
    const { calls } = show(<Team />, ...route, { [`POST ${API}/rocket/roles/revoke`]: { body: team() } });
    const roles = await screen.findByRole('table', { name: 'Roles' });

    await userEvent.click(within(roles).getByRole('button', { name: 'Remove' }));
    await userEvent.click(within(roles).getByRole('button', { name: 'Yes, remove the last lead' }));

    expect(await sent(calls, 'POST', `${API}/rocket/roles/revoke`)).toEqual({
      user_id: 21,
      role: 'lead',
      confirmed: true,
    });
  });

  it('archives once the name is typed', async () => {
    const { calls } = show(<Team />, ...route, {
      [`PUT ${API}/rocket/archived`]: { body: team({ status: 'archived' }) },
    });

    const archive = await screen.findByRole('button', { name: 'Archive' });
    expect(archive).toBeDisabled();
    await userEvent.type(screen.getByRole('textbox', { name: /Type the team's name/ }), 'Rocket Team');
    await userEvent.click(archive);

    expect(await sent(calls, 'PUT', `${API}/rocket/archived`)).toEqual({
      archived: true,
      confirm_name: 'Rocket Team',
    });
  });

  it('an archived team can be restored', async () => {
    const { calls } = show(<Team />, ...route, {
      [`${API}/rocket`]: { body: team({ status: 'archived', archived_at: '2026-09-01T10:00:00Z' }) },
      [`PUT ${API}/rocket/archived`]: { body: team() },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Restore' }));

    expect(await sent(calls, 'PUT', `${API}/rocket/archived`)).toEqual({
      archived: false,
      confirm_name: null,
    });
  });

  it('warns when no lead is in force', async () => {
    show(<Team />, ...route, { [`${API}/rocket`]: { body: team({ has_lead_in_force: false }) } });

    expect(
      await screen.findByText('No lead in force. Until there is one, only admins can run it.'),
    ).toBeInTheDocument();
  });
});
