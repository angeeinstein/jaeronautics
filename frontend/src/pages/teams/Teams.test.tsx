import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { About } from './About';
import { Leave } from './Leave';
import { TeamPage } from './TeamPage';
import { Teams } from './Teams';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/teams';
const labels = { singular: 'Team', plural: 'Teams' };

function membership(overrides: Partial<Schemas['TeamMembershipOut']> = {}): Schemas['TeamMembershipOut'] {
  return {
    status: null,
    status_label: null,
    ongoing: false,
    fee: null,
    notes: [],
    meeting: null,
    actions: [],
    join_label: null,
    why_not: null,
    ...overrides,
  };
}

function team(overrides: Partial<Schemas['TeamPageOut']> = {}): Schemas['TeamPageOut'] {
  return {
    slug: 'rocket',
    name: 'Rocket',
    description: 'We build rockets.',
    logo_url: null,
    about: null,
    picture_url: null,
    labels,
    is_member: true,
    membership: membership(),
    sees_team_page: false,
    can_manage: false,
    can_see_money: false,
    rules: null,
    joining: { mode: 'approval', rejoin_until: null, prompt: 'Why?', why_not: null, submit_label: 'Apply' },
    members: null,
    ...overrides,
  };
}

function show(element: React.ReactNode, answers: Answers, route = '/', path = '*') {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    ...answers,
  });
  renderPage(element, { route, path });
  return fetched;
}

describe('the overview', () => {
  it('mine first, with what to do next; the others to read about', async () => {
    show(<Teams />, {
      [API]: {
        body: {
          labels,
          is_member: true,
          mine: [
            {
              slug: 'rocket',
              name: 'Rocket',
              description: null,
              logo_url: null,
              opens: 'team',
              about_label: 'About & apply',
              membership: membership({
                status: 'active',
                status_label: 'Member',
                ongoing: true,
                notes: [
                  {
                    tone: 'warning',
                    text: 'Pay for the next period, until 31.03.2027, to stay without a gap.',
                  },
                ],
                actions: ['open', 'pay_next', 'leave'],
              }),
            },
          ],
          others: [
            {
              slug: 'glider',
              name: 'Glider',
              description: 'We fly gliders.',
              logo_url: null,
              opens: 'about',
              about_label: 'About & apply',
              membership: membership({ fee: '€10.00 every 6 months' }),
            },
          ],
        },
      },
    });

    const mine = await screen.findByRole('region', { name: 'My teams' });
    const rocket = within(mine).getByRole('region', { name: 'Rocket' });
    expect(rocket).toHaveTextContent('Member');
    expect(within(rocket).getByRole('button', { name: 'Pay for next period' })).toBeInTheDocument();
    expect(within(rocket).getByRole('link', { name: 'Open' })).toHaveAttribute('href', '/teams/rocket');
    const others = screen.getByRole('region', { name: 'Other teams' });
    expect(within(others).getByRole('link', { name: 'About & apply' })).toHaveAttribute(
      'href',
      '/teams/glider/about',
    );
    expect(others).toHaveTextContent('Fee: €10.00 every 6 months.');
  });
});

describe('a team', () => {
  it('its members see who is in it', async () => {
    show(
      <TeamPage />,
      {
        [`${API}/rocket`]: {
          body: team({
            sees_team_page: true,
            joining: null,
            membership: membership({
              status: 'active',
              status_label: 'Member',
              ongoing: true,
              actions: ['open', 'leave'],
            }),
            members: [
              { name: 'Lena Lead', picture_url: null, is_lead: true, university_email: 'lena@edu.example' },
            ],
          }),
        },
      },
      '/teams/rocket',
      '/teams/:slug',
    );

    const members = await screen.findByRole('list', { name: 'Members' });
    expect(members).toHaveTextContent(/Lena Lead\s*Lead\s*lena@edu.example/);
    expect(screen.queryByRole('link', { name: 'Open' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Leave' })).toBeInTheDocument();
  });

  it('applying: the rules ticked, read in a dialog without leaving the form', async () => {
    const { calls } = show(
      <About />,
      {
        [`${API}/rocket`]: {
          body: team({ rules: { version: '2026-10-01', accepted: null, changed_since: false } }),
        },
        [`${API}/rocket/rules`]: {
          body: {
            title: 'Teamordnung',
            language: 'de',
            is_translation: false,
            version: '2026-10-01',
            revision: null,
            effective_from: '2026-10-01',
            in_force: '2026-10-01',
            html: '<p>Einweisung vor jedem Flug.</p>',
            contents: [],
            german_url: '/teams/rocket/rules/de',
            english_url: null,
            english_elsewhere: false,
            others: [],
            pdf_url: '/teams/rocket/rules/pdf',
            pdf_has_english: false,
          },
        },
        [`POST ${API}/rocket/join`]: {
          body: { message: 'Application sent. The leads will be in touch.', team: team() },
        },
      },
      '/teams/rocket/about',
      '/teams/:slug/about',
    );

    const apply = await screen.findByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'rules of Rocket' }));
    expect(await screen.findByText('Einweisung vor jedem Flug.')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');

    await userEvent.type(screen.getByRole('textbox', { name: 'Why?' }), 'I like rockets.');
    await userEvent.click(screen.getByRole('checkbox', { name: /I accept the/ }));
    await userEvent.click(apply);

    expect(await screen.findByText('Application sent. The leads will be in touch.')).toBeInTheDocument();
    const sent = calls.find((call) => call.method === 'POST');
    expect(await sent?.clone().json()).toEqual({ application_text: 'I like rockets.', accept_rules: true });
  });
});

describe('leaving', () => {
  it('what it means, then a deliberate yes', async () => {
    const { calls } = show(
      <Leave />,
      {
        [`${API}/rocket/leave`]: {
          body: {
            labels,
            team: {
              slug: 'rocket',
              name: 'Rocket',
              description: null,
              logo_url: null,
              opens: 'team',
              about_label: '',
              membership: membership(),
            },
            stays_until: '2027-03-31',
            subscription: true,
            access_list: false,
            is_lead: false,
          },
        },
        [`POST ${API}/rocket/leave`]: {
          body: { message: 'You leave Rocket on 31.03.2027. Until then nothing changes.' },
        },
      },
      '/teams/rocket/leave',
      '/teams/:slug/leave',
    );

    expect(await screen.findByText(/You stay in the team until 31\.03\.2027/)).toBeInTheDocument();
    const leave = screen.getByRole('button', { name: 'Leave the team' });
    expect(leave).toBeDisabled();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Yes, I want to leave Rocket.' }));
    await userEvent.click(leave);

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST')).toBe(true);
    });
    expect(await screen.findByText(/You leave Rocket on 31\.03\.2027/)).toBeInTheDocument();
  });
});
