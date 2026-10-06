/**
 * The portal's look, from docs/design.md: always dark, one cyan accent that
 * means "act here", flat surfaces told apart by a hairline, square corners,
 * Inter for text, Archivo for headings, IBM Plex Mono for figures.
 *
 * Every colour and size a page uses comes from here or from the --ja-*
 * variables in styles/global.css; pages set none of their own.
 */
import {
  type CSSVariablesResolver,
  type MantineColorsTuple,
  createTheme,
  defaultVariantColorsResolver,
  type VariantColorsResolver,
} from '@mantine/core';

/** The association's cyan, #00dfff at index 5; lighter above, deeper below. */
const brand: MantineColorsTuple = [
  '#e0fbff',
  '#b3f5ff',
  '#80efff',
  '#66ecff',
  '#33e7ff',
  '#00dfff',
  '#00c2df',
  '#009fb8',
  '#007c90',
  '#005968',
];

/**
 * Mantine's dark scale, mapped onto our surfaces so that every component
 * that reaches for "dark" lands on one of them: 0 text, 2 muted, 4 hairline,
 * 5 field border, 6 card, 7 page, 8 field fill, 9 the top bar's black.
 */
const dark: MantineColorsTuple = [
  '#ffffff',
  '#d5dadd',
  '#aab4bb',
  '#8b8b8c',
  '#34383b',
  '#3a3d40',
  '#1f2123',
  '#161718',
  '#1b1b1c',
  '#000000',
];

const green: MantineColorsTuple = [
  '#e6f6ea',
  '#c3e9cc',
  '#9cdbab',
  '#73cd89',
  '#4dbf68',
  '#28a745',
  '#22903b',
  '#1c7832',
  '#156028',
  '#0f481e',
];
const amber: MantineColorsTuple = [
  '#fef6e7',
  '#fbe8c2',
  '#f8d996',
  '#f5c96b',
  '#f2b84b',
  '#e0a32e',
  '#bd8722',
  '#996b19',
  '#755011',
  '#51370a',
];
const red: MantineColorsTuple = [
  '#ffecec',
  '#ffd0d0',
  '#ffadad',
  '#ff8a8a',
  '#ff6b6b',
  '#f04848',
  '#dc3545',
  '#b82a38',
  '#94202c',
  '#701720',
];

/**
 * A primary button lightens on hover (docs/design.md: fading towards the dark
 * page reads like "disabled"); Mantine's default darkens it.
 */
const variantColorResolver: VariantColorsResolver = (input) => {
  const colors = defaultVariantColorsResolver(input);
  const color = input.color ?? input.theme.primaryColor;
  if (input.variant === 'filled' && color === 'brand') {
    return { ...colors, hover: 'var(--ja-accent-hover)', color: 'var(--ja-accent-text)' };
  }
  return colors;
};

export const theme = createTheme({
  primaryColor: 'brand',
  primaryShade: { light: 5, dark: 5 },
  colors: { brand, dark, green, amber, red },
  white: '#ffffff',
  black: '#081114',
  variantColorResolver,
  autoContrast: true,
  luminanceThreshold: 0.3,

  fontFamily: "'Inter Variable', Inter, system-ui, sans-serif",
  fontFamilyMonospace: "'IBM Plex Mono', ui-monospace, SFMono-Regular, monospace",
  headings: {
    fontFamily: "'Archivo Variable', Archivo, 'Inter Variable', sans-serif",
    fontWeight: '700',
    sizes: {
      h1: { fontSize: '1.75rem', lineHeight: '1.2' },
      h2: { fontSize: '1.375rem', lineHeight: '1.25' },
      h3: { fontSize: '1.125rem', lineHeight: '1.3', fontWeight: '600' },
      h4: { fontSize: '1rem', lineHeight: '1.35', fontWeight: '600' },
    },
  },
  fontSizes: { xs: '0.75rem', sm: '0.875rem', md: '1rem', lg: '1.125rem', xl: '1.25rem' },
  lineHeights: { md: '1.5' },

  // Square, everywhere (docs/design.md). One place to change it.
  defaultRadius: 0,
  radius: { xs: '0', sm: '0', md: '0', lg: '0', xl: '0' },
  shadows: { md: '0 12px 32px rgba(0, 0, 0, 0.45)', lg: '0 12px 32px rgba(0, 0, 0, 0.45)' },
  focusRing: 'auto',
  cursorType: 'pointer',

  components: {
    Button: { defaultProps: { size: 'md' } },
    Paper: { defaultProps: { withBorder: true, shadow: 'none' } },
    Card: { defaultProps: { withBorder: true, padding: 'lg' } },
    Menu: { defaultProps: { shadow: 'md' } },
    Modal: { defaultProps: { shadow: 'md' } },
  },
});

/** The page's own surfaces where Mantine's defaults differ from ours. */
export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {},
  dark: {
    '--mantine-color-body': 'var(--ja-bg)',
    '--mantine-color-text': 'var(--ja-text)',
    '--mantine-color-dimmed': 'var(--ja-muted)',
    '--mantine-color-default': 'var(--ja-surface-alt)',
    '--mantine-color-default-hover': '#2e3133',
    '--mantine-color-default-color': 'var(--ja-text)',
    '--mantine-color-default-border': 'var(--ja-border)',
    '--mantine-color-placeholder': 'var(--ja-placeholder)',
    '--mantine-color-anchor': 'var(--ja-link)',
    '--mantine-color-error': 'var(--ja-danger)',
  },
});
