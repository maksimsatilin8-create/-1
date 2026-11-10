from typing import Optional

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import (
    QWidget, QPushButton, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QDoubleSpinBox, QMessageBox, QCheckBox, QTextEdit,
    QApplication,
)

from config import (
    DARK_THEME_QSS, SUPPORTED_LANGS, HAS_WIN32,
    DEFAULT_INTERVAL, DEFAULT_BYPASS, DEFAULT_CANVAS_SIZE,
)
from overlay import TranslationOverlay
from region_selector import RegionSelector
from worker import TranslationWorker


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Screen Translator — Fast")
        self.resize(560, 540)
        self.setStyleSheet(DARK_THEME_QSS)
        self.region: Optional[QRect] = None
        self.worker: Optional[TranslationWorker] = None
        self.selector: Optional[RegionSelector] = None
        self.overlay = TranslationOverlay()
        self.overlay.hide()
        self._build_ui()

    # ------------- UI -------------
    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        btn_sel = QPushButton("🎯  Выделить область на экране")
        btn_sel.clicked.connect(self.select_region)
        layout.addWidget(btn_sel)

        ll = QHBoxLayout()
        ll.addWidget(QLabel("Исходный:"))
        self.src_combo = QComboBox()
        self.src_combo.addItems(SUPPORTED_LANGS)
        ll.addWidget(self.src_combo)
        ll.addWidget(QLabel("Целевой:"))
        self.tgt_combo = QComboBox()
        self.tgt_combo.addItems(SUPPORTED_LANGS)
        self.tgt_combo.setCurrentText("ru")
        ll.addWidget(self.tgt_combo)
        layout.addLayout(ll)

        il = QHBoxLayout()
        il.addWidget(QLabel("OCR:"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 10.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setDecimals(1)
        self.interval_spin.setValue(DEFAULT_INTERVAL)
        self.interval_spin.setSuffix(" с")
        il.addWidget(self.interval_spin)

        il.addWidget(QLabel("Deep refresh:"))
        self.bypass_spin = QDoubleSpinBox()
        self.bypass_spin.setRange(2.0, 120.0)
        self.bypass_spin.setDecimals(0)
        self.bypass_spin.setValue(DEFAULT_BYPASS)
        self.bypass_spin.setSuffix(" с")
        il.addWidget(self.bypass_spin)
        layout.addLayout(il)

        ol = QHBoxLayout()
        self.gpu_check = QCheckBox("GPU (CUDA)")
        self.inpaint_check = QCheckBox("Inpaint (чистый фон)")
        self.inpaint_check.setChecked(True)
        ol.addWidget(self.gpu_check)
        ol.addWidget(self.inpaint_check)
        layout.addLayout(ol)

        bl = QHBoxLayout()
        self.btn_start = QPushButton("▶  Запустить")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.start_translation)
        self.btn_stop = QPushButton("■  Стоп")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.clicked.connect(self.stop_translation)
        self.btn_stop.setEnabled(False)
        self.btn_refresh = QPushButton("🔄  Пере-распознать")
        self.btn_refresh.clicked.connect(self.manual_refresh)
        self.btn_refresh.setEnabled(False)
        bl.addWidget(self.btn_start)
        bl.addWidget(self.btn_stop)
        bl.addWidget(self.btn_refresh)
        layout.addLayout(bl)

        self.status_label = QLabel("Область не выбрана")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(220)
        layout.addWidget(self.log_box)

        self.setLayout(layout)

    # ------------- logging -------------
    def log(self, msg: str):
        self.log_box.append(msg)
        doc = self.log_box.document()
        if doc.blockCount() > 200:
            cur = self.log_box.textCursor()
            cur.movePosition(cur.Start)
            cur.movePosition(cur.Down, cur.KeepAnchor,
                             doc.blockCount() - 160)
            cur.removeSelectedText()

    # ------------- region -------------
    def select_region(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "Внимание",
                                    "Остановите перевод перед изменением области.")
            return
        self.status_label.setText("Выделите область (Esc — отмена)...")
        self.selector = RegionSelector()
        self.selector.region_selected.connect(self.on_region_selected)
        self.selector.cancelled.connect(self.on_region_cancelled)

    def on_region_selected(self, rect: QRect):
        self.region = rect
        self.overlay.set_region(rect)
        self.overlay.update_content([], None)
        self.overlay.hide()
        self.status_label.setText(
            f"Область: {rect.width()}×{rect.height()}  @ ({rect.x()}, {rect.y()})")
        self.log(f"Region set: {rect.width()}×{rect.height()}")

    def on_region_cancelled(self):
        self.status_label.setText("Выделение отменено")

    # ------------- start/stop -------------
    def start_translation(self):
        if not self.region:
            QMessageBox.warning(self, "Внимание", "Сначала выделите область.")
            return
        if self.worker and self.worker.isRunning():
            return

        src = self.src_combo.currentText()
        tgt = self.tgt_combo.currentText()
        self.log(f"=== START {src}→{tgt} ===")

        try:
            self.worker = TranslationWorker(
                self.region, src, tgt,
                interval=float(self.interval_spin.value()),
                use_gpu=self.gpu_check.isChecked(),
                bypass_interval=float(self.bypass_spin.value()),
                use_inpaint=self.inpaint_check.isChecked(),
                canvas_size=DEFAULT_CANVAS_SIZE,
            )
            self.worker.blocks_ready.connect(self.on_blocks_ready)
            self.worker.error.connect(self.on_error)
            self.worker.status.connect(self.on_status)
            self.worker.debug.connect(self.log)
            self.worker.finished.connect(self.on_finished)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать воркер: {e}")
            self.worker = None
            return

        self.overlay.show()
        self.overlay.raise_()
        if HAS_WIN32:
            try:
                self.worker.set_overlay_hwnd(int(self.overlay.winId()))
            except Exception:
                pass

        self.worker.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self.status_label.setText("Запуск...")

    def manual_refresh(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_refresh()

    def stop_translation(self):
        if self.worker and self.worker.isRunning():
            self.btn_stop.setEnabled(False)
            self.btn_refresh.setEnabled(False)
            self.status_label.setText("Остановка...")
            self.worker.stop()
        else:
            self._cleanup()

    def _cleanup(self):
        self.worker = None
        self.overlay.update_content([], None)
        self.overlay.hide()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_refresh.setEnabled(False)
        self.status_label.setText("Остановлен")
        self.log("=== STOP ===")

    # ------------- slots -------------
    def on_blocks_ready(self, blocks, bg_bytes):
        bg_image = None
        if bg_bytes is not None:
            h, w, data = bg_bytes
            bg_image = QImage(data, w, h, w * 3, QImage.Format_BGR888).copy()
        self.overlay.update_content(blocks, bg_image)
        self.overlay.raise_()

    def on_error(self, msg: str):
        self.log(f"[ERROR] {msg}")
        self.status_label.setText(f"Ошибка: {msg}")

    def on_status(self, msg: str):
        self.status_label.setText(msg)

    def on_finished(self):
        self._cleanup()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            if not self.worker.wait(3000):
                self.worker.terminate()
                self.worker.wait(1000)
        self.overlay.close()
        event.accept()