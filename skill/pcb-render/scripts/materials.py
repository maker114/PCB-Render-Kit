"""Semantic PBR material library for the PCB render.

The STEP/GLB colours are flat per-face RGB values (EasyEDA's generic palette).
They give us the *colour separation* (which face belongs to a body, a lead, a
PCB side, ...) but they are far from photoreal.  This module keeps the colour
coding and re-expresses it as physically plausible materials:

  * gold / tin plating  -> metallic, low roughness
  * MLCC ceramic bodies -> warm off-white dielectric with a weak clear coat
  * IC / resistor epoxy -> near-black, semi-glossy
  * connectors/switches -> moulded plastic
  * solder mask         -> glossy deep blue, with the DXF artwork injected:
                           copper traces below the mask (subtle tone shift),
                           pad openings (bare ENIG gold) and the silkscreen.
"""

from __future__ import annotations

import os

import bpy


def srgb(hex_str: str):
    """'#RRGGBB' -> linear RGBA tuple (Blender sockets are linear)."""
    hex_str = hex_str.lstrip("#")
    out = []
    for i in range(0, 6, 2):
        c = int(hex_str[i : i + 2], 16) / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return (out[0], out[1], out[2], 1.0)


# --------------------------------------------------------------------------- palette
# Anchor colours picked from the customer's 2D board preview so the render reads
# as "the same board": mask #0060B8-ish, gold #E0B848-ish.
PALETTE = {
    "gold":            dict(color="#EFCB64", metallic=1.0, roughness=0.22),
    "cap_term":        dict(color="#A98B4E", metallic=0.25, roughness=0.72),
    "gold_dark":       dict(color="#B98F33", metallic=1.0, roughness=0.26),
    "tin":             dict(color="#C6CBD1", metallic=1.0, roughness=0.28),
    "tin_dull":        dict(color="#AEB3B9", metallic=1.0, roughness=0.40),
    "copper":          dict(color="#B87333", metallic=1.0, roughness=0.35),
    "ceramic":         dict(color="#C9B79A", metallic=0.0, roughness=0.32, coat=0.55),
    "ceramic_light":   dict(color="#DFD7C6", metallic=0.0, roughness=0.28, coat=0.5),
    "epoxy_black":     dict(color="#0D0D0E", metallic=0.0, roughness=0.36, coat=0.2),
    "epoxy_darkgray":  dict(color="#232326", metallic=0.0, roughness=0.40),
    "epoxy_midgray":   dict(color="#484850", metallic=0.0, roughness=0.44),
    "plastic_white":   dict(color="#E6E2DA", metallic=0.0, roughness=0.44),
    "plastic_beige":   dict(color="#D9D2C2", metallic=0.0, roughness=0.44),
    "plastic_dark":    dict(color="#191A1C", metallic=0.0, roughness=0.38),
    "plastic_blue":    dict(color="#1B6FE0", metallic=0.0, roughness=0.35, coat=0.4),
    "shield_metal":    dict(color="#B5B2AC", metallic=0.9, roughness=0.42),
    "inductor_body":   dict(color="#6E6862", metallic=0.0, roughness=0.50),
    "mark_white":      dict(color="#F2F0EA", metallic=0.0, roughness=0.42),
    "led_lens_blue":   dict(color="#C9D4F0", metallic=0.0, roughness=0.13, coat=0.8),
    "led_red":         dict(color="#C41F1F", metallic=0.0, roughness=0.15, coat=0.7,
                            emit="#FF2A1A", emit_strength=2.2),
    "led_yellow":      dict(color="#E6C21F", metallic=0.0, roughness=0.15, coat=0.7,
                            emit="#FFC81E", emit_strength=2.0),
    "led_green":       dict(color="#7BE07B", metallic=0.0, roughness=0.15, coat=0.7,
                            emit="#66FF88", emit_strength=1.6),
    "fr4_edge":        dict(color="#C9B87C", metallic=0.0, roughness=0.60),
    "silk_white":      dict(color="#F4F2EC", metallic=0.0, roughness=0.50),
}

