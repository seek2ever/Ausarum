import sqlite3

from ausarum.book_record import (
    BOOK_COLUMNS,
    BOOK_COLUMN_TITLES,
    BookRecord,
    rows_to_records,
)


DB_NAME = "books_information.db"


def main() -> None:
    print("1. 检查字段定义")
    print(f"字段数量：{len(BOOK_COLUMNS)}")
    print(f"表头数量：{len(BOOK_COLUMN_TITLES)}")
    assert len(BOOK_COLUMNS) == 19
    assert len(BOOK_COLUMNS) == len(BOOK_COLUMN_TITLES)

    print("2. 读取数据库实际字段")
    connection = sqlite3.connect(DB_NAME)
    try:
        rows = connection.execute(
            "SELECT * FROM books_information ORDER BY book_id"
        ).fetchall()
    finally:
        connection.close()

    print(f"数据库记录数量：{len(rows)}")
    if rows:
        assert len(rows[0]) == len(BOOK_COLUMNS)
        records = rows_to_records(rows)
        first = records[0]
        assert isinstance(first, BookRecord)
        print(f"第一本书：{first.book_name}")
        print(f"作者：{first.author or '未填写'}")
        print(f"类型：{first.book_type or '未填写'}")
        assert len(first.to_row()) == len(BOOK_COLUMNS)
    else:
        print("数据库目前没有书籍记录，空结果检查通过")

    print("3. 检查字典转换")
    demo = BookRecord.from_mapping({
        "book_name": "阶段0测试书",
        "author": "测试作者",
        "book_type": "测试类型",
    })
    assert demo.book_name == "阶段0测试书"
    assert demo.to_mapping()["author"] == "测试作者"
    print("字典转换检查通过")

    print("阶段 0 数据结构检查全部通过")


if __name__ == "__main__":
    main()
