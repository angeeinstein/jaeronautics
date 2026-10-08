/**
 * The old address of one team's admin page. A team is run from its own pages
 * now -- an admin sees its details, fee and archiving in the team's side menu
 * (pages/teams/manage/Admin.tsx) -- so a bookmark or link to it goes there.
 */
import { Navigate, useParams } from 'react-router';

export function Team() {
  const { slug = '' } = useParams();
  return <Navigate to={`/teams/${slug}/manage/details`} replace />;
}
