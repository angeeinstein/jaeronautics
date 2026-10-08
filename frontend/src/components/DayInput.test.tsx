import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { DayInput, parseDay } from './DayInput';

describe('a day as the portal writes it', () => {
  it.each([
    ['06.10.2026', '2026-10-06'],
    ['6.1.2026', '2026-01-06'],
    [' 31.12.2026 ', '2026-12-31'],
    ['31.02.2026', null],
    ['2026-10-06', null],
    ['06.10.26', null],
    ['', null],
  ])('%s is %s', (text, day) => {
    expect(parseDay(text)).toBe(day);
  });

  it('shows the day as 06.10.2026 and takes one typed that way', async () => {
    let seen = '';
    function Field() {
      const [day, setDay] = useState('2026-10-06');
      seen = day;
      return <DayInput label="Sent on" value={day} onChange={setDay} />;
    }
    render(
      <MantineProvider>
        <Field />
      </MantineProvider>,
    );

    const field = screen.getByRole('textbox', { name: 'Sent on' });
    expect(field).toHaveValue('06.10.2026');
    await userEvent.clear(field);
    await userEvent.type(field, '01.09.2026');
    await userEvent.tab();

    expect(seen).toBe('2026-09-01');
  });
});
