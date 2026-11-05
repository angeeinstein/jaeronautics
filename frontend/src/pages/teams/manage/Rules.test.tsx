import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Rules } from './Rules';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/teams/rocket/manage/rules';

const DRAFT: Schemas['RulesWaitingOut'] = {
  title: 'Teamordnung',
  status: 'draft',
  version: '2026-11-04',
  effective_from: '2026-11-04',
  has_english: false,
  pdf_url: '/teams/rocket/manage/rules/waiting.pdf?record=7',
  record_id: 7,
  uploaded_at: '2026-11-04T09:30:00Z',
  uploaded_by: 'Lea Lead',
  deletable: true,
};

function rules(overrides: Partial<Schemas['TeamRulesOut']> = {}): Schemas['TeamRulesOut'] {
  return {
    in_force: {
      title: 'Teamordnung',
      validity: 'Version 6.1 · Gültig ab 01.10.2026',
      page_url: '/teams/rocket/rules',
      pdf_url: '/teams/rocket/rules/pdf',
    },
    waiting: [],
    template_url: '/teams/rocket/manage/rules/template',
    ...overrides,
  };
}

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: rules() },
    ...answers,
  });
  renderPage(<Rules />, { route: '/teams/rocket/manage/rules', path: '/teams/:slug/manage/rules' });
  return fetched;
}

describe("a team's rules, for its leads", () => {
  it('the rules in force, and the template for new ones', async () => {
    show();

    const inForce = await screen.findByRole('region', { name: 'In force' });
    expect(inForce).toHaveTextContent('Version 6.1 · Gültig ab 01.10.2026');
    expect(screen.getByRole('link', { name: 'Download a template' })).toHaveAttribute(
      'href',
      '/teams/rocket/manage/rules/template',
    );
    expect(screen.getByText('No drafts, and no version waiting for its day.')).toBeInTheDocument();
  });

  it('a draft uploaded is listed with its PDF', async () => {
    const { calls } = show({
      [`POST ${API}/drafts`]: { body: { ...rules({ waiting: [DRAFT] }), record_id: 7 } },
    });
    const button = await screen.findByRole('button', { name: 'Upload as draft' });

    const [germanInput] = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    await userEvent.upload(
      germanInput ?? document.body,
      new File(['---'], 'rules.md', { type: 'text/markdown' }),
    );
    await userEvent.click(button);

    const waiting = await screen.findByRole('region', { name: 'Not in force yet' });
    await waitFor(() => {
      expect(waiting).toHaveTextContent('by Lea Lead');
    });
    expect(within(waiting).getByRole('link', { name: 'PDF' })).toHaveAttribute('href', DRAFT.pdf_url);
    const sent = calls.find((call) => call.method === 'POST');
    expect(sent?.headers.get('Content-Type')).toMatch(/^multipart\/form-data; boundary=/);
  });

  it('what is wrong with the file is listed', async () => {
    show({
      [`POST ${API}/drafts`]: {
        status: 400,
        body: {
          error: {
            code: 'legal_upload_invalid',
            message: 'The text was not uploaded.',
            fields: null,
            details: { problems: ['This is not the rules of Rocket Team.'] },
          },
        },
      },
    });
    const button = await screen.findByRole('button', { name: 'Upload as draft' });

    const [germanInput] = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    await userEvent.upload(
      germanInput ?? document.body,
      new File(['---'], 'rules.md', { type: 'text/markdown' }),
    );
    await userEvent.click(button);

    expect(await screen.findByText('This is not the rules of Rocket Team.')).toBeInTheDocument();
  });

  it('a draft is deleted, asked twice', async () => {
    const { calls } = show({
      [API]: { body: rules({ waiting: [DRAFT] }) },
      [`DELETE ${API}/drafts/7`]: { body: rules() },
    });
    const waiting = await screen.findByRole('region', { name: 'Not in force yet' });

    await userEvent.click(within(waiting).getByRole('button', { name: 'Delete' }));
    await userEvent.click(within(waiting).getByRole('button', { name: 'Yes, delete' }));

    expect(await screen.findByText('No drafts, and no version waiting for its day.')).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'DELETE')).toBe(true);
  });
});
