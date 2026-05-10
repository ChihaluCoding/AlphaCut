from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

try:
    from PIL import Image
    from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, QSize
    from PySide6.QtGui import QFont, QFontDatabase, QIcon, QImage, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QComboBox,
        QProgressBar,
        QPushButton,
        QScrollArea,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except ImportError as import_error:
    print("PySide6 などのGUIライブラリを読み込めませんでした。")
    print("desktop_app フォルダで次を実行してください: python -m pip install --force-reinstall -r requirements.txt")
    print("QtCore の DLL エラーが出る場合は、PySide6 のバージョン不一致が原因のことがあります。")
    raise import_error

if TYPE_CHECKING:
    from core.birefnet_remover import BiRefNetRemover, RemovalResult


APP_NAME = "AlphaCut"
APP_DIR = Path(__file__).resolve().parent
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def resource_path(relative_path: str) -> str:
    base_path = Path(getattr(sys, "_MEIPASS", APP_DIR))
    return str(base_path / relative_path)


APP_ICON_PATH = Path(resource_path("assets/alphacut.ico"))


def is_supported_image_path(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES


def collect_supported_image_paths(paths: list[str | Path]) -> list[Path]:
    return [Path(path) for path in paths if is_supported_image_path(Path(path))]


class RemovalWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, image_path: Path) -> None:
        super().__init__()
        self.image_path = image_path

    def run(self) -> None:
        try:
            remover = get_remover()
            self.finished.emit(remover.remove_background(self.image_path, self.progress.emit))
        except Exception as error:
            self.failed.emit(str(error))


