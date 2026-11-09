# 阶段 2：将扫描入库迁移到 DatabaseWorker

本指南针对当前阶段 1 代码：`DatabaseWorker` 已经在专用 `QThread` 中维护 SQLite 连接并负责查询；`ScanBookFiles` 仍通过 `DatabaseManager` 在主线程逐本写库。阶段 2 只迁移扫描入库，不迁移编辑、删除和其他写操作。

目标是：扫描窗口只产生 `list[BookRecord]`；主线程把批次交给 `DatabaseWorker`；Worker 在自己的线程中完成重复检查和一个事务内的批量写入；成功后主线程使缓存失效并刷新两个 Model。Worker 不访问任何 Widget，也不把 SQLite 连接、游标或异常对象传回主线程。

> 以下代码是按项目当前 `database.py`、`books.py` 和 `app.py` 编写的可直接粘贴实现，不是伪代码。按步骤粘贴；不要把旧的 `DatabaseManager` 写入和新 Worker 写入同时用于扫描流程。

## 1. 阶段边界与重复书籍规则

- 保留 `DatabaseManager` 类供阶段 3 迁移删除、更新时使用；本阶段不创建 `DatabaseManager` 实例。
- 仅扫描入库改为异步；现有查询接口、`BookRecord` 及两个 Model 不变。
- 重复项先由 Worker 查询，再由主线程逐本弹窗选择“覆盖 / 重命名 / 跳过”。用户取消任一弹窗时，整个批次都不写入。
- 最终写入时 Worker 在事务中再次检查重复项。若检查后数据库被其他程序改变，Worker 会报错并回滚，而不是静默覆盖或跳过。
- 一个批次中的重复书名也作为重复项处理；所有记录的插入、覆盖、重命名、跳过都在同一个事务中完成。事务任一记录失败，整批回滚。
- 覆盖按 `book_name` 删除同名旧记录后插入新记录；重命名使用 `原书名(1)`、`原书名(2)`……寻找第一个可用名称。

## 2. 在 `database.py` 中增加批量写入接口

当前 `database.py` 已导入 `random`，且已有 `BOOK_COLUMNS`、`BookRecord`、`rows_to_records`。下面的代码放入现有 `DatabaseWorker`，无需新增导入。

### 2.1 添加 Worker 结果信号

在 `DatabaseWorker` 的信号定义区域加入：

```python
    duplicates_ready = Signal(list)
    write_finished = Signal(int, int, int)  # 新增数、跳过数、覆盖数
    write_error = Signal(str)
```

### 2.2 将以下完整方法加入 `DatabaseWorker`

