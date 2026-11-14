"""Minimal, dependency-light GLB (glTF 2.0 binary) reader.

Only what we need for CAD-derived meshes:
  * node hierarchy + world transforms
  * meshes / primitives -> positions, normals, indices, material index
  * materials -> name + baseColorFactor (linear)

Coordinates are returned in the file's own CAD space (this GLB comes from
OpenCASCADE and keeps Z-up millimetres converted to metres).
"""

from __future__ import annotations

import json
import struct

import numpy as np

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

_COMP = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class Mesh:
    __slots__ = ("name", "primitives")

    def __init__(self, name):
        self.name = name
        self.primitives = []  # list[Primitive]


class Primitive:
    __slots__ = ("positions", "normals", "triangles", "material")

    def __init__(self, positions, normals, triangles, material):
        self.positions = positions
        self.normals = normals
        self.triangles = triangles
        self.material = material


class Instance:
    __slots__ = ("name", "mesh_index", "matrix")

    def __init__(self, name, mesh_index, matrix):
        self.name = name
        self.mesh_index = mesh_index
        self.matrix = matrix


class Material:
    __slots__ = ("name", "base_color", "metallic", "roughness")

    def __init__(self, name, base_color, metallic, roughness):
        self.name = name
        self.base_color = base_color
        self.metallic = metallic
        self.roughness = roughness


class Scene:
    def __init__(self):
        self.meshes: list[Mesh] = []
        self.materials: list[Material] = []
        self.instances: list[Instance] = []


def _read_chunks(path):
    with open(path, "rb") as fh:
        magic, _version, _length = struct.unpack("<III", fh.read(12))
        if magic != GLB_MAGIC:
            raise ValueError(f"not a GLB file: {path}")
        js = None
        bin_chunk = b""
        while True:
            head = fh.read(8)
            if len(head) < 8:
                break
            clen, ctype = struct.unpack("<II", head)
            data = fh.read(clen)
            if ctype == CHUNK_JSON:
                js = json.loads(data.decode("utf-8"))
            elif ctype == CHUNK_BIN:
                bin_chunk = data
    if js is None:
        raise ValueError("GLB without JSON chunk")
    return js, bin_chunk


def _accessor(js, bin_chunk, index):
    acc = js["accessors"][index]
    view = js["bufferViews"][acc["bufferView"]]
    dtype = np.dtype(_COMP[acc["componentType"]])
    ncomp = _NCOMP[acc["type"]]
    count = acc["count"]
    offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    itemsize = dtype.itemsize * ncomp
    stride = view.get("byteStride") or itemsize
    if stride == itemsize:
        arr = np.frombuffer(bin_chunk, dtype=dtype, count=count * ncomp, offset=offset)
        return arr.reshape(count, ncomp)
    out = np.empty((count, ncomp), dtype=dtype)
    for i in range(count):
        out[i] = np.frombuffer(bin_chunk, dtype=dtype, count=ncomp, offset=offset + i * stride)
    return out


def _local_matrix(node):
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
    mat = np.eye(4, dtype=np.float64)
    if "scale" in node:
        mat[:3, :3] = np.diag(node["scale"])
    if "rotation" in node:
        x, y, z, w = node["rotation"]
        rot = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        mat[:3, :3] = mat[:3, :3] @ rot
    if "translation" in node:
        mat[:3, 3] = node["translation"]
    return mat


def load(path) -> Scene:
    js, bin_chunk = _read_chunks(path)
    scene = Scene()

    for gltf_mat in js.get("materials", []):
        pbr = gltf_mat.get("pbrMetallicRoughness", {})
        color = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        scene.materials.append(
            Material(
                name=gltf_mat.get("name", ""),
                base_color=(float(color[0]), float(color[1]), float(color[2])),
                metallic=float(pbr.get("metallicFactor", 1.0)),
                roughness=float(pbr.get("roughnessFactor", 1.0)),
            )
        )

    for gltf_mesh in js.get("meshes", []):
        mesh = Mesh(gltf_mesh.get("name", ""))
        for prim in gltf_mesh.get("primitives", []):
            attrs = prim["attributes"]
            if "POSITION" not in attrs:
                continue
            positions = _accessor(js, bin_chunk, attrs["POSITION"]).astype(np.float64)
            normals = None
            if "NORMAL" in attrs:
                normals = _accessor(js, bin_chunk, attrs["NORMAL"]).astype(np.float64)
            if "indices" in prim:
                idx = _accessor(js, bin_chunk, prim["indices"]).reshape(-1)
            else:
                idx = np.arange(len(positions), dtype=np.int64)
            triangles = idx[: (len(idx) // 3) * 3].reshape(-1, 3)
            mesh.primitives.append(Primitive(positions, normals, triangles, prim.get("material", -1)))
        scene.meshes.append(mesh)

    parents = {}
    for ni, node in enumerate(js["nodes"]):
        for child in node.get("children", []):
            parents[child] = ni

    def world(node_index):
        mat = np.eye(4, dtype=np.float64)
        cursor = node_index
        while cursor is not None:
            mat = _local_matrix(js["nodes"][cursor]) @ mat
            cursor = parents.get(cursor)
        return mat

    for ni, node in enumerate(js["nodes"]):
        if "mesh" not in node:
            continue
        scene.instances.append(
            Instance(name=node.get("name", f"node_{ni}"), mesh_index=node["mesh"], matrix=world(ni))
        )

    return scene
