"""Render one studio view of the PCB scene produced by build_board.py.

Usage:
    blender -b <board.blend> --factory-startup -P render_view.py -- [options]

Options:
    --framing three_quarter|top|low|detail
    --res WxH            (default 3840x2160)
    --samples N          (default 256)
    --out PATH           output PNG
    --azimuth DEG        camera azimuth override
    --elevation DEG      camera elevation override
    --lens MM            focal length override
    --margin F           framing margin (default 0.10)
    --world STYLE        studio world style (studio_dark|studio_light|transparent)
    --light STYLE        light rig style
    --floor STYLE        floor style (shadow|glossy|matte|black|light|none)
    --exposure F         exposure offset in stops
    --target X,Y,Z       focus/aim point override (metres, scene space)
    --region R           detail framing size (metres) - optional
    --no-dof
    --percent N          resolution percentage (default 100)
    --device DEVICE      AUTO (default) / CPU / GPU

The PCB scene convention: board centred on X/Y, bottom face at z = 0, top at z=1.6 mm.
"""

from __future__ import annotations

import argparse
import os
import sys

import bpy
from mathutils import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
# studio.py lives next to this script in the skill package, and in ../scene in
# the per-board working tree - support both.
STUDIO_DIR = HERE if os.path.exists(os.path.join(HERE, "studio.py")) \
    else os.path.join(os.path.dirname(HERE), "scene")
sys.path.insert(0, STUDIO_DIR)
sys.path.insert(0, HERE)

import studio  # noqa: E402


def pcb_bbox():
    lo = Vector((1e18, 1e18, 1e18))
    hi = Vector((-1e18, -1e18, -1e18))
    found = False
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        if obj.name.startswith("STUDIO_"):
            continue
        found = True
        for corner in obj.bound_box:
            world = obj.matrix_world @ Vector(corner)
            lo.x, lo.y, lo.z = min(lo.x, world.x), min(lo.y, world.y), min(lo.z, world.z)
            hi.x, hi.y, hi.z = max(hi.x, world.x), max(hi.y, world.y), max(hi.z, world.z)
    if not found:
        raise RuntimeError("no PCB meshes found in the scene")
    return lo, hi


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--framing", default="three_quarter")
    ap.add_argument("--res", default="3840x2160")
    ap.add_argument("--samples", type=int, default=256)
    ap.add_argument("--out", required=True)
    ap.add_argument("--azimuth", type=float, default=None)
    ap.add_argument("--elevation", type=float, default=None)
    ap.add_argument("--lens", type=float, default=None)
    ap.add_argument("--margin", type=float, default=0.10)
    ap.add_argument("--world", default="studio_dark")
    ap.add_argument("--light", default="product")
    ap.add_argument("--floor", default="shadow")
    ap.add_argument("--exposure", type=float, default=0.0)
    ap.add_argument("--target", default="")
    ap.add_argument("--region", type=float, default=0.0)
    ap.add_argument("--distance", type=float, default=0.0, help="override camera distance to target (metres)")
    ap.add_argument("--no-dof", action="store_true")
    ap.add_argument("--percent", type=int, default=100)
    ap.add_argument("--device", default="AUTO")
    args = ap.parse_args(argv)

    lo, hi = pcb_bbox()
    dims = hi - lo
    center = (lo + hi) * 0.5
    top_z = hi.z
    target = center.copy()
    if args.target:
        target = Vector([float(v) for v in args.target.split(",")])
    elif args.region > 0.0:
        target.z = top_z - 0.0005  # sit the focus on the board's top surface
    else:
        target.z = top_z * 0.35

    print(f"[view] bbox lo={tuple(round(v,5) for v in lo)} hi={tuple(round(v,5) for v in hi)}")
    print(f"[view] dims(mm)={tuple(round(v*1000,2) for v in dims)} target={tuple(round(v,5) for v in target)}")

    res = tuple(int(v) for v in args.res.split("x"))
    studio.setup_render(
        samples=args.samples,
        resolution=res,
        percent=args.percent,
        device=args.device,
        exposure=args.exposure,
        color_depth="16",
        adaptive_threshold=0.01 if args.samples >= 128 else 0.02,
    )
    studio.setup_world(args.world)
    detail_kw = {}
    if args.region > 0.0:
        detail_kw = {"target": target, "region_size": args.region}
    studio.setup_camera(
        center=target if args.region else center,
        bbox_size=dims if not args.region else (args.region, args.region, args.region),
        framing=args.framing,
        lens=args.lens,
        margin=args.margin,
        azimuth=args.azimuth,
        elevation=args.elevation,
        dof=not args.no_dof,
        **detail_kw,
    )
    cam = bpy.context.scene.camera
    if args.distance > 0.0:
        aim = target
        direction = (cam.location - aim)
        if direction.length > 1e-6:
            cam.location = aim + direction.normalized() * args.distance
            print(f"[view] camera distance overridden -> {args.distance*1000:.0f} mm")
    studio.setup_lights(center, dims, style=args.light, camera=cam)
    studio.add_floor(center, dims, style=args.floor)
    studio.render_to(os.path.abspath(args.out))
    print(f"[view] done -> {args.out}")


if __name__ == "__main__":
    main()
