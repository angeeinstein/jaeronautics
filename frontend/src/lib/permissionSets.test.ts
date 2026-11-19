import { describe, expect, it } from 'vitest';

import { groupByArea, untick, withNeeds, withoutUnmet } from './permissionSets';

const needs = new Map([
  ['money.change', ['money.see', 'admin']],
  ['money.see', ['admin']],
  ['cards', ['admin']],
]);

describe('permissions that need others', () => {
  it('a permission brings what it needs, and what that needs', () => {
    expect(withNeeds(['money.change'], needs)).toEqual(new Set(['money.change', 'money.see', 'admin']));
  });

  it('unticking one unticks what needs it', () => {
    expect(untick(['admin', 'money.see', 'money.change', 'cards'], 'money.see', needs)).toEqual(
      new Set(['admin', 'cards']),
    );
    expect(untick(['admin', 'money.see', 'cards'], 'admin', needs)).toEqual(new Set());
  });

  it('drops what lost its needs', () => {
    expect(withoutUnmet(['money.change', 'admin'], needs)).toEqual(new Set(['admin']));
  });

  it('groups by area in the order given', () => {
    const grouped = groupByArea([
      { area: 'B', name: 1 },
      { area: 'A', name: 2 },
      { area: 'B', name: 3 },
    ]);
    expect(grouped.map(([area, items]) => [area, items.map((item) => item.name)])).toEqual([
      ['B', [1, 3]],
      ['A', [2]],
    ]);
  });
});
