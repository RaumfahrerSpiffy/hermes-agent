"""Frame sanity: never return a blank capture as if it were real.

The spectacle blank-capture fooled a whole morning once: a dimensionally
correct PNG, entirely one colour, reported as a successful capture. A human
assistant would say "the screen is blank." These statistics say the same
thing locally, with no model call.

Thresholds MEASURED 2026-08-21 against real captures (spike 003b):
blank lock-screen frame = 1 colour / 0.0 stddev / 100% dominant / ~4 kB per
megapixel; real desktop frames = 22k+ colours. The margins are wide.
"""
import os

import numpy as np
from PIL import Image

MAX_UNIQUE_COLOURS = 50      # below this the frame is near-monochrome
MIN_LUM_STD = 8.0            # below this luminance is flat
MAX_DOMINANT_FRAC = 0.92     # above this one colour owns the frame
MIN_BYTES_PER_MP = 20000     # below this the PNG compressed to nothing


def frame_stats(path):
    """Cheap local statistics for a capture. No model call."""
    sz = os.path.getsize(path)
    im = Image.open(path).convert("RGB")
    a = np.asarray(im)
    h, w, _ = a.shape
    mp = (w * h) / 1e6

    small = a[::4, ::4]
    flat = small.reshape(-1, 3)
    lum = (0.2126 * small[:, :, 0] + 0.7152 * small[:, :, 1]
           + 0.0722 * small[:, :, 2])
    colours, counts = np.unique(flat, axis=0, return_counts=True)

    return {
        "path": path,
        "width": w,
        "height": h,
        "bytes": sz,
        "bytes_per_mp": int(sz / mp) if mp else 0,
        "unique_colours": len(colours),
        "lum_std": float(lum.std()),
        "dominant_frac": float(counts.max() / counts.sum()),
    }


def is_degenerate(stats):
    """(is_degenerate, reasons). Reasons are human-readable, never silent."""
    reasons = []
    if stats["unique_colours"] < MAX_UNIQUE_COLOURS:
        reasons.append(f"only {stats['unique_colours']} unique colours")
    if stats["lum_std"] < MIN_LUM_STD:
        reasons.append(f"luminance stddev {stats['lum_std']:.1f} (flat)")
    if stats["dominant_frac"] > MAX_DOMINANT_FRAC:
        reasons.append(f"{stats['dominant_frac'] * 100:.0f}% one colour")
    if stats["bytes_per_mp"] < MIN_BYTES_PER_MP:
        reasons.append(f"{stats['bytes_per_mp']} bytes/MP (too compressible)")
    return (bool(reasons), reasons)
