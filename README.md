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
- **Single-Port Architecture** — aiohttp proxy on port 1234 (Apache reverse-proxy friendly)

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
└── scripts/upload-pointclouds.sh   # Optional: publish PLYs to GitHub Releases
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
| Show Predicted Path | Green centerline for the model's predicted trajectory |
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
4. Overlays the predicted path on the ground as a green centerline (**Show Predicted Path**)

Force CPU on misconfigured GPU servers: `export COCO_AUTOPILOT_DEVICE=cpu`

---

## Deployment

Coco2Play needs an always-on server (unlike the static [coco-playground](https://vail-ucla.github.io/coco-playground/) demo).

**Live demo:** [http://bolei-gpu07.cs.ucla.edu/coco2play/](http://bolei-gpu07.cs.ucla.edu/coco2play/) (UCLA lab GPU host)

The viewer runs as a long-lived Python process on port **1234**. Apache reverse-proxies HTTP and WebSocket traffic on port 80. On shared lab hosts, Coco2Play is served at the **`/coco2play/`** subpath so other apps can keep the site root.

### 1. systemd service

Create `/etc/systemd/system/coco2play.service` (adjust paths to your install):

```ini
[Unit]
Description=Coco2Play Viser server (port 1234)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=krystof
WorkingDirectory=/home/krystof/github/coco2play
Environment=PATH=/home/krystof/miniconda3/bin:/usr/bin:/bin
Environment=PYTHONUNBUFFERED=1
Environment=COCO_BASE_PATH=/coco2play
Environment=COCO_AUTOPILOT_DEVICE=auto
ExecStart=/home/krystof/miniconda3/bin/python viewer.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now coco2play
```

`COCO_BASE_PATH=/coco2play` is required for subpath hosting — the app serves all routes under `/coco2play/`.

Verify locally (use GET, not `curl -I` — HEAD is not supported by Viser):

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:1234/coco2play/
```

### 2. Apache reverse proxy

Enable proxy modules once:

```bash
sudo a2enmod proxy proxy_http proxy_wstunnel rewrite headers
sudo systemctl restart apache2
```

Add the following inside your existing `<VirtualHost *:80>` (keeps the default site at `/` for other apps):

```apache
    ProxyPreserveHost On
    ProxyRequests Off
    ProxyTimeout 3600

    RewriteEngine On
    RewriteCond %{HTTP:Upgrade} =websocket [NC]
    RewriteRule ^/coco2play/?(.*) ws://127.0.0.1:1234/coco2play/$1 [P,L]

    ProxyPass        /coco2play/ http://127.0.0.1:1234/coco2play/
    ProxyPassReverse /coco2play/ http://127.0.0.1:1234/coco2play/
```

```bash
sudo apache2ctl configtest
sudo systemctl reload apache2
```

Open **http://bolei-gpu07.cs.ucla.edu/coco2play/** (trailing slash recommended).

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| 502 Bad Gateway | `sudo systemctl status coco2play` |
| Subpath 404 | `COCO_BASE_PATH` must be `/coco2play` and match the Apache path |
| Keyboard dead | Click the viewport; check WebSocket proxy rules |
| Old page in browser | Hard refresh or incognito (cache) |

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
