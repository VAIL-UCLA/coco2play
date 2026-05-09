#!/usr/bin/env python3
"""Viser viewer: 3DGS scene + drivable robot + collision physics.

Usage:
    python viewer.py

Then open http://localhost:8080 in your browser.
Use IJKL to drive the robot.
The invisible mesh provides collision boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import random
import re
import struct
import threading
import time
from pathlib import Path
from typing import TypedDict

import numpy as np
import numpy.typing as npt
import trimesh
from plyfile import PlyData

import viser
import viser.transforms as tf
from viser.extras import ViserUrdf
from viser import _messages as viser_messages

# WebSocket server for keyboard input

# ──────────────────────── Paths ────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
ROBOT_URDF = BASE_DIR / "urdf" / "coco_one.urdf"
OBJECTS_DIR = DATA_DIR / "objects"

# ──────────────────────── Ego Camera Constants ────────────────────────

EGO_FX = 650.0
EGO_FY = 650.0
EGO_CX = 960.0   # 1920 / 2
EGO_CY = 540.0   # 1080 / 2
EGO_FOV_Y = 2.0 * math.atan(EGO_CY / EGO_FY)   # ~1.387 rad (~79.4°)
EGO_ASPECT = 1920.0 / 1080.0                     # ~1.778
EGO_RENDER_W = 480
EGO_RENDER_H = 270
EGO_CAM_FORWARD_OFFSET = 0.60   # metres in front of robot centre (clears body)
EGO_CAM_UP_OFFSET = 0.35        # metres above robot centre (−Y direction)
EGO_FRUSTUM_FORWARD_OFFSET = 0.4  # frustum drawn slightly behind camera (near robot head)


def get_available_scenes() -> list[str]:
    """Return scene IDs (subdir names) that have both required PLY files."""
    scenes = []
    for d in sorted(DATA_DIR.iterdir()):
        if d.is_dir() and (d / "point_cloud.ply").exists() and (d / "mesh.ply").exists():
            scenes.append(d.name)
    return scenes


def load_scene_meta() -> dict:
    """Load meta.json from the data directory."""
    meta_path = DATA_DIR / "meta.json"
    if meta_path.exists():
        with open(meta_path) as f:
            return json.load(f)
    return {}

# ──────────────────────── 3DGS Loading ────────────────────────


class SplatFile(TypedDict):
    centers: npt.NDArray[np.floating]
    rgbs: npt.NDArray[np.floating]
    opacities: npt.NDArray[np.floating]
    covariances: npt.NDArray[np.floating]


def load_ply_splats(ply_path: Path) -> SplatFile:
    """Load 3D Gaussian Splat data from a standard PLY file."""
    print(f"Loading Gaussian splats from {ply_path.name}...")
    start = time.time()

    SH_C0 = 0.28209479177387814
    plydata = PlyData.read(str(ply_path))
    v = plydata["vertex"]

    positions = np.stack([v["x"], v["y"], v["z"]], axis=-1)
    scales = np.exp(np.stack([v["scale_0"], v["scale_1"], v["scale_2"]], axis=-1))
    wxyzs = np.stack([v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]], axis=1)
    colors = 0.5 + SH_C0 * np.stack([v["f_dc_0"], v["f_dc_1"], v["f_dc_2"]], axis=1)
    opacities = 1.0 / (1.0 + np.exp(-v["opacity"][:, None]))

    Rs = tf.SO3(wxyzs).as_matrix()
    covariances = np.einsum(
        "nij,njk,nlk->nil", Rs, np.eye(3)[None, :, :] * scales[:, None, :] ** 2, Rs
    )

    num = len(v)
    elapsed = time.time() - start
    print(f"  Loaded {num:,} Gaussians in {elapsed:.1f}s")
    return SplatFile(
        centers=positions,
        rgbs=np.clip(colors, 0, 1),
        opacities=np.clip(opacities, 0, 1),
        covariances=covariances,
    )


# ──────────────────────── Scene Mesh (collision) ────────────────────────


def load_collision_mesh(mesh_path: Path) -> trimesh.Trimesh:
    """Load the scene collision mesh (pre-triangulated PLY, no quads)."""
    print(f"Loading collision mesh from {mesh_path.name}...")
    start = time.time()
    mesh = trimesh.load(str(mesh_path))
    assert isinstance(mesh, trimesh.Trimesh)
    elapsed = time.time() - start
    print(f"  Loaded {mesh.vertices.shape[0]:,} vertices, {mesh.faces.shape[0]:,} faces in {elapsed:.1f}s")
    print(f"  Bounds: {mesh.bounds[0]} → {mesh.bounds[1]}")
    return mesh


# Ground clearance so the robot sits visibly on top of the mesh (not inside it)
GROUND_CLEARANCE = 0.08


def raycast_ground_height(
    mesh: trimesh.Trimesh, x: float, z: float, robot_y: float
) -> float | None:
    """Cast a ray downward (+Y) from just above the robot to find the ground surface.

    Coordinate convention: +Y is DOWN, -Y is UP (scene space).
    We start the ray from robot_y - 0.5 (half a metre above the robot in -Y)
    so the ray travels toward +Y (downward) and hits the top face of the ground.
    Using robot_y as reference avoids the fixed world-space start that breaks
    when the robot is far from y=0.
    """
    y_start = robot_y - 0.5          # start 0.5 m above (in -Y = up direction)
    origin    = np.array([[x, y_start, z]])
    direction = np.array([[0.0, 1.0, 0.0]])   # +Y = downward
    locations, _, _ = mesh.ray.intersects_location(origin, direction)
    if len(locations) == 0:
        return None
    # Smallest Y = first surface hit going downward (top face, not underside)
    return float(locations[:, 1].min())


def check_collision_box(
    mesh: trimesh.Trimesh,
    x: float, y: float, z: float,
    half_extents: tuple[float, float, float] = (0.3, 0.25, 0.25),
) -> bool:
    """Check if a box at (x,y,z) collides with the scene mesh.

    Coordinate system: X=forward, Y=up, Z=lateral.
    half_extents = (half_x, half_y, half_z).
    Cast rays in ±X and ±Z (ground plane) to detect walls.
    """
    origin = np.array([x, y, z])

    # Cast rays on the ground plane (X and Z axes)
    directions = [
        [1, 0, 0], [-1, 0, 0],   # ±X (forward/back)
        [0, 0, 1], [0, 0, -1],   # ±Z (lateral)
    ]
    thresholds = [
        half_extents[0], half_extents[0],
        half_extents[2], half_extents[2],
    ]

    for d, half in zip(directions, thresholds):
        d = np.array(d, dtype=float)
        locations, _, _ = mesh.ray.intersects_location(
            origin.reshape(1, 3), d.reshape(1, 3)
        )
        if len(locations) > 0:
            dists = np.linalg.norm(locations - origin, axis=1)
            if dists.min() < half:
                return True  # Collision!
    return False


def make_collision_manager(
    scene_mesh: trimesh.Trimesh,
    box_half_extents: tuple[float, float, float],
) -> tuple[object | None, object | None]:
    """Create a CollisionManager with the scene and a robot box for proper box-mesh collision.

    Returns (manager, box_mesh) or (None, None) if FCL/collision is unavailable.
    """
    try:
        from trimesh.collision import CollisionManager
        from trimesh.creation import box
    except ImportError:
        return None, None
    try:
        manager = CollisionManager()
        manager.add_object("scene", scene_mesh, np.eye(4))
        extents = (2.0 * box_half_extents[0], 2.0 * box_half_extents[1], 2.0 * box_half_extents[2])
        robot_box = box(extents=extents)
        return manager, robot_box
    except Exception:
        return None, None


def check_collision_box_fcl(
    manager: object,
    robot_box: object,
    x: float, y: float, z: float,
) -> bool:
    """Check if the robot box at (x,y,z) collides with the scene using FCL."""
    transform = np.eye(4)
    transform[:3, 3] = (x, y, z)
    return manager.in_collision_single(robot_box, transform)


# ──────────────────────── Assets ────────────────────────


class SceneAsset:
    """A movable obstacle placed in the scene."""

    __slots__ = (
        "name", "x", "y", "z", "vx", "vz",
        "collision_radius", "scale", "glb_handle", "mass", "friction",
    )

    def __init__(
        self, name: str, x: float, y: float, z: float,
        collision_radius: float, scale: float, glb_handle: object,
        mass: float = 1.0,
    ) -> None:
        self.name = name
        self.x, self.y, self.z = x, y, z
        self.vx: float = 0.0
        self.vz: float = 0.0
        self.collision_radius = collision_radius
        self.scale = scale
        self.glb_handle = glb_handle
        self.mass = mass
        self.friction: float = 5.0


def load_asset_calibration() -> dict:
    """Load data/objects/calibration.json."""
    cal_path = OBJECTS_DIR / "calibration.json"
    if cal_path.exists():
        with open(cal_path) as f:
            return json.load(f)
    return {}


# ──────────────────────── Ego Camera Pose ────────────────────────


def compute_ego_camera_pose(
    rs: "RobotState",
) -> tuple[tuple[float, float, float], tuple[float, float, float], np.ndarray, np.ndarray]:
    """Return (cam_position, frustum_position, wxyz_frustum_opencv, wxyz_render_threejs)."""
    cam_x = rs.x + EGO_CAM_FORWARD_OFFSET * math.cos(rs.yaw)
    cam_z = rs.z + EGO_CAM_FORWARD_OFFSET * math.sin(rs.yaw)
    cam_y = rs.y - EGO_CAM_UP_OFFSET

    frust_x = rs.x + EGO_FRUSTUM_FORWARD_OFFSET * math.cos(rs.yaw)
    frust_z = rs.z + EGO_FRUSTUM_FORWARD_OFFSET * math.sin(rs.yaw)
    frust_y = cam_y  # same height

    cy, sy = math.cos(rs.yaw), math.sin(rs.yaw)

    # OpenCV: +X right, +Y down, +Z forward
    R_opencv = np.array([
        [ sy, 0.0,  cy],
        [0.0, 1.0, 0.0],
        [-cy, 0.0,  sy],
    ])
    wxyz_frustum = tf.SO3.from_matrix(R_opencv).wxyz

    # Three.js: +X right, +Y up, −Z forward
    R_threejs = np.array([
        [ sy,  0.0, -cy],
        [0.0, -1.0,  0.0],
        [-cy,  0.0, -sy],
    ])
    wxyz_render = tf.SO3.from_matrix(R_threejs).wxyz

    return (cam_x, cam_y, cam_z), (frust_x, frust_y, frust_z), wxyz_frustum, wxyz_render


# ──────────────────────── Robot Driving Model ────────────────────────


class ClientSession:
    """All mutable state that belongs to one connected browser tab."""

    __slots__ = (
        "client_id", "robot_state", "key_state",
        "robot_frame", "viser_urdf",
        "camera_orbit_angle", "camera_orbit_elevation",
        "last_collision_banner_time",
        "ego_frustum",
        "lives", "dead",
    )

    def __init__(
        self,
        client_id: int,
        robot_state: "RobotState",
        robot_frame: object,
        viser_urdf: object,
    ) -> None:
        self.client_id = client_id
        self.robot_state = robot_state
        self.robot_frame = robot_frame
        self.viser_urdf = viser_urdf
        self.key_state: dict = {
            "up": False, "down": False, "left": False, "right": False,
            "orbit_left": False, "orbit_right": False,
            "orbit_up": False, "orbit_down": False,
        }
        self.camera_orbit_angle: float = 90.0
        self.camera_orbit_elevation: float = 1.5
        self.last_collision_banner_time: float = 0.0
        self.ego_frustum: object = None
        self.lives: int = 3
        self.dead: bool = False


class RobotState:
    """Kinematic bicycle with spring steering (sketchbook-style).

    Single source of truth: yaw drives both movement and display so I/K
    forward/back are always aligned with the mesh.
    """

    def __init__(self) -> None:
        self.x: float = 0.0
        self.y: float = 0.0
        self.z: float = 0.0
        self.yaw: float = 0.0
        self.steering_angle: float = 0.0
        self.steering_velocity: float = 0.0  # for spring
        self.speed: float = 0.0
        self.wheel_angle: float = 0.0

        # Robot dimensions from URDF
        self.wheelbase: float = 0.456
        self.wheel_radius: float = 0.11
        self.max_steering: float = math.radians(20.0)
        self.max_speed: float = 2.0  # m/s

        # Spring steering (like sketchbook SpringSimulator)
        self.steering_stiffness: float = 60.0   # spring toward target
        self.steering_damping: float = 10.0     # damp oscillation
        self.steering_mass: float = 0.6        # effective inertia

        # Speed smoothing and turn limit
        self.tau_speed: float = 0.2
        self.max_yaw_rate: float = 0.8

    def update(self, dt: float, throttle: float, steering: float) -> tuple[float, float]:
        """Update with spring steering, slip angle, and drift correction."""
        # Spring steering: steer toward target with spring-damper
        steering_target = np.clip(steering, -self.max_steering, self.max_steering)
        steering_force = self.steering_stiffness * (steering_target - self.steering_angle)
        steering_force -= self.steering_damping * self.steering_velocity
        self.steering_velocity += (steering_force / self.steering_mass) * dt
        self.steering_angle += self.steering_velocity * dt
        self.steering_angle = np.clip(self.steering_angle, -self.max_steering, self.max_steering)
        # Zero velocity when at limit to avoid wind-up
        if abs(self.steering_angle) >= self.max_steering - 1e-6:
            self.steering_velocity *= 0.9

        # Speed smoothing
        speed_target = np.clip(throttle * self.max_speed, -self.max_speed, self.max_speed)
        self.speed += (speed_target - self.speed) * min(1.0, dt / self.tau_speed)

        if abs(self.speed) < 1e-4:
            self.steering_velocity *= 0.9  # decay when stopped
            return self.x, self.z

        # Bicycle yaw rate from steering
        if abs(self.steering_angle) > 1e-4:
            turning_radius = self.wheelbase / math.tan(self.steering_angle)
            yaw_rate = self.speed / turning_radius
            yaw_rate = np.clip(yaw_rate, -self.max_yaw_rate, self.max_yaw_rate)
            dyaw = yaw_rate * dt
        else:
            dyaw = 0.0

        self.yaw += dyaw

        # Move in direction yaw (same as display) so I/K forward/back stay aligned with mesh
        dx = self.speed * math.cos(self.yaw) * dt
        dz = self.speed * math.sin(self.yaw) * dt
        new_x = self.x + dx
        new_z = self.z + dz

        # Do not update self.x/self.z here — main loop applies position only when collision check passes
        self.wheel_angle += (self.speed * dt) / self.wheel_radius

        return new_x, new_z


# ──────────────────────── Main Application ────────────────────────


def main() -> None:
    # Viser runs on internal port 1235; an aiohttp proxy on 1234 routes
    # /keyboard (WebSocket) here and forwards everything else to viser.
    PUBLIC_PORT = 1234

    server = viser.ViserServer(port=PUBLIC_PORT + 1)  # internal; proxy exposes PUBLIC_PORT
    server.gui.configure_theme(show_logo=False, titlebar_content=None)
    VISER_PORT = server.get_port()  # actual bound port (viser auto-increments if busy)
    print(f"Viser server starting on http://localhost:{PUBLIC_PORT} (viser internal: {VISER_PORT})")

    # ── 0) Discover scenes and load metadata ──
    available_scenes = get_available_scenes()
    scene_meta = load_scene_meta()
    if not available_scenes:
        raise RuntimeError(f"No valid scenes found in {DATA_DIR}. Each scene needs point_cloud.ply and mesh.ply.")
    print(f"✓ Found scenes: {available_scenes}")

    # Mutable scene state (replaced on scene switch)
    scene_handles: dict = {"frame": None, "splats": None, "mesh": None}
    collision_state: dict = {"mesh": None, "manager": None, "robot_box": None}
    box_half = (0.35, 0.2, 0.25)

    # Per-client sessions: keyed by viser client_id
    sessions: dict[int, ClientSession] = {}
    sessions_lock = threading.Lock()
    current_scene_id: str = available_scenes[0]

    # ── Asset state ──
    asset_calibration = load_asset_calibration()
    scene_assets: list[SceneAsset] = []
    assets_lock = threading.Lock()
    ROBOT_COLLISION_RADIUS = 0.35  # approx from box half-extents

    def remove_assets() -> None:
        """Remove all current scene assets."""
        with assets_lock:
            for a in scene_assets:
                try:
                    a.glb_handle.remove()
                except Exception:
                    pass
            scene_assets.clear()

    MAX_GLB_BYTES = 2 * 1024 * 1024  # 2 MB – keep WebSocket replay fast
    # Categories to exclude from scene asset placement
    EXCLUDED_ASSET_CATEGORIES = {
        "wall", "building",                                    # structures
        "bus", "bus station", "car", "caravan", "scooter",     # vehicles
        "train", "bicycle", "foodtruck",                       # vehicles
        "static biker", "static motocycle",                    # vehicle-related
    }

    def place_assets(spawn: dict, count: int = 8, asset_region: dict | None = None) -> None:
        """Place random pushable assets in a rectangular region in front of the robot.

        Args:
            spawn: dict with x, y, z spawn coordinates.
            count: number of assets to place.
            asset_region: dict with x (width) and y (length) defining the
                placement rectangle in front of the spawn point.  Falls back
                to a 4×16 m default.
        """
        remove_assets()
        pushable = {}
        for fname, info in asset_calibration.items():
            if not info.get("pushable", False):
                continue
            # Skip excluded categories (case-insensitive prefix before first '-')
            category = fname.split("-")[0].lower()
            if category in EXCLUDED_ASSET_CATEGORIES:
                continue
            glb_path = OBJECTS_DIR / fname
            if not glb_path.exists():
                continue
            if glb_path.stat().st_size > MAX_GLB_BYTES:
                continue
            pushable[fname] = info
        if not pushable:
            print("  No pushable assets under size limit")
            return

        # Region dimensions (x = width perpendicular to forward, y = length along forward)
        region_w = float((asset_region or {}).get("x", 4))
        region_l = float((asset_region or {}).get("y", 16))

        chosen = random.sample(list(pushable.keys()), min(count, len(pushable)))
        sx = spawn.get("x", 0.0)
        sz = spawn.get("z", 0.0)
        ground_y = 0.3  # assets shifted 0.5m lower (Y-down convention)
        placed: list[tuple[float, float, float]] = []  # (x, z, radius) for overlap check

        # Rotation to flip GLB from Y-up to scene Y-down
        flip_wxyz = tf.SO3.from_x_radians(np.pi).wxyz

        for fname in chosen:
            info = pushable[fname]
            glb_path = OBJECTS_DIR / fname
            if not glb_path.exists():
                continue
            scale = info["scale"]
            col_r = info["collision_radius"]
            # y_offset aligns the asset's bottom to ground after the π flip.
            # In Y-up GLB space, bb_min_y * scale gives the signed offset;
            # adding it to ground_y places the bottom flush with the floor.
            asset_y = ground_y + info.get("y_offset", 0.0)

            # Random position in a rectangle in front of the robot spawn.
            # Robot initial yaw is π/2, so forward is +Z, lateral is X.
            for _attempt in range(30):
                fwd = random.uniform(1.5, region_l)   # forward distance (skip first 1.5m)
                lat = random.uniform(-region_w / 2, region_w / 2)  # lateral offset
                ax = sx + lat
                az = sz + fwd
                # Check overlap with existing assets and robot spawn
                ok = True
                for px, pz, pr in placed:
                    if math.hypot(ax - px, az - pz) < col_r + pr + 0.3:
                        ok = False
                        break
                if math.hypot(ax - sx, az - sz) < ROBOT_COLLISION_RADIUS + col_r + 1.0:
                    ok = False
                if ok:
                    break
            else:
                continue  # couldn't place without overlap

            try:
                glb_data = glb_path.read_bytes()
                handle = server.scene.add_glb(
                    f"/assets/{fname}",
                    glb_data=glb_data,
                    scale=scale,
                    wxyz=flip_wxyz,
                    position=(ax, asset_y, az),
                )
                asset = SceneAsset(
                    name=fname, x=ax, y=asset_y, z=az,
                    collision_radius=col_r, scale=scale,
                    glb_handle=handle,
                )
                with assets_lock:
                    scene_assets.append(asset)
                placed.append((ax, az, col_r))
            except Exception as e:
                print(f"  Failed to load asset {fname}: {e}")

        print(f"✓ Placed {len(placed)} pushable assets")

    def _reset_session(sess: ClientSession, spawn: dict) -> None:
        """Reset a session's robot to the spawn point."""
        sess.robot_state.x = spawn.get("x", 0.0)
        sess.robot_state.y = 0.0  # flat floor, wheels on ground
        sess.robot_state.z = spawn.get("z", 0.0)
        sess.robot_state.yaw = math.pi / 2.0
        sess.robot_state.speed = 0.0
        sess.robot_state.steering_angle = 0.0
        sess.robot_state.steering_velocity = 0.0
        sess.robot_state.wheel_angle = 0.0
        sess.camera_orbit_angle = 90.0
        sess.camera_orbit_elevation = 1.5
        for k in sess.key_state:
            sess.key_state[k] = False
        sess.lives = 3
        sess.dead = False

    def load_scene(scene_id: str) -> None:
        """Load splats + collision mesh for the given scene_id, replacing existing ones."""
        nonlocal current_scene_id
        scene_dir = DATA_DIR / scene_id

        # Remove previous handles
        if scene_handles["splats"] is not None:
            scene_handles["splats"].remove()
        if scene_handles["mesh"] is not None:
            scene_handles["mesh"].remove()

        # Reload meta.json fresh so edits take effect without restart
        meta = load_scene_meta().get(scene_id, {})
        rot_deg = meta.get("scene_rotation", {"x": 0.0, "y": 0.0, "z": 0.0})
        rx = math.radians(rot_deg.get("x", 0.0))
        ry = math.radians(rot_deg.get("y", 0.0))
        rz = math.radians(rot_deg.get("z", 0.0))
        scene_rot = tf.SO3.from_x_radians(rx) @ tf.SO3.from_y_radians(ry) @ tf.SO3.from_z_radians(rz)
        if scene_handles["frame"] is None:
            scene_handles["frame"] = server.scene.add_frame("/scene", show_axes=False)
        scene_handles["frame"].wxyz = scene_rot.wxyz
        print(f"  Scene rotation: x={rot_deg.get('x',0):.1f}° y={rot_deg.get('y',0):.1f}° z={rot_deg.get('z',0):.1f}°")

        # Load and add splats
        splat_data = load_ply_splats(scene_dir / "point_cloud.ply")
        scene_handles["splats"] = server.scene.add_gaussian_splats(
            "/scene/gaussian_splats",
            centers=splat_data["centers"],
            rgbs=splat_data["rgbs"],
            opacities=splat_data["opacities"],
            covariances=splat_data["covariances"],
        )
        print("✓ Gaussian splats added to scene")

        # Load collision mesh (server-side only — not added to scene to save
        # ~53 MB of bandwidth per client connection)
        mesh = load_collision_mesh(scene_dir / "mesh.ply")
        collision_state["mesh"] = mesh
        scene_handles["mesh"] = None
        print("✓ Collision mesh loaded (server-side only, not sent to clients)")

        # Rebuild collision manager
        mgr, rbox = make_collision_manager(mesh, box_half)
        collision_state["manager"] = mgr
        collision_state["robot_box"] = rbox
        if mgr is not None:
            print("✓ FCL collision enabled")

        # Update current scene and reset all active sessions
        current_scene_id = scene_id
        spawn = meta.get("spawn_point", {"x": 0.0, "y": 0.0, "z": 0.0})
        with sessions_lock:
            for sess in sessions.values():
                _reset_session(sess, spawn)
        print(f"  Spawn: ({spawn.get('x',0):.2f}, {spawn.get('y',0):.2f}, {spawn.get('z',0):.2f})")

        # Place random assets in the scene
        region = meta.get("asset_region")
        place_assets(spawn, count=8, asset_region=region)

    # ── 3) Load initial scene (robot created per-client in on_client_connect) ──
    load_scene(available_scenes[0])
    print("✓ Initial scene loaded")

    # ── 3b) Environment lighting ──
    # Coordinate convention: +Y is DOWN, -Y is UP.
    # Hemisphere light: warm sky from above (-Y), cool fill from below (+Y).
    server.scene.add_light_hemisphere(
        "/lights/hemisphere",
        sky_color=(235, 225, 200),    # warm off-white sky
        ground_color=(80, 100, 130),  # cool blue-grey bounce from ground
        intensity=0.7,
    )
    # Key light: sun from upper-right-front.
    # Direction is from the light's world position toward (0,0,0).
    # In +Y-down coords, position y=-12 is high above the scene.
    server.scene.add_light_directional(
        "/lights/key",
        color=(255, 248, 220),   # warm sunlight
        intensity=1.4,
        cast_shadow=False,
        position=(6.0, -12.0, 4.0),
    )
    # Rim / fill light: cool back-left to separate robot from background.
    server.scene.add_light_directional(
        "/lights/rim",
        color=(180, 210, 255),   # cool sky-blue
        intensity=0.5,
        cast_shadow=False,
        position=(-6.0, -8.0, -8.0),
    )

    # ── 4) GUI Controls ──

    # ── 4a) aiohttp proxy on PUBLIC_PORT ──
    # /keyboard WS → keyboard input handler
    # Other WS  → proxy to viser (preserving subprotocols)
    # HTTP       → proxy to viser

    from aiohttp import web, WSMsgType, ClientSession as AioClientSession

    async def _keyboard_ws(request: web.Request) -> web.WebSocketResponse:
        """Handle /keyboard WebSocket for keyboard input."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        session_id: int | None = None
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    if data.get("type") == "init":
                        session_id = data.get("session_id")
                        continue
                    if session_id is None:
                        continue
                    action, key = data.get("action"), data.get("key")
                    with sessions_lock:
                        sess = sessions.get(session_id)
                    if sess is not None and key in sess.key_state:
                        sess.key_state[key] = (action == "press")
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            if session_id is not None:
                with sessions_lock:
                    sess = sessions.get(session_id)
                if sess is not None:
                    for k in sess.key_state:
                        sess.key_state[k] = False
        return ws

    async def _proxy(request: web.Request) -> web.StreamResponse:
        """Proxy HTTP and WebSocket requests to viser."""
        target = f"http://127.0.0.1:{VISER_PORT}{request.path_qs}"

        # ── WebSocket upgrade → bidirectional proxy to viser ──
        if request.headers.get("Upgrade", "").lower() == "websocket":
            subprotos: list[str] = []
            for val in request.headers.getall("Sec-WebSocket-Protocol", []):
                subprotos.extend(s.strip() for s in val.split(","))

            ws_browser = web.WebSocketResponse(
                protocols=tuple(subprotos) if subprotos else (),
                max_msg_size=500 * 1024 * 1024,
            )
            await ws_browser.prepare(request)
            print(f"[proxy] WS browser connected, subprotos={subprotos}")

            ws_url = f"ws://127.0.0.1:{VISER_PORT}{request.path}"
            try:
                async with AioClientSession() as cs:
                    async with cs.ws_connect(
                        ws_url,
                        protocols=tuple(subprotos) if subprotos else (),
                        compress=0,
                        max_msg_size=500 * 1024 * 1024,
                    ) as ws_viser:
                        print(f"[proxy] WS viser connected → {ws_url}")

                        msg_count = [0, 0]  # [b2v, v2b]

                        async def _b2v() -> None:
                            async for m in ws_browser:
                                if m.type == WSMsgType.BINARY:
                                    await ws_viser.send_bytes(m.data)
                                    msg_count[0] += 1
                                elif m.type == WSMsgType.TEXT:
                                    await ws_viser.send_str(m.data)
                                    msg_count[0] += 1
                                else:
                                    print(f"[proxy] b2v got msg type={m.type}, closing")
                                    break
                            print(f"[proxy] b2v ended after {msg_count[0]} msgs, browser closed={ws_browser.closed}, close_code={ws_browser.close_code}")

                        async def _v2b() -> None:
                            async for m in ws_viser:
                                if m.type == WSMsgType.BINARY:
                                    await ws_browser.send_bytes(m.data)
                                    msg_count[1] += 1
                                elif m.type == WSMsgType.TEXT:
                                    await ws_browser.send_str(m.data)
                                    msg_count[1] += 1
                                else:
                                    print(f"[proxy] v2b got msg type={m.type}, closing")
                                    break
                            print(f"[proxy] v2b ended after {msg_count[1]} msgs, viser closed={ws_viser.closed}")

                        done, pending = await asyncio.wait(
                            [asyncio.create_task(_b2v()),
                             asyncio.create_task(_v2b())],
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for t in done:
                            exc = t.exception()
                            if exc:
                                print(f"[proxy] WS task exception: {exc}")
                        for t in pending:
                            t.cancel()
                        print(f"[proxy] WS proxy ended, msgs b2v={msg_count[0]} v2b={msg_count[1]}")
            except Exception as e:
                print(f"[proxy] WS proxy error: {e}")
            return ws_browser

        # ── Regular HTTP → proxy to viser ──
        async with AioClientSession(auto_decompress=False) as cs:
            async with cs.request(
                request.method,
                target,
                headers={
                    "Host": f"127.0.0.1:{VISER_PORT}",
                    "Accept-Encoding": request.headers.get("Accept-Encoding", ""),
                },
                data=await request.read() if request.can_read_body else None,
                allow_redirects=False,
            ) as resp:
                body = await resp.read()
                headers = {
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in ("transfer-encoding", "connection",
                                         "content-length")
                }
                return web.Response(status=resp.status, headers=headers, body=body)

    def _start_proxy() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        app = web.Application()
        app.router.add_get("/keyboard", _keyboard_ws)
        app.router.add_route("*", "/{path:.*}", _proxy)

        async def _run() -> None:
            runner = web.AppRunner(app, handle_signals=False)
            await runner.setup()
            site = web.TCPSite(runner, None, PUBLIC_PORT, reuse_address=True)
            try:
                await site.start()
            except OSError:
                print(f"\n❌ Port {PUBLIC_PORT} is already in use.")
                print(f"   Run:  fuser -k {PUBLIC_PORT}/tcp   then restart.\n")
                return
            print(f"✓ Proxy running on http://localhost:{PUBLIC_PORT} → viser:{VISER_PORT}")
            await asyncio.Event().wait()

        loop.run_until_complete(_run())

    threading.Thread(target=_start_proxy, daemon=True).start()
    time.sleep(0.5)  # let proxy bind before printing

    # Life system + collision overlay JavaScript
    collision_banner_js = """
