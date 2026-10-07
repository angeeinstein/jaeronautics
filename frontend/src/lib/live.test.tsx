import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useLiveRefresh } from './live';

function Page({ ask }: { ask: () => Promise<number> }) {
  const data = useQuery({ queryKey: ['figure'], queryFn: ask });
  useLiveRefresh(['figure'], 30_000);
  return (
    <form>
      <span>{data.data ?? '…'}</span>
      <input aria-label="Note" />
    </form>
  );
}

function show() {
  let figure = 0;
  const ask = vi.fn(() => Promise.resolve(++figure));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <Page ask={ask} />
    </QueryClientProvider>,
  );
  return ask;
}

let visibility: DocumentVisibilityState = 'visible';

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  visibility = 'visible';
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('a page that refreshes itself', () => {
  it('asks again every so often, and shows the answer in place', async () => {
    const ask = show();
    expect(await screen.findByText('1')).toBeInTheDocument();

    await act(() => vi.advanceTimersByTimeAsync(30_000));

    expect(await screen.findByText('2')).toBeInTheDocument();
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it('not while the tab is out of view', async () => {
    const ask = show();
    await screen.findByText('1');
    visibility = 'hidden';

    await act(() => vi.advanceTimersByTimeAsync(90_000));

    expect(ask).toHaveBeenCalledTimes(1);
  });

  it('not while somebody types into a form on it', async () => {
    const ask = show();
    await screen.findByText('1');
    screen.getByRole('textbox', { name: 'Note' }).focus();

    await act(() => vi.advanceTimersByTimeAsync(90_000));
    expect(ask).toHaveBeenCalledTimes(1);

    screen.getByRole('textbox', { name: 'Note' }).blur();
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(ask).toHaveBeenCalledTimes(2);
  });
});
