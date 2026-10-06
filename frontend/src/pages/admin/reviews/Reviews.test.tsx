import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Reviews } from './Reviews';

type ReviewsOut = Schemas['ReviewsOut'];
type QueueItem = Schemas['QueueItem'];

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/reviews';

const picture: QueueItem = {
  kind: 'picture',
  id: 3,
  at: '2026-10-01T08:00:00Z',
  person: { user_id: 11, name: 'Petra Pending', email: 'petra@example.org' },
  change: null,
  picture: { image_url: '/forum/avatar/public/tok', forum_username: 'PendingP_L25' },
};

const nameChange: QueueItem = {
  kind: 'name_change',
  id: 5,
  at: '2026-10-02T08:00:00Z',
  person: { user_id: 12, name: 'Anna Huber', email: 'anna@example.org' },
  change: {
    is_name_change: true,
    requested_full_name: 'Anna Maier',
    member_note: 'Married in September.',
    changes: [{ field: 'last_name', label: 'Last name', current: 'Huber', requested: 'Maier' }],
    forum_username: { current: 'HuberA_L25', suggested: 'MaierA_L25' },
  },
  picture: null,
};

function reviews(overrides: Partial<ReviewsOut> = {}): ReviewsOut {
  return {
    waiting: { name_changes: 1, pictures: 1, sync_problems: 0 },
    queue: [picture, nameChange],
    sync_problems: [],
    ...overrides,
  };
}

function show(data: ReviewsOut = reviews(), answers: Answers = {}) {
  const fetched = mockFetch({
    [API]: { body: data },
    '/api/v1/me': { body: makeMe() },
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    ...answers,
  });
  renderPage(<Reviews />, { route: '/admin/reviews', path: '/admin/reviews' });
  return fetched;
}

async function sent(calls: Request[], path: string): Promise<unknown> {
  const request = calls
    .filter((call) => call.method === 'POST' && new URL(call.url).pathname === path)
    .at(-1);
  expect(request, path).toBeDefined();
  return request ? request.clone().json() : undefined;
}

