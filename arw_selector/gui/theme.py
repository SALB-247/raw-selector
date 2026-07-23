"""GUI 공통 스타일.

같은 스타일시트 문자열이 여러 파일에 흩어져 있으면 색 하나를 바꿀 때
빠뜨리는 곳이 생깁니다. 한곳에서 관리합니다.
"""

from __future__ import annotations

# 색상
BACKGROUND = "#232326"
SURFACE = "#2b2b30"
SURFACE_HOVER = "#35353b"
FIELD = "#303035"
BORDER = "#3a3a40"
TEXT = "#ddd"
TEXT_DIM = "#9a9aa2"
TEXT_FAINT = "#7a7a82"
ACCENT = "#7fb3ff"
SELECTION = "#3d5a80"
PROGRESS = "#4caf50"
WARNING = "#ffa726"
"""주의를 끌되 오류는 아닌 것. 잠금 안내, 클리핑 경고 등."""
DANGER = "#e55757"

CLIP_SHADOW = "#5a96f5"
CLIP_HIGHLIGHT = "#ff5a5a"
"""클리핑 표시 색. 이미지 위 점멸 색과 버튼 색이 같아야 뭘 보는지 압니다."""


TOKEN_BUTTON = (
    # 눌러서 넣는 치환 항목. 버튼이라기보다 '집어 넣을 조각'처럼 보여야
    # 실수로 실행 버튼과 헷갈리지 않습니다.
    "QPushButton { background: #33333a; color: #9fd0ff; border: 1px solid #45454e;"
    " padding: 2px 7px; border-radius: 10px; font-size: 11px; }"
    "QPushButton:hover { background: #3d3d46; color: #cfe6ff; }"
    "QPushButton:pressed { background: #4a4a55; }"
)


def clip_button(colour: str) -> str:
    """클리핑 오버레이 토글. 켜짐/꺼짐이 한눈에 갈려야 합니다.

    예전에는 히스토그램 모서리의 9px 삼각형이었습니다. 어두운 회색이라
    보이지 않았고, 22px 코너를 정확히 눌러야 해서 눌러도 안 눌렸습니다.
    """
    return (
        "QPushButton { background: transparent; color: %s;"
        " border: 1px solid %s; padding: 3px 8px; border-radius: 3px;"
        " font-size: 11px; }"
        "QPushButton:hover { background: %s; }"
        "QPushButton:checked { background: %s; color: #16161a;"
        " font-weight: bold; }"
    ) % (colour, BORDER, SURFACE_HOVER, colour)

GRADE_COLORS = {
    "keep": "#4caf50",
    "review": "#ffa726",
    "reject": "#e55757",
}

#: 버튼 여백. **점멸·토글 상태와 정확히 같은 값**이어야 합니다.
#:
#: 폭이 흔들리던 원인은 여백이 좁아서가 아니라 상태마다 **달라서**였습니다
#: (일반 11px / 점멸 12px + 굵은 글씨). 세 스타일이 이 상수 하나를 함께
#: 쓰고 굵기를 건드리지 않으면 어떤 값이어도 흔들리지 않습니다.
#:
#: 가로 1px당 보정 창 최소 폭이 약 8px 늘어납니다 (실측):
#:
#:     11px → 900px   14px → 922px   16px → 938px   18px → 954px
#:
#: 맞춰야 하는 화면은 13" MacBook Air (M1)의 기본 Retina인 1440x900
#: points입니다. 16px에서 보정 창이 938px이니 여유가 있습니다.
#:
#: 예전에는 11px에 묶여 있었습니다. 툴바가 한 줄 QHBoxLayout이라 버튼이
#: 넓어지는 만큼 창의 하한이 그대로 올라갔기 때문입니다. 툴바가 접히게
#: 되면서(gui/flow_layout.py) 그 제약이 없어졌습니다.
BUTTON_PADDING = "8px 16px"

