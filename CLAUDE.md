# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

个人图书馆管理工具（PySide6 桌面应用），用于扫描管理本地电子书、阅读统计、阅读热力图等。当前处于非常早期的开发阶段。

### 开发者背景

- 唯一开发者，非计算机相关专业，Python 编程爱好者
- 已掌握 Python 基本语法和 PySide6 部分常用控件用法，但还不是很熟练
- **所有代码尽量控制在开发者能理解的范围内，尽量避免引入超出当前知识水平的抽象、复杂设计或黑盒依赖**
- **如需引入超出当前知识范围的技术，必须附带相应的解释说明，便于学习**

### 项目定位

- 功能方向类似 Calibre，但侧重点不同，暂时只做本地电子书管理
- 后续可能开发：联网检索图书基本信息、在线书城集成、任何需要网络请求的元数据获取

## Tech Stack

- **GUI**: PySide6 (Qt6)
- **Database**: SQLite3
- **PDF**: PyMuPDF (fitz)
- **Python**: 3.12+ (项目自带venv)

## Architecture

```
src/ausarum/app.py           — 主窗口入口，MainWindow 类，GUI 布局和信号连接
src/ausarum/books.py         — 书籍业务逻辑：ScanBookFiles 扫描本地文件，Books 基类及子类
src/ausarum/database.py      — 数据库管理：DatabaseWorker，继承 QObject 使用 Qt 信号机制
src/ausarum/utils.py         — 工具类：Tips（QMessageBox 静态封装）
src/ausarum/pdf.py           — PDF 操作：路径获取、文字提取
```

### 关键模块说明

1. **MainWindow** (`src/ausarum/app.py`): 主界面，包含菜单栏、状态栏、操作按钮（扫描/显示/取消/关闭）、书籍表格（QTableWidget）。通过 Qt 信号监听数据库 Worker 的结果信号。

2. **DatabaseWorker** (`src/ausarum/database.py`): 核心数据层，使用 QObject 信号实现与 GUI 的解耦。数据库表 `books_information` 保存书籍信息。

3. **Books 类体系**: `Books` 基类 → `PDFBooks`、`TxtBooks`、`EpubBooks` 三个子类，目前多为占位方法。

### Qt 信号流

DatabaseManager 发出信号 → MainWindow 的槽函数处理：重复书籍弹窗让用户选择（覆盖/重命名/跳过），添加结果弹窗通知。

## Run

```bash
# 使用 uv 启动（Python 3.12）
uv run ausarum
```

## Development Notes

- 所有 UI 文本使用中文（通过 QCoreApplication.translate 包装）
- 数据库文件 `books_information.db` 自动创建在项目根目录
- 书籍图标在 `src/ausarum/icons/` 目录下
- 项目有 venv（Python 3.12），另有 pyvenv.cfg 指向的外部环境
- `exercise.py` 是 QPalette 调色板示例，与主项目无关

## UI Layout Plan

主界面采用 **左侧面板 + 右侧主区域** 的分栏布局：

### 整体结构

- **左侧面板**（固定约 220px）：视图切换按钮 → 分类筛选列表（全部/按类型/按阅读状态/按作者）→ 快捷操作按钮（扫描/显示/取消/关闭）
- **右侧主区域**（QStackedWidget 切换两种模式）：
  - **封面模式**：QScrollArea 包裹网格布局，每本书用 QFrame 卡片展示（占位封面 + 书名 + 作者/类型），点击卡片响应
  - **列表模式**：QTableWidget 表格展示（复用现有实现）
- 左右分区使用 **QSplitter** 实现，用户可拖动调整宽度

### 后续可扩展方向

- PDF 提取首页作为真实封面
- 详情弹窗 QDialog（点击卡片后显示完整信息）
- 右键菜单（编辑/删除/打开位置）
- 搜索框 + 实时过滤
- 深色模式 QSS 切换
