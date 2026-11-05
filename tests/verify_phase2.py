from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from ausarum.book_record import BOOK_COLUMNS, BookRecord
from ausarum.database import DatabaseWorker


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
