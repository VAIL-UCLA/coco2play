import * as THREE from 'three';
import { PLYLoader } from 'three/addons/loaders/PLYLoader.js';
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';

// ─────────────────────────────────────────────
//  Loading overlay helpers
// ─────────────────────────────────────────────

const loadingOverlay = document.getElementById('loading-overlay');
const loadingStatus  = document.getElementById('loading-status');

function setStatus(msg) {
  if (loadingStatus) loadingStatus.textContent = msg;
}

function hideOverlay() {
  if (loadingOverlay) loadingOverlay.classList.add('hidden');
}

function showOverlay() {
  if (loadingOverlay) loadingOverlay.classList.remove('hidden');
}

// ─────────────────────────────────────────────
//  Collision banner (ported exactly from Python)
// ─────────────────────────────────────────────

function injectCollisionBanner() {
  const style = document.createElement('style');
  style.textContent = `
    @keyframes __cb-slam {
      0%   { opacity:0; transform: translateY(-90px) scaleY(2.2); }
      8%   { opacity:1; transform: translateY(7px) scaleY(0.86); }
      13%  { transform: translateY(-3px) scaleY(1.05); }
      18%  { transform: translateY(0) scaleY(1); }
      20%  { transform: translateX(-7px) rotate(-0.6deg); }
      22%  { transform: translateX(7px) rotate(0.6deg); }
      24%  { transform: translateX(-5px) rotate(-0.4deg); }
      26%  { transform: translateX(5px) rotate(0.4deg); }
      28%  { transform: translateX(-2px); }
      30%  { transform: translateX(0); }
      68%  { opacity:1; transform: translateY(0); }
      100% { opacity:0; transform: translateY(-50px); }
    }
    @keyframes __cb-glitch1 {
      0%,90%,100% { clip-path:polygon(0 20%,100% 20%,100% 42%,0 42%); transform:translateX(-3px); }
      10%          { clip-path:polygon(0 30%,100% 30%,100% 50%,0 50%); transform:translateX(-7px); }
      50%          { clip-path:polygon(0 55%,100% 55%,100% 72%,0 72%); transform:translateX(-5px); }
    }
    @keyframes __cb-glitch2 {
      0%,85%,100% { clip-path:polygon(0 58%,100% 58%,100% 80%,0 80%); transform:translateX(3px); }
      15%          { clip-path:polygon(0 40%,100% 40%,100% 58%,0 58%); transform:translateX(6px); }
      60%          { clip-path:polygon(0 10%,100% 10%,100% 28%,0 28%); transform:translateX(5px); }
    }
    @keyframes __cb-pulse {
      0%,100% { box-shadow: 0 0 28px rgba(255,60,0,.65), 0 0 60px rgba(255,30,0,.3), inset 0 1px 0 rgba(255,160,0,.25); }
      50%      { box-shadow: 0 0 48px rgba(255,80,0,.9), 0 0 90px rgba(255,40,0,.5), inset 0 1px 0 rgba(255,180,0,.4); }
    }
    #__cb-overlay {
      position: fixed;
      top: 0; left: 0; right: 0;
      z-index: 2147483647;
      pointer-events: none;
      display: flex;
      justify-content: center;
      padding-top: 14px;
      opacity: 0;
      will-change: opacity, transform;
    }
    #__cb-overlay.active {
      animation: __cb-slam 1.9s cubic-bezier(.23,1,.32,1) forwards;
    }
    #__cb-inner {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 14px;
      padding: 8px 36px 11px;
      background: linear-gradient(135deg,
        rgba(18,2,2,.95) 0%,
        rgba(90,12,2,.95) 50%,
        rgba(18,2,2,.95) 100%);
      border: 2px solid #ff2800;
      border-top: 4px solid #ff7000;
      clip-path: polygon(10px 0%,calc(100% - 10px) 0%,100% 10px,100% calc(100% - 10px),calc(100% - 10px) 100%,10px 100%,0% calc(100% - 10px),0% 10px);
      animation: __cb-pulse 0.4s ease-in-out infinite;
    }
    #__cb-inner::after {
      content: '';
      position: absolute; inset: 0;
      background: repeating-linear-gradient(0deg,transparent 0px,transparent 2px,rgba(0,0,0,.12) 2px,rgba(0,0,0,.12) 3px);
      pointer-events: none;
    }
    #__cb-stripe-l, #__cb-stripe-r {
      width: 22px; height: 40px; flex-shrink: 0;
      background: repeating-linear-gradient(45deg,#ff4400 0px,#ff4400 4px,#1a0000 4px,#1a0000 9px);
      opacity: .85;
    }
    #__cb-text {
      font-family: 'Impact', 'Arial Black', 'Franklin Gothic Heavy', sans-serif;
      font-size: 44px;
      font-weight: 900;
      letter-spacing: .14em;
      color: #fff;
      text-transform: uppercase;
      text-shadow: 0 0 8px #ff5500, 0 0 24px #ff2200, 2px 2px 0 #990000, -1px -1px 0 #ff7700;
      position: relative;
    }
    #__cb-text::before, #__cb-text::after {
      content: 'COLLISION!';
      position: absolute; top:0; left:0; right:0;
    }
    #__cb-text::before {
      color: #ff0044;
      opacity: .7;
      animation: __cb-glitch1 0.28s steps(1) infinite;
    }
    #__cb-text::after {
      color: #00eeff;
      opacity: .45;
      animation: __cb-glitch2 0.38s steps(1) infinite;
    }
  `;
  document.head.appendChild(style);

  const overlay = document.createElement('div');
  overlay.id = '__cb-overlay';
  overlay.innerHTML = `
    <div id="__cb-inner">
      <div id="__cb-stripe-l"></div>
      <div id="__cb-text">COLLISION!</div>
      <div id="__cb-stripe-r"></div>
    </div>`;
  document.body.appendChild(overlay);

  let _hideTimer = null;
  window.showCollisionBanner = function () {
    if (_hideTimer) { clearTimeout(_hideTimer); _hideTimer = null; }
    overlay.classList.remove('active');
    void overlay.offsetWidth; // reflow to restart animation
    overlay.classList.add('active');
    _hideTimer = setTimeout(() => {
      overlay.classList.remove('active');
      _hideTimer = null;
    }, 1900);
  };
}

