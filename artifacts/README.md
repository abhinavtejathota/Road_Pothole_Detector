# Artifacts (gitignored — regenerate locally)

Downloadable / generated files that are not part of source code.

| Path | How to recreate |
|------|-----------------|
| `models/smartroad_ap.pt` | Copy trained weights or run `tools/ml/train_yolo.py` |
| `models/*.pt` | Bench weights via `tools/ml/seed_weights.py` |

GIS road networks live under `data/gis_states/` (see `tools/gis/README.md`).  
Training datasets live under `tools/ml/datasets/` (see `tools/ml/README.md`).

Clean everything: `python scripts/clean_downloaded_data.py --yes --weights`
