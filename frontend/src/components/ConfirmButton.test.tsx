import { act, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderPage } from '../test/render';
import { ConfirmButton } from './ConfirmButton';

afterEach(() => {
  vi.useRealTimers();
});

describe('the two-step confirm', () => {
  it('asks again first, and only the second click does it', () => {
    const onConfirm = vi.fn();
    renderPage(
      <ConfirmButton confirmLabel="Yes, erase" onConfirm={onConfirm}>
        Erase
      </ConfirmButton>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Erase' }));
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Yes, erase' }));

    expect(onConfirm).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Erase' })).toBeInTheDocument();
  });

  it('disarms by itself after a while', () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    renderPage(
      <ConfirmButton confirmLabel="Yes, erase" onConfirm={onConfirm} timeoutMs={1000}>
        Erase
      </ConfirmButton>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Erase' }));
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Erase' }));

    expect(onConfirm).not.toHaveBeenCalled();
  });
});
