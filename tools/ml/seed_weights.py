#!/usr/bin/env python3
"""
Seed local weight files under tools/ml/models/ so the bench can
train / compare without depending on hub downloads or mutating
production artifacts/models/smartroad_ap.pt.

  python tools/ml/seed_weights.py

Creates / refreshes:
  yolo_finetuned_copy.pt  — duplicate of production (train from this; never touch prod)
  rfdetr_small.pt         — RF-DETR Small COCO pretrained (local .pt for dropdown + train)
  yolo12n.pt, yolo11n.pt, yolov8n.pt, yolov8s.pt, yolo12s.pt — local YOLO bases
  yolo12m.pt, yolo12l.pt, yolo12x.pt — larger YOLO12 COCO bases (GPU bench on server)
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path
from urllib.request import urlretrieve

_PKG = Path(__file__).resolve().parent
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from datasets_config import (  # noqa: E402
    RFDETR_SMALL_LOCAL,
    RFDETR_SMALL_MD5,
    RFDETR_SMALL_URL,
)
from paths import BENCH_MODELS_DIR, PROJECT_ROOT, ensure_dirs  # noqa: E402

PROD_YOLO = PROJECT_ROOT / "artifacts/models" / "smartroad_ap.pt"
FINETUNED_COPY = "yolo_finetuned_copy.pt"

# Hub name → local filename under tools/ml/models/
# Direct URLs = reliable on Windows (Ultralytics cache path often breaks shutil.copy).
YOLO_ASSETS_BASE = "https://github.com/ultralytics/assets/releases/download/v8.4.0"
YOLO_BASELINES = [
    # (hub_name, local_name, direct_url or None)
    ("yolo12n.pt", "yolo12n.pt", f"{YOLO_ASSETS_BASE}/yolo12n.pt"),
    ("yolo11n.pt", "yolo11n.pt", f"{YOLO_ASSETS_BASE}/yolo11n.pt"),
    ("yolov8n.pt", "yolov8n.pt", f"{YOLO_ASSETS_BASE}/yolov8n.pt"),
    ("yolov8s.pt", "yolov8s.pt", f"{YOLO_ASSETS_BASE}/yolov8s.pt"),
    ("yolo12s.pt", "yolo12s.pt", f"{YOLO_ASSETS_BASE}/yolo12s.pt"),
    ("yolo12m.pt", "yolo12m.pt", f"{YOLO_ASSETS_BASE}/yolo12m.pt"),
    ("yolo12l.pt", "yolo12l.pt", f"{YOLO_ASSETS_BASE}/yolo12l.pt"),
    ("yolo12x.pt", "yolo12x.pt", f"{YOLO_ASSETS_BASE}/yolo12x.pt"),
]


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_prod_duplicate(*, force: bool) -> Path | None:
    dest = BENCH_MODELS_DIR / FINETUNED_COPY
    if not PROD_YOLO.is_file():
        print(f"[skip] Production weights not found: {PROD_YOLO}")
        return None
    if dest.is_file() and not force:
        print(f"[ok]   {dest.name} already present ({dest.stat().st_size:,} bytes)")
        return dest
    shutil.copy2(PROD_YOLO, dest)
    print(f"[copy] {PROD_YOLO.name} -> {dest} ({dest.stat().st_size:,} bytes)")
    print("       Production artifacts/models/smartroad_ap.pt was NOT modified.")
    return dest


def _resolve_downloaded(hub_name: str, model) -> Path | None:
    candidates: list[Path] = []
    for attr in ("ckpt_path", "weights", "model_path"):
        val = getattr(model, attr, None)
        if isinstance(val, (str, Path)) and Path(val).is_file():
            candidates.append(Path(val))
    try:
        from ultralytics.utils import SETTINGS

        weights_dir = Path(SETTINGS.get("weights_dir", ""))
        if weights_dir.is_dir():
            candidates.append(weights_dir / hub_name)
    except Exception:
        pass
    candidates.extend(
        [
            Path.cwd() / hub_name,
            PROJECT_ROOT / hub_name,
            BENCH_MODELS_DIR / hub_name,
        ]
    )
    for c in candidates:
        if c.is_file():
            return c
    return None


def _download_url(url: str, dest: Path) -> Path | None:
    """Direct HTTP download into dest (atomic via .partial)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    print(f"[dl]   {url}")

    def _progress(block: int, block_size: int, total: int) -> None:
        if total <= 0:
            return
        done = min(100.0, 100.0 * block * block_size / total)
        if block % 40 == 0 or done >= 99.9:
            print(f"\r       {done:5.1f}%", end="", flush=True)

    try:
        urlretrieve(url, tmp, reporthook=_progress)
        print()
    except Exception as e:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        print(f"[warn] Direct download failed: {e}")
        return None
    if not tmp.is_file() or tmp.stat().st_size < 1_000_000:
        tmp.unlink(missing_ok=True)
        print(f"[warn] Download too small / missing for {dest.name}")
        return None
    tmp.replace(dest)
    print(f"[ok]   {dest.name} ({dest.stat().st_size:,} bytes)")
    return dest


