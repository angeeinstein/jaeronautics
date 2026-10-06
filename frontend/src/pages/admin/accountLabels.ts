/**
 * How an account's two states read wherever they are shown: the membership
 * (by the server's one rule for it, services/account_directory.py) and the
 * account itself, which is a different question.
 */
import type { Schemas } from '../../api/client';
import type { Tone } from '../../components/Pill';

type Row = Schemas['AccountRow'];

export const MEMBERSHIP_PILL: Record<Row['membership'], { label: string; tone: Tone } | null> = {
  active: { label: 'Active', tone: 'active' },
  ending: { label: 'Ending', tone: 'pending' },
  pending: { label: 'Payment pending', tone: 'pending' },
  failed: { label: 'Payment failed', tone: 'failed' },
  ended: { label: 'Ended', tone: 'neutral' },
  none: null,
};

/** Nothing for an account in order: only what stops it being used is worth a label. */
export const ACCOUNT_STATE_PILL: Record<Row['account_state'], { label: string; tone: Tone } | null> = {
  active: null,
  no_sign_in: { label: 'No sign-in yet', tone: 'neutral' },
  disabled: { label: 'Deactivated', tone: 'failed' },
  erased: { label: 'Erased', tone: 'neutral' },
};
