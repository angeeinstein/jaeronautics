# The Member Portal's Front End

React and TypeScript, built with Vite; Mantine for components, themed with
the portal's design (`src/theme.ts`, `src/styles/global.css`,
`docs/design.md`). It draws the pages listed in `src/app/paths.json`; every
other address is still one of Flask's own pages. The plan and the order in
which pages move: `docs/frontend-plan.md`.

## Working on it

```bash
cd frontend
npm ci                     # once, and after package-lock.json changed
npm run dev                # http://127.0.0.1:5173, with Flask on :5000 behind it
```

`npm run dev` serves the app with hot reloading and passes everything else --
the API, the pages not moved yet, static files -- to Flask on port 5000
(`flask --app aeronautics_members.app:create_app run`). Sign in through Flask's
login page as usual; the session cookie is shared.

Before committing:

```bash
npm run typecheck && npm run lint && npm run format:check && npm test
```

## The API's types

`src/api/schema.d.ts` is generated from the API's description, which Flask
builds from the endpoints' own declarations (`aeronautics_members/api/`).
After changing the API, regenerate both files (from the repository root):

```bash
DATABASE_URL=sqlite:////tmp/schema.db python -m flask --app aeronautics_members.app:create_app \
  api-schema --out frontend/src/api/openapi.json
(cd frontend && npm run api:types)
```

CI fails when they are not current. Call the API through `src/api/client.ts`:
`await call(api.GET('/api/v1/me'))` -- typed paths, bodies and answers, the
CSRF token for changes, a renewed token when one has expired, and the login
page when the session has ended.

## Tests

- `npm test` -- component tests (Vitest, `src/**/*.test.ts(x)`).
- `npm run e2e` -- end-to-end tests (Playwright, `e2e/`): the built app in
  Chromium against the real Flask app on a throwaway database with sample
  people (`python scripts/visual/snapshot.py serve`, started for you). Run
  `npm run build` first. Every test fails on a console error or anything the
  security policy blocked.

## How it is served

`npm run build` writes to `aeronautics_members/static/app/` (not committed),
which nginx serves under `/static/`. Flask answers each address in
`paths.json` with its `index.html` (`aeronautics_members/blueprints/app_shell.py`),
putting the page's nonce in for the security policy
(`aeronautics_members/content_security.py`). On the server, `install.sh`
installs Node.js and builds on every install and update.

## Adding a page

1. Its endpoints in `aeronautics_members/api/`, with tests; regenerate the types.
2. The page in `src/pages/`, added to `src/app/routes.tsx` (loaded lazily)
   and its address to `src/app/paths.json`.
3. Its Flask route answers `app_shell()`; the old template and its page tests go.
4. Tick it off in `docs/frontend-routes.md`.

Links: use `AppLink` (`src/app/AppLink.tsx`) for every internal link -- it
moves within the app where the page is the app's, and loads the page where it
is still Flask's.
