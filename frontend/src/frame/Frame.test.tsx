import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { makeMe } from '../test/fixtures';
import { mockFetch, renderPage } from '../test/render';
import { adminSidebar } from './adminNavigation';
import { Frame } from './Frame';

afterEach(() => {
  vi.unstubAllGlobals();
});

function admin(me = makeMe()) {
  mockFetch({ '/api/v1/me': { body: me } });
  return renderPage(
    <Frame sidebar={adminSidebar} notices={(person) => person.admin_notices}>
      <p>Page</p>
    </Frame>,
    { route: '/admin' },
  );
}

describe('the frame', () => {
  it('shows the areas the person may use, the current one marked', async () => {
    admin();
    const areas = await screen.findByRole('navigation', { name: 'Areas' });

    expect(
      within(areas)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['My Account', 'Teams', 'Admin']);
    expect(within(areas).getByRole('link', { name: 'Admin' })).toHaveAttribute('aria-current', 'page');
  });

  it('leaves out Teams while teams are off, and Admin without access', async () => {
    admin(makeMe({ teams_area: false, admin_area: false }));
    const areas = await screen.findByRole('navigation', { name: 'Areas' });

    expect(
      within(areas)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['My Account']);
  });

  it('marks the current page in the sidebar and links pages not moved yet as plain links', async () => {
    admin();
    const sections = (await screen.findAllByRole('navigation', { name: 'Sections' }))[0];
    if (!sections) throw new Error('no sidebar');

    expect(within(sections).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page');
    expect(within(sections).getByRole('link', { name: 'Accounts' })).toHaveAttribute(
      'href',
      '/admin/accounts',
    );
  });

  it("shows the area's notices above the page", async () => {
    admin(
      makeMe({
        admin_notices: [
          { tone: 'warning', message: 'Background jobs are paused.', link_url: null, link_label: null },
        ],
      }),
    );

    expect(await screen.findByText('Background jobs are paused.')).toBeInTheDocument();
    expect(screen.getByText('Page')).toBeInTheDocument();
  });

  it('says so when the person cannot be loaded, with a way to try again', async () => {
    mockFetch({ '/api/v1/me': { status: 500, body: { error: { code: 'server_error', message: 'Down.' } } } });
    renderPage(
      <Frame>
        <p>Page</p>
      </Frame>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Down.');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.queryByText('Page')).toBeNull();
  });
});

describe('on the test server', () => {
  afterEach(() => {
    document.documentElement.removeAttribute('data-test-server');
  });

  it('every page says so', async () => {
    document.documentElement.setAttribute('data-test-server', '');
    admin();

    expect(await screen.findByRole('note')).toHaveTextContent('Test server · not the real membership portal');
  });

  it('the live site says nothing of it', async () => {
    admin();

    await screen.findByRole('navigation', { name: 'Areas' });
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });
});
