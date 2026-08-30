# Bench weight slots (`tools/ml/models/`)

All files here stay inside **model_testing**. Production `artifacts/models/smartroad_ap.pt` is never written by training.

| File | Role |
|------|------|
| `yolo_finetuned_copy.pt` | Duplicate of production YOLO — **train from this** |
| `yolo12m_trained.pt` (etc.) | Output of `train_yolo.py --base yolo12m.pt` — one file per base |
| `yolo_pothole_bench.pt` | Legacy single-slot output (older runs) |
| `rfdetr_small.pt` | RF-DETR Small COCO pretrained — dropdown + train base |
| `rfdetr_pothole.pth` | Output of `train_rfdetr.py` |
| `yolo12n.pt` … `yolo12x.pt` | YOLO12 COCO bases (seed_weights) |

Train examples:

```powershell
python tools/ml/train_yolo.py --base tools/ml/models/yolo12m.pt
# → models/yolo12m_trained.pt
# → runs/yolo/yolo12m_trained/

python tools/ml/train_yolo.py --base tools/ml/models/yolo12l.pt
# → models/yolo12l_trained.pt
# → runs/yolo/yolo12l_trained/

python tools/ml/train_yolo.py --base tools/ml/models/yolo12m.pt --name yolo12m_v2
# → models/yolo12m_v2.pt + runs/yolo/yolo12m_v2/
```

```powershell
python tools/ml/seed_weights.py
# or full setup:
python tools/ml/setup_train.py
# server: download YOLO12 family only
python tools/ml/seed_weights.py --yolo12-only
```
