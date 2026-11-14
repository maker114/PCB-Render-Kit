"""
Rasterise the PCB DXF artwork layers into board-aligned PNG textures.

Design goals
------------
* Pixel-exact, full-bleed mapping: the raster covers exactly the board-outline
  bounding box, so DXF mm -> pixel is a plain linear map (no margins).
* No matplotlib: a small custom PIL rasteriser so that bulges (arcs), hatch
  fills, stroke widths and TEXT glyphs are all under direct control.  Rendered
  at SS x supersampling and box-filtered down, which gives clean anti-aliasing.
* One shared coordinate frame for every layer (the DXF frame), documented in
  align.json.

Usage:
    python build_textures.py                    # full resolution (default 4000 px)
    python build_textures.py --long-px 500 --ss 2 --only silk_top   # quick preview
    python build_textures.py --manifest         # (re)write align.json + README only
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import ezdxf
from ezdxf import path as ezpath
from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
TEXDIR = os.path.dirname(HERE)                      # ...\render\tex
PCB = os.path.dirname(os.path.dirname(TEXDIR))      # ...\PCB
DXF = os.path.join(PCB, "PCB1_DXF.dxf")

OUTLINE_LAYER = "Board-Outline-Layer"

# ---------------------------------------------------------------- configuration
DEFAULTS = dict(
    long_px=4000,      # texture width in px (board long axis) -> 40 px/mm
    ss=3,              # supersampling factor
    flatten_mm=0.004,  # curve tessellation tolerance in mm (0.16 px at 40 px/mm)
    hairline_mm=0.02,  # width used for zero-width polylines that are NOT fill outlines
    text_scale=1.0,    # global tweak for glyph size
)

FONTS = {
    # style name in the DXF -> font file(s).  First existing wins.
    "default": [r"C:\Windows\Fonts\arial.ttf"],
    "arial": [r"C:\Windows\Fonts\arial.ttf"],
    "黑体": [r"C:\Windows\Fonts\simhei.ttf"],
    "微软雅黑": [r"C:\Windows\Fonts\msyh.ttc"],
    "宋体": [r"C:\Windows\Fonts\simsun.ttc"],
}
CJK_FALLBACK = [r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\msyh.ttc"]
# cap-height / em ratio is measured per font at runtime, no magic constants needed.

# texture specification: name -> dict
SPECS = {
    "silk_top": dict(layers=["Top-Silkscreen-Layer"], text=True, color="#FFFFFF", holes=True),
    "silk_bottom": dict(layers=["Bottom-Silkscreen-Layer"], text=True, color="#FFFFFF", holes=True),
    # Same artwork, but the 24 bottom TEXT entities are mirrored in place.  The DXF
    # stores them unmirrored (readable from the TOP, extrusion +Z for every text
    # entity), whereas KiCad normally stores bottom silk mirrored, i.e. readable
    # when the board is viewed from below.  Geometry (shapes, holes) is identical
    # in both files - only the glyphs differ.
    "silk_bottom_text_mirrored": dict(layers=["Bottom-Silkscreen-Layer"], text=True,
                                      color="#FFFFFF", holes=True, mirror_text=True),
    "mask_top": dict(layers=["Top-Solder-Mask-Layer"], text=False, color="#FFFFFF"),
    "mask_bottom": dict(layers=["Bottom-Solder-Mask-Layer"], text=False, color="#FFFFFF"),
    "copper_top": dict(layers=["Top-Layer", "Multi-Layer"], text=False, color="#FFFFFF"),
    "copper_bottom": dict(layers=["Bottom-Layer", "Multi-Layer"], text=False, color="#FFFFFF"),
    "document_top": dict(layers=["Document-Layer"], text=False, color="#FFFFFF"),
    # bonus (optional for the Lead): drill holes, white = hole.  Needed if the
    # mesh has no real drill geometry, and to keep the solder-mask openings from
    # turning the inside of a through-hole into gold.
    "drill": dict(layers=["Hole-Layer"], text=False, color="#FFFFFF"),
    "outline": dict(layers=[OUTLINE_LAYER], text=False, color="#FFFFFF", silhouette=True),
}
# extra textures derived by post-processing an existing one
MIRRORED = {}

# --------------------------------------------------------------------- geometry


def entity_subpaths(entity, flatten_mm):
    """Flatten a DXF entity into a list of (points, is_closed) 2-D polylines."""
    out = []
    try:
        p = ezpath.make_path(entity)
    except Exception:
        return out
    for sub in p.sub_paths():
        pts = [(v.x, v.y) for v in sub.flattening(flatten_mm)]
        if len(pts) >= 2:
            out.append((pts, bool(sub.is_closed)))
    return out


def hatch_polygons(entity, flatten_mm):
    """Return list of polygons (closed point lists) for a HATCH entity."""
    polys = []
    paths = list(ezpath.from_hatch(entity))
    for p in paths:
        for sub in p.sub_paths():
            pts = [(v.x, v.y) for v in sub.flattening(flatten_mm)]
            if len(pts) >= 3:
                polys.append(pts)
    return polys


def hatch_polygons_raw(entity):
    """Cheap polygon (no arc tessellation) - only used for duplicate detection."""
    polys = []
    for bp in entity.paths:
        verts = getattr(bp, "vertices", None)
        if verts is None:
            continue
        polys.append([(round(float(v[0]), 3), round(float(v[1]), 3)) for v in verts])
    return polys


def polyline_key(entity):
    pts = [(round(float(p[0]), 3), round(float(p[1]), 3)) for p in entity.get_points("xy")]
    if entity.closed and pts:
        pts = pts + [pts[0]]
    return (len(pts), tuple(pts))


def board_bbox(msp, flatten_mm):
    xs, ys = [], []
    for e in msp:
        if e.dxf.layer != OUTLINE_LAYER:
            continue
        for pts, _closed in entity_subpaths(e, flatten_mm):
            for x, y in pts:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


# ------------------------------------------------------------------- rasteriser
class Raster:
    def __init__(self, bbox, long_px, ss):
        self.xmin, self.ymin, self.xmax, self.ymax = bbox
        self.bw = self.xmax - self.xmin
        self.bh = self.ymax - self.ymin
        self.ss = ss
        self.W = int(round(long_px))
        self.H = int(round(long_px * self.bh / self.bw))
        self.w = self.W * ss
        self.h = self.H * ss
        # px per mm (identical on both axes up to rounding; report both)
        self.sx = self.w / self.bw
        self.sy = self.h / self.bh

    def p(self, x, y):
        """DXF mm -> supersampled PIL pixel (row 0 = ymax = board top edge)."""
        return ((x - self.xmin) * self.sx, (self.ymax - y) * self.sy)

    def ppm(self):
        return (self.W / self.bw, self.H / self.bh)


FONT_STATE = {}


def load_font(path, size_px):
    key = (path, round(size_px, 3))
    f = FONT_STATE.get(key)
    if f is None:
        f = ImageFont.truetype(path, max(1, int(round(size_px))))
        FONT_STATE[key] = f
    return f


def cap_ratio(path):
    """cap-height / em for a font file (measured, cached)."""
    key = ("capratio", path)
    if key not in FONT_STATE:
        try:
            f = ImageFont.truetype(path, 200)
            l, t, r, b = f.getbbox("H")
            FONT_STATE[key] = (b - t) / 200.0
        except Exception:
            FONT_STATE[key] = 0.716
    return FONT_STATE[key]


def pick_font(style, text):
    cands = list(FONTS.get(style, FONTS["default"]))
    # any non-ascii char -> prefer a CJK capable font
    if any(ord(c) > 0x2000 for c in text):
        cands = CJK_FALLBACK + list(FONTS.get(style, []))
    for c in cands:
        if os.path.exists(c):
            return c
    for c in FONTS["default"] + CJK_FALLBACK:
        if os.path.exists(c):
            return c
    raise RuntimeError("no usable font found")


def rotate_anchor(size, anchor, angle_deg):
    """Where does `anchor` (sprite coords) end up after PIL rotate(expand=True)?"""
    if angle_deg % 360 == 0:
        return size, anchor
    cw, ch = size
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    # rotated bounding box -> expanded canvas size
    nw = int(math.ceil(abs(cw * ca) + abs(ch * sa)))
    nh = int(math.ceil(abs(cw * sa) + abs(ch * ca)))
    # PIL rotates the *content* counter-clockwise (visually) about the centre.
    # In image coords (y down) a visual CCW turn maps a point to:
    #   X' =  X*cos + Y*sin ;  Y' = -X*sin + Y*cos
    cx, cy = cw / 2.0, ch / 2.0
    dx, dy = anchor[0] - cx, anchor[1] - cy
    nx = dx * ca + dy * sa
    ny = -dx * sa + dy * ca
    return (nw, nh), (nw / 2.0 + nx, nh / 2.0 + ny)


def text_sprite(text, height_mm, width_factor, style, raster, text_scale=1.0):
    """Render one TEXT entity into a tight L-mode sprite.

    Returns (sprite, anchor) where anchor is the pixel position of the
    baseline-left corner inside the sprite.
    """
    font_path = pick_font(style, text)
    size_px = height_mm * raster.sy * text_scale / cap_ratio(font_path)
    font = load_font(font_path, size_px)
    asc, desc = font.getmetrics()
    try:
        adv = font.getlength(text)
    except Exception:
        adv = size_px * len(text) * 0.6
    pad = max(4, int(size_px * 0.5))
    cw = int(math.ceil(adv)) + 2 * pad + pad
    chh = asc + desc + 2 * pad
    if cw < 2 or chh < 2:
        return None, None
    canvas = Image.new("L", (cw, chh), 0)
    d = ImageDraw.Draw(canvas)
    bx, by = pad, pad + asc            # baseline-left position
    d.text((bx, by), text, font=font, fill=255, anchor="ls")
    bb = canvas.getbbox()
    if bb is None:
        return None, None
    # crop; keep 2 px safety border so rotation does not clip
    x0, y0 = max(0, bb[0] - 2), max(0, bb[1] - 2)
    x1, y1 = min(cw, bb[2] + 2), min(chh, bb[3] + 2)
    spr = canvas.crop((x0, y0, x1, y1))
    anchor = (bx - x0, by - y0)
    if abs(width_factor - 1.0) > 1e-3:
        nw = max(1, int(round(spr.width * width_factor)))
        spr = spr.resize((nw, spr.height), Image.BICUBIC)
        anchor = (anchor[0] * width_factor, anchor[1])
    return spr, anchor


def draw_text_entity(dr, img, entity, raster, text_scale=1.0, mirror=False):
    dxf = entity.dxf
    text = dxf.text
    if not text or not text.strip():
        return False
    h = float(dxf.height)                      # mm
    if h <= 0:
        return False
    wf = float(dxf.get("width", 1.0) or 1.0)
    rot = float(dxf.rotation or 0.0) % 360.0
    style = str(dxf.get("style", "default")).lower()
    spr, anchor = text_sprite(text, h, wf, style, raster, text_scale)
    if spr is None:
        return False
    if mirror:
        # mirror the glyphs about the insert point (vertical axis through it)
        spr = spr.transpose(Image.FLIP_LEFT_RIGHT)
        anchor = (spr.width - 1 - anchor[0], anchor[1])
    if rot:
        size, anchor = rotate_anchor(spr.size, anchor, rot)
        spr = spr.rotate(rot, resample=Image.BICUBIC, expand=True,
                         center=(spr.width / 2.0, spr.height / 2.0))
        # pad if rounding left the sprite 1 px smaller than predicted
        if spr.size != size:
            canvas = Image.new("L", size, 0)
            canvas.paste(spr, (0, 0))
            spr = canvas
    # DXF text rotation is CCW; alignment point = insert (halign=left,valign=bottom
    # was verified for every entity in this file, see recon).
    ins = dxf.insert
    tx, ty = raster.p(ins.x, ins.y)
    left = int(round(tx - anchor[0]))
    top = int(round(ty - anchor[1]))
    # composite with a "lighter" (max) blend so overlapping glyphs stay white
    W, H = raster.w, raster.h
    x0, y0 = max(0, left), max(0, top)
    x1, y1 = min(W, left + spr.width), min(H, top + spr.height)
    if x1 <= x0 or y1 <= y0:
        return True
    crop = spr.crop((x0 - left, y0 - top, x1 - left, y1 - top))
    region = img.crop((x0, y0, x1, y1))
    region = ImageChops.lighter(region, crop)
    img.paste(region, (x0, y0))
    return True


def stroke(dr, pts, width_px):
    """Round-capped, round-jointed polyline in supersampled pixels."""
    if len(pts) < 2:
        return
    wpx = max(1, int(round(width_px)))
    if wpx <= 1:
        dr.line(pts, fill=255, width=1)
        return
    dr.line(pts, fill=255, width=wpx, joint="curve")
    r = wpx / 2.0
    for x, y in (pts[0], pts[-1]):
        dr.ellipse([x - r, y - r, x + r, y + r], fill=255)


# ------------------------------------------------------------------ main render
def polygon_area(v):
    """Signed area (CCW positive)."""
    n = len(v)
    return 0.5 * sum(v[j][0] * v[(j + 1) % n][1] - v[(j + 1) % n][0] * v[j][1]
                     for j in range(n))


def point_in_polygon(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def mark_holes(fills):
    """Flag fills that are the hole of a larger fill on the same layer.

    KiCad's DXF export writes a filled shape with a hole as two HATCH entities,
    the inner one traversed with the opposite winding (e.g. the '12V' triangle
    icon: outer triangle + inner triangle + the bolt).  Opposite winding alone is
    NOT a reliable signal (most mask openings are CW), so a polygon is only
    treated as a hole when it is nested inside a bigger polygon that has the
    opposite winding AND contains its centroid.  Only enabled for the silkscreen
    layers, where it is visually verified against the reference render.
    """
    order = sorted(range(len(fills)), key=lambda i: -fills[i]["bboxarea"])
    for rank, i in enumerate(order):
        p = fills[i]
        for j in order[:rank]:
            q = fills[j]
            if q["bboxarea"] <= p["bboxarea"]:
                continue
            if q["is_hole"]:
                continue
            if not (q["bb"][0] <= p["bb"][0] and q["bb"][1] <= p["bb"][1]
                    and q["bb"][2] >= p["bb"][2] and q["bb"][3] >= p["bb"][3]):
                continue
            if (p["area"] > 0) == (q["area"] > 0):
                continue
            if point_in_polygon(p["centroid"], q["poly"]):
                p["is_hole"] = True
                break
    return sum(1 for f in fills if f["is_hole"])


def build_texture(name, spec, msp, raster, flatten_mm, hairline_mm, text_scale,
                  stats):
    t0 = time.time()
    img = Image.new("L", (raster.w, raster.h), 0)
    dr = ImageDraw.Draw(img)
    info = {}

    # pre-collect: (a) exact vertex keys of hatch outlines, (b) bboxes of hatch
    # fills.  KiCad's DXF export writes every filled shape TWICE - as a solid
    # HATCH and as a closed LWPOLYLINE tracing the same boundary - so a closed
    # polyline whose bbox matches a fill on the same layer is a duplicate, not
    # extra artwork.  (Drawing both inflates the shape by the stroke width; it is
    # clearly visible on the '12V' triangle icon otherwise.)
    fill_keys = set()
    hatch_bboxes = []
    for layer in spec["layers"]:
        for e in msp:
            if e.dxf.layer != layer or e.dxftype() != "HATCH":
                continue
            for poly in hatch_polygons_raw(e):
                if poly:
                    fill_keys.add((len(poly), tuple(poly)))
                    fill_keys.add((len(poly) + 1, tuple(poly + [poly[0]])))
            for poly in hatch_polygons(e, flatten_mm):
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                hatch_bboxes.append((min(xs), min(ys), max(xs), max(ys)))

    counts = Counter()
    skipped = Counter()
    fills = []      # phase 1
    strokes = []    # phase 2
    for layer in spec["layers"]:
        ents = [e for e in msp if e.dxf.layer == layer]
        for e in ents:
            t = e.dxftype()
            if t == "LWPOLYLINE":
                cw = float(e.dxf.get("const_width", 0.0) or 0.0)
                subs = entity_subpaths(e, flatten_mm)
                dup = cw <= 0.005 and polyline_key(e) in fill_keys
                if dup:
                    skipped["zero_width_fill_outline"] += 1
                    continue
                if e.closed and subs and spec.get("dedup_closed_outlines", True):
                    xs = [p[0] for sub in subs for p in sub[0]]
                    ys = [p[1] for sub in subs for p in sub[0]]
                    pb = (min(xs), min(ys), max(xs), max(ys))
                    if any(all(abs(pb[i] - hb[i]) <= 0.02 for i in range(4))
                           for hb in hatch_bboxes):
                        skipped["closed_outline_of_fill"] += 1
                        continue
                w = cw if cw > 0.005 else hairline_mm
                for pts, closed in subs:
                    if closed and len(pts) > 2:
                        pts = pts + [pts[0]]
                    strokes.append((pts, w))
                counts["polyline"] += 1
            elif t == "HATCH":
                solid = bool(e.dxf.get("solid_fill", 0))
                polys = hatch_polygons(e, flatten_mm)
                if solid:
                    for poly in polys:
                        xs = [p[0] for p in poly]
                        ys = [p[1] for p in poly]
                        bb = (min(xs), min(ys), max(xs), max(ys))
                        fills.append(dict(
                            poly=poly, bb=bb,
                            bboxarea=(bb[2] - bb[0]) * (bb[3] - bb[1]),
                            area=polygon_area(poly),
                            centroid=(sum(xs) / len(xs), sum(ys) / len(ys)),
                            is_hole=False, layer=layer))
                    counts["hatch_fill"] += 1
                else:
                    # patterned hatch (ANSI37): pattern lines are not reproduced
                    # -> boundary as a hairline, noted in the report
                    for poly in polys:
                        strokes.append((poly + [poly[0]], 0.05))
                    counts["hatch_pattern"] += 1
                    skipped["pattern_hatch_outlined"] += 1
            elif t == "TEXT":
                if not spec.get("text"):
                    skipped["text_not_requested"] += 1
                    continue
                if draw_text_entity(dr, img, e, raster, text_scale,
                                    mirror=bool(spec.get("mirror_text"))):
                    counts["text"] += 1
            else:
                skipped["type_" + t] += 1

        if spec.get("silhouette"):
            for e in ents:
                for pts, _c in entity_subpaths(e, flatten_mm):
                    if len(pts) >= 3:
                        fills.append(dict(
                            poly=pts, bb=(min(p[0] for p in pts), min(p[1] for p in pts),
                                          max(p[0] for p in pts), max(p[1] for p in pts)),
                            bboxarea=0.0, area=abs(polygon_area(pts)),
                            centroid=(0.0, 0.0), is_hole=False, layer=layer))

    # ---- phase 1: fills, big to small so that holes can erase their parent
    if spec.get("holes"):
        n_holes = mark_holes(fills)
        counts["holes"] = n_holes
    fills.sort(key=lambda f: -f["bboxarea"])
    for f in fills:
        pp = [raster.p(x, y) for x, y in f["poly"]]
        if len(pp) < 3:
            continue
        dr.polygon(pp, fill=0 if f["is_hole"] else 255)

    # ---- phase 2: strokes
    for pts, w in strokes:
        pp = [raster.p(x, y) for x, y in pts]
        stroke(dr, pp, w * raster.sx)

    # downsample (BOX == exact area average)
    small = img.resize((raster.W, raster.H), Image.BOX)
    # The coverage mask is written into R, G, B *and* A: artwork = pure white
    # (255,255,255,255), background = black and fully transparent (0,0,0,0),
    # anti-aliased edges are the same value in all four channels.  This way the
    # consumer can read either the red channel or alpha and get the same mask
    # (Blender users often read R because it survives any colour management).
    out = Image.merge("RGBA", (small, small, small, small))
    path = os.path.join(TEXDIR, name + ".png")
    out.save(path, optimize=True)
    info[name] = dict(
        file=name + ".png", width=raster.W, height=raster.H,
        entities=dict(counts), skipped=dict(skipped),
        seconds=round(time.time() - t0, 2),
    )
    print("  %-20s %5.1fs  %s  skipped=%s" % (
        name, info[name]["seconds"], dict(counts), dict(skipped)))
    return out, info[name]


# ------------------------------------------------------------------- manifest
def rel_affine(bbox, W, H):
    xmin, ymin, xmax, ymax = bbox
    return dict(
        u_of_x="u = (x - %.6f) / %.6f * %d" % (xmin, xmax - xmin, W),
        v_of_y="v_png_row = (%.6f - y) / %.6f * %d   (row 0 = top row of the PNG as displayed)"
                 % (ymax, ymax - ymin, H),
    )


ROLES = {
    "silk_top": "top silkscreen ink (lines, shapes, 137 designator TEXT entities)",
    "silk_bottom": "bottom silkscreen ink exactly as the DXF stores it; the 24 bottom "
                   "TEXT entities are unmirrored here, i.e. readable from the TOP",
    "silk_bottom_text_mirrored": "same bottom artwork but with the 24 TEXT entities "
                                 "mirrored in place, i.e. readable when the board is "
                                 "viewed from BELOW (this is how KiCad stores bottom "
                                 "silk). Identical except for the glyphs",
    "mask_top": "top solder-mask OPENINGS (where the mask is absent / copper exposed)",
    "mask_bottom": "bottom solder-mask openings, shared DXF frame",
    "copper_top": "top copper artwork = Top-Layer + Multi-Layer (through-hole pads)",
    "copper_bottom": "bottom copper artwork = Bottom-Layer + Multi-Layer",
    "document_top": "Document/assembly markings (fab layer); not needed by the Lead",
    "drill": "drill holes, white = hole (Hole-Layer). Same for both sides; optional",
    "outline": "filled board silhouette (exact rounded-rect board shape) - usable as "
               "a board-face alpha if you ever need it",
}

def write_manifest(raster, infos, extra):
    xmin, ymin, xmax, ymax = raster_bbox = extra["bbox"]
    W, H = raster.W, raster.H
    ppmx, ppmy = raster.ppm()
    bw, bh = xmax - xmin, ymax - ymin
    textures = {}
    for name, info in infos.items():
        textures[name] = dict(
            file=info["file"], width=info["width"], height=info["height"],
            px_per_mm=round(ppmx, 6), px_per_mm_y=round(ppmy, 6),
            alpha="coverage is duplicated in R,G,B and A: artwork = pure white "
                  "(255,255,255,255), background = (0,0,0,0), AA edges carry the same "
                  "value in all four channels - read R or A, both give the mask",
            color=SPECS[name]["color"],
            role=ROLES.get(name, ""),
            dxf_layers=SPECS[name]["layers"],
            entity_counts=info["entities"],
            notes=info.get("notes", ""),
        )
    align = {
        "_about": "Single source of truth for the silkscreen / solder-mask / copper "
                  "raster textures generated from DXF_PCB1_2026-10-25_AutoCAD2007.dxf. "
                  "All textures share ONE coordinate frame: the DXF modelspace frame.",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_dxf": DXF,
        "board_bbox_mm": [round(xmin, 6), round(ymin, 6), round(xmax, 6), round(ymax, 6)],
        "board_width_mm": round(bw, 6),
        "board_height_mm": round(bh, 6),
        "board_bbox_note": "geometric bbox of the Board-Outline-Layer path (8 vertices, "
                           "3 mm bulge fillets -> rounded corners). ezdxf's naive bbox is "
                           "0.127 mm larger on every side because of the 0.254 mm const_width.",
        "dxf_y_axis": "up",
        "dxf_y_axis_evidence": (
            "DXF +Y points to the TOP edge of the board, i.e. y=0 is the top edge and "
            "y=-65.0001 is the bottom edge. Verified three ways: (1) Top-Silkscreen 'CH-1' "
            "is at (83.1585, -3.81) and 'CH-4' at (83.1585, -52.4511); in the reference "
            "render 3D_PCB1_2026-10-24.png CH-1 is the top-right and CH-4 the bottom-right "
            "connector. (2) 'PD-IN' is at (1.1303, -63.8811) = bottom-left in the reference "
            "preview. (3) PickAndPlace_PCB1_2026_10_25.xlsx (all 118 parts are on the TOP "
            "side) puts TP2/TP3 at y=-5.064 and SW1..SW4 at y=-62.972; in the preview TP2/TP3 "
            "mounting holes are at the top and the four SWx tactile switches along the bottom. "
            "=> a plain y-up render (PNG row 0 = ymax) is the correct orientation."),
        "pixel_mapping": {
            "png_row_of_y": "row = (ymax - y) / (ymax - ymin) * height",
            "png_col_of_x": "col = (x - xmin) / (xmax - xmin) * width",
            "note": "Full bleed: the raster covers exactly board_bbox_mm, no margin, no "
                    "border. ppm_x and ppm_y differ by <1e-5 because width/height are "
                    "rounded to whole pixels.",
            "affine_x": "col = %.9f * x + %.9f" % (W / bw, -xmin * W / bw),
            "affine_y": "row = %.9f * y + %.9f" % (-H / bh, ymax * H / bh),
        },
        "uv_formula": {
            "blender_image_space": "Blender samples UV (0,0) at the bottom-left of the "
                                   "image as displayed and (1,1) at the top-right, with no "
                                   "flip for PNG textures.",
            "flip_v": False,
            "flip_v_explanation": "flip_v = false: the PNGs are written with row 0 = y=ymax "
                                  "(the board's top edge), which is exactly the orientation "
                                  "Blender expects, so v must NOT be flipped - use the "
                                  "formula below as-is.",
            "top_face": [
                "u = (x_mm - %.6f) / %.6f" % (xmin, bw),
                "v = (y_mm - %.6f) / %.6f" % (ymin, bh),
                "",
                "with the Lead's scene in metres (1 BU = 1 m):",
                "u = X_bu / %.7f" % (bw / 1000.0),
                "v = (Y_bu + %.7f) / %.7f" % (-ymin / 1000.0, bh / 1000.0),
                "",
                "identical to the Lead's own form: u = x_mm/100.000201, "
                "v = (y_mm + 65.00013)/65.00013",
            ],
            "bottom_face": [
                "u = (x_mm - %.6f) / %.6f      (same u as the top face!)" % (xmin, bw),
                "v = (y_mm - %.6f) / %.6f" % (ymin, bh),
                "-> a point (x,y) on the bottom surface samples the same texel as the "
                "same (x,y) on the top surface. The mirroring you see when looking at "
                "the underside is then produced by the camera, not by the UVs.",
            ],
            "bottom_face_explanation": (
                "All textures are stored in ONE frame: the DXF top-view frame. Because the "
                "bottom face is viewed from -Z, if you build its UVs from the bottom's own "
                "local axes instead of from board (x,y) mm, u comes out mirrored - then use "
                "u_bottom = 1 - u_top (equivalently set the U scale of the mapping to -1 on "
                "that face). Use ONE of the two conventions consistently for mask, copper "
                "and silk, otherwise the pads will not line up with the mask openings. "
                "If you mirror u, then silk_bottom.png is the file to use; if you do not "
                "mirror u, use silk_bottom.png too - the only real choice is whether the "
                "24 bottom designators should read correctly from BELOW "
                "(silk_bottom_text_mirrored.png) or exactly as the DXF stores them "
                "(silk_bottom.png, readable from the top)."),
            "measured_residual": (
                "verified numerically against the reference render 3D_PCB1_2026-10-24.png "
                "(see verify/report.json): the solder-mask openings cross-correlate against "
                "the reference's gold areas at an integer shift of exactly (dy,dx)=(0,0) "
                "preview pixels, NCC 0.76 -> the mask geometry is aligned to better than "
                "half a preview pixel (0.023 mm). Silk: NCC 0.41 with a best shift of "
                "(0,4) px, but that number is biased by the reference rendering silk thinner "
                "than the DXF stroke width; comparing individual designator bounding boxes "
                "in mm gives -0.07 mm (USB-IN), +0.17/-0.13 mm (CH-1), +0.13/-0.15 mm (12V), "
                "i.e. placement is right and the remainder is the TrueType-vs-KiCad-stroke-"
                "font metric difference, not a coordinate offset. Y-axis test: NCC drops "
                "from 0.41 to 0.10 for silk and from 0.76 to 0.14 for the mask when the "
                "texture is flipped vertically, which proves the y-up orientation."),
        },
        "shader_recipe": SHADER_RECIPE,
        "textures": textures,
        "coordinate_frames": extra["axis_notes"],
        "step_alignment": extra["step_alignment"],
        "y_axis_evidence_images": extra["evidence_images"],
        "rendering": {
            "rasteriser": "custom PIL rasteriser (ezdxf.path flattening + hatch fills + "
                          "TrueType text), %d px across the board (%.3f px/mm), %dx "
                          "supersampling, box filter" % (W, ppmx, extra["ss"]),
            "hairline_mm": extra["hairline_mm"],
            "flatten_mm": extra["flatten_mm"],
            "regenerate": "python scripts/build_textures.py",
        },
    }
    p = os.path.join(TEXDIR, "align.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(align, f, indent=2, ensure_ascii=False)
    print("\nwrote", p)
    return align


def main():
    global DXF
    ap = argparse.ArgumentParser()
    ap.add_argument("--long-px", type=int, default=DEFAULTS["long_px"])
    ap.add_argument("--ss", type=int, default=DEFAULTS["ss"])
    ap.add_argument("--flatten-mm", type=float, default=DEFAULTS["flatten_mm"])
    ap.add_argument("--hairline-mm", type=float, default=DEFAULTS["hairline_mm"])
    ap.add_argument("--text-scale", type=float, default=DEFAULTS["text_scale"])
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--dxf", default=None, help="override the DXF path")
    args = ap.parse_args()

    global TEXDIR
    if args.outdir:
        TEXDIR = args.outdir
    os.makedirs(TEXDIR, exist_ok=True)

    if args.dxf:                      # must happen BEFORE the DXF is read
        DXF = os.path.abspath(args.dxf)
        print("DXF:", DXF)

    t0 = time.time()
    doc = ezdxf.readfile(DXF)
    msp = doc.modelspace()
    bbox = board_bbox(msp, args.flatten_mm)
    print("board bbox mm:", tuple(round(v, 6) for v in bbox))
    raster = Raster(bbox, args.long_px, args.ss)
    print("raster:", raster.W, "x", raster.H, " ss:", args.ss,
          " render buffer:", raster.w, "x", raster.h,
          " px/mm: %.6f %.6f" % raster.ppm())

    names = list(SPECS) if not args.only else args.only
    infos = {}
    imgs = {}
    for name in names:
        if name not in SPECS:
            print("unknown texture", name)
            continue
        imgs[name], infos[name] = build_texture(
            name, SPECS[name], msp, raster, args.flatten_mm, args.hairline_mm,
            args.text_scale, {})

    for newname, src in MIRRORED.items():
        if src not in imgs:
            continue
        im = imgs[src].transpose(Image.FLIP_LEFT_RIGHT)
        im.save(os.path.join(TEXDIR, newname + ".png"), optimize=True)
        infos[newname] = dict(
            file=newname + ".png", width=raster.W, height=raster.H,
            entities=dict(infos[src]["entities"]), skipped={},
            seconds=0.0,
            notes="horizontally mirrored copy of %s" % infos[src]["file"])
        SPECS[newname] = dict(SPECS[src])
        SPECS[newname]["notes"] = "u-mirrored copy of " + src
        print("  %-20s mirrored copy of %s" % (newname, src))

    extra = dict(
        bbox=bbox, ss=args.ss, hairline_mm=args.hairline_mm,
        flatten_mm=args.flatten_mm,
        axis_notes=AXIS_NOTES,
        step_alignment=STEP_ALIGNMENT,
        evidence_images=evidence_list(),
    )
    if not args.only:
        write_manifest(raster, infos, extra)
        write_readme(raster, infos, extra)
    print("\ntotal %.1fs" % (time.time() - t0))
    return infos


AXIS_NOTES = {
    "dxf_frame": "DXF modelspace, millimetres. x to the right (0..100.0002), y up "
                 "(0 = board top edge, -65.0001 = board bottom edge).",
    "text_orientation": "All 161 TEXT entities have extrusion (0,0,+1) and "
                        "halign=left/valign=bottom, so they are drawn upright in this "
                        "frame and readable looking at the TOP of the board. Bottom-side "
                        "text is stored NON-mirrored in the same frame (this is what the "
                        "DXF contains; see below).",
    "bottom_side": "The bottom silkscreen / bottom copper / bottom mask geometry is in "
                   "the SAME frame as the top (not x-mirrored): e.g. bottom-silk 'USB-IN' "
                   "sits at (10.4496,-13.335) next to the top-side USB1 connector at "
                   "(5.091,-11.786), and the bottom-silk CH-1..CH-4 labels sit at the same "
                   "y as the top ones. Top and bottom solder-mask bboxes are also identical "
                   "in x (1.766..93.000) which only happens without a mirror.",
    "bottom_view": "Physically, silkscreen printed on the bottom face is read from below, so "
                   "it is the mirror image of what you see from the top. The DXF stores the "
                   "bottom TEXT unmirrored in the top-view frame (all extrusions +Z), i.e. "
                   "readable from the top - so the physical board's bottom labels are most "
                   "likely mirrored relative to this DXF (KiCad normally stores bottom silk "
                   "mirrored). Both variants are shipped: silk_bottom.png = exactly what the "
                   "DXF contains, silk_bottom_text_mirrored.png = only the 24 glyphs mirrored, "
                   "readable when the board is viewed from below. Geometry is identical in "
                   "both, so pad/silk alignment is unaffected by the choice.",
}

STEP_ALIGNMENT = {
    "status": "confirmed with the Lead (2026-10-25)",
    "scene": "Blender scene, Z up, board lies in the XY plane, top side = +Z. "
             "1 Blender unit = 1 metre (the STEP->GLB conversion kept CAD metres, "
             "so 100 mm = 0.1 BU). No object scale, no object rotation.",
    "bare_board_bbox_scene_mm": [0.0, -65.0, 0.0, 100.0, 0.0, 1.6005],
    "bare_board_bbox_scene_bu": [0.0, -0.065, 0.0, 0.1, 0.0, 0.0016005],
    "axis_mapping": "DXF +x -> scene +X (100.0002 mm long axis), DXF +y -> scene +Y, "
                    "same sense, not mirrored; DXF/board +Z -> scene +Z (top side).",
    "offset_dxf_to_scene_mm": [0.0, 0.0, 0.0],
    "scale": 1.0,
    "residual": "the STEP board outline is rounded to (0, -65.0) .. (100.0, 0.0) while "
                "the DXF outline is (0, -65.00013) .. (100.000201, 0.0); the 0.2 um "
                "difference is CAD rounding and is far below one texture pixel "
                "(1 px = 25 um). Use the DXF numbers for the UVs, they are the "
                "texture's own grid.",
    "uv_computation": "The Lead computes UVs from board-space MILLIMETRES, which is "
                      "independent of the object centring: "
                      "u = x_mm / 100.000201, v = (y_mm + 65.00013) / 65.00013.",
}

SHADER_RECIPE = {
    "gold_pads": "gold = mask_top  AND  copper_top  (textures as alpha masks). The "
                 "solder-mask openings are ~0.05-0.10 mm LARGER than the copper pad "
                 "(mask expansion), so using mask_top alone makes every pad slightly "
                 "too big and would also turn the 4 M3 mounting holes and the "
                 "through-hole barrels into solid gold discs. mask_top AND copper_top "
                 "reproduces the pad size seen in the reference render.",
    "mask_expansion_ring": "mask_top AND NOT copper_top = bare FR4 inside the mask "
                           "opening. In the reference render this shows as a thin "
                           "darker blue halo around every pad; optional, but it is what "
                           "makes pads read as 'exposed copper' rather than 'painted'.",
    "through_holes": "mask_top is opaque over the whole pad INCLUDING the drill hole "
                     "(the solder-mask artwork for a TH pad is a single filled circle). "
                     "If your board mesh has the real drilled holes as geometry, nothing "
                     "more is needed. Otherwise subtract drill.png (Hole-Layer, white = "
                     "hole) from the mask/gold term - this is the only way to get the "
                     "dark holes seen at CN1..CN4 and at TP1..TP4.",
    "silk": "silk_top / silk_bottom: white ink, alpha=1. Multiply by a silkscreen "
            "opacity ~0.9-1.0 and optionally a slight roughness bump.",
    "colorspace": "load every PNG as Non-Color and use the red channel (= alpha, they "
                  "are identical) as a 0..1 mask; artwork is pure white (255,255,255), "
                  "background is black and transparent, so no gamma/fringing issues.",
}


def evidence_list():
    import glob as _glob
    ver = os.path.join(TEXDIR, "verify")
    if not os.path.isdir(ver):
        return []
    return ["verify/" + os.path.basename(p) for p in
            _glob.glob(os.path.join(ver, "*.png"))]


README = """# PCB artwork textures (DXF -> raster)

