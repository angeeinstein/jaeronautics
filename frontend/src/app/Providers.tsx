/**
 * Everything every page relies on: the theme (forced dark), loaded data and
 * its cache, and the short messages at the bottom ("Saved").
 *
 * Mantine writes its colour variables into <style> tags; the security policy
 * allows only those carrying the page's nonce, which Flask puts into
 * <meta name="csp-nonce"> (blueprints/app_shell.py).
 */
import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { type ReactNode, useState } from 'react';

import { ApiError } from '../api/client';
import { cssVariablesResolver, theme } from '../theme';

export function cspNonce(): string | undefined {
  const value = document.querySelector('meta[name="csp-nonce"]')?.getAttribute('content');
  return value && !value.startsWith('__') ? value : undefined;
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Asking again does not help with "not allowed" or "not found".
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failures < 2,
        refetchOnWindowFocus: true,
      },
    },
  });
}

interface ProvidersProps {
  children: ReactNode;
  client?: QueryClient;
  /** 'test': no transitions or portals, for the component tests. */
  env?: 'default' | 'test';
}

export function Providers({ children, client, env = 'default' }: ProvidersProps) {
  const [queryClient] = useState(() => client ?? makeQueryClient());
  return (
    <MantineProvider
      env={env}
      theme={theme}
      forceColorScheme="dark"
      cssVariablesResolver={cssVariablesResolver}
      getStyleNonce={() => cspNonce() ?? ''}
    >
      <QueryClientProvider client={queryClient}>
        <Notifications position="bottom-center" limit={3} />
        {children}
      </QueryClientProvider>
    </MantineProvider>
  );
}