injectCollisionBanner();

// ─────────────────────────────────────────────
//  Three.js renderer + scene
// ─────────────────────────────────────────────

const container = document.getElementById('canvas-container');

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(container.clientWidth, container.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const threeScene = new THREE.Scene();
threeScene.background = new THREE.Color(0x0a0a0a);

const camera = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 0.01, 1000);
camera.position.set(0, -2, -5);
camera.lookAt(0, 0, 0);

window.addEventListener('resize', () => {
  const w = container.clientWidth;
  const h = container.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
});

// Ambient + directional light for the robot mesh
threeScene.add(new THREE.AmbientLight(0xffffff, 0.6));
const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
dirLight.position.set(5, -10, 5);
threeScene.add(dirLight);

// ─────────────────────────────────────────────
//  Gaussian Splats viewer (selfDrivenMode:false)
// ─────────────────────────────────────────────

const gsViewer = new GaussianSplats3D.Viewer({
  selfDrivenMode: false,
  renderer,
  camera,
  useBuiltInControls: false,
  sharedMemoryForWorkers: false,
});

// ─────────────────────────────────────────────
//  Scene group (holds splats transform + mesh)
// ─────────────────────────────────────────────

const sceneGroup = new THREE.Group();
threeScene.add(sceneGroup);

// ─────────────────────────────────────────────
//  Robot state — bicycle model (mirroring Python)
// ─────────────────────────────────────────────

const state = {
  x: 0, y: 0, z: 0,
  yaw: Math.PI / 2,
  speed: 0,
  steer: 0,          // actual steering angle (rad)
  steerVel: 0,       // steering spring velocity
  wheelAngle: 0,     // cumulative rolling angle
};

const PARAMS = {
  maxSpeed:    4.0,
  maxReverse:  1.5,
  accel:       3.0,
  brake:       6.0,
  drag:        2.5,
  maxSteer:    0.45,
  steerRate:   2.5,
  steerReturn: 3.0,
  wheelbase:   0.5,
  wheelRadius: 0.11,
  // Spring steering (matching Python's spring-damper approach)
  steerStiffness: 60.0,
  steerDamping:   10.0,
  steerMass:      0.6,
};