```python
    def _book_name_exists(self, book_name: str) -> bool:
        """检查当前连接中是否已有同名书籍。"""
        row = self.connection.execute(
            "SELECT 1 FROM books_information WHERE book_name = ? LIMIT 1",
            (book_name,),
        ).fetchone()
        return row is not None

    @Slot(object)
    def find_batch_duplicates(self, books: object) -> None:
        """找出数据库中已存在或本批次内重复的书名，返回 (批次索引, 书名)。"""
        if self.connection is None:
            self.write_error.emit("数据库连接未初始化，无法检查重复书籍。")
            return

        try:
            if not isinstance(books, list):
                raise TypeError("待入库数据必须是 list[BookRecord]。")

            seen_names: set[str] = set()
            duplicates: list[tuple[int, str]] = []
            for index, book in enumerate(books):
                if not isinstance(book, BookRecord):
                    raise TypeError(f"第 {index + 1} 条数据不是 BookRecord。")
                if not isinstance(book.book_name, str) or not book.book_name.strip():
                    raise ValueError(f"第 {index + 1} 条书籍的书名不能为空。")

                book_name = book.book_name
                if book_name in seen_names or self._book_name_exists(book_name):
                    duplicates.append((index, book_name))
                seen_names.add(book_name)
        except (sqlite3.Error, TypeError, ValueError) as error:
            self.write_error.emit(f"检查重复书籍失败：{error}")
            return

        self.duplicates_ready.emit(duplicates)

    @staticmethod
    def _next_unique_book_name(
        book_name: str,
        occupied_names: set[str],
    ) -> str:
        """为重名书籍寻找第一个可用名称。"""
        suffix = 1
        while True:
            candidate = f"{book_name}({suffix})"
            if candidate not in occupied_names:
                return candidate
            suffix += 1

    @staticmethod
    def _next_unique_book_id(used_ids: set[int]) -> int:
        """生成当前数据库和本次批次都未使用的 10 位书籍 ID。"""
        while True:
            book_id = random.randint(1_000_000_000, 9_999_999_999)
            if book_id not in used_ids:
                used_ids.add(book_id)
                return book_id

    @Slot(object, object)
    def insert_books(self, books: object, duplicate_actions: object) -> None:
        """在单个 SQLite 事务中处理整批书籍。"""
        if self.connection is None:
            self.write_error.emit("数据库连接未初始化，无法写入书籍。")
            return

        try:
            if not isinstance(books, list):
                raise TypeError("待入库数据必须是 list[BookRecord]。")
            if not isinstance(duplicate_actions, dict):
                raise TypeError("重复书籍处理结果必须是字典。")

            for index, book in enumerate(books):
                if not isinstance(book, BookRecord):
                    raise TypeError(f"第 {index + 1} 条数据不是 BookRecord。")
                if not isinstance(book.book_name, str) or not book.book_name.strip():
                    raise ValueError(f"第 {index + 1} 条书籍的书名不能为空。")

            for index, action in duplicate_actions.items():
                if (
                    not isinstance(index, int)
                    or not 0 <= index < len(books)
                    or action not in {"overwrite", "rename", "skip"}
                ):
                    raise ValueError("重复书籍处理结果中包含无效选项。")

            added_count = 0
            skipped_count = 0
            overwritten_count = 0

            # 先取得 SQLite 写锁，再检查重复项，避免其他进程在检查与插入之间
            # 写入同名记录。connection 上下文负责成功提交或异常回滚。
            with self.connection:
                self.connection.execute("BEGIN IMMEDIATE")
                occupied_names = {
                    row[0]
                    for row in self.connection.execute(
                        "SELECT book_name FROM books_information"
                    ).fetchall()
                    if row[0] is not None
                }
                used_ids = {
                    row[0]
                    for row in self.connection.execute(
                        "SELECT book_id FROM books_information WHERE book_id IS NOT NULL"
                    ).fetchall()
                }

                placeholders = ", ".join("?" for _ in BOOK_COLUMNS)
                sql = (
                    f"INSERT INTO books_information "
                    f"({', '.join(BOOK_COLUMNS)}) "
                    f"VALUES ({placeholders})"
                )

                for index, book in enumerate(books):
                    original_name = book.book_name
                    final_name = original_name
                    is_duplicate = final_name in occupied_names

                    if is_duplicate:
                        action = duplicate_actions.get(index)
                        if action is None:
                            raise ValueError(
                                f"书名“{original_name}”在重复检查后发生变化，"
                                "请重新扫描并处理重复项。"
                            )

                        if action == "skip":
                            skipped_count += 1
                            continue
                        if action == "overwrite":
                            self.connection.execute(
                                "DELETE FROM books_information WHERE book_name = ?",
                                (original_name,),
                            )
                            occupied_names.discard(original_name)
                            overwritten_count += 1
                        elif action == "rename":
                            final_name = self._next_unique_book_name(
                                original_name,
                                occupied_names,
                            )

                    values = book.to_mapping()
                    values["book_name"] = final_name
                    if values["book_id"] is None or values["book_id"] in used_ids:
                        values["book_id"] = self._next_unique_book_id(used_ids)
                    else:
                        used_ids.add(values["book_id"])

                    self.connection.execute(
                        sql,
                        tuple(values[column] for column in BOOK_COLUMNS),
                    )
                    occupied_names.add(final_name)
                    added_count += 1

        except (sqlite3.Error, TypeError, ValueError) as error:
            self.write_error.emit(f"批量写入失败，已回滚本批次：{error}")
            return

        self.write_finished.emit(
            added_count,
            skipped_count,
            overwritten_count,
        )
```

`insert_books` 成功返回的第一个数字包含新增和覆盖后插入的记录；第二个数字是跳过数；第三个数字是覆盖数。数据库表结构仍使用现有 `BOOK_COLUMNS` 的 19 个字段。

## 3. 修改 `books.py`：扫描器只产生记录，不再操作数据库

### 3.1 替换导入

将：

```python
from PySide6.QtCore import Qt
from database import DatabaseManager
```

替换为：

```python
from PySide6.QtCore import Qt, Signal

from book_record import BookRecord
```

### 3.2 在 `ScanBookFiles` 类的 docstring 后增加信号

