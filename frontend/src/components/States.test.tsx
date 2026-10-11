import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, UPDATING } from '../api/client';
import { Providers } from '../app/Providers';
import { ErrorState, UPDATING_RETRY_MS } from './States';

function show(error: unknown, onRetry = vi.fn()) {
  render(
    <Providers env="test">
      <ErrorState error={error} onRetry={onRetry} />
    </Providers>,
  );
  return onRetry;
}

afterEach(() => {
  vi.useRealTimers();
});

describe('a page that could not be loaded', () => {
  it('says so, and offers to try again', () => {
    show(new ApiError(500, 'unexpected', 'Something went wrong on our side. Please try again later.'));
    expect(screen.getByText('This could not be loaded')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('during an update says that instead, and loads again by itself', () => {
    vi.useFakeTimers();
    const retry = show(new ApiError(503, UPDATING, 'The portal is being updated.'));

    expect(screen.getByText('Update in progress')).toBeInTheDocument();
    expect(screen.queryByText('This could not be loaded')).not.toBeInTheDocument();
    expect(retry).not.toHaveBeenCalled();
    vi.advanceTimersByTime(UPDATING_RETRY_MS);
    expect(retry).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(UPDATING_RETRY_MS);
    expect(retry).toHaveBeenCalledTimes(2);
  });
});
