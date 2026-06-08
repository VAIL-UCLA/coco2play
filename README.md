# Coco2Play — Interactive 3DGS Robot Viewer

An interactive web-based viewer for driving the [COCO](https://www.cocodelivery.com/) delivery robot through photorealistic 3D Gaussian Splatting (3DGS) scenes with real-time collision physics, ego recording, and learned autopilot.

Part of **COCOVerse**, a UCLA capstone project that also includes the stylized sandbox [coco-playground](https://github.com/VAIL-UCLA/coco-playground) (Sketchbook fork, GTA-style mesh world).

## Features

- **3D Gaussian Splatting** — Photorealistic scene rendering via [Viser](https://github.com/viser-project/viser)
- **Drivable Robot** — Full URDF robot (`coco_one`) with textured body mesh recovered from GLB
- **Collision Detection** — FCL box-vs-mesh collision with fallback to trimesh ray casting
- **Bicycle Kinematics** — Spring-damper steering, wheel roll animation, and speed dynamics
- **Ego Camera** — First-person view with optional frustum visualization
- **Observation Recording** — Ego-view JPEGs plus drive controls to `recordings/` (4 Hz, press **R**)
- **Autopilot** — Closed-loop **Coco-GL-SW1k** ONNX navigation (`models/coco_navigator.py`)
- **Pushable Assets** — Calibrated GLB objects with real-time push physics
- **Multi-Client Sessions** — Each browser tab gets its own robot; cleanup on disconnect
- **Multi-Scene** — Ten scenes with per-scene spawn and rotation in `data/meta.json`
- **Bandwidth Optimised** — Collision mesh server-side only (~53 MB saved per client)
- **Single-Port Architecture** — aiohttp proxy on port 1234 (Cloudflare-tunnel-friendly)

---

## Project Structure

```
.
├── viewer.py                  # Main application (Viser + proxy + sim + autopilot)
├── models/
│   ├── coco_navigator.py      # Coco-GL-SW1k ONNX wrapper
│   └── Coco_GL_SW1k/          # coco.onnx (not committed — see README there)
├── data/
│   ├── meta.json              # Scene metadata (name, spawn_point, scene_rotation)
│   ├── objects/               # Pushable GLB assets + calibration.json
│   └── scene*/                # point_cloud.ply (local) + mesh.ply (Git LFS)
├── urdf/                      # coco_one.urdf + meshes
├── scripts/upload-pointclouds.sh   # Optional: publish PLYs to GitHub Releases
├── deploy/                    # Cloudflare Tunnel + systemd templates
└── docs/                      # capstone report, DEPLOYMENT.md
```

---

## Quick Start

### Prerequisites

```bash
pip install viser trimesh plyfile numpy aiohttp
pip install python-fcl          # optional, preferred collision
```

**Autopilot (optional):**

```bash
pip install onnxruntime torch pyyaml        # macOS / CPU
pip install onnxruntime-gpu torch pyyaml    # Linux + NVIDIA GPU
```

Copy `coco.onnx` into `models/Coco_GL_SW1k/` (see `models/Coco_GL_SW1k/README.md`).

### Scene data

Each `data/sceneN/` needs both:

- `point_cloud.ply` — 3DGS splats (~200–800 MB, **not in git**; keep on disk)
- `mesh.ply` — collision mesh (Git LFS)

### Run

```bash
python viewer.py
```

Open **http://localhost:1234** in your browser. Click the viewport to capture keyboard focus.

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
| **R** | Toggle observation recording |

### GUI

| Control | Description |
|---------|-------------|
| Scene dropdown | Switch scenes (resets robot to spawn) |
| Autopilot | Coco-GL-SW1k closed-loop driving (Ego Camera panel) |
| Ego / Frustum | First-person view and camera frustum viz |
| Record Observations | Start/stop recording for all clients |
| Survival / Free Roam | Collision + lives vs. open exploration |
| Reset Position | Teleport robot to spawn |

---

## Observation Recording

Episodes are saved under `recordings/`:

```
recordings/scene1_20260601_143022_3/
  meta.json
  manifest.jsonl
  frames/000000.jpg ...
```

Captured at **4 Hz** from the ego-locked viewport (960×540 JPEG + pose + controls).

---

## Autopilot

Enable **Autopilot (Coco-GL-SW1k)** in the Ego Camera panel. The server:

1. Captures ego viewport frames at 5 Hz from the browser (no `get_render()` re-render)
2. Runs ONNX inference (`CocoNavigator`)
3. Maps $(v, \omega)$ to throttle/steering at 30 Hz physics

Force CPU on misconfigured GPU servers: `export COCO_AUTOPILOT_DEVICE=cpu`

---

## Public hosting (stable URL)

Coco2Play needs a always-on server (unlike the static [coco-playground](https://vail-ucla.github.io/coco-playground/) demo). For a **stable VAIL URL**, use a free **named Cloudflare Tunnel** pointing at port 1234.

**Full guide:** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

```bash
# One-time: create tunnel + DNS (e.g. coco2play.vail.ucla.edu)
cloudflared tunnel login
cloudflared tunnel create coco2play
cloudflared tunnel route dns coco2play coco2play.vail.ucla.edu
cp deploy/cloudflared/config.yml.example ~/.cloudflared/config.yml  # edit UUID + hostname

# Run
python viewer.py
cloudflared tunnel run coco2play
```

Templates for `systemd` are in `deploy/systemd/`.

**Ad-hoc demo** (URL changes every run):

```bash
python viewer.py
cloudflared tunnel --url http://localhost:1234
```

---

## Server Architecture

```
Browser
   │
   ▼
aiohttp Proxy :1234
   ├── WS /keyboard  →  keyboard + recording frames
   └── /*            →  Viser :1235 (3DGS + URDF + GUI)
```

Collision meshes are loaded server-side only and never streamed to clients.

---

## Large Assets

| Asset | Storage |
|-------|---------|
| `point_cloud.ply` | Local disk (gitignored) |
| `mesh.ply` | Git LFS |
| `coco.onnx` | Local (`models/Coco_GL_SW1k/`, gitignored) |

To share point clouds with collaborators without committing them:

```bash
scripts/upload-pointclouds.sh [tag]   # publishes to GitHub Releases
```

---

## Adding a New Scene

1. Create `data/<scene_id>/` with `point_cloud.ply` and `mesh.ply`
2. Add an entry to `data/meta.json` (`name`, `spawn_point`, `scene_rotation`)
3. Commit `mesh.ply` (LFS); keep `point_cloud.ply` local
4. Select the scene from the dropdown — no restart needed

---

## License

MIT
