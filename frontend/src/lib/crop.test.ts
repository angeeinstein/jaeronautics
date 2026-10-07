import { describe, expect, it } from 'vitest';

import { drag, settle, square, START } from './crop';

describe('the square crop', () => {
  it('is the shorter side, centred, at first', () => {
    expect(square(400, 300, START)).toEqual({ left: 50, top: 0, side: 300 });
  });

  it('zoomed in, a smaller square around the centre', () => {
    expect(square(400, 300, { zoom: 2, x: 0.5, y: 0.5 })).toEqual({ left: 125, top: 75, side: 150 });
  });

  it('stays inside the picture', () => {
    expect(square(400, 300, { zoom: 2, x: 0, y: 1 })).toEqual({ left: 0, top: 150, side: 150 });
  });

  it('the centre cannot wander off', () => {
    expect(settle(400, 300, { zoom: 1, x: 0, y: 0.5 })).toEqual({ zoom: 1, x: 0.375, y: 0.5 });
    expect(settle(400, 300, { zoom: 9, x: 0.5, y: 0.5 }).zoom).toBe(4);
  });

  it('dragging right moves the square left', () => {
    const moved = drag(400, 300, { zoom: 2, x: 0.5, y: 0.5 }, 30, 0, 300);
    expect(moved.x).toBeCloseTo(0.5 - (30 / 300) * (150 / 400));
    expect(moved.y).toBe(0.5);
  });
});
