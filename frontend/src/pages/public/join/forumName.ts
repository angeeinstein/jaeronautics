/**
 * The forum name a new member will get, shown while the form is filled in:
 * the same rule as the server's (build_forum_username_base in
 * aeronautics_members/services/forum.py) -- surname, first initial, and for a
 * year group "_" with its first letter and last two digits: "BergerA_L25".
 * The server makes the real one, adding a number when it is taken already.
 */

const LIMIT = 30;

const SPELLED_OUT: Record<string, string> = { ä: 'ae', ö: 'oe', ü: 'ue', Ä: 'Ae', Ö: 'Oe', Ü: 'Ue', ß: 'ss' };

/** ASCII letters and digits only, umlauts spelled out, other accents dropped. */
export function asciiName(text: string): string {
  return text
    .replace(/[äöüÄÖÜß]/g, (letter) => SPELLED_OUT[letter] ?? '')
    .normalize('NFKD')
    .replace(/[^A-Za-z0-9]/g, '');
}

export function forumName(firstName: string, lastName: string, yearGroup: string | null): string {
  const surname = asciiName(lastName);
  if (!surname) return '';
  let last = surname.charAt(0).toUpperCase() + surname.slice(1).toLowerCase();
  const initial = asciiName(firstName).charAt(0).toUpperCase();
  const group = yearGroup ?? '';
  const suffix = group ? `${group.charAt(0).toUpperCase()}${group.length > 2 ? group.slice(-2) : ''}` : '';
  const tail = suffix ? `${initial}_${suffix}` : initial;
  // The surname gives way, never the year group: it tells two Hubers apart.
  if (last.length + tail.length > LIMIT) last = last.slice(0, Math.max(LIMIT - tail.length, 1));
  return `${last}${tail}`;
}
