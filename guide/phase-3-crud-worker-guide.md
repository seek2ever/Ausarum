# 阶段 3：将删除和更新操作迁移到 DatabaseWorker

本指南以当前项目阶段 2 的代码为基础：`DatabaseWorker` 已经在专用 `QThread` 中执行查询和扫描批量写入；`MainWindow` 维护一个 Worker；扫描重复项和扫描写入已通过信号完成。

阶段 3 把其余修改操作也迁入同一个 Worker。本项目当前没有已经接入数据库的删除按钮或编辑保存流程：表格模型允许直接修改内存数据，`on_table_data_changed()` 目前只打印；旧 `DatabaseManager` 定义了主线程增删改方法，但目前没有被 `MainWindow` 或 `books.py` 实例化。`pdf.py` 仍会创建 `DatabaseManager` 读取书籍路径，因此本阶段不能直接删除整个旧类。本指南会一并接通表格编辑保存和一个选中行删除入口，并保留旧类供这个尚未迁移的 PDF 查询使用。

> 本文代码按当前 `database.py`、`app.py`、`table_model.py`、`book_record.py` 和阶段 2 验证脚本编写，代码块均为可直接粘贴的实现片段。不要让旧 `DatabaseManager` 和 `DatabaseWorker` 同时处理同一项修改。

## 1. 阶段目标与边界

完成后应满足：

- 所有 SQLite 修改只在 `DatabaseWorker` 所在线程执行；主线程不持有连接、游标，也不直接执行 SQL。
- `DatabaseWorker` 继续复用现有 SQLite 连接和阶段 2 的 `insert_books()`，并新增按 `book_id` 更新和删除的方法。
- 表格可编辑字段写入数据库；`book_id` 是稳定标识，不允许用户编辑。
- 更新书名时拒绝空名称和已有重名；删除和更新均要求目标 `book_id` 唯一且确实存在。
- 更新或删除成功后清除缓存并重新查询，封面 Model 和表格 Model 都从数据库刷新；失败时明确提示并重新读取数据库，撤销表格中的未保存显示。
- 阶段 1、阶段 2 的查询、扫描重复处理和批量写入行为保持不变。

本阶段不改数据库表结构，不添加复杂的详情编辑对话框，也不改变扫描时“覆盖 / 重命名 / 跳过”的规则。这里的编辑入口是项目已有的表格直接编辑；删除入口是左侧新增的“删除选中书籍”按钮，仅在表格视图选中单行后使用。

## 2. 数据库操作规则

| 操作 | 标识 | 事务规则 | 成功后的 UI |
|---|---|---|---|
| 更新一个字段 | `book_id` + 白名单字段名 | `BEGIN IMMEDIATE`；唯一性检查与更新在同一事务 | 失效缓存、重新查询两个 Model |
| 删除一本书 | `book_id` | `BEGIN IMMEDIATE`；确认目标 ID 只对应一条记录 | 失效缓存、重新查询两个 Model |

SQL 参数绑定只能保护**值**，不能把列名作为 `?` 参数传入。因此更新方法先用 `BOOK_COLUMNS` 白名单检查列名，再把通过检查的字段名放进 SQL；所有用户数据仍通过 `?` 绑定。

## 3. 修改 `database.py`：新增安全的更新和删除槽

### 3.1 在 `DatabaseWorker` 中添加两个信号

在 `DatabaseWorker` 当前的信号定义区域（`write_error` 附近）添加：

```python
    mutation_finished = Signal(str, int)  # 操作类型（update/delete）、book_id
    mutation_error = Signal(str)
```

不要复用 `write_finished` / `write_error`：它们专供阶段 2 的扫描批次使用，单独的信号可以避免两种 UI 流程互相误处理结果。

在 `database.py` 顶部的标准库导入区域增加：

```python
import math
```

### 3.2 将以下两个完整方法添加到 `DatabaseWorker`

将代码放在 `insert_books()` 之后、`close_database()` 之前：

