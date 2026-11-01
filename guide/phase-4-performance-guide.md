# 阶段 4：数据库查询与视图性能优化指南

**适用项目：** Eternity-Library（三木书斋）  
**前置条件：** 阶段 0–3 的数据库读取、批量写入、更新和删除流程已经通过验证。  
**涉及文件：** `database.py`、`el_gui.py`、`table_model.py`、`cover_model.py`、`verify_phase4.py`

> 本文是实施指南，不要求一次完成所有优化。阶段 4 只处理查询、结果传输和界面刷新性能，不直接修改扫描、重复书籍处理、更新或删除的业务规则，也不新增数据库线程。

## 1. 阶段目标与范围

阶段 4 优化书籍数量增加后，查询和界面刷新可能变慢的问题：

- 查询支持分页，避免每次把整张书表全部传回主线程。
- 封面视图只查询绘制卡片所需字段。
- 为常用筛选字段添加适量索引。
- 明确缓存的键和失效时机，避免显示旧结果。
- 为异步查询增加请求编号，防止较早发出的查询结果覆盖较新的页面或筛选结果。

本阶段不改变扫描、重复书籍处理、更新和删除业务规则；不增加独立数据库线程；不在 Worker 中操作 Qt Model 或 Widget。大型目录扫描、封面图片生成和图片解码也不纳入本阶段。

## 2. 当前实现需要注意的边界

当前 `DatabaseWorker` 的查询方法使用 `SELECT *` 并一次性读取所有记录。查询虽然在工作线程中执行，但结果转换、信号传输、`BookTableModel.set_data()` 和 `CoverCardModel.set_books()` 仍在主线程完成；当记录数量较大时，这些操作也会影响界面响应。

另外，`MainWindow` 当前使用一个 15 秒缓存保存完整查询结果。分页后，缓存不能只按时间判断是否有效，还必须区分筛选条件、视图所需字段和页码。任何写入、更新、删除成功后，都必须使相关缓存失效。

阶段 3 的验证脚本只覆盖更新和删除的业务行为，不能替代阶段 4 对分页、请求过期和界面模型行为的验证。

## 3. 实施顺序

建议按以下顺序逐步实施，每一步完成后单独验证：

1. **加索引并测量。** 为实际使用的筛选列创建索引，确认数据库能够正常初始化。
2. **Worker 增加分页查询。** 查询总数与指定页的数据，并保持现有查询错误信号行为。
3. **封面查询裁剪字段。** 只读取卡片需要的信息；列表视图仍保留完整字段。
4. **界面增加翻页状态。** 在主线程维护当前筛选条件、页码和总数，收到结果后更新模型。
5. **处理过期响应与缓存。** 加入请求编号；实现分页或筛选缓存前，先正确处理失效逻辑。
6. **补充验证脚本和手工检查。** 覆盖空结果、边界页、筛选和连续请求。

不要一次性把分页、缓存和异步界面调整全部混在一个改动中，否则出现结果错误时不容易定位原因。

## 4. 数据库索引

在 `DatabaseWorker.initialize()` 建表之后创建索引。建议从实际支持且常用的筛选字段开始：

```sql
CREATE INDEX IF NOT EXISTS idx_books_book_type
ON books_information(book_type);

CREATE INDEX IF NOT EXISTS idx_books_read_status
ON books_information(read_status);

CREATE INDEX IF NOT EXISTS idx_books_author
ON books_information(author);
```

使用 `IF NOT EXISTS` 可以让应用重复启动时安全执行。索引不会改变查询结果，但会增加数据库文件体积，并让写入稍慢；不要为了“可能有用”给每一列都建索引。只有界面实际支持并常用的筛选字段才值得建立索引。

索引创建失败应通过数据库初始化错误信号明确报告，不能静默忽略。不要在每次查询时创建索引。

索引优化应以实际查询为依据。可以在开发数据库副本上用 SQLite 的 `EXPLAIN QUERY PLAN` 检查筛选查询是否使用预期索引；不应为了让查询计划显示索引而盲目增加索引。`ORDER BY book_id` 是否需要额外索引，需结合现有表结构和测量结果判断。

## 5. Worker 分页查询接口

本节代码按当前项目的 `DatabaseWorker`、`BookRecord` 和 `MainWindow` 编写。把信号和方法分别加到指定位置；不要直接调用已移动到工作线程的 Worker 方法，数据库请求必须通过 Qt 信号发送。

### 5.1 在 `DatabaseWorker` 中增加结果信号