BUTTON = (
    # 버튼이 판(#2b2b30) 위에 얹혀 있으므로 배경만으로는 경계가 안 보입니다.
    # 예전 #3a3a3f는 판과 명도 차이가 작아 "글자만 떠 있는" 모습이었습니다.
    # 한 단계 밝히고 테두리를 줘서 누를 수 있는 것임을 드러냅니다.
    "QPushButton { background: #43434c; color: #f0f0f4;"
    f" border: 1px solid #56565f; padding: {BUTTON_PADDING}; border-radius: 4px; }}"
    "QPushButton:hover { background: #52525d; border-color: #6e6e7a; }"
    "QPushButton:pressed { background: #35353d; }"
    "QPushButton:disabled { background: #2c2c30; color: #6a6a72;"
    " border-color: #3a3a40; }"
)

PRIMARY_BUTTON = (
    # 테두리를 늘 1px 두되 켜져 있을 때는 배경과 같은 색으로 둡니다. 꺼질 때만
    # 색을 주면 버튼 크기가 상태에 따라 2px씩 달라져 툴바가 들썩입니다.
    # (padding을 8px에서 7px로 내려 전체 크기는 예전 그대로입니다.)
    "QPushButton { background: #4caf50; color: #16161a; font-weight: bold;"
    " border: 1px solid #4caf50; padding: 7px; border-radius: 4px; }"
    "QPushButton:hover { background: #5cc264; border-color: #5cc264; }"
    # 꺼졌을 때 테두리까지 지우면 배경(#2b2b30)과 명도가 거의 같아 버튼이
    # 통째로 사라진 것처럼 보였습니다 — 맥에서 "Export가 글자만 떠 있다"로
    # 드러났습니다. 다른 버튼들처럼 윤곽은 남깁니다.
    "QPushButton:disabled { background: #2c2c30; color: #6a6a72;"
    " border-color: #3a3a40; }"
)

TOGGLE_BUTTON = (
    # 켜져 있는지 한눈에 보여야 합니다. 눌린 상태를 테두리 음영만으로
    # 표시하면 어두운 테마에서 꺼진 버튼과 구분이 거의 안 됩니다.
    #
    # 켤 때 굵게 바꾸지 않습니다 — 같은 여백이어도 글자 폭이 늘어 버튼이
    # 커지고, 옆 버튼들이 밀립니다. 배경이 강조색으로 바뀌는 것만으로
    # 충분히 구분됩니다 (ATTENTION_BUTTON과 같은 이유).
    "QPushButton { background: #3a3a3f; color: #ddd; border: 1px solid #4a4a52;"
    f" padding: {BUTTON_PADDING}; border-radius: 4px; }}"
    "QPushButton:hover { background: #4a4a52; }"
    f"QPushButton:checked {{ background: {ACCENT}; color: #16161a;"
    " border: 1px solid #a8ccff; }"
    "QPushButton:checked:hover { background: #9dc6ff; }"
    "QPushButton:disabled { background: #2c2c30; color: #666; border-color: #35353b; }"
)

ATTENTION_BUTTON = (
    # "다음은 여기입니다"를 알리는 점멸 상태(gui/attention.py). 강조색 계열로
    # 두어 진행 중(주황)이나 실행(초록)과 헷갈리지 않게 합니다.
    #
    # **상자 크기는 일반 버튼과 완전히 같아야 합니다.** 여백이나 테두리
    # 두께가 다르면, 또는 font-weight를 굵게 바꾸면 점멸할 때마다 버튼이
    # 넓어졌다 좁아졌다 합니다. 여백 11→12과 굵은 글씨 때문에 실제로
    # 그랬습니다. 색만 바꿉니다 — 배경이 강조색으로 통째로 바뀌는 것만으로
    # 충분히 눈에 띕니다.
    f"QPushButton {{ background: {ACCENT}; color: #16161a;"
    f" border: 1px solid #cfe3ff; padding: {BUTTON_PADDING}; border-radius: 4px; }}"
)

