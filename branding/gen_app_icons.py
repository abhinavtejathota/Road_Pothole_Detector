"""Regenerate Expo app icons from branding/smartroad_icon_1024.png."""
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
EXPO = ROOT / "mobile_app"

BRAND = ROOT / "branding" / "smartroad_icon_1024.png"
CURSOR_ASSETS = Path(
    r"C:\Users\akshay teja thota\.cursor\projects\d-Docs-Problem-Solving-Forks-smart-road-app\assets"
)


def resolve_src() -> Path:
    if BRAND.is_file():
        return BRAND
    matches = sorted(CURSOR_ASSETS.glob("*IMG_9790*.png"))
    if matches:
        return matches[-1]
    raise FileNotFoundError("No icon source found (branding/smartroad_icon_1024.png or IMG_9790)")


def save_rgb(img: Image.Image, path: Path, size: int | None = None, bg=(255, 255, 255)) -> None:
    im = img if size is None else img.resize((size, size), Image.Resampling.LANCZOS)
    out = Image.new("RGB", im.size, bg)
    if im.mode == "RGBA":
        out.paste(im, mask=im.split()[3])
    else:
        out.paste(im.convert("RGB"))
    path.parent.mkdir(parents=True, exist_ok=True)
    out.save(path, "PNG", optimize=True)
    print("wrote", path, out.size)


def save_rgba(img: Image.Image, path: Path, size: int | None = None) -> None:
    im = img if size is None else img.resize((size, size), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", optimize=True)
    print("wrote", path, im.size)


def main() -> None:
    src = Image.open(resolve_src()).convert("RGBA")
    w, h = src.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.paste(src, ((side - w) // 2, (side - h) // 2), src)
    master = canvas.resize((1024, 1024), Image.Resampling.LANCZOS)

    brand_dir = ROOT / "branding"
    save_rgb(master, brand_dir / "smartroad_icon_1024.png", 1024)
    save_rgb(master, EXPO / "assets" / "icon.png", 1024)

    save_rgb(master, EXPO / "assets" / "splash-icon.png", 1024)
    save_rgb(master, EXPO / "assets" / "favicon.png", 48)

    # Adaptive foreground: inset logo in safe zone
    fg = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    inset = master.resize((820, 820), Image.Resampling.LANCZOS)
    fg.paste(inset, (102, 102), inset)
    save_rgba(fg, EXPO / "assets" / "android-icon-foreground.png", 1024)

    bg = Image.new("RGB", (1024, 1024), (255, 255, 255))
    bg_path = EXPO / "assets" / "android-icon-background.png"
    bg.save(bg_path, "PNG", optimize=True)
    print("wrote", bg_path)

    gray = ImageOps.grayscale(master.convert("RGB"))
    mono = ImageOps.autocontrast(gray)
    bw = mono.point(lambda p: 0 if p < 200 else 255)
    alpha = bw.point(lambda p: 255 if p < 128 else 0)
    mono_rgba = Image.merge(
        "RGBA",
        (
            Image.new("L", (1024, 1024), 0),
            Image.new("L", (1024, 1024), 0),
            Image.new("L", (1024, 1024), 0),
            alpha,
        ),
    )
    mono_fg = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    mono_small = mono_rgba.resize((820, 820), Image.Resampling.LANCZOS)
    mono_fg.paste(mono_small, (102, 102), mono_small)
    save_rgba(mono_fg, EXPO / "assets" / "android-icon-monochrome.png", 1024)
    print("DONE")


if __name__ == "__main__":
    main()
