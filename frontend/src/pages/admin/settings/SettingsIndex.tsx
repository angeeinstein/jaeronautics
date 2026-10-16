/**
 * /admin/settings: the first section -- or, for a link to a tab of the page
 * the settings used to be (``#settings-forum``, ``#backup-restore``), the page
 * that part has now.
 */
import { Navigate, useLocation } from 'react-router';

const MOVED: Record<string, string> = {
  '#settings-general': 'general',
  '#settings-notifications': 'email#sending',
  '#settings-billing': 'billing',
  '#settings-forum': 'forum',
  '#settings-mail': 'email#accounts',
  '#settings-test': 'email#test',
  '#settings-maintenance': 'health',
  '#backup-restore': 'backup',
};

export function SettingsIndex() {
  const { hash } = useLocation();
  return <Navigate to={`/admin/settings/${MOVED[hash] ?? 'general'}`} replace />;
}
