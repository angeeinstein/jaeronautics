import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { makeMe } from '../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { Editor } from './Editor';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/legal/drafts/7';

function session(overrides: Partial<Schemas['EditorOut']> = {}): Schemas['EditorOut'] {
  return {
    record_id: 7,
    document: 'statutes',
    name: 'Statuten',
    team: null,
    team_name: null,
    mode: 'draft',
    german: { title: 'Statuten', revision: null, body: '## § 1 Name\n\nDer Verein heißt.' },
    english: null,
    edit_count: 3,
    edited_at: null,
    in_force: {
      german: { title: 'Statuten', revision: null, body: '## § 1 Name\n\nDer Verein hieß.' },
      english: null,
      validity: 'Gültig ab 17.03.2019',
    },
    opened_snapshot_id: 21,
    snapshots: [
      { id: 21, created_at: '2026-11-04T10:00:00Z', created_by: 'Anna Berger' },
      { id: 20, created_at: '2026-11-03T10:00:00Z', created_by: 'Lea Lead' },
    ],
    pdf_url: '/admin/legal/waiting.pdf?document=statutes&record=7',
    markdown_url: '/admin/legal/waiting.md?document=statutes&record=7',
    english_markdown_url: null,
    back_url: '/admin/legal',
    may_publish: true,
    approved_by: null,
    approved_on: null,
    effective_from: '2026-11-04',
    ...overrides,
  };
}

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [`POST ${API}/session`]: { body: session() },
    [`POST ${API}/check`]: {
      body: {
        html: '<h2 id="paragraph-1">§ 1 Name</h2><p>Der Verein heißt.</p>',
        contents: [],
        problems: [],
      },
    },
    [`PUT ${API}`]: { body: { edit_count: 4, edited_at: '2026-11-04T10:05:00Z' } },
    ...answers,
  });
  renderPage(<Editor />, { route: '/admin/legal/drafts/7', path: '/admin/legal/drafts/:recordId' });
  return fetched;
}

describe('the editor', () => {
  it('the text, its fields, and the page as it will be', async () => {
    show();

    expect(await screen.findByRole('heading', { name: 'Statuten', level: 1 })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Statuten');
    expect(screen.getByRole('textbox', { name: 'German text, Markdown' })).toHaveTextContent(
      'Der Verein heißt.',
    );
    // A narrow screen, as the tests have it: Write, Preview and Changes one at a time.
    await userEvent.click(screen.getByRole('radio', { name: 'Preview' }));
    expect(await screen.findByText('Nothing in the way of publishing it.')).toBeInTheDocument();
    expect(screen.getByText('Der Verein heißt.', { selector: 'p' })).toBeInTheDocument();
  });

  it('saves itself, naming the count it started from', async () => {
    const { calls } = show();
    const title = await screen.findByRole('textbox', { name: 'Title' });

    await userEvent.type(title, ' neu');

    await waitFor(
      () => {
        expect(calls.some((call) => call.method === 'PUT')).toBe(true);
      },
      { timeout: 4000 },
    );
    const sent = calls.find((call) => call.method === 'PUT');
    expect(await sent?.clone().json()).toMatchObject({
      german: { title: 'Statuten neu' },
      english: null,
      edit_count: 3,
    });
    expect(await screen.findByText(/^Saved/)).toBeInTheDocument();
  });

  it('says when somebody else saved meanwhile', async () => {
    show({
      [`PUT ${API}`]: {
        status: 409,
        body: {
          error: {
            code: 'legal_draft_changed',
            message: 'Somebody else saved.',
            fields: null,
            details: null,
          },
        },
      },
    });

    await userEvent.type(await screen.findByRole('textbox', { name: 'Title' }), '!');

    expect(
      await screen.findByText('Somebody else saved this draft meanwhile', {}, { timeout: 4000 }),
    ).toBeInTheDocument();
  });

  it('the changes against the version in force', async () => {
    show();

    await screen.findByRole('textbox', { name: 'Title' });
    await userEvent.click(screen.getByRole('radio', { name: 'Changes' }));

    expect(await screen.findByText(/heißt/, { selector: 'ins' })).toBeInTheDocument();
    expect(screen.getByText(/hieß/, { selector: 'del' })).toBeInTheDocument();
    expect(screen.getByText('1 word added, 1 word removed.')).toBeInTheDocument();
  });

  it('an earlier state put back', async () => {
    const { calls } = show({
      [`GET ${API}/snapshots/20`]: {
        body: { german: { title: 'Statuten', revision: null, body: 'Früher.' }, english: null },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'History' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('When you opened it');
    const [, earlier] = within(dialog).getAllByRole('button', { name: 'Put back' });
    await userEvent.click(earlier ?? dialog);

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'German text, Markdown' })).toHaveTextContent('Früher.');
    });
    expect(calls.some((call) => new URL(call.url).pathname === `${API}/snapshots/20`)).toBe(true);
  });

  it('an English translation added', async () => {
    show();

    await userEvent.click(await screen.findByRole('tab', { name: 'English (none)' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add an English translation' }));

    expect(await screen.findByRole('textbox', { name: 'English text, Markdown' })).toBeInTheDocument();
  });

  it('replaced with a file, as a change undo takes back', async () => {
    const { calls } = show({
      [`POST ${API}/file`]: {
        body: { german: { title: 'Statuten', revision: 'Rev 2', body: 'Aus der Datei.' }, english: null },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'File' }));
    expect(await screen.findByRole('menuitem', { name: 'Download Markdown' })).toHaveAttribute(
      'href',
      '/admin/legal/waiting.md?document=statutes&record=7',
    );
    await userEvent.click(screen.getByRole('menuitem', { name: 'Replace with a file…' }));
    const dialog = await screen.findByRole('dialog');
    const [germanInput] = dialog.querySelectorAll<HTMLInputElement>('input[type="file"]');
    await userEvent.upload(germanInput ?? dialog, new File(['---'], 'changed.md', { type: 'text/markdown' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Replace' }));

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'German text, Markdown' })).toHaveTextContent(
        'Aus der Datei.',
      );
    });
    expect(screen.getByRole('textbox', { name: 'Revision (optional)' })).toHaveValue('Rev 2');
    expect(calls.some((call) => new URL(call.url).pathname === `${API}/file`)).toBe(true);
  });

  it('no Publish for somebody who may only write', async () => {
    show({ [`POST ${API}/session`]: { body: session({ may_publish: false }) } });

    await screen.findByRole('heading', { name: 'Statuten', level: 1 });

    expect(screen.queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument();
  });
});