function updateRobotState(dt, throttle, steerInput) {
  // Spring-damper steering (like Python's SpringSimulator)
  const steerTarget = Math.max(-PARAMS.maxSteer, Math.min(PARAMS.maxSteer, steerInput));
  const steerForce = PARAMS.steerStiffness * (steerTarget - state.steer)
                   - PARAMS.steerDamping   * state.steerVel;
  state.steerVel += (steerForce / PARAMS.steerMass) * dt;
  state.steer    += state.steerVel * dt;
  state.steer     = Math.max(-PARAMS.maxSteer, Math.min(PARAMS.maxSteer, state.steer));
  if (Math.abs(state.steer) >= PARAMS.maxSteer - 1e-6) state.steerVel *= 0.9;

  // Speed — direct throttle with drag (simplified from Python)
  const topSpeed = throttle >= 0 ? PARAMS.maxSpeed : PARAMS.maxReverse;
  const speedTarget = throttle * topSpeed;
  const accelRate = Math.abs(throttle) > 0.01 ? PARAMS.accel : PARAMS.brake;
  const speedDiff = speedTarget - state.speed;
  state.speed += Math.sign(speedDiff) * Math.min(Math.abs(speedDiff), accelRate * dt);
  // drag when coasting
  if (Math.abs(throttle) < 0.01) {
    state.speed *= Math.max(0, 1 - PARAMS.drag * dt);
    if (Math.abs(state.speed) < 0.001) state.speed = 0;
  }

  if (Math.abs(state.speed) < 1e-4) {
    state.steerVel *= 0.9;
    return { newX: state.x, newZ: state.z };
  }

  // Bicycle kinematics
  let yawRate = 0;
  if (Math.abs(state.steer) > 1e-4) {
    const turningRadius = PARAMS.wheelbase / Math.tan(state.steer);
    yawRate = Math.max(-0.8, Math.min(0.8, state.speed / turningRadius));
  }
  state.yaw += yawRate * dt;

  const newX = state.x + state.speed * Math.cos(state.yaw) * dt;
  const newZ = state.z + state.speed * Math.sin(state.yaw) * dt;

  state.wheelAngle += (state.speed * dt) / PARAMS.wheelRadius;

  return { newX, newZ };
}

// ─────────────────────────────────────────────
//  Robot mesh (simple car shape)
// ─────────────────────────────────────────────

const robotGroup = new THREE.Group();
threeScene.add(robotGroup);

// Body
const bodyMat = new THREE.MeshStandardMaterial({ color: 0xd633a0, roughness: 0.4, metalness: 0.3 });
const bodyMesh = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.35, 0.4), bodyMat);
bodyMesh.position.y = -0.18;
robotGroup.add(bodyMesh);

// Cabin top
const cabinMat = new THREE.MeshStandardMaterial({ color: 0x9a2272, roughness: 0.5, metalness: 0.2 });
const cabinMesh = new THREE.Mesh(new THREE.BoxGeometry(0.35, 0.22, 0.35), cabinMat);
cabinMesh.position.set(0.03, -0.39, 0);
robotGroup.add(cabinMesh);

// Wheels: [FL, FR, RL, RR]
const wheelGeo = new THREE.CylinderGeometry(PARAMS.wheelRadius, PARAMS.wheelRadius, 0.08, 16);
const wheelMat = new THREE.MeshStandardMaterial({ color: 0x333333, roughness: 0.8 });

const wheelOffsets = [
  { x:  0.28, y: -0.11, z:  0.22, front: true,  name: 'FL' },
  { x:  0.28, y: -0.11, z: -0.22, front: true,  name: 'FR' },
  { x: -0.28, y: -0.11, z:  0.22, front: false, name: 'RL' },
  { x: -0.28, y: -0.11, z: -0.22, front: false, name: 'RR' },
];

const wheelMeshes = wheelOffsets.map(w => {
  // Pivot group for steering (front wheels only)
  const pivot = new THREE.Group();
  pivot.position.set(w.x, w.y, w.z);

  const mesh = new THREE.Mesh(wheelGeo, wheelMat);
  // Cylinder is along Y by default; rotate to lie along Z (axle direction)
  mesh.rotation.x = Math.PI / 2;
  pivot.add(mesh);

  robotGroup.add(pivot);
  return { pivot, mesh, front: w.front };
});

