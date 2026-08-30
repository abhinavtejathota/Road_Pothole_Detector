# Incoming images for bench training

## images/
Drop road photos here (or use `collect_images.py`).

## labels/
YOLO format `.txt` files — **same filename stem** as each image.

Example `labels/road_001.txt`:
```
0 0.52 0.61 0.08 0.06
```
(class 0 = pothole, normalized bbox)

## Next steps
```powershell
python tools/ml/prepare_dataset.py
python tools/ml/train_rfdetr.py
python tools/ml/train_yolo.py
```

Test weights in the portal **Model testing** page (`/model-bench`).

See **TRAINING.md** for the full workflow.
