# Attendance with the same cards

Agreed and built 2026-10-15, on `claude/device-pairing`. The reader's side
is already in the firmware: in its *attendance* use it shows *Check in* and
*Check out*; one is pressed, then the card tapped, and it shows "Welcome Alex"
or "Goodbye Alex" with the time (`docs/device-protocol.md`, 8.2). The flight
team's members earn ECTS and must be there for a number of hours.

## Decided

- **A stay** is what is stored: who, checked in when and at which reader,
  checked out when and at which reader -- the exact seconds kept, counted in
  **full minutes**: both ends by their minute, nothing rounded up or down.
  In at 11:15:59, out at 13:40:02: 11:15 to 13:40, 2 h 25 min. "Only the
  amount of hours and when somebody worked. This is enough."
- **A stay belongs to the reader's owner** -- a team, or the association --
  not to the reader: checked in at one of the team's readers, out at another,
  is one stay. Totals are per team; the reader is only where it was tapped.
- **Who may check in:** at a team's reader, that team's active members only
  ("a Teams reader should only be able to track this team's attendance");
  at the association's, any active member. Kept as a rule in one place, so
  another one can be chosen later without changing what is stored ("If we
  need some different behavior, we could change this in the future").
- **The day runs from 03:15 to 03:15** (agreed 2026-10-16, after Codex's
  review of PR #39: at midnight a stay begun at 22:00 was closed and its
  check-out refused). Like the "day divide" of shift software: a stay belongs
  to the day it began on -- one begun after midnight to the evening before
  -- and is never split; periods, months and totals count by that day.
  Rejected: closing at midnight (late nights in the workshop happen), a
  maximum length (does not answer "who is here today"), a setting for the
  time (nobody needs another).
- **A forgotten check-out:** a stay still open at 03:15 is closed as **not
  checked out**, and counts **0 h until it is corrected**. The next check-in
  then simply starts a new stay.
- **Corrections by whoever runs attendance for the team** (below): add a stay
  forgotten altogether, set or change a check-out (or check-in), delete a
  stay. Each keeps who made it, when, and what it was before; the member sees
  "corrected by Lena" on the stay. Members do not change their own.
- **Who sees it:**
  - the member: My Account › Attendance -- their stays and totals, per team;
  - the team: a team permission of its own, *Attendance*, held by the leads
    and given to a role if wanted (whoever handles the ECTS): members ×
    hours for a chosen period (this month, a semester, any dates), a
    person's stays, who is checked in right now, a CSV export;
  - admins, as for all team data; nobody else.
- **Not built:** target hours ("we don't need the target hours"), ECTS
  worked out by the portal, a daily maximum or a minimum stay.
- **Kept until the account is erased** -- no deletion after a time: "If
  somebody wants to delete their data and erase their account, they can do
  this ... if somebody asks, we could implement something." Their stays are
  in their data export and go with an erasure. The privacy policy needs a
  sentence on it, with the drafts waiting for the board.
- **A member asking for a correction** (agreed 2026-10-15): corrections only
  by the team; on My Account a stay "not checked out" has *Ask for a
  correction*, which opens the existing *Message the leads* form (for the
  association's readers: the contact form) filled in with the stay. A
  request-and-approve step only if this becomes frequent.
- **Switched on per team, and for the association** (2026-10-15): "if only
  one team needs attendance tracking, they can switch it on, or an admin can".
  A team's leads or an admin switch the team's; admins the association's.
  Off: its readers refuse check-ins ("Attendance is switched off"), the team
  has no *Attendance* page, and its members see nothing of it.
- **In a member's account** only while it matters to them: attendance on for
  a team (or the association) they are an active member of. **Kept once there
  is data**: "even if this feature is switched off or they are not a member
  anymore ... this feature stays in their account with their data in it.
  Maybe moved to a different place somewhere so that it isn't too prominent
  anymore" -- then not in the side menu, but on *Privacy and data*, as a
  link to the same page.

## As built

- **Stored:** `attendance_stays` (`db_models.AttendanceStay`): who, the owner
  (`team_id`, none for the association), in and out with the reader, when a
  stay was closed as not checked out, who corrected it last and when. The
  switch: `teams.attendance_enabled`; the association's, the setting
  `attendance_enabled`. Rules and counting: `services/attendance.py`
  (`may_check_in` is the one place that says who may check in).
- **The reader:** device type *Attendance*, reports `check_in` and
  `check_out` (`docs/device-protocol.md`, 8.2). A reader belongs to a team, or
  to nobody -- the association.
- **The night:** `reconcile-billing` at 03:15 closes the stays begun before
  it; every page that shows stays, and each check-in and check-out, does the
  same first, so nothing waits for the night. The time is
  `attendance.DAY_DIVIDE`; the front end's `attendanceDay` (`lib/format.ts`)
  says the same.
- **Pages:**
  - My Account › Attendance (`/account/attendance`): a panel per team, a
    month at a time, the month's total and the total altogether; *Ask for a
    correction* on a stay not checked out -- *Message the leads* filled in, or
    the contact form (`/contact?topic=&subject=&message=`) for the
    association's readers. In the side menu while attendance is on for a team
    (or the association) they are an active member of; otherwise, while they
    have stays, a panel on *Privacy and data* leads there.
  - A team's Attendance (`/teams/<slug>/manage/attendance`), for the team
    permission *Attendance* (`team.attendance`; the leads hold it): who is
    here now, hours per person for a period (this month, last month, this
    semester -- October to February, March to September -- or any days), a
    person's stays with *Change*, *Delete* and *Add a stay*, a CSV of the
    period, the switch. In the side menu under People while on, under
    Settings while off.
  - Admin › Attendance (`/admin/attendance`, for whoever runs the teams):
    the same for the association's readers, always in the admin menu, with
    its switch on the page -- as a team's is (changed 2026-10-16: "more
    consistent if we have this sidebar menu for attendance always shown").
- **Corrections** are written to the log (`attendance_stay_added`,
  `_changed`, `_deleted`, `attendance_switched`), with the stay before and
  after.
- **The data export** has a person's stays; an erasure deletes them.
