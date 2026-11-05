from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6 import QtGui
from PySide6.QtCore import (
    QCoreApplication,
    QModelIndex,
    QSize,
    QThread,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .books import ScanBookFiles
from .database import (
    DatabaseWorker,
)
from .utils import Tips
from .book_record import (
    BOOK_COLUMNS,
    BOOK_COLUMN_TITLES,
    BookRecord,
)
from .cover_model import CoverCardModel
from .cover_delegate import CoverCardDelegate
from .table_model import BookTableModel
from .table_delegate import TableDelegate


class MainWindow(QMainWindow):
    request_initialize = Signal()
    request_all = Signal()
    request_by_type = Signal(str)
    request_by_status = Signal(str)
    request_by_author = Signal(str)
    request_close_database = Signal()
    request_find_duplicates = Signal(object)
    request_insert_books = Signal(object, object)
    request_update_book = Signal(object, str, object)
    request_delete_book = Signal(object)

    def __init__(self):
        super().__init__()
        # 初始化缓存与后台查询线程
        self._books_cache: list[BookRecord] | None
        self._cache_timestamp = 0.0

        # 待处理书籍与扫描状态标记
        self._pending_books: list[BookRecord] | None
        self._scanning = False

        self._scan_dialog: ScanBookFiles
        self._setup_book_worker()

        self.column_titles = list(BOOK_COLUMN_TITLES)

        # 实例化各个视图对应的数据模型和代理
        self.cover_model = CoverCardModel()
        self.cover_delegate = CoverCardDelegate()
        self.table_delegate = TableDelegate()
        self.table_model = BookTableModel(self.column_titles)
        self.table_model.dataChanged.connect(self.on_table_data_changed)

        # 窗口组件
        self.splitter: QSplitter
        self.statusbar: QStatusBar
        # 左右面板实例属性
        self.left_panel: QWidget
        self.right_stack: QStackedWidget
        # 左侧面板组件
        self.toggle_view_btn: QPushButton
        self.category_list: QListWidget
        self.scan_button: QPushButton
        self.clear_button: QPushButton
        self.close_button: QPushButton
        # 右侧视图组件（封面卡片视图+表格视图）
        self.cover_card_view: QListView
        self.table_view: QTableView

        self.setup_ui()

    def _setup_book_worker(self) -> None:
        """创建统一数据库线程，并连接读取及批量写入请求。"""
        self._database_thread = QThread(self)
        self._worker = DatabaseWorker()
        self._worker.moveToThread(self._database_thread)

        # 工作线程信号 -> 主线程槽函数
        self._worker.initialized.connect(self._on_database_initialized)
        self._worker.books_ready.connect(self._on_books_loaded)
        self._worker.query_error.connect(self._on_query_error)
        self._worker.duplicates_ready.connect(self._on_duplicates_ready)
        self._worker.write_finished.connect(self._on_write_finished)
        self._worker.write_error.connect(self._on_write_error)
        self._worker.change_finished.connect(self._on_change_finished)
        self._worker.change_error.connect(self._on_change_error)

        # 主线程信号 -> 工作线程槽函数
        self.request_initialize.connect(self._worker.initialize)
        self.request_all.connect(self._worker.fetch_all_books)
        self.request_by_type.connect(self._worker.fetch_by_type)
        self.request_by_status.connect(self._worker.fetch_by_status)
        self.request_by_author.connect(self._worker.fetch_by_author)
        self.request_find_duplicates.connect(self._worker.find_batch_duplicates)
        self.request_insert_books.connect(self._worker.insert_books)
        self.request_update_book.connect(self._worker.update_book_field)
        self.request_delete_book.connect(self._worker.delete_book_by_id)
        self.request_close_database.connect(self._worker.close_database)

        # 工作线程结束时安全清理__worker对象
        self._database_thread.finished.connect(self._worker.deleteLater)

        self._database_thread.start()
        self.request_initialize.emit()

    def setup_ui(self):
        # 设置窗口属性
        self.setObjectName("main_window")
        self.setWindowTitle(self._translate("Eternity Library", "三木书斋"))
        self.setGeometry(120, 140, 1080, 620)
        self.setWindowOpacity(1)

        # 设置窗口图标
        icon = QtGui.QIcon()
        icon.addPixmap(
            QtGui.QPixmap(
                str(Path(__file__).resolve().parent / "icons" / "book_icon.png")
            ),
            QtGui.QIcon.Mode.Normal,
            QtGui.QIcon.State.Off
        )
        self.setWindowIcon(icon)

        # 创建QSplitter作为左右分区的容器
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.left_panel = self._create_left_panel()

        # 创建右侧视图容器
        self.right_stack = QStackedWidget()
        cover_view_widget = self._create_cover_view()  # 封面视图，索引为0
        table_view_widget = self._create_table_view()  # 表格视图，索引为1
        self.right_stack.addWidget(cover_view_widget)
        self.right_stack.addWidget(table_view_widget)
        # 设置默认视图（当前默认为封面视图），索引顺序与添加的顺序一致
        self.right_stack.setCurrentIndex(0)

        # 将左右组件加入QSplitter
        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.right_stack)
        self.splitter.setStretchFactor(0, 1)  # 左侧比例
        self.splitter.setStretchFactor(1, 3)  # 右侧比例（更大）

        self.setCentralWidget(self.splitter)

        # 添加状态栏
        self.statusbar = QStatusBar(self)
        self.statusbar.setObjectName("statusbar")
        self.setStatusBar(self.statusbar)
        self.statusbar.showMessage("欢迎使用三木书斋")

        # 设置按钮字号
        self.setStyleSheet("""
        QPushButton {font-size: 11pt; font-family: Microsoft YaHei;}
        QListWidget {font-size: 11pt; font-family: Microsoft YaHei;}
        """)

    def _resize_table_columns(self):
        """调整表格视图的列宽"""
        # TODO: 目前存在点击按钮调整列宽时主界面卡顿的情况，后续需优化
        self.table_view.resizeColumnsToContents()
        self.table_view.resizeRowsToContents()
        return

    def _create_left_panel(self):
        """创建左侧面板"""
        panel = QWidget()
        panel.setFixedWidth(220)  # 设置左侧固定宽度
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # 视图切换区
        view_label = QLabel(self._translate("Views", "视图模式"))
        view_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view_label.setStyleSheet("font-weight: bold; font-size: 12pt;")

        self.toggle_view_btn = QPushButton("切换视图")
        self.toggle_view_btn.setFixedHeight(36)
        self.toggle_view_btn.clicked.connect(self._toggle_view_mode)

        self.resize_btn = QPushButton("调整表格列宽")
        self.resize_btn.setFixedHeight(36)
        # self.resize_btn.clicked.connect(self._resize_table_columns)

        layout.addWidget(view_label)
        layout.addWidget(self.toggle_view_btn)
        layout.addWidget(self.resize_btn)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Raised)
        layout.addWidget(line)

        # 分类筛选区，后续计划改进为标签（类型名称）+拉下列表框的方式筛选
        filter_label = QLabel(self._translate("Filter", "分类筛选"))
        filter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        filter_label.setStyleSheet("font-weight: bold; font-size: 12pt;")

        self.category_list = QListWidget()
        self.category_list.addItems([
            "全部书籍",
            "按书籍类型",
            "按阅读状态",
            "按作者",
        ])
        self.category_list.setFixedHeight(140)
        # self.category_list.currentRowChanged.connect(self._on_category_changed)

        layout.addWidget(filter_label)
        layout.addWidget(self.category_list)

        # 分隔线
        line_2 = QFrame()
        line_2.setFrameShape(QFrame.Shape.HLine)
        line_2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line_2)

        # 快捷操作区
        action_label = QLabel(self._translate("Actions", "快捷操作"))
        action_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        action_label.setStyleSheet("font-weight: bold; font-size: 12pt;")

        self.scan_button = QPushButton(self._translate("Scan Files", "扫描文件"))
        self.scan_button.setFixedHeight(36)
        self.scan_button.clicked.connect(self.scan_books)

        self.delete_button = QPushButton("删除选中书籍")
        self.delete_button.setFixedHeight(36)
        self.delete_button.clicked.connect(self._delete_book)

        self.clear_button = QPushButton(self._translate("Not show", "取消显示"))
        self.clear_button.setFixedHeight(36)
        self.clear_button.clicked.connect(self.close_info)

        self.close_button = QPushButton(self._translate("Close", "关闭"))
        self.close_button.setFixedHeight(36)
        self.close_button.clicked.connect(self.close)

        layout.addWidget(action_label)
        layout.addWidget(self.scan_button)
        layout.addWidget(self.delete_button)
        # layout.addWidget(self.show_button)
        layout.addWidget(self.clear_button)
        layout.addWidget(self.close_button)
        # 弹簧：将内容推到顶部
        layout.addStretch()
        return panel

    def _create_table_view(self) -> QTableView:
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self.table_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        # TODO: 暂时使用使用默认的显示和编辑行为，后续再完善自定义delegate以便支持更复杂的显示和编辑行为
        # self.table_view.setItemDelegate(self.table_delegate)
        self.table_view.setShowGrid(True)
        self.table_view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        return self.table_view

    def _create_cover_view(self) -> QWidget:
        """创建封面视图 —— QListView + Delegate 虚拟化绘制"""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)

        toolbar = QHBoxLayout()
        mode_label = QLabel(self._translate("Views", "封面模式"))
        mode_label.setStyleSheet("font-size: 13pt; font-weight: bold;")
        toolbar.addWidget(mode_label)
        toolbar.addStretch()
        layout.addLayout(toolbar)

        self.cover_card_view = QListView()
        self.cover_card_view.setModel(self.cover_model)
        self.cover_card_view.setItemDelegate(self.cover_delegate)

        self.cover_card_view.setViewMode(QListView.ViewMode.IconMode)
        self.cover_card_view.setMovement(QListView.Movement.Static)
        self.cover_card_view.setResizeMode(QListView.ResizeMode.Adjust)
        self.cover_card_view.setWrapping(True)
        self.cover_card_view.setSpacing(4)
        self.cover_card_view.setGridSize(QSize(
            CoverCardDelegate.CARD_W + 16,
            CoverCardDelegate.CARD_H + 10,
        ))
        self.cover_card_view.setUniformItemSizes(True)

        self.cover_card_view.setFrameShape(QFrame.Shape.NoFrame)
        self.cover_card_view.setBackgroundRole(QtGui.QPalette.ColorRole.Base)
        self.cover_card_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cover_card_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        self.cover_card_view.clicked.connect(self._on_cover_clicked)

        layout.addWidget(self.cover_card_view)
        return container

    def on_table_data_changed(
            self,
            top_left: QModelIndex,
            bottom_right: QModelIndex,
            roles: list[Qt.ItemDataRole],
    ) -> None:
        """把表格编辑转换成Worker更新请求。"""
        if roles and (Qt.ItemDataRole.EditRole not in roles):
            return

        for row in range(top_left.row(), bottom_right.row() + 1):
            for column in range(top_left.column(), bottom_right.column() + 1):
                if column == 0:
                    continue

                index = self.table_model.index(row, column)
                book_id = self.table_model.data(index, role=BookTableModel.BookIdRole)
                if not isinstance(book_id, int) or isinstance(book_id, bool):
                    self._on_change_error("未获取到有效ID，无法保存表格修改。")
                    return

                field_name = BOOK_COLUMNS[column]
                new_value = self.table_model.data(index, role=Qt.ItemDataRole.EditRole)
                self.request_update_book.emit(book_id, field_name, new_value)

        if self.statusbar is not None:
            self.statusbar.showMessage("正在保存修改……")

    def _delete_book(self) -> None:
        """确认后按书籍ID请求Worker删除选中记录。"""
        selected_rows = self.table_view.selectionModel().selectedIndexes()
        if not selected_rows:
            QMessageBox.information(self, "删除书籍", "需在表格视图选中书籍！")
            return

        row = selected_rows[0].row()
        index = self.table_model.index(row, 0)
        # 获取书籍ID和书名，用于确认删除操作
        book_id = self.table_model.data(index, role=BookTableModel.BookIdRole)
        book_name = self.table_model.data(
            self.table_model.index(row, 1),
            role=Qt.ItemDataRole.DisplayRole,
        )
        # bool类型的True/False也会被视为int类型（分别是0和1），因此需要排除
        if not isinstance(book_id, int) or isinstance(book_id, bool):
            Tips.information_msg("选中书籍不存在有效ID，无法删除！")
            return

        answer = QMessageBox.question(
            self,
            "确认删除",
            f"确定从书库中删除 {book_name} 吗？\n此操作不会删除本地电子书文件。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.delete_button.setEnabled(False)
        self.request_delete_book.emit(book_id)
        if self.statusbar is not None:
            self.statusbar.showMessage("正在删除书籍……")

    def _invalidate_cache(self) -> None:
        """写操作完成后清除旧缓存，确保视图重新读取数据库。"""
        self._books_cache = None
        self._cache_timestamp = 0.0

    def _on_change_finished(self, operation: str, book_id: int) -> None:
        """修改成功后刷新封面和表格 Model。"""
        self._invalidate_cache()
        self.delete_button.setEnabled(True)

        if self.statusbar is not None:
            action = "更新" if operation == "update" else "删除"
            self.statusbar.showMessage(f"书籍{book_id}已{action}，正在刷新视图……")
        self.request_all.emit()

    def _on_change_error(self, error_msg: str) -> None:
        """修改失败时提示用户，并从数据库恢复 Model 的真实内容。"""
        self._invalidate_cache()
        self.delete_button.setEnabled(True)

        Tips.information_msg(error_msg)
        if self.statusbar is not None:
            self.statusbar.showMessage("书籍修改失败，正在恢复数据……")
        self.request_all.emit()

    def _on_cover_clicked(self, index) -> None:
        """点击封面卡片"""
        book = self.cover_model.get_book(index.row())
        if book:
            self.statusbar.showMessage(f"{book['name']}")

    def _toggle_view_mode(self) -> None:
        """在封面模式和表格模式之间切换"""
        current = self.right_stack.currentIndex()
        if current == 0:
            # 当前是封面模式，切换到表格模式
            self.right_stack.setCurrentIndex(1)
            # TODO：切换到表格视图后，如何自动刷新表格的数据（是否可以直接调用实例属性self.table_view？）
            self.refresh_view()
            self.toggle_view_btn.setText(self._translate("Views", "切换到封面视图"))
            self.statusbar.showMessage(self._translate("Views", "已切换到表格视图"))
        elif current == 1:
            # 当前是表格模式，切换到封面模式
            self.right_stack.setCurrentIndex(0)
            self.refresh_view()
            self.toggle_view_btn.setText(self._translate("Views", "切换到表格视图"))
            self.statusbar.showMessage(self._translate("Views", "已切换到封面视图"))

    def refresh_view(self) -> None:
        """刷新当前数据；缓存有效时不重复查询全部书籍。"""
        if (
                self._books_cache is not None
                and (time.time() - self._cache_timestamp) < 15
        ):
            self._on_books_loaded(self._books_cache)
            return

        self.request_all.emit()

    def _on_query_error(self, error_msg: str) -> None:
        """所有数据库读取错误都在主线程提示用户。"""
        Tips.information_msg(f"数据库查询失败：{error_msg}")
        if self.statusbar is not None:
            self.statusbar.showMessage("数据库查询失败")

    def _on_database_initialized(self) -> None:
        """Worker完成连接和建表后，才允许发出查询请求。"""
        if self.statusbar is not None:
            self.statusbar.showMessage("数据库已就绪")
        self.request_all.emit()

    def _on_books_loaded(self, books: list[BookRecord]) -> None:
        """在主线程接收结果并刷新两个 Model。"""
        self._books_cache = books
        self._cache_timestamp = time.time()
        self.cover_model.set_books(books)
        self.table_model.set_data(books)

        if books:
            message = self._translate("Views", f"已刷新，共 {len(books)} 本书")
        else:
            message = self._translate("Views", "数据库中暂无书籍")
        if self.statusbar is not None:
            self.statusbar.showMessage(message)

    def load_by_type(self, book_type: str) -> None:
        """由界面传入具体类型，例如“小说”。"""
        self._books_cache = None
        self.request_by_type.emit(book_type)

    def load_by_status(self, read_status: str) -> None:
        """由界面传入具体阅读状态，例如“正在阅读”。"""
        self._books_cache = None
        self.request_by_status.emit(read_status)

    def load_by_author(self, author: str) -> None:
        """由界面传入具体作者名称。"""
        self._books_cache = None
        self.request_by_author.emit(author)

    def scan_books(self) -> None:
        """打开扫描窗口；每次只允许提交一个扫描批次。"""
        if self._scanning:
            return

        self._scan_dialog = ScanBookFiles()
        self._scan_dialog.books_scanned.connect(self._on_books_found)
        self._scan_dialog.select_directory()

    def _on_books_found(self, books: object) -> None:
        """接收扫描器生成的记录，并在Worker中检查重复项。"""
        if not isinstance(books, list):
            self._on_write_error("扫描器返回的数据格式不正确。")
            return
        if not books:
            if self.statusbar is not None:
                self.statusbar.showMessage("没有扫描到可入库的书籍")
            return
        if any(not isinstance(book, BookRecord) for book in books):
            self._on_write_error("扫描器返回的数据中包含无效书籍记录。")
            return

        self._pending_books = books
        self._scanning = True
        if self.scan_button is not None:
            self.scan_button.setEnabled(False)
        if self.statusbar is not None:
            self.statusbar.showMessage(
                f"正在检查 {len(books)} 本扫描书籍……"
            )
        self.request_find_duplicates.emit(books)

    def _on_duplicates_ready(self, duplicates: list) -> None:
        """由主线程逐本询问重复书籍的处理方式。"""
        if self._pending_books is None:
            self._on_write_error("没有待处理的重复文件。")
            return

        duplicate_actions: dict[int, str] = {}
        for index, book_name in duplicates:
            message = QMessageBox(self)
            message.setIcon(QMessageBox.Icon.Question)
            message.setText(f"检测到重复书籍：{book_name}")
            message.setInformativeText("应当如何处理？")

            # 添加自定义中文按钮 TODO: 目前UI文本为硬编码，后续可改为国际化
            overwrite = message.addButton("覆盖", QMessageBox.ButtonRole.YesRole)
            rename = message.addButton("重命名", QMessageBox.ButtonRole.NoRole)
            skip = message.addButton("跳过", QMessageBox.ButtonRole.ActionRole)
            cancel = message.addButton("全部取消", QMessageBox.ButtonRole.RejectRole)
            message.exec()
            clicked = message.clickedButton()
            if clicked == overwrite:
                duplicate_actions[index] = "overwrite"
            elif clicked == rename:
                duplicate_actions[index] = "rename"
            elif clicked == skip:
                duplicate_actions[index] = "skip"
            elif clicked == cancel:
                self._finish_scan_batch()
                if self.statusbar is not None:
                    self.statusbar.showMessage("已取消本次批量入库")
                if self._scan_dialog is not None:
                    self._scan_dialog.close()
                return

        self.request_insert_books.emit(self._pending_books, duplicate_actions)
        if self.statusbar is not None:
            self.statusbar.showMessage("正在批量写入数据库……")

    def _on_write_finished(
            self,
            added_count: int,
            skipped_count: int,
            overwritten_count: int,
    ) -> None:
        """写入成功后清除缓存，并让两个Model从数据库刷新。"""
        self._finish_scan_batch()
        self._books_cache = None
        self._cache_timestamp = 0.0

        if self.statusbar is not None:
            self.statusbar.showMessage(
                f"批量入库完成：新增 {added_count} 本，"
                f"覆盖 {overwritten_count} 本，跳过 {skipped_count} 本"
            )
        self.request_all.emit()

        if self._scan_dialog is not None:
            reply = QMessageBox.question(
                self,
                "扫描完成",
                "本批书籍已入库，是否继续扫描其他文件夹？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._scan_dialog.show()
                self._scan_dialog.select_directory()
            else:
                self._scan_dialog.close()

    def _on_write_error(self, error_msg: str) -> None:
        """写入或重复检查失败时结束批次并在主线程显示错误。"""
        self._finish_scan_batch()
        Tips.information_msg(error_msg)
        if self.statusbar is not None:
            self.statusbar.showMessage("批量入库失败")
        if self._scan_dialog is not None:
            self._scan_dialog.close()

    def _finish_scan_batch(self) -> None:
        """释放当前扫描批次并恢复扫描按钮。"""
        self._pending_books = None
        self._scanning = False
        if self.scan_button is not None:
            self.scan_button.setEnabled(True)

    def close_info(self) -> None:
        """清除主界面的书籍信息"""
        # self.bookWidget.clear()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        """窗口关闭时先关闭 Worker 连接，再退出线程。"""
        self.request_close_database.emit()
        if not self._database_thread.wait(3000):
            Tips.information_msg("数据库线程未能在3秒内正常退出。")
            event.ignore()
            return
        event.accept()

    @staticmethod
    def _translate(context, text) -> str:
        return QCoreApplication.translate(context, text)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
