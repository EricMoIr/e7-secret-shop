"""Marking rectangles on a game screenshot (PLAN.md §9.4).

`RegionPicker` shows the screenshot: the mouse wheel zooms, a right or middle drag pans, and a left
drag draws the selected region. `MarkingPanel` adds the list of regions a wizard page needs, with
the crop and OCR check of the selected one.

The pixmap sits at scene (0, 0) at scale 1, so scene coordinates are image pixels, which are
client-area coordinates.
"""

from collections.abc import Callable
from dataclasses import dataclass

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPixmap, QResizeEvent, QWheelEvent
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..geometry import Rect

MIN_SIZE = 4
REGION_COLOR = QColor(0, 200, 255)
SELECTED_COLOR = QColor(255, 210, 0)
OVERLAY_COLOR = QColor(80, 230, 120)


def to_qimage(image: Image.Image) -> QImage:
    rgb = image.convert("RGB")
    data = rgb.tobytes()
    # copy(): the QImage must not keep pointing at the Python bytes.
    return QImage(data, rgb.width, rgb.height, 3 * rgb.width, QImage.Format.Format_RGB888).copy()


def points_to_rect(a: QPointF, b: QPointF, image_size: tuple[int, int], min_size: int = MIN_SIZE) -> Rect | None:
    """The rectangle between two scene points, in whole image pixels, clamped to the image.

    None when it's smaller than `min_size` on a side (a stray click rather than a drag).
    """
    width, height = image_size
    x0, x1 = sorted((a.x(), b.x()))
    y0, y1 = sorted((a.y(), b.y()))
    x0, y0 = max(0, round(x0)), max(0, round(y0))
    x1, y1 = min(width, round(x1)), min(height, round(y1))
    if x1 - x0 < min_size or y1 - y0 < min_size:
        return None
    return Rect(x0, y0, x1, y1)


