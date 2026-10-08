/**
 * A team's logo where it sits on a picture (the overview's cards, the About
 * page's cover): whole, whatever its shape, with a soft glow that follows
 * its own outline -- through transparent backgrounds and gaps -- dark around
 * a light logo and light around a dark one (lib/logoTone.ts), so it does not
 * melt into the photo behind it. Fills the box it is put in.
 */
import { useLogoTone } from '../lib/logoTone';
import classes from './GlowLogo.module.css';

export function GlowLogo({ url }: { url: string }) {
  const tone = useLogoTone(url);
  // A light logo gets a dark glow; until it is measured, the dark one, which
  // suits the shaded covers most logos sit on.
  return <img className={classes.logo} src={url} alt="" data-glow={tone === 'dark' ? 'light' : 'dark'} />;
}
