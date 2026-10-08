/**
 * A link that works for both kinds of page: within the app it changes the
 * page without loading it again; to an address still drawn by Flask (or
 * another site) it is a plain link. Use it for every internal link.
 */
import type { AnchorHTMLAttributes, Ref } from 'react';
import { Link } from 'react-router';

import { isAppPath } from './paths';

export interface AppLinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string;
  ref?: Ref<HTMLAnchorElement>;
}

export function AppLink({ to, ref, ...rest }: AppLinkProps) {
  if (isAppPath(to)) return <Link to={to} ref={ref} {...rest} />;
  return <a href={to} ref={ref} {...rest} />;
}
