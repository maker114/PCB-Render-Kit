"""Build a Blender scene from the cascadio GLB of the EasyEDA PCB.

Run headless:
    blender -b --factory-startup -P build_board.py -- [options]

Options:
    --glb PATH       input GLB (default: render/data/board_cascadio.glb)
    --blend PATH     save .blend here (optional)
    --mode MODE      semantic (default) | flat (STEP colours as emission, diagnostic)
    --tex PATH       directory with the DXF-derived textures (silk/mask/copper)
    --out PATH       render a diagnostic PNG here (optional)
    --cam top|hero   camera preset for the diagnostic render
    --res WxH        render resolution (default 2400x1600)
    --samples N      cycles samples (default 24)

Conventions: 1 Blender unit = 1 metre, board top faces +Z, board centred on X/Y
with the bottom surface at z = 0.  Board UVs are laid out in board millimetres so
the DXF artwork (0..100 mm x -65..0 mm) maps 1:1 onto the copper/mask/silk planes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glb_reader  # noqa: E402
import materials as matlib  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../PCB/render
DEFAULT_GLB = os.path.join(ROOT, "data", "board_cascadio.glb")

# Board artwork coordinate frame (millimetres, straight from the DXF outline).
# Overridden per-board from <tex_dir>/align.json when that file exists.
BOARD_X0, BOARD_X1 = 0.0, 100.0
BOARD_Y0, BOARD_Y1 = -65.0, 0.0

# "Visual layer" products that some STEP exports add on top of the board
# (copper / silkscreen as separate 3D layers). We paint those from the DXF
# textures instead, so the meshes are hidden to avoid z-fighting.
VISUAL_LAYER_PRODUCTS = {"TopCopper", "BottomCopper", "TopSilkscreen", "BottomSilkscreen"}


def load_board_frame(tex_dir):
    """Read the artwork frame from align.json; fall back to the defaults."""
    global BOARD_X0, BOARD_X1, BOARD_Y0, BOARD_Y1
    if not tex_dir:
        return
    path = os.path.join(tex_dir, "align.json")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            align = json.load(fh)
        x0, y0, x1, y1 = align["board_bbox_mm"]
        BOARD_X0, BOARD_X1 = float(x0), float(x1)
        BOARD_Y0, BOARD_Y1 = float(y0), float(y1)
        print(f"[build] board frame from align.json: x {BOARD_X0}..{BOARD_X1}  y {BOARD_Y0}..{BOARD_Y1} mm")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[build] WARNING could not read align.json ({exc}); using defaults")


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.objects):
        for item in list(coll):
            coll.remove(item)


def srgb_hex(linear_rgb):
    out = []
    for c in linear_rgb:
        c = max(0.0, min(1.0, c))
        s = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
        out.append(int(round(s * 255)))
    return "#%02X%02X%02X" % tuple(out)


def flat_materials(scene_data):
    out = []
    for smat in scene_data.materials:
        mat = bpy.data.materials.new(name=f"diag_{srgb_hex(smat.base_color)[1:]}")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        shader = nt.nodes.new("ShaderNodeEmission")
        shader.inputs["Color"].default_value = (*smat.base_color, 1.0)
        outn = nt.nodes.new("ShaderNodeOutputMaterial")
        nt.links.new(shader.outputs[0], outn.inputs["Surface"])
        mat.diffuse_color = (*smat.base_color, 1.0)
        out.append(mat)
    return out


def board_uv(verts_mm):
    """Board millimetres -> UV, matching the DXF artwork raster frame."""
    u = (verts_mm[:, 0] - BOARD_X0) / (BOARD_X1 - BOARD_X0)
    v = (verts_mm[:, 1] - BOARD_Y0) / (BOARD_Y1 - BOARD_Y0)
    return np.stack([u, v], axis=1)


def build(glb_path, mode="semantic", tex_dir=None, finish=None):
    load_board_frame(tex_dir)
    scene_data = glb_reader.load(glb_path)

    lib = matlib.build_all(tex_dir, finish) if mode == "semantic" else None
    flat_lib = flat_materials(scene_data) if mode == "flat" else None

    # overall bbox to recentre
    lo = np.full(3, 1e18)
    hi = -lo
    for inst in scene_data.instances:
        for prim in scene_data.meshes[inst.mesh_index].primitives:
            world = np.c_[prim.positions, np.ones(len(prim.positions))] @ inst.matrix.T
            lo = np.minimum(lo, world[:, :3].min(axis=0))
            hi = np.maximum(hi, world[:, :3].max(axis=0))
    offset = np.array([(lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0, lo[2]])

    collection = bpy.data.collections.new("PCB")
    bpy.context.scene.collection.children.link(collection)
    root = bpy.data.objects.new("PCB_ROOT", None)
    root.empty_display_size = 0.01
    collection.objects.link(root)

    objects = []
    stats = {"parts": 0, "board_verts": 0, "skipped_layers": []}

    for inst in scene_data.instances:
        gmesh = scene_data.meshes[inst.mesh_index]
        footprint = gmesh.name.split("~")[0]
        name = inst.name.replace("->0", "")
        if footprint in VISUAL_LAYER_PRODUCTS:
            stats.setdefault("skipped_layers", []).append(footprint)
            continue
        is_board = footprint == "Board"

        verts_all, norms_all, tris_all, mat_all = [], [], [], []
        base = 0
        rot = inst.matrix[:3, :3]
        for prim in gmesh.primitives:
            pts = prim.positions @ rot.T + inst.matrix[:3, 3] - offset
            verts_all.append(pts)
            if prim.normals is not None:
                nrm = prim.normals @ rot.T
                ln = np.linalg.norm(nrm, axis=1, keepdims=True)
                nrm = nrm / np.where(ln > 1e-12, ln, 1.0)
            else:
                nrm = np.zeros_like(pts)
                nrm[:, 2] = 1.0
            norms_all.append(nrm)
            tris_all.append(prim.triangles + base)
            if mode == "flat":
                key = prim.material if prim.material >= 0 else 0
            else:
                hex_color = srgb_hex(scene_data.materials[prim.material].base_color)
                if is_board:
                    key = "board"  # resolved per face below
                else:
                    key = matlib.classify(footprint, hex_color)
            mat_all.append(np.full(len(prim.triangles), key, dtype=object))
            base += len(pts)

        if not tris_all:
            continue
        verts = np.concatenate(verts_all, axis=0)
        norms = np.concatenate(norms_all, axis=0)
        tris = np.concatenate(tris_all, axis=0)
        keys = np.concatenate(mat_all, axis=0)

        verts = verts.astype(np.float32)
        mesh = bpy.data.meshes.new(name=f"mesh_{name}")
        mesh.from_pydata(verts.tolist(), [], tris.tolist())
        mesh.update()

        if is_board:
            vv = (verts.astype(np.float64) + offset) * 1000.0  # back to board mm
            uvs = board_uv(vv)
            uv_layer = mesh.uv_layers.new(name="board_uv")
            loop_v = np.empty(len(mesh.loops), dtype=np.int32)
            mesh.loops.foreach_get("vertex_index", loop_v)
            uv_layer.data.foreach_set("uv", uvs[loop_v].astype(np.float32).ravel())
            stats["board_verts"] = len(verts)

            # classify faces by orientation: top / bottom / edge
            tri_idx = tris
            face_n = norms[tri_idx].sum(axis=1)
            face_n /= np.maximum(np.linalg.norm(face_n, axis=1, keepdims=True), 1e-12)
            cls = np.where(face_n[:, 2] > 0.7, "board_top",
                           np.where(face_n[:, 2] < -0.7, "board_bottom", "fr4_edge"))
            order = ["board_top", "board_bottom", "fr4_edge"]
            slot = {k: i for i, k in enumerate(order)}
            for k in order:
                mesh.materials.append(lib[k])
            mesh.polygons.foreach_set("material_index",
                                      np.array([slot[c] for c in cls], dtype=np.int32))
        else:
            used = []
            for k in keys:
                if k not in used:
                    used.append(k)
            slot = {}
            for k in used:
                slot[k] = len(mesh.materials)
                mesh.materials.append(flat_lib[k] if mode == "flat" else lib[k])
            mesh.polygons.foreach_set("material_index",
                                      np.array([slot[k] for k in keys], dtype=np.int32))

        mesh.polygons.foreach_set("use_smooth", np.ones(len(mesh.polygons), dtype=bool))
        try:
            mesh.normals_split_custom_set_from_vertices([tuple(n) for n in norms])
        except Exception as exc:  # pragma: no cover
            print(f"[build] WARNING custom normals failed for {name}: {exc}")

        obj = bpy.data.objects.new(name=name, object_data=mesh)
        collection.objects.link(obj)
        obj.parent = root
        objects.append(obj)
        stats["parts"] += 1

    return objects, (hi - lo), offset, stats


# ------------------------------------------------------------------ quick lighting
def _enable_gpu(scene):
    """Turn on the OptiX/CUDA device for headless renders."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        prefs.refresh_devices()
        for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
            try:
                prefs.compute_device_type = backend
            except TypeError:
                continue
            devices = [d for d in prefs.devices if d.type == backend]
            if devices:
                for d in prefs.devices:
                    d.use = d.type == backend
                print(f"[gpu] using {backend}: {devices[0].name}")
                return
        scene.cycles.device = "CPU"
        print("[gpu] no GPU backend found, falling back to CPU")
    except Exception as exc:  # pragma: no cover
        scene.cycles.device = "CPU"
        print(f"[gpu] device setup failed ({exc}), falling back to CPU")


