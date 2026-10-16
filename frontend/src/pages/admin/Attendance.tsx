/**
 * Admin › Attendance (docs/attendance-plan.md): the association's attendance
 * readers -- any active member checks in there. Who is checked in now, hours
 * per person for a period, a person's stays and their corrections, a CSV, and
 * the switch. Always in the admin menu. A team's attendance is on its own
 * pages. Data: /api/v1/admin/attendance (aeronautics_members/api/attendance.py).
 */
import { api, call } from '../../api/client';
import { type AttendanceApi, AttendanceBoard } from '../../components/attendance/AttendanceBoard';
import { PageHeader } from '../../components/PageHeader';

export const associationAttendance: AttendanceApi = {
  key: ['admin', 'attendance'],
  overview: (start, end) => call(api.GET('/api/v1/admin/attendance', { params: { query: { start, end } } })),
  person: (userId, start, end) =>
    call(
      api.GET('/api/v1/admin/attendance/people/{user_id}', {
        params: { path: { user_id: userId }, query: { start, end } },
      }),
    ),
  add: (body) => call(api.POST('/api/v1/admin/attendance/stays', { body })),
  change: (stayId, body) =>
    call(
      api.PUT('/api/v1/admin/attendance/stays/{stay_id}', { params: { path: { stay_id: stayId } }, body }),
    ),
  remove: (stayId) =>
    call(api.DELETE('/api/v1/admin/attendance/stays/{stay_id}', { params: { path: { stay_id: stayId } } })),
  setEnabled: (enabled) => call(api.PUT('/api/v1/admin/attendance/switch', { body: { enabled } })),
  csvHref: (start, end) => `/api/v1/admin/attendance.csv?${new URLSearchParams({ start, end }).toString()}`,
};

export function Attendance() {
  return (
    <>
      <PageHeader
        title="Attendance"
        description="Time at the association's readers, in full minutes."
        crumbs={[{ label: 'Admin', to: '/admin' }, { label: 'Attendance' }]}
      />
      <AttendanceBoard api={associationAttendance} where="the association" />
    </>
  );
}
