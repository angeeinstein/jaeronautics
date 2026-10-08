import type { Me } from '../api/session';

/** A signed-in admin who may do everything, unless told otherwise. */
export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: 1,
    email: 'anna@example.org',
    first_name: 'Anna',
    last_name: 'Berger',
    forum_username: 'anna',
    picture_url: null,
    roles: ['admin'],
    permissions: [
      'accounts.view',
      'admin.access',
      'approvals.review',
      'forum.moderate',
      'logs.view',
      'notifications.manage',
      'settings.credentials',
      'settings.general',
      'system.backup',
      'system.update',
      'teams.manage',
      'teams.money',
    ],
    admin_area: true,
    forum_area: true,
    forum_ready: false,
    teams_area: true,
    team_labels: { singular: 'Team', plural: 'Teams' },
    counts: { reviews_waiting: 0 },
    admin_notices: [],
    ...overrides,
  };
}
