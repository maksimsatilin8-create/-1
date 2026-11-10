from PyQt5.QtWidgets import QWidget, QApplication
from PyQt5.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt5.QtGui import QPainter, QColor, QPen, QFont


class RegionSelector(QWidget):
    region_selected = pyqtSignal(QRect)
    cancelled = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint |
                            Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(QApplication.primaryScreen().virtualGeometry())
        self.start = QPoint()
        self.end = QPoint()
        self.drawing = False
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(0, 0, 0, 140))
        if self.drawing:
            r = QRect(self.start, self.end).normalized()
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(r, Qt.transparent)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            p.setPen(QPen(QColor(0, 200, 255), 2))
            p.drawRect(r)
            p.setPen(Qt.white)
            p.setFont(QFont("Arial", 10, QFont.Bold))
            tx = r.x() + 4
            ty = r.y() - 8 if r.y() > 20 else r.y() + r.height() + 16
            p.drawText(tx, ty, f"{r.width()} × {r.height()}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.start = self.end = e.pos()
            self.drawing = True
            self.update()

    def mouseMoveEvent(self, e):
        if self.drawing:
            self.end = e.pos()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.drawing:
            self.end = e.pos()
            self.drawing = False
            r = QRect(self.start, self.end).normalized()
            if r.width() > 5 and r.height() > 5:
                tl = self.mapToGlobal(r.topLeft())
                br = self.mapToGlobal(r.bottomRight())
                self.region_selected.emit(QRect(tl, br).normalized())
            else:
                self.cancelled.emit()
            self.close()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()