// Wheel hub caps (white dots)
const hubGeo = new THREE.CircleGeometry(0.04, 8);
const hubMat = new THREE.MeshStandardMaterial({ color: 0xcccccc, side: THREE.DoubleSide });
wheelMeshes.forEach(w => {
  const h1 = new THREE.Mesh(hubGeo, hubMat);
  h1.position.z = 0.042;
  w.mesh.add(h1);
  const h2 = new THREE.Mesh(hubGeo, hubMat);
  h2.position.z = -0.042;
  h2.rotation.y = Math.PI;
  w.mesh.add(h2);
});

function updateRobotMesh() {
  // Position: scene is +Y down (like Python), so use state.y directly
  robotGroup.position.set(state.x, state.y, state.z);

  // Yaw around Y axis — driving direction matches Python's convention
  // In Three.js: +X forward, but Python uses cos(yaw)=X, sin(yaw)=Z
  // We rotate around Y axis (which is up in standard Three.js, but -Y is up in scene)
  robotGroup.rotation.set(0, -state.yaw + Math.PI / 2, 0);

  // Front wheel steering
  wheelMeshes.forEach(w => {
    if (w.front) {
      w.pivot.rotation.y = state.steer;
    }
    // Rolling rotation: wheels roll around their local X (after being rotated to Z-axle)
    w.mesh.rotation.y = state.wheelAngle;
  });
}

// ─────────────────────────────────────────────
//  Collision mesh (PLY, hidden by default)
// ─────────────────────────────────────────────

let collisionMesh = null;        // THREE.Mesh with geometry for raycasting
let collisionMeshVisible = false;
const plyLoader = new PLYLoader();

// ─────────────────────────────────────────────
//  Collision detection — 8-ray sphere cast
// ─────────────────────────────────────────────

const raycaster = new THREE.Raycaster();
raycaster.near = 0;
raycaster.far = 0.55;

const COL_RADIUS = 0.5;
const COL_DIRS = [];
for (let i = 0; i < 8; i++) {
  const angle = (i / 8) * Math.PI * 2;
  COL_DIRS.push(new THREE.Vector3(Math.cos(angle), 0, Math.sin(angle)));
}

function checkCollision(x, y, z) {
  if (!collisionMesh) return false;
  const origin = new THREE.Vector3(x, y, z);
  for (const dir of COL_DIRS) {
    raycaster.set(origin, dir);
    raycaster.far = COL_RADIUS;
    const hits = raycaster.intersectObject(collisionMesh, false);
    if (hits.length > 0) return true;
  }
  return false;
}

// ─────────────────────────────────────────────
//  Camera orbit state
// ─────────────────────────────────────────────

const camState = {
  orbitAngle: Math.PI,   // radians, starts behind robot (PI = -X direction at yaw=PI/2)
  elevation: 1.5,        // vertical offset (positive = above robot in world -Y)
  followDist: 3.5,
};

// Smooth camera target
const camTarget = new THREE.Vector3();

function updateCamera(dt) {
  // Camera orbit controls (WASD)
  const orbitSpeed = 2.0; // rad/s
  const elevSpeed  = 3.0; // units/s
  if (keys.a) camState.orbitAngle -= orbitSpeed * dt;
  if (keys.d) camState.orbitAngle += orbitSpeed * dt;
  if (keys.w) camState.elevation   = Math.max(0.2, camState.elevation - elevSpeed * dt);
  if (keys.s) camState.elevation   = Math.min(8.0, camState.elevation + elevSpeed * dt);

  // Orbit position behind robot
  const ox = -camState.followDist * Math.cos(camState.orbitAngle);
  const oz = -camState.followDist * Math.sin(camState.orbitAngle);
  const oy = -camState.elevation;  // -Y is up in scene

  const targetPos = new THREE.Vector3(
    state.x + ox,
    state.y + oy,
    state.z + oz
  );

  // Smooth follow
  camera.position.lerp(targetPos, Math.min(1, dt * 8));

  // Look at robot
  camTarget.lerp(new THREE.Vector3(state.x, state.y, state.z), Math.min(1, dt * 10));
  camera.lookAt(camTarget);
  camera.up.set(0, -1, 0); // -Y is up in scene
}