```python
    @Slot(int, str, object)


def update_book_field(
        self,
        book_id: int,
        field_name: str,
        value: object,
) -> None:
    """按稳定 ID 更新一个字段；禁止更改 book_id。"""
    if self.connection is None:
        self.change_error.emit("数据库连接未初始化，无法更新书籍。")
        return

    try:
        if not isinstance(book_id, int) or isinstance(book_id, bool):
            raise ValueError("书籍 ID 必须是整数。")
        if book_id <= 0:
            raise ValueError("书籍 ID 无效。")

        # book_id 是记录定位键，不允许通过表格编辑。
        editable_columns = frozenset(BOOK_COLUMNS[1:])
        if field_name not in editable_columns:
            raise ValueError(f"不允许编辑字段：{field_name}")

        if field_name in {"isbn", "pages"}:
            if value is None or value == "":
                normalized_value = None
            elif isinstance(value, bool):
                raise ValueError(f"{field_name} 必须是整数。")
            else:
                if isinstance(value, float) and not value.is_integer():
                    raise ValueError(f"{field_name} 必须是整数。")
                normalized_value = int(value)
                if field_name == "pages" and normalized_value < 0:
                    raise ValueError("页数不能小于 0。")
        elif field_name == "read_time":
            if value is None or value == "":
                normalized_value = None
            else:
                normalized_value = float(value)
                if not math.isfinite(normalized_value) or normalized_value < 0:
                    raise ValueError("阅读时长必须是有限的非负数字。")
        elif value is None:
            normalized_value = None
        elif isinstance(value, str):
            normalized_value = value
        else:
            normalized_value = str(value)

        if field_name == "book_name":
            if not isinstance(normalized_value, str):
                raise ValueError("书名不能为空。")
            normalized_value = normalized_value.strip()
            if not normalized_value:
                raise ValueError("书名不能为空。")

        with self.connection:
            # 先锁定写事务，再做校验，避免检查后另一写入者抢先修改记录。
            self.connection.execute("BEGIN IMMEDIATE")
            matching_rows = self.connection.execute(
                "SELECT COUNT(*) FROM books_information WHERE book_id = ?",
                (book_id,),
            ).fetchone()[0]
            if matching_rows != 1:
                raise ValueError(
                    f"书籍 ID {book_id} 对应 {matching_rows} 条记录，"
                    "无法安全更新。"
                )

            if field_name == "book_name":
                duplicate = self.connection.execute(
                    """
                    SELECT 1
                    FROM books_information
                    WHERE book_name = ? AND book_id <> ?
                    LIMIT 1
                    """,
                    (normalized_value, book_id),
                ).fetchone()
                if duplicate is not None:
                    raise ValueError(
                        f"书名“{normalized_value}”已经存在，未保存修改。"
                    )

            # field_name 已经过白名单验证；字段值仍必须使用参数绑定。
            cursor = self.connection.execute(
                f"UPDATE books_information SET {field_name} = ? "
                "WHERE book_id = ?",
                (normalized_value, book_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("更新记录数量不为 1，已撤销本次操作。")

    except (sqlite3.Error, OverflowError, TypeError, ValueError) as error:
        self.change_error.emit(f"更新书籍失败：{error}")
        return

    self.change_finished.emit("update", book_id)


@Slot(int)
def delete_book_by_id(self, book_id: int) -> None:
    """按唯一的 book_id 删除一条记录。"""
    if self.connection is None:
        self.change_error.emit("数据库连接未初始化，无法删除书籍。")
        return

    try:
        if not isinstance(book_id, int) or isinstance(book_id, bool):
            raise ValueError("书籍 ID 必须是整数。")
        if book_id <= 0:
            raise ValueError("书籍 ID 无效。")

        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            matching_rows = self.connection.execute(
                "SELECT COUNT(*) FROM books_information WHERE book_id = ?",
                (book_id,),
            ).fetchone()[0]
            if matching_rows != 1:
                raise ValueError(
                    f"书籍 ID {book_id} 对应 {matching_rows} 条记录，"
                    "无法安全删除。"
                )

            cursor = self.connection.execute(
                "DELETE FROM books_information WHERE book_id = ?",
                (book_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("删除记录数量不为 1，已撤销本次操作。")

    except (sqlite3.Error, OverflowError, TypeError, ValueError) as error:
        self.change_error.emit(f"删除书籍失败：{error}")
        return

    self.change_finished.emit("delete", book_id)
```

`with self.connection` 会在代码正常离开时提交，在异常离开时回滚。重复名称校验也位于写事务里，所以不会出现“先检查重名、之后才更新”的竞态窗口。当前表没有 `book_id` 唯一约束；方法因此显式要求 ID 只对应一条记录，遇到历史重复 ID 时宁可拒绝修改，也不误改多本书。

### 3.3 确认关闭方法保持原样

`DatabaseWorker.close_database()` 保持现有实现；不要为删除或更新新建额外 `QThread` 或 SQLite 连接。

## 4. 修改 `table_model.py`：禁止改动 `book_id`