在 `database.py` 的 `DatabaseWorker` 类信号声明区添加：

```python
page_ready = Signal(int, list, int)
page_error = Signal(int, str)
```

成功信号依次携带请求编号、当前页的 `list[BookRecord]` 和筛选后的总记录数；错误信号携带请求编号和错误信息。请求编号用于后续忽略过期响应。

### 5.2 添加分页查询方法

把下面的方法放在 `DatabaseWorker` 类中，例如紧接 `fetch_by_author()` 之后。项目已有 `sqlite3`、`BookRecord` 和 `BOOK_COLUMNS` 导入，无需增加导入。

```python
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

        if not isinstance(offset, int) or offset < 0:
            self.page_error.emit(request_id, "分页偏移量必须是非负整数。")
            return
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            self.page_error.emit(request_id, "每页条数必须在 1 到 200 之间。")
            return

        filter_columns = {
            "type": "book_type",
            "status": "read_status",
            "author": "author",
        }
        if filter_name and filter_name not in filter_columns:
            self.page_error.emit(request_id, "不支持的书籍筛选字段。")
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
            self.page_error.emit(request_id, "不支持的书籍视图类型。")
            return

        where_sql = ""
        filter_params = ()
        if filter_name:
            column = filter_columns[filter_name]
            where_sql = f" WHERE {column} = ?"
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
```

筛选列名只从 `filter_columns` 白名单中取得；筛选值、`LIMIT` 和 `OFFSET` 都通过 SQL 参数绑定。`rowid` 仅用于 `book_id` 相同时稳定排序，不会返回给界面。封面模式只查询卡片所需字段，`BookRecord.from_mapping()` 会让未查询的字段保留默认值。空结果是成功查询，会发送空列表和正确总数；查询错误则发送 `page_error`，不会伪装成空结果。

### 5.3 从 `MainWindow` 发起请求

在 `el_gui.py` 的 `MainWindow` 信号声明区增加请求信号：

```python
request_page = Signal(int, str, str, int, int, str)
```

在 `_setup_book_worker()` 中，和其他主线程到 Worker 的连接放在一起：

```python
self.request_page.connect(self._worker.fetch_page)
```

完成第 7、8 节的界面结果处理槽后，再添加下面两条响应连接；此时对应的槽方法尚未实现，不要提前粘贴这两行：

```python
self._worker.page_ready.connect(self._on_page_ready)
self._worker.page_error.connect(self._on_page_error)
```

下面示例展示无筛选查询封面视图第 1 页、每页 60 条的调用方式。`request_id = 1` 仅用于说明参数；实际界面必须按第 8 节为每次请求递增生成编号：

```python
request_id = 1
page_number = 1
page_size = 60
offset = (page_number - 1) * page_size
self.request_page.emit(
    request_id,
    "",
    "",
    offset,
    page_size,
    "cover",
)
```

按书籍类型筛选列表视图时，将筛选名和值分别设为 `"type"` 和实际类型值，例如 `"PDF"`；阅读状态使用 `"status"`，作者使用 `"author"`。页码从 1 开始，偏移量按 `(page_number - 1) * page_size` 计算。筛选条件变化时从第 1 页重新查询；总记录数为 0 时显示空状态，当前页超出总页数时应先调整页码再发请求。

## 6. 封面模式与列表模式字段裁剪

列表模式需要完整记录以支持表格显示和编辑；封面模式只需要卡片用到的字段。建议封面查询只取：

```sql
SELECT book_id, book_name, author, book_type, book_path
FROM books_information
ORDER BY book_id
LIMIT ? OFFSET ?;
```

数据库行转换必须使用明确的列名映射，不能把五列结果直接传给要求完整列数的 `BookRecord.from_row()`。可以用 `BookRecord.from_mapping()` 构造部分记录，其余字段保留默认值。列表视图仍查询完整字段，并保持列顺序与 `BOOK_COLUMNS` 一致。

封面模式分页和列表模式分页应分别定义每页大小。可先采用保守值，例如封面每页 60 条、列表每页 100 条，再通过实际体验调整；不要把“分页大小”散落在多个方法中。

分页后，`CoverCardModel` 和 `BookTableModel` 不能把当前页数据误认为整个数据库的全部数据。每次收到有效的当前页结果，只更新当前活动视图对应的模型；若产品设计需要同时保留两个视图的状态，则应分别保存各自页码和查询条件。

## 7. 界面翻页与模型刷新

`MainWindow` 在主线程维护当前查询状态，例如：

