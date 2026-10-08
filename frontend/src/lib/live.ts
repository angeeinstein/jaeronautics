/**
 * Pages whose figures and lists change while somebody looks at them -- the
 * dashboard, the accounts, the reviews, a team's people, money -- refresh
 * themselves: every so often, only while the tab is in view, and not while
 * somebody is typing into a form on the page, where new data could reset what
 * they typed. Coming back to the tab refreshes at once (Providers.tsx).
 *
 * Asked again, not pushed: a connection held open per tab would take one of
 * the server's few request slots for as long as the page is open.
 */
import { type QueryKey, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

export const EVERY_30_SECONDS = 30_000;
export const EVERY_MINUTE = 60_000;

/** Whether somebody is typing into a field of a form on the page. */
function typing(): boolean {
  const field = document.activeElement;
  return (
    field instanceof HTMLElement &&
    field.matches('input, textarea, select, [contenteditable="true"]') &&
    field.closest('form') !== null
  );
}

/** Ask for the data under ``queryKey`` again every ``everyMs``, while the page shows it. */
export function useLiveRefresh(queryKey: QueryKey, everyMs: number, enabled = true) {
  const client = useQueryClient();
  // The key as text: a new array each render would restart the timer each render.
  // Read back the same way TanStack Query compares keys (as JSON).
  const key = JSON.stringify(queryKey);
  useEffect(() => {
    if (!enabled) return undefined;
    const asked = JSON.parse(key) as QueryKey;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== 'visible' || typing()) return;
      void client.refetchQueries({ queryKey: asked, exact: true, type: 'active' });
    }, everyMs);
    return () => {
      window.clearInterval(timer);
    };
  }, [client, key, everyMs, enabled]);
}
