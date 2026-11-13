#!/usr/bin/env python3
"""Turn a generated icon illustration into a valid iOS app icon.

Image models reliably draw an app icon *inside* a white frame with rounded
corners already applied. iOS does the rounding itself and App Store Connect
rejects an icon that has an alpha channel, so that frame has to go and the
background has to reach all four corners.

    python backend/scripts/flatten_icon.py candidate.jpg icon_1024.png

What it does:
  1. finds the light frame connected to the image border and removes it,
  2. flattens every near-background tone to one flat colour,
  3. writes a 1024x1024 sRGB PNG with no alpha,
  4. refuses to write anything that would still be rejected.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ICON_SIZE = 1024
LIGHT_THRESHOLD = 170       # a pixel this bright on every channel is "frame"
FRAME_GROW_PX = 4           # swallow the antialiased edge between frame and art
BACKGROUND_TOLERANCE = 60   # summed RGB distance still counted as background


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised RGB->HSV with hue in degrees. Avoids a matplotlib dependency."""
    high = rgb.max(-1)
    low = rgb.min(-1)
    span = high - low
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    hue = np.zeros_like(high)
    nonzero = span > 1e-6
    for channel, offset in ((red, 0.0), (green, 2.0), (blue, 4.0)):
        pick = nonzero & (high == channel)
        if offset == 0.0:
            hue[pick] = ((green - blue)[pick] / span[pick]) % 6
        elif offset == 2.0:
            hue[pick] = ((blue - red)[pick] / span[pick]) + 2
        else:
            hue[pick] = ((red - green)[pick] / span[pick]) + 4
    saturation = np.where(high > 0, span / np.maximum(high, 1e-6), 0)
    return hue * 60, saturation, high


def strip_frame(image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Replace the border-connected light frame with the background colour.

    Also returns the background colour it measured, so the caller does not have
    to guess it a second time.
    """
    light = image.min(axis=2) > LIGHT_THRESHOLD
    labels, count = ndimage.label(light)
    empty = np.zeros(image.shape[:2], bool)
    if not count:
        return image, empty, None
    touching = set(labels[0, :]) | set(labels[-1, :]) \
        | set(labels[:, 0]) | set(labels[:, -1])
    touching.discard(0)
    if not touching:
        return image, empty, None
    frame = np.isin(labels, list(touching))
    frame = ndimage.binary_dilation(frame, iterations=FRAME_GROW_PX)
    # The background colour is whatever sits just inside the frame.
    ring = ndimage.binary_dilation(frame, iterations=10) & ~frame
    if not ring.any():
        return image, frame, None
    background = np.median(image[ring].reshape(-1, 3), axis=0).astype(np.uint8)
    out = image.copy()
    out[frame] = background
    return out, frame, background


def flatten_background(image: np.ndarray, reference: np.ndarray,
                       colour: np.ndarray | None = None) -> np.ndarray:
    """Collapse the field around the artwork into one flat colour.

    Generated art leaves subtle banding where the frame used to be. Only
    regions that both resemble the measured background *and* touch the image
    border are repainted, so the subject is never swallowed.
    """
    distance = np.abs(image.astype(np.int16) - reference.astype(np.int16)).sum(axis=2)
    similar = distance < BACKGROUND_TOLERANCE
    labels, count = ndimage.label(similar)
    if not count:
        return image
    touching = set(labels[0, :]) | set(labels[-1, :]) \
        | set(labels[:, 0]) | set(labels[:, -1])
    touching.discard(0)
    if not touching:
        return image
    region = ndimage.binary_closing(np.isin(labels, list(touching)),
                                    np.ones((5, 5)))
    if region.mean() > 0.95:
        raise SystemExit("background detection swallowed the whole image; "
                         "lower --tolerance")
    out = image.copy()
    out[region] = reference if colour is None else colour
    return out


def verify(path: Path) -> None:
    """Fail on exactly what App Store Connect fails on."""
    with Image.open(path) as opened:
        if "A" in opened.mode:
            raise SystemExit(f"{path}: icon still has an alpha channel")
        if opened.size != (ICON_SIZE, ICON_SIZE):
            raise SystemExit(f"{path}: expected {ICON_SIZE}x{ICON_SIZE}, got {opened.size}")
        pixels = np.array(opened.convert("RGB"))
    corners = [tuple(pixels[y, x]) for y, x in
               ((0, 0), (0, ICON_SIZE - 1), (ICON_SIZE - 1, 0),
                (ICON_SIZE - 1, ICON_SIZE - 1))]
    if len(set(corners)) != 1:
        raise SystemExit(
            f"{path}: corners differ ({corners}) — the icon is still rounded "
            "or framed")


def parse_hex(value: str) -> np.ndarray:
    value = value.lstrip("#")
    if len(value) != 6:
        raise argparse.ArgumentTypeError("expected a colour like #0E1A2F")
    return np.array([int(value[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clean a generated illustration into an iOS app icon.")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--background", type=parse_hex, default=None,
                        help="force the background colour, e.g. #0E1A2F")
    parser.add_argument("--tolerance", type=int, default=BACKGROUND_TOLERANCE,
                        help="summed RGB distance still counted as background")
    args = parser.parse_args(argv)

    globals()["BACKGROUND_TOLERANCE"] = args.tolerance

    with Image.open(args.source) as opened:
        image = np.array(opened.convert("RGB"))

    image, frame, measured = strip_frame(image)
    print(f"frame removed: {frame.mean() * 100:.1f}% of the image")
    reference = measured if measured is not None else image[0, 0]
    image = flatten_background(image, reference, args.background)

    result = Image.fromarray(image).convert("RGB")
    if result.size != (ICON_SIZE, ICON_SIZE):
        result = result.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(args.destination, format="PNG", optimize=True)

    verify(args.destination)
    print(f"wrote {args.destination} — {ICON_SIZE}x{ICON_SIZE}, no alpha, "
          "flat corners")
    return 0


if __name__ == "__main__":
    sys.exit(main())
