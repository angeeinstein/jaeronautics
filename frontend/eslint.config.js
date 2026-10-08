// Linting, checked in CI like ruff is for the Python side: the recommended
// rules with type information, and React's rules of hooks.
import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['node_modules', 'src/api/schema.d.ts'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.strictTypeChecked,
      tseslint.configs.stylisticTypeChecked,
    ],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // Numbers in messages ("3 waiting") are the common, harmless case.
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
    },
  },
  // React's rules of hooks for the app; Playwright's fixtures (e2e/) only look like hooks.
  { files: ['src/**/*.{ts,tsx}'], extends: [reactHooks.configs.flat['recommended-latest']] },
  {
    files: ['eslint.config.js', 'postcss.config.js'],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
);
