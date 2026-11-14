"""Print the footprints and STEP colour sets of a board, to extend materials.py.

Every new board introduces a few footprints that FOOTPRINT_MAP does not know
yet.  This dumps exactly the information needed to add them:

    python recon_footprints.py --glb <render>/data/board.glb [--min-tris 0]

Read the output as: "footprint X uses colours A/B/C" -> add
    "X": {"#A": "<palette key>", "#B": ...}
to FOOTPRINT_MAP in scripts/materials.py.  Also prints the assembly bbox, which
is the quickest sanity check that the GLB is the board you think it is.
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import glb_reader  # noqa: E402


def srgb_hex(linear):
    out = []
    for c in linear:
        c = max(0.0, min(1.0, c))
        s = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
        out.append(int(round(s * 255)))
    return "#%02X%02X%02X" % tuple(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--min-tris", type=int, default=0)
    args = ap.parse_args()

    scene = glb_reader.load(args.glb)
    lo = np.full(3, 1e18)
    hi = -lo
    for inst in scene.instances:
        for prim in scene.meshes[inst.mesh_index].primitives:
            w = np.c_[prim.positions, np.ones(len(prim.positions))] @ inst.matrix.T
            lo = np.minimum(lo, w[:, :3].min(0))
            hi = np.maximum(hi, w[:, :3].max(0))

    print(f"parts={len(scene.instances)} meshes={len(scene.meshes)} materials={len(scene.materials)}")
    print(f"assembly bbox (mm): {np.round((hi - lo) * 1000, 2)}")

    groups = collections.defaultdict(list)
    for inst in scene.instances:
        mesh = scene.meshes[inst.mesh_index]
        footprint = mesh.name.split("~")[0]
        per = collections.Counter()
        for prim in mesh.primitives:
            per[srgb_hex(scene.materials[prim.material].base_color)] += len(prim.triangles)
        groups[footprint].append((inst.name.replace("->0", ""), dict(per)))

    print(f"\nfootprints: {len(groups)}")
    for footprint, entries in sorted(groups.items()):
        names = ", ".join(n for n, _ in entries)
        print(f"== {footprint}  ({len(entries)}: {names[:70]})")
        colours = entries[0][1]
        for hex_color, tris in sorted(colours.items(), key=lambda kv: -kv[1]):
            if tris < args.min_tris:
                continue
            print(f"     {hex_color}  tris={tris}")


if __name__ == "__main__":
    main()
