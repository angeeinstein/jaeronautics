/**
 * Which addresses the app draws itself (paths.json). Everything else is
 * Flask's -- a download, a PDF, the forum's sign-in -- so a link there loads
 * it from the server.
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
