"""Compare the DXF-derived silkscreen raster against the STEP silkscreen geometry.

The STEP export of this board contains TopSilkscreen / BottomSilkscreen as real
geometry, so it can serve as an independent reference: if the DXF raster is
offset (or scaled wrongly), the cross-correlation peak will be away from (0,0).

Outputs:
    <diag>/silk_step_top.png        STEP silk rasterised in the DXF frame
    <diag>/silk_overlay_top.png     red = DXF silk, green = STEP silk
    best shift in pixels and millimetres, NCC at zero and at the peak
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, r"E:\DSH WorkSpace\PCB\render\scripts")
import glb_reader  # noqa: E402

RENDER = os.path.dirname(os.path.dirname(HERE))     # .../PCB2/render
TEXDIR = os.path.join(RENDER, "tex")
GLB = os.path.join(RENDER, "data", "board.glb")
OUT = os.path.join(RENDER, "out", "diag")
PX_PER_MM = 100.0


def board_frame():
    with open(os.path.join(TEXDIR, "align.json"), encoding="utf-8") as fh:
        b = json.load(fh)["board_bbox_mm"]
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


def raster_step_layer(footprint, x0, y0, x1, y1):
    W = int(round((x1 - x0) * PX_PER_MM))
    H = int(round((y1 - y0) * PX_PER_MM))
    img = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(img)
    scene = glb_reader.load(GLB)
    n = 0
    for inst in scene.instances:
        mesh = scene.meshes[inst.mesh_index]
        if mesh.name.split("~")[0] != footprint:
            continue
        for prim in mesh.primitives:
            pts = prim.positions @ inst.matrix[:3, :3].T + inst.matrix[:3, 3]
            xy = (pts[:, :2] * 1000.0 - np.array([x0, y0])) * PX_PER_MM
            xy[:, 1] = H - xy[:, 1]
            for a, b, c in xy[prim.triangles]:
                draw.polygon([tuple(a), tuple(b), tuple(c)], fill=255)
            n += len(prim.triangles)
    return img, n


def ncc_shift(a, b, max_shift=80):
    """Cross-correlation via FFT (mask overlap, normalised by union)."""
    a = (a > 128).astype(np.float32)
    b = (b > 128).astype(np.float32)
    fa = np.fft.rfft2(a)
    fb = np.fft.rfft2(b)
    corr = np.fft.irfft2(fa * np.conj(fb), s=a.shape)   # cyclic cross-correlation
    # candidate shifts inside +-max_shift
    h, w = a.shape
    cand = corr[:max_shift + 1, :max_shift + 1].copy()
    cand[1:, 0] = corr[-max_shift:, 0][::-1]
    cand[0, 1:] = corr[0, -max_shift:][::-1]
    cand[1:, 1:] = corr[-max_shift:, -max_shift:][::-1] + 0.0
    # build full signed-shift window properly
    out = {}
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            out[(dy, dx)] = corr[dy % h, dx % w]
    best = max(out.items(), key=lambda kv: kv[1])
    (dy, dx), v = best
    return float(v), dy, dx


def main():
    x0, y0, x1, y1 = board_frame()
    print(f"board frame mm: {x0}..{x1} x {y0}..{y1}")

    step_img, ntri = raster_step_layer("TopSilkscreen", x0, y0, x1, y1)
    print(f"STEP TopSilkscreen: {ntri} triangles")
    step_arr = np.asarray(step_img)
    print(f"  coverage: {(step_arr > 128).mean()*100:.2f}%")

    dxf_img = Image.open(os.path.join(TEXDIR, "silk_top.png")).convert("L")
    dxf_arr = np.asarray(dxf_img)
    print(f"DXF silk_top: {dxf_img.size} coverage: {(dxf_arr > 128).mean()*100:.2f}%")

    if dxf_img.size != step_img.size:
        print("size mismatch, resizing DXF to STEP raster")
        dxf_arr = np.asarray(Image.fromarray(dxf_arr).resize(step_img.size, Image.LANCZOS))

    ncc0 = float(((dxf_arr > 128) & (step_arr > 128)).sum() / max(1, ((dxf_arr > 128) | (step_arr > 128)).sum()))
    print(f"IoU at zero shift: {ncc0:.3f}")

    v, pdy, pdx = ncc_shift(dxf_arr > 128, step_arr > 128, max_shift=80)

    def iou_at(shift_y, shift_x):
        d = dxf_arr > 128
        s_ = step_arr > 128
        h, w = d.shape
        ys, xs = slice(max(0, -shift_y), h - max(0, shift_y)), slice(max(0, -shift_x), w - max(0, shift_x))
        yd, xd = slice(max(0, shift_y), h - max(0, -shift_y)), slice(max(0, shift_x), w - max(0, -shift_x))
        dd = np.zeros_like(d)
        dd[yd, xd] = d[ys, xs]
        inter = np.logical_and(dd, s_).sum()
        union = np.logical_or(dd, s_).sum()
        return float(inter / max(1, union))

    cands = [(pdy, pdx), (-pdy, -pdx), (0, 0)]
    scored = [(iou_at(sy, sx), sy, sx) for sy, sx in cands]
    scored.sort(reverse=True)
    best_iou, dy, dx = scored[0]
    print("IoU candidates (iou, dy, dx):", [(round(v_, 3), y, x) for v_, y, x in scored])
    print(f"best alignment: shift (dy,dx)=({dy},{dx}) px = ({dy/PX_PER_MM:+.3f},{dx/PX_PER_MM:+.3f}) mm, IoU={best_iou:.3f}")
    if abs(dy) or abs(dx):
        print(f"==> DXF silkscreen is offset by {dy/PX_PER_MM:+.3f},{dx/PX_PER_MM:+.3f} mm relative to STEP geometry")
    else:
        print("==> DXF silkscreen already aligned with STEP geometry")

    os.makedirs(OUT, exist_ok=True)
    step_img.save(os.path.join(OUT, "silk_step_top.png"))

    # overlay: red = DXF, green = STEP
    W, H = step_img.size
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    d = dxf_arr > 128
    s = step_arr > 128
    rgb[d, 0] = 255
    rgb[s, 1] = 255
    Image.fromarray(rgb).resize((W // 5, H // 5), Image.BILINEAR).save(
        os.path.join(OUT, "silk_overlay_top.png"))

    # also the same overlay after shifting DXF by the found offset
    shifted = np.zeros_like(d)
    h, w = d.shape
    ys, xs = slice(max(0, -dy), h - max(0, dy)), slice(max(0, -dx), w - max(0, dx))
    yd, xd = slice(max(0, dy), h - max(0, -dy)), slice(max(0, dx), w - max(0, -dx))
    shifted[yd, xd] = d[ys, xs]
    rgb2 = np.zeros((H, W, 3), dtype=np.uint8)
    rgb2[shifted, 0] = 255
    rgb2[s, 1] = 255
    Image.fromarray(rgb2).resize((W // 5, H // 5), Image.BILINEAR).save(
        os.path.join(OUT, "silk_overlay_top_shifted.png"))
    print("wrote silk_step_top.png / silk_overlay_top.png / silk_overlay_top_shifted.png")


if __name__ == "__main__":
    main()