class PreviewLabel(QLabel):
    def __init__(self, placeholder_text: str) -> None:
        super().__init__(placeholder_text)
        self._source_pixmap: QPixmap | None = None
        self._placeholder_text = placeholder_text
        self._fit_to_view = True
        self._zoom_percent = 100
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setObjectName("previewCanvas")
        self.setWordWrap(True)
        self.resize(340, 420)
        self._updating_pixmap = False

    def set_image(self, image: Image.Image) -> None:
        preview = image.convert("RGBA")
        self._source_pixmap = QPixmap.fromImage(pil_to_qimage(preview))
        self.setText("")
        self.update_pixmap()

    def clear_image(self) -> None:
        self._source_pixmap = None
        self._fit_to_view = True
        self._zoom_percent = 100
        self.clear()
        self.setText(self._placeholder_text)
        self.resize(340, 420)

    @property
    def zoom_label(self) -> str:
        return "全体" if self._fit_to_view else f"{self._zoom_percent}%"

    def set_fit_to_view(self) -> None:
        self._fit_to_view = True
        self.update_pixmap()

    def set_actual_size(self) -> None:
        self._fit_to_view = False
        self._zoom_percent = 100
        self.update_pixmap()

    def zoom_in(self) -> None:
        self._set_zoom(self._zoom_percent + 25)

    def zoom_out(self) -> None:
        self._set_zoom(self._zoom_percent - 25)

    def _set_zoom(self, zoom_percent: int) -> None:
        self._fit_to_view = False
        self._zoom_percent = max(25, min(400, zoom_percent))
        self.update_pixmap()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_to_view:
            self.update_pixmap()

    def update_pixmap(self) -> None:
        if self._source_pixmap is None or self._updating_pixmap:
            return

        self._updating_pixmap = True
        try:
            available_size = self.target_preview_size()
            scaled = self._source_pixmap.scaled(
                available_size,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.setPixmap(scaled)
            if self.size() != scaled.size():
                self.resize(scaled.size())
        finally:
            self._updating_pixmap = False

    def target_preview_size(self) -> QSize:
        if not self._fit_to_view:
            width = max(1, self._source_pixmap.width() * self._zoom_percent // 100)
            height = max(1, self._source_pixmap.height() * self._zoom_percent // 100)
            return QSize(width, height)

        parent = self.parentWidget()
        viewport_size = parent.size() if parent is not None else self.size()
        padding = 28
        width = max(1, viewport_size.width() - padding)
        height = max(1, viewport_size.height() - padding)
        return QSize(width, height)

    def is_fit_to_view(self) -> bool:
        return self._fit_to_view

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return

        super().wheelEvent(event)


class PreviewScrollArea(QScrollArea):
    def __init__(self, preview_label: PreviewLabel) -> None:
        super().__init__()
        self.preview_label = preview_label
        self.setObjectName("previewScroll")
        self.setWidget(preview_label)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.preview_label.is_fit_to_view():
            QTimer.singleShot(0, self.preview_label.update_pixmap)


_remover: "BiRefNetRemover | None" = None


def get_remover() -> "BiRefNetRemover":
    global _remover
    if _remover is None:
        from core.birefnet_remover import BiRefNetRemover

        _remover = BiRefNetRemover()
    return _remover


def release_remover() -> None:
    global _remover
    if _remover is not None:
        _remover.release()
        _remover = None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        if APP_ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.resize(1240, 780)
        self.setMinimumSize(1120, 720)
        self.setAcceptDrops(True)

        self.source_path: Path | None = None
        self.result: Image.Image | None = None
        self.batch_paths: list[Path] = []
        self.batch_results: list[tuple[Path, Image.Image]] = []
        self.batch_failures: list[tuple[Path, str]] = []
        self.batch_index = 0
        self.worker_thread: QThread | None = None
        self.worker: RemovalWorker | None = None
        self.process_started_at: float | None = None
        self.current_progress = 0
        self.current_stage = ""

        self.status_label = QLabel("画像を選択してください")
        self.status_label.setWordWrap(True)
        self.progress_label = QLabel("残り時間: -")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.result_list = QListWidget()
        self.result_list.setObjectName("resultList")
        self.result_list.setMinimumHeight(96)
        self.result_list.currentRowChanged.connect(self.on_result_selected)
        self.preview_background_label = QLabel("プレビュー背景")
        self.preview_background_label.setObjectName("sectionLabel")
        self.preview_background_select = QComboBox()
        self.preview_background_select.addItem("市松模様", "checker")
        self.preview_background_select.addItem("グレー", "gray")
        self.preview_background_select.addItem("水色", "blue")
        self.preview_background_select.addItem("黒", "black")
        self.preview_background_select.addItem("白", "white")
        self.preview_background_select.currentIndexChanged.connect(self.refresh_result_preview)

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(1000)
        self.progress_timer.timeout.connect(self.update_progress_time)

        self.source_preview = PreviewLabel("元画像\n画像が表示されます")
        self.result_preview = PreviewLabel("透過後\n結果が表示されます")

        self.open_button = QPushButton("画像を選択")
        self.run_button = QPushButton("背景透過")
        self.save_button = QPushButton("PNGで保存")
        self.open_button.setObjectName("primaryButton")
        self.run_button.setObjectName("secondaryButton")
        self.save_button.setObjectName("primaryButton")
        self.run_button.setEnabled(False)
        self.save_button.setEnabled(False)

        self.open_button.clicked.connect(self.open_image)
        self.run_button.clicked.connect(self.start_removal)
        self.save_button.clicked.connect(self.save_result)

        container = QWidget()
        container.setObjectName("appRoot")
        root = QVBoxLayout()
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(0)

        content = QHBoxLayout()
        content.setSpacing(16)
        content.addWidget(self.create_preview_panel(), 1)
        content.addWidget(self.create_action_panel(), 0)
        content.setAlignment(Qt.AlignTop)
        root.addLayout(content, 1)

        container.setLayout(root)
        self.setCentralWidget(container)
        self.apply_theme()

    def open_image(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を選択",
            "",
            "画像ファイル (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        self.apply_image_paths(collect_supported_image_paths(paths), append=False)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._drop_contains_supported_images(event):
            event.acceptProposedAction()
            return

        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self._drop_contains_supported_images(event):
            event.acceptProposedAction()
            return

        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        dropped_paths = self._supported_paths_from_drop_event(event)
        if not dropped_paths:
            event.ignore()
            return

        self.apply_image_paths(dropped_paths, append=True)
        event.acceptProposedAction()

    def apply_image_paths(self, paths: list[Path], append: bool) -> None:
        if not paths:
            return

        if append:
            self.batch_paths.extend(paths)
        else:
            self.batch_paths = paths

        self.batch_results = []
        self.batch_failures = []
        self.batch_index = 0
        self.result_list.clear()
        if self.source_path is None or not append:
            self.source_path = self.batch_paths[0]
        self.result = None
        self.reset_progress()
        self.run_button.setEnabled(True)
        self.save_button.setEnabled(False)
        if append:
            self.status_label.setText(f"追加: {len(paths)}件 / 合計 {len(self.batch_paths)}件")
        else:
            self.status_label.setText(f"選択: {len(self.batch_paths)}件")
        self._set_preview(self.source_preview, Image.open(self.source_path).convert("RGBA"))
        self.result_preview.clear_image()

    def _drop_contains_supported_images(self, event) -> bool:
        return self.open_button.isEnabled() and bool(self._supported_paths_from_drop_event(event))

    def _supported_paths_from_drop_event(self, event) -> list[Path]:
        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return []

        local_paths = [
            Path(url.toLocalFile())
            for url in mime_data.urls()
            if url.isLocalFile()
        ]
        return collect_supported_image_paths(local_paths)

    def start_removal(self) -> None:
        if not self.batch_paths:
            QMessageBox.warning(self, "画像未選択", "先に画像を選択してください。")
            return

        self._set_busy(True)
        self.batch_results = []
        self.batch_failures = []
        self.batch_index = 0
        self.result_list.clear()
        self.process_started_at = time.monotonic()
        self.current_progress = 0
        self.current_stage = "開始中"
        self.progress_bar.setValue(0)
        self.status_label.setText(f"処理中: 1/{len(self.batch_paths)}")
        self.progress_label.setText("経過時間: 0秒 / 残り時間: 推定中")
        self.progress_timer.start()
        self.start_next_batch_item()

    def start_next_batch_item(self) -> None:
        if self.batch_index >= len(self.batch_paths):
            self.finish_batch()
            return

        self.source_path = self.batch_paths[self.batch_index]
        self.status_label.setText(f"処理中: {self.batch_index + 1}/{len(self.batch_paths)}")
        self._set_preview(self.source_preview, Image.open(self.source_path).convert("RGBA"))
        self.worker_thread = QThread()
        self.worker = RemovalWorker(self.source_path)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_removal_progress)
        self.worker.finished.connect(self.on_removal_finished)
        self.worker.failed.connect(self.on_removal_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.failed.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def on_removal_finished(self, result: "RemovalResult") -> None:
        current_path = self.batch_paths[self.batch_index]
        self.batch_results.append((current_path, result.image))
        self.result_list.addItem(current_path.name)
        self.result = result.image
        self.result_list.setCurrentRow(len(self.batch_results) - 1)
        self.batch_index += 1
        QTimer.singleShot(0, self.start_next_batch_item)

    def on_removal_failed(self, message: str) -> None:
        current_path = self.batch_paths[self.batch_index]
        self.batch_failures.append((current_path, message))
        self.batch_index += 1
        QTimer.singleShot(0, self.start_next_batch_item)

    def on_removal_progress(self, percent: int, stage: str) -> None:
        item_progress = max(0, min(100, percent)) / 100
        total = max(1, len(self.batch_paths))
        overall_progress = ((self.batch_index + item_progress) / total) * 100
        self.current_progress = round(max(0, min(100, overall_progress)))
        self.current_stage = stage
        self.progress_bar.setValue(self.current_progress)
        self.update_progress_time()

    def finish_batch(self) -> None:
        self.progress_timer.stop()
        self._set_busy(False)
        self.save_button.setEnabled(bool(self.batch_results))
        elapsed = self.elapsed_seconds()
        self.progress_bar.setValue(100 if self.batch_results else 0)
        self.progress_label.setText(f"経過時間: {format_seconds(elapsed)} / 残り時間: 0秒")

        success_count = len(self.batch_results)
        failure_count = len(self.batch_failures)
        if failure_count:
            failure_detail = "\n".join(
                f"- {path.name}: {message}" for path, message in self.batch_failures[:5]
            )
            if failure_count > 5:
                failure_detail += f"\n...ほか {failure_count - 5}件"
            self.status_label.setText(f"完了: 成功 {success_count}件 / 失敗 {failure_count}件")
            QMessageBox.warning(
                self,
                "一部失敗",
                f"{failure_count}件の処理に失敗しました。成功した画像は保存できます。\n\n{failure_detail}",
            )
        else:
            self.status_label.setText(f"完了: {success_count}件")

    def save_result(self) -> None:
        if not self.batch_results:
            QMessageBox.warning(self, "結果なし", "先に背景透過を実行してください。")
            return

        if len(self.batch_results) > 1:
            directory = QFileDialog.getExistingDirectory(self, "保存先フォルダを選択")
            if not directory:
                return

            output_dir = Path(directory)
            for source_path, image in self.batch_results:
                image.save(unique_output_path(output_dir, f"{source_path.stem}-removed.png"))
            self.status_label.setText(f"保存しました: {len(self.batch_results)}件")
            return

        default_path = "background-removed.png"
        source_path, image = self.batch_results[0]
        default_path = f"{source_path.stem}-removed.png"

        path, _ = QFileDialog.getSaveFileName(
            self,
            "PNGで保存",
            default_path,
            "PNG画像 (*.png)",
        )
        if not path:
            return

        output_path = Path(path)
        if output_path.suffix.lower() != ".png":
            output_path = output_path.with_suffix(".png")
        image.save(output_path)
        self.status_label.setText("保存しました")

    def on_result_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.batch_results):
            return

        source_path, image = self.batch_results[row]
        self.source_path = source_path
        self.result = image
        try:
            self._set_preview(self.source_preview, Image.open(source_path).convert("RGBA"))
        except Exception:
            self.source_preview.clear_image()
        self.refresh_result_preview()

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self.run_button.setEnabled(not busy and bool(self.batch_paths))
        self.save_button.setEnabled(not busy and bool(self.batch_results))

    def _set_preview(self, label: PreviewLabel, image: Image.Image) -> None:
        label.set_image(image)

    def refresh_result_preview(self) -> None:
        if self.result is None:
            return

        preview_image = render_transparency_preview(
            self.result,
            str(self.preview_background_select.currentData()),
        )
        self._set_preview(self.result_preview, preview_image)

    def update_progress_time(self) -> None:
        elapsed = self.elapsed_seconds()
        remaining_label = "推定中"
        if 5 <= self.current_progress < 100:
            total_estimate = elapsed / (self.current_progress / 100)
            remaining = max(0, round(total_estimate - elapsed))
            remaining_label = format_seconds(remaining)

        self.progress_label.setText(
            f"{self.current_stage} / 経過時間: {format_seconds(elapsed)} / 残り時間: {remaining_label}"
        )

    def elapsed_seconds(self) -> int:
        if self.process_started_at is None:
            return 0
        return max(0, round(time.monotonic() - self.process_started_at))

    def reset_progress(self) -> None:
        self.progress_timer.stop()
        self.process_started_at = None
        self.current_progress = 0
        self.current_stage = ""
        self.progress_bar.setValue(0)
        self.progress_label.setText("残り時間: -")

    def create_action_panel(self) -> QFrame:
        panel = create_card("sidePanel")
        panel.setFixedWidth(300)
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        layout.addWidget(create_section_label("操作"))
        layout.addWidget(self.open_button)
        layout.addWidget(self.run_button)

        status_card = create_card("innerCard")
        status_layout = QVBoxLayout()
        status_layout.setContentsMargins(14, 14, 14, 14)
        status_layout.setSpacing(8)
        status_layout.addWidget(create_section_label("処理状況"))
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.progress_label)
        status_card.setLayout(status_layout)
        layout.addWidget(status_card)

        layout.addWidget(create_section_label("結果一覧"))
        layout.addWidget(self.result_list)

        layout.addWidget(create_section_label("出力"))
        layout.addWidget(self.save_button)

        background_card = create_card("innerCard")
        background_layout = QVBoxLayout()
        background_layout.setContentsMargins(14, 14, 14, 14)
        background_layout.setSpacing(8)
        background_layout.addWidget(self.preview_background_label)
        background_layout.addWidget(self.preview_background_select)
        background_card.setLayout(background_layout)
        layout.addWidget(background_card)
        layout.addStretch()

        panel.setLayout(layout)
        panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Maximum)
        return panel

    def create_preview_panel(self) -> QFrame:
        panel = create_card("previewPanel")
        layout = QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.addWidget(create_section_label("プレビュー"))
        header.addStretch()
        compare_note = QLabel("左: 元画像 / 右: 透過後")
        compare_note.setObjectName("smallNote")
        header.addWidget(compare_note)

        previews = QHBoxLayout()
        previews.setSpacing(14)
        previews.addWidget(self.create_preview_card("元画像", self.source_preview), 1)
        previews.addWidget(self.create_preview_card("透過後", self.result_preview), 1)

        layout.addLayout(header)
        layout.addLayout(previews, 1)
        panel.setLayout(layout)
        return panel

    def create_preview_card(self, title_text: str, preview_label: PreviewLabel) -> QFrame:
        card = create_card("previewCard")
        layout = QVBoxLayout()
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel(title_text)
        title.setObjectName("previewTitle")
        zoom_label = QLabel(preview_label.zoom_label)
        zoom_label.setObjectName("zoomLabel")
        zoom_out_button = create_tool_button("-")
        zoom_in_button = create_tool_button("+")
        actual_size_button = create_tool_button("1:1")
        fit_button = create_tool_button("全体")

        zoom_out_button.clicked.connect(
            lambda: self.update_preview_zoom(preview_label, zoom_label, "out")
        )
        zoom_in_button.clicked.connect(
            lambda: self.update_preview_zoom(preview_label, zoom_label, "in")
        )
        actual_size_button.clicked.connect(
            lambda: self.update_preview_zoom(preview_label, zoom_label, "actual")
        )
        fit_button.clicked.connect(
            lambda: self.update_preview_zoom(preview_label, zoom_label, "fit")
        )

        header.addWidget(title)
        header.addStretch()
        header.addWidget(zoom_out_button)
        header.addWidget(zoom_in_button)
        header.addWidget(actual_size_button)
        header.addWidget(fit_button)
        header.addWidget(zoom_label)

        scroll_area = PreviewScrollArea(preview_label)

        layout.addLayout(header)
        layout.addWidget(scroll_area, 1)
        card.setLayout(layout)
        return card

    def update_preview_zoom(
        self,
        preview_label: PreviewLabel,
        zoom_label: QLabel,
        action: str,
    ) -> None:
        if action == "in":
            preview_label.zoom_in()
        elif action == "out":
            preview_label.zoom_out()
        elif action == "actual":
            preview_label.set_actual_size()
        else:
            preview_label.set_fit_to_view()
        zoom_label.setText(preview_label.zoom_label)

    def closeEvent(self, event) -> None:  # noqa: N802
        release_remover()
        super().closeEvent(event)

    def apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QWidget#appRoot {
                background: #f4f8fb;
                color: #182230;
                font-family: "Meiryo UI", "Yu Gothic UI", "Meiryo";
                font-size: 14px;
            }
            QFrame#sidePanel,
            QFrame#previewPanel {
                background: #ffffff;
                border: 1px solid #d7e4ef;
                border-radius: 14px;
            }
            QFrame#innerCard,
            QFrame#previewCard {
                background: #fbfdff;
                border: 1px solid #dce8f2;
                border-radius: 12px;
            }
            QLabel#smallNote {
                color: #637083;
            }
            QLabel#zoomLabel {
                color: #637083;
                min-width: 42px;
            }
            QLabel#sectionLabel,
            QLabel#previewTitle,
            QLabel#preview_background_label {
                color: #1a314a;
                font-weight: 700;
            }
            QLabel#previewCanvas {
                background: #f8fbff;
                border: 1px dashed #b7cde0;
                border-radius: 10px;
                color: #718197;
                padding: 18px;
            }
            QScrollArea#previewScroll {
                background: #f8fbff;
                border: 1px dashed #b7cde0;
                border-radius: 10px;
            }
            QScrollArea#previewScroll QLabel#previewCanvas {
                border: 0;
                border-radius: 0;
            }
            QPushButton {
                border-radius: 10px;
                min-height: 42px;
                padding: 10px 14px;
                font-weight: 700;
            }
            QPushButton#primaryButton {
                background: #248bd6;
                border: 1px solid #197ac2;
                color: #ffffff;
            }
            QPushButton#primaryButton:hover {
                background: #1d7fc6;
            }
            QPushButton#secondaryButton {
                background: #ffffff;
                border: 1px solid #8fc2ea;
                color: #116da9;
            }
            QPushButton#secondaryButton:hover {
                background: #edf7ff;
            }
            QPushButton:disabled {
                background: #e8eef4;
                border: 1px solid #d2dde8;
                color: #98a6b5;
            }
            QPushButton#toolButton {
                background: #ffffff;
                border: 1px solid #bfd3e5;
                border-radius: 7px;
                color: #26384d;
                font-weight: 700;
                min-height: 28px;
                min-width: 34px;
                padding: 4px 8px;
            }
            QPushButton#toolButton:hover {
                background: #edf7ff;
            }
            QComboBox {
                background: #ffffff;
                border: 1px solid #bfd3e5;
                border-radius: 9px;
                min-height: 34px;
                padding: 5px 10px;
            }
            QListWidget#resultList {
                background: #ffffff;
                border: 1px solid #d1dfeb;
                border-radius: 10px;
                color: #26384d;
                padding: 4px;
            }
            QListWidget#resultList::item {
                border-radius: 7px;
                padding: 7px 8px;
            }
            QListWidget#resultList::item:selected {
                background: #e7f4ff;
                color: #116da9;
            }
            QProgressBar {
                background: #eaf1f7;
                border: 1px solid #d1dfeb;
                border-radius: 8px;
                height: 18px;
                text-align: center;
                color: #31445a;
            }
            QProgressBar::chunk {
                background: #2f9ae5;
                border-radius: 7px;
            }
            """
        )


def pil_to_qimage(image: Image.Image) -> QImage:
    rgba_image = image.convert("RGBA")
    image_bytes = rgba_image.tobytes("raw", "RGBA")
    qimage = QImage(
        image_bytes,
        rgba_image.width,
        rgba_image.height,
        rgba_image.width * 4,
        QImage.Format_RGBA8888,
    )
    return qimage.copy()


def create_card(object_name: str) -> QFrame:
    card = QFrame()
    card.setObjectName(object_name)
    return card


def create_section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def create_tool_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("toolButton")
    button.setFixedHeight(30)
    return button


def render_transparency_preview(image: Image.Image, background_mode: str) -> Image.Image:
    rgba_image = image.convert("RGBA")
    background = create_preview_background(rgba_image.size, background_mode)
    background.alpha_composite(rgba_image)
    return background


def create_preview_background(size: tuple[int, int], background_mode: str) -> Image.Image:
    if background_mode == "checker":
        return create_checkerboard(size)

    colors = {
        "gray": (210, 216, 224, 255),
        "blue": (190, 225, 247, 255),
        "black": (28, 31, 36, 255),
        "white": (255, 255, 255, 255),
    }
    return Image.new("RGBA", size, colors.get(background_mode, colors["gray"]))


def create_checkerboard(size: tuple[int, int], tile_size: int = 24) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (255, 255, 255, 255))
    light = (245, 247, 250, 255)
    dark = (205, 214, 224, 255)

    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            color = light if ((x // tile_size) + (y // tile_size)) % 2 == 0 else dark
            right = min(x + tile_size, width)
            bottom = min(y + tile_size, height)
            image.paste(color, (x, y, right, bottom))

    return image


def unique_output_path(output_dir: Path, filename: str) -> Path:
    output_path = output_dir / filename
    if not output_path.exists():
        return output_path

    stem = output_path.stem
    suffix = output_path.suffix
    counter = 2
    while True:
        candidate = output_dir / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def format_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}秒"

    minutes, rest_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{rest_seconds:02d}秒"

    hours, rest_minutes = divmod(minutes, 60)
    return f"{hours}時間{rest_minutes:02d}分"


def configure_app_font(app: QApplication) -> None:
    font_candidates = [
        (Path("C:/Windows/Fonts/NotoSansJP-Regular.otf"), "Noto Sans JP"),
        (Path("C:/Windows/Fonts/meiryo.ttc"), "Meiryo"),
        (Path("C:/Windows/Fonts/YuGothR.ttc"), "Yu Gothic"),
        (Path("C:/Windows/Fonts/BIZ-UDGothicR.ttc"), "BIZ UDGothic"),
    ]

    for font_path, family in font_candidates:
        if font_path.exists() and QFontDatabase.addApplicationFont(str(font_path)) != -1:
            app.setFont(QFont(family, 10))
            return

    app.setFont(QFont("Meiryo UI", 10))


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if APP_ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    configure_app_font(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
