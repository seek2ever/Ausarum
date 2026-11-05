from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from ausarum.book_record import BOOK_COLUMNS
from ausarum.database import DatabaseWorker


class WorkerCheck(QObject):
    initialize_requested = Signal()
    fetch_all_requested = Signal()
    fetch_type_requested = Signal(str)
    fetch_status_requested = Signal(str)
    fetch_author_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, app: QCoreApplication, db_name: str):
        super().__init__()
        self.app = app
        self.thread = QThread()
        self.worker = DatabaseWorker(db_name)
        self.worker.moveToThread(self.thread)
        self.initialize_requested.connect(self.worker.initialize)
        self.fetch_all_requested.connect(self.worker.fetch_all_books)
        self.fetch_type_requested.connect(self.worker.fetch_by_type)
        self.fetch_status_requested.connect(self.worker.fetch_by_status)
        self.fetch_author_requested.connect(self.worker.fetch_by_author)
        self.close_requested.connect(self.worker.close_database)
        self.worker.initialized.connect(self.on_initialized)
        self.worker.books_ready.connect(self.on_books_ready)
        self.worker.query_error.connect(self.on_error)
        self.worker.database_closed.connect(self.on_database_closed)
        self.thread.finished.connect(self.worker.deleteLater)
        self.step = 0
        self.expected = [
            ("all", 2),
            ("type", 1),
            ("status", 1),
            ("author", 1),
            ("empty", 0),
        ]
        self.thread.start()
        self.initialize_requested.emit()

    def on_initialized(self):
        self.fetch_all_requested.emit()

    def on_books_ready(self, books):
        name, expected_count = self.expected[self.step]
        actual_count = len(books)
        assert actual_count == expected_count, (
            f"{name} 查询应返回 {expected_count} 条，实际 {actual_count} 条"
        )
        if name == "all":
            assert len(books[0].to_row()) == len(BOOK_COLUMNS)
            self.fetch_type_requested.emit(".pdf")
        elif name == "type":
            self.fetch_status_requested.emit("正在阅读")
        elif name == "status":
            self.fetch_author_requested.emit("作者A")
        elif name == "author":
            self.fetch_type_requested.emit("不存在的类型")
        else:
            self.close_requested.emit()
        self.step += 1

    def on_error(self, message):
        raise RuntimeError(message)

    def on_database_closed(self):
        self.thread.quit()
        self.thread.finished.connect(self.app.quit)


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
        values = [
            (1, "甲", "a", "", "作者A", "", "", "", "", "",
             "正在阅读", ".pdf", None, None, "", None, "", "", ""),
            (2, "乙", "b", "", "作者B", "", "", "", "", "",
             "尚未阅读", ".txt", None, None, "", None, "", "", ""),
        ]
        connection.executemany(
            "INSERT INTO books_information VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    app = QCoreApplication(sys.argv)
    file_descriptor, db_name = tempfile.mkstemp(suffix=".db")
    os.close(file_descriptor)
    try:
        os.remove(db_name)
        create_database(db_name)
        check = WorkerCheck(app, db_name)
        exit_code = app.exec()
        assert check.step == len(check.expected), "Worker 验证没有完成"
        print("阶段 1 DatabaseWorker 检查全部通过")
        raise SystemExit(exit_code)
    finally:
        if os.path.exists(db_name):
            os.remove(db_name)


if __name__ == "__main__":
    main()