# ------------------------------------------------------------------- classification
# footprint (mesh name before the first '~')  ->  {step_hex: palette key}
FOOTPRINT_MAP = {
    "C0402": {"#977600": "cap_term", "#DADADA": "ceramic", "#FFFFFF": "ceramic_light"},
    "C0603": {"#977600": "cap_term", "#DADADA": "ceramic", "#FFFFFF": "ceramic_light"},
    "C0805": {"#977600": "cap_term", "#DADADA": "ceramic", "#FFFFFF": "ceramic_light"},
    "R0603": {"#000000": "epoxy_black", "#C0C0C0": "tin", "#404040": "epoxy_black",
              "#FFFFFF": "mark_white"},
    "SOT-23_L2.9-W1.3-P1.90-LS2.4-BR": {
        "#404040": "epoxy_darkgray", "#C4C4C4": "tin", "#FFFFFF": "mark_white",
        "#A49D99": "epoxy_midgray"},
    "SOT-23-5_L3.0-W1.7-P0.95-LS2.8-BL": {
        "#404040": "epoxy_darkgray", "#C4C4C4": "tin", "#FFFFFF": "mark_white",
        "#A49D99": "epoxy_midgray"},
    "SOT-23-6_L2.9-W1.6-P0.95-LS2.8-BL": {
        "#404040": "epoxy_darkgray", "#C4C4C4": "tin", "#FFFFFF": "mark_white",
        "#A49D99": "epoxy_midgray"},
    "SOT-363-6_L2.2-W1.3-P0.65-LS2.1-BL": {
        "#4C4C4C": "epoxy_darkgray", "#AAAAAA": "tin", "#FFFFFF": "mark_white",
        "#7F7F7F": "epoxy_midgray"},
    "SOIC-8_L5.0-W4.0-P1.27-LS6.0-BL": {
        "#393939": "epoxy_black", "#D9D9D9": "tin", "#707070": "epoxy_midgray"},
    "ESSOP-10_L4.9-W3.9-P1.0-LS6.0-TL-EP": {
        "#4C4C4C": "epoxy_darkgray", "#AAAAAA": "tin", "#FFFFFF": "mark_white",
        "#7F7F7F": "epoxy_midgray"},
    "WSON-6_L2.0-W2.0-P0.65-TL-EP": {
        "#404040": "epoxy_darkgray", "#FFFFFF": "mark_white", "#AAAAAA": "tin"},
    "TQFN-16_L3.0-W3.0-P0.50-BL-EP1.7": {
        "#404040": "epoxy_darkgray", "#FFFFFF": "mark_white", "#FFFF00": "gold",
        "#C0C0C0": "tin"},
    "PDFN3333-8_L3.1-W3.2-P0.65-LS3.4-BL_1": {
        "#404040": "epoxy_darkgray", "#C0C0C0": "tin", "#FFFFFF": "mark_white",
        "#FFFF00": "gold"},
    "IND-SMD_L7.0-W6.6_AAPS0630": {
        "#808080": "inductor_body", "#FFFFFF": "mark_white", "#000000": "plastic_dark"},
    "LED_0603": {"#D4AB21": "gold", "#FF0000": "led_red", "#FFFFFF": "led_lens_blue",
                 "#CBD2EF": "led_lens_blue", "#000000": "plastic_dark"},
    "LED-SMD_4P-L1.6-W1.5-TL-1": {
        "#FFFFFF": "plastic_white", "#FFFF00": "led_yellow", "#80FF80": "led_green",
        "#C0FFC0": "led_green", "#404040": "epoxy_midgray", "#000000": "plastic_dark"},
    "CONN-TH_XH-4AW": {"#FFFFFF": "plastic_white", "#AAAAAA": "tin", "#000000": "plastic_dark"},
    "SW-SMD_L3.9-W3.0-P4.45": {
        "#C7C2BD": "plastic_beige", "#D7D0C0": "plastic_beige", "#1D1D1D": "tin_dull",
        "#000000": "plastic_dark"},
    "SW-SMD_TS1102-TD35HBM": {
        "#AAAAAA": "tin", "#D7D0C0": "plastic_beige", "#4C4C4C": "epoxy_darkgray",
        "#000000": "plastic_dark", "#1D1D1D": "tin_dull"},
    "USB-C-SMD_TYPE-C-6PIN-2MD-073": {
        "#AAAAAA": "tin", "#FFFFFF": "plastic_white", "#4C4C4C": "epoxy_darkgray",
        "#D3AA21": "gold", "#000000": "plastic_dark"},
    "FPC-SMD_P0.50-8P_FGS-XJ-H2.0": {
        "#F7F7F7": "plastic_white", "#A59E96": "plastic_beige", "#282828": "epoxy_darkgray",
        "#000000": "plastic_dark", "#CAD1EE": "led_lens_blue"},
    "WIFIM-SMD_ESP-WROOM-32": {
        "#A59E96": "shield_metal", "#1A1A1A": "epoxy_black", "#A58400": "gold",
        "#000000": "plastic_dark", "#FFFFFF": "mark_white"},
    # ---- board #2: STM32F103C8T6 dev board (USB-C / CH343P / MAX485) ----
    "HDR-TH_20P-P2.54-V-M-1": {"#F2C22E": "gold", "#404040": "plastic_dark",
                               "#000000": "plastic_dark", "#FFFFFF": "mark_white"},
    "HDR-TH_4P-P2.54-V-M": {"#262626": "plastic_dark", "#C0C0C0": "tin",
                            "#FFFFFF": "mark_white"},
    "LQFP-48_L7.0-W7.0-P0.50-LS9.0-BL": {"#C4C4C4": "tin", "#A49D99": "plastic_beige",
                                         "#404040": "epoxy_darkgray", "#FFFFFF": "mark_white"},
    "SW-SMD_L6.0-W3.3-LS8.0": {"#FFFBF0": "plastic_white", "#C0C0C0": "tin",
                               "#000000": "plastic_dark"},
    "OSC-SMD_4P-L3.2-W2.5-BL": {"#393939": "tin_dull", "#404040": "tin_dull",
                                "#FFFF00": "gold", "#FFFFFF": "mark_white",
                                "#000000": "plastic_dark"},
    "FC-135R_L3.2-W1.5": {"#8282A0": "tin_dull", "#E6C8AF": "plastic_beige",
                          "#D9D9D9": "tin", "#FFFFFF": "mark_white", "#000000": "plastic_dark"},
    # ---- board #3: ESP32 + ETA9741 battery/power board ----
    "HDR-TH_2P-P2.54-V-M-1": {"#F2C22E": "gold", "#404040": "plastic_dark",
                              "#000000": "plastic_dark", "#FFFFFF": "mark_white"},
    "SOP-16_L10.0-W3.9-P1.27-LS6.0-BL": {"#C4C4C4": "tin", "#A49D99": "plastic_beige",
                                         "#404040": "epoxy_darkgray", "#FFFFFF": "mark_white"},
    "SOT-23-3_L2.9-W1.3-P1.90-LS2.4-BR": {"#404040": "epoxy_darkgray", "#C4C4C4": "tin",
                                          "#FFFFFF": "mark_white", "#A49D99": "epoxy_midgray"},
    "SW-SMD_SK-3296S-01-L2": {"#FFFFFF": "plastic_white", "#323232": "plastic_dark",
                              "#000000": "plastic_dark"},
    "SW-SMD_TS-1101VS-C-A-B": {"#C0C0C0": "tin", "#1776FF": "plastic_blue",
                               "#FFFBF0": "plastic_white", "#000000": "plastic_dark"},
    "IND-SMD_L4.4-W4.2": {"#DADADA": "inductor_body", "#A5A5A5": "tin_dull",
                          "#404040": "epoxy_darkgray", "#000000": "plastic_dark"},
}