Generated from `{dxf}` by `scripts/build_textures.py`; see `align.json` for the
machine-readable alignment contract and `verify/report.json` for the numeric
alignment check.

## Regenerate (one command)

```
{py} scripts/build_textures.py
```

Fast preview run (low resolution): `--long-px 500 --ss 2`.
Verification images + numbers: `python scripts/verify.py`.

## Coordinate frame

* DXF modelspace millimetres, y **up**: `y = 0` is the board's TOP edge,
  `y = -65.0001` is the bottom edge. `x = 0 .. 100.0002`.
* Every texture is full-bleed over `board_bbox_mm = {bbox}`, i.e.
  pixel (0,0) of the PNG is the board corner (xmin, **ymax**) - the top-left
  corner of the board as seen from above.
* Resolution: **{W} x {H} px = {ppmx:.4f} px/mm** ({ss}x supersampled, box filtered).
* RGBA. The coverage mask is written into **all four channels**: artwork is pure
  white `(255,255,255,255)`, background is `(0,0,0,0)`, and anti-aliased edges
  carry the same value in R, G, B and A. So the mask can be read from the red
  channel *or* from alpha - both are identical.

## UV formula (Blender)

`flip_v = false` - no v flip is needed: Blender samples v=0 at the bottom row of
the image as displayed, and row 0 of the PNG is the board's top edge, so:

```
TOP face:     u = (x_mm - {xmin:.4f}) / {bw:.4f}
              v = (y_mm - {ymin:.4f}) / {bh:.4f}

BOTTOM face:  same formula if you build the UVs from board (x,y) mm;
              if you build them from the bottom's own local axes, mirror u:
              u = 1 - (x_mm - {xmin:.4f}) / {bw:.4f}
```

The scene keeps the DXF frame (Lead confirmed: 1 BU = 1 m, no scale/rotation,
DXF +x -> scene +X, DXF +y -> scene +Y same sense, top side +Z), so in Blender
units u = X_bu/0.1000002 and v = (Y_bu + 0.0650001)/0.0650001.

Every texture is stored in that ONE frame. Use the same convention (mirror u or
not) for mask, copper and silk on the bottom face, otherwise pads will not line
up with the mask openings. The only genuine choice is whether the 24 bottom
designators should read correctly from **below** (`silk_bottom_text_mirrored.png`,
which is how KiCad stores bottom silk) or exactly as the DXF stores them
(`silk_bottom.png`, readable from the top). Geometry is identical in both.

## How to use the textures (shader recipe)

```
gold            = mask_top   AND  copper_top
bare FR4 ring   = mask_top   AND NOT copper_top     # the thin dark halo around pads
dark hole       = drill                              # if the mesh has no real drills
silk            = silk_top / silk_bottom             # white ink
```

