"""Coco-GL-SW1k ONNX navigator for coco2play autopilot.

Expects model files in models/Coco_GL_SW1k/:
    coco.onnx, model_info.yaml

Input:  (B, 21, 3, 288, 512) float32 RGB in [0, 1] @ 5 Hz
Output: inference_vw() → (B, 2) linear + angular velocity [m/s, rad/s]
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent / "Coco_GL_SW1k"
ONNX_PATH = MODEL_DIR / "coco.onnx"
INFO_PATH = MODEL_DIR / "model_info.yaml"

M = 5
NUM_PTS = 10
POSE_WIDTH = 3
FEAT_DIM = 256
FEAT_BUFFER_LEN = 9
COCO_H = 128
COCO_W = 352

_deps_ok: bool | None = None
_deps_error: str | None = None


def _check_deps() -> tuple[bool, str | None]:
    global _deps_ok, _deps_error
    if _deps_ok is not None:
        return _deps_ok, _deps_error
    try:
        import onnxruntime  # noqa: F401
        import torch  # noqa: F401
        import yaml  # noqa: F401
        _deps_ok = True
        _deps_error = None
    except ImportError as exc:
        _deps_ok = False
        _deps_error = str(exc)
    return _deps_ok, _deps_error


def autopilot_available() -> bool:
    ok, _ = _check_deps()
    return ok and ONNX_PATH.is_file()


def autopilot_status() -> str:
    ok, err = _check_deps()
    if not ok:
        return f"missing deps: {err}"
    if not ONNX_PATH.is_file():
        return f"missing model: {ONNX_PATH}"
    return "ready"


def _softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - x.max(axis=axis, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=axis, keepdims=True)


def _parse_plan(plan_raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    B = plan_raw.shape[0]
    per_mode = NUM_PTS * 2 * POSE_WIDTH + 1
    pr = plan_raw.reshape(B, M, per_mode)
    scores = _softmax(pr[:, :, -1], axis=-1)
    means = pr[:, :, :-1].reshape(B, M, 2, NUM_PTS, POSE_WIDTH)[:, :, 0, :, :]
    return means, scores


def select_best_mode(trajectory: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    best_idx = scores.argmax(axis=1)
    best_traj = trajectory[np.arange(trajectory.shape[0]), best_idx]
    return best_traj, best_idx


class PDController:
    MAX_V = 1.5
    MAX_W = 0.65

    def __init__(self) -> None:
        self.last_v = None

    def reset(self) -> None:
        self.last_v = None

    def __call__(self, waypoints, dt: float = 1.5):
        import torch

        EPS = 1e-6
        dxr = waypoints[:, -1, 0]
        dyr = waypoints[:, -1, 1]
        idx = (waypoints.shape[1] - 1) // 2
        dx = waypoints[:, idx, 0]

        v = dx / dt
        w = (torch.atan2(dyr, dxr.abs().clamp(min=EPS)) * dxr.sign() / dt)

        near_zero = dx.abs() < EPS
        v = torch.where(near_zero, torch.zeros_like(v), v)
        w = torch.where(near_zero, dyr.sign() * (math.pi / 20.0), w)
        self.last_v = v
        return v, w


def _create_ort_session(onnx_path: str | os.PathLike[str], prefer_cuda: bool):
    """Open an ONNX session, falling back to CPU if CUDA/cuDNN init fails."""
    import onnxruntime as ort

    ort.set_default_logger_severity(3)
    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    cpu_providers = ["CPUExecutionProvider"]

    if prefer_cuda and "CUDAExecutionProvider" in ort.get_available_providers():
        cuda_providers = [
            ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
            "CPUExecutionProvider",
        ]
        try:
            session = ort.InferenceSession(
                str(onnx_path), sess_options=sess_opts, providers=cuda_providers
            )
            print("✓ CocoNavigator ONNX session: CUDAExecutionProvider")
            return session, "cuda"
        except Exception as exc:
            print(f"⚠ CocoNavigator CUDA init failed ({exc!r}), using CPU")

    session = ort.InferenceSession(
        str(onnx_path), sess_options=sess_opts, providers=cpu_providers
    )
    print("✓ CocoNavigator ONNX session: CPUExecutionProvider")
    return session, "cpu"


class CocoNavigator:
    context_size = 21
    multimodal = True

    def __init__(
        self,
        onnx_path: str | os.PathLike[str] = ONNX_PATH,
        device: str = "auto",
        max_v: float = 1.5,
        max_w: float = 0.65,
        dt: float = 1.0,
    ) -> None:
        import yaml

        self.dt = dt
        prefer_cuda = device.lower() == "cuda" or (
            device.lower() == "auto" and os.environ.get("COCO_AUTOPILOT_DEVICE", "auto").lower() != "cpu"
        )
        if os.environ.get("COCO_AUTOPILOT_DEVICE", "").lower() == "cpu":
            prefer_cuda = False

        self._session, self.ort_device = _create_ort_session(onnx_path, prefer_cuda=prefer_cuda)

        with open(INFO_PATH, "r", encoding="utf-8") as f:
            self._info = yaml.safe_load(f)

        self.feature_buffer: np.ndarray | None = None
        self._controller = PDController()
        self._controller.MAX_V = max_v
        self._controller.MAX_W = max_w

    def reset(self) -> None:
        if self.feature_buffer is not None:
            self.feature_buffer[:] = 0.0
        self._controller.reset()

    def _ensure_feat_buffer(self, B: int) -> None:
        if self.feature_buffer is None or self.feature_buffer.shape[0] != B:
            self.feature_buffer = np.zeros((B, FEAT_BUFFER_LEN, FEAT_DIM), dtype=np.float32)

    def _roll_feat_buffer(self, feat_out: np.ndarray) -> None:
        assert self.feature_buffer is not None
        self.feature_buffer[:, :-1] = self.feature_buffer[:, 1:]
        self.feature_buffer[:, -1] = feat_out

    def _preprocess_obs(self, obs) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        if isinstance(obs, np.ndarray):
            x = torch.from_numpy(obs).float()
        else:
            x = obs.float()

        x = x[:, -2:, :, :, :]
        B, T, C, H, W = x.shape
        x = x.reshape(B, T * C, H, W)
        x = F.interpolate(x, size=(COCO_H, COCO_W), mode="bilinear", align_corners=False)
        return x.cpu().numpy().astype(np.float32)

    def _forward(self, obs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        img_np = self._preprocess_obs(obs)
        B = img_np.shape[0]
        self._ensure_feat_buffer(B)

        plans, poses, feats = [], [], []
        for b in range(B):
            plan_b, pose_b, feat_b = self._session.run(
                ["plan", "pose", "feat_out"],
                {
                    "input": img_np[b : b + 1],
                    "feature_buffer": self.feature_buffer[b : b + 1],
                },
            )
            plans.append(plan_b)
            poses.append(pose_b)
            feats.append(feat_b)

        plan = np.concatenate(plans, axis=0)
        pose = np.concatenate(poses, axis=0)
        feat_out = np.concatenate(feats, axis=0)
        self._roll_feat_buffer(feat_out)
        return plan, pose, feat_out

    def inference_trajectory(self, obs) -> tuple[np.ndarray, np.ndarray]:
        plan, *_ = self._forward(obs)
        traj3d, scores = _parse_plan(plan)
        return traj3d, scores

    def inference_vw(self, obs):
        import torch

        trajectory, scores = self.inference_trajectory(obs)
        best_traj, _ = select_best_mode(trajectory, scores)
        waypoints = torch.from_numpy(best_traj).float()
        _, w = self._controller(waypoints[..., :2], dt=self.dt)
        v = waypoints[:, 5, 2]
        return torch.stack([v, w], dim=1), best_traj
