import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { type Answers, mockFetch, renderPage } from '../../test/render';
import { LegalTexts } from './LegalTexts';

type LegalOut = Schemas['LegalOut'];

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const API = '/api/v1/admin/legal';

function legal(overrides: Partial<LegalOut> = {}): LegalOut {
  return {
    in_force: [
      {
        title: 'Statuten',
        team_name: null,
        version: '2019-03-17',
        revision: null,
        effective_from: '2019-03-17',
        page_url: '/legal/statutes',
        pdf_url: '/legal/statutes/pdf',
      },
    ],
    teams_without_rules: ['Glider Team'],
    waiting: [
      {
        title: 'Datenschutzerklärung',
        team_name: null,
        version: '2099-01-01',
        revision: null,
        status: 'scheduled',
        effective_from: '2099-01-01',
        has_english: true,
        pdf_url: '/admin/legal/waiting.pdf?document=privacy-policy&version=2099-01-01',
      },
    ],
    pdf_jobs: [{ key: '/statutes/2019-03-17', title: 'Statuten', team_name: null, version: '2019-03-17' }],
    problems: [],
    preview_max_kb: 1024,
    template_url: '/admin/legal/template',
    ...overrides,
  };
}

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    [API]: { body: legal() },
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    ...answers,
  });
  renderPage(<LegalTexts />, { route: '/admin/legal', path: '/admin/legal' });
  return fetched;
}

describe('what there is', () => {
  it('the texts in force, the teams without rules, and what is waiting', async () => {
    show();

    const inForce = await screen.findByRole('region', { name: 'In force' });
    expect(within(inForce).getByRole('link', { name: 'Statuten' })).toHaveAttribute(
      'href',
      '/legal/statutes',
    );
    expect(inForce).toHaveTextContent('Version of 17.03.2019 · in force since 17.03.2019');
    expect(inForce).toHaveTextContent('Glider Team · None in force');
    const waiting = screen.getByRole('region', { name: 'Not in force yet' });
    expect(waiting).toHaveTextContent('Datenschutzerklärung');
    expect(waiting).toHaveTextContent('with English');
    expect(waiting).toHaveTextContent('From 01.01.2099');
    expect(screen.getByText('Every file in legal/ is in order.')).toBeInTheDocument();
  });

  it('what is wrong with the files', async () => {
    show({ [API]: { body: legal({ problems: ['stray.md: not where a version goes'] }) } });

    expect(await screen.findByText('stray.md: not where a version goes')).toBeInTheDocument();
  });
});

describe('a PDF link', () => {
  it('has the PDF made first, then opens it', async () => {
    const opened = vi.spyOn(window, 'open').mockReturnValue({ opener: null } as Window);
    show({ '/legal/statutes/pdf': { body: { url: '/legal/statutes/pdf?v=abc' } } });
    const inForce = await screen.findByRole('region', { name: 'In force' });

    await userEvent.click(within(inForce).getByRole('link', { name: 'PDF' }));

    await waitFor(() => {
      expect(opened).toHaveBeenCalledWith('/legal/statutes/pdf?v=abc', '_blank');
    });
  });

  it('says when it could not be made', async () => {
    show({ '/legal/statutes/pdf': { status: 500, body: { error: 'The PDF could not be made just now.' } } });
    const inForce = await screen.findByRole('region', { name: 'In force' });

    await userEvent.click(within(inForce).getByRole('link', { name: 'PDF' }));

    expect(await within(inForce).findByRole('alert')).toHaveTextContent(
      'The PDF could not be made just now.',
    );
  });
});

describe('the preview', () => {
  it('lists what keeps the text from being laid out', async () => {
    const { calls } = show({
      [`POST ${API}/preview`]: {
        status: 400,
        body: {
          error: {
            code: 'legal_preview_invalid',
            message: 'The text was not laid out.',
            fields: null,
            details: { problems: ['2026-10-05.md: no front matter'] },
          },
        },
      },
    });
    const button = await screen.findByRole('button', { name: 'Make the PDF' });
    expect(button).toBeDisabled();

    // The field is a button; the file goes to the input beside it.
    const [germanInput] = document.querySelectorAll<HTMLInputElement>('input[type="file"]');
    await userEvent.upload(
      germanInput ?? document.body,
      new File(['no front matter'], '2026-10-05.md', { type: 'text/markdown' }),
    );
    await userEvent.click(button);

    expect(await screen.findByText('2026-10-05.md: no front matter')).toBeInTheDocument();
    const request = calls.find((call) => call.method === 'POST');
    expect(request?.headers.get('Content-Type')).toMatch(/^multipart\/form-data; boundary=/);
    expect(request?.headers.get('X-CSRFToken')).toBe('token');
  });
});

describe('making every PDF again', () => {
  it('removes the kept ones, then makes each, ticking them off', async () => {
    const { calls } = show({
      [`POST ${API}/pdfs/forget`]: { body: { removed: 3 } },
      [`POST ${API}/pdfs/make`]: {
        status: 422,
        body: {
          error: { code: 'legal_pdf_failed', message: 'division by zero', fields: null, details: null },
        },
      },
    });

    await userEvent.click(await screen.findByRole('button', { name: 'Make all PDFs again' }));

    const steps = await screen.findByRole('list', { name: 'Making the PDFs' });
    await waitFor(() => {
      const [stored, statutes] = within(steps).getAllByRole('listitem');
      expect(stored).toHaveTextContent('Remove the kept PDFs: Done3 removed');
      expect(statutes).toHaveTextContent('Statuten · Version of 17.03.2019: Faileddivision by zero');
    });
    const made = calls.find((call) => new URL(call.url).pathname === `${API}/pdfs/make`);
    expect(await made?.clone().json()).toEqual({ key: '/statutes/2019-03-17' });
  });
});
