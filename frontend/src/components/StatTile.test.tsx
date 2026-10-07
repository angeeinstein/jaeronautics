import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { renderPage } from '../test/render';
import { StatTile } from './StatTile';

function Live() {
  const [value, setValue] = useState(12);
  return (
    <>
      <button
        type="button"
        onClick={() => {
          setValue(13);
        }}
      >
        Someone joins
      </button>
      <StatTile value={value} label="Active members" />
    </>
  );
}

describe('a figure', () => {
  it('lights up for a moment when it changes while the page is open -- not when first shown', async () => {
    renderPage(<Live />);
    expect(screen.getByText('12')).not.toHaveClass('ja-changed');

    await userEvent.click(screen.getByRole('button', { name: 'Someone joins' }));

    expect(screen.getByText('13')).toHaveClass('ja-changed');
  });
});
