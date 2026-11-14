"""
studio.py -- reusable photographic studio rig for PCB product renders.

Target: Blender 5.2 LTS, headless (`blender -b -P script.py`).

Scope of this module
--------------------
Camera + lights + world/floor + render settings + composition only. It never
creates or edits PCB geometry or board materials; it expects the caller to
provide the board mesh(es) as objects and a board bounding box.

Conventions (IMPORTANT)
-----------------------
* 1 Blender unit == 1 metre.  `scene.unit_settings.scale_length = 1.0`.
* The board lies in the XY plane, top side faces +Z and is centred at `center`
  (usually the world origin).
* `bbox_size` is either
    - a 3-sequence (dx, dy, dz) in metres, e.g. (0.10, 0.07, 0.0016), or
    - a scalar, interpreted as the long dimension of a 0.1 x 0.07 x 0.0016 style
      board -> dims = (s, 0.7*s, 0.016*s).
* Camera azimuth (degrees): 0 = "front" (camera on -Y looking toward +Y),
  +90 = camera on +X.  Lights are placed relative to the camera azimuth, so
  call `setup_camera()` first (or pass `camera_azimuth=`).
* Repeated calls are safe: `setup_lights()`, `add_floor()` and `setup_camera()`
  replace the previously created studio objects instead of accumulating them.
* All objects created here live in the collection "STUDIO"; `clear_studio()`
  removes them.  Board geometry/materials are never touched.

Typical use
-----------
    import studio
    studio.clear_scene_objects()                 # your board-building code here
    studio.setup_render(samples=256, resolution=(3840, 2160))
    studio.setup_world("studio_dark")
    studio.setup_camera((0, 0, 0), (0.10, 0.07, 0.0016), framing="three_quarter")
    studio.setup_lights((0, 0, 0), (0.10, 0.07, 0.0016))
    studio.add_floor((0, 0, 0), (0.10, 0.07, 0.0016))
    studio.render_to(r"<workdir>/render/out/hero.png")

`setup_scene()` is a convenience wrapper that builds the whole rig.
"""

from __future__ import annotations

import math
import os
import time

import bpy
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view

__all__ = [
    "setup_render",
    "setup_world",
    "setup_lights",
    "setup_camera",
    "add_floor",
    "render_to",
    "add_light_bounce_card",
    "camera_on_azimuth",
    "setup_scene",
    "clear_studio",
    "clear_scene_objects",
]

# --------------------------------------------------------------------------- #
# constants / calibration
# --------------------------------------------------------------------------- #

REFERENCE_SIZE = 0.10          # metre: the reference "long side" this rig is tuned for
SENSOR_WIDTH_MM = 36.0         # full-frame sensor width
COLLECTION_NAME = "STUDIO"

# Bounding box convention used when a scalar bbox_size is given.
_SCALAR_ASPECT = (1.0, 0.70, 0.016)

