# 阶段 5：清理旧数据库接口与统一调用链指南

**适用项目：** Ausarum（三木书斋）  
**前置条件：** 阶段 0–3 的数据结构、查询、批量写入、更新和删除流程已经通过验证。  
**当前性能策略：** 暂不实施分页，保留现有全量查询和封面视图滚动模式。  
**涉及文件：** `src/ausarum/database.py`、`src/ausarum/app.py`、`src/ausarum/pdf.py`、`src/ausarum/book_record.py`、`tests/` 阶段验证脚本

> 本阶段的目标是完成数据库访问架构迁移后的收尾工作：确认所有调用方都使用统一的
> `DatabaseWorker`，再删除不再需要的主线程数据库连接和旧接口。
>
> 阶段 5 不负责分页、封面图片后台生成、搜索功能或新的数据库线程。性能优化可以在后续阶段单独设计。

---

## 1. 阶段目标

阶段 1–3 已经把主界面的读取、扫描批量写入、更新和删除逐步迁移到
`DatabaseWorker`。但是旧的 `DatabaseManager` 仍然存在，`pdf.py` 还会直接创建
它并在主线程查询数据库：

```python
from database import DatabaseManager

path = DatabaseManager()
file_path = path.get_book(book_name, columns="book_path")
```

这会造成以下问题：

- 同一个数据库同时存在旧的主线程连接和新的 Worker 连接；
- `pdf.py` 的查询绕过了统一的线程边界；
- 旧类的连接和游标关闭责任不清晰；
- 后续修改数据库结构时必须同时维护两套访问接口；
- 很难确认某个数据库操作是否会阻塞主线程。

本阶段完成后，数据库访问应遵循一条规则：

> 所有 SQLite 连接、游标和 SQL 查询都由 `DatabaseWorker` 在数据库线程中管理；
> 主线程只能通过 Qt 信号发送请求，并通过结果信号接收数据。

---

## 2. 当前代码边界

### 2.1 应保留的统一接口

以下 `DatabaseWorker` 接口属于当前架构的一部分，不应因为清理旧类而删除：

| 接口 | 用途 |
|---|---|
| `initialize()` | 在 Worker 所在线程创建连接、建表并初始化数据库 |
| `fetch_all_books()` | 查询全部书籍 |
| `fetch_by_type()` | 按书籍类型查询 |
| `fetch_by_status()` | 按阅读状态查询 |
| `fetch_by_author()` | 按作者查询 |
| `find_batch_duplicates()` | 检查扫描批次中的重复书名 |
| `insert_books()` | 在事务中批量写入书籍 |
| `update_book_field()` | 更新单个字段 |
| `delete_book_by_id()` | 按 ID 删除书籍 |
| `close_database()` | 关闭 Worker 的数据库连接 |

阶段 5 不应重复实现这些功能，也不应为了迁移 `pdf.py` 而建立第二个
`DatabaseWorker` 或第二条数据库线程。

### 2.2 可以删除的旧接口

只有在确认没有调用方后，才可以删除：

- `DatabaseManager` 类；
- 旧类中的主线程连接和游标；
- 只服务于旧类的同步查询方法；
- `pdf.py` 对 `DatabaseManager` 的导入和实例化；
- 旧接口对应的、已经没有使用者的信号或辅助方法。

不能仅凭“主界面已经不用”就直接删除。`pdf.py`、测试脚本和其他工具文件也必须搜索确认。

### 2.3 暂不处理的内容

以下内容不属于阶段 5：

- 分页查询和分页控件；
- 真正的增量加载或无限滚动；
- 封面图片生成和图片缓存；
- 搜索框和模糊查询；
- 新增数据库线程；
- 重写扫描器或重复书籍业务规则；
- 将所有数据库字段改成新的数据结构。

当前封面视图继续使用全量 `BookRecord` 列表，`CoverCardModel` 继续在主线程接收查询结果
并刷新模型。书籍数量增大后的性能问题留待后续优化。

---

## 3. 实施原则

### 3.1 先迁移调用方，再删除旧接口

推荐顺序如下：

1. 找出所有 `DatabaseManager` 和旧查询方法的调用方；
2. 为仍有需要的功能补充 Worker 请求信号和结果信号；
3. 把调用方改成信号槽流程；
4. 用验证脚本和手工流程确认新调用链正常；
5. 再删除旧类和旧方法；
6. 最后重新搜索整个项目，确认没有残留引用。

不要先删除 `DatabaseManager` 再根据报错逐个修复。这样容易遗漏非主界面脚本，
也容易把“接口不存在”误判成“功能已经不需要”。

### 3.2 不要让 Worker 返回 UI 对象

Worker 可以返回：

- 字符串；
- 路径；
- `BookRecord`；
- `list[BookRecord]`；
- 明确的成功状态和错误信息。

Worker 不应创建或修改：

