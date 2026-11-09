#!/usr/bin/env python3
"""
Interfaz gráfica no bloqueante para la cola persistente de descargas de MaoMusic.
Se ejecuta en el hilo principal de Qt y se comunica con QueueRunner mediante señales.
"""

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from download_queue import DownloadQueue, QueueItem, QueueRunner, parse_playlist_txt


class QueueSignals(QObject):
    """Puente seguro de señales Qt entre los hilos del QueueRunner y la GUI."""
    item_changed = pyqtSignal(int)
    summary_changed = pyqtSignal()
    item_completed_for_playlist = pyqtSignal(int, str, str, dict)  # item_id, query, target_playlist, result_dict
    playlist_enqueued = pyqtSignal(str, list, bool)  # playlist_name, queries, keep_local


class DownloadQueuePanel(QDialog):
    """Panel flotante no modal para monitorizar y gestionar la cola de descargas."""

    def __init__(
        self,
        queue: DownloadQueue,
        runner: QueueRunner,
        signals: QueueSignals,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.queue = queue
        self.runner = runner
        self.signals = signals

        self.setWindowTitle("📥 Cola de Descargas y Sincronización — MaoMusic")
        self.setModal(False)
        self.resize(880, 520)
        self.setMinimumSize(700, 380)

        self._setup_style()
        self._init_ui()

        # Conectar señales del runner
        self.signals.item_changed.connect(self._on_item_changed)
        self.signals.summary_changed.connect(self._on_summary_changed)

        self._refresh_table()

    def _setup_style(self) -> None:
        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
                color: #FFFFFF;
            }
            QLabel {
                color: #E4E4E7;
                font-size: 13px;
            }
            QLabel#StatusHeader {
                font-size: 15px;
                font-weight: 700;
                color: #FAFAFA;
            }
            QLabel#SummaryStats {
                font-size: 12px;
                color: #A1A1AA;
            }
            QPushButton {
                background-color: #27272A;
                color: #E4E4E7;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3F3F46;
                color: #FFFFFF;
            }
            QPushButton#BtnPrimary {
                background-color: #10B981;
                color: #000000;
                border: none;
                font-weight: 700;
            }
            QPushButton#BtnPrimary:hover {
                background-color: #059669;
            }
            QPushButton#BtnActionSmall {
                background-color: #1F1F23;
                border: 1px solid #333338;
                border-radius: 4px;
                padding: 3px 8px;
                font-size: 11px;
            }
            QPushButton#BtnActionSmall:hover {
                background-color: #2E2E35;
                color: #FFFFFF;
            }
            QComboBox {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            QTableWidget {
                background-color: #18181B;
                color: #FAFAFA;
                gridline-color: #27272A;
                border: 1px solid #27272A;
                border-radius: 8px;
                selection-background-color: #27272A;
                selection-color: #FFFFFF;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #121214;
                color: #A1A1AA;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #27272A;
                font-weight: 700;
                font-size: 12px;
            }
            QProgressBar {
                background-color: #27272A;
                border-radius: 4px;
                text-align: center;
                color: #FFFFFF;
                font-size: 11px;
                font-weight: 600;
            }
            QProgressBar::chunk {
                background-color: #10B981;
                border-radius: 4px;
            }
            QMenu {
                background-color: #18181B;
                color: #FAFAFA;
                border: 1px solid #27272A;
            }
            QMenu::item:selected {
                background-color: #27272A;
            }
        """)

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # Barra Superior de Encabezado y Estadísticas
        top_row = QHBoxLayout()
        header_vbox = QVBoxLayout()
        self.lbl_title = QLabel("📥 Cola de Descargas Multimedia", self)
        self.lbl_title.setObjectName("StatusHeader")
        self.lbl_summary = QLabel("Cargando estado de la cola...", self)
        self.lbl_summary.setObjectName("SummaryStats")
        header_vbox.addWidget(self.lbl_title)
        header_vbox.addWidget(self.lbl_summary)
        top_row.addLayout(header_vbox)
        top_row.addStretch()

        # Botón de Pausa / Reanudación global
        self.btn_pause_resume = QPushButton("⏸ Pausar Cola", self)
        self.btn_pause_resume.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause_resume.clicked.connect(self._toggle_pause_resume)
        top_row.addWidget(self.btn_pause_resume)

        # Selector de concurrencia
        lbl_concurrency = QLabel("Simultáneas:", self)
        top_row.addWidget(lbl_concurrency)
        self.cmb_concurrency = QComboBox(self)
        self.cmb_concurrency.addItems(["1", "2", "3"])
        self.cmb_concurrency.setCurrentText(str(self.runner.max_parallel))
        self.cmb_concurrency.currentTextChanged.connect(self._on_concurrency_changed)
        top_row.addWidget(self.cmb_concurrency)

        layout.addLayout(top_row)

        # Barra de Acciones Principales
        actions_row = QHBoxLayout()
        self.btn_add = QPushButton("➕ Agregar Canciones...", self)
        self.btn_add.setObjectName("BtnPrimary")
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_add.clicked.connect(self._open_add_dialog)
        actions_row.addWidget(self.btn_add)

        self.btn_import = QPushButton("📂 Subir .txt a Playlist...", self)
        self.btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import.clicked.connect(self._import_txt_file)
        actions_row.addWidget(self.btn_import)

        lbl_filter_pl = QLabel("Playlist:", self)
        actions_row.addWidget(lbl_filter_pl)
        self.cmb_filter_playlist = QComboBox(self)
        self.cmb_filter_playlist.addItem("Todas las playlists")
        self.cmb_filter_playlist.currentTextChanged.connect(lambda _: self._refresh_table())
        actions_row.addWidget(self.cmb_filter_playlist)

        actions_row.addStretch()

        self.btn_retry_failed = QPushButton("🔄 Reintentar Fallidos", self)
        self.btn_retry_failed.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_retry_failed.clicked.connect(self._retry_failed)
        actions_row.addWidget(self.btn_retry_failed)

        self.btn_clear_done = QPushButton("🧹 Limpiar Terminados", self)
        self.btn_clear_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_done.clicked.connect(self._clear_finished)
        actions_row.addWidget(self.btn_clear_done)

        layout.addLayout(actions_row)

        # Tabla de Descargas (7 columnas con Playlist Destino)
        self.table = QTableWidget(self)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "#",
            "Consulta / Canción",
            "Playlist Destino",
            "Estado",
            "Etapa",
            "Progreso",
            "Acción",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.verticalHeader().setVisible(False)

        # Configurar anchos de columna
        h_header = self.table.horizontalHeader()
        h_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(5, 140)
        h_header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)

        layout.addWidget(self.table)

    def _open_add_dialog(self) -> None:
        """Abre el diálogo para ingresar canciones y encolarlas."""
        from main import YouTubeBatchDownloadDialog
        dlg = YouTubeBatchDownloadDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            keep_local = dlg.should_keep_local()
            tgt_pl = getattr(dlg, "get_target_playlist", lambda: "")()
            raw_text = dlg.txt_list.toPlainText() if hasattr(dlg, "txt_list") else ""
            entries = parse_playlist_txt(raw_text, default_playlist_name=tgt_pl) if raw_text else []
            if not entries:
                queries = dlg.get_queries()
                entries = [(q, tgt_pl) for q in queries]
            if entries:
                grouped: dict[str, list[str]] = {}
                for q, pl_name in entries:
                    grouped.setdefault(pl_name, []).append(q)
                total_added = 0
                total_skipped = 0
                for pl_name, qs in grouped.items():
                    added, skipped = self.queue.add(qs, keep_local=keep_local, target_playlist=pl_name)
                    total_added += added
                    total_skipped += skipped
                    if pl_name:
                        self.signals.playlist_enqueued.emit(pl_name, qs, keep_local)
                self._refresh_table()
                self.signals.summary_changed.emit()
                pl_names = [p for p in grouped.keys() if p]
                pl_info = f" en la playlist en la nube «{', '.join(pl_names)}»" if pl_names else ""
                if total_skipped > 0 or total_added > 0:
                    QMessageBox.information(
                        self,
                        "Descargas y Playlist en la Nube",
                        f"✔ Se procesaron {total_added + total_skipped} canciones{pl_info}.\n"
                        f"• Encoladas para descarga: {total_added}\n"
                        f"• Vinculadas de descargas existentes: {total_skipped}",
                    )

    def _import_txt_file(self) -> None:
        """Permite seleccionar un archivo .txt con canciones línea por línea para crear una Playlist."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar lista de canciones (.txt)",
            "",
            "Archivos de texto (*.txt);;Todos los archivos (*)",
        )
        if not file_path:
            return

        try:
            content = Path(file_path).read_text(encoding="utf-8")
            raw_stem = Path(file_path).stem.replace("_", " ").strip()
            default_name = raw_stem.title() if raw_stem else "Nueva Playlist"

            pl_name, ok = QInputDialog.getText(
                self,
                "Crear Playlist desde .txt",
                "Nombre de la Playlist en la Nube a crear con estas canciones:",
                text=default_name,
            )
            if not ok or not pl_name.strip():
                return
            pl_name = pl_name.strip()

            entries = parse_playlist_txt(content, default_playlist_name=pl_name)
            if not entries:
                QMessageBox.information(self, "Lista vacía", "No se encontraron canciones válidas en el archivo .txt.")
                return

            grouped: dict[str, list[str]] = {}
            for q, pl in entries:
                grouped.setdefault(pl or pl_name, []).append(q)

            total_added = 0
            total_skipped = 0
            for target_pl, qs in grouped.items():
                a, s = self.queue.add(qs, keep_local=True, target_playlist=target_pl)
                total_added += a
                total_skipped += s
                if target_pl:
                    self.signals.playlist_enqueued.emit(target_pl, qs, True)

            self._refresh_table()
            self.signals.summary_changed.emit()
            QMessageBox.information(
                self,
                "Playlist en la Nube Creada",
                f"✔ Se agrupó la playlist en la nube «{', '.join(grouped.keys())}».\n"
                f"• Descargas nuevas encoladas: {total_added}\n"
                f"• Canciones existentes vinculadas a la playlist: {total_skipped}",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Error al importar", f"No se pudo leer el archivo:\n{exc}")

    def _toggle_pause_resume(self) -> None:
        if self.runner.is_paused:
            self.runner.resume()
            self.btn_pause_resume.setText("⏸ Pausar Cola")
        else:
            self.runner.pause()
            self.btn_pause_resume.setText("▶ Reanudar Cola")
        self._refresh_summary_label()

    def _on_concurrency_changed(self, text: str) -> None:
        try:
            val = int(text)
            self.runner.max_parallel = val
            self.queue.set_meta("max_parallel", str(val))
        except ValueError:
            pass

    def _retry_failed(self) -> None:
        count = self.queue.retry_failed()
        self._refresh_table()
        if count > 0:
            self._refresh_summary_label()

    def _clear_finished(self) -> None:
        self.queue.clear_finished()
        self._refresh_table()

    def _on_item_changed(self, item_id: int) -> None:
        self._refresh_table()

    def _on_summary_changed(self) -> None:
        self._refresh_table()

    def _refresh_summary_label(self) -> None:
        items = self.queue.list()
        pending = sum(1 for it in items if it.status == "pending")
        running = sum(1 for it in items if it.status == "running")
        done = sum(1 for it in items if it.status == "done")
        error = sum(1 for it in items if it.status == "error")
        cancelled = sum(1 for it in items if it.status == "cancelled")

        status_prefix = "⏸ [Pausada] " if self.runner.is_paused else "▶ [Activa] "
        self.lbl_summary.setText(
            f"{status_prefix}Total: {len(items)} | Pendientes: {pending} | En curso: {running} | "
            f"Listas: {done} | Errores: {error} | Canceladas: {cancelled}"
        )

    def _refresh_table(self) -> None:
        all_items = self.queue.list()
        self._refresh_summary_label()

        # Actualizar opciones del selector de playlists sin perder la selección actual
        distinct_pls = sorted(set(it.target_playlist.strip() for it in all_items if it.target_playlist.strip()))
        if hasattr(self, "cmb_filter_playlist"):
            cur_sel = self.cmb_filter_playlist.currentText()
            self.cmb_filter_playlist.blockSignals(True)
            self.cmb_filter_playlist.clear()
            self.cmb_filter_playlist.addItem("Todas las playlists")
            for pl in distinct_pls:
                self.cmb_filter_playlist.addItem(pl)
            idx = self.cmb_filter_playlist.findText(cur_sel)
            self.cmb_filter_playlist.setCurrentIndex(idx if idx >= 0 else 0)
            self.cmb_filter_playlist.blockSignals(False)

            active_pl_filter = self.cmb_filter_playlist.currentText()
            if active_pl_filter and active_pl_filter != "Todas las playlists":
                items = [it for it in all_items if it.target_playlist.strip() == active_pl_filter]
            else:
                items = all_items
        else:
            items = all_items

        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            # Col 0: ID
            item_id_cell = QTableWidgetItem(str(item.id))
            item_id_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 0, item_id_cell)

            # Col 1: Consulta / Canción
            display_text = item.title if item.title else item.query
            query_cell = QTableWidgetItem(display_text)
            if item.error:
                query_cell.setToolTip(f"Error: {item.error}")
            self.table.setItem(row, 1, query_cell)

            # Col 2: Playlist Destino
            pl_text = f"🎵 {item.target_playlist}" if item.target_playlist else "Biblioteca General"
            pl_cell = QTableWidgetItem(pl_text)
            pl_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            pl_cell.setForeground(QColor("#10B981") if item.target_playlist else QColor("#71717A"))
            self.table.setItem(row, 2, pl_cell)

            # Col 3: Estado
            status_labels = {
                "pending": ("⏳ En cola", "#A1A1AA"),
                "running": ("⚡ Descargando", "#60A5FA"),
                "done": ("✔ Listo", "#10B981"),
                "error": ("❌ Error", "#EF4444"),
                "cancelled": ("⏹ Cancelado", "#71717A"),
            }
            st_text, st_color = status_labels.get(item.status, (item.status, "#E4E4E7"))
            status_cell = QTableWidgetItem(st_text)
            status_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 3, status_cell)

            # Col 4: Etapa
            stage_labels = {
                "queued": "En espera",
                "starting": "Iniciando",
                "search": "Buscando",
                "download": "Descargando",
                "convert": "Extrayendo audio",
                "upload": "Subiendo Drive",
                "completed": "Completado",
                "waiting_retry": f"Reintento ({item.attempts})",
                "failed": "Fallo",
                "cancelled": "Cancelado",
                "recovered": "Recuperado",
            }
            stage_display = stage_labels.get(item.stage, item.stage or "-")
            stage_cell = QTableWidgetItem(stage_display)
            stage_cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, stage_cell)

            # Col 5: Progreso (QProgressBar)
            pbar = QProgressBar()
            pbar.setMinimum(0)
            pbar.setMaximum(100)
            pbar.setValue(int(item.progress))
            if item.status == "done":
                pbar.setValue(100)
            elif item.status == "error":
                pbar.setStyleSheet("QProgressBar::chunk { background-color: #EF4444; }")
            self.table.setCellWidget(row, 5, pbar)

            # Col 6: Botón de acción rápido
            action_btn = QPushButton()
            action_btn.setObjectName("BtnActionSmall")
            action_btn.setCursor(Qt.CursorShape.PointingHandCursor)

            if item.status in ("pending", "running"):
                action_btn.setText("Cancelar")
                action_btn.clicked.connect(lambda _, i=item.id: self._cancel_item(i))
            elif item.status in ("error", "cancelled"):
                action_btn.setText("Reintentar")
                action_btn.clicked.connect(lambda _, i=item.id: self._retry_item(i))
            else:
                action_btn.setText("Quitar")
                action_btn.clicked.connect(lambda _, i=item.id: self._remove_item(i))

            self.table.setCellWidget(row, 6, action_btn)

    def _cancel_item(self, item_id: int) -> None:
        self.runner.cancel_item(item_id)
        self._refresh_table()

    def _retry_item(self, item_id: int) -> None:
        self.queue.retry(item_id)
        self._refresh_table()

    def _remove_item(self, item_id: int) -> None:
        self.queue.remove(item_id)
        self._refresh_table()

    def _show_context_menu(self, pos) -> None:
        item = self.table.itemAt(pos)
        if not item:
            return

        row = item.row()
        id_item = self.table.item(row, 0)
        if not id_item:
            return

        item_id = int(id_item.text())
        q_item = self.queue.get(item_id)
        if not q_item:
            return

        menu = QMenu(self)

        if q_item.status in ("pending", "running"):
            act_cancel = menu.addAction("⏹ Cancelar descarga")
            act_cancel.triggered.connect(lambda: self._cancel_item(item_id))

        if q_item.status in ("error", "cancelled"):
            act_retry = menu.addAction("🔄 Reintentar descarga")
            act_retry.triggered.connect(lambda: self._retry_item(item_id))

        if q_item.error:
            act_copy_err = menu.addAction("📋 Copiar mensaje de error")
            act_copy_err.triggered.connect(lambda: QApplication.clipboard().setText(q_item.error))

        act_remove = menu.addAction("🗑 Quitar de la lista")
        act_remove.triggered.connect(lambda: self._remove_item(item_id))

        menu.exec(self.table.viewport().mapToGlobal(pos))
