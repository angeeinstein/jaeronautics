/**
 * A team's logo, small, beside its name in a list or menu. Logos come round,
 * shield-shaped or square, so each is shown whole, as it is, in a square
 * space; a team without one gets its initial on a tile of the same size, so
 * the names line up.
 */
import classes from './TeamMark.module.css';

export function TeamMark({ name, logoUrl }: { name: string; logoUrl: string | null }) {
  return (
    <span className={classes.mark} data-image={logoUrl ? true : undefined} aria-hidden>
      {logoUrl ? <img src={logoUrl} alt="" /> : name.charAt(0)}
    </span>
  );
}