BUSY_BUTTON = (
    # 켜져 있고 지금 실제로 작업 중인 상태. 켜짐(파랑)과 구분되는 주황으로
    # "기다리는 중"을 알립니다.
    "QPushButton { background: #d8952f; color: #16161a; border: 1px solid #f0b45a;"
    " padding: 6px 12px; border-radius: 4px; font-weight: bold; }"
)

DANGER_BUTTON = (
    "QPushButton { background: #a04040; color: #eee; border: none;"
    " padding: 6px 12px; border-radius: 4px; }"
    "QPushButton:hover { background: #b55050; }"
)

INPUT = (
    "QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {"
    " background: #303035; color: #eee; border: 1px solid #4c4c55;"
    " border-radius: 3px; padding: 3px; }"
    f"QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{"
    f" border: 1px solid {ACCENT}; }}"
    # 위/아래 화살표. 기본 Fusion 화살표는 어두운 바탕에서 거의 안 보여서
    # 스핀박스인지조차 알 수 없었습니다(사용자가 스크린샷에 표시).
    # 눌리는 자리를 판으로 만들고 삼각형을 밝게 그립니다.
    "QSpinBox::up-button, QDoubleSpinBox::up-button,"
    " QSpinBox::down-button, QDoubleSpinBox::down-button {"
    " background: #45454f; border: none; width: 17px; }"
    "QSpinBox::up-button, QDoubleSpinBox::up-button {"
    " subcontrol-origin: border; subcontrol-position: top right;"
    " border-top-right-radius: 3px; }"
    "QSpinBox::down-button, QDoubleSpinBox::down-button {"
    " subcontrol-origin: border; subcontrol-position: bottom right;"
    " border-bottom-right-radius: 3px; }"
    "QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,"
    " QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {"
    " background: #5a5a68; }"
    # 화살표 그림은 spin_arrow_style()이 실행 시점에 만들어 붙입니다.
    # (스타일시트로 up-button을 건드리는 순간 Qt가 네이티브 그리기를
    #  그만두기 때문에, 그림을 직접 주지 않으면 화살표가 아예 사라집니다.
    #  CSS 삼각형 트릭도 Qt에서는 그려지지 않았습니다 — 둘 다 실측 확인.)
)


def _arrow_icon(direction: str, size: int = 9) -> "Path | None":
    """스핀박스 화살표 PNG를 한 번 만들어 두고 경로를 돌려줍니다.

    QApplication이 살아 있어야 QPixmap을 만들 수 있으므로 실행 시점에
    부릅니다. 배포본에서도 쓰기 가능한 데이터 폴더에 둡니다.
    """
    from pathlib import Path

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygonF

    from ..core.appinfo import user_state_dir

    try:
        folder = Path(user_state_dir()) / "ui"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"spin_{direction}_{size}.png"
        if target.is_file():
            return target

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e2e2ea"))
        if direction == "up":
            points = [QPointF(size / 2, 1.0), QPointF(size - 1.0, size - 2.0),
                      QPointF(1.0, size - 2.0)]
        else:
            points = [QPointF(size / 2, size - 1.0), QPointF(1.0, 2.0),
                      QPointF(size - 1.0, 2.0)]
        painter.drawPolygon(QPolygonF(points))
        painter.end()
        return target if pixmap.save(str(target), "PNG") else None
    except Exception:  # noqa: BLE001 - 화살표가 없다고 앱이 안 뜨면 안 됩니다
        return None


