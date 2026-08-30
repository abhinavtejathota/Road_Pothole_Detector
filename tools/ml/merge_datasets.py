from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent

# Source datasets
BASE = ROOT / "datasets" / "roboflow" / "Pothole-Detection--2"
P600 = ROOT / "datasets" / "Pothole.v1-raw.yolov11"

# Output dataset
OUT = ROOT / "datasets" / "combined"

if OUT.exists():
    shutil.rmtree(OUT)

for split in ["train", "valid", "test"]:
    (OUT / split / "images").mkdir(parents=True, exist_ok=True)
    (OUT / split / "labels").mkdir(parents=True, exist_ok=True)


def copy_dataset(src):
    for split in ["train", "valid", "test"]:
        img_src = src / split / "images"
        lbl_src = src / split / "labels"

        img_dst = OUT / split / "images"
        lbl_dst = OUT / split / "labels"

        if img_src.exists():
            for f in img_src.iterdir():
                shutil.copy2(f, img_dst / f.name)

        if lbl_src.exists():
            for f in lbl_src.iterdir():
                shutil.copy2(f, lbl_dst / f.name)


print("Copying Roboflow dataset...")
copy_dataset(BASE)

print("Copying Pothole600 dataset...")
copy_dataset(P600)

yaml = """path: .
train: train/images
val: valid/images
test: test/images

nc: 1
names: ['pothole']
"""

(OUT / "data.yaml").write_text(yaml)

print("\nCombined dataset created successfully!")

for split in ["train", "valid", "test"]:
    imgs = len(list((OUT / split / "images").glob("*")))
    lbls = len(list((OUT / split / "labels").glob("*")))

    print(f"{split}: {imgs} images | {lbls} labels")