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
    about_html: null,
    picture_url: null,
    photos: [],
    member_count: 3,
    fee: null,
    labels,
    is_member: true,
    membership: membership(),
    sees_team_page: false,
    can_manage: false,
    can_edit_page: false,
    can_see_money: false,
    permissions: [],
    role: null,
    applications_waiting: null,
    access_list_enabled: false,
    lead_missing: false,
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

function card(overrides: Partial<Schemas['TeamCardOut']> = {}): Schemas['TeamCardOut'] {
  return {
    slug: 'rocket',
    name: 'Rocket',
    description: null,
    logo_url: null,
    picture_url: null,
    member_count: 3,
    role: null,
    applications_waiting: null,
    admission: null,
    opens: 'team',
    about_label: 'About & apply',
    membership: membership(),
    ...overrides,
  };
}

describe('the overview', () => {
  it('each team a tile, the whole of it the way in, with the one thing that matters now', async () => {
    show(<Teams />, {
      [API]: {
        body: {
          labels,
          is_member: true,
          mine: [
            card({
              logo_url: '/teams/logo/r',
              membership: membership({
                status: 'active',
                status_label: 'Member',
                ongoing: true,
                notes: [{ tone: 'warning', text: 'Pay for the next period, until 31.03.2027.' }],
                actions: ['open', 'pay_next', 'leave'],
              }),
            }),
            card({
              slug: 'drone',
              name: 'Drone Tech',
              role: 'Lead',
              applications_waiting: 2,
              membership: membership({ status: 'active', status_label: 'Member', ongoing: true }),
            }),
          ],
          others: [
            card({
              slug: 'glider',
              name: 'Glider Team',
              description: 'We fly gliders.',
              opens: 'about',
              admission: 'approval',
              member_count: 1,
              membership: membership({ fee: '€10.00 every 6 months' }),
            }),
          ],
        },
      },
    });

    const mine = await screen.findByRole('region', { name: 'My teams' });
    const rocket = within(mine).getByRole('link', { name: 'Rocket' });
    expect(rocket).toHaveAttribute('href', '/teams/rocket');
    expect(rocket).toHaveAccessibleDescription(/Member.*3 members/);
    expect(rocket).toHaveTextContent('Member');
    expect(rocket).toHaveTextContent('3 members');
    expect(rocket).toHaveTextContent('Pay for the next period, until 31.03.2027.');
    expect(rocket.querySelector('img')).toHaveAttribute('src', '/teams/logo/r');
    const drone = within(mine).getByRole('link', { name: 'Drone Tech' });
    expect(drone).toHaveTextContent('Lead');
    expect(drone).toHaveTextContent('2 applications to answer');
    expect(drone).toHaveTextContent('DT'); // no logo: the initials
    // What to do is on the team's own pages, not here.
    expect(within(mine).queryByRole('button')).not.toBeInTheDocument();

    const others = screen.getByRole('region', { name: 'Other teams' });
    const glider = within(others).getByRole('link', { name: 'Glider Team' });
    expect(glider).toHaveAttribute('href', '/teams/glider/about');
    expect(glider).toHaveTextContent('1 member');
    expect(glider).toHaveTextContent('Applications open');
    expect(glider).toHaveTextContent('€10.00 every 6 months');
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
    expect(screen.getByRole('button', { name: 'Leave the team…' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Needs your attention' })).not.toBeInTheDocument();
  });

  it('whoever runs it sees what waits for them, a way straight there', async () => {
    show(
      <TeamPage />,
      {
        [`${API}/rocket`]: {
          body: team({
            sees_team_page: true,
            can_manage: true,
            role: 'Lead',
            permissions: ['team.view_members'],
            applications_waiting: 2,
            joining: null,
            membership: membership({ status: 'active', status_label: 'Member', ongoing: true }),
            members: [],
          }),
        },
      },
      '/teams/rocket',
      '/teams/:slug',
    );

    const attention = await screen.findByRole('region', { name: 'Needs your attention' });
    expect(within(attention).getByRole('link', { name: /2\s*Applications to answer/ })).toHaveAttribute(
      'href',
      '/teams/rocket/manage',
    );
    expect(screen.getByRole('region', { name: 'Rocket' })).toHaveTextContent('Lead');
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

describe('the about page', () => {
  const photo = (id: number, caption: string | null): Schemas['PhotoOut'] => ({
    id,
    url: `/teams/photo/p${String(id)}`,
    caption,
    width: 1200,
    height: 900,
  });

  it('the team presented: cover, facts, its story and photos, joining beside', async () => {
    show(
      <About />,
      {
        [`${API}/rocket`]: {
          body: team({
            picture_url: '/teams/picture/cover',
            about_html: '<h2>What we do</h2>\n<p>We <strong>build</strong> rockets.</p>\n',
            fee: '€10.00 every 6 months',
            photos: [photo(1, 'Launch day'), photo(2, null), photo(3, 'Workshop')],
          }),
        },
      },
      '/teams/rocket/about',
      '/teams/:slug/about',
    );

    expect(await screen.findByRole('heading', { level: 1, name: 'Rocket' })).toBeInTheDocument();
    const cover = screen.getByRole('region', { name: 'Rocket' });
    expect(cover).toHaveTextContent('3 members');
    expect(cover).toHaveTextContent('Applications open');
    expect(cover).toHaveTextContent('€10.00 every 6 months');
    expect(within(cover).getByRole('link', { name: 'Apply' })).toHaveAttribute('href', '#join');
    expect(within(cover).queryByRole('link', { name: 'Edit page' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 2, name: 'What we do' })).toBeInTheDocument();
    expect(screen.getByText('build').tagName).toBe('STRONG');

    const photos = within(screen.getByRole('region', { name: 'Photos' })).getAllByRole('button');
    expect(photos).toHaveLength(3);
    await userEvent.click(screen.getByRole('button', { name: 'Open the photo: Launch day' }));
    const viewer = await screen.findByRole('dialog', { name: '1 / 3' });
    expect(within(viewer).getByRole('img', { name: 'Launch day' })).toBeInTheDocument();
    await userEvent.click(within(viewer).getByRole('button', { name: 'Previous photo' }));
    expect(await screen.findByRole('dialog', { name: '3 / 3' })).toBeInTheDocument();
    await userEvent.keyboard('{ArrowRight}{ArrowRight}');
    expect(await screen.findByRole('dialog', { name: '2 / 3' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Photo of Rocket' })).toBeInTheDocument();
  });

  it('for somebody in it: the story without the form, their membership being on the overview', async () => {
    show(
      <About />,
      {
        [`${API}/rocket`]: {
          body: team({
            sees_team_page: true,
            can_manage: true,
            can_edit_page: true,
            joining: null,
            membership: membership({ status: 'active', status_label: 'Member', ongoing: true }),
          }),
        },
      },
      '/teams/rocket/about',
      '/teams/:slug/about',
    );

    const cover = await screen.findByRole('region', { name: 'Rocket' });
    expect(cover).toHaveTextContent('No team fee');
    expect(cover).not.toHaveTextContent('Applications open');
    // The team's menu has the ways elsewhere; the cover has none.
    expect(within(cover).queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Membership' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Write it' })).toHaveAttribute(
      'href',
      '/teams/rocket/manage/page',
    );
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
