/**
 * The square crop of a profile picture, as the server makes it
 * (forum_service.py, _apply_avatar_crop): a square of the shorter side divided
 * by the zoom (1 to 4), around a centre given as fractions of the width and
 * height, pushed back inside the picture. The page draws the same square, so
 * what is seen is what is sent.
 */
export interface Crop {
  zoom: number;
  x: number;
  y: number;
}

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 4;

export const START: Crop = { zoom: 1, x: 0.5, y: 0.5 };

function clamp(value: number, low: number, high: number) {
  return Math.min(Math.max(value, low), high);
}

/** The square, in the picture's pixels. */
export function square(width: number, height: number, crop: Crop) {
  const side = Math.max(1, Math.floor(Math.min(width, height) / crop.zoom));
  const left = clamp(Math.round(crop.x * width) - Math.floor(side / 2), 0, width - side);
  const top = clamp(Math.round(crop.y * height) - Math.floor(side / 2), 0, height - side);
  return { left, top, side };
}

/** The centre kept where the square still fits inside the picture. */
export function settle(width: number, height: number, crop: Crop): Crop {
  const zoom = clamp(crop.zoom, MIN_ZOOM, MAX_ZOOM);
  const side = Math.min(width, height) / zoom;
  const halfX = side / (2 * width);
  const halfY = side / (2 * height);
  return { zoom, x: clamp(crop.x, halfX, 1 - halfX), y: clamp(crop.y, halfY, 1 - halfY) };
}

/** Dragging the picture by (dx, dy) screen pixels in a preview ``size`` pixels wide. */
export function drag(width: number, height: number, crop: Crop, dx: number, dy: number, size: number): Crop {
  const side = Math.min(width, height) / crop.zoom;
  return settle(width, height, {
    ...crop,
    x: crop.x - (dx / size) * (side / width),
    y: crop.y - (dy / size) * (side / height),
  });
}