```python
    books_scanned = Signal(object)
```

### 3.3 替换 `scan_book_files` 与 `write_into_database`

在 `ScanBookFiles` 中，用下面两段完整方法替换原来的 `scan_book_files` 和 `write_into_database`。`write_into_database` 不再保留。

```python
    def scan_book_files(self, directory: str) -> None:
    """扫描目录并把记录交给主窗口，不在这里创建数据库连接。"""
    date = datetime.datetime.now().strftime("%Y年%m月%d日 %H:%M:%S")
    supported_extensions = (".pdf", ".doc", ".docx", ".epub", ".txt")
    scan_info: list[dict[str, str]] = []

    try:
        for root, _dirs, files in os.walk(directory):
            for file_name in files:
                if file_name.lower().endswith(supported_extensions):
                    book_name, extension = os.path.splitext(file_name)
                    scan_info.append({
                        "book_name": book_name,
                        "book_path": root,
                        "book_type": extension.lower(),
                        "add_time": date,
                    })

        if not scan_info:
            choose = QMessageBox.question(
                self,
                "提示",
                f"未扫描到书籍文件，是否手动检查以下路径：\n{directory}？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No,
            )
            if choose == QMessageBox.StandardButton.Yes:
                os.startfile(directory)
        else:
            books = [
                BookRecord.from_mapping(book_info)
                for book_info in scan_info
            ]
            self.books_scanned.emit(books)
            self.hide()
            return

        reply = QMessageBox.question(
            self,
            "提示",
            "扫描完成！是否继续扫描其他文件夹？",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.select_directory()
        else:
            Tips.info_msg("扫描已结束！")
            self.close()

    except FileNotFoundError:
        Tips.info_msg("系统找不到指定的文件，请重试。")
    except NotADirectoryError:
        Tips.info_msg("系统找不到指定的路径，请重试。")
    except PermissionError:
        Tips.info_msg("扫描路径无访问权限，请检查路径权限。")
```

扫描器仍然在主线程同步遍历目录；本阶段只把 SQLite 写入移出主线程。每个批次扫描一个目录，批次入库完成后再询问是否扫描下一个目录，避免写入尚未完成时第二批覆盖第一批的待处理状态。把大型目录扫描本身改成后台任务属于后续独立优化，不在阶段 2 引入第二个线程。

## 4. 修改 `app.py`：连接扫描信号与 Worker

### 4.1 增加请求信号

在 `MainWindow` 当前的请求信号定义中加入：

```python
    request_find_duplicates = Signal(object)
    request_insert_books = Signal(object, object)
```

### 4.2 在 `__init__` 中初始化批次状态

在调用 `_setup_book_worker()` 之前加入：

```python
        self._pending_books: list[BookRecord] | None = None
self._scanning = False
self._scan_dialog: ScanBookFiles | None = None
```

### 4.3 替换 `_setup_book_worker`

用下面完整方法替换现有 `_setup_book_worker`。它保留阶段 1 的读取连接，并将新的批量写入槽连接到同一个 Worker：

```python
    def _setup_book_worker(self) -> None:


"""创建统一数据库线程，并连接读取及批量写入请求。"""
self._database_thread = QThread(self)
self._worker = DatabaseWorker()
self._worker.moveToThread(self._database_thread)

self._worker.initialized.connect(
    self._on_database_initialized
)
self._worker.books_ready.connect(self._on_books_loaded)
self._worker.query_error.connect(self._on_query_error)
self._worker.duplicates_ready.connect(
    self._on_duplicates_ready
)
self._worker.write_finished.connect(
    self._on_write_finished
)
self._worker.write_error.connect(self._on_write_error)

self.request_initialize.connect(
    self._worker.initialize
)
self.request_all.connect(
    self._worker.fetch_all_books
)
self.request_by_type.connect(
    self._worker.fetch_by_type
)
self.request_by_status.connect(
    self._worker.fetch_by_status
)
self.request_by_author.connect(
    self._worker.fetch_by_author
)
self.sig_find_duplicates.connect(
    self._worker.find_batch_duplicates
)
self.sig_insert_books.connect(
    self._worker.insert_books
)
self.sig_close_database.connect(
    self._worker.close_database
)

self._database_thread.finished.connect(
    self._worker.deleteLater
)
self._database_thread.start()
self.request_initialize.emit()
```

### 4.4 替换 `scan_books`，并增加扫描/写入结果槽

