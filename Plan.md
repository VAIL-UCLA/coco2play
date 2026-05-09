# Implementation Plan

## Feature 1: Ego Camera

### Sub-feature 1a: Camera Frustum Visualization

**Key parameters:**
- fx = fy = 650, cx = 960 (1920/2), cy = 540 (1080/2)
- fov_y = 2 * atan(540 / 650) ≈ 79.4° ≈ 1.387 rad
- aspect = 1920 / 1080 ≈ 1.778

**Steps:**
1. Add `ego_frustum` handle to `ClientSession.__slots__`
2. In `on_client_connect`, create a camera frustum via `server.scene.add_camera_frustum()` at path `/robot_{cid}/ego_cam`
   - Being a child of the robot frame means position/rotation are in robot-local space
   - Position at front of robot: need to figure out local-space coords after the base_rot + mesh_rot_90 transform
   - Alternative: add frustum as a standalone scene node and update its world pose each frame in `_step_session`
3. Compute ego camera world pose from robot state each frame:
   - Forward direction: `(cos(yaw), 0, sin(yaw))`
   - Camera position: robot_pos + forward * 0.35 + up * 0.15 (front of robot, slightly above)
   - Camera orientation: OpenCV convention (+Z forward, +X right, +Y down)
   - Convert rotation matrix to quaternion via `tf.SO3.from_matrix()`
4. Add GUI checkbox "Show Ego Frustum" in Settings folder
5. Toggle frustum `.visible` on checkbox change

### Sub-feature 1b: Ego View Display

**Approach:** Server-side rendering via `client.get_render()` + JavaScript HUD overlay

**Steps:**
1. Inject CSS/HTML for a fixed overlay `<div>` in the top-left corner (25% of window height)
2. In simulation loop, every ~5 frames (6 Hz), call `client.get_render(height=270, width=480, wxyz=ego_wxyz, position=ego_pos, fov=fov_y)` for each client
3. Encode returned numpy array as JPEG → base64 data URL
4. Send to client via `RunJavascriptMessage` to update the overlay `<img>` src
5. Handle errors gracefully (skip frame if render fails)

---

## Feature 2: Scene Assets (Obstacles)

### Step 1: Asset Scale Calibration

**Problem:** GLB files have wildly inconsistent scales (some in mm, some in cm, some in meters).

**Approach:**
- Write a script to compute per-file scale factors
- For each file: load GLB, get bounding box max dimension, compute `scale = target_max / raw_max`
- Target max dimensions per category based on real-world sizes:
  - trafficcone: 0.75m, Trash_bin: 0.8m, TrashCan: 0.8m
  - Bollard: 1.0m, Chair: 0.85m, Bonsai: 0.5m, Bag: 0.4m
  - Scooter: 1.5m, Table: 1.0m, Mailbox: 1.2m, etc.
- Save as `data/objects/calibration.json` with format:
  ```json
  {
    "filename.glb": {"scale": 0.003, "category": "trafficcone", "collision_radius": 0.15}
  }
  ```

### Step 2: Asset Data Structure

- New `SceneAsset` class: position (x, y, z), velocity (vx, vz), collision_radius, mass, glb_handle, scale
- List of active assets per scene
- Lock for thread safety

### Step 3: Asset Placement

- On scene load, randomly select 5-10 assets from pushable categories
- Place them at random positions near the spawn area (within some radius)
- Set Y to spawn height (ground plane)
- Load GLB bytes and call `server.scene.add_glb()` with computed scale

### Step 4: Ground Plane

- Add an invisible large mesh plane at robot spawn Y height
- Used as physics reference for keeping assets grounded
- Make it invisible (visible=False) and large enough to cover the playable area
- Ensure scene mesh and ground plane don't interfere (they won't since ground plane is invisible and only used for Y reference)

### Step 5: Push Physics (Simplified)

- In `_step_session`, after robot movement:
  1. For each asset, check distance to robot in XZ plane
  2. If distance < robot_radius + asset_collision_radius → collision
  3. Compute push direction = normalize(asset_pos - robot_pos) in XZ
  4. Apply impulse to asset velocity based on robot speed and direction
  5. Apply friction decay to asset velocity each frame
  6. Move asset by velocity * dt
  7. Check asset-scene collision via raycast (keep on ground, stop at walls)
  8. Update GLB handle position
- Robot collision radius ≈ 0.35 (from box half-extent)

### Step 6: GUI Controls

- Add "Assets" folder in GUI with:
  - Button to randomize asset placement
  - Number slider for asset count