- `QMessageBox`；
- `QWidget`；
- `QAbstractItemModel`；
- `QTableView` 或 `QListView`。

主线程收到结果后负责显示提示、打开文件或更新模型。

### 3.3 不要在主线程直接调用 Worker 方法

即使 Worker 对象可以通过属性访问，也不能在主线程直接执行：

```python
self._worker.get_book_path(book_name)
```

这种调用会在调用方线程同步执行，破坏 `moveToThread()` 建立的线程边界。
正确方式是：

```python
self.request_book_path.emit(book_name)
```

然后由 Qt 将请求排队到 Worker 所在线程。

---

## 4. 迁移 `pdf.py` 的数据库查询

### 4.1 现有问题

当前 `pdf.py:get_pdf_path()` 同时承担了两件事：

1. 查询书籍路径；
2. 检查本地路径是否存在。

其中第 1 件事属于数据库 Worker，第 2 件事属于文件系统和 PDF 业务逻辑。
迁移时应把职责拆开，不要把 `PDFEdit` 或文件检查逻辑塞进数据库 Worker。

建议的新流程是：

```text
主线程发出书名
    ↓
DatabaseWorker 查询 book_path
    ↓
Worker 发出 book_path_ready 或 book_path_error
    ↓
主线程收到路径后检查文件是否存在
    ↓
PDFEdit 使用已确认的路径打开文件
```

### 4.2 建议的请求和结果信号

信号名称应遵循项目现有命名风格。可以在 `MainWindow` 或负责 PDF 操作的主线程对象中增加：

```python
request_book_path = Signal(str)
```

在 `DatabaseWorker` 中增加结果信号：

```python
book_path_ready = Signal(str, str)
book_path_error = Signal(str)
```

两个字符串依次表示书名和数据库中保存的路径。若项目只允许根据唯一 ID 查询，
也可以使用 `Signal(object, str)`，让请求和结果都使用 `book_id`。

请求方法应满足以下要求：

- 使用参数绑定，不拼接书名；
- 查询不到记录时发送明确错误；
- 查询结果为 `NULL` 或空字符串时发送明确错误；
- 捕获数据库错误并通过错误信号报告；
- 不在 Worker 中检查 `os.path.exists()`；
- 不返回 `None` 伪装成查询成功。

示例结构如下，具体字段类型应结合项目现有信号约定调整：

```python
@Slot(str)
def fetch_book_path(self, book_name: str) -> None:
    if self.connection is None:
        self.book_path_error.emit("数据库连接未初始化，无法查询书籍路径。")
        return

    try:
        row = self.connection.execute(
            """
            SELECT book_path
            FROM books_information
            WHERE book_name = ?
            LIMIT 1
            """,
            (book_name,),
        ).fetchone()
    except sqlite3.Error as error:
        self.book_path_error.emit(f"书籍路径查询失败：{error}")
        return

    if row is None or not row[0]:
        self.book_path_error.emit(f"未找到书籍“{book_name}”的有效路径。")
        return

    self.book_path_ready.emit(book_name, row[0])
```

这段代码只是接口形状示例。实施时必须放入现有 `DatabaseWorker`，复用同一个连接，
并通过现有数据库线程的请求信号连接。

### 4.3 文件路径检查的主线程处理

主线程收到路径后，应明确区分以下错误：

- 数据库中没有这本书；
- 数据库记录没有保存路径；
- 路径已经不存在；
- 路径存在但不是可打开的文件；
- PDF 库打开文件失败。

这些错误不能全部转换成“暂无书籍”或空字符串。用户需要知道失败发生在数据库、
文件系统还是 PDF 读取阶段。

如果 PDF 处理本身耗时较长，后续可以再设计 PDF 专用任务；阶段 5 只迁移路径查询，
不要同时扩大任务范围。

---

## 5. Worker 连接和窗口关闭

### 5.1 初始化要求

数据库连接必须只在 `DatabaseWorker.initialize()` 中创建，并且该方法必须由
Worker 所在线程执行。初始化失败时应：

1. 关闭已经创建的连接；
2. 发出明确的错误信号；
3. 不发出“数据库已就绪”信号；
4. 不允许主线程继续发送依赖数据库的请求。

重复收到初始化请求时，应保持现有的幂等行为，不能创建第二个连接。

### 5.2 关闭顺序

`MainWindow.closeEvent()` 应遵循以下顺序：

```text
停止接受新的数据库请求
    ↓
请求 Worker 提交并关闭连接
    ↓
Worker 发出关闭完成信号
    ↓
退出 QThread
    ↓
等待线程结束
    ↓
接受窗口关闭事件
```

不能在 Worker 仍有任务时直接销毁线程，也不能先关闭连接再让排队请求继续执行。
验证时应关注是否出现：

```text
QThread: Destroyed while thread is still running
```

