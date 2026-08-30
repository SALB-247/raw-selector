"""Shared GUI styles.

With the same stylesheet strings scattered across several files, changing one
colour always leaves somewhere behind. They are managed in one place.
"""

from __future__ import annotations

# Colours
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
"""Something that draws attention but is not an error. Lock notices, clipping
warnings and the like."""
DANGER = "#e55757"

CLIP_SHADOW = "#5a96f5"
CLIP_HIGHLIGHT = "#ff5a5a"
"""Clipping indicator colours. The flashing colour on the image and the button
colour have to match for you to know what you are looking at."""


TOKEN_BUTTON = (
    # Substitution tokens you press to insert. They have to look like 'a piece
    # to drop in' rather than a button, so they are never mistaken for an
    # action button.
    "QPushButton { background: #33333a; color: #9fd0ff; border: 1px solid #45454e;"
    " padding: 2px 7px; border-radius: 10px; font-size: 11px; }"
    "QPushButton:hover { background: #3d3d46; color: #cfe6ff; }"
    "QPushButton:pressed { background: #4a4a55; }"
)


def clip_button(colour: str) -> str:
    """The clipping overlay toggle. On and off have to read apart at a glance.

    This button exists because the corner of the histogram was the only way
    in before: a 9px triangle you had to hit within a 22px corner, dark grey
    and effectively invisible, so pressing it often did nothing. The
    triangles are still drawn (histogram._draw_clip_markers) - they are the
    warning that tones are being cut off, and clicking them still toggles -
    but the button is what makes the state readable at a glance.
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

#: Button padding. It has to be **exactly the same value as the flashing and
#: toggle states**.
#:
#: The cause of the width jitter was not that the padding was narrow but that
#: it **differed** per state (normal 11px / flashing 12px + bold text). As
#: long as the three styles share this one constant and leave the weight
#: alone, no value makes it jitter.
#:
#: Every 1px horizontally raises the adjustment window's minimum width by
#: about 8px (measured):
#:
#:     11px -> 900px   14px -> 922px   16px -> 938px   18px -> 954px
#:
#: The screen that has to be met is the 13" MacBook Air (M1) at its default
#: Retina 1440x900 points. At 16px the adjustment window is 938px, so there
#: is room to spare.
#:
#: It used to be pinned at 11px. That was because the toolbar was a
#: single-row QHBoxLayout, so the window's lower bound rose exactly as much
#: as the buttons widened. Once the toolbar became wrapping
#: (gui/flow_layout.py) that constraint went away.
BUTTON_PADDING = "8px 16px"

BUTTON = (
    # The button sits on a panel (#2b2b30), so the background alone does not
    # show its edges. The old #3a3a3f had little brightness difference from
    # the panel and looked like "only the text is floating there". It is
    # brightened one step and given a border to show it can be pressed.
    "QPushButton { background: #43434c; color: #f0f0f4;"
    f" border: 1px solid #56565f; padding: {BUTTON_PADDING}; border-radius: 4px; }}"
    "QPushButton:hover { background: #52525d; border-color: #6e6e7a; }"
    "QPushButton:pressed { background: #35353d; }"
    "QPushButton:disabled { background: #2c2c30; color: #6a6a72;"
    " border-color: #3a3a40; }"
)

COMPACT_BUTTON = (
    # For buttons fixed at 20~30px, like the ↺ reset. On buttons like these
    # the global BUTTON's padding of 8px 16px makes the content width 0 and
    # the text is clipped entirely (confirmed by measuring pixels from an
    # offscreen grab). Only the padding is removed; the rest of the look -
    # background, border and so on - is inherited from the global style.
    "QPushButton { padding: 0px; }"
)

PRIMARY_BUTTON = (
    # The border is always 1px, but while enabled it is the same colour as the
    # background. Colouring it only when disabled would change the button size
    # by 2px per state and the toolbar would judder.
    # (The padding was dropped from 8px to 7px, so the overall size is exactly
    # what it was before.)
    "QPushButton { background: #4caf50; color: #16161a; font-weight: bold;"
    " border: 1px solid #4caf50; padding: 7px; border-radius: 4px; }"
    "QPushButton:hover { background: #5cc264; border-color: #5cc264; }"
    # Erasing the border as well when disabled left it at almost the same
    # brightness as the background (#2b2b30) and the button looked like it had
    # vanished entirely - it surfaced on the Mac as "Export is just floating
    # text". The outline is kept, like the other buttons.
    "QPushButton:disabled { background: #2c2c30; color: #6a6a72;"
    " border-color: #3a3a40; }"
)

TOGGLE_BUTTON = (
    # Whether it is on has to be visible at a glance. Showing the pressed
    # state with border shading alone is almost indistinguishable from an off
    # button in a dark theme.
    #
    # It is not switched to bold when on - even at the same padding the text
    # grows wider, the button grows, and the neighbouring buttons are pushed
    # along. The background turning the accent colour is distinction enough
    # (the same reason as ATTENTION_BUTTON).
    "QPushButton { background: #3a3a3f; color: #ddd; border: 1px solid #4a4a52;"
    f" padding: {BUTTON_PADDING}; border-radius: 4px; }}"
    "QPushButton:hover { background: #4a4a52; }"
    f"QPushButton:checked {{ background: {ACCENT}; color: #16161a;"
    " border: 1px solid #a8ccff; }"
    "QPushButton:checked:hover { background: #9dc6ff; }"
    "QPushButton:disabled { background: #2c2c30; color: #666; border-color: #35353b; }"
)

ATTENTION_BUTTON = (
    # The flashing state that says "here is what's next" (gui/attention.py).
    # Kept in the accent family so it is not confused with in-progress
    # (orange) or run (green).
    #
    # **The box size has to be exactly the same as an ordinary button.** If
    # the padding or the border thickness differs, or if font-weight is
    # switched to bold, the button widens and narrows on every flash. That
    # really happened, from padding 11 -> 12 and bold text. Only the colour
    # changes - the background turning the accent colour outright is
    # conspicuous enough.
    f"QPushButton {{ background: {ACCENT}; color: #16161a;"
    f" border: 1px solid #cfe3ff; padding: {BUTTON_PADDING}; border-radius: 4px; }}"
)

BUSY_BUTTON = (
    # On, and actually working right now. An orange distinct from on (blue)
    # says "waiting".
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
    # Up/down arrows. The default Fusion arrows are barely visible on a dark
    # background, to the point that you could not even tell it was a spin box
    # (the user marked it up on a screenshot). The pressable area is made into
    # a panel and the triangle is drawn brightly.
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
    # The arrow images are built and attached at runtime by
    # spin_arrow_style().
    # (The moment the stylesheet touches up-button, Qt stops drawing natively,
    #  so without supplying the image the arrows disappear altogether. The CSS
    #  triangle trick did not draw in Qt either - both measured and
    #  confirmed.)
)


def _arrow_icon(direction: str, size: int = 9) -> "Path | None":
    """Builds the spin box arrow PNG once and returns its path.

    QApplication has to be alive to create a QPixmap, so this is called at
    runtime. It is put in a data folder that is writable in a build too.
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
    except Exception:  # noqa: BLE001 - a missing arrow must not stop the app
        return None