在 `MainWindow` 中删除旧的 `@staticmethod scan_books`，并粘贴以下方法。原 `handle_duplicate_book` 与 `show_add_result` 是旧 `DatabaseManager` 流程的遗留入口，本阶段不再调用，可一并删除；不要保留对 `self.db` 的调用。

```python
    def scan_books(self) -> None:


"""打开扫描窗口；每次只允许提交一个扫描批次。"""
if self._scanning:
    return

self._scan_dialog = ScanBookFiles(self)
self._scan_dialog.books_scanned.connect(
    self._on_books_found
)
self._scan_dialog.show()
self._scan_dialog.select_directory()


def _on_scan_books_found(self, books: object) -> None:
    """接收扫描器生成的记录，并在 Worker 中检查重复项。"""
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
    self.sig_find_duplicates.emit(books)


def _on_duplicates_ready(self, duplicates: list) -> None:
    """由主线程逐本询问重复书籍的处理方式。"""
    if self._pending_books is None:
        self._on_write_error("收到重复检查结果时，没有待处理的扫描批次。")
        return

    duplicate_actions: dict[int, str] = {}
    for index, book_name in duplicates:
        message = QMessageBox(self)
        message.setIcon(QMessageBox.Question)
        message.setText(f"检测到重复书籍：{book_name}")
        message.setInformativeText(
            "选择的处理方式将用于这一条重复记录。"
        )
        message.setStandardButtons(
            QMessageBox.Yes
            | QMessageBox.No
            | QMessageBox.Ignore
            | QMessageBox.Cancel
        )
        message.setButtonText(QMessageBox.Yes, "覆盖")
        message.setButtonText(QMessageBox.No, "重命名")
        message.setButtonText(QMessageBox.Ignore, "跳过")
        message.setButtonText(QMessageBox.Cancel, "取消整个批次")

        choice = message.exec()
        if choice == QMessageBox.Yes:
            duplicate_actions[index] = "overwrite"
        elif choice == QMessageBox.No:
            duplicate_actions[index] = "rename"
        elif choice == QMessageBox.Ignore:
            duplicate_actions[index] = "skip"
        else:
            self._finish_scan_batch()
            if self.statusbar is not None:
                self.statusbar.showMessage("已取消本次批量入库")
            if self._scan_dialog is not None:
                self._scan_dialog.close()
            return

    self.sig_insert_books.emit(
        self._pending_books,
        duplicate_actions,
    )
    if self.statusbar is not None:
        self.statusbar.showMessage("正在批量写入数据库……")


def _on_write_finished(
        self,
        added_count: int,
        skipped_count: int,
        overwritten_count: int,
) -> None:
    """写入成功后清除缓存，并让两个 Model 从数据库刷新。"""
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
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._scan_dialog.show()
            self._scan_dialog.select_directory()
        else:
            self._scan_dialog.close()


def _on_write_error(self, error_msg: str) -> None:
    """写入或重复检查失败时结束批次并在主线程显示错误。"""
    self._finish_scan_batch()
    Tips.info_msg(error_msg)
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
```

如果担心旧的查询结果在写入后覆盖新列表，可在 `books_ready` 的结果中增加请求编号；当前应用的筛选 UI 尚未接通，阶段 2 保持阶段 1 的加载流程，不额外引入请求编号机制。

### 4.5 清理旧导入

`app.py` 顶部把数据库导入改为：

```python
from database import DatabaseWorker
```

`DatabaseManager` 在扫描流程中不再使用。保留 `DatabaseWorker`、`BookRecord`、`ScanBookFiles` 的其他现有导入。删除已经不用的 `handle_duplicate_book`、`show_add_result` 后，如果某个导入只被这两个方法使用，也一并删除。

## 5. 可直接运行的阶段 2 验证脚本

在项目根目录新建 `verify_phase2.py`，粘贴以下完整脚本。它使用临时数据库验证：阶段 1 查询仍正常、重复检测包含数据库与批次内冲突、重命名和跳过结果正确，以及触发 SQL 错误时整批回滚。

