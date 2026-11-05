# 项目文件夹改名流程：`Eternity-Library` → `Ausarum`

> 本文是**手动操作手册**。项目代码、依赖、PyCharm 配置里的旧名称已经改好了，
> 只剩「文件夹本身」还叫 `D:\Eternity-Library`。
> 把文件夹改名会牵动虚拟环境、PyCharm 全局配置和 Git 记录，所以按下面的顺序做，不要跳步。

预计耗时：10～20 分钟（其中 `uv sync` 视网络情况可能需要几分钟）。

---

## 0. 开始前必须确认的事

- [x] **先退出 DeepSeek Harness（DSH）会话**。本会话当前以 `D:\Eternity-Library` 为工作区，
      改名会让会话立即失效，而且会话的文件监视器可能让改名直接失败（提示"文件夹正在使用"）。
      请先结束会话，再在普通 PowerShell 或资源管理器里操作。
- [x] **关闭 PyCharm**（包括"退出"而不是只关窗口），确认任务管理器里没有 `pycharm64.exe`。
- [x] 关闭所有打开了项目目录的终端 / PowerShell / CMD 窗口（它们的当前目录会锁住文件夹）。
- [x] 关闭运行中的 `Ausarum` 程序。
- [ ] 关闭指向该目录的资源管理器窗口。
- [ ] 提交或暂存当前改动：`git status` 应为干净状态（当前分支 `dev`）。
- [ ] 确认没有其他工具（Everything、杀毒软件实时扫描、OneDrive 同步）正在占用该目录。

> ⚠️ **不要在 PyCharm 里用「Refactor → Rename」改项目根目录**。IDE 的重构只适合改代码符号，
> 改物理目录会留下大量失效的绝对路径。用资源管理器或下面的 PowerShell 命令改。

---

## 1. 备份

改名前先留一份退路（`.venv` 可以不备份，后面会重建）：

```powershell
# 只备份配置和源码，跳过 .venv
robocopy "D:\Eternity-Library" "D:\Eternity-Library-backup" /E /XD .venv .git
```

如果不想多占空间，至少确认 `git status` 是干净的——代码都能从 Git 恢复。

---

## 2. 重命名文件夹

```powershell
Rename-Item -Path "D:\Eternity-Library" -NewName "Ausarum"
```

或者直接在资源管理器里右键重命名。

改名后立刻确认：

```powershell
Test-Path "D:\Ausarum\pyproject.toml"   # 应输出 True
Test-Path "D:\Eternity-Library"         # 应输出 False
```

---

## 3. 重建虚拟环境（必须做）

**为什么必须重建？**
`.venv` 里写死了大量绝对路径：

| 文件 | 写死的内容 |
| --- | --- |
| `.venv\Scripts\activate.bat` / `Activate.ps1` / `activate` | `VIRTUAL_ENV="D:\Eternity-Library\.venv"` |
| `.venv\Scripts\ausarum.exe` | 启动器里内嵌的解释器绝对路径 |
| `.venv\Lib\site-packages\ausarum.pth` | `D:\Eternity-Library\src` |
| `.venv\Lib\site-packages\ausarum-0.1.0.dist-info\direct_url.json` | 指向旧目录 |
| `.venv\pyvenv.cfg` | 提示符等元信息 |

只改文件夹名不重建，`uv run` 会报找不到解释器或 `ModuleNotFoundError`。

```powershell
cd D:\Ausarum
Remove-Item .venv -Recurse -Force
uv sync
```

说明：

- `uv sync` 会按 `uv.lock` 里的锁定版本从本机缓存重新创建 `.venv`，**不会**升级依赖版本。
- 因为 uv 的包缓存是全局的（`C:\Users\Wang\AppData\Local\uv\cache`），通常不需要重新下载。
- 重建后 `uv` 会自动把本项目的控制台入口 `ausarum` 重新装到 `.venv\Scripts\ausarum.exe`。