# global fallback so no STEP colour is ever left unmapped
HEX_FALLBACK = {
    "#977600": "gold", "#A58400": "gold", "#D4AB21": "gold", "#D3AA21": "gold",
    "#C0C0C0": "tin", "#C4C4C4": "tin", "#AAAAAA": "tin", "#D9D9D9": "tin",
    "#DADADA": "ceramic", "#FFFFFF": "mark_white", "#F7F7F7": "plastic_white",
    "#000000": "epoxy_black", "#1A1A1A": "epoxy_black", "#1D1D1D": "epoxy_black",
    "#282828": "epoxy_darkgray", "#393939": "epoxy_black", "#404040": "epoxy_darkgray",
    "#4C4C4C": "epoxy_darkgray", "#707070": "epoxy_midgray", "#7F7F7F": "epoxy_midgray",
    "#808080": "inductor_body", "#A49D99": "plastic_beige", "#A59E96": "plastic_beige",
    "#C7C2BD": "plastic_beige", "#D7D0C0": "plastic_beige",
    "#FFFF00": "led_yellow", "#FF0000": "led_red", "#80FF80": "led_green",
    "#C0FFC0": "led_green", "#CBD2EF": "led_lens_blue", "#CAD1EE": "led_lens_blue",
    "#EFEFA6": "fr4_edge", "#356A9E": "silk_white",
    "#008DBF": "gold", "#262626": "plastic_dark", "#8282A0": "tin_dull",
    "#00FF00": "led_green", "#1776FF": "plastic_blue", "#323232": "plastic_dark",
    "#A5A5A5": "tin_dull", "#00FFFF": "tin_dull",
    "#E6C8AF": "plastic_beige", "#F2C22E": "gold", "#FFFBF0": "plastic_white",
}