如果当前项目已有稳定的关闭实现，本阶段只需确认删除 `DatabaseManager` 后没有改变
关闭顺序，不要为了重构而重写已经正常工作的生命周期代码。

---

## 6. 代码清理步骤

建议按以下批次实施，每批完成后都进行一次搜索和验证。

### 第一步：建立调用方清单

搜索以下内容：

```text
DatabaseManager
from database import
get_book(
get_pdf_path(
sqlite3.connect(
books_information
```

记录每个命中的文件、调用目的和计划处理方式。不要只搜索 `app.py`；
验证脚本、工具脚本和独立模块同样可能创建数据库连接。

### 第二步：迁移 PDF 路径查询

- 在统一 Worker 中增加路径查询方法；
- 在主线程中增加请求信号和结果处理；
- 保留 `PDFEdit` 的文件处理职责；
- 增加查询不到路径和路径不存在的错误处理；
- 确认 PDF 功能不再创建 `DatabaseManager`。

### 第三步：删除旧类

只有以下条件全部满足时才能删除 `DatabaseManager`：

- 项目中没有任何 `DatabaseManager` 实例化；
- `pdf.py` 不再导入它；
- 阶段 0–3 验证脚本不依赖它；
- 没有旧的主线程连接需要继续关闭；
- 所有现有读取、写入、更新和删除流程都通过 Worker；
- 应用启动、扫描、编辑、删除、PDF 路径查询和退出流程均已验证。

### 第四步：清理无效导入和注释

删除：

- 不再使用的 `sqlite3`、`DatabaseManager` 导入；
- 指向旧接口的过时注释；
- 已经没有调用方的兼容方法；
- 误导开发者继续直接操作数据库的示例代码。

不要删除与当前 Worker 仍然共用的 `sqlite3`、`BookRecord` 或 SQL 辅助函数。

---

## 7. 验证要求

### 7.1 自动验证

建议新增 `verify_phase5.py`，使用临时数据库，不修改正式的
`books_information.db`。至少验证：

- Worker 可以初始化临时数据库；
- 初始化重复执行不会创建重复连接或重复表；
- 通过 Worker 可以按书名查询有效路径；
- 查询不存在的书名会发出明确错误；
- 空路径不会被当成成功结果；
- 查询使用参数绑定，特殊字符书名不会破坏 SQL；
- 阶段 1–3 的读取、批量写入、更新和删除验证仍然通过；
- Worker 关闭后连接已释放；
- 退出流程不会遗留运行中的数据库线程。

如果验证脚本需要 Qt 事件循环，应使用 `QCoreApplication`，不要通过
`time.sleep()` 猜测异步结果是否已经返回。应使用信号、事件循环或项目现有的
同步辅助方法等待明确结果。

### 7.2 静态检查

清理完成后，以下搜索不应再找到生产代码中的旧调用：

```text
DatabaseManager(
from database import DatabaseManager
DatabaseManager
sqlite3.connect(
```

其中 `sqlite3.connect()` 在 `DatabaseWorker.initialize()` 内是允许的；
验证脚本中的临时数据库连接也可以保留，但必须明确它不是应用运行时连接。

还应确认：

- 没有主线程直接调用 Worker 的数据库方法；
- 没有 Model 或 Widget 被传入 Worker；
- 没有错误被转换为空列表或空路径；
- 没有为了迁移而新增第二个数据库线程。

### 7.3 手工验证

建议按以下顺序检查：

1. 空数据库启动，确认界面显示空状态；
2. 扫描并写入至少一本书；
3. 切换封面视图和表格视图，确认现有滚动和刷新行为不变；
4. 修改书籍字段并确认重新查询结果正确；
5. 删除书籍并确认两个模型都刷新；
6. 使用 PDF 功能查询路径；
7. 分别测试不存在的书名、空路径和已删除文件；
8. 快速关闭窗口，确认没有线程销毁警告。

本阶段不要求测试大量书籍的加载速度，也不要求实现分页。当前全量加载行为只需保持
正确，性能优化另行安排。

---

## 8. 阶段完成标准

满足以下条件时，阶段 5 的代码清理才算完成：

- 应用运行时只有一个统一的 `DatabaseWorker` 负责 SQLite 访问；
- `MainWindow`、扫描流程和 PDF 路径查询都通过信号槽访问数据库；
- `pdf.py` 不再创建 `DatabaseManager` 或主线程数据库连接；
- `DatabaseManager` 和无效同步查询方法已经删除；
- 所有数据库错误、路径错误和文件错误都有明确处理；
- Worker 初始化和关闭流程没有线程生命周期警告；
- 阶段 0–3 验证仍然通过；
- 阶段 5 验证脚本和手工检查通过；
- 当前封面视图的全量查询和滚动行为保持不变。

阶段 5 完成后，项目可以在统一的数据库访问边界上继续开发。分页、增量加载、
封面图片任务和缓存应作为独立性能工作评估，不要与旧接口清理混在同一个改动中。
