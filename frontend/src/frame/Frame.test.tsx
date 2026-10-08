import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
    ).toEqual(['Forum', 'Teams']);
    // The forum is the server's address: it signs a member in there.
    expect(within(areas).getByRole('link', { name: 'Forum' })).toHaveAttribute('href', '/forum');
  });

  it('leaves out Teams while teams are off, Admin without access, the Forum without a membership', async () => {
    admin(makeMe({ teams_area: false, admin_area: false }));
    const areas = await screen.findByRole('navigation', { name: 'Areas' });

    expect(
      within(areas)
        .getAllByRole('link')
        .map((link) => link.textContent),
    ).toEqual(['Forum']);
  });

  it('without a membership, no forum; the account is the menu at the right', async () => {
    admin(makeMe({ forum_area: false }));
    const areas = await screen.findByRole('navigation', { name: 'Areas' });

    expect(within(areas).queryByRole('link', { name: 'Forum' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Account menu for Anna Berger' }));
    expect(await screen.findByRole('menuitem', { name: 'My account' })).toHaveAttribute('href', '/account');
  });

  it('the person by their picture, where there is one', async () => {
    admin(makeMe({ picture_url: '/forum/avatar/public/abc' }));

    const button = await screen.findByRole('button', { name: 'Account menu for Anna Berger' });
    expect(button.querySelector('img')).toHaveAttribute('src', '/forum/avatar/public/abc');
  });

  it('marks the current page in the sidebar', async () => {
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

describe('the menu button', () => {
  function member(me = makeMe(), route = '/account') {
    mockFetch({ '/api/v1/me': { body: me } });
    return renderPage(
      <Frame narrow>
        <p>Page</p>
      </Frame>,
      { route },
    );
  }

  it('in the admin area folds its menu away and back', async () => {
    admin();

    const hide = await screen.findByRole('button', { name: 'Hide the admin menu' });
    await userEvent.click(hide);

    expect(screen.getByRole('button', { name: 'Show the admin menu' })).toBeInTheDocument();
  });

  it('elsewhere brings the admin menu in, for whoever may administer', async () => {
    member();

    await userEvent.click(await screen.findByRole('button', { name: 'Open the admin menu' }));

    const menu = await screen.findByRole('dialog', { name: 'Admin menu' });
    expect(within(menu).getByRole('link', { name: 'Accounts' })).toHaveAttribute('href', '/admin/accounts');
  });

  it('is not there for a member who administers nothing', async () => {
    member(makeMe({ admin_area: false, roles: [], permissions: [] }));

    await screen.findByRole('navigation', { name: 'Areas' });
    expect(screen.queryByRole('button', { name: /menu$/ })).not.toBeInTheDocument();
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