def classify(footprint: str, hex_color: str) -> str:
    per_fp = FOOTPRINT_MAP.get(footprint, {})
    if hex_color in per_fp:
        return per_fp[hex_color]
    if hex_color in HEX_FALLBACK:
        return HEX_FALLBACK[hex_color]
    return "plastic_beige"


# --------------------------------------------------------------------- material build
def _new(name):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (700, 0)
    return mat, nt, out


def _principled(nt, out, **kw):
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    if "color" in kw:
        bsdf.inputs["Base Color"].default_value = srgb(kw["color"])
    bsdf.inputs["Metallic"].default_value = kw.get("metallic", 0.0)
    bsdf.inputs["Roughness"].default_value = kw.get("roughness", 0.5)
    if "coat" in kw and "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = kw["coat"]
        if "Coat Roughness" in bsdf.inputs:
            bsdf.inputs["Coat Roughness"].default_value = 0.07
    if "emit" in kw:
        if "Emission Color" in bsdf.inputs:
            bsdf.inputs["Emission Color"].default_value = srgb(kw["emit"])
            bsdf.inputs["Emission Strength"].default_value = kw.get("emit_strength", 1.0)
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return bsdf


def build_library():
    lib = {}
    for name, spec in PALETTE.items():
        mat, nt, out = _new(f"m_{name}")
        _principled(nt, out, **spec)
        mat.diffuse_color = srgb(spec["color"])
        lib[name] = mat
    return lib


def _img_node(nt, image, location, colorspace="Non-Color"):
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.extension = "CLIP"
    tex.interpolation = "Cubic"
    tex.location = location
    try:
        image.colorspace_settings.name = colorspace
    except Exception:  # pragma: no cover
        pass
    return tex


def _channel(nt, image_texture, location, channel=0):
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = location
    nt.links.new(image_texture.outputs["Color"], sep.inputs["Color"])
    return sep.outputs[channel]


