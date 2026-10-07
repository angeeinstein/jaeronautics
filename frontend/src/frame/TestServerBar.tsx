/**
 * On the test server, a red bar above the top bar of every page, so nobody
 * takes it for the real portal. Nothing on the live site.
 */
import { onTestServer } from '../lib/testServer';
import classes from './Frame.module.css';

export function TestServerBar() {
  if (!onTestServer()) return null;
  return (
    <div className={classes.testServerBar} role="note">
      Test server <span>· not the real membership portal</span>
    </div>
  );
}