def _seed_yolo_baseline(
    hub_name: str,
    local_name: str,
    direct_url: str | None,
    *,
    force: bool,
) -> Path | None:
    dest = BENCH_MODELS_DIR / local_name
    if dest.is_file() and not force:
        print(f"[ok]   {local_name} already present ({dest.stat().st_size:,} bytes)")
        return dest

    # Prefer direct GitHub asset URL (reliable on Windows).
    if direct_url:
        got = _download_url(direct_url, dest)
        if got is not None:
            return got

    # Fallback: Ultralytics YOLO() auto-download (Linux / when URL blocked).
    print(f"[dl]   Fallback via Ultralytics YOLO({hub_name!r}) ...")
    import os

    from ultralytics import YOLO

    prev_cwd = Path.cwd()
    try:
        os.chdir(BENCH_MODELS_DIR)
        model = YOLO(hub_name)
        # After load, Ultralytics often already wrote into cwd or weights_dir.
        if dest.is_file():
            print(f"[ok]   {local_name} ({dest.stat().st_size:,} bytes)")
            return dest
        src = _resolve_downloaded(hub_name, model)
    finally:
        os.chdir(prev_cwd)

    # Also check after restoring cwd
    if dest.is_file():
        print(f"[ok]   {local_name} ({dest.stat().st_size:,} bytes)")
        return dest

    if src is None or not Path(src).is_file():
        try:
            model = YOLO(hub_name)
            model.save(str(dest))
            if dest.is_file():
                print(f"[save] {local_name}")
                return dest
        except Exception as e:
            print(f"[warn] Could not materialize {hub_name}: {e}")
            return None
        print(f"[warn] Could not locate downloaded {hub_name}")
        return None

    src_path = Path(src)
    if src_path.resolve() != dest.resolve():
        try:
            shutil.copy2(src_path, dest)
        except FileNotFoundError as e:
            print(f"[warn] copy failed ({e}) — try: wget -O {dest} {direct_url or hub_name}")
            return None
        root_copy = PROJECT_ROOT / hub_name
        if root_copy.is_file() and root_copy.resolve() != dest.resolve():
            try:
                root_copy.unlink()
            except OSError:
                pass
    if not dest.is_file():
        print(f"[warn] {local_name} still missing after seed")
        return None
    print(f"[ok]   {local_name} <- {src_path}")
    return dest


def _seed_rfdetr_small(*, force: bool) -> Path | None:
    dest = BENCH_MODELS_DIR / RFDETR_SMALL_LOCAL
    if dest.is_file() and not force:
        if RFDETR_SMALL_MD5 and _md5(dest) != RFDETR_SMALL_MD5:
            print(f"[warn] {dest.name} MD5 mismatch — re-download with --force")
        else:
            print(f"[ok]   {dest.name} already present ({dest.stat().st_size:,} bytes)")
            return dest

    print(f"[dl]   Fetching RF-DETR Small -> {dest.name} ...")
    print(f"       {RFDETR_SMALL_URL}")
    tmp = dest.with_suffix(dest.suffix + ".partial")

    def _progress(block: int, block_size: int, total: int) -> None:
        if total <= 0:
            return
        done = min(100.0, 100.0 * block * block_size / total)
        if block % 40 == 0 or done >= 99.9:
            print(f"\r       {done:5.1f}%", end="", flush=True)

    try:
        urlretrieve(RFDETR_SMALL_URL, tmp, reporthook=_progress)
        print()
    except Exception as e:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        print(f"[warn] RF-DETR download failed: {e}")
        print('       Install deps: pip install -r tools/ml/requirements.txt')
        return None

    if RFDETR_SMALL_MD5:
        digest = _md5(tmp)
        if digest != RFDETR_SMALL_MD5:
            tmp.unlink(missing_ok=True)
            print(f"[warn] MD5 mismatch for RF-DETR Small ({digest})")
            return None

    tmp.replace(dest)
    print(f"[ok]   {dest.name} ({dest.stat().st_size:,} bytes)")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed tools/ml/models/ weight files")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-copy / re-download even if local files already exist",
    )
    parser.add_argument(
        "--skip-baselines",
        action="store_true",
        help="Only copy fine-tuned YOLO + RF-DETR Small (skip COCO YOLO downloads)",
    )
    parser.add_argument(
        "--yolo12-only",
        action="store_true",
        help="Seed only YOLO12 sizes (n/s/m/l/x) — skip yolo11n and yolov8 baselines",
    )
    parser.add_argument(
        "--skip-rfdetr",
        action="store_true",
        help="Skip RF-DETR Small .pt download",
    )
    args = parser.parse_args()

    ensure_dirs()
    print(f"Bench models dir: {BENCH_MODELS_DIR}\n")

    _copy_prod_duplicate(force=args.force)

    if not args.skip_rfdetr:
        print()
        _seed_rfdetr_small(force=args.force)

    if not args.skip_baselines:
        print()
        yolo12_names = {"yolo12n.pt", "yolo12s.pt", "yolo12m.pt", "yolo12l.pt", "yolo12x.pt"}
        for hub, local, url in YOLO_BASELINES:
            if args.yolo12_only and local not in yolo12_names:
                continue
            try:
                _seed_yolo_baseline(hub, local, url, force=args.force)
            except Exception as e:
                print(f"[warn] Failed to seed {local}: {e}")
                if url:
                    print(f"       Manual: curl -L -o tools/ml/models/{local} {url}")

    fine = BENCH_MODELS_DIR / "rfdetr_pothole.pth"
    if fine.is_file():
        print(f"\n[ok]   rfdetr_pothole.pth present ({fine.stat().st_size:,} bytes)")

    print("\nDone. Full train setup:")
    print("  python tools/ml/setup_train.py")
    print("  python tools/ml/train_yolo.py")
    print("  python tools/ml/train_rfdetr.py")


if __name__ == "__main__":
    main()
