from __future__ import annotations

import math
import random
import sqlite3

from PySide6.QtCore import (
    QObject,
    QThread,
    Signal,
    Slot,
)

from .book_record import (
    BOOK_COLUMNS,
    BOOK_COLUMN_TITLES,
    BookRecord,
    rows_to_records,
)


class DatabaseWorker(QObject):
    """在专用QThread中串行执行所有数据库读取操作。"""
    initialized = Signal()  # 数据库初始化完成信号
    books_ready = Signal(list)
    query_error = Signal(str)
    database_closed = Signal()
    duplicates_ready = Signal(list)
    write_finished = Signal(int, int, int)  # 新增数、跳过数、覆盖数
    write_error = Signal(str)
    change_finished = Signal(str, object)  # 操作类型（update/delete）、book_id
    change_error = Signal(str)

    def __init__(self, db_name='books_information.db'):
        super().__init__()
        self.db_name = db_name
        self.connection = None

    @Slot()
    def initialize(self):
        """由Worker所在线程调用，创建该线程专属的连接。"""
        if self.connection is not None:
            return

        try:
            self.connection = sqlite3.connect(self.db_name)
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS books_information (
                    book_id INTEGER,
                    book_name TEXT NOT NULL,
                    book_path TEXT,
                    add_time TEXT,
                    author TEXT,
                    nationality TEXT,
                    translator TEXT,
                    publisher TEXT,
                    publication_date TEXT,
                    level TEXT,
                    read_status TEXT,
                    book_type TEXT,
                    isbn INTEGER,
                    pages INTEGER,
                    read_progress TEXT,
                    read_time REAL,
                    read_date TEXT,
                    read_link TEXT,
                    introduction TEXT
                )
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_book_type
                ON books_information(book_type)
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_read_status
                ON books_information(read_status)
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_author
                ON books_information(author)
                """
            )
            self.connection.commit()
        except sqlite3.Error as e:
            self._close_connection()
            self.query_error.emit(f"数据库初始化失败：{e}")
            return
        self.initialized.emit()

    def _close_connection(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _select_all(self, sql: str, params: tuple = ()) -> list[BookRecord] | None:
        """执行一次参数化查询，并把结果转换成阶段0的数据结果（BookRecord列表）"""
        if self.connection is None:
            self.query_error.emit("数据库连接未初始化，无法查询。")
            return None
        try:
            rows = self.connection.execute(sql, params).fetchall()
            return rows_to_records(rows)
        except sqlite3.Error as e:
            self.query_error.emit(f"查询失败：{e}")
        except (TypeError, ValueError) as error:
            self.query_error.emit(f"数据转换失败：{error}")
        return None

    def _select_one(self, sql: str, params: tuple = ()) -> BookRecord | None:
        """执行一次单行查询，返回 BookRecord 或 None。"""
        if self.connection is None:
            self.query_error.emit("数据库连接未初始化，无法查询。")
            return None
        try:
            row = self.connection.execute(sql, params).fetchone()
        except sqlite3.Error as e:
            self.query_error.emit(f"查询失败：{e}")
            return None
        if row is None:
            return None
        try:
            return BookRecord.from_row(row)
        except ValueError as error:
            self.query_error.emit(f"数据转换失败：{error}")
            return None

    @Slot()
    def fetch_all_books(self) -> None:
        """查询全部书籍，空数据库也会发送books_ready([])。"""
        books = self._select_all(
            "SELECT * FROM books_information ORDER BY book_id"
        )
        if books is not None:
            self.books_ready.emit(books)

    @Slot(str)
    def fetch_by_type(self, book_type: str) -> None:
        """按书籍类型精确筛选。"""
        books = self._select_all(
            """
            SELECT * FROM books_information WHERE book_type=? 
            ORDER BY book_id
            """,
            (book_type,),
        )
        if books is not None:
            self.books_ready.emit(books)

    @Slot(str)
    def fetch_by_status(self, read_status: str) -> None:
        """按阅读状态精确筛选。"""
        books = self._select_all(
            """
            SELECT * FROM books_information
            WHERE read_status = ?
            ORDER BY book_id
            """,
            (read_status,),
        )
        if books is not None:
            self.books_ready.emit(books)

    @Slot(str)
    def fetch_by_author(self, author: str) -> None:
        """按作者精确筛选。"""
        books = self._select_all(
            """
            SELECT * FROM books_information
            WHERE author = ?
            ORDER BY book_id
            """,
            (author,),
        )
        if books is not None:
            self.books_ready.emit(books)

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
                raise TypeError("待入库数据必须是list[BookRecord]。")

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
    def _next_unique_book_name(book_name: str, occupied_names: set[str]) -> str:
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
        """在单个SQLite事务中处理整批书籍。"""
        if self.connection is None:
            self.write_error.emit("数据库连接未初始化，无法写入书籍。")
            return

        try:
            if not isinstance(books, list):
                raise TypeError("待入库数据必须是list[BookRecord]。")
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

            # 先取得SQLite写锁，再检查重复项，避免其他进程在检查与插入之间
            # 写入同名记录，connection上下文负责成功提交或异常回滚。
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

    @Slot(int, str, object)
    def update_book_field(
        self,
        book_id: int,
        field_name: str,
        value: int | float | str | None,
    ) -> None:
        """按稳定ID更新一个字段；禁止更改 book_id。"""
        if self.connection is None:
            self.change_error.emit("数据库连接未初始化，无法更新书籍。")
            return

        try:
            if not isinstance(book_id, int) or isinstance(book_id, bool):
                raise ValueError("书籍ID必须是整数。")
            if book_id <= 0:
                raise ValueError("书籍ID无效。")

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
        """按唯一的book_id删除一条记录。"""
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

    @Slot()
    def close_database(self):
        """关闭数据库连接"""
        self._close_connection()
        self.database_closed.emit()
        QThread.currentThread().quit()


if __name__ == '__main__':
    worker = DatabaseWorker()
    worker.initialize()
    result = worker._select_one(
        "SELECT * FROM books_information WHERE book_name=?",
        ("test(1)",)
    )
    print(result.book_id)