def _set_input(nt, node, socket, value):
    """Set a node input from either a literal or another socket."""
    if hasattr(value, "node") and hasattr(value, "is_output"):
        nt.links.new(value, socket)
    else:
        socket.default_value = value


def _mix_color(nt, factor_socket, color_a, color_b, location):
    node = nt.nodes.new("ShaderNodeMix")
    node.data_type = "RGBA"
    node.location = location
    _set_input(nt, node, node.inputs[6], color_a)
    _set_input(nt, node, node.inputs[7], color_b)
    nt.links.new(factor_socket, node.inputs[0])
    return node.outputs[2]


def _mix_float(nt, factor_socket, value_a, value_b, location):
    node = nt.nodes.new("ShaderNodeMix")
    node.data_type = "FLOAT"
    node.location = location
    _set_input(nt, node, node.inputs[2], float(value_a) if not hasattr(value_a, "node") else value_a)
    _set_input(nt, node, node.inputs[3], float(value_b) if not hasattr(value_b, "node") else value_b)
    nt.links.new(factor_socket, node.inputs[0])
    return node.outputs[0]


def _board_surface(nt, out, name, art, finish=None):
    """Shared node graph for a board face with artwork.

    art : dict with keys mask / silk / copper mapping to image textures or None
    finish : optional dict with rough_fr4 / rough_cu / spec / coat overrides
    """
    finish = finish or {}
    r_fr4 = float(finish.get("rough_fr4", 0.34))
    r_cu = float(finish.get("rough_cu", 0.27))
    spec = float(finish.get("spec", 0.5))
    coat = float(finish.get("coat", 0.12))
    base_mask_fr4 = srgb(finish.get("mask_fr4", "#093C66"))  # mask over bare FR4
    base_mask_cu = srgb(finish.get("mask_cu", "#1C74C8"))    # mask over copper
    base_gold = srgb(finish.get("gold", "#F0CD68"))          # bare ENIG pad
    base_silk = srgb("#F4F2EC")

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)

    mask_tex = _img_node(nt, art["mask"], (-1700, 300)) if art["mask"] else None
    silk_tex = _img_node(nt, art["silk"], (-1700, 0)) if art["silk"] else None
    copper_tex = _img_node(nt, art["copper"], (-1700, -300)) if art["copper"] else None
    drill_tex = _img_node(nt, art["drill"], (-1700, -600)) if art.get("drill") else None

    mask_open = _channel(nt, mask_tex, (-1450, 300), 0) if mask_tex else None
    silk = _channel(nt, silk_tex, (-1450, 0), 0) if silk_tex else None
    copper = _channel(nt, copper_tex, (-1450, -300), 0) if copper_tex else None
    drill = _channel(nt, drill_tex, (-1450, -600), 0) if drill_tex else None

    def _mul(a, b, location):
        node = nt.nodes.new("ShaderNodeMath")
        node.operation = "MULTIPLY"
        node.use_clamp = True
        node.location = location
        nt.links.new(a, node.inputs[0])
        nt.links.new(b, node.inputs[1])
        return node.outputs[0]

    def _inv(a, location):
        node = nt.nodes.new("ShaderNodeMath")
        node.operation = "SUBTRACT"
        node.use_clamp = True
        node.location = location
        node.inputs[0].default_value = 1.0
        nt.links.new(a, node.inputs[1])
        return node.outputs[0]

    # --- derived masks -------------------------------------------------------
    # Gold is only where the mask is open AND copper is underneath: the mask
    # opening is deliberately a little larger than the pad (mask expansion), so
    # mask-only would give oversized pads and turn every through-hole into a
    # gold disc.
    gold_f = None
    halo_f = None
    if mask_open is not None and copper is not None:
        gold_f = _mul(mask_open, copper, (-1150, -150))
        halo_f = _mul(mask_open, _inv(copper, (-1200, -450)), (-1000, -450))

    # base colour chain: mask(fr4) -> mask(copper) -> bare-FR4 halo -> gold -> silk
    rgb = nt.nodes.new("ShaderNodeRGB")
    rgb.location = (-900, 350)
    rgb.outputs[0].default_value = base_mask_fr4
    color = rgb.outputs[0]
    if copper is not None:
        color = _mix_color(nt, copper, base_mask_fr4, base_mask_cu, (-650, 350))
    if halo_f is not None:
        color = _mix_color(nt, halo_f, color, srgb("#9C8A55"), (-450, 350))
    if gold_f is not None:
        color = _mix_color(nt, gold_f, color, base_gold, (-250, 350))
    if silk is not None:
        color = _mix_color(nt, silk, color, base_silk, (-50, 350))
    if drill is not None:
        color = _mix_color(nt, drill, color, srgb("#070708"), (150, 350))
    nt.links.new(color, bsdf.inputs["Base Color"])

    # metallic: bare ENIG pads only
    if gold_f is not None:
        nt.links.new(_mix_float(nt, gold_f, 0.0, 1.0, (-50, -50)), bsdf.inputs["Metallic"])
    else:
        bsdf.inputs["Metallic"].default_value = 0.0

    # roughness chain
    if copper is not None:
        rough = _mix_float(nt, copper, r_fr4, r_cu, (-650, -300))
    else:
        value = nt.nodes.new("ShaderNodeValue")
        value.location = (-650, -300)
        value.outputs[0].default_value = r_fr4
        rough = value.outputs[0]
    if halo_f is not None:
        rough = _mix_float(nt, halo_f, rough, 0.62, (-450, -300))
    r_gold = float(finish.get("rough_gold", 0.20))
    if gold_f is not None:
        rough = _mix_float(nt, gold_f, rough, r_gold, (-250, -300))
    if silk is not None:
        rough = _mix_float(nt, silk, rough, 0.55, (-50, -300))
    if drill is not None:
        rough = _mix_float(nt, drill, rough, 0.85, (150, -300))
    nt.links.new(rough, bsdf.inputs["Roughness"])

    if copper is not None and finish.get("mask_bump", True):
        bump = nt.nodes.new("ShaderNodeBump")
        bump.location = (60, -520)
        bump.inputs["Strength"].default_value = float(finish.get("bump_strength", 0.35))
        bump.inputs["Distance"].default_value = float(finish.get("bump_distance", 0.000025))
        nt.links.new(copper, bump.inputs["Height"])
        nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = spec
    if "Coat Weight" in bsdf.inputs:
        bsdf.inputs["Coat Weight"].default_value = coat
        bsdf.inputs["Coat Roughness"].default_value = 0.16

    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    return bsdf


