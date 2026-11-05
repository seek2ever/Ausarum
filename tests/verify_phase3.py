from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from ausarum.book_record import BookRecord
from ausarum.database import DatabaseWorker


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

            updated: BookRecord | None = next(
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
            updated: BookRecord | None = next(
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
            updated: BookRecord | None = next(
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
