/**
 * After an update the server has a new build and the files of the old one are
 * gone, so a page still open from before cannot load the next part it needs.
 * Then the page is loaded again, with the new build -- but not twice in a row:
 * if that did not help, the error page says what went wrong.
 */
const KEY = 'jaeronautics:reloaded-for-new-build';
const AGAIN_AFTER_MS = 30_000;

interface Host {
  addEventListener: (type: 'vite:preloadError', listener: (event: Event) => void) => void;
  sessionStorage: Pick<Storage, 'getItem' | 'setItem'>;
  location: Pick<Location, 'reload'>;
}

export function reloadOnStaleBuild(host: Host = window, now: () => number = Date.now) {
  host.addEventListener('vite:preloadError', (event) => {
    let last = 0;
    try {
      last = Number(host.sessionStorage.getItem(KEY) ?? 0);
    } catch {
      // No storage (a private window): reload anyway, a loop is unlikely.
    }
    if (now() - last < AGAIN_AFTER_MS) return;
    event.preventDefault();
    try {
      host.sessionStorage.setItem(KEY, String(now()));
    } catch {
      // As above.
    }
    host.location.reload();
  });
}