class RegionPicker(QGraphicsView):
    """The screenshot with the marked regions. Emits `drawn(rect)` when the user finishes a drag."""

    drawn = Signal(object)  # Rect

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor(30, 30, 30)))
        self.setMinimumSize(480, 320)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._image_size = (0, 0)
        self._items: list[QGraphicsRectItem | QGraphicsSimpleTextItem] = []
        self._shapes: list[tuple[Rect, str, QColor, bool]] = []
        self._rubber: QGraphicsRectItem | None = None
        self._start: QPointF | None = None
        self._pan_from: QPoint | None = None
        self._fit = True
        self.drawing_enabled = True

    # Image and view

    def set_image(self, image: Image.Image | None) -> None:
        self.scene().clear()
        self._items = []
        self._shapes = []
        self._rubber = None
        self._pixmap_item = None
        if image is None:
            self._image_size = (0, 0)
            return
        self._image_size = image.size
        self._pixmap_item = self.scene().addPixmap(QPixmap.fromImage(to_qimage(image)))
        self.scene().setSceneRect(QRectF(0, 0, *image.size))
        self.fit()

    @property
    def image_size(self) -> tuple[int, int]:
        return self._image_size

    def fit(self) -> None:
        self._fit = True
        if self._pixmap_item:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self.set_shapes(self._shapes)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._fit:
            self.fit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not self._pixmap_item:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        self._fit = False
        self.scale(factor, factor)
        self.set_shapes(self._shapes)

    # Shapes

    def set_shapes(self, shapes: list[tuple[Rect, str, QColor, bool]]) -> None:
        """Replace the drawn rectangles: (rect, label, color, label_left) each. Labels go above their box,
        or to its left when label_left is set. An empty label draws none."""
        self._shapes = shapes
        for item in self._items:
            self.scene().removeItem(item)
        self._items = []
        font = QFont()
        font.setPointSizeF(font.pointSizeF() + 1.5)
        font.setBold(True)
        # Labels ignore the zoom, so their offsets from the boxes are in screen pixels.
        scale = self.transform().m22() or 1
        for rect, label, color, left in shapes:
            self._items.append(self.scene().addRect(QRectF(rect.x0, rect.y0, rect.width, rect.height), self._pen(color)))
            if not label:
                continue
            text = QGraphicsSimpleTextItem(label)
            text.setFont(font)
            text.setBrush(QBrush(color))
            size = text.boundingRect()
            if left:
                text.setPos(rect.x0 - (size.width() + 6) / scale, rect.center[1] - size.height() / 2 / scale)
            else:
                text.setPos(rect.x0, max(rect.y0 - (size.height() + 2) / scale, 0))
            text.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self.scene().addItem(text)
            self._items.append(text)

    @staticmethod
    def _pen(color: QColor) -> QPen:
        pen = QPen(color, 2)
        pen.setCosmetic(True)  # 2 px on screen whatever the zoom
        return pen

    # Mouse

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_from = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_item and self.drawing_enabled:
            self._start = self.mapToScene(event.position().toPoint())
            self._rubber = self.scene().addRect(QRectF(self._start, self._start), self._pen(SELECTED_COLOR))
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pan_from is not None:
            delta = event.position().toPoint() - self._pan_from
            self._pan_from = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            return
        if self._start is not None and self._rubber is not None:
            end = self.mapToScene(event.position().toPoint())
            self._rubber.setRect(QRectF(self._start, end).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pan_from is not None and event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan_from = None
            self.unsetCursor()
            return
        if self._start is not None and event.button() == Qt.MouseButton.LeftButton:
            end = self.mapToScene(event.position().toPoint())
            self.finish_drag(self._start, end)
            return
        super().mouseReleaseEvent(event)

    def finish_drag(self, start: QPointF, end: QPointF) -> None:
        """End a drag between two scene points. Public so tests can draw without synthesizing mouse events."""
        if self._rubber is not None:
            self.scene().removeItem(self._rubber)
        self._rubber = None
        self._start = None
        rect = points_to_rect(start, end, self._image_size)
        if rect is not None:
            self.drawn.emit(rect)


@dataclass
class RegionSpec:
    """A region a page needs. `check` gets the region's crop and returns (passed, text to show)."""

    key: str
    label: str
    check: Callable[[Image.Image], tuple[bool, str]] | None = None


@dataclass
class _RegionState:
    spec: RegionSpec
    rect: Rect | None = None
    passed: bool = False
    result: str = ""


class MarkingPanel(QWidget):
    """The picker plus the list of regions to mark, each with its check result.

    Click a region in the list, then drag on the screenshot to mark it. Drawing again replaces it.
    Emits `changed` whenever a region is marked or re-checked.
    """

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.picker = RegionPicker()
        self.picker.drawn.connect(self._on_drawn)
        self.region_list = QListWidget()
        self.region_list.setMaximumWidth(320)
        self.region_list.currentRowChanged.connect(self._on_selected)
        self.crop_label = QLabel()
        self.crop_label.setMinimumHeight(70)
        self.crop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setMaximumWidth(320)
        fit_button = QPushButton("Fit to view")
        fit_button.clicked.connect(self.picker.fit)
        hint = QLabel("Pick a region in the list, then drag on the screenshot. Wheel zooms; right-drag pans.")
        hint.setWordWrap(True)
        hint.setMaximumWidth(320)

        side = QVBoxLayout()
        side.addWidget(self.region_list, 1)
        side.addWidget(self.crop_label)
        side.addWidget(self.result_label)
        side.addWidget(hint)
        side.addWidget(fit_button)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.picker, 1)
        layout.addLayout(side)

        self._image: Image.Image | None = None
        self._states: dict[str, _RegionState] = {}
        self._overlays: list[tuple[Rect, str]] = []

    # Setup

    def load(self, image: Image.Image | None, specs: list[RegionSpec]) -> None:
        """Show `image` and ask for `specs`, checking every region once.

        Rectangles already marked under the same key are kept. The image is only reloaded when it changed.
        """
        if image is not self._image:
            self._image = image
            self.picker.set_image(image)
        old = self._states
        self._states = {s.key: _RegionState(s, old[s.key].rect if s.key in old else None) for s in specs}
        self.region_list.blockSignals(True)
        self.region_list.clear()
        for _ in specs:
            self.region_list.addItem(QListWidgetItem())
        self.region_list.blockSignals(False)
        self.select(next((s.key for s in specs if self._states[s.key].rect is None), specs[0].key if specs else None))
        self.recheck()

    def set_image(self, image: Image.Image | None) -> None:
        self._image = image
        self.picker.set_image(image)
        self.recheck()

    def set_overlays(self, overlays: list[tuple[Rect, str]]) -> None:
        """Extra rectangles to show, e.g. the rows found from the marked one."""
        self._overlays = overlays
        self._redraw()

    def clear(self) -> None:
        for state in self._states.values():
            state.rect = None
        self._overlays = []
        self.recheck()

    # Access

    def rect(self, key: str) -> Rect | None:
        return self._states[key].rect if key in self._states else None

    def set_rect(self, key: str, rect: Rect | None) -> None:
        self._states[key].rect = rect
        self._check(self._states[key])
        self._refresh()
        self.changed.emit()

    def passed(self, key: str) -> bool:
        return key in self._states and self._states[key].passed

    def all_passed(self) -> bool:
        return bool(self._states) and all(s.passed for s in self._states.values())

    def crop(self, rect: Rect) -> Image.Image | None:
        return self._image.crop(tuple(rect)) if self._image else None

    def select(self, key: str | None) -> None:
        keys = list(self._states)
        if key in keys:
            self.region_list.setCurrentRow(keys.index(key))

    @property
    def selected_key(self) -> str | None:
        row = self.region_list.currentRow()
        keys = list(self._states)
        return keys[row] if 0 <= row < len(keys) else None

    # Checks

    def recheck(self) -> None:
        for state in self._states.values():
            self._check(state)
        self._refresh()
        self.changed.emit()

    def _check(self, state: _RegionState) -> None:
        if state.rect is None or self._image is None:
            state.passed, state.result = False, "not marked"
            return
        if state.spec.check is None:
            state.passed, state.result = True, "marked"
            return
        try:
            state.passed, state.result = state.spec.check(self._image.crop(tuple(state.rect)))
        except Exception as exc:  # an OCR failure must not crash the wizard
            state.passed, state.result = False, f"check failed: {exc}"

    # Events

    def _on_drawn(self, rect: Rect) -> None:
        key = self.selected_key
        if key is None:
            return
        self.set_rect(key, rect)
        # Move on to the next region that still needs marking.
        keys = list(self._states)
        pending = [k for k in keys[keys.index(key) + 1 :] + keys if self._states[k].rect is None]
        if pending:
            self.select(pending[0])

    def _on_selected(self, _row: int) -> None:
        self._refresh()

    def _refresh(self) -> None:
        for i, state in enumerate(self._states.values()):
            mark = "✓" if state.passed else ("✗" if state.rect else "…")
            item = self.region_list.item(i)
            if item is not None:
                item.setText(f"{mark} {state.spec.label}: {state.result}")
        key = self.selected_key
        state = self._states.get(key) if key else None
        if state and state.rect and self._image:
            pixmap = QPixmap.fromImage(to_qimage(self._image.crop(tuple(state.rect))))
            self.crop_label.setPixmap(
                pixmap.scaled(300, 120, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
            self.result_label.setText(f"{state.spec.label}: {state.result}")
        else:
            self.crop_label.clear()
            self.result_label.setText(f"Drag on the screenshot to mark: {state.spec.label}" if state else "")
        self._redraw()

    def _redraw(self) -> None:
        selected = self.selected_key
        # Overlay labels go left of their boxes, so they don't collide with the marked regions' labels above.
        shapes = [(rect, label, OVERLAY_COLOR, True) for rect, label in self._overlays]
        # Only the selected region is labelled: marked regions often nest (a price inside its button).
        for key, state in self._states.items():
            if state.rect:
                is_selected = key == selected
                shapes.append(
                    (state.rect, state.spec.label if is_selected else "", SELECTED_COLOR if is_selected else REGION_COLOR, False)
                )
        self.picker.set_shapes(shapes)