def _check_icon(size: int = 15) -> "Path | None":
    """체크 표시 PNG를 한 번 만들어 두고 경로를 돌려줍니다.

    켜짐을 배경색으로만 칠하면 파란 네모가 되어 체크박스가 아니라 색 견본처럼
    보입니다. 라디오 버튼은 가운데 점을 그려 주면서 체크박스만 빠져 있었습니다.
    (스타일시트로 indicator를 건드리는 순간 Qt가 네이티브 체크를 그만 그리므로
    그림을 직접 주지 않으면 표시가 아예 없습니다 — 화살표와 같은 사정입니다.)
    """
    from pathlib import Path

    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

    from ..core.appinfo import user_state_dir

    try:
        folder = Path(user_state_dir()) / "ui"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"check_{size}.png"
        if target.is_file():
            return target

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        # 밝은 강조색 위에 얹히므로 어두운 선이라야 읽힙니다
        pen = QPen(QColor("#16161a"))
        pen.setWidthF(max(1.8, size / 7.0))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([
            QPointF(size * 0.24, size * 0.52),
            QPointF(size * 0.43, size * 0.71),
            QPointF(size * 0.77, size * 0.30),
        ]))
        painter.end()
        return target if pixmap.save(str(target), "PNG") else None
    except Exception:  # noqa: BLE001 - 체크 표시가 없다고 앱이 안 뜨면 안 됩니다
        return None


def check_icon_style() -> str:
    """체크 표시 그림을 켜짐 상태에 붙입니다. 실패하면 빈 문자열."""
    icon = _check_icon()
    if icon is None:
        return ""
    return f"QCheckBox::indicator:checked {{ image: url({str(icon).replace(chr(92), '/')}); }}"


