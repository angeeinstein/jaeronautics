import { describe, expect, it } from 'vitest';

import { groupByArea, pick, permissionsOf, sameLevels, settle } from './permissionLevels';

const rows = [
  { key: 'accounts', area: 'Members', levels: [{ permissions: ['accounts.view'], needs: {} }] },
  {
    key: 'billing',
    area: 'Members',
    levels: [{ permissions: ['accounts.billing'], needs: { accounts: 1 } }],
  },
  {
    key: 'money',
    area: 'Money',
    levels: [
      { permissions: ['money.view'], needs: {} },
      { permissions: ['money.view', 'money.manage'], needs: {} },
    ],
  },
  { key: 'payouts', area: 'Money', levels: [{ permissions: ['payouts'], needs: { money: 2 } }] },
];

describe('levels that need others', () => {
  it('a level raises what it needs, and what that needs', () => {
    expect(pick(rows, {}, 'payouts', 1)).toEqual({ payouts: 1, money: 2 });
    expect(pick(rows, {}, 'billing', 1)).toEqual({ billing: 1, accounts: 1 });
  });

  it('lowering one lowers what needed it', () => {
    expect(pick(rows, { money: 2, payouts: 1 }, 'money', 1)).toEqual({ money: 1, payouts: 0 });
    expect(settle(rows, { billing: 1 })).toEqual({ billing: 0 });
  });

  it('saves what the chosen levels hold, the lower ones included', () => {
    expect(permissionsOf(rows, { money: 2, accounts: 1 })).toEqual([
      'accounts.view',
      'money.manage',
      'money.view',
    ]);
  });

  it('compares levels with none missing as none', () => {
    expect(sameLevels({ money: 0, accounts: 1 }, { accounts: 1 })).toBe(true);
  });

  it('groups by area in the order given', () => {
    expect(groupByArea(rows).map(([area, listed]) => [area, listed.map((row) => row.key)])).toEqual([
      ['Members', ['accounts', 'billing']],
      ['Money', ['money', 'payouts']],
    ]);
  });
});