当前 Model 的 `flags()` 将每一列都标记为可编辑，`setData()` 也允许修改任意列。用以下版本**替换**这两个方法：

```python
    def setData(
        self,
        index,
        value: typing.Any,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid():
            return False

        row = index.row()
        column = index.column()
        if (
            role != Qt.ItemDataRole.EditRole
            or not (0 <= row < self.rowCount())
            or not (0 <= column < self.columnCount())
            or column == BOOK_ID_INDEX
        ):
            return False

        self._table_books[row][column] = value
        self.dataChanged.emit(
            index,
            index,
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole],
        )
        return True

    def flags(self, index):
        """仅将书籍信息字段标为可编辑，ID 保持只读。"""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags

        flag = super().flags(index)
        if index.column() != BOOK_ID_INDEX:
            flag |= Qt.ItemFlag.ItemIsEditable
        return flag
```

这里的 `BOOK_ID_INDEX` 已在 `table_model.py` 顶部定义为 `0`。`setData()` 与 `flags()` 都检查 ID 列：前者是实际写入保护，后者是界面可编辑状态。

## 5. 修改 `app.py`：连接 Worker 请求和结果

### 5.1 导入列名常量

将现有 `book_record` 导入：

```python
from book_record import (
    BOOK_COLUMNS,
    BOOK_COLUMN_TITLES,
    BookRecord,
)
```

### 5.2 添加请求信号

在 `MainWindow` 当前的信号定义区域添加：

```python
    request_update_book = Signal(int, str, object)
    request_delete_book = Signal(int)
```

### 5.3 在 `_setup_book_worker()` 中接通信号

在 Worker 结果信号连接区域添加：

```python
        self._worker.change_finished.connect(
    self._on_change_finished
)
self._worker.change_error.connect(
    self._on_change_error
)
```

在主线程请求信号连接区域添加：

```python
        self.sig_update_book.connect(
    self._worker.update_book_field
)
self.sig_delete_book.connect(
    self._worker.delete_book_by_id
)
```

这些连接应放在现有 `sig_insert_books`、`sig_close_database` 连接附近。Worker 结果槽函数仍由 Qt 在主线程执行，数据库操作则由 Worker 所在线程执行。

## 6. 修改表格编辑处理：把修改请求发给 Worker

用以下方法**完整替换** `MainWindow.on_table_data_changed()`。原方法只打印数据；新方法将修改后的字段值通过信号发送给 Worker。

```python
    def on_table_data_changed(self, top_left, bottom_right, roles) -> None:


    """把表格编辑转换成 Worker 更新请求。"""
if roles and Qt.ItemDataRole.EditRole not in roles:
    return

for row in range(top_left.row(), bottom_right.row() + 1):
    for column in range(top_left.column(), bottom_right.column() + 1):
        if column == 0:
            continue

        index = self.table_model.index(row, column)
        book_id = self.table_model.data(
            index,
            role=BookTableModel.BookIdRole,
        )
        if not isinstance(book_id, int) or isinstance(book_id, bool):
            self._on_change_error(
                "当前书籍没有有效 ID，无法保存表格修改。"
            )
            return

        field_name = BOOK_COLUMNS[column]
        new_value = self.table_model.data(
            index,
            role=Qt.ItemDataRole.EditRole,
        )
        self.sig_update_book.emit(
            book_id,
            field_name,
            new_value,
        )

if self.statusbar is not None:
    self.statusbar.showMessage("正在保存表格修改……")
```

`dataChanged` 发出时 Model 的内存值已经改变，所以 Worker 失败后必须重新查询数据库，把 Model 恢复成实际保存的数据。第 8 节会加入统一的成功、失败处理。

## 7. 增加删除入口

### 7.1 在左侧快捷操作区添加按钮

在 `_create_left_panel()` 中，创建 `self.scan_button` 的附近添加：

```python
        self.delete_button = QPushButton("删除选中书籍")
self.delete_button.setFixedHeight(36)
self.delete_button.clicked.connect(self._delete_book)
```

然后在 `layout.addWidget(self.scan_button)` 后添加：

```python
        layout.addWidget(self.delete_button)
```

不用替换现有的“取消显示”按钮；它目前并不是数据库删除操作。

### 7.2 配置表格单行选择

在 `_create_table_view()` 中，`self.table_view.setModel(self.table_model)` 后添加：

```python
        self.table_view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self.table_view.setSelectionMode(
            QTableView.SelectionMode.SingleSelection
        )
```