// ─────────────────────────────────────────────
//  Keyboard input
// ─────────────────────────────────────────────

const keys = { up: false, down: false, left: false, right: false, a: false, d: false, w: false, s: false };

const KEY_MAP = {
  ArrowUp:    'up',    KeyI: 'up',
  ArrowDown:  'down',  KeyK: 'down',
  ArrowLeft:  'left',  KeyJ: 'left',
  ArrowRight: 'right', KeyL: 'right',
  KeyA: 'a', KeyD: 'd', KeyW: 'w', KeyS: 's',
};

document.addEventListener('keydown', e => {
  const k = KEY_MAP[e.code];
  if (k) { keys[k] = true; e.preventDefault(); }
});

document.addEventListener('keyup', e => {
  const k = KEY_MAP[e.code];
  if (k) { keys[k] = false; e.preventDefault(); }
});

window.addEventListener('blur', () => {
  Object.keys(keys).forEach(k => keys[k] = false);
});

// ─────────────────────────────────────────────
//  Scene management
// ─────────────────────────────────────────────

let meta = {};
let currentSceneId = null;
let splatSceneAdded = false;
let lastCollisionTime = -999;
let meshLoaded = false;

async function loadMeta() {
  setStatus('Loading scene metadata…');
  const res = await fetch('./data/meta.json');
  meta = await res.json();
}

// Point clouds are too large for GitHub Pages (>100 MB per file) and are
// hosted as GitHub Release assets. The URL template lives in meta.json under
// `_data_sources.point_cloud_url_template`; falls back to the local path so
// `npm run dev` keeps working when the files are present on disk.
function pointCloudUrl(sceneId) {
  const tmpl = meta?._data_sources?.point_cloud_url_template;
  if (tmpl) return tmpl.replaceAll('{scene_id}', sceneId);
  return `./data/${sceneId}/point_cloud.ply`;
}

function isSceneEntry(key) {
  return !key.startsWith('_');
}

function applySceneRotation(sceneId) {
  const sceneMeta = meta[sceneId] || {};
  const rot = sceneMeta.scene_rotation || { x: 0, y: 0, z: 0 };
  const rx = THREE.MathUtils.degToRad(rot.x || 0);
  const ry = THREE.MathUtils.degToRad(rot.y || 0);
  const rz = THREE.MathUtils.degToRad(rot.z || 0);
  sceneGroup.rotation.set(rx, ry, rz, 'XYZ');
}

function resetRobotToSpawn(sceneId) {
  const sceneMeta = meta[sceneId] || {};
  const spawn = sceneMeta.spawn_point || { x: 0, y: 0, z: 0 };
  state.x = spawn.x;
  state.y = spawn.y;
  state.z = spawn.z;
  state.yaw = Math.PI / 2;
  state.speed = 0;
  state.steer = 0;
  state.steerVel = 0;
  state.wheelAngle = 0;

  // Reset camera orbit behind robot
  camState.orbitAngle = Math.PI;
  camState.elevation  = 1.5;

  // Snap camera immediately
  camera.position.set(state.x - 3.5 * Math.cos(camState.orbitAngle),
                      state.y - camState.elevation,
                      state.z - 3.5 * Math.sin(camState.orbitAngle));
  camTarget.set(state.x, state.y, state.z);
}

