import { describe, expect, it } from 'vitest';

import { forumName } from './forumName';

// Each as build_forum_username_base gives it on the server: the two must agree.
describe('the forum name shown while joining', () => {
  it('surname, initial and the year group', () => {
    expect(forumName('Anna', 'Berger', 'LAV25')).toBe('BergerA_L25');
  });

  it('without a year group, no separator', () => {
    expect(forumName('Hans', 'Huber', null)).toBe('HuberH');
  });

  it('umlauts spelled out, other accents dropped, the rest of the surname small', () => {
    expect(forumName('Björn', 'Obermüller', 'LAV24')).toBe('ObermuellerB_L24');
    expect(forumName('Émile', 'Čapek', null)).toBe('CapekE');
    expect(forumName('Lena', 'MÜLLER-Lüdenscheidt', 'MAV23')).toBe('MuellerluedenscheidtL_M23');
  });

  it('nothing until there is a surname', () => {
    expect(forumName('Anna', '', 'LAV25')).toBe('');
  });

  it('a long surname gives way, the year group stays', () => {
    const name = forumName('Anna', 'Maximilianhofstetterwallensteiner', 'LAV25');
    expect(name).toHaveLength(30);
    expect(name.endsWith('A_L25')).toBe(true);
  });
});
