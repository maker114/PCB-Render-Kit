"""Rasterise board layers straight from the STEP/GLB geometry.

Why not the DXF?  (Both of these actually happened on this project)
  * copper: a board with a ground pour exports the pour as nested filled
    polygons whose clearances are easy to lose -> the raster becomes a solid
    rectangle and every trace disappears.
  * silkscreen: DXF TEXT entities have to be re-drawn with a substitute
    TrueType font whose advance widths differ from the EDA stroke font, so
    longer labels drift out of place by up to ~0.5 mm.

The STEP export contains these layers as real geometry, in exactly the same
coordinate frame as the board, so projecting the triangles is unambiguous and
perfectly registered.  Rule of thumb: if the STEP has the layer, use the STEP.

Usage:
    python layers_from_step.py --tex DIR [--glb FILE] [--px-per-mm 100]
                               [--layers TopCopper,BottomCopper,TopSilkscreen]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glb_reader  # noqa: E402

# STEP product name -> output raster names (first is the primary)
LAYERS = {
    "TopCopper": ["copper_top"],
    "BottomCopper": ["copper_bottom"],
    "TopSilkscreen": ["silk_top"],
    "BottomSilkscreen": ["silk_bottom", "silk_bottom_viewed_from_bottom"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tex", required=True, help="texture directory (reads align.json, writes PNGs)")
    ap.add_argument("--glb", default=None, help="GLB path (default: <tex>/../data/board.glb)")
    ap.add_argument("--px-per-mm", type=float, default=None)
    ap.add_argument("--supersample", type=int, default=2)
    ap.add_argument("--layers", default=None, help="comma separated subset of " + ",".join(LAYERS))
    args = ap.parse_args()

    tex = os.path.abspath(args.tex)
    render = os.path.dirname(tex)
    glb = args.glb or os.path.join(render, "data", "board.glb")
    align = os.path.join(tex, "align.json")
    if not os.path.exists(align):
        raise SystemExit(f"missing {align} - run build_textures.py first")
    with open(align, encoding="utf-8") as fh:
        b = json.load(fh)["board_bbox_mm"]
    x0, y0, x1, y1 = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))

    px = args.px_per_mm
    if px is None:
        px = json.load(open(align, encoding="utf-8"))["textures"]["silk_top"]["px_per_mm"]
    ss = max(1, args.supersample)
    W = int(round((x1 - x0) * px))
    H = int(round((y1 - y0) * px))
    print(f"board {x1-x0:.3f} x {y1-y0:.3f} mm -> {W} x {H} @ {px:g} px/mm (ss={ss})")
    print(f"glb: {glb}")

    wanted = set(LAYERS) if not args.layers else {n.strip() for n in args.layers.split(",") if n.strip()}
    scene = glb_reader.load(glb)
    print(f"GLB: {len(scene.instances)} instances, {len(scene.meshes)} meshes")

    for product, outputs in LAYERS.items():
        if product not in wanted:
            continue
        img = Image.new("L", (W * ss, H * ss), 0)
        draw = ImageDraw.Draw(img)
        tris = 0
        for inst in scene.instances:
            mesh = scene.meshes[inst.mesh_index]
            if mesh.name.split("~")[0] != product:
                continue
            for prim in mesh.primitives:
                pts = prim.positions @ inst.matrix[:3, :3].T + inst.matrix[:3, 3]
                xy = (pts[:, :2] * 1000.0 - np.array([x0, y0])) * px * ss
                xy[:, 1] = H * ss - xy[:, 1]
                for a, bb, c in xy[prim.triangles]:
                    draw.polygon([tuple(a), tuple(bb), tuple(c)], fill=255)
                tris += len(prim.triangles)
        if tris == 0:
            print(f"  {product}: NOT in this STEP export, skipped")
            continue
        small = img.resize((W, H), Image.BOX) if ss > 1 else img
        cov = np.asarray(small).astype(np.float32).mean() / 255.0
        for out_name in outputs:
            data = small.transpose(Image.FLIP_LEFT_RIGHT) if out_name.endswith("viewed_from_bottom") else small
            out = Image.merge("RGBA", (data, data, data, data))
            path = os.path.join(tex, out_name + ".png")
            if os.path.exists(path):
                os.replace(path, path + ".dxf-backup")
            out.save(path)
        print(f"  {product}: {tris} triangles, coverage {cov*100:.1f}% -> {', '.join(outputs)}")
    print("done")


if __name__ == "__main__":
    main()
