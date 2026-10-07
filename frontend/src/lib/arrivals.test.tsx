import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useArrivals } from './arrivals';

function list(scope: string | null, ids: string[] | undefined) {
  return { scope, ids };
}

describe('what arrived in a list', () => {
  it('nothing, the first time the list is drawn', () => {
    const { result } = renderHook(({ scope, ids }) => useArrivals(scope, ids), {
      initialProps: list('all', ['1', '2']),
    });

    expect([...result.current]).toEqual([]);
  });

  it('what came in since', () => {
    const { result, rerender } = renderHook(({ scope, ids }) => useArrivals(scope, ids), {
      initialProps: list('all', ['1', '2']),
    });

    rerender(list('all', ['3', '1', '2']));

    expect([...result.current]).toEqual(['3']);
  });

  it('nothing, when another filter or page shows other rows', () => {
    const { result, rerender } = renderHook(({ scope, ids }) => useArrivals(scope, ids), {
      initialProps: list('page 1', ['1', '2']),
    });

    rerender(list('page 2', ['3', '4']));

    expect([...result.current]).toEqual([]);
  });

  it('nothing, while the next rows are on their way', () => {
    const { result, rerender } = renderHook(({ scope, ids }) => useArrivals(scope, ids), {
      initialProps: list('all', ['1']),
    });

    rerender(list('all', undefined));
    rerender(list('all', ['1']));

    expect([...result.current]).toEqual([]);
  });

  it('a row that left is no arrival', () => {
    const { result, rerender } = renderHook(({ scope, ids }) => useArrivals(scope, ids), {
      initialProps: list('all', ['1', '2']),
    });

    rerender(list('all', ['2']));

    expect([...result.current]).toEqual([]);
  });
});
