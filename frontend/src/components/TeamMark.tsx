/**
 * A team's logo, small, beside its name in a list or menu. Logos come round,
 * shield-shaped or square, so each is shown whole in a square box; a team
 * without one gets its initial in the same box, so the names line up.
 */
import classes from './TeamMark.module.css';

export function TeamMark({ name, logoUrl }: { name: string; logoUrl: string | null }) {
  return (
    <span className={classes.mark} aria-hidden>
      {logoUrl ? <img src={logoUrl} alt="" /> : name.charAt(0)}
    </span>
  );
}