```python
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from book_record import BOOK_COLUMNS, BookRecord
from database import DatabaseWorker


class Phase2Check(QObject):
    initialize_requested = Signal()
    fetch_all_requested = Signal()
    find_duplicates_requested = Signal(object)
    insert_books_requested = Signal(object, object)
    close_requested = Signal()

    def __init__(self, app: QCoreApplication, db_name: str):
        super().__init__()
        self.app = app
        self.thread = QThread()
        self.worker = DatabaseWorker(db_name)
        self.worker.moveToThread(self.thread)

        self.initialize_requested.connect(self.worker.initialize)
        self.fetch_all_requested.connect(self.worker.fetch_all_books)
        self.find_duplicates_requested.connect(
            self.worker.find_batch_duplicates
        )
        self.insert_books_requested.connect(self.worker.insert_books)
        self.close_requested.connect(self.worker.close_database)

        self.worker.initialized.connect(self._on_initialized)
        self.worker.books_ready.connect(self._on_books_ready)
        self.worker.duplicates_ready.connect(self._on_duplicates_ready)
        self.worker.write_finished.connect(self._on_write_finished)
        self.worker.write_error.connect(self._on_write_error)
        self.worker.query_error.connect(self._on_query_error)
        self.worker.database_closed.connect(self._on_database_closed)
        self.thread.finished.connect(self.worker.deleteLater)

        self.errors: list[str] = []
        self.stage = "starting"
        self.thread.start()
        self.initialize_requested.emit()

    def _on_initialized(self) -> None:
        self.stage = "initial-read"
        self.fetch_all_requested.emit()

    def _on_books_ready(self, books: list[BookRecord]) -> None:
        if self.stage == "initial-read":
            assert len(books) == 1
            assert books[0].book_name == "既有书"
            assert len(books[0].to_row()) == len(BOOK_COLUMNS)

            self.stage = "duplicate-check"
            self.find_duplicates_requested.emit([
                BookRecord(book_name="既有书"),
                BookRecord(book_name="新书"),
                BookRecord(book_name="新书"),
            ])
            return

        if self.stage == "check-first-batch":
            names = {book.book_name for book in books}
            assert len(books) == 3
            assert "既有书" in names
            assert "既有书(1)" in names
            assert "新书" in names
            assert "既有书(2)" not in names

            self.stage = "rollback-duplicate-check"
            self.find_duplicates_requested.emit([
                BookRecord(book_name="回滚前"),
                BookRecord(book_name="故意失败"),
            ])
            return

        if self.stage == "check-rollback":
            names = {book.book_name for book in books}
            assert len(books) == 3
            assert "回滚前" not in names
            assert "故意失败" not in names
            self.stage = "closing"
            self.close_requested.emit()
            return

        self._fail(f"在阶段 {self.stage} 收到意外查询结果")

    def _on_duplicates_ready(
        self,
        duplicates: list[tuple[int, str]],
    ) -> None:
        if self.stage == "duplicate-check":
            assert duplicates == [(0, "既有书"), (2, "新书")]
            self.stage = "write-first-batch"
            self.insert_books_requested.emit(
                [
                    BookRecord(book_name="既有书"),
                    BookRecord(book_name="新书"),
                    BookRecord(book_name="新书"),
                ],
                {0: "rename", 2: "skip"},
            )
            return

        if self.stage == "rollback-duplicate-check":
            assert duplicates == []
            self.stage = "write-rollback-batch"
            self.insert_books_requested.emit(
                [
                    BookRecord(book_name="回滚前"),
                    BookRecord(book_name="故意失败"),
                ],
                {},
            )
            return

        self._fail(f"在阶段 {self.stage} 收到意外重复检查结果")

    def _on_write_finished(
        self,
        added_count: int,
        skipped_count: int,
        overwritten_count: int,
    ) -> None:
        if self.stage != "write-first-batch":
            self._fail(f"阶段 {self.stage} 不应收到写入成功信号")
            return

        assert (added_count, skipped_count, overwritten_count) == (2, 1, 0)
        self.stage = "check-first-batch"
        self.fetch_all_requested.emit()

    def _on_write_error(self, message: str) -> None:
        if self.stage != "write-rollback-batch":
            self._fail(f"阶段 {self.stage} 意外写入失败：{message}")
            return

        assert "已回滚" in message
        self.stage = "check-rollback"
        self.fetch_all_requested.emit()

    def _on_query_error(self, message: str) -> None:
        self._fail(f"数据库查询失败：{message}")

    def _on_database_closed(self) -> None:
        self.thread.quit()
        self.thread.finished.connect(self.app.quit)

    def _fail(self, message: str) -> None:
        self.errors.append(message)
        self.close_requested.emit()


def create_database(path: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE books_information (
                book_id INTEGER, book_name TEXT NOT NULL, book_path TEXT,
                add_time TEXT, author TEXT, nationality TEXT, translator TEXT,
                publisher TEXT, publication_date TEXT, level TEXT,
                read_status TEXT, book_type TEXT, isbn INTEGER, pages INTEGER,
                read_progress TEXT, read_time REAL, read_date TEXT,
                read_link TEXT, introduction TEXT
            )
            """
        )
        existing_book = [None] * len(BOOK_COLUMNS)
        existing_book[0] = 1
        existing_book[1] = "既有书"
        connection.execute(
            "INSERT INTO books_information VALUES "
            f"({', '.join('?' for _ in BOOK_COLUMNS)})",
            existing_book,
        )
        connection.execute(
            """
            CREATE TRIGGER reject_test_book
            BEFORE INSERT ON books_information
            WHEN NEW.book_name = '故意失败'
            BEGIN
                SELECT RAISE(ABORT, '阶段2回滚验证');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    app = QCoreApplication(sys.argv)
    file_descriptor, db_name = tempfile.mkstemp(suffix=".db")
    os.close(file_descriptor)

    try:
        create_database(db_name)
        check = Phase2Check(app, db_name)
        exit_code = app.exec()
        if check.errors:
            raise AssertionError("\n".join(check.errors))
        assert check.stage == "closing", (
            f"验证流程未完成，最后阶段：{check.stage}"
        )
        print("阶段 2 批量写入检查全部通过")
        raise SystemExit(exit_code)
    finally:
        if os.path.exists(db_name):
            os.remove(db_name)


if __name__ == "__main__":
    main()
```

