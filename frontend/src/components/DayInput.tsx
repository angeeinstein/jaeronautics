/**
 * A day, as the portal writes it everywhere: 31.12.2026 -- typed, or picked
 * from a calendar. The value is the API's "2026-12-31", or "" for none; the
 * browser's own date field would show the browser's format instead.
 */
import { DateInput, type DateInputProps } from '@mantine/dates';

/** "31.12.2026" as "2026-12-31"; null when it is no day (31.02.2026 is none). */
export function parseDay(text: string): string | null {
  const match = /^(\d{1,2})\.(\d{1,2})\.(\d{4})$/.exec(text.trim());
  if (!match) return null;
  const [, day = '', month = '', year = ''] = match;
  const iso = `${year}-${month.padStart(2, '0')}-${day.padStart(2, '0')}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().startsWith(iso) ? iso : null;
}

interface DayInputProps extends Omit<DateInputProps, 'value' | 'onChange' | 'valueFormat' | 'dateParser'> {
  value: string;
  onChange: (day: string) => void;
}

export function DayInput({ value, onChange, ...rest }: DayInputProps) {
  return (
    <DateInput
      valueFormat="DD.MM.YYYY"
      placeholder="dd.mm.yyyy"
      dateParser={parseDay}
      firstDayOfWeek={1}
      value={value || null}
      onChange={(day) => {
        onChange(day ?? '');
      }}
      {...rest}
    />
  );
}
