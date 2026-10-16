/**
 * A university address that looks mistyped (docs/todo.md, 2026-10-15): the
 * domain typed, with its dots and hyphens left out, is one of the portal's own
 * (`edu-fh.joanneum.at`, `edu.fh.joanneum.at` for `edu.fh-joanneum.at`), or
 * it is one letter off one (`edu.fh-joaneum.at`). Then the address as it was
 * likely meant; otherwise nothing -- `gmail.com`, another university, and the
 * portal's own domains spelled right (staff's `fh-joanneum.at` is not taken
 * for a mistyped student address).
 *
 * The domains are the portal's (Settings › General, GET /api/v1/forms/options).
 */

const squashed = (domain: string) => domain.replace(/[.-]/g, '');

/** Whether ``a`` becomes ``b`` by one letter added, left out or changed. */
function oneLetterOff(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1 || a === b) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  if (a.length === b.length) return a.slice(i + 1) === b.slice(i + 1);
  return a.length > b.length ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1);
}

/** The address as it was likely meant, or null. */
export function likelyMeant(address: string, domains: readonly string[]): string | null {
  const typed = address.trim();
  const at = typed.lastIndexOf('@');
  if (at < 1) return null;
  const local = typed.slice(0, at);
  const domain = typed.slice(at + 1).toLowerCase();
  if (!domain.includes('.')) return null;
  const known = domains.map((item) => item.toLowerCase());
  if (known.includes(domain)) return null;
  const meant =
    known.find((item) => squashed(item) === squashed(domain)) ??
    known.find((item) => oneLetterOff(item, domain));
  return meant ? `${local}@${meant}` : null;
}