(function() {
    if (window._lifeSystemInitialized) return;
    window._lifeSystemInitialized = true;

    const MAX_LIVES = 3;
    let currentLives = MAX_LIVES;

    const CSS = `
        @import url('https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@600;700&family=Orbitron:wght@700;900&display=swap');

        /* ── Heart HUD ── */
        #__life-hud {
            position: fixed;
            top: 14px; left: 50%;
            transform: translateX(-50%);
            z-index: 2147483640;
            pointer-events: none;
            display: flex;
            gap: 8px;
            align-items: center;
            background: rgba(0,0,0,0.45);
            padding: 6px 16px;
            border-radius: 20px;
            backdrop-filter: blur(6px);
            -webkit-backdrop-filter: blur(6px);
            border: 1px solid rgba(255,255,255,0.08);
            filter: drop-shadow(0 2px 8px rgba(0,0,0,.5));
        }
        #__life-label {
            font-family: 'Chakra Petch', 'Orbitron', sans-serif;
            font-weight: 600;
            font-size: 12px;
            letter-spacing: 0.12em;
            color: rgba(255,255,255,0.55);
            text-transform: uppercase;
            margin-right: 2px;
        }
        .life-heart {
            width: 32px; height: 32px;
            transition: transform 0.35s cubic-bezier(.34,1.56,.64,1),
                        opacity 0.4s ease,
                        filter 0.4s ease;
        }
        .life-heart.lost {
            transform: scale(0);
            opacity: 0;
            filter: grayscale(1) brightness(0.3);
        }
        .life-heart.hit {
            animation: __lh-shake 0.5s ease-out;
        }
        @keyframes __lh-shake {
            0%   { transform: scale(1.4) rotate(-12deg); }
            20%  { transform: scale(0.85) rotate(8deg); }
            40%  { transform: scale(1.15) rotate(-4deg); }
            60%  { transform: scale(0.95) rotate(2deg); }
            100% { transform: scale(1) rotate(0); }
        }

        /* ── Collision flash overlay ── */
        #__hit-flash {
            position: fixed;
            inset: 0;
            z-index: 2147483645;
            pointer-events: none;
            opacity: 0;
            background: radial-gradient(ellipse at center,
                rgba(255,30,0,0) 30%,
                rgba(255,20,0,0.5) 70%,
                rgba(180,0,0,0.7) 100%);
            transition: opacity 0.08s ease-in;
        }
        #__hit-flash.active {
            animation: __hf-vignette 0.7s ease-out forwards;
        }
        @keyframes __hf-vignette {
            0%   { opacity: 0.85; }
            30%  { opacity: 0.5; }
            100% { opacity: 0; }
        }

        /* ── Collision text banner ── */
        #__hit-banner {
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%) scale(0.3);
            z-index: 2147483646;
            pointer-events: none;
            opacity: 0;
            font-family: 'Orbitron', sans-serif;
            font-weight: 900;
            font-size: 52px;
            letter-spacing: 0.08em;
            color: #fff;
            text-shadow:
                0 0 20px rgba(255,60,0,0.9),
                0 0 60px rgba(255,30,0,0.5),
                0 4px 0 #8b0000;
            white-space: nowrap;
        }
        #__hit-banner.active {
            animation: __hb-pop 1.1s cubic-bezier(.16,1,.3,1) forwards;
        }
        @keyframes __hb-pop {
            0%   { opacity: 0; transform: translate(-50%, -50%) scale(0.3); }
            12%  { opacity: 1; transform: translate(-50%, -50%) scale(1.08); }
            22%  { transform: translate(-50%, -50%) scale(0.96); }
            30%  { transform: translate(-50%, -50%) scale(1); }
            70%  { opacity: 1; transform: translate(-50%, -50%) scale(1); }
            100% { opacity: 0; transform: translate(-50%, -54%) scale(0.9); }
        }

        /* ── Death screen ── */
        #__death-screen {
            position: fixed;
            inset: 0;
            z-index: 2147483647;
            pointer-events: none;
            opacity: 0;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 16px;
            background: radial-gradient(ellipse at center,
                rgba(10,0,0,0.85) 0%,
                rgba(0,0,0,0.95) 100%);
            transition: opacity 0.6s ease;
        }
        #__death-screen.active {
            opacity: 1;
            pointer-events: auto;
        }
        #__death-title {
            font-family: 'Orbitron', sans-serif;
            font-weight: 900;
            font-size: 72px;
            letter-spacing: 0.12em;
            color: transparent;
            background: linear-gradient(180deg, #ff4444 0%, #cc0000 50%, #880000 100%);
            -webkit-background-clip: text;
            background-clip: text;
            text-shadow: none;
            filter: drop-shadow(0 0 30px rgba(255,0,0,0.6)) drop-shadow(0 4px 0 rgba(80,0,0,0.8));
            animation: __dt-pulse 2s ease-in-out infinite;
            transform: scale(0);
            transition: transform 0.6s cubic-bezier(.34,1.56,.64,1);
        }
        #__death-screen.active #__death-title {
            transform: scale(1);
        }
        @keyframes __dt-pulse {
            0%,100% { filter: drop-shadow(0 0 30px rgba(255,0,0,0.5)) drop-shadow(0 4px 0 rgba(80,0,0,0.8)); }
            50%     { filter: drop-shadow(0 0 50px rgba(255,0,0,0.8)) drop-shadow(0 4px 0 rgba(80,0,0,0.8)); }
        }
        #__death-subtitle {
            font-family: 'Chakra Petch', sans-serif;
            font-weight: 600;
            font-size: 18px;
            letter-spacing: 0.2em;
            color: rgba(255,255,255,0.5);
            text-transform: uppercase;
            opacity: 0;
            transform: translateY(10px);
            transition: opacity 0.5s ease 0.4s, transform 0.5s ease 0.4s;
        }
        #__death-screen.active #__death-subtitle {
            opacity: 1;
            transform: translateY(0);
        }
        #__death-hearts {
            display: flex;
            gap: 12px;
            margin-top: 8px;
            opacity: 0;
            transition: opacity 0.5s ease 0.7s;
        }
        #__death-screen.active #__death-hearts {
            opacity: 1;
        }
        .death-heart-empty {
            width: 28px; height: 28px;
            opacity: 0.25;
            filter: grayscale(1);
        }
    `;

    const styleEl = document.createElement('style');
    styleEl.textContent = CSS;
    document.head.appendChild(styleEl);

    /* Heart SVG (clean, flat design) */
    const heartSvg = (cls) =>
        '<svg class="' + cls + '" viewBox="0 0 24 24" fill="#ff2244" xmlns="http://www.w3.org/2000/svg">' +
        '<path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 ' +
        '2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 ' +
        '14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 ' +
        '11.54L12 21.35z"/></svg>';

    /* ── Build DOM ── */
    // Life HUD
    const hud = document.createElement('div');
    hud.id = '__life-hud';
    hud.insertAdjacentHTML('beforeend', '<span id="__life-label">LIFE</span>');
    for (let i = 0; i < MAX_LIVES; i++) {
        hud.insertAdjacentHTML('beforeend', heartSvg('life-heart'));
    }
    document.body.appendChild(hud);

    // Hit flash
    const flash = document.createElement('div');
    flash.id = '__hit-flash';
    document.body.appendChild(flash);

    // Hit banner
    const banner = document.createElement('div');
    banner.id = '__hit-banner';
    document.body.appendChild(banner);

    // Death screen
    const deathScreen = document.createElement('div');
    deathScreen.id = '__death-screen';
    deathScreen.innerHTML =
        '<div id="__death-title">WRECKED</div>' +
        '<div id="__death-subtitle">Resetting in a moment...</div>' +
        '<div id="__death-hearts">' +
            heartSvg('death-heart-empty') +
            heartSvg('death-heart-empty') +
            heartSvg('death-heart-empty') +
        '</div>';
    document.body.appendChild(deathScreen);

    const hearts = hud.querySelectorAll('.life-heart');

    function updateHearts(lives) {
        hearts.forEach((h, i) => {
            if (i < lives) {
                h.classList.remove('lost');
            } else {
                h.classList.add('lost');
            }
        });
    }

    /* ── Public API ── */
    window.showCollisionBanner = function(livesLeft) {
        if (typeof livesLeft === 'number') currentLives = livesLeft;
        else currentLives = Math.max(0, currentLives - 1);
        updateHearts(currentLives);

        /* Shake the heart that was just lost */
        const lostIdx = currentLives;
        if (lostIdx < MAX_LIVES && hearts[lostIdx]) {
            hearts[lostIdx].classList.remove('hit');
            void hearts[lostIdx].offsetWidth;
            hearts[lostIdx].classList.add('hit');
        }

        /* Red vignette flash */
        flash.classList.remove('active');
        void flash.offsetWidth;
        flash.classList.add('active');
        setTimeout(() => flash.classList.remove('active'), 700);

        /* Impact text */
        const msgs = ['-1', 'OUCH', 'HIT!', 'CRASH'];
        banner.textContent = msgs[Math.floor(Math.random() * msgs.length)];
        banner.classList.remove('active');
        void banner.offsetWidth;
        banner.classList.add('active');
        setTimeout(() => banner.classList.remove('active'), 1100);
    };

    window.showDeathScreen = function() {
        currentLives = 0;
        updateHearts(0);
        deathScreen.classList.add('active');
    };

    window.hideDeathScreen = function(lives) {
        deathScreen.classList.remove('active');
        currentLives = (typeof lives === 'number') ? lives : MAX_LIVES;
        updateHearts(currentLives);
    };

    window.setLives = function(lives) {
        currentLives = lives;
        updateHearts(lives);
    };

    updateHearts(MAX_LIVES);
    console.log('Life system ready');
})();
"""

    # Ego-view HUD overlay JavaScript (uses client-side canvas, not server get_render)
    ego_view_js = """
