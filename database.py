from __future__ import annotations

import math
import random
import sqlite3
from typing import Union
from PySide6.QtCore import (
    QObject,
    QThread,
    Signal,
    Slot,
)

from book_record import (
    BOOK_COLUMNS,
    BOOK_COLUMN_TITLES,
    BookRecord,
    rows_to_records,
)


class DatabaseManager(QObject):
    """负责在主线程中与SQLite数据库交互，提供书籍信息的增删改查功能"""
    # 信号必须在类层级定义，不能在__init__中定义
    duplicate_book = Signal(str)  # 发送重复书籍名称
    add_book_result = Signal(bool, str)  # 返回处理结果（成功状态，消息）

    def __init__(self, db_name='books_information.db'):
        super().__init__()
        self.db_name = db_name  # 数据库名称
        self.connection = sqlite3.connect(self.db_name)  # 连接到数据库
        self.cursor = self.connection.cursor()  # 创建一个Cursor（游标）对象，用于执行SQL语句
        self.pending_book = None  # 临时存储待处理的书籍数据

    def create_table(self):
        self.cursor.execute("""
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
        """)
        self.connection.commit()

    def add_column(self, table_name, column_name, column_type='TEXT'):
        """
        添加新的列
        :param table_name: 表名
        :param column_name: 列名
        :param column_type: 列类型
        """
        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        self.cursor.execute(sql)
        self.connection.commit()

    def _gen_new_name(self, old_name):
        """
        生成唯一的书籍名称，如果书籍名称与已存在书籍重复，自动为其添加数字后缀进行重命名。
        :param old_name: 初始书籍名称
        :return: 生成的唯一书籍名称
        """
        suffix = 1
        while True:
            sql = "SELECT COUNT(*) FROM books_information WHERE book_name = ?"
            new_name = f"{old_name}({suffix})" if suffix > 1 else old_name  # TODO 待修复：重命名逻辑存在问题，无法自动添加后缀
            self.cursor.execute(sql, (new_name,))

            count = self.cursor.fetchone()[0]
            if count == 0:
                return new_name

            suffix += 1

    def add_book(self, **kwargs):  # 以关键字参数的形式接收书籍信息
        """
        添加书籍信息，如果书籍名称与已存在书籍重复，自动跳过该书籍的录入步骤。
        :param kwargs: 书籍信息
        """
        self.pending_book = kwargs  # 将待处理的书籍数据存储在类的属性中
        old_name = kwargs['book_name']
        sql_pass = 'SELECT * FROM books_information WHERE book_name=?'
        self.cursor.execute(sql_pass, (old_name,))
        # 如果数据库中已经存在同名书籍
        if self.cursor.fetchall():
            self.duplicate_book.emit(old_name)  # 发送信号，通知主窗口书籍已存在，请选择是否继续添加
        else:
            self._insert_book(kwargs)  # 执行插入操作

    def handle_duplicate_book(self, choice: str, original_name: str):
        """
        处理用户选择的重复书籍操作
        :param choice: 'skip'/'overwrite'/'rename'
        :param original_name: 原始书名
        """
        # 发送信号，通知主窗口已跳过重复书籍
        if choice == 'skip':
            self.add_book_result.emit(False, "已跳过重复书籍")

        # 执行覆盖操作
        elif choice == 'overwrite':
            self.delete_book(original_name)
            self._insert_book(self.pending_book)

        # 执行重命名操作
        elif choice == 'rename':
            new_name = self._gen_new_name(original_name)
            self.pending_book['book_name'] = new_name
            self._insert_book(self.pending_book)

    def _insert_book(self, book_data):
        """实际插入数据的内部方法"""
        try:
            columns = ', '.join(book_data.keys())
            placeholders = ', '.join(['?'] * len(book_data))
            sql = f"INSERT INTO books_information ({columns}) VALUES ({placeholders})"
            self.cursor.execute(sql, tuple(book_data.values()))
            self.set_book_id(book_data['book_name'])
            self.connection.commit()
        except Exception as e:
            self.add_book_result.emit(False, f"书籍添加失败！错误信息：{e}")
            self.add_book_result.emit(True, f"书籍添加成功！")

    def set_book_id(self, book_name):
        """
        为添加到数据库中的书籍设置唯一的书籍ID（由随机生成的10位整数组成）
        :param book_name: 需要设置ID的书籍名称
        :return:
        """
        # 查询数据库中是否已经存在该书籍对应的ID
        sql = "SELECT book_id FROM books_information WHERE book_name=?"
        self.cursor.execute(sql, (book_name,))
        result = self.cursor.fetchone()
        # 如果存在匹配的记录，将查询结果的第一个元素赋值给变量result；否则，将变量result设为None
        result = result[0] if result else None

        # 如果不存在，则生成新的ID
        if result is None:
            while True:
                book_id = random.randint(1000000000, 9999999999)
                # 查询数据库中是否已经存在相同ID，确保生成的ID不与数据库中已有的ID重复
                check_sql = "SELECT 1 FROM books_information WHERE book_id=?"
                self.cursor.execute(check_sql, (book_id,))
                existing_id = self.cursor.fetchone()

                # 如果不存在重复ID，则使用新生成的ID，退出循环
                if existing_id is None:
                    break

            # 更新书籍的ID
            update_sql = "UPDATE books_information SET book_id=? WHERE book_name=?"
            self.cursor.execute(update_sql, (book_id, book_name))
            self.connection.commit()

    def delete_book(self, book_name, book_id=None):
        """
        删除书籍信息
        :param book_id：书籍ID
        :param book_name: 书籍名称
        """
        sql = "DELETE FROM books_information WHERE book_name=?"
        self.cursor.execute(sql, (book_name,))
        self.connection.commit()

    def _delete_all_books(self):
        """
        删除所有书籍信息
        """
        sql = "DELETE FROM books_information"
        self.cursor.execute(sql)
        self.connection.commit()

    def update_book(self, book_name, **kwargs):
        """
        更新书籍信息(增加、修改或删除相关信息)
        :param book_name: 书籍名称
        :param kwargs: 更新的书籍信息
        """
        columns = ', '.join([f'{key}=?' for key in kwargs.keys()])  # 以逗号分隔的列名
        values = tuple(kwargs.values()) + (book_name,)

        sql = f"UPDATE books_information SET {columns} WHERE book_name=?"
        self.cursor.execute(sql, values)
        self.connection.commit()
        # TODO: 用户修改本地文件名称时，自动更新数据库中的书籍名称

    def check_book_info(self, book_name) -> str:
        """
        检查书籍是否存在于数据库中
        :param book_name: 书籍名称
        :return: 如果书籍存在，返回True；否则，返回提示
        """
        sql = "SELECT * FROM books_information WHERE book_name=?"
        self.cursor.execute(sql, (book_name,))
        if not None:
            return self.cursor.fetchone()
        else:
            return f"未找到名为{book_name}的书籍"

    def get_book(self, book_name, columns=None) -> Union[list, str]:
        """
        获取指定书籍的部分信息（默认返回所有信息）
        :param book_name：书籍名称
        :param columns：指定要获取的列名列表，默认为None，表示获取书籍的所有信息
        :return: 书籍信息列表，元素只有一个元组，元组中的元素为书籍的各项信息，
        如 [(4071916953, '学术规范导论', 'F:/Books', '2024年04月18日 23:49:50', None, ..., None)]
        """
        try:
            sql_check = f"SELECT * FROM books_information WHERE book_name=?"
            self.cursor.execute(sql_check, (book_name,))
        except sqlite3.OperationalError:
            return f'没有查询到名为"{book_name}"的书籍，请重试。'
        else:
            # 如果指定了要获取的列名(即columns不为空)，则根据指定的列名查询书籍信息
            if columns is not None:
                sql = f"SELECT {columns} FROM books_information WHERE book_name=?"
                self.cursor.execute(sql, (book_name,))
                books = self.cursor.fetchall()
                return books

            # 如果没有指定要获取的列名，则查询书籍的所有信息
            else:
                sql = f"SELECT * FROM books_information WHERE book_name=?"
                self.cursor.execute(sql, (book_name,))
                books = self.cursor.fetchall()
                return books

    def close(self):
        self.cursor.close()
        self.connection.close()


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

    page_ready = Signal(int, list, int)     # 成功信号：携带请求编号、当前页list[BookRecord]和筛选后的总记录数
    page_error = Signal(int, str)           # 错误信号：带请求编号和错误信息

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

    @Slot(int, str, str, int, int, str)
    def fetch_page(
            self,
            request_id: int,
            filter_name: str,
            filter_value: str,
            offset: int,
            limit: int,
            view_type: str,
    ) -> None:
        """查询一页书籍；filter_name 为空字符串表示不筛选。"""
        if self.connection is None:
            self.page_error.emit(request_id, "数据库连接未初始化，无法查询。")
            return

        if not isinstance(offset, int) or offset <0:
            self.page_error.emit(request_id, "分页偏移量必须是非负整数。")
            return
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            self.page_error.emit(request_id, "分页大小必须是 1~200 的整数。")
            return

        filter_columns = {
            "type": "book_type",
            "status": "read_status",
            "author": "author",
        }
        if filter_name and filter_name not in filter_columns:
            self.page_error.emit(request_id, f"不支持的筛选字段：{filter_name}")
            return

        if view_type == "list":
            columns = BOOK_COLUMNS
        elif view_type == "cover":
            columns = (
                "book_id",
                "book_name",
                "author",
                "book_type",
                "book_path",
            )
        else:
            self.page_error.emit(request_id, f"不支持的视图类型：{view_type}")
            return

        where_sql = ""
        filter_params: tuple = ()
        if filter_name:
            column = filter_columns[filter_name]
            where_sql = f"WHERE {column} = ?"
            filter_params = (filter_value,)

        columns_sql = ", ".join(columns)
        count_sql = f"SELECT COUNT(*) FROM books_information{where_sql}"
        page_sql = (
            f"SELECT {columns_sql} FROM books_information{where_sql} "
            "ORDER BY book_id, rowid LIMIT ? OFFSET ?"
        )

        try:
            total_count = self.connection.execute(
                count_sql,
                filter_params,
            ).fetchone()[0]
            cursor = self.connection.execute(
                page_sql,
                (*filter_params, limit, offset),
            )
            column_names = [description[0] for description in cursor.description]
            books = [
                BookRecord.from_mapping(dict(zip(column_names, row)))
                for row in cursor.fetchall()
            ]
        except sqlite3.Error as error:
            self.page_error.emit(request_id, f"分页查询失败：{error}")
            return
        except (TypeError, ValueError) as error:
            self.page_error.emit(request_id, f"分页结果转换失败：{error}")
            return

        self.page_ready.emit(request_id, books, total_count)

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
