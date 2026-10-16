import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { type Answers, mockFetch, renderPage } from '../../test/render';
import { LinkCard } from './LinkCard';

const OPEN = '/api/v1/card-links/tok3n/open';
const USE = '/api/v1/card-links/tok3n';

function opened(signedIn: boolean) {
  return {
    claim: 'claim-1',
    card: '…E5F6',
    reader: 'Rocket fridge',
    place: 'Workshop',
    tapped_at: '2026-10-14T12:02:00Z',
    expires_at: '2026-10-14T12:12:00Z',
    signed_in: signedIn,
  };
}

function show(answers: Answers) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: false, csrf_token: 'token' } },
    ...answers,
  });
  renderPage(<LinkCard />, { route: '/link/tok3n', path: '/link/:token' });
  return fetched;
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('a card linked with a reader’s QR code', () => {
  it('opened before signing in: the card and the reader, and the way to sign in and come back', async () => {
    const { calls } = show({ [`POST ${OPEN}`]: { body: opened(false) } });

    expect(
      await screen.findByText(/Tapped on Rocket fridge \(Workshop\) at 14\.10\.2026 14:02/),
    ).toBeVisible();
    expect(screen.getByText('Card …E5F6')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Sign in' })).toHaveAttribute(
      'href',
      `/login?next=${encodeURIComponent('/link/tok3n')}`,
    );
    // The first open has no claim; the one it is given is kept for coming back.
    const open = calls.find((call) => call.url.endsWith(OPEN));
    expect(await open?.clone().json()).toEqual({ claim: null });
    expect(window.localStorage.getItem('card-link:tok3n')).toBe('claim-1');
  });

  it('signed in, back with its claim: named and linked, then greeted', async () => {
    window.localStorage.setItem('card-link:tok3n', 'claim-1');
    const { calls } = show({
      [`POST ${OPEN}`]: { body: opened(true) },
      [`POST ${USE}`]: {
        status: 201,
        body: {
          first_name: 'Anna',
          card: {
            id: 4,
            name: 'Student card',
            tail: '…E5F6',
            state: 'active',
            linked_at: '2026-10-14T12:03:00Z',
            linked_how: 'Tapped on a reader, linked by its owner',
            last_used_at: null,
            last_used_on: null,
          },
        },
      },
    });

    await userEvent.type(await screen.findByRole('textbox', { name: /^Name/ }), 'Student card');
    await userEvent.click(screen.getByRole('button', { name: 'Link to my account' }));

    expect(await screen.findByText('Linked, hi Anna')).toBeVisible();
    expect(screen.getByRole('status')).toHaveTextContent('Student card (…E5F6) is linked to your account.');
    const open = calls.find((call) => call.url.endsWith(OPEN));
    expect(await open?.clone().json()).toEqual({ claim: 'claim-1' });
    const use = calls.find((call) => call.url.endsWith(USE));
    expect(await use?.clone().json()).toEqual({ claim: 'claim-1', name: 'Student card' });
    expect(window.localStorage.getItem('card-link:tok3n')).toBeNull();
  });

  it('open on another phone, or run out: it says to tap the card again', async () => {
    show({
      [`POST ${OPEN}`]: {
        status: 409,
        body: {
          error: {
            code: 'card_link_taken',
            message: 'This link is open on another phone. Tap your card on the reader again for a new one.',
          },
        },
      },
    });

    expect(await screen.findByText(/open on another phone/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Link to my account' })).not.toBeInTheDocument();
  });
});
