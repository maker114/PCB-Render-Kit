"""Pre-render audit of the DXF/STEP-derived artwork textures.

The job of this script is to catch the class of bug where a *layer texture is
structurally wrong* even though the render looks plausible: e.g. a ground pour
whose clearances were filled in, so the copper layer becomes a solid rectangle
and every trace disappears.

It prints, per texture: pixel size, coverage, connected-component count and a
few sanity verdicts, and writes a contact sheet next to it.

Usage:
    python audit_assets.py [--tex DIR] [--out DIR]
Exit code 1 if any texture is flagged as suspicious.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

# coverage thresholds (fraction of the board area covered by the artwork)
HUGE = 0.90      # almost the whole board -> probably an un-punched fill
TINY = 0.004     # almost nothing -> probably wrong layer / wrong frame

EXPECT = {
    "copper_top": (0.25, 0.995, "top copper (pour + traces)"),
    "copper_bottom": (0.25, 0.995, "bottom copper (pour + traces)"),
    "mask_top": (0.01, 0.60, "solder-mask openings"),
    "mask_bottom": (0.01, 0.60, "solder-mask openings"),
    "silk_top": (0.005, 0.40, "silkscreen"),
    "silk_bottom": (0.000, 0.40, "silkscreen"),
    "drill": (0.001, 0.30, "drill holes"),
}


def coverage(path):
    im = Image.open(path).convert("RGBA")
    a = np.asarray(im)
    mask = a[..., 3] > 8
    cov = float(mask.mean())
    return im, mask, cov


def components(mask, max_side=1200):
    """Cheap 4-connected component count on a downscaled mask."""
    im = Image.fromarray((mask * 255).astype(np.uint8))
    w, h = im.size
    scale = max(1, int(max(w, h) / max_side))
    small = np.asarray(im.resize((max(1, w // scale), max(1, h // scale)), Image.NEAREST)) > 128
    try:
        from scipy import ndimage  # optional
        _, n = ndimage.label(small)
        return int(n)
    except Exception:
        # fallback: count transitions (rough proxy, no scipy installed)
        return int(np.count_nonzero(small[1:] != small[:-1]) + np.count_nonzero(small[:, 1:] != small[:, :-1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tex", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--out", default=None, help="contact sheet output dir (default: <tex>/../out/diag)")
    args = ap.parse_args()

    tex = os.path.abspath(args.tex)
    align_path = os.path.join(tex, "align.json")
    board = None
    if os.path.exists(align_path):
        with open(align_path, encoding="utf-8") as fh:
            b = json.load(fh)["board_bbox_mm"]
        board = f"{(b[2]-b[0]):.2f} x {(b[3]-b[1]):.2f} mm"

    outdir = args.out or os.path.join(os.path.dirname(tex), "out", "diag")
    os.makedirs(outdir, exist_ok=True)

    print(f"[audit] textures in {tex}" + (f"  (board {board})" if board else ""))
    print(f"{'texture':20s} {'pixels':>12s} {'coverage':>9s} {'regions':>8s}  verdict")
    flagged = []
    thumbs = []
    for name in sorted(EXPECT):
        path = os.path.join(tex, name + ".png")
        if not os.path.exists(path):
            print(f"{name:20s} {'-':>12s} {'-':>9s} {'-':>8s}  MISSING")
            continue
        im, mask, cov = coverage(path)
        n = components(mask)
        lo, hi, label = EXPECT[name]
        notes = []
        if cov > HUGE:
            notes.append(f"SOLID-FILL? cover={cov:.1%}")
        if cov < lo:
            notes.append("near-empty / wrong layer?")
        if cov > hi:
            notes.append(f"above expected max {hi:.0%}")
        # a real copper layer with a pour has many regions (traces + islands);
        # a solid fill has exactly 1
        if name.startswith("copper") and n <= 2 and cov > 0.5:
            notes.append("single blob -> clearances probably not punched")
        verdict = "OK" if not notes else "; ".join(notes)
        if notes:
            flagged.append(name)
        print(f"{name:20s} {im.size[0]:>5d}x{im.size[1]:<6d} {cov:>8.1%} {n:>8d}  {verdict}")

        thumb = Image.new("RGBA", im.size, (16, 78, 143, 255))
        thumb.alpha_composite(im)
        thumb = thumb.convert("RGB")
        thumb.thumbnail((300, 460))
        thumbs.append((name, thumb, cov))

    # contact sheet
    if thumbs:
        cols = 4
        rows = (len(thumbs) + cols - 1) // cols
        cw, ch = 320, 500
        sheet = Image.new("RGB", (cols * cw, rows * ch), (18, 18, 20))
        for i, (name, thumb, cov) in enumerate(thumbs):
            x, y = (i % cols) * cw + 10, (i // cols) * ch + 10
            sheet.paste(thumb, (x, y))
            print(f"[audit] sheet cell {i}: {name} {cov:.1%}")
        path = os.path.join(outdir, "audit_contact_sheet.png")
        sheet.save(path)
        print(f"[audit] contact sheet -> {path}")

    if flagged:
        print(f"\n[audit] FLAGGED: {', '.join(flagged)}")
        return 1
    print("\n[audit] all textures look structurally sane")
    return 0


if __name__ == "__main__":
    sys.exit(main())