def spin_arrow_style() -> str:
    """스핀박스 화살표 그림을 스타일시트로 묶습니다. 실패하면 빈 문자열."""
    up = _arrow_icon("up")
    down = _arrow_icon("down")
    if up is None or down is None:
        return ""
    # Qt 스타일시트의 url()은 슬래시 경로를 씁니다
    up_path = str(up).replace("\\", "/")
    down_path = str(down).replace("\\", "/")
    return (
        f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{"
        f" image: url({up_path}); width: 9px; height: 9px; }}"
        f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{"
        f" image: url({down_path}); width: 9px; height: 9px; }}"
    )

CHECKBOX = (
    # 켜짐/꺼짐이 한눈에 갈려야 합니다. Fusion 기본 체크박스는 어두운
    # 팔레트에서 네모 테두리가 배경에 묻혀 체크 여부를 알기 어렵습니다.
    f"QCheckBox {{ color: {TEXT}; spacing: 6px; }}"
    "QCheckBox::indicator, QRadioButton::indicator {"
    " width: 15px; height: 15px; }"
    "QCheckBox::indicator {"
    " background: #26262b; border: 1px solid #5a5a66; border-radius: 3px; }"
    "QCheckBox::indicator:hover { border: 1px solid #8a8a9a; }"
    f"QCheckBox::indicator:checked {{ background: {ACCENT};"
    f" border: 1px solid {ACCENT}; }}"
    "QCheckBox::indicator:disabled { background: #232326; border-color: #3a3a40; }"
    # 라디오는 동그라미여야 체크박스와 구분됩니다. 켜짐은 가운데 점으로
    # 표현하되, border-radius를 켜짐 규칙에도 다시 써야 합니다 —
    # 안 쓰면 네모로 돌아가서 체크박스와 똑같이 보입니다.
    "QRadioButton::indicator {"
    " background: #26262b; border: 1px solid #5a5a66; border-radius: 8px; }"
    "QRadioButton::indicator:hover { border: 1px solid #8a8a9a; }"
    # 켜짐은 가운데 점. 배경을 통째로 칠하면 테두리 안이 네모로 남아
    # 체크박스와 구별이 안 됐습니다(실측). 방사형 그라디언트로 점을 찍습니다.
    f"QRadioButton::indicator:checked {{"
    f" background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,"
    f" fx:0.5, fy:0.5, stop:0 {ACCENT}, stop:0.5 {ACCENT},"
    f" stop:0.55 #26262b, stop:1 #26262b);"
    f" border: 1px solid {ACCENT}; border-radius: 8px; }}"
    f"QRadioButton {{ color: {TEXT}; spacing: 6px; }}"
)

TABLE = (
    "QTableWidget { background: #232326; color: #ccc;"
    " border: 1px solid #3a3a3f; gridline-color: #303035; }"
    "QTableWidget::item:selected { background: #3d5a80; }"
    "QHeaderView::section { background: #2b2b30; color: #bbb;"
    " border: none; padding: 4px; }"
)

SECTION_HEADER = (
    "QPushButton { background: #2b2b30; color: #ddd; border: none;"
    " padding: 7px 8px; text-align: left; font-weight: bold; }"
    "QPushButton:hover { background: #35353b; }"
    "QPushButton:checked { background: #35353b; color: #fff; }"
)

EYE_BUTTON = (
    "QPushButton { background: #2b2b30; color: #7fb3ff; border: none;"
    " padding: 7px 0; font-size: 13px; }"
    "QPushButton:hover { background: #35353b; }"
    "QPushButton:!checked { color: #5a5a62; }"
)

GROUP_BOX = (
    "QGroupBox { border: 1px solid #3a3a40; border-radius: 4px;"
    " margin-top: 8px; padding-top: 8px; color: #ddd; }"
    "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
)


PROGRESS_BAR = (
    # 기본 청크는 회색(#595959)이라 어두운 바탕에서 진행 중인지 눈에 안 띕니다.
    # keep 등급과 같은 초록을 써서 "진행/성공"을 한 색으로 통일합니다.
    f"QProgressBar {{ background: {SURFACE}; border: 1px solid {BORDER};"
    f" border-radius: 4px; text-align: center; color: {TEXT}; }}"
    f"QProgressBar::chunk {{ background: {PROGRESS}; border-radius: 3px; }}"
)

SCROLLBAR = (
    f"QScrollBar:vertical {{ background: {BACKGROUND}; width: 12px; margin: 0; }}"
    f"QScrollBar:horizontal {{ background: {BACKGROUND}; height: 12px; margin: 0; }}"
    "QScrollBar::handle:vertical { background: #4a4a52; border-radius: 5px;"
    " min-height: 24px; }"
    "QScrollBar::handle:horizontal { background: #4a4a52; border-radius: 5px;"
    " min-width: 24px; }"
    "QScrollBar::handle:hover { background: #5a5a64; }"
    "QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }"
    "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
)

MENU = (
    f"QMenu {{ background: {SURFACE}; color: {TEXT};"
    " border: 1px solid #4a4a52; }"
    "QMenu::item:selected { background: #4a5a75; }"
    f"QToolTip {{ background: {SURFACE}; color: {TEXT};"
    " border: 1px solid #4a4a52; }"
)

#: 위젯별 스타일시트가 닿지 않는 곳까지 덮는 앱 전역 스타일입니다.
COMBO = (
    # combobox-popup: 0 은 네이티브 팝업 대신 목록 뷰를 쓰게 합니다. 기본
    # 팝업은 항목이 조금만 많아도 위아래 스크롤 화살표로 접혀서, 목록을
    # 고르려면 매번 스크롤해야 합니다.
    "QComboBox { background: #3a3a3f; color: #eee; padding: 4px;"
    " border-radius: 4px; combobox-popup: 0; }"
    "QComboBox QAbstractItemView { background: #2b2b30; color: #eee;"
    " border: 1px solid #4a4a52; selection-background-color: #4a5a75; }"
)

DIALOG = (
    # 앱 전체에 걸리는 대화상자 기본형.
    #
    # 예전에는 ExportDialog·CalibrationDialog처럼 직접 스타일을 건 창만
    # 앱과 같은 톤이었고, QMessageBox·QInputDialog·QFileDialog(코드 62곳에서
    # 부릅니다)는 Fusion 기본 모습이었습니다. 같은 프로그램인데 창마다
    # 버튼 생김새와 여백이 달라 보였습니다.
    f"QDialog, QMessageBox {{ background: {BACKGROUND}; }}"
    f"QMessageBox QLabel {{ color: {TEXT}; }}"
    # 메시지 상자가 너무 좁으면 문구가 여러 줄로 접혀 읽기 나쁩니다
    "QMessageBox { min-width: 340px; }"
    f"QGroupBox {{ border: 1px solid {BORDER}; border-radius: 4px;"
    f" margin-top: 9px; padding-top: 9px; color: {TEXT}; }}"
    "QGroupBox::title { subcontrol-origin: margin; left: 9px;"
    " padding: 0 4px; }"
    f"QToolTip {{ background: {SURFACE}; color: {TEXT};"
    f" border: 1px solid {BORDER}; padding: 4px; }}"
)

APP_STYLE = (PROGRESS_BAR + SCROLLBAR + MENU + DIALOG + BUTTON + INPUT
             + COMBO + CHECKBOX)
"""앱 전체 스타일.

버튼·입력칸·콤보까지 여기에 두는 이유: 위젯이 자기 스타일시트를 따로 걸면
그쪽이 이깁니다. 그래서 여기 값은 '아무도 손대지 않은 위젯의 기본'이 되고,
특별한 버튼(중단·기본 동작·토글)은 지금처럼 각자 계속 덮어씁니다.
"""


def app_icon():
    """창과 작업표시줄에 쓰는 아이콘.

    빌드본에서는 exe 옆 assets/, 소스 실행에서는 저장소 assets/ 에 있습니다.
    없으면 빈 QIcon을 돌려줍니다 — 아이콘이 없다고 앱이 안 뜨면 안 됩니다.
    """
    from PySide6.QtGui import QIcon

    from ..core.appinfo import app_root

    for candidate in (app_root() / "assets" / "icon.ico",
                      app_root() / "assets" / "icon.png"):
        if candidate.is_file():
            return QIcon(str(candidate))
    return QIcon()


def apply_app_theme(app) -> None:
    """앱 전체를 다크로 고정합니다.

    예전에는 QMainWindow/QDialog 배경만 스타일시트로 칠하고 팔레트는 그대로
    뒀습니다. 그러면 스타일이 안 걸린 위젯(메시지 상자, 메뉴, 진행 막대)이
    OS 설정을 따라갑니다. 실측하니 라이트 모드 PC에서는 앱 글자색 #ddd 가
    #f3f3f3 바탕에 얹혀 읽을 수 없었습니다.

    windows11 스타일은 팔레트를 상당 부분 무시하고 자기 색으로 그리므로
    Fusion으로 바꿉니다. Fusion은 팔레트를 그대로 따르고, macOS에서도 같은
    모습이 나와 두 플랫폼의 화면이 갈라지지 않습니다.
    """
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BACKGROUND))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(FIELD))
    palette.setColor(QPalette.AlternateBase, QColor(SURFACE))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ToolTipBase, QColor(SURFACE))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    palette.setColor(QPalette.PlaceholderText, QColor(TEXT_FAINT))
    palette.setColor(QPalette.Link, QColor(ACCENT))
    palette.setColor(QPalette.Highlight, QColor(SELECTION))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))

    # 비활성 위젯은 흐리되 배경과 구분은 되어야 합니다
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_FAINT))

    app.setPalette(palette)
    # 화살표 그림은 QApplication이 있어야 만들 수 있어 여기서 붙입니다
    app.setStyleSheet(APP_STYLE + spin_arrow_style() + check_icon_style())

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)