* The solder-mask openings are **0.05-0.10 mm larger than the copper pad** (mask
  expansion). Using `mask_top` alone makes every pad slightly too big and turns
  the 4 M3 mounting holes plus every through-hole barrel into solid gold discs.
  `mask_top AND copper_top` reproduces the pad size seen in the reference render.
* `mask_top` is opaque over the whole TH pad including the drill; subtract
  `drill.png` to get the dark hole (only needed if your mesh has no real holes).
* Load every PNG as Non-Color and read the red channel / alpha as a 0..1 mask.
  The artwork is pure white (255,255,255).

## Textures

| file | role | px |
|---|---|---|
{texture_table}

## Notes / limitations

* HATCH: 1827 of 1829 hatches are SOLID and render exactly as filled polygons
  (bulged arcs included, so round/slot pads are exact). The 2 `ANSI37` patterned
  hatches (Top-Layer and Multi-Layer keepout areas) are rendered as outlines
  only - their 45-degree hatching is NOT reproduced; they are keepout markers,
  not copper.
* KiCad's DXF export writes every filled shape twice: as a solid HATCH and as a
  closed LWPOLYLINE tracing the same boundary. Such duplicate outlines are
  skipped (`closed_outline_of_fill` in `align.json`); otherwise every filled
  shape would be inflated by its stroke width (clearly visible on the '12V'
  triangle icon).
