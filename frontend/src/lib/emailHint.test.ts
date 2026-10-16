import { describe, expect, it } from 'vitest';

import { likelyMeant } from './emailHint';

const DOMAINS = ['edu.fh-joanneum.at', 'fh-joanneum.at'];

describe('a university address that looks mistyped', () => {
  it.each([
    ['anna.muster@edu-fh.joanneum.at', 'anna.muster@edu.fh-joanneum.at'],
    ['anna.muster@edu.fh.joanneum.at', 'anna.muster@edu.fh-joanneum.at'],
    ['anna.muster@edufh-joanneum.at', 'anna.muster@edu.fh-joanneum.at'],
    ['anna.muster@edu.fh-joaneum.at', 'anna.muster@edu.fh-joanneum.at'],
    ['Anna.Muster@EDU-FH.JOANNEUM.AT', 'Anna.Muster@edu.fh-joanneum.at'],
    ['lena@fh-joaneum.at', 'lena@fh-joanneum.at'],
  ])('%s: did you mean %s?', (typed, meant) => {
    expect(likelyMeant(typed, DOMAINS)).toBe(meant);
  });

  it.each([
    'anna.muster@edu.fh-joanneum.at',
    'lena@fh-joanneum.at',
    'anna@gmail.com',
    'anna@tugraz.at',
    'anna@',
    'anna',
    '',
  ])('%s: nothing', (typed) => {
    expect(likelyMeant(typed, DOMAINS)).toBeNull();
  });
});
