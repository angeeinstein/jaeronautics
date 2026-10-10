import { describe, expect, it } from 'vitest';

import { makeMe } from '../test/fixtures';
import { adminSidebar } from './adminNavigation';
import { containsCurrent, isCurrent } from './navigation';

describe('which entry is the current page', () => {
  it('matches the address and the pages below it', () => {
    const accounts = { label: 'Accounts', to: '/admin/accounts' };
    expect(isCurrent(accounts, '/admin/accounts')).toBe(true);
    expect(isCurrent(accounts, '/admin/accounts/12')).toBe(true);
    expect(isCurrent(accounts, '/admin/accounts-old')).toBe(false);
  });

  it('only the address itself for an exact entry', () => {
    const dashboard = { label: 'Dashboard', to: '/admin', exact: true };
    expect(isCurrent(dashboard, '/admin')).toBe(true);
    expect(isCurrent(dashboard, '/admin/accounts')).toBe(false);
  });

  it('an entry pointing into a page by its anchor', () => {
    const general = { label: 'General', to: '/admin/settings#settings-general' };
    expect(isCurrent(general, '/admin/settings', '#settings-general')).toBe(true);
    expect(isCurrent(general, '/admin/settings', '#settings-forum')).toBe(false);
    const settings = { label: 'Settings', to: '/admin/settings', children: [general] };
    expect(containsCurrent(settings, '/admin/settings', '#settings-general')).toBe(true);
  });
});

function labels(me = makeMe()) {
  return adminSidebar(me).groups.map((group) => [group.label, group.items.map((item) => item.label)]);
}

describe('the admin sidebar', () => {
  it('everything for somebody who may do everything', () => {
    expect(labels()).toEqual([
      ['Overview', ['Dashboard']],
      ['People', ['Accounts', 'Reviews']],
      ['Teams', ['Teams', 'Money']],
      ['System', ['Logs', 'Legal texts', 'Settings']],
    ]);
  });

  it('only what a permission allows, and no empty group', () => {
    const treasurer = makeMe({ permissions: ['admin.access', 'teams.money'] });
    expect(labels(treasurer)).toEqual([
      ['Overview', ['Dashboard']],
      ['Teams', ['Money']],
    ]);
  });

  it('the settings sections with secrets only with settings.credentials', () => {
    const me = makeMe({ permissions: ['admin.access', 'settings.general', 'notifications.manage'] });
    const settings = adminSidebar(me)
      .groups.at(-1)
      ?.items.find((item) => item.label === 'Settings');
    expect(settings?.children?.map((child) => child.label)).toEqual([
      'General',
      'Notifications',
      'Credit',
      'Mailings',
      'Test email',
    ]);
  });

  it('every section, each at its own address, for somebody who may do everything', () => {
    const settings = adminSidebar(makeMe())
      .groups.at(-1)
      ?.items.find((item) => item.label === 'Settings');
    expect(settings?.children?.map((child) => child.to)).toEqual([
      '/admin/settings/general',
      '/admin/settings/notifications',
      '/admin/settings/credit',
      '/admin/settings/mailings',
      '/admin/settings/billing',
      '/admin/settings/forum',
      '/admin/settings/mail',
      '/admin/settings/test-email',
      '/admin/settings/health',
      '/admin/settings/updates',
      '/admin/settings/backup',
    ]);
  });

  it('teams under the name they have here, and the count of reviews waiting', () => {
    const me = makeMe({ team_labels: { singular: 'Crew', plural: 'Crews' }, counts: { reviews_waiting: 3 } });
    const groups = adminSidebar(me).groups;
    expect(groups[2]?.label).toBe('Crews');
    expect(groups[1]?.items.find((item) => item.label === 'Reviews')?.count).toBe(3);
  });
});
