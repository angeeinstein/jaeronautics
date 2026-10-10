import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { makeMe } from '../../../test/fixtures';
import { mockFetch, renderPage } from '../../../test/render';
import { MessageLeads } from '../MessageLeads';
import { Messages } from './Messages';

afterEach(() => {
  vi.unstubAllGlobals();
});

const base = {
  '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
  '/api/v1/me': { body: makeMe() },
};

describe("a team's Messages page", () => {
  it('writes to the active members, lists what was sent and what came in', async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      ...base,
      'GET /api/v1/teams/rocket/manage/mailings': { body: { items: [], recipients: 12 } },
      'POST /api/v1/teams/rocket/manage/mailings': {
        status: 201,
        body: {
          id: 1,
          kind: 'team',
          kind_label: 'Team mailing',
          audience_label: 'The members of Rocket',
          subject: 'Meeting',
          body: 'Monday',
          assembly_at: null,
          author: 'Lena Lead',
          created_at: '2026-10-09T10:00:00Z',
          status: 'sending',
          finished_at: null,
          recipient_count: 12,
          unsubscribed_count: 0,
          sent: 0,
          failed: 0,
          waiting: 12,
          stopped: 0,
        },
      },
      'GET /api/v1/teams/rocket/manage/messages': { body: { items: [], open_count: 0 } },
    });
    renderPage(<Messages />, {
      route: '/teams/rocket/manage/messages',
      path: '/teams/:slug/manage/messages',
    });

    expect(await screen.findByText('Nothing sent yet.')).toBeInTheDocument();
    expect(await screen.findByText('Nothing open. All answered.')).toBeInTheDocument();
    await user.type(screen.getByLabelText(/^Subject/), 'Meeting');
    await user.type(screen.getByLabelText(/^Text/), 'Monday');
    await user.click(screen.getByRole('button', { name: 'Send to 12 people' }));
    await user.click(await screen.findByRole('button', { name: 'Yes, send to 12 people' }));
    await waitFor(() => {
      expect(calls.some((call) => call.method === 'POST' && call.url.endsWith('/manage/mailings'))).toBe(
        true,
      );
    });
  });
});

describe('message the leads', () => {
  it('a subject and a message, sent to the team', async () => {
    const user = userEvent.setup();
    const { calls } = mockFetch({
      ...base,
      'POST /api/v1/teams/rocket/message': {
        body: { message: "Sent to the team's leads. They answer by email." },
      },
    });
    renderPage(<MessageLeads slug="rocket" teamName="Rocket" />);
    await user.click(await screen.findByRole('button', { name: 'Message the leads' }));
    await user.type(await screen.findByLabelText(/^Subject/), 'Joining');
    await user.type(screen.getByRole('textbox', { name: /^Message/ }), 'Can I come?');
    await user.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Sent to the team');
    const post = calls.find((call) => call.method === 'POST');
    expect(await post?.clone().json()).toMatchObject({ subject: 'Joining', message: 'Can I come?' });
  });
});
