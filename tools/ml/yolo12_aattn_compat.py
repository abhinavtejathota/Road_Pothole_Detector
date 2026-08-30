"""
YOLOv12 Area-Attention (AAttn) Turbo ↔ V1 compatibility.

Two incompatible layouts exist in the wild:

* **Turbo** (sunsmarterjie yolov12 fork): ``self.qk`` + ``self.v``
* **V1 / current Ultralytics YOLO12**: ``self.qkv``

Loading ``best.pt`` / ``yolo12s.pt`` with the wrong layout raises::

    AttributeError: 'AAttn' object has no attribute 'qk'
    AttributeError: 'AAttn' object has no attribute 'qkv'

Call :func:`patch_yolo_aattn` once after ``YOLO(...)`` so inference uses the
forward that matches the attributes present on each module.
"""
from __future__ import annotations

import types
from typing import Any


def _v1_aattn_forward(self, x):
    """Official Ultralytics-style Area Attention (combined qkv)."""
    B, _, H, W = x.shape
    N = H * W
    all_head_dim = getattr(self, "all_head_dim", None)
    if all_head_dim is None:
        all_head_dim = int(self.head_dim) * int(self.num_heads)
        self.all_head_dim = all_head_dim

    qkv = self.qkv(x).flatten(2).transpose(1, 2)
    if self.area > 1:
        qkv = qkv.reshape(B * self.area, N // self.area, all_head_dim * 3)
        B, N, _ = qkv.shape
    q, k, v = (
        qkv.view(B, N, self.num_heads, self.head_dim * 3)
        .permute(0, 2, 3, 1)
        .split([self.head_dim, self.head_dim, self.head_dim], dim=2)
    )
    attn = (q.transpose(-2, -1) @ k) * (self.head_dim**-0.5)
    attn = attn.softmax(dim=-1)
    x = v @ attn.transpose(-2, -1)
    x = x.permute(0, 3, 1, 2)
    v = v.permute(0, 3, 1, 2)

    if self.area > 1:
        x = x.reshape(B // self.area, N * self.area, all_head_dim)
        v = v.reshape(B // self.area, N * self.area, all_head_dim)
        B, N, _ = x.shape

    x = x.reshape(B, H, W, all_head_dim).permute(0, 3, 1, 2).contiguous()
    v = v.reshape(B, H, W, all_head_dim).permute(0, 3, 1, 2).contiguous()
    x = x + self.pe(v)
    return self.proj(x)


def _turbo_aattn_forward(self, x):
    """sunsmarterjie Turbo Area Attention (separate qk + v), without requiring FlashAttention."""
    import torch

    B, C, H, W = x.shape
    N = H * W

    qk = self.qk(x).flatten(2).transpose(1, 2)
    v = self.v(x)
    pp = self.pe(v)
    v = v.flatten(2).transpose(1, 2)

    if self.area > 1:
        qk = qk.reshape(B * self.area, N // self.area, C * 2)
        v = v.reshape(B * self.area, N // self.area, C)
        B, N, _ = qk.shape

    q, k = qk.split([C, C], dim=2)
    q = q.transpose(1, 2).view(B, self.num_heads, self.head_dim, N)
    k = k.transpose(1, 2).view(B, self.num_heads, self.head_dim, N)
    v = v.transpose(1, 2).view(B, self.num_heads, self.head_dim, N)

    attn = (q.transpose(-2, -1) @ k) * (self.head_dim**-0.5)
    max_attn = attn.max(dim=-1, keepdim=True).values
    exp_attn = torch.exp(attn - max_attn)
    attn = exp_attn / exp_attn.sum(dim=-1, keepdim=True)
    x = v @ attn.transpose(-2, -1)
    x = x.permute(0, 3, 1, 2)

    if self.area > 1:
        x = x.reshape(B // self.area, N * self.area, C)
        B, N, _ = x.shape
    x = x.reshape(B, H, W, C).permute(0, 3, 1, 2)
    return self.proj(x + pp)


def _iter_modules(root: Any):
    if root is None:
        return
    seen: set[int] = set()
    stack = [root]
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        if hasattr(obj, "modules") and callable(obj.modules):
            try:
                yield from obj.modules()
                continue
            except Exception:
                pass
        inner = getattr(obj, "model", None)
        if inner is not None and id(inner) not in seen:
            stack.append(inner)


def patch_yolo_aattn(model: Any) -> int:
    """Bind the correct AAttn/Attn forward for each module. Returns patched count."""
    patched = 0
    for m in _iter_modules(model):
        name = getattr(m.__class__, "__name__", "")

        if name == "AAttn":
            mods = getattr(m, "_modules", {}) or {}
            has_qk = "qk" in mods or hasattr(m, "qk")
            has_v = "v" in mods or hasattr(m, "v")
            has_qkv = "qkv" in mods or hasattr(m, "qkv")
            # Prefer whichever projection actually exists as a submodule (load-time truth).
            if "qkv" in mods or (has_qkv and not has_qk):
                if not hasattr(m, "all_head_dim"):
                    try:
                        m.all_head_dim = int(m.head_dim) * int(m.num_heads)
                    except Exception:
                        pass
                m.forward = types.MethodType(_v1_aattn_forward, m)
                patched += 1
            elif ("qk" in mods and "v" in mods) or (has_qk and has_v):
                m.forward = types.MethodType(_turbo_aattn_forward, m)
                patched += 1
            continue

        if name == "Attn":
            if hasattr(m, "qk") and not hasattr(m, "qkv"):
                try:
                    m.qkv = m.qk
                    patched += 1
                except Exception:
                    pass
            elif hasattr(m, "qkv") and not hasattr(m, "qk"):
                try:
                    m.qk = m.qkv
                    patched += 1
                except Exception:
                    pass

    return patched
