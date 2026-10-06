import { describe, expect, it } from 'vitest';

import { formatDate, formatDateTime, formatDayOf, titleFromCode } from './format';

describe('dates and times', () => {
  it('a moment in Vienna time, summer and winter', () => {
    expect(formatDateTime('2026-07-01T12:05:00Z')).toBe('01.07.2026 14:05');
    expect(formatDateTime('2026-12-31T23:30:00Z')).toBe('01.01.2027 00:30');
  });

  it('the day of a moment, in Vienna', () => {
    expect(formatDayOf('2026-12-31T23:30:00Z')).toBe('01.01.2027');
  });

  it('a day as it is', () => {
    expect(formatDate('2026-12-31')).toBe('31.12.2026');
  });

  it('codes from the log as words', () => {
    expect(titleFromCode('email_changed')).toBe('Email Changed');
  });
});
