"""앱 아이콘(카메라)을 만듭니다.

외부 이미지를 받아 오지 않고 그려서 만듭니다 — 라이선스 문제가 없고,
색을 테마와 같은 값으로 맞출 수 있습니다.

작은 크기에서 뭉개지지 않도록 각 크기를 따로 그립니다. 16px에서 렌즈
안쪽 무늬까지 그리면 회색 덩어리가 되므로 크기별로 요소를 덜어냅니다.

    python make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
ASSETS = ROOT / "assets"
ICON = ASSETS / "icon.ico"

# 앱 테마와 같은 색 (gui/theme.py)
BODY = (43, 43, 48, 255)        # SURFACE
BODY_EDGE = (58, 58, 64, 255)   # BORDER
ACCENT = (76, 175, 80, 255)     # keep 초록 — 셀렉트 도구라는 성격을 드러냅니다
GLASS = (35, 35, 38, 255)       # BACKGROUND
HILIGHT = (221, 221, 221, 255)  # TEXT

SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw_icon(size: int) -> Image.Image:
    """한 크기의 아이콘을 그립니다.

    큰 크기에서 그린 뒤 줄이면 가장자리가 뭉개져서, 크기마다 좌표를
    비율로 계산해 직접 그립니다.
    """
    # 4배로 그린 뒤 줄여서 가장자리를 부드럽게 합니다
    scale = 4
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def px(fraction: float) -> float:
        return fraction * s

    # 바디 — 둥근 사각형
    body_box = (px(0.06), px(0.24), px(0.94), px(0.88))
    radius = px(0.12)
    draw.rounded_rectangle(body_box, radius=radius, fill=BODY, outline=BODY_EDGE,
                           width=max(1, int(px(0.015))))

    # 상단 뷰파인더 돌출부
    draw.rounded_rectangle(
        (px(0.30), px(0.13), px(0.58), px(0.28)),
        radius=px(0.04), fill=BODY, outline=BODY_EDGE,
        width=max(1, int(px(0.012))),
    )

    # 렌즈 — 바깥 링(초록)과 유리
    cx, cy = px(0.50), px(0.57)
    outer = px(0.255)
    draw.ellipse((cx - outer, cy - outer, cx + outer, cy + outer),
                 fill=ACCENT)
    inner = px(0.195)
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner),
                 fill=GLASS)

    if size >= 32:
        # 유리 반사 — 작은 크기에서는 점처럼 보여 오히려 지저분합니다
        glint = px(0.075)
        gx, gy = cx - px(0.075), cy - px(0.085)
        draw.ellipse((gx - glint, gy - glint, gx + glint, gy + glint),
                     fill=(255, 255, 255, 70))

    if size >= 24:
        # 셔터 버튼
        draw.ellipse(
            (px(0.76), px(0.30), px(0.86), px(0.40)), fill=HILIGHT
        )

    return image.resize((size, size), Image.LANCZOS)


#: macOS .icns가 요구하는 (파일이름, 픽셀크기). @2x는 레티나용으로 두 배를
#: 같은 이름에 얹는 애플의 규칙이라 512@2x = 1024까지 필요합니다.
ICONSET = (
    ("icon_16x16", 16), ("icon_16x16@2x", 32),
    ("icon_32x32", 32), ("icon_32x32@2x", 64),
    ("icon_128x128", 128), ("icon_128x128@2x", 256),
    ("icon_256x256", 256), ("icon_256x256@2x", 512),
    ("icon_512x512", 512), ("icon_512x512@2x", 1024),
)


def build_icns() -> Path | None:
    """macOS 앱 번들용 .icns를 만듭니다. 맥이 아니면 건너뜁니다.

    .ico는 맥에서 쓰이지 않습니다 — 번들 아이콘은 .icns여야 하고, Dock과
    Finder가 레티나에서 1024px까지 씁니다. 저장소의 icon.png는 256px이라
    그대로 늘리면 뭉개지므로, 크기마다 draw_icon()으로 다시 그립니다.

    변환은 애플의 iconutil에 맡깁니다(맥에 기본 포함). Pillow의 ICNS 쓰기는
    지원 크기가 제한적입니다.
    """
    import shutil
    import subprocess
    import sys
    import tempfile

    if sys.platform != "darwin":
        return None
    if shutil.which("iconutil") is None:
        print("iconutil이 없어 .icns를 건너뜁니다")
        return None

    target = ASSETS / "icon.icns"
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        drawn: dict[int, Image.Image] = {}
        for name, size in ICONSET:
            if size not in drawn:
                drawn[size] = draw_icon(size)
            drawn[size].save(iconset / f"{name}.png", format="PNG")
        result = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            capture_output=True, text=True,
        )
    if result.returncode != 0:
        print(f"iconutil 실패: {result.stderr.strip()}")
        return None
    return target


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    frames = [draw_icon(size) for size in SIZES]

    # ICO는 첫 이미지에 나머지를 sizes로 얹는 방식입니다
    frames[-1].save(ICON, format="ICO", sizes=[(s, s) for s in SIZES])

    # 미리보기용 PNG도 하나 남깁니다 (README나 macOS 변환에 씁니다)
    frames[-1].save(ASSETS / "icon.png", format="PNG")

    print(f"만들었습니다: {ICON}  ({ICON.stat().st_size / 1024:.1f}KB)")
    print(f"             {ASSETS / 'icon.png'}")
    print(f"포함 크기: {', '.join(str(s) for s in SIZES)}")

    icns = build_icns()
    if icns is not None:
        print(f"             {icns}  ({icns.stat().st_size / 1024:.1f}KB)")
        print(f"icns 크기: {', '.join(str(s) for _, s in ICONSET)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
