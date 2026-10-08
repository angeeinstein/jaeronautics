// Visits every page listed by snapshot.py, takes full-page screenshots and
// reports what can be seen to be wrong. Run through snapshot.py, not directly.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const [manifestPath, outDir] = process.argv.slice(2);
const { base, password, pages } = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));

// The screens members use: a large monitor, a 13-inch laptop, a tablet either
// way round, a phone. The laptop is also shot with the device in dark mode,
// which must change nothing.
const MODES = [
  { suffix: 'desktop', viewport: { width: 1920, height: 1080 }, colorScheme: 'light' },
  { suffix: 'laptop', viewport: { width: 1280, height: 800 }, colorScheme: 'light' },
  { suffix: 'laptop-dark', viewport: { width: 1280, height: 800 }, colorScheme: 'dark' },
  { suffix: 'tablet', viewport: { width: 820, height: 1180 }, colorScheme: 'light', isMobile: true, hasTouch: true },
  { suffix: 'tablet-landscape', viewport: { width: 1180, height: 820 }, colorScheme: 'light', isMobile: true, hasTouch: true },
  { suffix: 'phone', viewport: { width: 390, height: 844 }, colorScheme: 'light', isMobile: true, hasTouch: true },
];

// Runs inside the page. Returns the problems a person would notice.
function inspect() {
  const problems = { sideways_scroll: [], overlapping: [], sticking_out: [] };
  const describe = (el) => {
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('alt') || '')
      .trim().replace(/\s+/g, ' ').slice(0, 40);
    const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean).slice(0, 2).join('.');
    return `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}${text ? ` "${text}"` : ''}`;
  };
  const visible = (el) => {
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
    // Inside a folded <details>: laid out, but not drawn.
    if (el.checkVisibility && !el.checkVisibility()) return false;
    const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2;
  };

  const doc = document.documentElement;
  if (doc.scrollWidth > window.innerWidth + 1) {
    const culprits = [...document.querySelectorAll('body *')]
      .filter((el) => visible(el) && el.getBoundingClientRect().right > window.innerWidth + 1)
      .filter((el) => ![...el.children].some((c) => c.getBoundingClientRect().right > window.innerWidth + 1));
    problems.sideways_scroll.push(
      `page is ${doc.scrollWidth}px wide in a ${window.innerWidth}px window; widest: ` +
      culprits.slice(0, 3).map(describe).join(', '));
  }

  // Things a person reads or clicks -- and the cards holding them -- lying on
  // top of one another. With a dialog open, only what is in it: the page
  // behind is meant to be covered.
  const dialog = [...document.querySelectorAll('[role=dialog]')].find(visible);
  const targets = [...(dialog ?? document).querySelectorAll(
    'a, button, input:not([type=hidden]), select, textarea, label, h1, h2, h3, h4, h5, p, dt, dd, img, .badge, .alert, .card')]
    // The short messages after an action float over the page by design, and
    // so does what stands on a picture marked as a backdrop (a team's cover).
    .filter((el) => visible(el) && !el.closest('.mantine-Notifications-root') && !el.hasAttribute('data-backdrop'));
  // A link wrapping onto a second line is two boxes, not the one around both.
  const boxes = (el) => (getComputedStyle(el).display === 'inline' ? [...el.getClientRects()] : [el.getBoundingClientRect()]);
  // A field and what sits inside its box by design (the eye of a password field).
  const field = (el) => el.closest('.mantine-Input-wrapper');
  const overlap = (ra, rb) => {
    const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
    const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
    if (w <= 2 || h <= 2) return false;
    return (w * h) / Math.min(ra.width * ra.height, rb.width * rb.height) >= 0.15;
  };
  for (let i = 0; i < targets.length; i++) {
    for (let j = i + 1; j < targets.length; j++) {
      const a = targets[i], b = targets[j];
      if (a.contains(b) || b.contains(a)) continue;
      if (field(a) && field(a) === field(b)) continue;
      if (!boxes(a).some((ra) => boxes(b).some((rb) => overlap(ra, rb)))) continue;
      problems.overlapping.push(`${describe(a)}  <->  ${describe(b)}`);
    }
  }

  // A card running past the edge of the column it sits in -- as a card set to
  // full height does when another card shares its column: it takes the
  // column's height on top of the other card's and spills into what follows.
  for (const card of document.querySelectorAll('.card')) {
    const parent = card.parentElement;
    if (!visible(card) || !parent || getComputedStyle(parent).overflow !== 'visible') continue;
    const rc = card.getBoundingClientRect(), rp = parent.getBoundingClientRect();
    if (rc.bottom > rp.bottom + 2 || rc.right > rp.right + 2) {
      problems.sticking_out.push(`${describe(card)} runs ${Math.round(Math.max(rc.bottom - rp.bottom, rc.right - rp.right))}px past its column`);
    }
  }

  // Text or controls wider than the box that holds them, spilling out of it.
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || el.children.length > 0 && !['TD', 'DD', 'P', 'SPAN', 'A', 'LABEL', 'BUTTON'].includes(el.tagName)) continue;
    const style = getComputedStyle(el);
    if (style.overflowX !== 'visible') continue;
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) {
      problems.sticking_out.push(`${describe(el)} needs ${el.scrollWidth}px, has ${el.clientWidth}px`);
    }
  }
  return problems;
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const sessions = {};
  const findings = {};

  async function sessionFor(user) {
    if (!user) return undefined;
    if (!sessions[user]) {
      const context = await browser.newContext();
      const page = await context.newPage();
      await page.goto(`${base}/login`);
      await page.getByLabel(/^Email/).fill(user);
      await page.getByLabel(/^Password/).fill(password);
      await Promise.all([
        page.waitForURL((url) => !url.pathname.startsWith('/login')),
        page.getByRole('button', { name: 'Sign in', exact: true }).click(),
      ]);
      sessions[user] = await context.storageState();
      await context.close();
    }
    return sessions[user];
  }

  for (const entry of pages) {
    for (const mode of MODES) {
      const context = await browser.newContext({
        viewport: mode.viewport, colorScheme: mode.colorScheme, isMobile: mode.isMobile,
        hasTouch: mode.hasTouch, storageState: await sessionFor(entry.user), reducedMotion: 'reduce',
      });
      const page = await context.newPage();
      await page.goto(`${base}${entry.path}`, { waitUntil: 'networkidle' });
      if (entry.open) {
        // Something on the page opened first (a card's *Edit*, a choice that moves on by
        // itself): shown as it then is.
        await page.click(entry.open);
        await page.waitForTimeout(800);
      }
      if (entry.submit) {
        // A form sent as it is (its button): shown with the server's answer.
        await page.click(entry.submit);
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(200);
        // Back to the top, as the page is shot whole: scrolled, the fixed top bar lies over its middle.
        await page.evaluate(() => window.scrollTo(0, 0));
      }
      if (entry.click) {
        // Something that opens over the page (a dialog): shown open.
        await page.click(entry.click);
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(200);
      }
      // Times of day ("Uploaded 2026-09-27 08:30", "Last synced ...") differ
      // from one run to the next; zeroed, they don't show up as a change.
      await page.evaluate(() => {
        const clock = /\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?|\d{2}\.\d{2}\.\d{4},? \d{2}:\d{2}(:\d{2})?/g;
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          if (clock.test(node.nodeValue)) {
            node.nodeValue = node.nodeValue.replace(clock, (time) => time.replace(/\d/g, '0'));
          }
          clock.lastIndex = 0;
        }
      });
      const shot = `${entry.name}__${mode.suffix}`;
      await page.screenshot({ path: path.join(outDir, `${shot}.png`), fullPage: true });
      if (!mode.suffix.endsWith('-dark')) findings[shot] = await page.evaluate(inspect);
      await context.close();
    }
    process.stdout.write('.');
  }
  fs.writeFileSync(path.join(outDir, 'findings.json'), JSON.stringify(findings, null, 2));
  await browser.close();
})().catch((error) => { console.error(error); process.exit(1); });