## 6. 实施与验证顺序

在项目根目录执行：

```powershell
uv run python -m compileall src/ausarum tests
python verify_phase1.py
python verify_phase2.py
```

预期结果：

- 阶段 1 检查输出 `阶段 1 DatabaseWorker 检查全部通过`。
- 阶段 2 检查输出 `阶段 2 批量写入检查全部通过`。
- 编译命令没有语法错误。

随后运行 `uv run ausarum`，手工检查：

1. 扫描仅包含新书的目录，成功后封面和表格都出现新书。
2. 再次扫描同一目录，对重复项分别选择覆盖、重命名、跳过，确认状态栏统计和最终数据正确。
3. 扫描多本书时，取消任一重复项弹窗，确认这一批一本也没有写入。
4. 检查批次里有重复书名的目录，确认同批次重复也会弹窗，不会产生未处理冲突。
5. 连续扫描不同目录，确认扫描按钮会在当前批次完成或取消后恢复。
6. 扫描过程中切换封面/表格视图；写入成功后两种 Model 都显示刷新后的数据。
7. 关闭窗口，确认没有 `QThread: Destroyed while thread is still running` 警告。

## 7. 常见问题与阶段 2 完成标准

| 现象 | 检查点 |
|---|---|
| `ScanBookFiles` 没有 `books_scanned` | 确认信号定义在类体中，并已替换 `books.py` 的两个旧方法。 |
| 扫描后没有重复检查 | 确认 `scan_books` 创建的对话框连接了 `books_scanned`，且 `sig_find_duplicates` 已连接 Worker 槽。 |
| `QObject::connect` 或信号参数错误 | 请求信号为 `Signal(object)` 与 `Signal(object, object)`；不要传递 SQLite 对象。 |
| 写入后视图未刷新 | 确认 `write_finished` 连接 `_on_write_finished`，此槽清空缓存后调用 `request_fetch_all.emit()`。 |
| 一批书只写入部分记录 | 检查 Worker 使用 `with self.connection:`，且 SQL 错误通过 `write_error` 返回；整批应回滚。 |
| 重命名书名仍冲突 | 确认 Worker 的 `occupied_names` 在每条插入后更新，重命名通过 `_next_unique_book_name` 分配。 |
| 重复项弹窗导致界面无法操作 | 此处是等待用户选择的模态对话框，数据库线程不持有事务；直到用户确认后才提交写入请求。 |

阶段 2 完成时应满足：扫描路径不再创建 `DatabaseManager`；所有扫描批次经同一个 `DatabaseWorker` 写入；一个批次只有一次事务提交；失败整批回滚并显示错误；成功后缓存失效且两个 Model 刷新；旧的 `self.db`、`handle_duplicate_book`、`show_add_result` 扫描写入调用已移除。`DatabaseManager` 类本身暂时保留，留待阶段 3 迁移其余写操作后再清理。