### 7.3 添加删除槽函数

将以下方法添加到 `MainWindow` 类中，例如放在 `on_table_data_changed()` 后面：

```python
    def _delete_selected_book(self) -> None:
    """确认后按书籍 ID 请求 Worker 删除选中记录。"""
    selected_rows = self.table_view.selectionModel().selectedRows()
    if not selected_rows:
        QMessageBox.information(
            self,
            "删除书籍",
            "请先切换到表格视图并选择一本书。",
        )
        return

    row = selected_rows[0].row()
    index = self.table_model.index(row, 0)
    book_id = self.table_model.data(
        index,
        role=BookTableModel.BookIdRole,
    )
    book_name = self.table_model.data(
        self.table_model.index(row, 1),
        role=Qt.ItemDataRole.DisplayRole,
    )
    if not isinstance(book_id, int) or isinstance(book_id, bool):
        Tips.information_msg("选中书籍没有有效 ID，无法删除。")
        return

    answer = QMessageBox.question(
        self,
        "确认删除",
        f"确定从书库中删除《{book_name}》吗？\n"
        "此操作不会删除本地电子书文件。",
        QMessageBox.StandardButton.Yes
        | QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return

    self.delete_button.setEnabled(False)
    self.sig_delete_book.emit(book_id)
    if self.statusbar is not None:
        self.statusbar.showMessage("正在删除书籍……")
```

这里仅删除数据库记录，不触碰本地文件；这与本项目目前“管理本地图书信息”的定位一致。

## 8. 添加统一修改结果处理

将以下两个方法添加到 `MainWindow` 类中：

```python
    def _invalidate_books_cache(self) -> None:
    """写操作成功或失败后都不能继续使用旧缓存。"""
    self._books_cache = None
    self._cache_timestamp = 0.0


def _on_mutation_finished(
        self,
        operation: str,
        book_id: int,
) -> None:
    """修改成功后刷新封面和表格 Model。"""
    self._invalidate_cache()
    if hasattr(self, "delete_button"):
        self.delete_button.setEnabled(True)

    if self.statusbar is not None:
        action = "更新" if operation == "update" else "删除"
        self.statusbar.showMessage(
            f"书籍 {book_id} 已{action}，正在刷新视图……"
        )
    self.request_all.emit()


def _on_mutation_error(self, error_msg: str) -> None:
    """修改失败时提示用户，并从数据库恢复 Model 的真实内容。"""
    self._invalidate_cache()
    if hasattr(self, "delete_button"):
        self.delete_button.setEnabled(True)

    Tips.information_msg(error_msg)
    if self.statusbar is not None:
        self.statusbar.showMessage("书籍修改失败，正在恢复数据……")
    self.request_all.emit()
```

该方法通过现有 `_on_books_loaded()` 更新 `cover_model` 和 `table_model`。错误时也刷新，因为表格在 `dataChanged` 时已经更新了内存值；重新查询可以撤销未写入的值。`delete_button` 的 `hasattr` 检查可覆盖窗口初始化早期出现错误的情况。

## 9. 清理旧 `DatabaseManager`

当前 `app.py` 已只导入 `DatabaseWorker`，扫描器也只发送 `BookRecord`；但 `pdf.py:get_pdf_path()` 仍会创建 `DatabaseManager` 并调用 `get_book()`。因此本阶段不要删除整个 `DatabaseManager` 类，也不要删除 PDF 仍在使用的查询方法。

本阶段应确认 `MainWindow`、扫描器及其他新增 UI 流程不再实例化 `DatabaseManager` 或调用其写方法。对 `DatabaseManager`、`handle_duplicate_book`、`add_book_result`、`update_book(` 和 `delete_book(` 做项目搜索；若写方法还有调用方，先迁移到 Worker 请求信号，不要直接删除仍被调用的 API。旧类及其只读连接的最终清理由总架构指南阶段 5 处理：先将 `pdf.py` 的路径查询迁入 Worker，再确认无调用方后删除 `DatabaseManager` 和旧类型导入。

不要删除阶段 2 的 `insert_books()`、`find_batch_duplicates()` 或其信号；扫描流程依赖这些接口。

## 10. 新增可直接运行的验证脚本

在项目根目录新建 `verify_phase3.py`，完整粘贴以下脚本。它使用临时 SQLite 数据库验证更新字段、拒绝重名、按 ID 删除并确认非目标记录仍存在；不会修改正式的 `books_information.db`。