describe('the queue', () => {
  it('one card per item, each with its own decision', async () => {
    show();

    const pictureCard = await screen.findByRole('region', { name: 'Profile picture: Petra Pending' });
    const changeCard = screen.getByRole('region', { name: 'Name change: Anna Huber' });
    expect(within(pictureCard).getByRole('img', { name: /Petra Pending/ })).toHaveAttribute(
      'src',
      '/forum/avatar/public/tok',
    );
    expect(
      within(pictureCard).getByRole('button', { name: 'Approve and send to forum' }),
    ).toBeInTheDocument();
    expect(within(changeCard).getByText('Anna Maier')).toBeInTheDocument();
    expect(within(changeCard).getByText('“Married in September.”')).toBeInTheDocument();
    expect(within(changeCard).getByRole('table', { name: 'What changes' })).toHaveTextContent(
      'Last nameHuber→ Maier',
    );
  });

  it('approves a picture with the note', async () => {
    const { calls } = show(reviews(), {
      [`POST ${API}/pictures/3/approve`]: { body: { forum_error: null } },
    });
    const card = await screen.findByRole('region', { name: 'Profile picture: Petra Pending' });

    await userEvent.type(within(card).getByLabelText('Note to the member (optional)'), 'Nice one');
    await userEvent.click(within(card).getByRole('button', { name: 'Approve and send to forum' }));

    expect(await sent(calls, `${API}/pictures/3/approve`)).toEqual({ note: 'Nice one' });
  });

  it('rejects only after a second click', async () => {
    const { calls } = show(reviews(), { [`POST ${API}/name-changes/5/reject`]: { status: 204 } });
    const card = await screen.findByRole('region', { name: 'Name change: Anna Huber' });

    await userEvent.click(within(card).getByRole('button', { name: 'Reject' }));
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    await userEvent.click(within(card).getByRole('button', { name: 'Yes, reject' }));

    expect(await sent(calls, `${API}/name-changes/5/reject`)).toEqual({ note: null });
  });

  it('keeps the forum username unless asked to change it', async () => {
    const { calls } = show(reviews(), {
      [`POST ${API}/name-changes/5/approve`]: { body: { rename_pending: false, forum_error: null } },
    });
    const card = await screen.findByRole('region', { name: 'Name change: Anna Huber' });
    expect(within(card).getByRole('textbox', { name: 'New forum username' })).toBeDisabled();

    await userEvent.click(within(card).getByRole('button', { name: 'Approve' }));

    expect(await sent(calls, `${API}/name-changes/5/approve`)).toEqual({ note: null, forum_username: null });
  });

  it('renames on the forum when ticked, to the suggested name or another', async () => {
    const { calls } = show(reviews(), {
      [`POST ${API}/name-changes/5/approve`]: { body: { rename_pending: false, forum_error: null } },
    });
    const card = await screen.findByRole('region', { name: 'Name change: Anna Huber' });

    await userEvent.click(
      within(card).getByRole('checkbox', { name: /Also change the forum username from HuberA_L25/ }),
    );
    const field = within(card).getByRole('textbox', { name: 'New forum username' });
    expect(field).toHaveValue('MaierA_L25');
    await userEvent.clear(field);
    await userEvent.type(field, 'AnnaM');
    await userEvent.click(within(card).getByRole('button', { name: 'Approve' }));

    expect(await sent(calls, `${API}/name-changes/5/approve`)).toEqual({
      note: null,
      forum_username: 'AnnaM',
    });
  });

  it('nothing waiting is one line', async () => {
    show(reviews({ queue: [], waiting: { name_changes: 0, pictures: 0, sync_problems: 0 } }));

    expect(await screen.findByText('Nothing waiting for review')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Forum sync problems' })).toBeNull();
  });

  it('says when only the oldest are shown', async () => {
    show(reviews({ waiting: { name_changes: 30, pictures: 40, sync_problems: 0 } }));

    expect(
      await screen.findByText('The oldest 2 of 70. The rest appear here as these are decided.'),
    ).toBeInTheDocument();
  });
});

describe('the rest of the page', () => {
  it('sync problems link to the account’s Forum tab', async () => {
    show(
      reviews({
        waiting: { name_changes: 0, pictures: 0, sync_problems: 1 },
        queue: [],
        sync_problems: [
          {
            user_id: 11,
            email: 'petra@example.org',
            error: 'The forum could not be reached.',
            last_synced_at: null,
          },
        ],
      }),
    );

    const table = await screen.findByRole('table', { name: 'Forum sync problems' });
    expect(within(table).getByRole('link', { name: 'petra@example.org' })).toHaveAttribute(
      'href',
      '/admin/accounts/11#forum',
    );
    expect(within(table).getByText('The forum could not be reached.')).toBeInTheDocument();
  });

  it('the history is folded until asked for', async () => {
    show(reviews(), {
      [`${API}/history`]: {
        body: {
          items: [
            {
              kind: 'name_change',
              id: 1,
              at: '2026-09-01T10:00:00Z',
              is_name_change: true,
              requested_full_name: 'Bernd Back',
              member_email: 'bernd@example.org',
              decision: 'approved',
              by: 'boss@example.org',
            },
          ],
          page: 1,
          pages: 1,
          total: 1,
        },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Show past decisions' }));

    const table = await screen.findByRole('table', { name: 'Past decisions' });
    expect(table).toHaveTextContent('Bernd Back');
    expect(table).toHaveTextContent('Approved');
  });

  it('a refusal by the server is shown', async () => {
    show(reviews(), {
      [`POST ${API}/pictures/3/approve`]: {
        status: 409,
        body: {
          error: {
            code: 'already_decided',
            message: 'That profile picture is no longer waiting for review.',
            fields: null,
            details: null,
          },
        },
      },
    });
    const card = await screen.findByRole('region', { name: 'Profile picture: Petra Pending' });

    await userEvent.click(within(card).getByRole('button', { name: 'Approve and send to forum' }));

    expect(
      await screen.findByText('That profile picture is no longer waiting for review.'),
    ).toBeInTheDocument();
  });
});
