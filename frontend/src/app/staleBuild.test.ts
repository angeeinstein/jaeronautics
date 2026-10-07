import { describe, expect, it, vi } from 'vitest';

import { reloadOnStaleBuild } from './staleBuild';

function host() {
  const target = new EventTarget();
  const stored = new Map<string, string>();
  return {
    addEventListener: target.addEventListener.bind(target),
    fire: () => {
      const event = new Event('vite:preloadError', { cancelable: true });
      target.dispatchEvent(event);
      return event;
    },
    sessionStorage: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => void stored.set(key, value),
    },
    location: { reload: vi.fn() },
  };
}

describe('a part of an older build that is gone', () => {
  it('loads the page again, with the new build', () => {
    const page = host();
    reloadOnStaleBuild(page, () => 100_000);

    const event = page.fire();

    expect(page.location.reload).toHaveBeenCalledOnce();
    expect(event.defaultPrevented).toBe(true);
  });

  it('not again right after, so it cannot loop', () => {
    const page = host();
    let now = 100_000;
    reloadOnStaleBuild(page, () => now);

    page.fire();
    now += 5_000;
    const second = page.fire();

    expect(page.location.reload).toHaveBeenCalledOnce();
    expect(second.defaultPrevented).toBe(false); // the error page shows it
  });

  it('again for a later update', () => {
    const page = host();
    let now = 100_000;
    reloadOnStaleBuild(page, () => now);

    page.fire();
    now += 3_600_000;
    page.fire();

    expect(page.location.reload).toHaveBeenCalledTimes(2);
  });
});
