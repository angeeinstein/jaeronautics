/**
 * Telling the server somebody is using this page (docs/presence.md), so that
 * Settings › Updates can show whether anybody is there before an update.
 *
 * Only while the tab is in front and somebody clicked, typed or scrolled in
 * the last minute: about every half minute then, and at once on a new page
 * or when somebody starts typing.
 * A tab left open, refreshing itself, says nothing. What is sent: a random id
 * for this tab, the page as the app's address pattern ("/teams/:slug" -- never
 * a token or an id from the address), and whether somebody typed since.
 * Failures are ignored: it is a hint, never in anybody's way.
 */
import { useEffect } from 'react';
import { matchPath, useLocation } from 'react-router';

import { api } from '../api/client';
import { APP_PATHS } from '../app/paths';

/** Clicked or typed this lately: somebody is there. */
export const IDLE_AFTER_MS = 60_000;
/** At most this often, unless the page changed. */
export const EVERY_MS = 30_000;
/** On a new page, but not more often than this. */
const SOONEST_MS = 2_000;

const TAB_KEY = 'ja-presence-tab';

function newTabId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `t${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`.slice(0, 36);
}

let tab: string | null = null;

/** This tab's id: the same across reloads of the tab, another in a new tab. */
export function presenceTab(): string {
  if (tab) return tab;
  try {
    tab = window.sessionStorage.getItem(TAB_KEY);
    if (!tab) {
      tab = newTabId();
      window.sessionStorage.setItem(TAB_KEY, tab);
    }
  } catch {
    tab ??= newTabId();
  }
  return tab;
}

/** The app's address pattern for ``path``, or "other". */
export function pagePattern(path: string): string {
  return APP_PATHS.find((pattern) => matchPath({ path: pattern, end: true }, path) !== null) ?? 'other';
}

interface State {
  page: string;
  lastInteraction: number;
  lastSent: number;
  typed: boolean;
}

const state: State = { page: 'other', lastInteraction: 0, lastSent: 0, typed: false };

function send(soonest: number) {
  const now = Date.now();
  if (document.visibilityState !== 'visible') return;
  if (now - state.lastInteraction > IDLE_AFTER_MS) return;
  if (now - state.lastSent < soonest) return;
  state.lastSent = now;
  const typed = state.typed;
  state.typed = false;
  api
    .POST('/api/v1/presence', { body: { tab: presenceTab(), page: state.page, typed } })
    .catch(() => undefined);
}

function interacted() {
  state.lastInteraction = Date.now();
  send(EVERY_MS);
}

/** Somebody starting to type is said soon, not with the next half minute. */
function typedIn() {
  state.typed = true;
  state.lastInteraction = Date.now();
  send(SOONEST_MS);
}

/** Mounted once, at the root of the app. */
export function usePresence() {
  const location = useLocation();

  useEffect(() => {
    const options = { capture: true, passive: true } as const;
    window.addEventListener('pointerdown', interacted, options);
    window.addEventListener('keydown', interacted, options);
    window.addEventListener('wheel', interacted, options);
    window.addEventListener('input', typedIn, options);
    const timer = window.setInterval(() => {
      send(state.typed ? SOONEST_MS : EVERY_MS);
    }, EVERY_MS / 3);
    return () => {
      window.removeEventListener('pointerdown', interacted, options);
      window.removeEventListener('keydown', interacted, options);
      window.removeEventListener('wheel', interacted, options);
      window.removeEventListener('input', typedIn, options);
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    state.page = pagePattern(location.pathname);
    send(SOONEST_MS);
  }, [location.pathname]);
}

/** For the tests: as if the page had just loaded. */
export function resetPresence() {
  tab = null;
  Object.assign(state, { page: 'other', lastInteraction: 0, lastSent: 0, typed: false });
}
