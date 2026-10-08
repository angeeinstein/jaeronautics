/**
 * How the join form speaks to each kind of member. What each is asked -- the
 * year group, the university address, the company -- is the server's
 * (GET /api/v1/forms/options); this is only the wording around it, and a
 * kind added on the server without wording here gets the general one.
 */
import {
  IconAward,
  IconBuilding,
  IconPresentation,
  IconSchool,
  IconUser,
  type Icon,
} from '@tabler/icons-react';

export interface KindWording {
  icon: Icon;
  /** One line on the card in the first step. */
  line: string | null;
  /** What the second step says it is about. */
  about: string;
  /** The heading over what this kind is asked besides the name. */
  block: string;
  workEmail: string;
  /** Where the work phone is asked, its label; null: not asked. */
  workPhone: string | null;
  /** The university address only behind "it still works": an alumnus's often does not. */
  workEmailOnRequest: boolean;
  /** Under the private address. */
  privateEmail: string;
}

const GENERAL: KindWording = {
  icon: IconUser,
  line: null,
  about: 'Your name, and how to reach you at work.',
  block: 'More about you',
  workEmail: 'University or company email',
  workPhone: 'Work phone',
  workEmailOnRequest: false,
  privateEmail: 'Your login. Your own address.',
};

const WORDING: Record<string, Partial<KindWording>> = {
  student: {
    icon: IconSchool,
    line: 'Studying at FH Joanneum now.',
    about: 'Your name and your studies.',
    block: 'Your studies',
    workEmail: 'University email',
    workPhone: null,
    privateEmail: 'Your login. Not your university address: this one stays when you graduate.',
  },
  alumni: {
    icon: IconAward,
    line: 'Studied here.',
    about: 'Your name, and your year group if you remember it.',
    block: 'Your time here',
    workEmail: 'University email',
    workPhone: null,
    workEmailOnRequest: true,
    privateEmail: 'Your login. Not your university address: this one keeps working.',
  },
  staff: {
    icon: IconPresentation,
    line: 'Working at the institute.',
    about: 'Your name, and how to reach you at the institute.',
    block: 'At the institute',
    workEmail: 'Institute email',
    workPhone: 'Office phone',
    privateEmail: 'Your login. Your own address, not the institute’s.',
  },
  partner: {
    icon: IconBuilding,
    line: 'Joining for a company.',
    about: 'Your name, and the company you join for.',
    block: 'Your company',
    workEmail: 'Company email',
    workPhone: 'Company phone',
  },
};

export function wordingFor(kind: string): KindWording {
  return { ...GENERAL, ...WORDING[kind] };
}
