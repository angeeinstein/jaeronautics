import { screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Schemas } from '../../api/client';
import { mockFetch, renderPage } from '../../test/render';
import { LegalTextPage } from './LegalText';

afterEach(() => {
  vi.unstubAllGlobals();
});

function text(overrides: Partial<Schemas['LegalTextOut']> = {}): Schemas['LegalTextOut'] {
  return {
    title: 'Statuten',
    language: 'de',
    is_translation: false,
    version: '2026-11-05',
    revision: null,
    effective_from: '2026-12-01',
    in_force: '2019-03-17',
    upcoming: true,
    validity: 'Gültig ab 01.12.2026',
    html: '<p>Neu.</p>',
    contents: [],
    german_url: '/legal/statutes/de/2026-11-05',
    english_url: null,
    english_elsewhere: false,
    others: [{ version: '2019-03-17', in_force: true, url: '/legal/statutes/de', upcoming: false }],
    pdf_url: '/legal/statutes/pdf/2026-11-05',
    pdf_has_english: false,
    ...overrides,
  };
}

function show(data: Schemas['LegalTextOut']) {
  mockFetch({ '/api/v1/session': { body: { signed_in: false, csrf_token: 'token' } } });
  renderPage(<LegalTextPage text={data} />);
}

describe('a version announced for a later day', () => {
  it('says it is not in force yet, and which version applies until then', () => {
    show(text());

    expect(
      screen.getByText('Not in force yet; until then the version of 17.03.2019 applies.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '17.03.2019 (in force)' })).toBeInTheDocument();
  });

  it('is listed with its day beside the version in force', () => {
    show(
      text({
        version: '2019-03-17',
        effective_from: '2019-03-17',
        upcoming: false,
        others: [
          {
            version: '2026-11-05',
            in_force: false,
            url: '/legal/statutes/de/2026-11-05',
            upcoming: true,
            effective_from: '2026-12-01',
          },
        ],
      }),
    );

    expect(screen.getByRole('link', { name: '05.11.2026 (from 01.12.2026)' })).toBeInTheDocument();
    expect(screen.queryByText(/Not in force yet/)).not.toBeInTheDocument();
  });
});
