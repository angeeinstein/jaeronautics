/**
 * Dates and times as the portal shows them everywhere (docs/design.md):
 * 31.12.2026 and 31.12.2026 14:05, 24-hour, always in Vienna time. The API
 * sends days as YYYY-MM-DD and moments as UTC.
 */
const VIENNA = 'Europe/Vienna';

const dateTimeParts = new Intl.DateTimeFormat('en-GB', {
  timeZone: VIENNA,
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});

function part(parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes): string {
  return parts.find((item) => item.type === type)?.value ?? '';
}

/** A moment (UTC from the API) as "31.12.2026 14:05" in Vienna. */
export function formatDateTime(iso: string): string {
  const parts = dateTimeParts.formatToParts(new Date(iso));
  return `${part(parts, 'day')}.${part(parts, 'month')}.${part(parts, 'year')} ${part(parts, 'hour')}:${part(parts, 'minute')}`;
}

/** The day of a moment (UTC from the API) as "31.12.2026", in Vienna. */
export function formatDayOf(iso: string): string {
  const parts = dateTimeParts.formatToParts(new Date(iso));
  return `${part(parts, 'day')}.${part(parts, 'month')}.${part(parts, 'year')}`;
}

/** A day ("2026-12-31") as "31.12.2026" -- no time zone involved. */
export function formatDate(day: string): string {
  const [year, month, date] = day.split('-');
  return `${date ?? ''}.${month ?? ''}.${year ?? ''}`;
}

/** "account_email_changed" as "Account Email Changed", for codes from the log. */
export function titleFromCode(code: string): string {
  return code
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** "1 change request" / "2 change requests". */
export function plural(count: number, one: string, many: string): string {
  return count === 1 ? one : many;
}

const euroFormat = new Intl.NumberFormat('en-IE', { style: 'currency', currency: 'EUR' });

/** Cents as "€1,234.50", as the server writes them (services/team_money.py). */
export function formatEuros(cents: number): string {
  return euroFormat.format(cents / 100);
}

/** "Anna Berger" as "AB", for a picture that is not there. */
export function initialsOf(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  return (words.length > 1 ? [words[0]?.[0], words.at(-1)?.[0]] : [words[0]?.[0]]).join('').toUpperCase();
}

/** Minutes as "2 h 25 min", "45 min", "3 h": time counted at the readers. */
export function formatMinutes(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** The time of a moment (UTC from the API) as "14:05" in Vienna. */
export function formatTime(iso: string): string {
  const parts = dateTimeParts.formatToParts(new Date(iso));
  return `${part(parts, 'hour')}:${part(parts, 'minute')}`;
}

/** A moment (UTC from the API) as Vienna's day ("2026-12-31") and time ("14:05"), for a form. */
export function viennaParts(iso: string): { day: string; time: string } {
  const parts = dateTimeParts.formatToParts(new Date(iso));
  return {
    day: `${part(parts, 'year')}-${part(parts, 'month')}-${part(parts, 'day')}`,
    time: `${part(parts, 'hour')}:${part(parts, 'minute')}`,
  };
}

/**
 * The attendance day of a moment ("2026-10-14"): it runs from 03:15 to 03:15 in Vienna, so a stay begun
 * after midnight belongs to the evening before (services/attendance.py, ``DAY_DIVIDE``).
 */
export function attendanceDay(iso: string): string {
  const { day, time } = viennaParts(iso);
  if (time >= '03:15') return day;
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, date - 1)).toISOString().slice(0, 10);
}

/** How far Vienna's clock is ahead of UTC at ``moment``, in milliseconds. */
function viennaOffset(moment: number): number {
  const parts = dateTimeParts.formatToParts(new Date(moment));
  const wall = Date.UTC(
    Number(part(parts, 'year')),
    Number(part(parts, 'month')) - 1,
    Number(part(parts, 'day')),
    Number(part(parts, 'hour')),
    Number(part(parts, 'minute')),
  );
  return wall - Math.floor(moment / 60000) * 60000;
}

/** Vienna's day ("2026-12-31") and time ("14:05") as the moment in UTC, for the API. */
export function fromVienna(day: string, time: string): string {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number);
  const [hour = 0, minute = 0] = time.split(':').map(Number);
  const wall = Date.UTC(year, month - 1, date, hour, minute);
  // The offset at the guess, then again at the moment it gives: right on both sides of a clock change.
  let moment = wall - viennaOffset(wall);
  moment = wall - viennaOffset(moment);
  return new Date(moment).toISOString();
}
