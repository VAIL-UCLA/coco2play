#!/usr/bin/env python3
"""Convert the coco_one USD robot to URDF format.

Extracts meshes from the USD file as OBJ, then generates a URDF file
with the correct joint/link hierarchy.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from pxr import Usd, UsdGeom

USD_PATH = Path(__file__).parent / "urdf/assets/robots/coco_one/coco_one.usd"
URDF_DIR = Path(__file__).parent / "urdf"
MESH_DIR = URDF_DIR / "meshes"


def extract_mesh_to_obj(stage: Usd.Stage, prim_path: str, obj_path: Path) -> bool:
    """Extract a UsdGeom.Mesh prim to a Wavefront OBJ file."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsA(UsdGeom.Mesh):
        return False

    mesh = UsdGeom.Mesh(prim)
    points = np.array(mesh.GetPointsAttr().Get())
    face_counts = np.array(mesh.GetFaceVertexCountsAttr().Get())
    face_indices = np.array(mesh.GetFaceVertexIndicesAttr().Get())
    normals = mesh.GetNormalsAttr().Get()

    obj_path.parent.mkdir(parents=True, exist_ok=True)
    with open(obj_path, "w") as f:
        f.write(f"# Exported from {prim_path}\n")
        for p in points:
            f.write(f"v {p[0]:.8f} {p[1]:.8f} {p[2]:.8f}\n")
        if normals:
            for n in normals:
                f.write(f"vn {n[0]:.8f} {n[1]:.8f} {n[2]:.8f}\n")

        # Write faces — handle both tris and quads
        idx = 0
        for count in face_counts:
            verts = face_indices[idx : idx + count]
            # OBJ is 1-indexed
            face_str = " ".join(str(v + 1) for v in verts)
            f.write(f"f {face_str}\n")
            idx += count

    print(f"  Exported {len(points)} vertices → {obj_path.name}")
    return True


def get_xform_translate(stage: Usd.Stage, prim_path: str) -> tuple[float, float, float]:
    """Get the translation of an Xform prim."""
    prim = stage.GetPrimAtPath(prim_path)
    if not prim:
        return (0.0, 0.0, 0.0)
    xformable = UsdGeom.Xformable(prim)
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == "xformOp:translate":
            t = op.Get()
            return (float(t[0]), float(t[1]), float(t[2]))
    return (0.0, 0.0, 0.0)


# ──────────────────────── URDF builder ────────────────────────