_STATE = {
    "center": Vector((0.0, 0.0, 0.0)),
    "dims": Vector((0.10, 0.07, 0.0016)),
    "world_style": "studio_dark",
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _scene():
    return bpy.context.scene


def _dims(bbox_size) -> Vector:
    """Normalise bbox_size (scalar or 3-sequence) into a dimension Vector."""
    if bbox_size is None:
        d = Vector((0.10, 0.07, 0.0016))
    elif isinstance(bbox_size, (int, float)):
        s = float(bbox_size)
        d = Vector((_SCALAR_ASPECT[0] * s, _SCALAR_ASPECT[1] * s, _SCALAR_ASPECT[2] * s))
    else:
        seq = list(bbox_size)
        if len(seq) >= 3:
            d = Vector((abs(float(seq[0])), abs(float(seq[1])), abs(float(seq[2]))))
        elif len(seq) == 2:
            d = Vector((abs(float(seq[0])), abs(float(seq[1])), 0.016 * max(seq)))
        elif len(seq) == 1:
            s = abs(float(seq[0]))
            d = Vector((s, 0.7 * s, 0.016 * s))
        else:
            d = Vector((0.10, 0.07, 0.0016))
    d.x = d.x if d.x > 1e-6 else 1e-3
    d.y = d.y if d.y > 1e-6 else 1e-3
    d.z = d.z if d.z > 1e-6 else 1e-3
    return d


def _center(center) -> Vector:
    if center is None:
        return Vector((0.0, 0.0, 0.0))
    return Vector((float(center[0]), float(center[1]), float(center[2])))


def _max_dim(dims: Vector) -> float:
    return max(dims.x, dims.y, dims.z)


def _diag(dims: Vector) -> float:
    return dims.length


def _studio_collection(create: bool = True):
    sc = _scene()
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is None and create:
        coll = bpy.data.collections.new(COLLECTION_NAME)
        sc.collection.children.link(coll)
    return coll


def _link(obj):
    coll = _studio_collection()
    coll.objects.link(obj)
    return obj


def _new_object(name, data):
    obj = bpy.data.objects.new(name, data)
    return _link(obj)


def _remove_studio_objects(*prefixes):
    """Delete studio objects whose names start with any of the given prefixes."""
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is None:
        return
    for obj in list(coll.objects):
        if any(obj.name.startswith(p) for p in prefixes):
            bpy.data.objects.remove(obj, do_unlink=True)


def _remove_data_orphans():
    for datacoll in (bpy.data.lights, bpy.data.cameras, bpy.data.meshes, bpy.data.materials):
        for block in list(datacoll):
            if block.users == 0 and block.name.startswith("STUDIO"):
                try:
                    datacoll.remove(block)
                except Exception:  # noqa: BLE001
                    pass


def _try_set(obj, attr, value, label=None):
    """Set an attribute, silently ignoring API differences between releases."""
    try:
        setattr(obj, attr, value)
        return True
    except Exception as exc:  # noqa: BLE001 - Blender enums are release dependent
        if label:
            print(f"[studio] warning: could not set {label}: {exc}", flush=True)
        return False


def _set_enum(obj, attr, candidates, label=None):
    """Assign the first enum value from `candidates` that the build accepts."""
    for candidate in candidates:
        try:
            setattr(obj, attr, candidate)
            return candidate
        except Exception:  # noqa: BLE001
            continue
    if label:
        print(f"[studio] warning: none of {candidates} valid for {label}", flush=True)
    return None


def clear_scene_objects(keep=("STUDIO", "BOARD")):
    """Delete every object in the scene except those in the given collections."""
    keep = set(keep or ())
    for obj in list(bpy.data.objects):
        colls = [c.name for c in obj.users_collection]
        if keep and any(c in keep for c in colls):
            continue
        bpy.data.objects.remove(obj, do_unlink=True)
    for datacoll in (bpy.data.lights, bpy.data.cameras, bpy.data.meshes, bpy.data.materials):
        for block in list(datacoll):
            if block.users == 0:
                try:
                    datacoll.remove(block)
                except Exception:  # noqa: BLE001
                    pass


def clear_studio():
    """Remove everything created by this module (lights, camera, floor, world)."""
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is not None:
        for obj in list(coll.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(coll)
    _remove_data_orphans()
    world = bpy.data.worlds.get("STUDIO_WORLD")
    if world is not None and world.users == 0:
        bpy.data.worlds.remove(world)
    _STATE["center"] = Vector((0.0, 0.0, 0.0))
    _STATE["dims"] = Vector((0.10, 0.07, 0.0016))
    _STATE["world_style"] = "studio_dark"


# --------------------------------------------------------------------------- #
# 1. render settings / device
# --------------------------------------------------------------------------- #

_COMPUTE_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")


def _setup_cycles_device(requested="AUTO"):
    """Configure Cycles compute device. Returns (device_kind, backend, names)."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU", "CPU", []

    requested = (requested or "AUTO").upper()
    if requested in ("CPU",):
        try:
            _scene().cycles.device = "CPU"
        except Exception:  # noqa: BLE001
            pass
        return "CPU", "CPU", ["CPU"]

    backends = list(_COMPUTE_BACKENDS)
    if requested not in ("AUTO",) and requested in backends:
        backends = [requested] + [b for b in backends if b != requested]

    for backend in backends:
        if _set_enum(prefs, "compute_device_type", [backend]) is None:
            continue
        try:
            prefs.get_devices()
        except Exception:  # noqa: BLE001
            continue
        devices = list(getattr(prefs, "devices", []))
        matching = [d for d in devices if d.type == backend]
        if not matching:
            continue
        for device in devices:
            try:
                device.use = device.type == backend
            except Exception:  # noqa: BLE001
                pass
        try:
            _scene().cycles.device = "GPU"
        except Exception:  # noqa: BLE001
            pass
        return "GPU", backend, [d.name for d in matching]

    try:
        _scene().cycles.device = "CPU"
    except Exception:  # noqa: BLE001
        pass
    return "CPU", "CPU", ["CPU"]


def setup_render(
    engine="CYCLES",
    samples=256,
    resolution=(3840, 2160),
    percent=100,
    device="AUTO",
    denoiser="OPENIMAGEDENOISE",
    denoise_gpu=True,
    adaptive=True,
    adaptive_threshold=0.01,
    adaptive_min_samples=32,
    exposure=0.0,
    view_transform="AgX",
    look="AgX - Medium High Contrast",
    transparent=False,
    color_depth="16",
    image_format="PNG",
    max_bounces=12,
    verbose=True,
):
    """Configure the render engine, sampling, denoiser and colour management.

    Parameters
    ----------
    engine : "CYCLES" (recommended) or "BLENDER_EEVEE".
    samples : final samples per pixel (256 is a good 4K quality/price point).
    resolution : (width, height) in pixels.
    percent : resolution percentage.
    device : "AUTO" (OptiX -> CUDA -> HIP -> ONEAPI -> CPU), "CPU", or a backend name.
    denoiser : "OPENIMAGEDENOISE" (default) or "OPTIX".
    adaptive : use Cycles adaptive sampling.
    adaptive_threshold : 0.01 for final, ~0.03 for fast previews.
    exposure : view-transform exposure in stops (0.0 = calibrated default).
    view_transform / look : colour management; AgX + Medium High Contrast by
        default. Invalid names fall back automatically on this Blender build.
    transparent : render with an alpha background (film_transparent).
    color_depth : "16" or "8" PNG bit depth.

    Returns
    -------
    dict with keys: engine, device, backend, devices, denoiser, samples,
    resolution, percent, view_transform, look, exposure, color_depth.
    """
    sc = _scene()

    try:
        sc.unit_settings.system = "METRIC"
        sc.unit_settings.scale_length = 1.0
        sc.unit_settings.length_unit = "MILLIMETERS"
    except Exception:  # noqa: BLE001
        pass

    if not _set_enum(sc.render, "engine", [engine], "render.engine"):
        raise RuntimeError(
            f"[studio] render engine {engine!r} is not available in this Blender build"
        )
    sc.render.resolution_x, sc.render.resolution_y = int(resolution[0]), int(resolution[1])
    sc.render.resolution_percentage = int(percent)
    sc.render.pixel_aspect_x = 1.0
    sc.render.pixel_aspect_y = 1.0
    sc.render.use_border = False
    sc.render.use_crop_to_border = False
    sc.render.film_transparent = bool(transparent)
    sc.render.use_file_extension = True
    sc.render.use_overwrite = True
    sc.render.use_persistent_data = True
    _try_set(sc.render, "dither_intensity", 1.0)  # kills 8-bit gradient banding

    # ---- colour management -------------------------------------------------
    chosen_vt = _set_enum(sc.view_settings, "view_transform", ["AgX", "Filmic", "Standard"],
                          "view_settings.view_transform") or "Standard"
    looks = [
        look,
        "AgX - Medium High Contrast", "AgX - Punchy", "AgX - Base Contrast",
        "Medium High Contrast", "None",
    ]
    seen, ordered = set(), []
    for name in looks:
        if name and name not in seen:
            ordered.append(name)
            seen.add(name)
    chosen_look = None
    for name in ordered:
        try:
            sc.view_settings.look = name
            chosen_look = name
            break
        except Exception:  # noqa: BLE001
            continue
    sc.view_settings.exposure = float(exposure)
    _try_set(sc.view_settings, "gamma", 1.0)
    _set_enum(sc.display_settings, "display_device", ["sRGB"], "display_device")

    # ---- output format -----------------------------------------------------
    im = sc.render.image_settings
    _set_enum(im, "file_format", [image_format], "image_settings.file_format")
    _set_enum(im, "color_mode", ["RGBA" if transparent else "RGB"], "image_settings.color_mode")
    _set_enum(im, "color_depth", [str(color_depth)], "image_settings.color_depth")
    _try_set(im, "compression", 30)

    # ---- Cycles-specific settings -----------------------------------------
    device_kind, backend, device_names = "CPU", "CPU", ["CPU"]
    chosen_denoiser = None
    if sc.render.engine == "CYCLES":
        device_kind, backend, device_names = _setup_cycles_device(device)
        cy = sc.cycles
        cy.samples = int(samples)
        _try_set(cy, "use_adaptive_sampling", bool(adaptive))
        _try_set(cy, "adaptive_threshold", float(adaptive_threshold))
        _try_set(cy, "adaptive_min_samples", int(adaptive_min_samples))
        _try_set(cy, "use_denoising", True)
        chosen_denoiser = _set_enum(cy, "denoiser", [denoiser, "OPENIMAGEDENOISE", "OPTIX"],
                                    "cycles.denoiser")
        _try_set(cy, "denoising_quality", "HIGH")
        if device_kind == "GPU" and denoise_gpu:
            _try_set(cy, "denoising_use_gpu", True)
        _try_set(cy, "max_bounces", int(max_bounces))
        _try_set(cy, "diffuse_bounces", 3)
        _try_set(cy, "glossy_bounces", 4)
        _try_set(cy, "transmission_bounces", 4)
        _try_set(cy, "transparent_max_bounces", 8)
        _try_set(cy, "caustics_reflective", False)
        _try_set(cy, "caustics_refractive", False)
        _try_set(cy, "blur_glossy", 1.0)
        _try_set(cy, "use_light_tree", True)
        _try_set(cy, "sample_clamp_indirect", 10.0)
        _try_set(cy, "use_auto_tile", True)
        _try_set(cy, "tile_size", 2048)
        _try_set(cy, "use_animated_seed", False)

    report = {
        "engine": sc.render.engine,
        "device": device_kind,
        "backend": backend,
        "devices": device_names,
        "denoiser": chosen_denoiser or "n/a",
        "samples": int(samples),
        "resolution": (int(resolution[0]), int(resolution[1])),
        "percent": int(percent),
        "view_transform": chosen_vt,
        "look": chosen_look or "None",
        "exposure": float(exposure),
        "color_depth": str(color_depth),
        "transparent": bool(transparent),
    }
    if verbose:
        dev = f"{device_kind}/{backend} ({', '.join(device_names)})"
        den = f"{report['denoiser']}{'(gpu)' if device_kind == 'GPU' and denoise_gpu else ''}"
        print(
            f"[studio] render: {report['engine']} | device={dev} | denoise={den} | "
            f"{report['resolution'][0]}x{report['resolution'][1]} @{percent}% | "
            f"{samples} spp adaptive {adaptive_threshold:g} | "
            f"{chosen_vt} / {report['look']} / exposure {exposure:+.2f} | "
            f"{color_depth}-bit {image_format}",
            flush=True,
        )
    return report


# --------------------------------------------------------------------------- #
# 2. world
# --------------------------------------------------------------------------- #

# Gradient stops are linear RGB with t derived from the view-ray Z:
# t = (z + 1) / 2, so t=0.5 is the horizon.
_WORLD_STYLES = {
    "studio_dark": {
        "strength": 1.0,
        "stops": [
            (0.00, (0.005, 0.005, 0.006)),
            (0.34, (0.011, 0.012, 0.014)),
            (0.50, (0.034, 0.035, 0.039)),   # soft horizon glow behind the board
            (0.62, (0.015, 0.015, 0.017)),
            (1.00, (0.003, 0.003, 0.004)),
        ],
    },
    "studio_light": {
        "strength": 1.0,
        "stops": [
            (0.00, (0.42, 0.42, 0.43)),
            (0.34, (0.55, 0.55, 0.56)),
            (0.50, (0.72, 0.72, 0.74)),
            (0.62, (0.58, 0.58, 0.60)),
            (1.00, (0.38, 0.38, 0.40)),
        ],
    },
    "transparent": {
        "strength": 0.8,
        "stops": [
            (0.00, (0.008, 0.008, 0.009)),
            (0.50, (0.030, 0.031, 0.034)),
            (1.00, (0.008, 0.008, 0.009)),
        ],
    },
}


def _build_world(style="studio_dark", strength=None, rotation_deg=0.0):
    preset = _WORLD_STYLES.get(style, _WORLD_STYLES["studio_dark"])
    world = bpy.data.worlds.new("STUDIO_WORLD")
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputWorld")
    out.location = (620, 0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.location = (420, 0)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    coord.location = (-820, 0)
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.location = (-640, 0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-460, 0)
    maprange = nt.nodes.new("ShaderNodeMapRange")
    maprange.location = (-280, 0)
    maprange.inputs["From Min"].default_value = -1.0
    maprange.inputs["From Max"].default_value = 1.0
    maprange.inputs["To Min"].default_value = 0.0
    maprange.inputs["To Max"].default_value = 1.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-60, 0)
    ramp.color_ramp.interpolation = "EASE"

    stops = preset["stops"]
    elements = ramp.color_ramp.elements
    elements[0].position = stops[0][0]
    elements[0].color = (*stops[0][1], 1.0)
    elements[1].position = stops[-1][0]
    elements[1].color = (*stops[-1][1], 1.0)
    for pos, col in stops[1:-1]:
        elem = elements.new(pos)
        elem.color = (*col, 1.0)

    mapping.inputs["Rotation"].default_value = (0.0, 0.0, math.radians(rotation_deg))

    links = nt.links
    links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], sep.inputs["Vector"])
    links.new(sep.outputs["Z"], maprange.inputs["Value"])
    links.new(maprange.outputs["Result"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bg.inputs["Color"])
    links.new(bg.outputs["Background"], out.inputs["Surface"])

    bg.inputs["Strength"].default_value = float(
        preset["strength"] if strength is None else strength
    )
    return world


def setup_world(style="studio_dark", strength=None, rotation_deg=0.0):
    """Procedural (no HDRI) studio environment.

    style:
        "studio_dark"  -- dark charcoal gradient with a gentle horizon glow (default)
        "studio_light" -- bright neutral seamless-paper look
        "transparent"  -- dark neutral environment for reflections, alpha film
    strength:
        optional multiplier for the environment light (None = style default).
    rotation_deg:
        rotate the gradient around Z (rarely needed).

    Returns the world block.
    """
    sc = _scene()
    style = style or "studio_dark"
    old = bpy.data.worlds.get("STUDIO_WORLD")
    world = _build_world(style=style, strength=strength, rotation_deg=rotation_deg)
    sc.world = world
    if old is not None and old is not world and old.users == 0:
        bpy.data.worlds.remove(old)
    _STATE["world_style"] = style

    transparent = style == "transparent"
    sc.render.film_transparent = transparent
    _set_enum(sc.render.image_settings, "color_mode",
              ["RGBA" if transparent else "RGB"], "image_settings.color_mode")

    bg_strength = next(
        (n.inputs["Strength"].default_value for n in world.node_tree.nodes if n.type == "BACKGROUND"),
        0.0,
    )
    print(
        f"[studio] world: procedural {style!r} gradient "
        f"(strength {bg_strength:g}, "
        f"film_transparent={transparent})",
        flush=True,
    )
    return world


# --------------------------------------------------------------------------- #
# 3. lights
# --------------------------------------------------------------------------- #

# Energy is derived from the light distance: E = irradiance * pi * d^2, which is
# the inverse-square scaling for an area light approximating a small emitter.
def _power_for(irradiance, distance):
    return float(irradiance) * math.pi * float(distance) ** 2


def _make_area_light(name, azimuth_deg, elevation_deg, distance, center,
                     size, size_y=None, irradiance=1.0, color=(1.0, 1.0, 1.0),
                     shape=None, spread_deg=180.0):
    """Create an area light aimed at `center`, positioned on an az/el orbit."""
    az = math.radians(float(azimuth_deg))
    el = math.radians(float(elevation_deg))
    direction = Vector((
        math.sin(az) * math.cos(el),
        -math.cos(az) * math.cos(el),
        math.sin(el),
    ))
    location = Vector(center) + direction * float(distance)

    data = bpy.data.lights.new(name, "AREA")
    if shape is None:
        shape = "RECTANGLE" if size_y is not None else "SQUARE"
    _try_set(data, "shape", shape)
    data.size = float(size)
    if size_y is not None:
        try:
            data.size_y = float(size_y)
        except Exception:  # noqa: BLE001
            data.size = float(max(size, size_y))
    data.energy = _power_for(irradiance, distance)
    data.color = tuple(float(c) for c in color)
    _try_set(data, "spread", math.radians(float(spread_deg)))
    _try_set(data, "use_shadow", True)

    obj = _new_object(name, data)
    obj.location = location
    aim = (Vector(center) - location)
    if aim.length < 1e-9:
        aim = Vector((0.0, 0.0, -1.0))
    obj.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()
    obj["studio_light"] = name
    return obj


# Light rig presets.  Each entry: (name, azimuth_offset, elevation, distance,
# size, size_y_factor, irradiance, colour).  Offsets are relative to the camera
# azimuth so the rig follows the chosen framing.  Sizes/distances are multiples
# of the board's long dimension; irradiance values are calibrated for a ~0.1 m
# board at AgX exposure 0.
_LIGHT_STYLES = {
    "product": dict(
        exposure=0.60,
        lights=[
            # soft key: upper front-left, large enough for soft shadows
            ("key", -50.0, 44.0, 3.6, 2.0, None, 1.9, (1.00, 0.96, 0.91)),
            # broad dim fill from the right to open up the shadow side
            ("fill", 68.0, 16.0, 4.8, 2.8, None, 0.16, (0.80, 0.88, 1.00)),
            # rim: low behind the board, grazes the far edge and keeps the mask
            # deep instead of washing it with a broad specular reflection
            ("rim", 168.0, 19.0, 3.1, 1.3, None, 2.6, (0.94, 0.97, 1.00)),
            # second weaker kicker on the other edge
            ("rim2", -150.0, 12.0, 3.2, 1.0, None, 0.95, (0.90, 0.95, 1.00)),
            # broad top fill: keeps the mask readable, softens the key shadow
            ("top", 8.0, 78.0, 4.0, 2.6, None, 0.11, (1.00, 1.00, 1.00)),
            # strip lights behind/above: elongated specular streaks on the mask
            # and sharp glints on gold pads (mirror direction for a 30-35 deg camera)
            ("strip_l", 176.0, 31.0, 2.8, 0.17, 2.2, 0.85, (1.00, 0.97, 0.93)),
            ("strip_r", -172.0, 28.0, 2.8, 0.13, 1.9, 0.55, (0.95, 0.97, 1.00)),
        ],
        bounce=0.20,
    ),
    # Tuned against the real 100 x 66 mm alarm/LED driver board (matte solder
    # mask + textured art).  Deep saturated blue, readable far half, no broad
    # white sheen; strips only exist for glints on gold pins and metal shells.
    "pcb_hero": dict(
        exposure=0.60,   # calibrated: use view exposure -0.8 (AgX Med High Contrast)
        lights=[
            # big soft key from the camera's upper-left: gentle left-to-right
            # falloff across the board, soft shadow toward the lower right
            ("key", -46.0, 52.0, 3.4, 2.2, None, 2.2, (1.00, 0.96, 0.92)),
            # low, wide fill from camera-right: keeps the connector half readable
            # without flattening the mask (raised from 0.55 after the hero sweep)
            ("fill", 72.0, 18.0, 5.0, 3.2, None, 1.05, (0.82, 0.90, 1.00)),
            # low rim behind the board: silhouette and connector-side definition
            ("rim", 162.0, 26.0, 3.2, 1.6, None, 3.0, (0.94, 0.97, 1.00)),
            # weaker edge kicker on the opposite side
            ("rim2", -150.0, 18.0, 3.4, 1.2, None, 1.1, (0.90, 0.95, 1.00)),
            # broad but small top fill: keeps the matte mask colour everywhere
            ("top", 6.0, 76.0, 4.2, 3.0, None, 0.45, (1.00, 1.00, 1.00)),
            # thin strips: specular glints on gold pins / metal shells only
            ("strip_l", 178.0, 34.0, 3.0, 0.18, 2.2, 1.40, (1.00, 0.97, 0.93)),
            ("strip_r", -170.0, 30.0, 3.0, 0.14, 1.9, 0.90, (0.95, 0.97, 1.00)),
        ],
        bounce=0.30,
    ),
    "dramatic": dict(
        exposure=0.55,
        lights=[
            ("key", -58.0, 50.0, 3.2, 1.7, None, 1.8, (1.00, 0.94, 0.87)),
            ("fill", 72.0, 14.0, 4.8, 4.8, None, 0.12, (0.74, 0.86, 1.00)),
            ("rim", 168.0, 30.0, 2.8, 1.4, None, 3.2, (0.92, 0.96, 1.00)),
            ("rim2", -144.0, 20.0, 3.0, 1.2, None, 1.0, (0.88, 0.94, 1.00)),
            ("top", 14.0, 80.0, 3.8, 4.0, None, 0.15, (1.0, 1.0, 1.0)),
            ("strip_l", 178.0, 29.0, 2.4, 0.24, 2.6, 1.8, (1.00, 0.95, 0.88)),
            ("strip_r", -170.0, 25.0, 2.4, 0.20, 2.2, 1.2, (0.90, 0.95, 1.00)),
        ],
        bounce=0.10,
    ),
    "soft": dict(
        exposure=0.70,
        lights=[
            ("key", -45.0, 42.0, 3.0, 3.2, None, 2.0, (1.00, 0.97, 0.94)),
            ("fill", 60.0, 22.0, 3.6, 4.4, None, 0.75, (0.86, 0.92, 1.00)),
            ("rim", 155.0, 30.0, 2.8, 2.4, None, 1.4, (0.95, 0.97, 1.00)),
            ("rim2", -148.0, 24.0, 2.9, 2.0, None, 0.55, (0.92, 0.96, 1.00)),
            ("top", 10.0, 76.0, 3.4, 4.2, None, 0.55, (1.0, 1.0, 1.0)),
            ("strip_l", 176.0, 31.0, 2.5, 0.40, 2.2, 0.85, (1.00, 0.98, 0.95)),
            ("strip_r", -170.0, 29.0, 2.5, 0.34, 2.0, 0.65, (0.96, 0.98, 1.00)),
        ],
        bounce=0.50,
    ),
    "top": dict(
        exposure=0.65,
        lights=[
            ("key", -40.0, 68.0, 2.8, 2.4, None, 1.8, (1.00, 0.98, 0.95)),
            ("fill", 70.0, 42.0, 3.2, 3.6, None, 0.6, (0.86, 0.92, 1.00)),
            ("rim", 150.0, 18.0, 2.8, 2.0, None, 1.0, (0.94, 0.97, 1.00)),
            ("top", 5.0, 84.0, 3.2, 4.2, None, 0.85, (1.0, 1.0, 1.0)),
            # near-vertical strips give streaks on the mask in a top view
            ("strip_l", -26.0, 76.0, 2.6, 0.26, 2.2, 1.30, (1.0, 0.98, 0.95)),
            ("strip_r", 30.0, 76.0, 2.6, 0.22, 2.0, 0.95, (0.95, 0.98, 1.0)),
        ],
        bounce=0.25,
    ),
}

# default azimuth used when setup_lights is called before setup_camera
_DEFAULT_CAMERA_AZIMUTH = -32.0


def setup_lights(center=(0.0, 0.0, 0.0), bbox_size=0.10, style="product",
                 camera=None, camera_azimuth=None, exposure=1.0, verbose=True):
    """Area-light rig scaled to the board (replaces any previous studio lights).

    center : board centre (Vector / 3-tuple).
    bbox_size : board bounding box (dims or scalar, see module docstring).
    style : "product" (default), "dramatic", "soft", "top".
    camera : optional camera object; the rig is oriented relative to it.
    camera_azimuth : optional explicit camera azimuth in degrees.
    exposure : master multiplier on every light's power (1.0 = calibrated).

    Returns the list of light objects.
    """
    center = _center(center)
    dims = _dims(bbox_size)
    _STATE["center"] = center.copy()
    _STATE["dims"] = dims.copy()

    _remove_studio_objects("STUDIO_light")

    if camera_azimuth is None and camera is not None:
        camera_azimuth = camera.get("studio_azimuth_deg")
    if camera_azimuth is None:
        cam = _scene().camera
        camera_azimuth = cam.get("studio_azimuth_deg") if cam else None
    if camera_azimuth is None:
        camera_azimuth = _DEFAULT_CAMERA_AZIMUTH
    camera_azimuth = float(camera_azimuth)

    preset = _LIGHT_STYLES.get(style, _LIGHT_STYLES["product"])
    size_ref = _max_dim(dims)
    master = float(preset.get("exposure", 1.0)) * float(exposure)

    lights = []
    for name, az_off, el, dist_f, size_f, size_y_f, irr, color in preset["lights"]:
        distance = size_ref * dist_f
        size = size_ref * size_f
        size_y = size_ref * size_y_f if size_y_f else None
        light = _make_area_light(
            f"STUDIO_light_{name}",
            azimuth_deg=camera_azimuth + az_off,
            elevation_deg=el,
            distance=distance,
            center=center,
            size=size,
            size_y=size_y,
            irradiance=irr * master,
            color=color,
        )
        lights.append(light)

    bounce = preset.get("bounce", 0.2) * master
    if bounce > 0.01:
        lights.append(add_light_bounce_card(
            center, dims, side="front", strength=bounce, size_factor=2.6,
        ))

    if verbose:
        print(
            f"[studio] lights: {style!r} rig, {len(lights)} area lights, "
            f"camera azimuth {camera_azimuth:g} deg, board size {size_ref*1000:.1f} mm",
            flush=True,
        )
    return lights


def add_light_bounce_card(center=(0.0, 0.0, 0.0), bbox_size=0.10, side="front",
                          strength=0.3, size_factor=2.6, distance_factor=2.2,
                          color=(1.0, 0.98, 0.95)):
    """Large, dim, invisible "bounce card" that lifts the shadow side.

    `strength` is a small fraction of the key light irradiance.
    `side` is "front", "left", "right", "front-left", "front-right", "back".
    """
    center = _center(center)
    dims = _dims(bbox_size)
    size_ref = _max_dim(dims)
    az = float(_DEFAULT_CAMERA_AZIMUTH)
    cam = _scene().camera
    if cam is not None and cam.get("studio_azimuth_deg") is not None:
        az = float(cam["studio_azimuth_deg"])
    offsets = {"front": 0.0, "left": -90.0, "right": 90.0,
               "front-left": -45.0, "front-right": 45.0, "back": 180.0}
    az += offsets.get(side, 0.0)

    distance = size_ref * distance_factor
    light = _make_area_light(
        f"STUDIO_light_bounce_{side}", azimuth_deg=az, elevation_deg=6.0,
        distance=distance, center=center, size=size_ref * size_factor,
        size_y=size_ref * 0.9, irradiance=strength, color=color,
    )
    return light


# --------------------------------------------------------------------------- #
# 4. camera
# --------------------------------------------------------------------------- #

_FRAMING_PRESETS = {
    # azimuth, elevation, lens (mm, full-frame equivalent), default margin
    "three_quarter": dict(azimuth=-32.0, elevation=31.0, lens=135.0, margin=0.09),
    "top": dict(azimuth=0.0, elevation=84.0, lens=200.0, margin=0.06),
    "low": dict(azimuth=-24.0, elevation=14.0, lens=85.0, margin=0.06),
    "detail": dict(azimuth=-20.0, elevation=42.0, lens=100.0, margin=0.08),
}


def _orbit_direction(azimuth_deg, elevation_deg):
    az = math.radians(float(azimuth_deg))
    el = math.radians(float(elevation_deg))
    return Vector((
        math.sin(az) * math.cos(el),
        -math.cos(az) * math.cos(el),
        math.sin(el),
    ))


def _aim(obj, target):
    aim = Vector(target) - obj.location
    if aim.length < 1e-9:
        aim = Vector((0.0, 0.0, -1.0))
    obj.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()


def _bbox_corners(center, dims):
    hx, hy, hz = dims.x * 0.5, dims.y * 0.5, dims.z * 0.5
    return [
        Vector((center.x + sx, center.y + sy, center.z + sz))
        for sx in (-hx, hx) for sy in (-hy, hy) for sz in (-hz, hz)
    ]


def _fit_distance(cam_obj, center, dims, direction, margin=0.10, iterations=40,
                  target_center=None):
    """Move the camera along `direction` until the bbox fits with `margin`."""
    scene = _scene()
    fit_center = Vector(target_center) if target_center is not None else Vector(center)
    corners = _bbox_corners(fit_center, dims)
    data = cam_obj.data
    try:
        angle_min = min(data.angle_x, data.angle_y)
    except Exception:  # noqa: BLE001
        angle_min = math.radians(30.0)
    radius = dims.length * 0.5
    distance = max(radius / max(math.tan(angle_min * 0.5), 1e-4) * 1.6, radius * 1.5)
    target_e = 1.0 - 2.0 * float(margin)   # content half-extent in frame units

    world_center = Vector(center)
    for _ in range(int(iterations)):
        cam_obj.location = world_center + direction * distance
        _aim(cam_obj, fit_center)
        bpy.context.view_layer.update()
        us, vs = [], []
        behind = False
        for corner in corners:
            co = world_to_camera_view(scene, cam_obj, corner)
            if co.z <= 1e-6:
                behind = True
                break
            us.append(co.x)
            vs.append(co.y)
        if behind:
            distance *= 1.8
            continue
        ex = max(max(us) - 0.5, 0.5 - min(us)) / 0.5
        ey = max(max(vs) - 0.5, 0.5 - min(vs)) / 0.5
        extent = max(ex, ey)
        if extent < 1e-6:
            distance *= 0.6
            continue
        ratio = extent / target_e
        if abs(ratio - 1.0) < 0.004:
            break
        distance *= ratio ** 0.9
        distance = max(distance, radius * 1.02)
    return distance


def _auto_fstop(cam_obj, focus_point, dims, max_blur_px=2.5, fstop_min=8.0):
    """F-stop that keeps the bbox edge blur below `max_blur_px` at current res.

    At PCB scale (0.1 m board filling a 16:9 frame) a literal f/8-f/16 has only a
    fraction of a millimetre of depth of field, so product renders need a much
    larger effective f-stop to keep the whole board sharp.  We derive it from a
    blur budget instead; the floor/background still falls off naturally.
    """
    scene = _scene()
    res_x = max(int(scene.render.resolution_x), 1)
    res_y = max(int(scene.render.resolution_y), 1)
    sensor = SENSOR_WIDTH_MM if res_x >= res_y else SENSOR_WIDTH_MM * res_y / res_x
    blur_sensor_mm = float(max_blur_px) * sensor / float(res_x)
    c = blur_sensor_mm / 1000.0

    focus_point = Vector(focus_point)
    loc = cam_obj.location.copy()
    to_focus = (focus_point - loc)
    s = max(to_focus.length, 1e-4)
    view_dir = to_focus.normalized() if to_focus.length > 1e-9 else Vector((0, 0, -1))
    half = dims * 0.5
    depth = abs(view_dir.x) * half.x + abs(view_dir.y) * half.y + abs(view_dir.z) * half.z
    if depth < 1e-6:
        depth = 1e-6
    f = cam_obj.data.lens / 1000.0
    denominator = c * max(s - f, 1e-5) * (s + depth)
    if denominator <= 0.0:
        return float(fstop_min)
    n = (f * f * depth) / denominator * 1.25
    return float(min(max(n, fstop_min), 1.0e5))


def _refresh_auto_fstop():
    """Recompute an auto f-stop for the current output resolution (called by render_to)."""
    scene = _scene()
    cam = scene.camera
    if cam is None or not cam.get("studio_auto_blur_px"):
        return
    dims = Vector(tuple(cam["studio_dims"]))
    focus = Vector(tuple(cam["studio_focus_point"]))
    n = _auto_fstop(cam, focus, dims, max_blur_px=float(cam["studio_auto_blur_px"]))
    try:
        cam.data.dof.aperture_fstop = n
    except Exception:  # noqa: BLE001
        pass
    cam["studio_fstop"] = n


def setup_camera(center=(0.0, 0.0, 0.0), bbox_size=0.10, framing="three_quarter",
                 lens=None, target=None, region_size=None, margin=None,
                 azimuth=None, elevation=None, fstop="auto", dof=True,
                 focus_target=None, max_blur_px=2.5):
    """Create (or re-use) the product-shot camera and auto-fit it to the board.

    Parameters
    ----------
    center, bbox_size : board placement / size (see module docstring).
    framing : "three_quarter" | "top" | "low" | "detail".
    lens : focal length in mm (full-frame equivalent); None = framing preset.
    target, region_size : required for framing="detail": the world-space point to
        frame and the width (metres) of the region around it.
    margin : per-side empty margin (0.10 = 10 % of the frame on each side);
        None = framing preset.
    azimuth, elevation : optional overrides in degrees.
    fstop : "auto" (default: keeps the board crisp while the background falls
        off) or a float for a literal photographic aperture (shallow DOF).
    dof : enable depth of field (focus is at the board / region centre).
    focus_target : optional explicit focus point (default = target or center).
    max_blur_px : blur budget for the auto f-stop at the current resolution.

    Returns the camera object (also set as scene.camera).
    """
    scene = _scene()
    center = _center(center)
    dims = _dims(bbox_size)
    _STATE["center"] = center.copy()
    _STATE["dims"] = dims.copy()

    preset = _FRAMING_PRESETS.get(framing)
    if preset is None:
        raise ValueError(
            f"[studio] unknown framing {framing!r}; expected one of "
            f"{sorted(_FRAMING_PRESETS)}"
        )

    az = float(preset["azimuth"] if azimuth is None else azimuth)
    el = float(preset["elevation"] if elevation is None else elevation)
    lens_mm = float(preset["lens"] if lens is None else lens)
    if margin is None:
        margin = float(preset["margin"])
    margin = float(margin)

    # framing target: whole board, or a macro region
    fit_center = center.copy()
    fit_dims = dims.copy()
    if framing == "detail":
        # Accept both calling conventions:
        #   setup_camera(target=..., region_size=...)            <- explicit
        #   setup_camera(center=target, bbox_size=region)        <- convenience
        if target is None:
            target = center
        if region_size is None:
            extents = sorted((dims.x, dims.y, dims.z))
            if extents[2] <= extents[0] * 1.3:      # bbox is a cubic region
                region_size = extents[0]
            else:                                    # bbox is the whole board
                region_size = 0.40 * min(dims.x, dims.y)
        fit_center = _center(target)
        r = float(region_size)
        fit_dims = Vector((r, r * 0.75, max(r * 0.12, dims.z * 2.0)))
        margin = max(0.04, min(margin, 0.12))

    # ---- camera object (re-use the previous one) ---------------------------
    cam_obj = bpy.data.objects.get("STUDIO_camera")
    if cam_obj is None:
        cam_data = bpy.data.cameras.new("STUDIO_CAMERA")
        cam_obj = _new_object("STUDIO_camera", cam_data)
    cam_data = cam_obj.data
    cam_data.sensor_fit = "AUTO"
    cam_data.sensor_width = SENSOR_WIDTH_MM
    cam_data.lens = lens_mm
    cam_data.clip_start = 0.001 if _max_dim(dims) < 0.2 else 0.01
    cam_data.clip_end = 100.0
    scene.camera = cam_obj

    direction = _orbit_direction(az, el)
    distance = _fit_distance(cam_obj, center, fit_dims, direction, margin=margin,
                             target_center=fit_center)
    cam_obj.location = center + direction * distance
    _aim(cam_obj, fit_center)
    bpy.context.view_layer.update()

    # ---- depth of field ----------------------------------------------------
    if focus_target is None:
        focus_target = fit_center if framing == "detail" else center
    focus = _center(focus_target)
    cam_data.dof.use_dof = bool(dof)
    _try_set(cam_data.dof, "aperture_blades", 0)       # round bokeh
    _try_set(cam_data.dof, "aperture_rotation", 0.0)
    auto = dof and isinstance(fstop, str) and fstop.lower() == "auto"
    if dof:
        cam_data.dof.focus_distance = (cam_obj.location - focus).length
        if auto:
            n = _auto_fstop(cam_obj, focus, fit_dims, max_blur_px=max_blur_px)
        else:
            n = float(fstop)
        cam_data.dof.aperture_fstop = n
    cam_obj["studio_auto_blur_px"] = float(max_blur_px) if auto else 0.0

    cam_obj["studio_framing"] = framing
    cam_obj["studio_azimuth_deg"] = az
    cam_obj["studio_elevation_deg"] = el
    cam_obj["studio_lens_mm"] = lens_mm
    cam_obj["studio_center"] = list(center)
    cam_obj["studio_dims"] = [fit_dims.x, fit_dims.y, fit_dims.z]
    cam_obj["studio_focus_point"] = list(focus)
    cam_obj["studio_margin"] = margin
    cam_obj["studio_fstop"] = float(cam_data.dof.aperture_fstop) if dof else 0.0

    fstop_text = (f"f/{cam_data.dof.aperture_fstop:.1f} (auto blur {max_blur_px:g} px)"
                  if auto else f"f/{cam_data.dof.aperture_fstop:.1f} (fixed)")
    print(
        f"[studio] camera: {framing!r} az {az:g} deg / el {el:g} deg | "
        f"{lens_mm:g} mm | fit distance {distance*1000:.1f} mm | "
        f"margin {margin*100:g}% | {fstop_text if dof else 'no DOF'}",
        flush=True,
    )
    return cam_obj


def camera_on_azimuth(camera, center=(0.0, 0.0, 0.0), bbox_size=0.10,
                      azimuth_deg=None, elevation_deg=None, lens=None,
                      margin=None, refit=True, rebuild_dof=False, max_blur_px=2.5):
    """Turntable helper: keep the framing, rotate the camera to a new azimuth.

    The STUDIO camera (or any camera) is moved around -Z over the board and
    re-fitted, so a sequence of azimuths (0, 45, 90 ...) yields consistent
    turntable frames.  Rebuild the lights afterwards with `setup_lights()` so the
    key/rim follow the camera.  Returns the camera.
    """
    scene = _scene()
    cam = camera or scene.camera
    if cam is None:
        raise RuntimeError("[studio] camera_on_azimuth: no camera in scene")
    center = _center(center)
    dims = _dims(bbox_size)
    if margin is None:
        margin = float(cam.get("studio_margin", 0.09))
    if azimuth_deg is not None:
        cam["studio_azimuth_deg"] = float(azimuth_deg)
    if elevation_deg is not None:
        cam["studio_elevation_deg"] = float(elevation_deg)
    if lens is not None:
        cam.data.lens = float(lens)
        cam["studio_lens_mm"] = float(lens)
    az = float(cam.get("studio_azimuth_deg", -32.0))
    el = float(cam.get("studio_elevation_deg", 31.0))

    direction = _orbit_direction(az, el)
    if refit:
        distance = _fit_distance(cam, center, dims, direction, margin=margin)
        cam.location = center + direction * distance
    else:
        distance = (cam.location - center).length
        cam.location = center + direction * distance
    _aim(cam, center)
    bpy.context.view_layer.update()

    if cam.data.dof.use_dof:
        focus = (Vector(tuple(cam["studio_focus_point"]))
                 if cam.get("studio_focus_point") else center)
        cam.data.dof.focus_distance = (cam.location - focus).length
        if rebuild_dof or cam.get("studio_auto_blur_px"):
            n = _auto_fstop(cam, focus, dims,
                            max_blur_px=float(cam.get("studio_auto_blur_px", max_blur_px)))
            cam.data.dof.aperture_fstop = n
            cam["studio_fstop"] = n
        cam["studio_azimuth_deg"] = az
        cam["studio_elevation_deg"] = el
    return cam


# --------------------------------------------------------------------------- #
# 5. floor / seamless backdrop
# --------------------------------------------------------------------------- #

_FLOOR_STYLES = {
    # near colour, far colour, backdrop glow (linear RGB), roughness, specular
    "shadow": dict(near=(0.030, 0.031, 0.033), far=(0.008, 0.008, 0.009),
                   glow=(0.017, 0.017, 0.019), roughness=0.68, specular=0.12),
    "glossy": dict(near=(0.014, 0.014, 0.016), far=(0.005, 0.005, 0.006),
                   glow=(0.015, 0.016, 0.018), roughness=0.22, specular=0.30),
    "matte": dict(near=(0.030, 0.030, 0.032), far=(0.009, 0.009, 0.010),
                  glow=(0.019, 0.019, 0.021), roughness=0.90, specular=0.08),
    "black": dict(near=(0.006, 0.006, 0.007), far=(0.002, 0.002, 0.002),
                  glow=(0.005, 0.005, 0.006), roughness=0.40, specular=0.30),
    "light": dict(near=(0.44, 0.44, 0.45), far=(0.24, 0.24, 0.25),
                  glow=(0.22, 0.22, 0.23), roughness=0.62, specular=0.25),
}


def _floor_material(name, preset, radius_color, radius_glow0, radius_glow1):
    """Seamless radial floor: lit diffuse near the board, glowing backdrop far away.

    The emission ramp takes over where the lights have fallen off, so the floor
    always blends into a controlled backdrop tone instead of going pure black.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (620, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (320, 0)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    coord.location = (-820, 0)
    length = nt.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    length.location = (-620, 0)
    # distance (m) -> colour gradient
    map_color = nt.nodes.new("ShaderNodeMapRange")
    map_color.location = (-440, 120)
    map_color.inputs["From Min"].default_value = 0.0
    map_color.inputs["From Max"].default_value = float(radius_color)
    map_color.inputs["To Min"].default_value = 0.0
    map_color.inputs["To Max"].default_value = 1.0
    map_color.clamp = True
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.location = (-220, 120)
    ramp.color_ramp.interpolation = "EASE"
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (*preset["near"], 1.0)
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (*preset["far"], 1.0)
    # distance (m) -> backdrop emission strength
    map_glow = nt.nodes.new("ShaderNodeMapRange")
    map_glow.location = (-440, -160)
    map_glow.inputs["From Min"].default_value = float(radius_glow0)
    map_glow.inputs["From Max"].default_value = float(radius_glow1)
    map_glow.inputs["To Min"].default_value = 0.0
    map_glow.inputs["To Max"].default_value = 1.0
    map_glow.clamp = True

    links = nt.links
    links.new(coord.outputs["Object"], length.inputs["Vector"])
    links.new(length.outputs["Value"], map_color.inputs["Value"])
    links.new(map_color.outputs["Result"], ramp.inputs["Fac"])
    links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(length.outputs["Value"], map_glow.inputs["Value"])
    links.new(map_glow.outputs["Result"], bsdf.inputs["Emission Strength"])
    bsdf.inputs["Emission Color"].default_value = (*preset["glow"], 1.0)
    bsdf.inputs["Metallic"].default_value = 0.0
    bsdf.inputs["Roughness"].default_value = float(preset["roughness"])
    _try_set(bsdf.inputs["Specular IOR Level"], "default_value", float(preset["specular"]))
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _plane_mesh(name, size_x, size_y):
    mesh = bpy.data.meshes.new(name)
    verts = [
        (-0.5 * size_x, -0.5 * size_y, 0.0),
        (0.5 * size_x, -0.5 * size_y, 0.0),
        (0.5 * size_x, 0.5 * size_y, 0.0),
        (-0.5 * size_x, 0.5 * size_y, 0.0),
    ]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    return mesh


def add_floor(center=(0.0, 0.0, 0.0), bbox_size=0.10, style="shadow",
             contact_shadow=False, shadow_catcher=False, size_factor=40.0,
             gap=0.00005, verbose=True):
    """Seamless studio floor that receives soft shadows (replaces a previous one).

    Parameters
    ----------
    style : "shadow" (default), "glossy", "matte", "black", "light", "none".
        "none" adds nothing (useful together with setup_world("transparent")).
    contact_shadow : add a very soft contact darkening under the board.
    shadow_catcher : make the floor a Cycles shadow catcher (invisible except for
        its shadow); combine with setup_world("transparent").
    size_factor : floor size as a multiple of the board's long dimension.
    gap : distance below the board's bottom face (metres).

    Returns the floor object (or None).
    """
    if style == "none":
        return None
    preset = _FLOOR_STYLES.get(style, _FLOOR_STYLES["shadow"])
    center = _center(center)
    dims = _dims(bbox_size)
    size_ref = _max_dim(dims)
    floor_size = size_ref * float(size_factor)
    floor_z = center.z - dims.z * 0.5 - float(gap)

    _remove_studio_objects("STUDIO_floor", "STUDIO_contact_shadow")

    mesh = _plane_mesh("STUDIO_floor_mesh", floor_size, floor_size)
    obj = _new_object("STUDIO_floor", mesh)
    obj.location = (center.x, center.y, floor_z)
    obj["studio_floor"] = style

    mat = _floor_material(
        "STUDIO_floor_mat", preset,
        radius_color=2.2 * size_ref,
        radius_glow0=0.9 * size_ref,
        radius_glow1=2.2 * size_ref,
    )
    obj.data.materials.append(mat)

    if shadow_catcher:
        try:
            obj.is_shadow_catcher = True
        except Exception:  # noqa: BLE001
            pass

    if contact_shadow:
        _add_contact_shadow(center, dims, floor_z)

    if verbose:
        print(
            f"[studio] floor: {style!r} seamless backdrop, {floor_size*100:.0f} cm, "
            f"z={floor_z*1000:+.3f} mm"
            f"{', shadow catcher' if shadow_catcher else ''}"
            f"{', contact shadow' if contact_shadow else ''}",
            flush=True,
        )
    return obj


def _add_contact_shadow(center, dims, floor_z):
    """Soft elliptical darkening directly under the board edge."""
    mesh = _plane_mesh("STUDIO_contact_mesh", dims.x * 1.55, dims.y * 1.55)
    obj = _new_object("STUDIO_contact_shadow", mesh)
    obj.location = (center.x, center.y, floor_z + 0.00002)

    mat = bpy.data.materials.new("STUDIO_contact_mat")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    coord = nt.nodes.new("ShaderNodeTexCoord")
    length = nt.nodes.new("ShaderNodeVectorMath")
    length.operation = "LENGTH"
    maprange = nt.nodes.new("ShaderNodeMapRange")
    maprange.inputs["From Min"].default_value = 0.20
    maprange.inputs["From Max"].default_value = 0.50
    maprange.inputs["To Min"].default_value = 0.55   # alpha at the contact core
    maprange.inputs["To Max"].default_value = 0.0
    maprange.clamp = True
    links = nt.links
    links.new(coord.outputs["Object"], length.inputs["Vector"])
    links.new(length.outputs["Value"], maprange.inputs["Value"])
    links.new(maprange.outputs["Result"], bsdf.inputs["Alpha"])
    bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)
    bsdf.inputs["Roughness"].default_value = 1.0
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    obj.data.materials.append(mat)
    obj.visible_shadow = False
    return obj


# --------------------------------------------------------------------------- #
# 6. rendering
# --------------------------------------------------------------------------- #

def render_to(path, write_still=True, verbose=True):
    """Render the current scene and save a PNG to `path`; returns timing info.

    Recomputes the auto f-stop for the current output resolution first.
    """
    sc = _scene()
    if sc.camera is None:
        raise RuntimeError("[studio] render_to: scene.camera is not set (call setup_camera first)")
    _refresh_auto_fstop()
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    sc.render.filepath = path
    t0 = time.perf_counter()
    bpy.ops.render.render(write_still=bool(write_still))
    elapsed = time.perf_counter() - t0
    info = {
        "path": path,
        "seconds": elapsed,
        "resolution": (sc.render.resolution_x, sc.render.resolution_y),
        "samples": sc.cycles.samples if sc.render.engine == "CYCLES" else None,
        "device": sc.cycles.device if sc.render.engine == "CYCLES" else "GPU",
    }
    if verbose:
        print(
            f"[studio] rendered {info['resolution'][0]}x{info['resolution'][1]} "
            f"{info['samples']}spp in {elapsed:.2f} s -> {path}",
            flush=True,
        )
    return info


# --------------------------------------------------------------------------- #
# convenience: whole rig at once
# --------------------------------------------------------------------------- #

def setup_scene(center=(0.0, 0.0, 0.0), bbox_size=0.10, framing="three_quarter",
                world_style="studio_dark", light_style="product",
                floor_style="shadow", samples=256, resolution=(3840, 2160),
                percent=100, exposure=0.0, azimuth=None, elevation=None, lens=None,
                margin=None, device="AUTO", color_depth="16", dof=True,
                light_exposure=1.0):
    """Build the complete studio in one call. Returns a dict of the created parts."""
    center = _center(center)
    dims = _dims(bbox_size)
    render = setup_render(samples=samples, resolution=resolution, percent=percent,
                          device=device, exposure=exposure, color_depth=color_depth)
    world = setup_world(world_style)
    camera = setup_camera(center, dims, framing=framing, lens=lens, margin=margin,
                          azimuth=azimuth, elevation=elevation, dof=dof)
    lights = setup_lights(center, dims, style=light_style, camera=camera,
                          exposure=light_exposure)
    floor = add_floor(center, dims, style=floor_style)
    return {"render": render, "world": world, "camera": camera,
            "lights": lights, "floor": floor}