(function() {
    if (window._egoViewInitialized) return;
    window._egoViewInitialized = true;

    const container = document.createElement('div');
    container.id = '__ego-view';
    container.style.cssText = `
        position: fixed; top: 10px; left: 10px; z-index: 10000;
        border: 2px solid rgba(255,255,255,0.3); border-radius: 8px;
        overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.4);
        background: #000; pointer-events: none;
    `;

    const cvs = document.createElement('canvas');
    cvs.id = '__ego-canvas';
    cvs.width = 480;
    cvs.height = 270;
    cvs.style.cssText = 'display:block; width:auto; height:20vh;';
    container.appendChild(cvs);

    const label = document.createElement('div');
    label.textContent = 'EGO CAM';
    label.style.cssText = `
        position:absolute; top:4px; left:8px;
        font-family:monospace; font-size:11px;
        color:rgba(255,255,255,0.7); background:rgba(0,0,0,0.5);
        padding:2px 6px; border-radius:4px;
    `;
    container.appendChild(label);
    document.body.appendChild(container);

    window.setEgoViewVisible = function(v) { container.style.display = v ? 'block' : 'none'; };
    console.log('📷 Ego view overlay ready (client-side rendering)');
})();
"""

    # JavaScript to inject into browser for keyboard capture
    keyboard_js = f"""
    (function() {{
        if (window._robotKeyboardInitialized) return;
        window._robotKeyboardInitialized = true;

        const keys = {{up: false, down: false, left: false, right: false, orbit_left: false, orbit_right: false, orbit_up: false, orbit_down: false}};
        const keyMap = {{
            'KeyI': 'up',
            'KeyK': 'down',
            'KeyJ': 'left',
            'KeyL': 'right',
            'ArrowUp': 'up',
            'ArrowDown': 'down',
            'ArrowLeft': 'left',
            'ArrowRight': 'right',
            'KeyA': 'orbit_left',
            'KeyD': 'orbit_right',
            'KeyW': 'orbit_up',
            'KeyS': 'orbit_down'
        }};

        let ws = null;

        function connect() {{
            const _proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(_proto + '//' + window.location.host + '/keyboard');

            ws.onopen = () => {{
                // Send init frame to link this WS to the viser session.
                // Poll until _viserClientId is injected (race-free).
                const sendInit = () => {{
                    if (window._viserClientId !== undefined) {{
                        ws.send(JSON.stringify({{type: 'init', session_id: window._viserClientId}}));
                        console.log('🎮 Keyboard linked to session', window._viserClientId);
                    }} else {{
                        setTimeout(sendInit, 50);
                    }}
                }};
                sendInit();
            }};
            ws.onerror = () => {{}};  // suppress console noise on retry
            ws.onclose = () => {{
                // Reset all keys so robot doesn't get stuck on disconnect
                for (const key in keys) keys[key] = false;
                // Reconnect after 1s
                setTimeout(connect, 1000);
            }};
        }}

        connect();

        document.addEventListener('keydown', (e) => {{
            const mapped = keyMap[e.code];
            if (mapped && !keys[mapped]) {{
                keys[mapped] = true;
                if (ws && ws.readyState === WebSocket.OPEN)
                    ws.send(JSON.stringify({{action: 'press', key: mapped}}));
                e.preventDefault();
            }}
        }});

        document.addEventListener('keyup', (e) => {{
            const mapped = keyMap[e.code];
            if (mapped && keys[mapped]) {{
                keys[mapped] = false;
                if (ws && ws.readyState === WebSocket.OPEN)
                    ws.send(JSON.stringify({{action: 'release', key: mapped}}));
                e.preventDefault();
            }}
        }});

        window.addEventListener('blur', () => {{
            for (const key in keys) {{
                if (keys[key]) {{
                    keys[key] = false;
                    if (ws && ws.readyState === WebSocket.OPEN)
                        ws.send(JSON.stringify({{action: 'release', key: key}}));
                }}
            }}
        }});

        console.log('🎮 Robot keyboard controller initialized');
    }})();
    """

    title_js = """
    document.title = 'Coco2Play';
    """

    def _inject_js(client: viser.ClientHandle) -> None:
        # Inject client_id first so keyboard JS can send the init frame.
        # Retry with increasing delays — the client may not be ready on the
        # very first attempt (WebSocket not yet established).
        client_id_js = f"window._viserClientId = {client.client_id};"
        for delay in [0.5, 1.0, 2.0, 4.0]:
            time.sleep(delay)
            try:
                client._websock_connection.queue_message(
                    viser_messages.RunJavascriptMessage(source=client_id_js)
                )
                client._websock_connection.queue_message(
                    viser_messages.RunJavascriptMessage(source=collision_banner_js)
                )
                client._websock_connection.queue_message(
                    viser_messages.RunJavascriptMessage(source=ego_view_js)
                )
                # Enable ego view rendering on client if checkbox is on
                if ego_view_cb.value:
                    client._websock_connection.queue_message(
                        viser_messages.RunJavascriptMessage(
                            source="window.__egoViewEnabled = true;"
                        )
                    )
                client._websock_connection.queue_message(
                    viser_messages.RunJavascriptMessage(source=keyboard_js)
                )
                client._websock_connection.queue_message(
                    viser_messages.RunJavascriptMessage(source=title_js)
                )
                # Hide life HUD if starting in Free Roam
                if game_mode.value == "Free Roam":
                    client._websock_connection.queue_message(
                        viser_messages.RunJavascriptMessage(
                            source="document.getElementById('__life-hud').style.display='none';"
                        )
                    )
            except Exception:
                continue  # retry on next delay instead of giving up

    # Inject JavaScript + create per-client session on connect
    @server.on_client_connect
    def on_client_connect(client: viser.ClientHandle) -> None:
        cid = client.client_id

        # Spawn at current scene's spawn point
        meta = load_scene_meta().get(current_scene_id, {})
        spawn = meta.get("spawn_point", {"x": 0.0, "y": 0.0, "z": 0.0})
        rs = RobotState()
        rs.x = spawn.get("x", 0.0)
        rs.y = 0.0  # flat floor, wheels on ground
        rs.z = spawn.get("z", 0.0)
        rs.yaw = math.pi / 2.0

        # Use server.scene with unique per-client paths so the robot shares
        # the same world coordinate system as the scene splats.
        # client.scene nodes use a separate coordinate origin in some viser
        # versions, causing misalignment with server.scene content.
        robot_root = f"/robot_{cid}"
        rf = server.scene.add_frame(robot_root, show_axes=False)
        rf.position = (rs.x, rs.y, rs.z)
        vu = ViserUrdf(
            server,
            urdf_or_path=ROBOT_URDF,
            root_node_name=f"{robot_root}/urdf",
            scale=1.0,
        )

        # Ego camera frustum (standalone scene node, updated each frame)
        _, pos0, wxyz0, _ = compute_ego_camera_pose(rs)
        ego_frust = server.scene.add_camera_frustum(
            f"/ego_cam_{cid}",
            fov=EGO_FOV_Y,
            aspect=EGO_ASPECT,
            scale=0.15,
            line_width=2.0,
            color=(0, 200, 255),
            wxyz=wxyz0,
            position=pos0,
            visible=False,  # toggled by GUI checkbox
        )

        sess = ClientSession(
            client_id=cid,
            robot_state=rs,
            robot_frame=rf,
            viser_urdf=vu,
        )
        sess.ego_frustum = ego_frust
        with sessions_lock:
            sessions[cid] = sess

        client.camera.fov = math.radians(fov_slider.value)
        threading.Thread(target=_inject_js, args=(client,), daemon=True).start()

    @server.on_client_disconnect
    def on_client_disconnect(client: viser.ClientHandle) -> None:
        cid = client.client_id
        with sessions_lock:
            sess = sessions.pop(cid, None)
        if sess is None:
            return

        # Remove per-client scene nodes from server scene.
        # URDF must be removed first so each child node's create message in
        # the persistent buffer is replaced by a remove message.  Then the
        # root frame removal only needs to handle itself.
        # Suppress "already removed" warnings — the URDF joint-frame cascade
        # marks mesh children as removed before the explicit mesh.remove().
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Attempted to remove already removed node")
            for label, node in [
                ("urdf", sess.viser_urdf),
                ("robot_frame", sess.robot_frame),
                ("ego_frustum", sess.ego_frustum),
            ]:
                if node is None:
                    continue
                try:
                    node.remove()
                except Exception as e:
                    print(f"  ⚠ Failed to remove {label} for client {cid}: {e}")

        # Force garbage collection to purge stale remove messages from the
        # persistent buffer.  The normal GC skips when message_event is set
        # (which the simulation loop keeps set), so without this the buffer
        # grows by ~86 messages per disconnect cycle.
        server._run_garbage_collector(force=True)

        print(f"✓ Cleaned up robot for client {cid}")

    with server.gui.add_folder("🗺️ Scene"):
        scene_names = {sid: scene_meta.get(sid, {}).get("name", sid) for sid in available_scenes}
        scene_dropdown = server.gui.add_dropdown(
            "Scene",
            options=list(scene_names.values()),
            initial_value=scene_names[available_scenes[0]],
        )

        @scene_dropdown.on_update
        def _(_) -> None:
            # Map display name back to scene_id
            name_to_id = {v: k for k, v in scene_names.items()}
            sid = name_to_id.get(scene_dropdown.value, available_scenes[0])
            load_scene(sid)

    with server.gui.add_folder("⚙️ Settings"):
        game_mode = server.gui.add_dropdown(
            "Game Mode",
            options=["Survival", "Free Roam"],
            initial_value="Survival",
        )

        @game_mode.on_update
        def _(_) -> None:
            is_free = game_mode.value == "Free Roam"
            # Reset lives and hide death screen when switching modes
            with sessions_lock:
                for sess in sessions.values():
                    sess.lives = 3
                    sess.dead = False
                    vc = server.get_clients().get(sess.client_id)
                    if vc is not None:
                        try:
                            js = "window.hideDeathScreen && window.hideDeathScreen(3);"
                            if is_free:
                                js += "document.getElementById('__life-hud').style.display='none';"
                            else:
                                js += "document.getElementById('__life-hud').style.display='flex';"
                            vc._websock_connection.queue_message(
                                viser_messages.RunJavascriptMessage(source=js)
                            )
                        except Exception:
                            pass

        fov_slider = server.gui.add_slider(
            "Camera FOV (°)",
            min=30,
            max=120,
            step=1,
            initial_value=75,
        )

        @fov_slider.on_update
        def _(_) -> None:
            fov_rad = math.radians(fov_slider.value)
            for client in server.get_clients().values():
                client.camera.fov = fov_rad

        max_speed_slider = server.gui.add_slider(
            "Max Speed (m/s)",
            min=0.5,
            max=10.0,
            step=0.5,
            initial_value=2.0,
        )
        collision_cb = server.gui.add_checkbox(
            "Enable Collision",
            initial_value=True,
        )
        camera_follow_cb = server.gui.add_checkbox(
            "Camera Follow Robot",
            initial_value=True,
        )
        speed_display = server.gui.add_number(
            "Speed (m/s)",
            initial_value=0.0,
            disabled=True,
            step=0.01,
        )

    with server.gui.add_folder("📷 Ego Camera"):
        ego_frustum_cb = server.gui.add_checkbox(
            "Show Frustum",
            initial_value=False,
        )

        @ego_frustum_cb.on_update
        def _(_) -> None:
            vis = ego_frustum_cb.value
            with sessions_lock:
                for sess in sessions.values():
                    if sess.ego_frustum is not None:
                        sess.ego_frustum.visible = vis

        ego_view_cb = server.gui.add_checkbox(
            "Ego View Overlay",
            initial_value=True,
        )

        @ego_view_cb.on_update
        def _(_) -> None:
            enabled = ego_view_cb.value
            vis_str = "true" if enabled else "false"
            for client in server.get_clients().values():
                try:
                    client._websock_connection.queue_message(
                        viser_messages.RunJavascriptMessage(
                            source=(
                                f"window.__egoViewEnabled = {vis_str};"
                                f"window.setEgoViewVisible && window.setEgoViewVisible({vis_str});"
                            )
                        )
                    )
                except Exception:
                    pass

    with server.gui.add_folder("🧱 Assets"):
        asset_count_slider = server.gui.add_slider(
            "Asset Count",
            min=0,
            max=20,
            step=1,
            initial_value=5,
        )
        randomize_btn = server.gui.add_button("🎲 Randomize Assets")

        @randomize_btn.on_click
        def _(_) -> None:
            name_to_id = {v: k for k, v in scene_names.items()}
            sid = name_to_id.get(scene_dropdown.value, available_scenes[0])
            scene_meta = load_scene_meta().get(sid, {})
            spawn = scene_meta.get("spawn_point", {"x": 0.0, "y": 0.0, "z": 0.0})
            region = scene_meta.get("asset_region")
            place_assets(spawn, count=int(asset_count_slider.value), asset_region=region)

    reset_button = server.gui.add_button("🔄 Reset Position")

    @reset_button.on_click
    def _(_) -> None:
        name_to_id = {v: k for k, v in scene_names.items()}
        sid = name_to_id.get(scene_dropdown.value, available_scenes[0])
        spawn = load_scene_meta().get(sid, {}).get("spawn_point", {"x": 0.0, "y": 0.0, "z": 0.0})
        with sessions_lock:
            for sess in sessions.values():
                _reset_session(sess, spawn)
                vc = server.get_clients().get(sess.client_id)
                if vc is not None:
                    try:
                        vc._websock_connection.queue_message(
                            viser_messages.RunJavascriptMessage(
                                source="window.hideDeathScreen && window.hideDeathScreen(3);"
                            )
                        )
                    except Exception:
                        pass

    # ── 5) Simulation Loop ──
    sim_dt = 1.0 / 30.0  # 30 Hz
    running = True

    def _step_session(sess: ClientSession, dt: float, now: float) -> None:
        """Advance one simulation tick for a single client session."""
        if sess.dead and game_mode.value == "Survival":
            return  # frozen while death screen is shown (survival only)
        rs = sess.robot_state
        ks = sess.key_state

        rs.max_speed = max_speed_slider.value

        throttle = 1.0 if ks["up"] else (-1.0 if ks["down"] else 0.0)
        steering = rs.max_steering if ks["left"] else (-rs.max_steering if ks["right"] else 0.0)

        new_x, new_z = rs.update(dt, throttle, steering)

        # Collision check (walls only — skipped in Free Roam so robot can push assets freely)
        collided = False
        is_survival = game_mode.value == "Survival"
        c_mesh = collision_state["mesh"]
        c_mgr  = collision_state["manager"]
        c_box  = collision_state["robot_box"]
        if is_survival and collision_cb.value and c_mesh is not None:
            if c_mgr is not None and c_box is not None:
                collided = check_collision_box_fcl(c_mgr, c_box, new_x, rs.y, new_z)
            else:
                collided = check_collision_box(c_mesh, new_x, rs.y, new_z, half_extents=box_half)

        if not collided:
            rs.x, rs.z = new_x, new_z
        else:
            rs.speed = 0.0
            rs.steering_velocity = 0.0
            rs.x -= 0.05 * math.cos(rs.yaw)
            rs.z -= 0.05 * math.sin(rs.yaw)
            if is_survival and not sess.dead and now - sess.last_collision_banner_time > 1.5:
                sess.last_collision_banner_time = now
                sess.lives = max(0, sess.lives - 1)
                viser_client = server.get_clients().get(sess.client_id)
                if viser_client is not None:
                    try:
                        if sess.lives > 0:
                            viser_client._websock_connection.queue_message(
                                viser_messages.RunJavascriptMessage(
                                    source=f"window.showCollisionBanner && window.showCollisionBanner({sess.lives});"
                                )
                            )
                        else:
                            # Dead — show death screen, then reset after delay
                            sess.dead = True
                            viser_client._websock_connection.queue_message(
                                viser_messages.RunJavascriptMessage(
                                    source="window.showCollisionBanner && window.showCollisionBanner(0); "
                                           "setTimeout(function(){ window.showDeathScreen && window.showDeathScreen(); }, 800);"
                                )
                            )
                            # Schedule reset after 3 seconds
                            def _delayed_reset(s=sess, vc=viser_client):
                                time.sleep(3.0)
                                name_to_id = {v: k for k, v in scene_names.items()}
                                sid = name_to_id.get(scene_dropdown.value, available_scenes[0])
                                spawn = load_scene_meta().get(sid, {}).get(
                                    "spawn_point", {"x": 0.0, "y": 0.0, "z": 0.0})
                                _reset_session(s, spawn)
                                s.lives = 3
                                s.dead = False
                                try:
                                    vc._websock_connection.queue_message(
                                        viser_messages.RunJavascriptMessage(
                                            source="window.hideDeathScreen && window.hideDeathScreen(3);"
                                        )
                                    )
                                except Exception:
                                    pass
                            threading.Thread(target=_delayed_reset, daemon=True).start()
                    except Exception:
                        pass

        # Update robot transform (per-client scene)
        base_rot    = tf.SO3.from_x_radians(np.pi / 2)
        mesh_rot_90 = tf.SO3.from_z_radians(np.pi / 2)
        yaw_rot     = tf.SO3.from_y_radians(np.pi / 2 - rs.yaw)
        sess.robot_frame.position = (rs.x, rs.y, rs.z)
        sess.robot_frame.wxyz     = (yaw_rot @ base_rot @ mesh_rot_90).wxyz

        joint_config = np.array([
            rs.steering_angle,
            rs.wheel_angle, rs.wheel_angle,
            rs.wheel_angle, rs.wheel_angle,
        ])
        sess.viser_urdf.update_cfg(joint_config)

        # ── Update ego camera frustum ──
        _, pos_ego, wxyz_frust, _ = compute_ego_camera_pose(rs)
        if sess.ego_frustum is not None:
            sess.ego_frustum.position = pos_ego
            sess.ego_frustum.wxyz = wxyz_frust

        # ── Asset push physics ──
        asset_hit = False
        with assets_lock:
            for asset in scene_assets:
                dx = asset.x - rs.x
                dz = asset.z - rs.z
                dist = math.sqrt(dx * dx + dz * dz)
                min_dist = ROBOT_COLLISION_RADIUS + asset.collision_radius

                if dist < min_dist and dist > 0.01:
                    asset_hit = True
                    # Resolve overlap
                    nx, nz = dx / dist, dz / dist
                    overlap = min_dist - dist
                    asset.x += nx * overlap
                    asset.z += nz * overlap
                    # Impulse from robot velocity
                    v_along = abs(rs.speed) * (
                        math.cos(rs.yaw) * nx + math.sin(rs.yaw) * nz
                    )
                    if v_along > 0:
                        impulse = v_along * 0.8 / asset.mass
                        asset.vx += nx * impulse
                        asset.vz += nz * impulse

        # Trigger life loss on asset collision (survival mode only)
        if asset_hit and game_mode.value == "Survival" and not sess.dead and now - sess.last_collision_banner_time > 1.5:
            sess.last_collision_banner_time = now
            sess.lives = max(0, sess.lives - 1)
            viser_client = server.get_clients().get(sess.client_id)
            if viser_client is not None:
                try:
                    if sess.lives > 0:
                        viser_client._websock_connection.queue_message(
                            viser_messages.RunJavascriptMessage(
                                source=f"window.showCollisionBanner && window.showCollisionBanner({sess.lives});"
                            )
                        )
                    else:
                        sess.dead = True
                        viser_client._websock_connection.queue_message(
                            viser_messages.RunJavascriptMessage(
                                source="window.showCollisionBanner && window.showCollisionBanner(0); "
                                       "setTimeout(function(){ window.showDeathScreen && window.showDeathScreen(); }, 800);"
                            )
                        )
                        def _delayed_reset_asset(s=sess, vc=viser_client):
                            time.sleep(3.0)
                            name_to_id = {v: k for k, v in scene_names.items()}
                            sid = name_to_id.get(scene_dropdown.value, available_scenes[0])
                            spawn = load_scene_meta().get(sid, {}).get(
                                "spawn_point", {"x": 0.0, "y": 0.0, "z": 0.0})
                            _reset_session(s, spawn)
                            s.lives = 3
                            s.dead = False
                            try:
                                vc._websock_connection.queue_message(
                                    viser_messages.RunJavascriptMessage(
                                        source="window.hideDeathScreen && window.hideDeathScreen(3);"
                                    )
                                )
                            except Exception:
                                pass
                        threading.Thread(target=_delayed_reset_asset, daemon=True).start()
                except Exception:
                    pass

            # Asset-asset collisions (simple pairwise)
            n_assets = len(scene_assets)
            for i in range(n_assets):
                a = scene_assets[i]
                for j in range(i + 1, n_assets):
                    b = scene_assets[j]
                    ddx = b.x - a.x
                    ddz = b.z - a.z
                    d = math.sqrt(ddx * ddx + ddz * ddz)
                    md = a.collision_radius + b.collision_radius
                    if d < md and d > 0.01:
                        nnx, nnz = ddx / d, ddz / d
                        ov = md - d
                        a.x -= nnx * ov * 0.5
                        a.z -= nnz * ov * 0.5
                        b.x += nnx * ov * 0.5
                        b.z += nnz * ov * 0.5

            # Apply friction and move assets
            for asset in scene_assets:
                decay = max(0.0, 1.0 - asset.friction * dt)
                asset.vx *= decay
                asset.vz *= decay
                asset.x += asset.vx * dt
                asset.z += asset.vz * dt
                # Constrain Y to ground level
                # Update visual
                asset.glb_handle.position = (asset.x, asset.y, asset.z)

        # Camera orbit (WASD adjusts per-session angle/elevation)
        if camera_follow_cb.value:
            orbit_speed, elev_speed = 120.0, 3.0
            if ks["orbit_left"]:  sess.camera_orbit_angle -= orbit_speed * dt
            if ks["orbit_right"]: sess.camera_orbit_angle += orbit_speed * dt
            if ks["orbit_up"]:    sess.camera_orbit_elevation = max(0.2, sess.camera_orbit_elevation - elev_speed * dt)
            if ks["orbit_down"]:  sess.camera_orbit_elevation = min(8.0, sess.camera_orbit_elevation + elev_speed * dt)

            follow_dist = 3.5
            orbit_rad = math.radians(sess.camera_orbit_angle)
            cam_x = rs.x - follow_dist * math.cos(orbit_rad)
            cam_z = rs.z - follow_dist * math.sin(orbit_rad)
            cam_y = rs.y - sess.camera_orbit_elevation

            viser_client = server.get_clients().get(sess.client_id)
            if viser_client is not None:
                with viser_client.atomic():
                    viser_client.camera.position     = (cam_x, cam_y, cam_z)
                    viser_client.camera.look_at      = (rs.x, rs.y, rs.z)
                    viser_client.camera.up_direction = (0.0, -1.0, 0.0)

    def simulation_loop() -> None:
        nonlocal running
        last_time = time.time()

        while running:
            now = time.time()
            dt = min(now - last_time, 0.1)
            last_time = now

            with sessions_lock:
                active = list(sessions.values())

            for sess in active:
                _step_session(sess, dt, now)

            # Speed display: show fastest active session (or 0)
            if active:
                speed_display.value = round(max(abs(s.robot_state.speed) for s in active), 2)

            time.sleep(sim_dt)

    def ego_pose_loop() -> None:
        """Send ego camera pose to clients at ~10 Hz for client-side rendering."""
        while running:
            time.sleep(0.1)  # 10 Hz pose updates
            if not ego_view_cb.value:
                continue
            with sessions_lock:
                active = list(sessions.values())
            for sess in active:
                try:
                    vc = server.get_clients().get(sess.client_id)
                    if vc is None:
                        continue
                    pos, _, wxyz_f, _ = compute_ego_camera_pose(sess.robot_state)
                    # Send pose to client — rendering happens in client's useFrame
                    # wxyz_f is OpenCV convention (Z-forward, Y-down); client applies
                    # the OpenCV→OpenGL pi rotation on X internally.
                    js = (
                        f"window.__egoPose={{x:{pos[0]},y:{pos[1]},z:{pos[2]},"
                        f"qx:{wxyz_f[1]},qy:{wxyz_f[2]},qz:{wxyz_f[3]},qw:{wxyz_f[0]},"
                        f"fov:{EGO_FOV_Y},width:{EGO_RENDER_W},height:{EGO_RENDER_H}}};"
                    )
                    vc._websock_connection.queue_message(
                        viser_messages.RunJavascriptMessage(source=js)
                    )
                except Exception:
                    pass

    sim_thread = threading.Thread(target=simulation_loop, daemon=True)
    sim_thread.start()
    ego_thread = threading.Thread(target=ego_pose_loop, daemon=True)
    ego_thread.start()
    print("✓ Simulation loop started (30 Hz)")
    print("✓ Ego pose updates started (10 Hz)")
    print("\n" + "=" * 60)
    print(f"  Viewer ready! Open http://localhost:{PUBLIC_PORT}")
    print("  Click in the browser window first to capture keys!")
    print("=" * 60 + "\n")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        running = False
        print("\nShutting down...")


if __name__ == "__main__":
    main()
