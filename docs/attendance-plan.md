# Attendance with the same cards

Agreed 2026-10-15, to be built on `claude/device-pairing`. The reader's side
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
- **A forgotten check-out:** every night at 03:15 a stay still open from the
  day before is closed as **not checked out**, and counts **0 h until it is
  corrected**. The next check-in then simply starts a new stay.
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
