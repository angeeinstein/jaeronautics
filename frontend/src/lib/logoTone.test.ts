import { describe, expect, it } from 'vitest';

import { toneOf } from './logoTone';

const pixel = (red: number, green: number, blue: number, alpha = 255) => [red, green, blue, alpha];

describe('the tone of a logo', () => {
  it('light or dark by what can be seen', () => {
    expect(toneOf(new Uint8ClampedArray([...pixel(250, 250, 250), ...pixel(240, 240, 240)]))).toBe('light');
    expect(toneOf(new Uint8ClampedArray([...pixel(10, 10, 30), ...pixel(40, 40, 40)]))).toBe('dark');
  });

  it('a transparent background and gaps count for nothing', () => {
    const blackOnNothing = [...pixel(0, 0, 0), ...pixel(255, 255, 255, 0), ...pixel(255, 255, 255, 0)];
    expect(toneOf(new Uint8ClampedArray(blackOnNothing))).toBe('dark');
  });

  it('nothing to see, nothing to say', () => {
    expect(toneOf(new Uint8ClampedArray(pixel(255, 255, 255, 0)))).toBeNull();
  });
});