验证：

```powershell
uv run python -c "import ausarum; print(ausarum.__file__)"
# 期望输出：D:\Ausarum\src\ausarum\__init__.py

uv sync --check       # 检查锁文件与项目配置是否一致
```

---

## 4. 更新 PyCharm 全局解释器表

PyCharm 把解释器登记在**全局配置文件**里（不在项目目录内），路径变了必须同步，否则会显示红色的失效解释器。

文件位置：

```
C:\Users\Wang\AppData\Roaming\JetBrains\PyCharm2026.2\options\jdk.table.xml
```

找到 `<name value="uv (Ausarum)" />` 这一节，把下面所有 `D:/Eternity-Library` 改成 `D:/Ausarum`：

- `<homePath value="D:/Eternity-Library/.venv/Scripts/python.exe" />`
- `<root url="file://D:/Eternity-Library/.venv/Lib/site-packages" ... />`（以及 win32、win32/lib、pythonwin 几条）
- `<additional ... ASSOCIATED_PROJECT_PATH="D:/Eternity-Library" WORKING_DIRECTORY="D:\Eternity-Library" UV_WORKING_DIR="D:\Eternity-Library" UV_VENV_PATH="D:\Eternity-Library\.venv" ...>`
- `FLAVOR_DATA` 里的 `uvWorkingDirectory` 和 `venvPath`

**更省事的做法**：不手改，改完文件夹后打开 PyCharm，进入
`Settings → Project: Ausarum → Python Interpreter → Add Interpreter → uv`，
选择 `D:\Ausarum\.venv`，PyCharm 会自己更新这条记录；然后再把旧的失效项删掉。

改完用这条命令检查 XML 没被改坏：

```powershell
[xml](Get-Content "$env:APPDATA\JetBrains\PyCharm2026.2\options\jdk.table.xml" -Raw) | Out-Null
```

> 同理，如果你还有 `PyCharm2026.1` 或 `PyCharmCE2024.1` 的配置目录，按需重复一遍。

---

## 5. 更新 PyCharm 的「最近项目」和「受信任路径」

同一目录 `C:\Users\Wang\AppData\Roaming\JetBrains\PyCharm2026.2\options\` 下：

| 文件 | 要改的内容 |
| --- | --- |
| `recentProjects.xml` | `<entry key="D:/Eternity-Library">`、`<frameTitle ...>`、`lastOpenedProject` |
| `trusted-paths.xml` | `<entry key="D:\Eternity-Library" value="true" />` |
| `other.xml` | `last_opened_file_path`、最近文件列表里的 `D:/Eternity-Library` |

**最省事的做法**：改完文件夹后直接打开新目录，PyCharm 会把 `D:\Ausarum` 记成新项目；
旧的 `D:\Eternity-Library` 记录会变成一条点不开的"幽灵项目"，在欢迎页右键 → Remove 即可。
`trusted-paths.xml` 里的旧条目留着不影响使用。

---

## 6. 项目内 `.idea` 目录：基本不用动

项目里的 `.idea` 已经检查并修正过，绝大多数路径是相对 `$PROJECT_DIR$` 写的，改名后自动生效：

- `.idea/.name` → `Ausarum`
- `.idea/ausarum.iml` → 解释器名 `uv (Ausarum)`，内容根 `file://$MODULE_DIR$`
- `.idea/misc.xml` → 工程 SDK 和 Black 都指向 `uv (Ausarum)`
- `.idea/workspace.xml` → 运行配置 `Ausarum` 使用 `$PROJECT_DIR$` 和 `ausarum` 模块名

**唯一需要你确认的**：如果 PyCharm 打开后提示解释器无效，说明第 4 步没生效，
回到 `Settings → Python Interpreter` 重新选一次 `D:\Ausarum\.venv\Scripts\python.exe`。

---

## 7. Git 相关