def indent_xml(elem: ET.Element, level: int = 0) -> None:
    """Add pretty-print indentation to XML tree."""
    indent = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = indent + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            indent_xml(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def add_link(robot: ET.Element, name: str, mesh_file: str | None, color: tuple = (0.7, 0.7, 0.7, 1.0)) -> None:
    """Add a link element to the URDF."""
    link = ET.SubElement(robot, "link", name=name)

    if mesh_file:
        # Visual
        visual = ET.SubElement(link, "visual")
        geom = ET.SubElement(visual, "geometry")
        ET.SubElement(geom, "mesh", filename=mesh_file)
        mat = ET.SubElement(visual, "material", name=f"{name}_material")
        ET.SubElement(mat, "color", rgba=f"{color[0]} {color[1]} {color[2]} {color[3]}")

        # Collision — use same mesh
        collision = ET.SubElement(link, "collision")
        col_geom = ET.SubElement(collision, "geometry")
        ET.SubElement(col_geom, "mesh", filename=mesh_file)

    # Inertial (simple placeholder)
    inertial = ET.SubElement(link, "inertial")
    ET.SubElement(inertial, "mass", value="1.0")
    ET.SubElement(inertial, "inertia", ixx="0.01", ixy="0", ixz="0", iyy="0.01", iyz="0", izz="0.01")


def add_joint(
    robot: ET.Element,
    name: str,
    joint_type: str,
    parent: str,
    child: str,
    origin_xyz: tuple[float, float, float],
    axis: tuple[float, float, float] = (0, 0, 1),
    lower: float | None = None,
    upper: float | None = None,
) -> None:
    """Add a joint element to the URDF."""
    joint = ET.SubElement(robot, "joint", name=name, type=joint_type)
    ET.SubElement(joint, "parent", link=parent)
    ET.SubElement(joint, "child", link=child)
    ET.SubElement(joint, "origin", xyz=f"{origin_xyz[0]} {origin_xyz[1]} {origin_xyz[2]}", rpy="0 0 0")
    ET.SubElement(joint, "axis", xyz=f"{axis[0]} {axis[1]} {axis[2]}")
    if lower is not None and upper is not None and joint_type != "fixed":
        ET.SubElement(joint, "limit", lower=str(lower), upper=str(upper), effort="100", velocity="10")


def build_urdf(stage: Usd.Stage) -> ET.Element:
    """Build the full URDF tree from USD data."""
    robot = ET.Element("robot", name="coco_one")

    # ── Links ──
    # Colors matching the USD materials roughly
    body_color = (0.85, 0.2, 0.6, 1.0)    # Coco Pink
    axle_color = (0.3, 0.3, 0.3, 1.0)     # Dark gray
    wheel_color = (0.15, 0.15, 0.15, 1.0)  # Near black

    add_link(robot, "base_link", "meshes/base_link.obj", axle_color)
    add_link(robot, "body_link", "meshes/body_link.obj", body_color)
    add_link(robot, "front_axle_link", "meshes/front_axle_link.obj", axle_color)
    add_link(robot, "rear_axle_link", None, axle_color)  # No mesh — dummy link
    add_link(robot, "front_left_shock_link", None)  # No mesh
    add_link(robot, "front_right_shock_link", None)
    add_link(robot, "rear_left_shock_link", None)
    add_link(robot, "rear_right_shock_link", None)
    add_link(robot, "front_left_wheel_link", "meshes/front_left_wheel_link.obj", wheel_color)
    add_link(robot, "front_right_wheel_link", "meshes/front_right_wheel_link.obj", wheel_color)
    add_link(robot, "rear_left_wheel_link", "meshes/rear_left_wheel_link.obj", wheel_color)
    add_link(robot, "rear_right_wheel_link", "meshes/rear_right_wheel_link.obj", wheel_color)

    # ── Joints ──
    # Transforms from USD (all relative to /coco_one root):
    front_axle_t = get_xform_translate(stage, "/coco_one/front_axle_link")  # (0.221, 0, -0.095)
    rear_axle_t = get_xform_translate(stage, "/coco_one/rear_axle_link")    # (-0.235, 0, -0.095)
    fl_wheel_t = get_xform_translate(stage, "/coco_one/front_left_wheel_link")  # (0.221, 0.236, -0.166)
    fr_wheel_t = get_xform_translate(stage, "/coco_one/front_right_wheel_link")
    rl_wheel_t = get_xform_translate(stage, "/coco_one/rear_left_wheel_link")
    rr_wheel_t = get_xform_translate(stage, "/coco_one/rear_right_wheel_link")

    # body_to_base: fixed
    add_joint(robot, "body_to_base_joint", "fixed", "base_link", "body_link",
              origin_xyz=(0, 0, 0))

    # Steering joint: base → front_axle (revolute around Z, ±20°)
    steering_limit_rad = np.radians(20.0)
    add_joint(robot, "base_to_front_axle_joint", "revolute", "base_link", "front_axle_link",
              origin_xyz=front_axle_t, axis=(0, 0, 1),
              lower=-steering_limit_rad, upper=steering_limit_rad)

    # Rear axle: fixed
    add_joint(robot, "base_to_rear_axle_joint", "fixed", "base_link", "rear_axle_link",
              origin_xyz=rear_axle_t)

    # Shock joints (simplified as fixed for URDF — we don't simulate suspension)
    # Front shocks — relative to front_axle
    fl_shock_rel = (fl_wheel_t[0] - front_axle_t[0], fl_wheel_t[1] - front_axle_t[1], fl_wheel_t[2] - front_axle_t[2])
    fr_shock_rel = (fr_wheel_t[0] - front_axle_t[0], fr_wheel_t[1] - front_axle_t[1], fr_wheel_t[2] - front_axle_t[2])
    add_joint(robot, "front_left_shock_joint", "fixed", "front_axle_link", "front_left_shock_link",
              origin_xyz=fl_shock_rel)
    add_joint(robot, "front_right_shock_joint", "fixed", "front_axle_link", "front_right_shock_link",
              origin_xyz=fr_shock_rel)

    # Rear shocks — relative to rear_axle
    rl_shock_rel = (rl_wheel_t[0] - rear_axle_t[0], rl_wheel_t[1] - rear_axle_t[1], rl_wheel_t[2] - rear_axle_t[2])
    rr_shock_rel = (rr_wheel_t[0] - rear_axle_t[0], rr_wheel_t[1] - rear_axle_t[1], rr_wheel_t[2] - rear_axle_t[2])
    add_joint(robot, "rear_left_shock_joint", "fixed", "rear_axle_link", "rear_left_shock_link",
              origin_xyz=rl_shock_rel)
    add_joint(robot, "rear_right_shock_joint", "fixed", "rear_axle_link", "rear_right_shock_link",
              origin_xyz=rr_shock_rel)

    # Wheel joints (continuous rotation around Y axis)
    add_joint(robot, "front_left_wheel_joint", "continuous", "front_left_shock_link", "front_left_wheel_link",
              origin_xyz=(0, 0, 0), axis=(0, 1, 0))
    add_joint(robot, "front_right_wheel_joint", "continuous", "front_right_shock_link", "front_right_wheel_link",
              origin_xyz=(0, 0, 0), axis=(0, 1, 0))
    add_joint(robot, "rear_left_wheel_joint", "continuous", "rear_left_shock_link", "rear_left_wheel_link",
              origin_xyz=(0, 0, 0), axis=(0, 1, 0))
    add_joint(robot, "rear_right_wheel_joint", "continuous", "rear_right_shock_link", "rear_right_wheel_link",
              origin_xyz=(0, 0, 0), axis=(0, 1, 0))

    return robot


def main() -> None:
    print(f"Opening USD: {USD_PATH}")
    stage = Usd.Stage.Open(str(USD_PATH))

    # ── Export meshes ──
    print("\nExporting meshes...")
    mesh_links = [
        "base_link", "body_link", "front_axle_link",
        "front_left_wheel_link", "front_right_wheel_link",
        "rear_left_wheel_link", "rear_right_wheel_link",
    ]
    for link_name in mesh_links:
        prim_path = f"/coco_one/{link_name}/visuals"
        obj_path = MESH_DIR / f"{link_name}.obj"
        if not extract_mesh_to_obj(stage, prim_path, obj_path):
            print(f"  WARNING: No mesh at {prim_path}")

    # ── Build URDF ──
    print("\nBuilding URDF...")
    robot = build_urdf(stage)
    indent_xml(robot)
    tree = ET.ElementTree(robot)

    urdf_path = URDF_DIR / "coco_one.urdf"
    tree.write(str(urdf_path), encoding="unicode", xml_declaration=True)
    print(f"\nURDF written to: {urdf_path}")
    print("Done!")


if __name__ == "__main__":
    main()
