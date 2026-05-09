# Coco2Play — Interactive 3DGS Robot Viewer

An interactive web-based viewer for driving a robot through photorealistic 3D Gaussian Splatting scenes with real-time collision physics.

## Features

- **3D Gaussian Splatting** — Photorealistic scene rendering via [Viser](https://github.com/viser-project/viser)
- **Drivable Robot** — Full URDF robot (coco_one) with textured body mesh recovered from GLB
- **Collision Detection** — FCL box-vs-mesh collision with fallback to trimesh ray casting
- **Bicycle Kinematics** — Realistic steering, wheel roll animation, and speed dynamics
- **Collision Banner** — Dramatic animated "COLLISION!" overlay on impact
- **Camera Orbit** — Follow-cam with adjustable elevation and orbit angle
- **Ego Camera** — First-person camera view from the robot with HUD overlay and optional frustum visualization
- **Pushable Assets** — Calibrated GLB objects placed in the scene that respond to robot collisions with real-time physics
- **Multi-Client Sessions** — Each browser tab gets its own robot; disconnected robots are automatically cleaned up from the scene
- **Multi-Scene** — Switch scenes at runtime; each has its own spawn point and rotation defined in `data/meta.json`
- **Bandwidth Optimised** — Collision mesh kept server-side only (~53 MB saved per client connection), suitable for Cloudflare tunnels
- **Single-Port Architecture** — aiohttp proxy on port 1234 routes keyboard WebSocket (`/keyboard`) and all Viser traffic through one port, Cloudflare-tunnel-compatible

---

## Project Structure

```
.
├── viewer.py                  # Main application (Viser server + TCP proxy + sim loop)
├── data/
│   ├── meta.json              # Scene metadata (name, spawn_point, scene_rotation)
│   ├── objects/
│   │   ├── calibration.json   # Asset scale, collision radius, and physics metadata
│   │   └── *.glb              # Pushable 3D asset meshes
│   ├── scene1/
│   │   ├── point_cloud.ply    # 3DGS point cloud (NOT committed — hosted as a GitHub Release asset)
│   │   └── mesh.ply           # Collision mesh (Git LFS)
│   └── scene2/  ...
├── urdf/
│   ├── coco_one.urdf          # Robot description
│   ├── meshes/
│   │   ├── Back_Graphic.obj   # Textured body mesh (extracted from coco-gradient.glb)
│   │   ├── Back_Graphic.mtl   # Material definitions
│   │   ├── base_link.obj      # Chassis mesh
│   │   ├── front_axle_link.obj
│   │   └── *_wheel_link.obj   # Wheel meshes (x4)
│   └── textures/              # PNG textures extracted from GLB
│       ├── Body.png
│       ├── Back-Graphic.png
│       ├── LID.png
│       └── coco-white.png
├── web/                       # Static GitHub Pages app (Three.js + GaussianSplats3D)
│   ├── src/main.js
│   ├── index.html
│   └── package.json
└── .github/workflows/
    └── deploy.yml             # Auto-deploy to GitHub Pages on push to main
```

---

## Quick Start

### Prerequisites

```bash
pip install viser trimesh plyfile numpy
```

Optional — full FCL box-vs-mesh collision:

```bash
pip install python-fcl
```

### Run

```bash
python viewer.py
```

Opens on **http://localhost:1234**

---

## Controls

| Key | Action |
|:---:|:-------|
| **I / ↑** | Drive forward |
| **K / ↓** | Drive backward |
| **J / ←** | Steer left |
| **L / →** | Steer right |
| **A / D** | Orbit camera left / right |
| **W / S** | Tilt camera up / down |

### GUI Panel

| Control | Description |
|---------|-------------|
| Scene dropdown | Switch between scenes (resets robot to spawn) |
| Camera FOV | Adjust field of view (30°–120°) |
| Max Speed | Cap robot top speed (0.5–10 m/s) |
| Enable Collision | Toggle collision detection |
| Camera Follow Robot | Toggle follow-cam |
| Ego Camera View | Toggle first-person camera from robot |
| Show Ego Frustum | Show/hide camera frustum wireframe |
| Show Mesh | Visualise the hidden collision mesh |
| Reset Position | Teleport robot back to spawn point |

---

## Scene Configuration (`data/meta.json`)

Each scene entry supports:

```json
{
  "scene1": {
    "name": "Scene 1",
    "spawn_point": { "x": -1.0, "y": 0.0, "z": -17.0 },
    "scene_rotation": { "x": 0.0, "y": 0.0, "z": 0.0 }
  }
}
```

- **`spawn_point`** — Robot start position (world coordinates)
- **`scene_rotation`** — XYZ Euler angles in degrees applied to the `/scene` frame (rotates splats + collision mesh together)

Changes take effect on the next scene switch — **no server restart required**.

---

## Sharing Publicly — Cloudflare Tunnel

The server uses a single-port architecture (port 1234) so one tunnel exposes everything — the Viser UI, keyboard WebSocket, and collision banner.

```bash
# Terminal 1 — start the server
python viewer.py

# Terminal 2 — expose publicly
cloudflared tunnel --url http://localhost:1234
```

Share the generated `*.trycloudflare.com` URL. Keyboard control works remotely because the keyboard WebSocket is routed through the same port via the built-in TCP proxy.

---

## Server Architecture

```
Browser / Cloudflare
        │
        ▼
  aiohttp Proxy  :1234
    ├── WS /keyboard  →  keyboard WebSocket handler  (inline)
    └── everything else →  bidirectional proxy to Viser :1235
                                    │
                                    ▼
                           Viser Server :1235
                         (HTTP + WebSocket + 3DGS)
```

The aiohttp proxy forwards HTTP and WebSocket traffic to Viser, preserving all headers (including `Sec-WebSocket-Protocol` for version negotiation). The `/keyboard` path is intercepted and handled inline for low-latency key input.

---

## GitHub Pages Deployment

The `web/` directory contains a static Three.js app that mirrors the viewer's features and runs entirely in the browser.

### How big assets are split

GitHub Pages caps individual files at **100 MB** and the published site at **1 GB**, which the per-scene point clouds (200–400 MB each) blow past. So:

| Asset | Size | Where it lives |
|-------|------|----------------|
| `data/scene*/point_cloud.ply` | 200–400 MB | **GitHub Release asset** — fetched by the web client at runtime |
| `data/scene*/mesh.ply` | 19–72 MB | Git LFS, bundled into the Pages build |
| `urdf/meshes/*.obj` | 3–44 MB | Git LFS, bundled into the Pages build |
| `urdf/textures/*.png`, JSON, code | KB-range | Plain Git, bundled into the Pages build |

The web client reads `data/meta.json` `_data_sources.point_cloud_url_template` and substitutes `{scene_id}` to build the download URL. To switch hosts (e.g. R2, S3) just change that one string.

### Setup (one time)

```bash
git lfs install
git remote add coco2play git@github.com:VAIL-UCLA/coco2play.git
git push -u coco2play main          # commits + LFS objects (~95 MB)
```

### Publish point clouds to a GitHub Release

```bash
# Defaults to the tag set in data/meta.json (_data_sources.tag).
# Requires the GitHub CLI (`gh auth login`) and `jq`.
scripts/upload-pointclouds.sh                # uses tag "data-v1"
scripts/upload-pointclouds.sh data-v2        # cut a new tag
```

If you change the tag, also update `_data_sources.tag` and the URL template in `data/meta.json` so the web client points at the new release.

### Enable GitHub Pages

In **Settings → Pages**, set source to the `gh-pages` branch. GitHub Actions deploys automatically on every push to `main`.

Live URL: **https://vail-ucla.github.io/coco2play/**

---

## Adding a New Scene

1. Create `data/<scene_id>/` with `point_cloud.ply` and `mesh.ply`
2. Add an entry to `data/meta.json`:
   ```json
   "scene3": {
     "name": "My New Scene",
     "spawn_point": { "x": 0.0, "y": 0.0, "z": 0.0 },
     "scene_rotation": { "x": 0.0, "y": 0.0, "z": 0.0 }
   }
   ```
3. Commit `mesh.ply` (Git LFS picks it up automatically); `point_cloud.ply` stays on disk only.
4. Run `scripts/upload-pointclouds.sh` to push the new point cloud to the GitHub Release.
5. Select the scene from the dropdown — no restart needed.

---

## Asset Calibration (`data/objects/calibration.json`)

Each pushable GLB asset has calibration metadata:

| Field | Description |
|-------|-------------|
| `scale` | Uniform scale factor: `calibrated_height / raw_size[1]` |
| `raw_size` | Bounding box `[X, Y, Z]` of the original GLB mesh |
| `calibrated_height` | Target real-world height in metres |
| `collision_radius` | Physics collision radius for push interactions |
| `pushable` | Whether the robot can push this asset |

The scale normalises each asset's Y-axis (height) to match its `calibrated_height` in metres.

---

## License

MIT