```python
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from database import DatabaseWorker


class Phase3Check(QObject):
    initialize_requested = Signal()
    update_requested = Signal(int, str, object)
    delete_requested = Signal(int)
    fetch_all_requested = Signal()
    close_requested = Signal()

    def __init__(self, app: QCoreApplication, db_name: str):
        super().__init__()
        self.app = app
        self.thread = QThread()
        self.worker = DatabaseWorker(db_name)
        self.worker.moveToThread(self.thread)

        self.initialize_requested.connect(self.worker.initialize)
        self.update_requested.connect(self.worker.update_book_field)
        self.delete_requested.connect(self.worker.delete_book_by_id)
        self.fetch_all_requested.connect(self.worker.fetch_all_books)
        self.close_requested.connect(self.worker.close_database)

        self.worker.initialized.connect(self._on_initialized)
        self.worker.change_finished.connect(self._on_mutation_finished)
        self.worker.change_error.connect(self._on_mutation_error)
        self.worker.books_ready.connect(self._on_books_ready)
        self.worker.query_error.connect(self._on_query_error)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.app.quit)

        self.errors: list[str] = []
        self.stage = "starting"
        self.thread.start()
        self.initialize_requested.emit()

    def _on_initialized(self) -> None:
        self.stage = "update-name"
        self.update_requested.emit(101, "book_name", "改名后的书")

    def _on_mutation_finished(self, operation: str, book_id: int) -> None:
        if self.stage == "update-name":
            if operation != "update" or book_id != 101:
                self._fail("收到不符合预期的更新成功信号。")
                return
            self.stage = "check-update"
            self.fetch_all_requested.emit()
            return

        if self.stage == "update-author":
            if operation != "update" or book_id != 101:
                self._fail("收到不符合预期的第二次更新成功信号。")
                return
            self.stage = "check-author"
            self.fetch_all_requested.emit()
            return

        if self.stage == "check-delete":
            if operation != "delete" or book_id != 101:
                self._fail("收到不符合预期的删除成功信号。")
                return
            self.stage = "check-empty"
            self.fetch_all_requested.emit()
            return

        self._fail(f"阶段 {self.stage} 收到意外修改成功信号。")

    def _on_books_ready(self, books) -> None:
        if self.stage == "check-update":
            if len(books) != 2:
                self._fail("更新检查应仍有两本书。")
                return

            updated = next(
                (book for book in books if book.book_id == 101),
                None,
            )
            if updated is None or updated.book_name != "改名后的书":
                self._fail("更新后的书名未写入数据库。")
                return

            self.stage = "reject-duplicate"
            self.update_requested.emit(101, "book_name", "已存在的书")
            return

        if self.stage == "check-rollback":
            updated = next(
                (book for book in books if book.book_id == 101),
                None,
            )
            if updated is None or updated.book_name != "改名后的书":
                self._fail("重名更新失败后，原书名没有正确恢复。")
                return

            self.stage = "update-author"
            self.update_requested.emit(101, "author", "新作者")
            return

        if self.stage == "check-author":
            updated = next(
                (book for book in books if book.book_id == 101),
                None,
            )
            if updated is None or updated.author != "新作者":
                self._fail("更新后的作者没有写入数据库。")
                return

            self.stage = "check-delete"
            self.delete_requested.emit(101)
            return

        if self.stage == "check-empty":
            if len(books) != 1 or books[0].book_id != 202:
                self._fail("删除后应只保留未选中的那本书。")
                return
            self.stage = "closing"
            self.close_requested.emit()
            return

        self._fail(f"阶段 {self.stage} 收到意外查询结果。")

    def _on_mutation_error(self, message: str) -> None:
        if self.stage == "reject-duplicate":
            if "已经存在" not in message:
                self._fail(f"重名更新返回了意外错误：{message}")
                return
            self.stage = "check-rollback"
            self.fetch_all_requested.emit()
            return

        self._fail(f"Worker 修改失败：{message}")

    def _on_query_error(self, message: str) -> None:
        self._fail(f"Worker 查询失败：{message}")

    def _fail(self, message: str) -> None:
        self.errors.append(message)
        if self.stage != "closing":
            self.stage = "closing"
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
        connection.executemany(
            "INSERT INTO books_information VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    101, "原书名", None, None, "旧作者", None, None, None,
                    None, None, None, ".pdf", None, 120, None, None, None,
                    None, None,
                ),
                (
                    202, "已存在的书", None, None, "另一作者", None, None,
                    None, None, None, None, ".txt", None, 80, None, None,
                    None, None, None,
                ),
            ],
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
        check = Phase3Check(app, db_name)
        app.exec()

        if check.errors:
            raise AssertionError("\n".join(check.errors))
        if check.stage != "closing":
            raise AssertionError(
                f"验证流程未完成，最后阶段：{check.stage}"
            )

        # 用独立连接确认删除只影响目标记录。
        connection = sqlite3.connect(db_name)
        try:
            row = connection.execute(
                "SELECT book_name FROM books_information WHERE book_id = ?",
                (202,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or row[0] != "已存在的书":
            raise AssertionError("删除或更新操作错误地修改了其他书籍。")

        print("阶段 3 DatabaseWorker 更新和删除检查全部通过")
    finally:
        if os.path.exists(db_name):
            os.remove(db_name)


if __name__ == "__main__":
    main()
```

