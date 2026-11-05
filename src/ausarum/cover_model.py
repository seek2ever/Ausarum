# 数据层：只负责管理数据，不关心如何显示
from __future__ import annotations

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    Qt,
)

from .book_record import BookRecord


class CoverCardModel(QAbstractListModel):
    """书籍封面卡片的数据模型
    只存数据，不涉及任何 UI。View 通过 rowCount() 知道有多少条，
    通过 data() 获取每条的具体内容。
    """

    # 自定义 data role —— Qt.UserRole 以上是预留给应用层的
    BookIdRole = Qt.UserRole + 1  # book_id
    BookNameRole = Qt.UserRole + 2  # book_name
    AuthorRole = Qt.UserRole + 3  # author
    BookTypeRole = Qt.UserRole + 4  # book_type
    CoverPathRole = Qt.UserRole + 5  # 封面图片路径（预留）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._books = []  # list[dict]，每个 dict 是一本书的关键字段

    def rowCount(self, parent=QModelIndex()):
        """返回总条目数。View 靠这个值计算滚动条范围。"""
        return len(self._books)
    
    def data(self, index, role=Qt.DisplayRole):
        """
        View 请求第 index.test_row() 条数据的 role 角色的值。View 会在需要绘制某个条目时调用这个方法。
        例如：View 说"给我第 42 个条目的 DisplayRole"，就返回书名字符串。
        """
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._books):
            return None

        book = self._books[row]

        if role == Qt.DisplayRole:
            return book.get("name", "")
        elif role == Qt.ToolTipRole:
            return book.get("name", "")
        elif role == self.BookIdRole:
            return book.get("id")
        elif role == self.BookNameRole:
            return book.get("name", "")
        elif role == self.AuthorRole:
            return book.get("author", "")
        elif role == self.BookTypeRole:
            return book.get("type", "")
        elif role == self.CoverPathRole:
            return book.get("cover_path")
        return None

    def set_books(self, books: list[BookRecord]):
        """
        批量替换全部数据。Model位于主线程，只有主线程收到查询结果后才能调用此方法。
        """
        self.beginResetModel()
        self._books = [
            {
                "id": book.book_id,
                "name": book.book_name or "未知书名",
                "path": book.book_path,
                "author": book.author or "未知作者",
                "type": book.book_type or "未知类型",
                "cover_path": None,
            }
            for book in books
        ]
        self.endResetModel()

    def get_book(self, row):
        """通过行号获取原始 dict，供外部使用（如点击卡片打开详情）"""
        if 0 <= row < len(self._books):
            return self._books[row]
        return None

    def clear(self):
        """清空全部数据"""
        self.beginResetModel()
        self._books.clear()
        self.endResetModel()