async function loadScene(sceneId) {
  if (sceneId === currentSceneId) return;
  currentSceneId = sceneId;
  meshLoaded = false;

  showOverlay();
  setStatus('Removing previous scene…');

  // Remove old splat scene
  if (splatSceneAdded) {
    try {
      gsViewer.removeSplatScene(0);
    } catch (_) {}
    splatSceneAdded = false;
  }

  // Remove old collision mesh from scene group
  if (collisionMesh) {
    sceneGroup.remove(collisionMesh);
    collisionMesh.geometry.dispose();
    collisionMesh = null;
  }

  applySceneRotation(sceneId);
  resetRobotToSpawn(sceneId);

  // ── Load collision mesh (PLY) ──
  setStatus('Loading collision mesh…');
  const meshUrl = `./data/${sceneId}/mesh.ply`;
  await new Promise((resolve) => {
    plyLoader.load(
      meshUrl,
      (geometry) => {
        geometry.computeVertexNormals();
        const mat = new THREE.MeshStandardMaterial({
          color: 0x44aaff,
          transparent: true,
          opacity: 0.35,
          wireframe: false,
          side: THREE.DoubleSide,
        });
        collisionMesh = new THREE.Mesh(geometry, mat);
        collisionMesh.visible = collisionMeshVisible;
        sceneGroup.add(collisionMesh);
        meshLoaded = true;
        resolve();
      },
      undefined,
      (err) => {
        console.warn('Mesh load error:', err);
        meshLoaded = true;
        resolve(); // non-fatal
      }
    );
  });

  // ── Load Gaussian Splats ──
  setStatus('Loading Gaussian splats…');
  const splatUrl = pointCloudUrl(sceneId);

  try {
    await gsViewer.addSplatScene(splatUrl, {
      splatAlphaRemovalThreshold: 5,
      showLoadingUI: false,
    });
    splatSceneAdded = true;
  } catch (err) {
    console.error('Splat load error:', err);
    setStatus('Error loading splats: ' + err.message);
    return;
  }

  setStatus('Ready');
  hideOverlay();
}

// ─────────────────────────────────────────────
//  GUI wiring
// ─────────────────────────────────────────────

const sceneSelect    = document.getElementById('scene-select');
const speedDisplay   = document.getElementById('speed-display');
const resetBtn       = document.getElementById('reset-btn');
const showMeshToggle = document.getElementById('show-mesh-toggle');

function populateSceneDropdown() {
  sceneSelect.innerHTML = '';
  for (const [id, sceneMeta] of Object.entries(meta)) {
    if (!isSceneEntry(id)) continue;
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = sceneMeta.name || id;
    sceneSelect.appendChild(opt);
  }
}

sceneSelect.addEventListener('change', () => {
  loadScene(sceneSelect.value);
});

resetBtn.addEventListener('click', () => {
  if (currentSceneId) resetRobotToSpawn(currentSceneId);
});

showMeshToggle.addEventListener('change', () => {
  collisionMeshVisible = showMeshToggle.checked;
  if (collisionMesh) collisionMesh.visible = collisionMeshVisible;
});

// ─────────────────────────────────────────────
//  Animation loop
// ─────────────────────────────────────────────

let lastFrameTime = performance.now();

function animate() {
  requestAnimationFrame(animate);

  const now = performance.now();
  const dt  = Math.min((now - lastFrameTime) / 1000, 0.05);
  lastFrameTime = now;

  // ── Robot simulation ──
  const throttle   = (keys.up   ? 1 : 0) - (keys.down  ? 1 : 0);
  const steerInput = (keys.left ? PARAMS.maxSteer : 0) - (keys.right ? PARAMS.maxSteer : 0);

  const { newX, newZ } = updateRobotState(dt, throttle, steerInput);

  // ── Collision detection ──
  const collided = checkCollision(newX, state.y, newZ);
  if (!collided) {
    state.x = newX;
    state.z = newZ;
  } else {
    state.speed = 0;
    state.steerVel *= 0.5;
    const elapsed = now / 1000;
    if (elapsed - lastCollisionTime > 1.5) {
      lastCollisionTime = elapsed;
      window.showCollisionBanner && window.showCollisionBanner();
    }
  }

  // ── Update robot mesh ──
  updateRobotMesh();

  // ── Camera ──
  updateCamera(dt);

  // ── GUI ──
  speedDisplay.value = Math.abs(state.speed).toFixed(2);

  // ── Render ──
  // Let GaussianSplats3D update its internal state
  if (splatSceneAdded) {
    try {
      gsViewer.update();
      gsViewer.render();
    } catch (_) {}
  }

  renderer.render(threeScene, camera);
}

// ─────────────────────────────────────────────
//  Bootstrap
// ─────────────────────────────────────────────

async function init() {
  try {
    await loadMeta();
    populateSceneDropdown();

    const firstScene = Object.keys(meta).find(isSceneEntry);
    if (firstScene) {
      sceneSelect.value = firstScene;
      await loadScene(firstScene);
    } else {
      setStatus('No scenes found in meta.json');
    }
  } catch (err) {
    setStatus('Init error: ' + err.message);
    console.error(err);
  }

  animate();
}

init();