def quick_setup(res, cam, out_path, samples):
    """Minimal but decent studio-ish lighting for material validation renders."""
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "GPU"
    _enable_gpu(scene)
    scene.cycles.samples = samples
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 8
    scene.render.resolution_x, scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "AgX"
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except TypeError:
        pass

    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.035, 0.045, 0.06, 1.0)
    bg.inputs[1].default_value = 0.25
    scene.world = world

    # floor
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(0, 0, 0))
    floor = bpy.context.object
    fm = bpy.data.materials.new("floor")
    fm.use_nodes = True
    fb = next(n for n in fm.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    fb.inputs["Base Color"].default_value = (0.03, 0.032, 0.036, 1.0)
    fb.inputs["Roughness"].default_value = 0.35
    floor.data.materials.append(fm)

    def sun(name, strength, rot, angle_deg=8.0, color=(1, 1, 1)):
        ld = bpy.data.lights.new(name, type="SUN")
        ld.energy = strength
        ld.angle = math.radians(angle_deg)
        ld.color = color
        ob = bpy.data.objects.new(name, ld)
        ob.rotation_euler = rot
        scene.collection.objects.link(ob)
        return ob

    def area(name, loc, rot, energy, size, color=(1, 1, 1)):
        ld = bpy.data.lights.new(name, type="AREA")
        ld.energy = energy
        ld.size = size
        ld.color = color
        ob = bpy.data.objects.new(name, ld)
        ob.location = loc
        ob.rotation_euler = rot
        scene.collection.objects.link(ob)
        return ob

    sun("key_sun", 3.4, (math.radians(44), math.radians(-8), math.radians(-34)), 7.0)
    sun("fill_sun", 0.85, (math.radians(60), 0, math.radians(118)), 30.0, (0.92, 0.95, 1.0))
    area("rim", (0.02, 0.105, 0.055), (math.radians(-68), 0, math.radians(176)), 0.4, 0.10)

    cam_data = bpy.data.cameras.new("cam")
    cam_obj = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    if cam == "top":
        cam_data.type = "ORTHO"
        cam_data.ortho_scale = 0.106
        cam_obj.location = (0, 0, 0.4)
        cam_obj.rotation_euler = (0, 0, 0)
        scene.render.resolution_y = int(res[0] * 0.6556)
    else:
        cam_data.type = "PERSP"
        cam_data.lens = 100
        cam_obj.location = (0.028, -0.105, 0.098)
        target = Vector((0.0, -0.004, 0.003))
        cam_obj.rotation_euler = (target - cam_obj.location).to_track_quat("-Z", "Y").to_euler()

    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "8"
    scene.render.filepath = out_path
    bpy.ops.render.render(write_still=True)


def main():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", default=DEFAULT_GLB)
    ap.add_argument("--blend", default="")
    ap.add_argument("--mode", default="semantic", choices=["semantic", "flat"])
    ap.add_argument("--tex", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--cam", default="top")
    ap.add_argument("--res", default="2400x1600")
    ap.add_argument("--samples", type=int, default=24)
    ap.add_argument("--rough", default="0.34,0.27", help="solder-mask roughness over FR4,over copper")
    ap.add_argument("--spec", type=float, default=0.5, help="solder-mask specular IOR level")
    ap.add_argument("--coat", type=float, default=0.12, help="solder-mask clearcoat weight")
    args = ap.parse_args(argv)

    rough = [float(v) for v in args.rough.split(",")]
    finish = {"rough_fr4": rough[0], "rough_cu": rough[1], "spec": args.spec, "coat": args.coat}

    clear_scene()
    tex_dir = args.tex or os.path.join(ROOT, "tex")
    objects, size, offset, stats = build(args.glb, mode=args.mode, tex_dir=tex_dir, finish=finish)
    print(f"[build] mode={args.mode} parts={stats['parts']} bbox_m={np.round(size, 5)} offset={np.round(offset, 6)}")
    print(f"[build] board_verts={stats['board_verts']} tex_dir={tex_dir} finish={finish}")

    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=args.blend)
        print(f"[build] saved {args.blend}")

    if args.out:
        res = tuple(int(v) for v in args.res.split("x"))
        quick_setup(res, args.cam, args.out, args.samples)
        print(f"[render] wrote {args.out}")


if __name__ == "__main__":
    main()

