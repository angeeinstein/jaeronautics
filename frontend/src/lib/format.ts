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
