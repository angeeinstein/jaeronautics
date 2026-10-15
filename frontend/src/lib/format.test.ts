import { describe, expect, it } from 'vitest';

import {
  formatDate,
  formatDateTime,
  formatDayOf,
  formatMinutes,
  formatTime,
  fromVienna,
  titleFromCode,
  viennaParts,
} from './format';

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

  it('minutes as hours and minutes', () => {
    expect(formatMinutes(145)).toBe('2 h 25 min');
    expect(formatMinutes(45)).toBe('45 min');
    expect(formatMinutes(180)).toBe('3 h');
    expect(formatMinutes(0)).toBe('0 min');
  });

  it('Vienna time to UTC and back, on both sides of a clock change', () => {
    expect(fromVienna('2026-07-01', '14:05')).toBe('2026-07-01T12:05:00.000Z');
    expect(fromVienna('2026-12-31', '00:30')).toBe('2026-12-30T23:30:00.000Z');
    expect(fromVienna('2026-10-25', '04:00')).toBe('2026-10-25T03:00:00.000Z');
    expect(fromVienna('2026-03-29', '01:30')).toBe('2026-03-29T00:30:00.000Z');
    expect(viennaParts('2026-07-01T12:05:00Z')).toEqual({ day: '2026-07-01', time: '14:05' });
    expect(formatTime('2026-12-31T23:30:00Z')).toBe('00:30');
  });
});
