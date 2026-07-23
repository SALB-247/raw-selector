"""A horizontal layout that wraps onto a second row when the window is narrow.

The toolbar holds a dozen controls in one row. In a plain QHBoxLayout that
row becomes a hard floor on the window width: measured with the real
interface font, the toolbar demanded 1366px in Korean and 1547px in English,
and Qt refuses to shrink a window below its layout's minimum. The lowest
default Retina resolution on an Apple Silicon MacBook is 1440x900 points
(13" MacBook Air, M1), so the English toolbar simply did not fit.

Wrapping removes the floor entirely: the minimum width becomes the widest
single control, not the sum of all of them.

Adapted from Qt's own flow layout example, with two changes that matter
here: items are centred vertically inside their row (the toolbar mixes
labels, buttons and a slider, and top-aligning them looks broken), and a
spacer that lands at the start of a wrapped row is dropped so the row does
not begin with a gap.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QSpacerItem, QWidget


class FlowLayout(QLayout):
    """Lay items out left to right, wrapping when they run out of width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setSpacing(spacing)

    # -------------------------------------------------- building

    def addItem(self, item) -> None:  # noqa: N802 - Qt's name
        self._items.append(item)

    def addSpacing(self, width: int) -> None:  # noqa: N802 - matches QBoxLayout
        """A gap between groups of controls.

        Kept as an item rather than as extra spacing so callers can go on
        writing `bar.addSpacing(16)` exactly as they did with QHBoxLayout.
        """
        self.addItem(QSpacerItem(width, 0, QSizePolicy.Fixed, QSizePolicy.Minimum))

    def addStretch(self, stretch: int = 0) -> None:  # noqa: N802, ARG002
        """Accepted and ignored.

        Stretch has no meaning once rows wrap — there is no single row left
        to distribute the slack across. Silently doing nothing keeps callers
        from having to branch on which layout they are talking to.
        """

    # -------------------------------------------------- QLayout plumbing

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt's name
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802 - Qt's name
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802 - Qt's name
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt's name
        return self._lay_out(QRect(0, 0, width, 0), place=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt's name
        super().setGeometry(rect)
        self._lay_out(rect, place=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        """What one uncramped row would need.

        Qt opens the window at this size, so a wide screen still gets the
        single row it always had.
        """
        width = 0
        height = 0
        for item in self._items:
            size = self._item_size(item)
            width += size.width() + self.spacing()
            height = max(height, size.height())
        margins = self.contentsMargins()
        return QSize(
            width + margins.left() + margins.right(),
            height + margins.top() + margins.bottom(),
        )

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt's name
        """The widest single item — this is the whole point of wrapping."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    # -------------------------------------------------- the layout itself

    @staticmethod
    def _item_size(item) -> QSize:
        """The size an item will actually take, not the one it asks for.

        `sizeHint()` alone is not enough: the thumbnail-size slider is
        `setFixedWidth(110)` and still reports a smaller hint, so measuring
        by the hint let it overflow the row instead of wrapping — and it
        pushed the buttons after it off the right edge entirely.
        """
        return (
            item.sizeHint()
            .expandedTo(item.minimumSize())
            .boundedTo(item.maximumSize())
        )

    def _lay_out(self, rect: QRect, place: bool) -> int:
        """Place the items (or just measure) and return the total height."""
        margins = self.contentsMargins()
        area = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        spacing = self.spacing()

        y = area.y()
        total_height = 0
        for row, height in self._rows(area.width(), spacing):
            if place:
                self._place_row(row, area.x(), y, height, spacing)
            y += height + spacing
            total_height += height + spacing

        if total_height:
            total_height -= spacing  # no gap after the last row
        return total_height + margins.top() + margins.bottom()

    def _rows(self, width: int, spacing: int) -> list[tuple[list, int]]:
        """Group items into rows that fit inside `width`."""
        rows: list[tuple[list, int]] = []
        current: list = []
        used = 0
        height = 0

        for item in self._items:
            size = self._item_size(item)
            needed = size.width() + (spacing if current else 0)
            if current and used + needed > width:
                rows.append((current, height))
                current = []
                used = 0
                height = 0
                # A group gap at the start of a row reads as a stray indent.
                if item.widget() is None:
                    continue
                needed = size.width()
            current.append(item)
            used += needed
            height = max(height, size.height())

        if current:
            rows.append((current, height))
        return rows

    def _place_row(self, row: list, x: int, y: int, height: int, spacing: int) -> None:
        """Centre each item vertically inside the row it landed on."""
        for item in row:
            size = self._item_size(item)
            offset = (height - size.height()) // 2
            item.setGeometry(QRect(QPoint(x, y + offset), size))
            x += size.width() + spacing