def reset_button(changed: bool, size: int = 12) -> str:
    """되돌리기 버튼 스타일.

    기본값이면 눌러도 소용없으므로 흐리게, 값이 바뀌었으면 또렷하게
    표시합니다. 슬라이더와 색상휠에서 글자 크기만 다릅니다.
    """
    if changed:
        return (
            "QPushButton { background: #3a3a42; color: #cfe0ff;"
            " border: 1px solid #4d5b73; border-radius: 3px;"
            f" font-size: {size}px; }}"
            "QPushButton:hover { background: #4a5a75; color: #fff; }"
        )
    return (
        "QPushButton { background: transparent; color: #4a4a52;"
        f" border: 1px solid {BORDER}; border-radius: 3px; font-size: {size}px; }}"
    )


def dialog_style(background: str = BACKGROUND) -> str:
    """대화상자 전체에 적용하는 기본 스타일."""
    return (
        f"QDialog {{ background: {background}; }}"
        f"QLabel, QCheckBox, QGroupBox {{ color: {TEXT}; }}"
        + BUTTON
        + INPUT
        + COMBO
    )


def window_style() -> str:
    """메인 창 스타일.

    영역마다 밝기를 달리해 층을 만듭니다. 예전에는 툴바·필터·격자·상태바가
    거의 같은 회색이라(화면의 81%가 두 색) 어디가 어디인지 구분이 안 됐습니다.
    사진이 주인공이므로 격자를 가장 어둡게 두고, 조작부를 한 단계 밝힙니다.
    """
    return (
        f"QMainWindow {{ background: {BACKGROUND}; }}"
        f"QLabel, QCheckBox {{ color: {TEXT}; }}"
        # 툴바·필터 줄은 한 단계 밝은 판 위에
        f"QToolBar, #toolbar, #filterbar {{ background: {SURFACE};"
        f" border-bottom: 1px solid {BORDER}; }}"
        # 사진 격자는 가장 어둡게 — 썸네일이 떠 보이게
        f"QListView {{ background: #161618; border: none; }}"
        # 상태바는 "지금 무슨 일이 일어나는가"를 알리는 유일한 자리입니다.
        # 예전에는 툴바와 같은 색에 높이도 얇아 진행 중인지조차 안 보였습니다.
        # 위쪽에 밝은 선을 긋고 높이를 키워 화면 바닥을 확실히 점유하게 합니다.
        f"QStatusBar {{ background: {SURFACE}; color: {TEXT};"
        f" border-top: 2px solid {ACCENT}; min-height: 34px; }}"
        f"QStatusBar QLabel {{ font-size: 12px; }}"
        "QStatusBar::item { border: none; }"
        # 상태바 안의 진행바는 바탕(SURFACE)과 같은 색이면 안 보입니다
        f"QStatusBar QProgressBar {{ background: {BACKGROUND};"
        f" border: 1px solid {BORDER}; border-radius: 4px; height: 18px;"
        f" text-align: center; color: {TEXT}; font-size: 11px; }}"
        # 이어지는 줄도 f-string 이어야 합니다. 평범한 문자열에서 `}}`는
        # 이스케이프가 아니라 닫는 중괄호 두 개라, 시트 전체가 파싱에
        # 실패하고 Qt가 **통째로 버립니다** (경고 한 줄만 나옵니다).
        f"QStatusBar QProgressBar::chunk {{ background: {PROGRESS};"
        f" border-radius: 3px; }}"
        f"QSplitter::handle {{ background: {BORDER}; }}"
        + BUTTON
        + COMBO
    )


