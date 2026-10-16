/**
 * A team's Attendance (docs/attendance-plan.md), for whoever holds *Attendance*
 * in it -- the leads, and a role it is given to: who is checked in now, hours
 * per person for a period, a person's stays and their corrections, a CSV, and
 * the switch. In the side menu under People while it is on, under Settings
 * while it is off. Data: /api/v1/teams/<slug>/manage/attendance
 * (aeronautics_members/api/attendance.py).
 */
import { api, call } from '../../../api/client';
import { type AttendanceApi, AttendanceBoard } from '../../../components/attendance/AttendanceBoard';
import { ManageHeader, useManage, useSlug } from './shared';

function teamApi(slug: string): AttendanceApi {
  const base = '/api/v1/teams/{slug}/manage/attendance';
  const path = { slug };
  return {
    key: ['teams', slug, 'attendance'],
    overview: (start, end) => call(api.GET(base, { params: { path, query: { start, end } } })),
    person: (userId, start, end) =>
      call(
        api.GET(`${base}/people/{user_id}`, {
          params: { path: { ...path, user_id: userId }, query: { start, end } },
        }),
      ),
    add: (body) => call(api.POST(`${base}/stays`, { params: { path }, body })),
    change: (stayId, body) =>
      call(api.PUT(`${base}/stays/{stay_id}`, { params: { path: { ...path, stay_id: stayId } }, body })),
    remove: (stayId) =>
      call(api.DELETE(`${base}/stays/{stay_id}`, { params: { path: { ...path, stay_id: stayId } } })),
    setEnabled: (enabled) => call(api.PUT(`${base}/switch`, { params: { path }, body: { enabled } })),
    csvHref: (start, end) =>
      `/api/v1/teams/${encodeURIComponent(slug)}/manage/attendance.csv?${new URLSearchParams({ start, end }).toString()}`,
  };
}

export function Attendance() {
  const slug = useSlug();
  const manage = useManage().data;
  return (
    <>
      <ManageHeader title="Attendance" description="Time at the team's readers, in full minutes." />
      <AttendanceBoard api={teamApi(slug)} where={manage?.name ?? 'the team'} />
    </>
  );
}
