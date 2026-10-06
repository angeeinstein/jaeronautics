import '@testing-library/jest-dom/vitest';

import { notifications } from '@mantine/notifications';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
  // The notices live in one store for the whole run; at most three show at a
  // time, so one test's would hold back the next one's.
  notifications.clean();
  notifications.cleanQueue();
});

// jsdom has neither; Mantine asks for both.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }),
});

class ResizeObserverStub {
  observe = () => undefined;
  unobserve = () => undefined;
  disconnect = () => undefined;
}
Object.defineProperty(window, 'ResizeObserver', { writable: true, value: ResizeObserverStub });

// Nor document.fonts, which a growing textarea listens to for when the web
// fonts arrive.
Object.defineProperty(document, 'fonts', {
  configurable: true,
  value: {
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    ready: Promise.resolve(),
  },
});
