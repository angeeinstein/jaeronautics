import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MantineProvider } from '@mantine/core';
import { describe, expect, it } from 'vitest';

import { type Stay, StayTable, totalMinutes } from './StayTable';

function stay(id: number, overrides: Partial<Stay> = {}): Stay {
  return {
    id,
    started_at: `2026-10-${String(id).padStart(2, '0')}T07:00:00Z`,
    ended_at: `2026-10-${String(id).padStart(2, '0')}T08:00:00Z`,
    minutes: 60,
    state: 'done',
    in_place: 'Hangar',
    out_place: 'Hangar',
    corrected_by: null,
    corrected_at: null,
    ...overrides,
  };
}

describe('a list of stays', () => {
  it('a long one shows the newest first, the rest behind Show all', async () => {
    const stays = Array.from({ length: 20 }, (_, index) => stay(20 - index));
    render(
      <MantineProvider>
        <StayTable stays={stays} label="Stays" />
      </MantineProvider>,
    );

    expect(screen.getAllByRole('row')).toHaveLength(1 + 15);
    await userEvent.click(screen.getByRole('button', { name: 'Show all 20' }));
    expect(screen.getAllByRole('row')).toHaveLength(1 + 20);
    expect(screen.queryByRole('button', { name: /Show all/ })).not.toBeInTheDocument();
  });

  it('remote work marked as not counted counts nothing', () => {
    expect(totalMinutes([stay(1), stay(2, { remote: true, not_counted_at: '2026-10-03T08:00:00Z' })])).toBe(
      60,
    );
  });
});