def build_board_materials(tex_dir, finish=None):
    """Board top / bottom / edge materials driven by the DXF artwork textures."""
    lib = {}

    def _load(fname):
        if not tex_dir:
            return None
        path = os.path.join(tex_dir, fname)
        if not os.path.exists(path):
            return None
        return bpy.data.images.load(path, check_existing=True)

    top_art = {
        "mask": _load("mask_top.png"),
        "silk": _load("silk_top.png"),
        "copper": _load("copper_top.png"),
        "drill": _load("drill.png"),
    }
    bottom_silk = _load("silk_bottom_viewed_from_bottom.png") or _load("silk_bottom.png")
    bottom_art = {
        "mask": _load("mask_bottom.png"),
        "silk": bottom_silk,
        "copper": _load("copper_bottom.png"),
        "drill": _load("drill.png"),
    }

    mat, nt, out = _new("m_board_top")
    _board_surface(nt, out, "top", top_art, finish)
    lib["board_top"] = mat

    mat, nt, out = _new("m_board_bottom")
    _board_surface(nt, out, "bottom", bottom_art, finish)
    lib["board_bottom"] = mat

    return lib


def build_all(tex_dir=None, finish=None):
    lib = build_library()
    lib.update(build_board_materials(tex_dir, finish))
    return lib
