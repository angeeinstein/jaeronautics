/**
 * Going to an address from code, the way AppLink goes there from a link:
 * within the app without loading the page again, anywhere else by loading it.
 */
import { useCallback } from 'react';
import { useNavigate } from 'react-router';

import { isAppPath } from './paths';

export function useGo() {
  const navigate = useNavigate();
  return useCallback(
    (to: string, { newTab = false }: { newTab?: boolean } = {}) => {
      if (newTab) window.open(to, '_blank', 'noopener');
      else if (isAppPath(to)) void navigate(to);
      else window.location.assign(to);
    },
    [navigate],
  );
}
