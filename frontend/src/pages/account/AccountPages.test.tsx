import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { DeleteAccount } from './DeleteAccount';
import { Password } from './Password';
import { PictureUpload } from './Picture';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base: Answers = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe() },
};

const picture: Schemas['PictureOut'] = {
  upload: true,
  replacing: false,
  pending_url: null,
  rejected_reason: null,
  formats: 'JPG, PNG, WebP, AVIF',
  max_bytes: 1000,
  max_label: '1 KB',
};

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error('No file input on the page.');
  return input;
}

describe('the picture upload', () => {
  beforeEach(() => {
    // jsdom draws nothing; the crop is what is checked.
    vi.stubGlobal(
      'createImageBitmap',
      vi.fn(() => Promise.resolve({ width: 400, height: 300, close: vi.fn() })),
    );
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as never;
  });

  it('a photo too large is refused before it is sent', async () => {
    mockFetch(base);
    renderPage(<PictureUpload picture={picture} />);

    const input = fileInput();
    await userEvent.upload(input, new File(['x'.repeat(2000)], 'me.jpg', { type: 'image/jpeg' }));

    expect(await screen.findByText(/too large/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Send for review' })).not.toBeInTheDocument();
  });

  it('framed and sent with the square chosen', async () => {
    const { calls } = mockFetch({
      ...base,
      'POST /api/v1/account/picture': { status: 201, body: {} },
    });
    renderPage(<PictureUpload picture={picture} />);

    const input = fileInput();
    await userEvent.upload(input, new File(['x'], 'me.jpg', { type: 'image/jpeg' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Zoom in' }));
    await userEvent.click(screen.getByRole('button', { name: 'Send for review' }));

    expect(await screen.findByText(/waiting for review/)).toBeInTheDocument();
    const sent = calls.find((call) => call.method === 'POST');
    const query = new URL(sent?.url ?? 'http://x').searchParams;
    expect(Number(query.get('zoom'))).toBeCloseTo(1.1);
    expect(query.get('x')).toBe('0.5');
  });

  it('the server’s word on the photo is shown with it', async () => {
    mockFetch({
      ...base,
      'POST /api/v1/account/picture': {
        status: 400,
        body: {
          error: {
            code: 'validation_error',
            message: 'Please upload a valid JPG, PNG, WebP or AVIF image.',
            fields: { image: 'Please upload a valid JPG, PNG, WebP or AVIF image.' },
          },
        },
      },
    });
    renderPage(<PictureUpload picture={picture} />);

    const input = fileInput();
    await userEvent.upload(input, new File(['x'], 'me.jpg', { type: 'image/jpeg' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Send for review' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Please upload a valid JPG');
  });
});

describe('the password', () => {
  it('the new one twice, the same', async () => {
    const { calls } = mockFetch(base);
    renderPage(<Password />, { route: '/change-password', path: '/change-password' });

    await userEvent.type(screen.getByLabelText(/^Current password/), 'old-one-1');
    await userEvent.type(screen.getByLabelText(/^New password(?! again)/), 'new-one-12');
    await userEvent.type(screen.getByLabelText(/^New password again/), 'new-one-13');
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }));

    expect(await screen.findByText('The two new passwords are not the same.')).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'PUT')).toBe(false);
  });

  it('a wrong current one is said at its field', async () => {
    mockFetch({
      ...base,
      'PUT /api/v1/account/password': {
        status: 400,
        body: {
          error: {
            code: 'validation_error',
            message: 'Please check the form.',
            fields: { current_password: 'This is not your current password.' },
          },
        },
      },
    });
    renderPage(<Password />, { route: '/change-password', path: '/change-password' });

    await userEvent.type(screen.getByLabelText(/^Current password/), 'a-guess');
    await userEvent.type(screen.getByLabelText(/^New password(?! again)/), 'new-one-12');
    await userEvent.type(screen.getByLabelText(/^New password again/), 'new-one-12');
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }));

    expect(await screen.findByText('This is not your current password.')).toBeInTheDocument();
  });
});

describe('deleting from the emailed link', () => {
  const impact: Schemas['DeletionOut'] = {
    has_forum_account: true,
    has_stripe_customer: true,
    subscription_active: true,
    paid_until: '2026-12-31',
    is_last_admin: false,
    export_url: '/account/data-export',
  };

  it('says what is lost first, and deletes only on the second click', async () => {
    const assign = vi.fn();
    vi.stubGlobal('location', { origin: window.location.origin, href: window.location.href, assign });
    const { calls } = mockFetch({
      ...base,
      'GET /api/v1/account/deletion/tok': { body: impact },
      'POST /api/v1/account/deletion/tok': { body: { tone: 'success', text: 'Deleted.' } },
    });
    renderPage(<DeleteAccount />, { route: '/account/delete/tok', path: '/account/delete/:token' });

    expect(await screen.findByText(/You have paid until 31\.12\.2026/)).toBeInTheDocument();
    expect(screen.getByText(/Stripe, who keep their own record/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Delete my account' }));
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Yes, delete it' }));

    await waitFor(() => {
      expect(assign).toHaveBeenCalledWith('/');
    });
  });

  it('a link that no longer works says so', async () => {
    mockFetch({
      ...base,
      'GET /api/v1/account/deletion/old': {
        status: 400,
        body: {
          error: {
            code: 'link_invalid',
            message: 'This deletion link is invalid or has expired. Please start again.',
          },
        },
      },
    });
    renderPage(<DeleteAccount />, { route: '/account/delete/old', path: '/account/delete/:token' });

    expect(await screen.findByText(/invalid or has expired/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete my account' })).not.toBeInTheDocument();
  });
});