## 11. 实施和验证顺序

从项目根目录运行：

```powershell
uv run python -m compileall src/ausarum tests
python verify_phase1.py
python verify_phase2.py
python verify_phase3.py
```

预期三个验证脚本分别输出阶段 1、阶段 2 和阶段 3 检查通过。随后运行 `uv run ausarum` 手工检查：

1. 在表格视图编辑书名、作者、页数、阅读时长等字段，完成后数据库值仍存在；切换封面/表格视图后两个 Model 显示相同数据。
2. 尝试把书名改为另一本书的书名，确认出现明确错误，原书名恢复，其他记录不受影响。
3. 清空书名、输入非法页数或非法阅读时长，确认收到错误并且 Model 恢复数据库原值。
4. 尝试编辑书籍 ID，确认该列不可编辑。
5. 选中一行并点击“删除选中书籍”，确认二次确认后只删除书库记录，本地文件仍存在。
6. 未选中行时点击删除，确认显示提示，不提交数据库操作。
7. 在删除或更新后切换两种视图，确认已显示最新数据库内容；数据库为空时两个 Model 都清空。
8. 扫描新书和重复书籍，确认阶段 2 的批量写入、覆盖/重命名/跳过和回滚行为不变。
9. 关闭应用，确认无 `QThread: Destroyed while thread is still running` 警告。

## 12. 常见问题

| 现象 | 检查点 |
|---|---|
| 编辑后短暂显示新值，随后恢复旧值 | 查看 `change_error` 内容；失败后重新查询是预期行为，用于撤销未保存的 Model 值。 |
| 编辑成功但封面视图未更新 | 确认 `_on_mutation_finished()` 调用 `request_all.emit()`，且 `_on_books_loaded()` 同时更新两个 Model。 |
| 点击删除时报没有选中行 | 当前删除按钮仅使用表格视图的选中行；切换到表格模式并选中整行。 |
| SQL 报字段名错误 | 检查 `BOOK_COLUMNS` 与 SQLite 建表字段顺序、名称是否一致；不要允许调用方自行传入 SQL 列名。 |
| 修改一个字段时其他字段被覆盖 | 更新槽必须只执行单字段 `UPDATE`，不能把整行缓存作为待更新记录写回。 |
| 删除多条记录或更新失败 | 检查数据库中是否已有重复 `book_id`；当前实现发现 ID 不唯一时会拒绝操作以保护数据。 |
| 写操作与扫描提示混乱 | 确认 CRUD 使用 `change_finished` / `change_error`，扫描仍使用 `write_finished` / `write_error`。 |

## 阶段 3 完成标准

- `MainWindow`、扫描及本阶段新增的编辑/删除流程不再使用 `DatabaseManager`；`pdf.py` 的既有路径查询仍待阶段 5 迁移。
- 主界面查询、扫描批量写入、字段更新、单条删除和数据库关闭均通过同一个 `DatabaseWorker` 处理；`pdf.py` 的既有路径查询是待阶段 5 迁移的遗留例外。
- `book_id` 不可通过表格编辑；更新和删除均使用 `book_id`，不依赖可能被修改的书名定位记录。
- 更新书名唯一性检查、字段更新和删除均在 Worker 的事务内执行；失败时明确发出错误，数据库不留下部分修改。
- 修改结果回到主线程后刷新两个 Model；错误结果也重新查询以恢复真实数据。
- 阶段 1、阶段 2、阶段 3 自动验证和上述手工验证全部通过。

完成后才进入总架构指南中的阶段 4（分页、字段裁剪、索引和缓存优化）；不要把性能优化和本阶段的数据库写入迁移混在同一个改动里。