* A filled shape that has a hole is exported as two nested HATCHes with opposite
  winding (e.g. the 12V triangle: outer triangle, inner triangle, bolt). For the
  silkscreen layers the inner one is treated as a hole. This is only enabled for
  silk - on copper it cannot be distinguished from a pad inside a pour, so
  copper is drawn as the plain union of its fills.
* TEXT is rendered with TrueType fonts (Arial for the `default` style, SimHei /
  MS YaHei for the DXF styles that specify them - needed for the CJK string),
  sized so cap height = the DXF text height, with the DXF width factor. Total
  white-ink area matches the reference render to 1.3% (see verify/measure output).
* Silkscreen artwork extends up to 0.57 mm beyond the board outline (the SWx
  footprint brackets); it is clipped by the texture, which is correct - it hangs
  over the board edge.
"""


def write_readme(raster, infos, extra):
    xmin, ymin, xmax, ymax = extra["bbox"]
    bw, bh = xmax - xmin, ymax - ymin
    rows = []
    for name, info in infos.items():
        lay = ROLES.get(name, ", ".join(SPECS[name]["layers"]))
        rows.append("| `%s.png` | %s | %d x %d |" % (name, lay, info["width"], info["height"]))
    table = "\n".join(rows)
    txt = README.format(
        dxf=os.path.basename(DXF), py=sys.executable,
        bbox="[%.4f, %.4f, %.4f, %.4f]" % (xmin, ymin, xmax, ymax),
        W=raster.W, H=raster.H, ppmx=raster.ppm()[0], ss=raster.ss,
        xmin=xmin, ymin=ymin, xmax=xmax, ymax=ymax, bw=bw, bh=bh,
        texture_table=table)
    p = os.path.join(TEXDIR, "README.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write(txt)
    print("wrote", p)


if __name__ == "__main__":
    main()