def _check_icon(size: int = 15) -> "Path | None":
    """Builds the check mark PNG once and returns its path.

    Painting the on state with the background colour alone makes a blue square
    that looks like a colour swatch rather than a checkbox. The radio buttons
    got their centre dot drawn and only the checkbox was left out.
    (The moment the stylesheet touches indicator, Qt stops drawing the native
    check, so without supplying the image there is no mark at all - the same
    situation as the arrows.)
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
        # It sits on the bright accent colour, so only a dark line reads
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
    except Exception:  # noqa: BLE001 - a missing check must not stop the app
        return None


def check_icon_style() -> str:
    """Attaches the check mark image to the on state. Empty string on
    failure."""
    icon = _check_icon()
    if icon is None:
        return ""
    return f"QCheckBox::indicator:checked {{ image: url({str(icon).replace(chr(92), '/')}); }}"


def spin_arrow_style() -> str:
    """Wraps the spin box arrow images into a stylesheet. Empty string on
    failure."""
    up = _arrow_icon("up")
    down = _arrow_icon("down")
    if up is None or down is None:
        return ""
    # url() in a Qt stylesheet takes forward-slash paths
    up_path = str(up).replace("\\", "/")
    down_path = str(down).replace("\\", "/")
    return (
        f"QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{"
        f" image: url({up_path}); width: 9px; height: 9px; }}"
        f"QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{"
        f" image: url({down_path}); width: 9px; height: 9px; }}"
    )

CHECKBOX = (
    # On and off have to read apart at a glance. With the default Fusion
    # checkbox the square border sinks into the background in a dark palette
    # and it is hard to tell whether it is checked.
    f"QCheckBox {{ color: {TEXT}; spacing: 6px; }}"
    "QCheckBox::indicator, QRadioButton::indicator {"
    " width: 15px; height: 15px; }"
    "QCheckBox::indicator {"
    " background: #26262b; border: 1px solid #5a5a66; border-radius: 3px; }"
    "QCheckBox::indicator:hover { border: 1px solid #8a8a9a; }"
    f"QCheckBox::indicator:checked {{ background: {ACCENT};"
    f" border: 1px solid {ACCENT}; }}"
    "QCheckBox::indicator:disabled { background: #232326; border-color: #3a3a40; }"
    # A radio has to be a circle to be told apart from a checkbox. The on
    # state is a centre dot, but border-radius has to be repeated in the
    # checked rule too - without it, it goes back to a square and looks
    # identical to a checkbox.
    "QRadioButton::indicator {"
    " background: #26262b; border: 1px solid #5a5a66; border-radius: 8px; }"
    "QRadioButton::indicator:hover { border: 1px solid #8a8a9a; }"
    # On is a centre dot. Painting the whole background left the inside of the
    # border as a square and it could not be told apart from a checkbox
    # (measured). The dot is placed with a radial gradient.
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
    # The default chunk is grey (#595959), so on a dark background you cannot
    # see that it is progressing. The same green as the keep grade is used to
    # unify "progress/success" under one colour.
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

#: The app-wide style, covering places the per-widget stylesheets do not
#: reach.
COMBO = (
    # combobox-popup: 0 makes it use a list view instead of the native popup.
    # The default popup folds into up/down scroll arrows as soon as there are
    # more than a few items, so picking from the list means scrolling every
    # time.
    "QComboBox { background: #3a3a3f; color: #eee; padding: 4px;"
    " border-radius: 4px; combobox-popup: 0; }"
    "QComboBox QAbstractItemView { background: #2b2b30; color: #eee;"
    " border: 1px solid #4a4a52; selection-background-color: #4a5a75; }"
)

DIALOG = (
    # The app-wide dialog baseline.
    #
    # It used to be that only windows with a style applied directly, like
    # ExportDialog and CalibrationDialog, matched the app's tone, while
    # QMessageBox, QInputDialog and QFileDialog (called from 62 places in the
    # code) came up in the default Fusion look. Within one program the button
    # shapes and padding looked different from window to window.
    f"QDialog, QMessageBox {{ background: {BACKGROUND}; }}"
    f"QMessageBox QLabel {{ color: {TEXT}; }}"
    # Too narrow, a message box wraps the text over several lines and reads
    # badly
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
"""The app-wide style.

