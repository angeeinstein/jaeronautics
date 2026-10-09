import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { mockFetch, renderPage } from '../../test/render';
import { Contact } from './Contact';
import { Unsubscribe } from './Unsubscribe';

afterEach(() => {
  vi.unstubAllGlobals();
});

const TOPICS = [
  { value: 'membership', label: 'Membership & payment' },
  { value: 'other', label: 'Something else' },
];

function show(signedIn: boolean) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: signedIn, csrf_token: 'token' } },
    'GET /api/v1/contact': {
      body: signedIn
        ? { topics: TOPICS, signed_in: true, name: 'Anna Berger', email: 'anna@example.com' }
        : { topics: TOPICS, signed_in: false, name: null, email: null },
    },
    'POST /api/v1/contact': { body: { message: 'Sent. We answer by email.' } },
  });
  renderPage(<Contact />);
  return fetched;
}

async function sent(calls: Request[]) {
  const post = calls.find((call) => call.method === 'POST');
  return (await post?.clone().json()) as Record<string, unknown>;
}

describe('the contact form', () => {
  it('a visitor gives a name and address, chooses what it is about, and sends', async () => {
    const user = userEvent.setup();
    const { calls } = show(false);
    await user.type(await screen.findByLabelText(/^Name/), 'Vera Visitor');
    await user.type(screen.getByLabelText(/^Email/), 'vera@example.net');
    await user.click(screen.getByRole('combobox', { name: /^About/ }));
    await user.click(await screen.findByRole('option', { name: 'Something else' }));
    await user.type(screen.getByLabelText(/^Subject/), 'A question');
    await user.type(screen.getByLabelText(/^Message/), 'Hello there');
    await user.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('status')).toHaveTextContent('Sent. We answer by email.');
    const body = await sent(calls);
    expect(body).toMatchObject({
      topic: 'other',
      name: 'Vera Visitor',
      email: 'vera@example.net',
      subject: 'A question',
      message: 'Hello there',
      website: null,
    });
    expect(typeof body.seconds).toBe('number');
  });

  it('signed in, it says who is writing and asks no name', async () => {
    show(true);
    expect(await screen.findByText(/As Anna Berger \(anna@example.com\)/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Name/)).not.toBeInTheDocument();
  });

  it('the field for bots is out of sight and out of the tab order', async () => {
    show(false);
    const trap = await screen.findByLabelText('Website');
    expect(trap).toHaveAttribute('tabindex', '-1');
    expect(trap.closest('[aria-hidden]')).not.toBeNull();
  });
});

describe('switching the news off from an email', () => {
  it('nothing changes until the button is pressed, and it can be undone', async () => {
    const user = userEvent.setup();
    const answers = {
      '/api/v1/session': { body: { signed_in: false, csrf_token: 'token' } },
      'GET /api/v1/news/abc': {
        body: { email: 'anna@example.com', subscribed: true, unsubscribed_at: null },
      },
      'PUT /api/v1/news/abc': {
        body: { email: 'anna@example.com', subscribed: false, unsubscribed_at: '2026-10-09T10:00:00Z' },
      },
    };
    const { calls } = mockFetch(answers);
    renderPage(<Unsubscribe />, { route: '/unsubscribe/abc', path: '/unsubscribe/:token' });

    expect(await screen.findByText('anna@example.com')).toBeInTheDocument();
    expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(0);
    await user.click(screen.getByRole('button', { name: 'No more news by email' }));
    expect(await screen.findByRole('status')).toHaveTextContent('No more news by email.');
    expect(screen.getByRole('button', { name: 'Subscribe again' })).toBeInTheDocument();
    await waitFor(() => {
      expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(1);
    });
  });
});
