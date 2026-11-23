import { describe, expect, it } from 'vitest';

import { remoteDays, remoteMinutesOf } from './RemoteWorkForm';

describe('remote work form', () => {
  it('offers today and the days before, today first', () => {
    const days = remoteDays('2026-10-06', '2026-10-20');
    expect(days).toHaveLength(15);
    expect(days[0]).toEqual({ value: '2026-10-20', label: 'Today, Tue 20.10.2026' });
    expect(days[1]?.label).toBe('Yesterday, Mon 19.10.2026');
    expect(days.at(-1)?.value).toBe('2026-10-06');
  });

  it('counts past midnight up to 03:15 on the same attendance day', () => {
    expect(remoteMinutesOf('18:00', '20:30')).toBe(150);
    expect(remoteMinutesOf('22:00', '03:15')).toBe(315);
    expect(remoteMinutesOf('01:00', '02:00')).toBe(60);
    expect(remoteMinutesOf('20:00', '19:00')).toBe(0);
    expect(remoteMinutesOf('', '19:00')).toBe(0);
  });
});
