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
    { name: 'llm', label: 'Language model', key_set: false },
  ],
  azure_region: '',
  llm_provider: 'anthropic',
  llm_model: '',
  llm_address: '',
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
  it("the services, DeepL's characters used, and the glossary", async () => {
    show();

    expect(await screen.findByText('1,234 of 500,000 characters used.')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'German term 1' })).toHaveValue('Vorstand');
    expect(screen.getByRole('textbox', { name: 'English term 1' })).toHaveValue('board');
  });

  it('the order a list, Azure moved up to the top, a term added, saved', async () => {
    const { calls } = show();
    const order = await screen.findByRole('list', { name: 'Order the services are tried in' });
    expect(
      within(order)
        .getAllByRole('listitem')
        .map((row) => row.textContent),
    ).toEqual([
      expect.stringContaining('DeepL'),
      expect.stringContaining('Azure AI Translator'),
      expect.stringContaining('Language model'),
    ]);
    expect(within(order).getAllByRole('listitem')[1]).toHaveTextContent('Not set up: skipped');
    expect(within(order).getByRole('button', { name: 'Move DeepL up' })).toBeDisabled();

    await userEvent.click(within(order).getByRole('button', { name: 'Move Azure AI Translator up' }));
    await userEvent.click(screen.getByRole('button', { name: 'Add a term' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'German term 2' }), 'Verein');
    await userEvent.type(screen.getByRole('textbox', { name: 'English term 2' }), 'association');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.find((call) => call.method === 'PUT');
    expect(await sent?.clone().json()).toMatchObject({
      order: ['azure', 'deepl', 'llm'],
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

  it('a language model on a server of ones own: model and address, no key needed', async () => {
    const { calls } = show();
    const services = await screen.findByRole('region', { name: 'Services' });

    await userEvent.click(within(services).getByRole('radio', { name: 'Another server' }));
    await userEvent.type(within(services).getByRole('textbox', { name: /Model/ }), 'a-model');
    await userEvent.type(
      within(services).getByRole('textbox', { name: /Address/ }),
      'http://localhost:11434/v1',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(calls.some((call) => call.method === 'PUT')).toBe(true);
    });
    const sent = calls.find((call) => call.method === 'PUT');
    expect(await sent?.clone().json()).toMatchObject({
      llm_provider: 'other',
      llm_model: 'a-model',
      llm_address: 'http://localhost:11434/v1',
      llm_key: null,
    });
  });
});