```text
当前视图类型
当前筛选字段和值
当前页码
每页条数
总记录数
当前请求编号
```

用户切换筛选条件或视图时，将页码重置为第一页。用户翻页时，根据当前筛选状态重新发出分页请求。翻页控件的可用状态根据总数计算：

```text
总页数 = ceil(total_count / page_size)
```

`total_count == 0` 时总页数显示为 0 或以项目现有 UI 约定显示为空状态；此时禁用上一页和下一页。非空结果时，页码范围必须是 `1..总页数`。

在当前代码中 `refresh_view()` 总是请求全部书籍，`_on_books_loaded()` 同时刷新两个 Model。分页改造后，应明确当前查询服务哪个视图；避免封面页查询结束后把不完整字段的数据写入表格模型。

空结果必须清空当前视图的 Model，不能保留上一页或之前筛选条件的数据。发生查询错误时，应显示错误并保留或恢复到项目明确规定的 UI 状态，不能把错误当成“暂无书籍”。

## 8. 处理异步过期结果

Worker 串行执行查询，但用户仍可能在前一个查询结果返回前切换筛选或翻页。主线程发出每个查询时递增 `request_id`；收到结果时，只在请求编号与当前请求一致时应用结果：

```text
收到结果的 request_id != 当前 request_id → 丢弃该结果
收到结果的 request_id == 当前 request_id → 更新模型和页码状态
```

过期结果可安全忽略，因为它不是数据库错误，而是已被后续用户操作取代的请求。错误处理也要检查请求编号，避免旧请求的错误弹窗打断新操作。

请求编号应由 `MainWindow` 维护并随成功结果和错误结果一起返回。不要尝试通过比较结果内容判断它是否过期，因为相同筛选条件可能连续查询多次，内容也可能在这期间发生变化。

## 9. 缓存策略

缓存不是分页的前置条件。建议先完成正确的分页查询和请求编号，再决定是否保留缓存。

如果保留缓存，缓存键至少应包含：

```text
视图类型、筛选字段、筛选值、页码、每页条数
```

写入、更新、删除成功后，应清除与书籍列表相关的缓存。筛选条件变化或分页大小变化时，也不能复用不匹配的缓存。缓存失效后重新查询数据库，不能用过期缓存填补查询错误。

当前 `_books_cache` 存储完整书籍列表且有 15 秒有效期。开始分页后，应删除或改造这一全量缓存逻辑，不要在原实现上简单增加分页变量而继续复用全量列表缓存。

缓存应只在确实减少重复查询且正确性可验证时启用。若目前主要问题是一次传输数据过多，先实现分页和字段裁剪，不必为了完成阶段而强行增加缓存。

## 10. 阶段 4 验证要求

新增 `verify_phase4.py`，使用临时数据库验证，不修改正式数据库。至少覆盖：

- 无筛选查询第一页、最后一页和超出范围的页码。
- 空表及无匹配筛选条件时返回空列表和 0 条总数。
- 类型、状态、作者筛选的总数与分页结果正确。
- 每页返回数量不超过 `limit`，排序稳定且没有跨页重复记录。
- 封面查询只返回所需字段，并能转换成 `BookRecord`。
- 列表查询仍返回完整记录，现有编辑和删除逻辑不受影响。
- `request_id` 不匹配的旧结果不会覆盖当前模型。
- 初始化可重复执行索引创建语句。
- 批量写入、更新和删除后，界面重新查询到最新结果。

手工检查时，准备超过两页的数据，切换视图、筛选和翻页；确认空结果会清空当前模型，快速连续操作不会闪回旧页面。

自动测试应使用固定数据和可预测的排序；对于请求过期逻辑，测试主线程处理结果时是否丢弃旧编号，不要依赖实际运行速度或 `sleep()` 来制造竞态。

## 11. 阶段完成标准

- Worker 查询支持参数化筛选、分页和总数统计。
- 封面模式只传输绘制所需字段；列表模式保留完整字段。
- 模型仅在主线程更新，旧请求结果不会覆盖新请求。
- 索引只覆盖实际筛选列，初始化可重复运行。
- 所有写操作完成后，相关查询结果或缓存正确失效。
- 空结果、查询错误和过期响应有明确且不同的处理方式。
- 阶段 0–3 的验证仍通过，阶段 4 自动和手工验证通过。

阶段 4 完成后再评估是否需要缓存或封面图片后台任务；不要为了预先优化而引入第二个数据库线程。
