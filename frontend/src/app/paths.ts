/**
 * Which addresses the app draws itself (paths.json). Everything else is
 * still one of Flask's pages, so a link there loads the whole page.
 */
import { matchPath } from 'react-router';

import appPaths from './paths.json';

export const APP_PATHS: readonly string[] = appPaths.paths;

/** Whether ``href`` (a path, maybe with ?query or #hash) is drawn by the app. */
export function isAppPath(href: string): boolean {
  if (!href.startsWith('/') || href.startsWith('//')) return false;
  const path = href.split(/[?#]/)[0] ?? href;
  return APP_PATHS.some((pattern) => matchPath({ path: pattern, end: true }, path) !== null);
}
