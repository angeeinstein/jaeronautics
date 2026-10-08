import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../api/client';
import { makeMe } from '../test/fixtures';
import { mockFetch, renderPage } from '../test/render';
import { TeamLayout, teamSidebar } from './TeamLayout';

afterEach(() => {
  vi.unstubAllGlobals();
});

function team(overrides: Partial<Schemas['TeamPageOut']> = {}): Schemas['TeamPageOut'] {
  return {
    slug: 'rocket',
    name: 'Rocket',
    description: null,
    logo_url: null,
    about: null,
    about_html: null,
    picture_url: null,
    photos: [],
    member_count: 3,
    fee: null,
    labels: { singular: 'Team', plural: 'Teams' },
    is_member: true,
    membership: {
      status: 'active',
      status_label: 'Member',
      ongoing: true,
      fee: null,
      notes: [],
      meeting: null,
      actions: [],
      join_label: null,
      why_not: null,
    },
    sees_team_page: true,
    can_manage: false,
    can_edit_page: false,
    can_see_money: false,
    permissions: [],
    role: null,
    applications_waiting: null,
    access_list_enabled: false,
    lead_missing: false,
    rules: null,
    joining: null,
    members: [],
    ...overrides,
  };
}

const labels = (content: ReturnType<typeof teamSidebar>) =>
  content.groups.map((group) => [group.label, ...group.items.map((item) => item.label)]);

describe("a team's menu", () => {
  it('a member: the team, nothing to run', () => {
    expect(labels(teamSidebar(team()))).toEqual([['Team', 'Overview', 'About the team']]);
  });

  it('a lead: its people, money and settings too, with the applications waiting', () => {
    const content = teamSidebar(
      team({
        role: 'Lead',
        applications_waiting: 2,
        access_list_enabled: true,
        permissions: ['team.view_members', 'team.edit_settings', 'team.send_access_list', 'team.view_money'],
      }),
    );

    expect(labels(content)).toEqual([
      ['Team', 'Overview', 'About the team'],
      ['People', 'Applications', 'Members', 'Former members'],
      ['Money', 'Money'],
      ['Settings', 'Team page', 'Joining', 'Access list', 'Roles'],
    ]);
    expect(content.groups[1]?.items[0]?.count).toBe(2);
  });

  it("a site admin: the admins' settings besides, in a group of their own", () => {
    const content = teamSidebar(
      team({
        administers: true,
        permissions: ['team.view_members', 'team.edit_settings', 'team.view_money', 'team.appoint_leads'],
      }),
    );

    expect(labels(content).at(-1)).toEqual(['Admin', 'Details', 'Fee', 'Archive']);
    expect(
      labels(teamSidebar(team({ role: 'Lead', permissions: ['team.view_members'] }))).flat(),
    ).not.toContain('Admin');
  });

  it('a treasurer: the money, nothing about its people', () => {
    expect(labels(teamSidebar(team({ permissions: ['team.view_money'] })))).toEqual([
      ['Team', 'Overview', 'About the team'],
      ['Money', 'Money'],
    ]);
  });
});

describe("a team's frame", () => {
  function show(body: Schemas['TeamPageOut']) {
    mockFetch({
      '/api/v1/me': { body: makeMe() },
      '/api/v1/teams/rocket': { body },
    });
    renderPage(<TeamLayout />, { route: '/teams/rocket/about', path: '/teams/:slug/about' });
  }

  it('the menu for somebody in the team', async () => {
    show(team());

    const [menu] = await screen.findAllByRole('navigation', { name: 'Sections' });
    if (!menu) throw new Error('No menu');
    expect(within(menu).getByRole('link', { name: 'Overview' })).toHaveAttribute('href', '/teams/rocket');
  });

  it('none for somebody who is not in it', async () => {
    show(
      team({
        sees_team_page: false,
        is_member: true,
        membership: { ...team().membership, status: null, ongoing: false },
      }),
    );

    await screen.findByRole('navigation', { name: 'Areas' });
    expect(screen.queryByRole('navigation', { name: 'Sections' })).not.toBeInTheDocument();
  });

  it('says so to whoever runs it when the team has no lead', async () => {
    show(team({ permissions: ['team.view_members'], lead_missing: true }));

    expect(await screen.findByText('This team has no active lead.')).toBeInTheDocument();
  });
});
