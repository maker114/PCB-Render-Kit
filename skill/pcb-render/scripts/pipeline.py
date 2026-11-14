"""One-command PCB render pipeline: EDA export package -> 2K/4K Blender renders.

Steps (each also runnable alone, see references/pipeline.md):
  1. unpack the EDA export package (STEP + DXF + BOM + PickAndPlace)
  2. STEP -> GLB (cascadio)
  3. DXF -> textures for the layers the STEP does NOT contain (mask openings, drill)
  4. STEP -> textures for copper + silkscreen (the authoritative source)
  5. asset audit  (hard gate: aborts if a layer looks structurally wrong)
  6. build board.blend
  7. render hero / top / macro

Usage:
    python pipeline.py --package "<素材包.zip>" --workdir "<工作目录>"
                       [--resolution preview|2k|4k] [--views hero,top,macro]
                       [--samples N] [--blender PATH]
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from env import find_blender as _find_blender, require_python_deps  # noqa: E402

RESOLUTIONS = {
    "preview": dict(hero="800x800", top="800x800", macro="900x630", samples=64),
    "2k": dict(hero="2048x2048", top="2048x2048", macro="2400x1680", samples=150),
    "4k": dict(hero="3840x3840", top="3840x3840", macro="3840x2560", samples=220),
}


def log(msg):
    print(f"\n== {msg}", flush=True)


def run(cmd, what, cwd=None, expect_file=None):
    """Run a step; fail loudly on a non-zero exit.

    Blender returns exit code 0 even when the -P script raises, so for renders we
    also require the output file to exist - otherwise a broken render would be
    reported as success (this actually happened while developing the skill).
    """
    print("   $ " + " ".join(f'"{c}"' if " " in c else c for c in cmd), flush=True)
    t = time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    tail = (r.stdout or "")[-1500:] + (r.stderr or "")[-1500:]
    if r.stdout:
        print(r.stdout.strip()[-1200:])
    if "Traceback (most recent call last)" in tail:
        print(tail)
        raise SystemExit(f"[FAIL] {what}: python/Blender raised (exit {r.returncode})")
    if r.returncode != 0:
        print(tail)
        raise SystemExit(f"[FAIL] {what} (exit {r.returncode})")
    if expect_file and not (os.path.exists(expect_file) and os.path.getsize(expect_file) > 1024):
        print(tail)
        raise SystemExit(f"[FAIL] {what}: expected output missing/empty -> {expect_file}")
    print(f"   ok ({time.time() - t:.1f}s)", flush=True)


def find_blender(explicit):
    """Blender 路径全部交给 env.py 探测（--blender / config.json / PATH / 常见安装位置）。"""
    return _find_blender(explicit or None)


def step_unpack(package, workdir):
    import zipfile
    log(f"unpack {package}")
    os.makedirs(workdir, exist_ok=True)
    with zipfile.ZipFile(package) as zf:
        zf.extractall(workdir)
        for i in sorted(zf.infolist(), key=lambda x: -x.file_size)[:8]:
            print(f"   {i.file_size:>12}  {i.filename}")
    steps = sorted(glob.glob(os.path.join(workdir, "*.step")), key=os.path.getsize, reverse=True)
    dxfs = glob.glob(os.path.join(workdir, "*.dxf"))
    if not steps:
        raise SystemExit("no .step in the package")
    if not dxfs:
        raise SystemExit("no .dxf in the package")
    return steps[0], dxfs[0]


def step_glb(step_file, glb):
    import cascadio
    log("STEP -> GLB")
    t = time.time()
    cascadio.step_to_glb(step_file, glb, tol_linear=0.05, tol_angular=0.3,
                         merge_primitives=True, use_parallel=True, include_materials=True)
    if not os.path.exists(glb):
        raise SystemExit("STEP -> GLB produced no file")
    print(f"   {os.path.getsize(glb)/1e6:.1f} MB in {time.time()-t:.1f}s", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--resolution", choices=list(RESOLUTIONS), default="2k")
    ap.add_argument("--views", default="hero,top,macro")
    ap.add_argument("--samples", type=int, default=0)
    ap.add_argument("--blender", default="")
    ap.add_argument("--skip-render", action="store_true")
    ap.add_argument("--macro-target", default="0.010,-0.010,0.0016",
                    help="macro aim point in scene metres (board centre = 0,0)")
    ap.add_argument("--macro-distance", type=float, default=0.062,
                    help="macro camera distance in metres")
    args = ap.parse_args()

    require_python_deps()          # 缺依赖时给出安装命令，而不是裸 traceback
    blender = find_blender(args.blender)
    render = os.path.join(args.workdir, "render")
    tex = os.path.join(render, "tex")
    data = os.path.join(render, "data")
    blend_dir = os.path.join(render, "blend")
    final = os.path.join(render, "out", "final")
    diag = os.path.join(render, "out", "diag")
    for d in (tex, data, blend_dir, final, diag):
        os.makedirs(d, exist_ok=True)
    glb = os.path.join(data, "board.glb")
    board_blend = os.path.join(blend_dir, "board.blend")

    py = sys.executable
    print(f"blender : {blender}\npython  : {py}\nworkdir : {args.workdir}")

    step_file, dxf_file = step_unpack(args.package, args.workdir)
    print(f"   STEP: {os.path.basename(step_file)}   DXF: {os.path.basename(dxf_file)}")

    step_glb(step_file, glb)

    log("DXF textures (mask openings / drill; also writes align.json)")
    run([py, os.path.join(HERE, "build_textures.py"), "--dxf", dxf_file, "--outdir", tex],
        "build_textures.py")

    log("STEP textures (copper + silkscreen, authoritative source)")
    run([py, os.path.join(HERE, "layers_from_step.py"), "--tex", tex, "--glb", glb],
        "layers_from_step.py")

    log("asset audit (hard gate - no render if this fails)")
    r = subprocess.run([py, os.path.join(HERE, "audit_assets.py"), "--tex", tex])
    if r.returncode != 0:
        raise SystemExit("[STOP] audit failed: a layer texture is structurally wrong; "
                         "do NOT render. See the verdict column above.")

    log("footprint recon (extend materials.py FOOTPRINT_MAP for unknown footprints)")
    subprocess.run([py, os.path.join(HERE, "recon_footprints.py"), "--glb", glb])

    log("build board.blend")
    run([blender, "-b", "--factory-startup", "-P", os.path.join(HERE, "build_board.py"), "--",
         "--glb", glb, "--tex", tex, "--blend", board_blend,
         "--rough", "0.45,0.38", "--spec", "0.3", "--coat", "0.06"],
        "build_board.py")

    if args.skip_render:
        print("\n[skip-render] scene is ready:", board_blend)
        return

    p = RESOLUTIONS[args.resolution]
    samples = args.samples or p["samples"]
    want = {v.strip() for v in args.views.split(",") if v.strip()}

    def render(name, extra, res):
        log(f"render {name} ({res}, {samples} spp)")
        run([blender, "-b", "--factory-startup", board_blend,
             "-P", os.path.join(HERE, "render_view.py"), "--",
             "--res", res, "--samples", str(samples),
             "--light", "pcb_hero", "--floor", "black",
             "--out", os.path.join(final, f"{name}_16bit.png")] + extra,
            f"render {name}", expect_file=os.path.join(final, f"{name}_16bit.png"))

    if "hero" in want:
        render("01_hero", ["--framing", "three_quarter", "--azimuth", "-32", "--elevation", "38",
                           "--margin", "0.0", "--exposure", "-0.8"], p["hero"])
    if "top" in want:
        render("02_top", ["--framing", "top", "--azimuth", "0", "--elevation", "84",
                          "--margin", "0.05", "--exposure", "-0.2"], p["top"])
    if "macro" in want:
        render("03_macro", ["--framing", "three_quarter", "--target", args.macro_target,
                            "--azimuth", "-32", "--elevation", "44", "--lens", "100",
                            "--distance", str(args.macro_distance), "--margin", "0.05", "--exposure", "-0.8"],
               p["macro"])

    log("down-convert 16-bit masters -> 8-bit PNG + JPEG")
    from PIL import Image
    for f in sorted(glob.glob(os.path.join(final, "*_16bit.png"))):
        base = f[: -len("_16bit.png")]
        im = Image.open(f)
        im.convert("RGB").save(base + ".png", optimize=True)
        im.convert("RGB").save(base + ".jpg", quality=95, subsampling=0)
        print(f"   {os.path.basename(base)}.png / .jpg", flush=True)

    print(f"\n== done\n   renders: {final}")
    print("   QA next: open the renders at 100%, crop 3 spots (pad / trace / silk label) and check them.")


if __name__ == "__main__":
    main()
