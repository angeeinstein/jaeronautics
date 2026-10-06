/**
 * A team's logo, small, beside its name in a list. Logos are often wide, so
 * each is shown whole in a box; a team without one gets its initial in the
 * same box, so the names line up.
 */
import classes from './TeamMark.module.css';

export function TeamMark({ name, logoUrl }: { name: string; logoUrl: string | null }) {
  return (
    <span className={classes.mark} aria-hidden>
      {logoUrl ? <img src={logoUrl} alt="" /> : name.charAt(0)}
    </span>
  );
}