FILTER_BUTTON = (
    # 지금 어떤 필터가 켜져 있는지 한눈에 보여야 합니다. 예전에는 눌린
    # 상태가 미세한 음영뿐이라 켜 놓고도 몰랐습니다.
    "QPushButton { background: transparent; color: #9a9aa2;"
    " border: 1px solid #3a3a40; padding: 5px 14px; border-radius: 13px; }"
    "QPushButton:hover { background: #2f2f35; color: #ddd; }"
    f"QPushButton:checked {{ background: {ACCENT}; color: #16161a;"
    " border-color: #a8ccff; font-weight: bold; }"
)


def grade_button(grade: str) -> str:
    """등급 버튼은 그 등급의 색을 입어야 무엇을 누르는지 압니다."""
    colour = GRADE_COLORS.get(grade, "#4a4a52")
    return (
        f"QPushButton {{ background: transparent; color: {colour};"
        f" border: 1px solid {colour}; padding: 5px 14px; border-radius: 4px;"
        " font-weight: bold; }"
        f"QPushButton:hover {{ background: {colour}; color: #16161a; }}"
        f"QPushButton:checked {{ background: {colour}; color: #16161a; }}"
    )


def hint_label(color: str = TEXT_FAINT, size: int = 11) -> str:
    """보조 설명 라벨 스타일."""
    return f"color: {color}; font-size: {size}px;"
