from __future__ import annotations

import typing

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
)

from .book_record import (
    BOOK_COLUMN_TITLES,
    BookRecord,
)

BOOK_ID_INDEX = 0
BOOK_NAME_INDEX = 1
AUTHOR_INDEX = 4
BOOK_TYPE_INDEX = 11


class BookTableModel(QAbstractTableModel):
    """表格视图的数据模型"""

    # 自定义 data role
    BookIdRole = Qt.ItemDataRole.UserRole + 1       # book_id
    BookNameRole = Qt.ItemDataRole.UserRole + 2     # book_name
    AuthorRole = Qt.ItemDataRole.UserRole + 3       # author
    BookTypeRole = Qt.ItemDataRole.UserRole + 4     # book_type
    CoverPathRole = Qt.ItemDataRole.UserRole + 5    # 封面图片路径（预留）

    def __init__(self, column_titles=None, parent=None):
        super().__init__(parent)
        self._table_books: list[list[object]] = []
        self._column_titles = column_titles or list(BOOK_COLUMN_TITLES)

    def set_data(self, books: list[BookRecord]):
        """把BookRecord列表转换成表格内部可编辑的二维列表。"""
        self.beginResetModel()
        self._table_books = [
            list(book.to_row())
            for book in books
        ]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        """返回表格的行数，即书籍的数量"""
        return len(self._table_books)

    def columnCount(self, parent=QModelIndex()):
        """返回表格的列数"""
        return len(self._column_titles)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """返回指定单元格的数据"""
        if not index.isValid():
            return None

        row = index.row()
        column = index.column()
        if not (0 <= row < self.rowCount() and 0 <= column < self.columnCount()):
            return None

        if role in (
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.EditRole,
        ):
            return self._table_books[row][column]
        elif role == self.BookIdRole:
            return self._table_books[row][BOOK_ID_INDEX]
        elif role == self.BookNameRole:
            return self._table_books[row][BOOK_NAME_INDEX]
        elif role == self.AuthorRole:
            return self._table_books[row][AUTHOR_INDEX]
        elif role == self.BookTypeRole:
            return self._table_books[row][BOOK_TYPE_INDEX]
        elif role == self.CoverPathRole:
            return None

        return None

    def headerData(self, section, orientation, /, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                if 0 <= section < len(self._column_titles):
                    return self._column_titles[section]
                return None
            else:
                # 垂直方向时，返回行号（从1开始）
                return str(section + 1)
        return None

    def setData(
            self,
            index,
            value: typing.Any,
            role: int = Qt.ItemDataRole.EditRole
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
        # 告知视图：从index开始到index结束的矩形区域（可以只是一个单元格）的数据已改变
        # DisplayRole：用于显示的内容发生变化；EditRole：用于编辑的内容发生变化。
        self.dataChanged.emit(
            index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole]
        )
        return True

    def flags(self, index):
        """返回指定单元格的标志，表示该单元格是否可编辑、可选中等"""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flag = super().flags(index)
        if index.column() != BOOK_ID_INDEX:
            flag |= Qt.ItemFlag.ItemIsEditable
        return flag