Why buttons, input fields and combos live here too: if a widget applies its
own stylesheet, that one wins. So the values here become 'the default for a
widget nobody has touched', and the special buttons (stop, primary action,
toggle) keep overriding them individually as they do now.
"""


def app_icon():
    """The icon used for the window and the taskbar.

    In a build it is in assets/ beside the exe; running from source, in the
    repository's assets/. If it is missing, an empty QIcon is returned - a
    missing icon must not stop the app coming up.
    """
    from PySide6.QtGui import QIcon

    from ..core.appinfo import app_root

    for candidate in (app_root() / "assets" / "icon.ico",
                      app_root() / "assets" / "icon.png"):
        if candidate.is_file():
            return QIcon(str(candidate))
    return QIcon()


def apply_app_theme(app) -> None:
    """Pins the whole app to dark.

    It used to paint only the QMainWindow/QDialog background with a stylesheet
    and leave the palette alone. That way the widgets with no style applied
    (message boxes, menus, progress bars) follow the OS setting. Measured, on
    a light-mode PC the app text colour #ddd sat on a #f3f3f3 background and
    was unreadable.

    The windows11 style ignores much of the palette and draws in its own
    colours, so it is switched to Fusion. Fusion follows the palette as given
    and comes out the same on macOS, so the two platforms do not diverge.
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

    # Disabled widgets are dimmed but must still stand out from the background
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(TEXT_FAINT))

    app.setPalette(palette)
    # The arrow images need a QApplication to be built, so they go on here
    app.setStyleSheet(APP_STYLE + spin_arrow_style() + check_icon_style())

    icon = app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)


