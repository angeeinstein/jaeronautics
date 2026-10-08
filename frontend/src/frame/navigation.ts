/**
 * What a sidebar holds (docs/frontend-structure.md, sections 4-5): a link
 * back out of the area, then groups of entries, each entry with an icon, an
 * optional count and optional children that fold open. Built per person:
 * an entry somebody may not use is left out, and so is a group left empty.
 */
import type { ComponentType } from 'react';

export interface NavItem {
  label: string;
  to: string;
  icon?: ComponentType<{ size?: number; stroke?: number }>;
  /** Shown beside the label when above zero: things waiting for this person. */
  count?: number;
  /** Current only on exactly this address, not on the addresses below it. */
  exact?: boolean;
  children?: NavItem[];
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export interface SidebarContent {
  /** The way out to where the area sits; none for an area at the top (My Account). */
  back?: { label: string; to: string };
  /** What the area is about, under the way back: a team's mark and name. */
  header?: { label: string; logoUrl: string | null };
  groups: NavGroup[];
}

/** Whether ``item`` is the page at ``pathname`` (``hash`` for entries that point into a page). */
export function isCurrent(item: NavItem, pathname: string, hash = ''): boolean {
  const [path = item.to, anchor] = item.to.split('#');
  if (anchor !== undefined) return pathname === path && hash === `#${anchor}`;
  if (item.exact) return pathname === path;
  return pathname === path || pathname.startsWith(`${path}/`);
}

/** Whether ``item`` or one of its children is the current page. */
export function containsCurrent(item: NavItem, pathname: string, hash = ''): boolean {
  if (isCurrent(item, pathname, hash)) return true;
  return (item.children ?? []).some((child) => containsCurrent(child, pathname, hash));
}

/** Groups without the entries that are ``null`` (not for this person), and without empty groups. */
export function compact(groups: { label: string; items: (NavItem | null)[] }[]): NavGroup[] {
  return groups
    .map((group) => ({
      label: group.label,
      items: group.items.filter((item): item is NavItem => item !== null),
    }))
    .filter((group) => group.items.length > 0);
}