- 本地 Git 仓库**不需要**为文件夹改名做任何事，重命名文件夹不影响 `.git`。
- GitHub 仓库已经改名为 `seek2ever/Ausarum`，`.idea/workspace.xml` 里的仓库地址已同步为新地址。
- **本地远程地址还需要你自己改一次**（`.git` 目录受沙箱保护，我无法代改）。退出本会话后运行：

  ```powershell
  cd D:\Ausarum
  git remote set-url origin https://github.com/seek2ever/Ausarum.git
  git remote -v
  ```

  也可以等改名、打开 PyCharm 后再执行，位置无所谓。
- GitHub 会把旧地址 `Eternity-Library` 自动重定向到新仓库，所以**即使忘了改也能正常拉取**，
  只是 `git remote -v` 会一直显示旧名。

---

## 8. 改名后自查清单

```powershell
cd D:\Ausarum

# 1. 包能被导入，且指向新路径
uv run python -c "import ausarum; print(ausarum.__file__)"

# 2. 依赖完整
uv sync --check

# 3. 程序能启动
uv run ausarum
```

再检查：

- [ ] 启动后窗口标题是「三木书斋」，程序能读取 `books_information.db`（数据库在项目根目录，随目录一起搬走）。
- [ ] PyCharm 里 `Settings → Project → Python Interpreter` 显示 `Python 3.12 (Ausarum)` 或 `uv (Ausarum)`，没有红色报错。
- [ ] 右上角运行配置下拉框里有 `Ausarum`，点击能直接启动。
- [ ] 终端里执行 `uv run pytest`（如果你之后加了测试）或 `uv run python -m compileall src/ausarum tests` 不报错。
- [ ] 全局搜索确认没有遗漏的旧名：

```powershell
# 需要 ripgrep；没有的话用 Select-String
rg -uu -n "Eternity" D:\Ausarum --glob "!*.pyc" --glob "!.git/*"
```

预期：只可能剩 `.git` 历史提交信息、以及本文档里的示例文字。

---

## 9. 出问题怎么回滚

1. 把文件夹改回去：`Rename-Item D:\Ausarum D:\Eternity-Library`
2. 删掉 `.venv`，在旧路径重新 `uv sync`。
3. 把备份的配置还原：
   - 本仓库内的 `.backup-ausarum\` 里有改名前的 `.idea`、`jdk.table.xml`、`recentProjects.xml` 副本。
   - `jdk.table.xml` / `recentProjects.xml` 覆盖回
     `C:\Users\Wang\AppData\Roaming\JetBrains\PyCharm2026.2\options\`。
4. 如果第 1 步的备份副本存在，直接整体覆盖回去即可。

---

## 附：本次已完成的配置同步（改名后依然有效）

| 项目 | 状态 |
| --- | --- |
| `pyproject.toml` 包名、入口 `ausarum` | 已是 `ausarum` |
| `.venv` 中旧包名 `eternity_library` 残留 | 已删除 |
| `.venv/pyvenv.cfg` 提示符 | 已改为 `ausarum` |
| 项目根目录孤立的 Python 3.9 `pyvenv.cfg` | 已删除 |
| `.idea/.name` | 已改为 `Ausarum` |
| `.idea` 中失效运行配置 `el_gui` / `database` | 已删除，新增 `Ausarum` |
| PyCharm 全局 SDK `uv-EL` | 已改名 `uv (Ausarum)` |
| PyCharm 全局失效 SDK `uv (Eternity-Library)` | 已删除 |
| `guide/` 文档里的 `el_gui.py` / `Eternity-Library` | 已替换（含 `D:\Ausarum` 路径写法） |
| `pyproject.toml` 中 `[build-system]` 下无效的 `url` | 已删除并加注释 |
| GitHub 仓库地址 | 仓库已改名 `Ausarum`，`.idea/workspace.xml` 已同步；`git remote set-url` 需你自己执行（见第 7 节） |