def reset_button(changed: bool, size: int = 12) -> str:
    """The revert button style.

    At the default value pressing it does nothing, so it is dimmed; once the
    value has changed it is shown clearly. Only the font size differs between
    the slider and the colour wheel.

    Why the padding is spelled out: without it the global BUTTON's 8px 16px
    applies as-is, and at the fixed width of 20~24px the content width becomes
    0 and the ↺ is clipped entirely (confirmed by measuring pixels from an
    offscreen grab).
    """
    if changed:
        return (
            "QPushButton { background: #3a3a42; color: #cfe0ff;"
            " border: 1px solid #4d5b73; border-radius: 3px;"
            f" font-size: {size}px; padding: 0px; }}"
            "QPushButton:hover { background: #4a5a75; color: #fff; }"
        )
    return (
        "QPushButton { background: transparent; color: #4a4a52;"
        f" border: 1px solid {BORDER}; border-radius: 3px; font-size: {size}px;"
        " padding: 0px; }"
    )


def dialog_style(background: str = BACKGROUND) -> str:
    """The baseline style applied to a whole dialog."""
    return (
        f"QDialog {{ background: {background}; }}"
        f"QLabel, QCheckBox, QGroupBox {{ color: {TEXT}; }}"
        + BUTTON
        + INPUT
        + COMBO
    )


def window_style() -> str:
    """The main window style.

    Each area gets a different brightness to build up layers. It used to be
    that the toolbar, filter bar, grid and status bar were almost the same
    grey (81% of the screen in two colours) and you could not tell one from
    another. The photo is the protagonist, so the grid is left the darkest and
    the controls are brightened one step.
    """
    return (
        f"QMainWindow {{ background: {BACKGROUND}; }}"
        f"QLabel, QCheckBox {{ color: {TEXT}; }}"
        # The toolbar and filter rows sit on a panel one step brighter
        f"QToolBar, #toolbar, #filterbar {{ background: {SURFACE};"
        f" border-bottom: 1px solid {BORDER}; }}"
        # The photo grid is the darkest - so the thumbnails float above it
        f"QListView {{ background: #161618; border: none; }}"
        # The status bar is the only place that says "what is happening right
        # now". It used to be the same colour as the toolbar and thin as well,
        # so you could not even see that something was in progress. A bright
        # line is drawn along the top and the height raised, so it occupies
        # the bottom of the screen unmistakably.
        f"QStatusBar {{ background: {SURFACE}; color: {TEXT};"
        f" border-top: 2px solid {ACCENT}; min-height: 34px; }}"
        f"QStatusBar QLabel {{ font-size: 12px; }}"
        "QStatusBar::item { border: none; }"
        # A progress bar inside the status bar is invisible if it is the same
        # colour as the ground (SURFACE)
        f"QStatusBar QProgressBar {{ background: {BACKGROUND};"
        f" border: 1px solid {BORDER}; border-radius: 4px; height: 18px;"
        f" text-align: center; color: {TEXT}; font-size: 11px; }}"
        # The continuation line has to be an f-string too. In a plain string
        # `}}` is not an escape but two closing braces, so the whole sheet
        # fails to parse and Qt **throws all of it away** (with a single line
        # of warning).
        f"QStatusBar QProgressBar::chunk {{ background: {PROGRESS};"
        f" border-radius: 3px; }}"
        f"QSplitter::handle {{ background: {BORDER}; }}"
        + BUTTON
        + COMBO
    )


FILTER_BUTTON = (
    # Which filter is on has to be visible at a glance. The pressed state used
    # to be nothing but a faint shading, so you could have it on and not know.
    "QPushButton { background: transparent; color: #9a9aa2;"
    " border: 1px solid #3a3a40; padding: 5px 14px; border-radius: 13px; }"
    "QPushButton:hover { background: #2f2f35; color: #ddd; }"
    f"QPushButton:checked {{ background: {ACCENT}; color: #16161a;"
    " border-color: #a8ccff; font-weight: bold; }"
)


def grade_button(grade: str) -> str:
    """A grade button has to wear that grade's colour for you to know what you
    are pressing."""
    colour = GRADE_COLORS.get(grade, "#4a4a52")
    return (
        f"QPushButton {{ background: transparent; color: {colour};"
        f" border: 1px solid {colour}; padding: 5px 14px; border-radius: 4px;"
        " font-weight: bold; }"
        f"QPushButton:hover {{ background: {colour}; color: #16161a; }}"
        f"QPushButton:checked {{ background: {colour}; color: #16161a; }}"
    )


def hint_label(color: str = TEXT_FAINT, size: int = 11) -> str:
    """The style for a secondary explanation label."""
    return f"color: {color}; font-size: {size}px;"
