import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../../api/client';
import { makeMe } from '../../../test/fixtures';
import { type Answers, mockFetch, renderPage } from '../../../test/render';
import { Translation } from './Translation';

afterEach(() => {
  vi.unstubAllGlobals();
});

const API = '/api/v1/admin/settings/translation';

const SETTINGS: Schemas['TranslationOut'] = {
  services: [
    { name: 'deepl', label: 'DeepL', key_set: true },
    { name: 'azure', label: 'Azure AI Translator', key_set: false },
  ],
  azure_region: '',
  glossary: [['Vorstand', 'board']],
  deepl_used: 1234,
  deepl_limit: 500000,
};

function show(answers: Answers = {}) {
  const fetched = mockFetch({
    '/api/v1/session': { body: { signed_in: true, csrf_token: 'token' } },
    '/api/v1/me': { body: makeMe() },
    [API]: { body: SETTINGS },
    [`PUT ${API}`]: { body: { changed: ['order'] } },
    ...answers,
  });
  renderPage(<Translation />, { route: '/admin/settings/translation', path: '/admin/settings/translation' });
  return fetched;
}

describe('Settings › Translation', () => {
  it("the services, DeepL's characters this month, and the glossary", async () => {
    show();

    expect(await screen.findByText('1,234 of 500,000 characters used this month.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'German term 1' })).toHaveValue('Vorstand');
    expect(screen.getByRole('textbox', { name: 'English term 1' })).toHaveValue('board');
  });

  it('Azure first, a term added, saved', async () => {
    const { calls } = show();
    await screen.findByRole('textbox', { name: 'German term 1' });

    await userEvent.click(screen.getByRole('radio', { name: 'Azure AI Translator' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add a term' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'German term 2' }), 'Verein');
    await userEvent.type(screen.getByRole('textbox', { name: 'English term 2' }), 'association');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.find((call) => call.method === 'PUT');
    expect(await sent?.clone().json()).toMatchObject({
      order: ['azure', 'deepl'],
      glossary: [
        ['Vorstand', 'board'],
        ['Verein', 'association'],
      ],
      forget: [],
    });
  });

  it('a key tried', async () => {
    show({ [`POST ${API}/deepl/try`]: { body: { ok: true, message: 'The board decides.' } } });

    const services = await screen.findByRole('region', { name: 'Services' });
    await userEvent.click(within(services).getByRole('button', { name: 'Try it' }));

    expect(await screen.findByText('The board decides.')).toBeInTheDocument();
  });
});
