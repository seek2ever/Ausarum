from __future__ import annotations

from dataclasses import (
    dataclass,
    fields,
)
from typing import (
    Any,
    Iterable,
    Sequence,
)

# 必须与database.py中CREATE TABLE的字段顺序一致。
BOOK_COLUMNS: tuple[str, ...] = (
    "book_id",
    "book_name",
    "book_path",
    "add_time",
    "author",
    "nationality",
    "translator",
    "publisher",
    "publication_date",
    "level",
    "read_status",
    "book_type",
    "isbn",
    "pages",
    "read_progress",
    "read_time",
    "read_date",
    "read_link",
    "introduction",
)

BOOK_COLUMN_TITLES: tuple[str, ...] = (
    "书籍ID",
    "书籍名称",
    "书籍路径",
    "添加时间",
    "作者",
    "国籍",
    "译者",
    "出版社",
    "出版日期",
    "书籍等级",
    "阅读状态",
    "书籍类型",
    "ISBN",
    "页数",
    "阅读进度",
    "阅读时间",
    "阅读日期",
    "阅读链接",
    "简介",
)

if len(BOOK_COLUMNS) != len(BOOK_COLUMN_TITLES):
    raise RuntimeError("书籍字段数量与中文表头数量不一致")


@dataclass
class BookRecord:
    """表示数据库中的一条书籍记录。"""
    book_id: int | None = None
    book_name: str = ""
    book_path: str | None = None
    add_time: str | None = None
    author: str | None = None
    nationality: str | None = None
    translator: str | None = None
    publisher: str | None = None
    publication_date: str | None = None
    level: str | None = None
    read_status: str | None = None
    book_type: str | None = None
    isbn: int | None = None
    pages: int | None = None
    read_progress: str | None = None
    read_time: float | None = None
    read_date: str | None = None
    read_link: str | None = None
    introduction: str | None = None

    @classmethod
    def from_row(cls, row: Sequence[Any]) -> "BookRecord":
        """把SQLite返回的一行数据转换成BookRecord。"""
        if len(row) != len(BOOK_COLUMNS):
            raise ValueError(
                f"书籍记录应有{len(BOOK_COLUMNS)}字段，实际收到{len(row)}个"
            )
        # 使用*row解包元组，按字段顺序传给BookRecord的构造函数
        return cls(*row)

    @classmethod
    def from_mapping(cls, values: dict[str, Any]) -> "BookRecord":
        """从字段字典创建BookRecord，适合扫描结果转成统一对象。"""
        unknown_keys = set(values) - set(BOOK_COLUMNS)
        if unknown_keys:
            names = ", ".join(sorted(unknown_keys))
            raise ValueError(f"发现未知书籍字段：{names}")

        allowed_values = {
            field.name: values[field.name]
            for field in fields(cls)
            if field.name in values
        }
        # 字典解包
        return cls(**allowed_values)

    def to_row(self) -> tuple[Any, ...]:
        """按数据库字段顺序转换为可用于INSERT的元组。"""
        return tuple(getattr(self, column) for column in BOOK_COLUMNS)

    def to_mapping(self) -> dict[str, Any]:
        """转换为字段名到字段值的字典。"""
        return {
            column: getattr(self, column)
            for column in BOOK_COLUMNS
        }


def rows_to_records(rows: Iterable[Sequence[Any]]) -> list[BookRecord]:
    """逐行调用from_row把多行SQLite查询结果转换成BookRecord列表。"""
    return [BookRecord.from_row(row) for row in rows]


if __name__ == "__main__":
    # 测试BookRecord的to_row和from_row方法
    record = BookRecord(
        book_id=1,
        book_name="测试书籍",
        author="作者A",
        book_type="小说",
    )
    test_row = record.to_row()
    print("Row:", test_row)

    new_record = BookRecord.from_row(test_row)
    print("New Record:", new_record)
