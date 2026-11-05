"""
生成 app.py 的精美语法高亮 PDF（浅色背景，保存到桌面）
使用 Microsoft YaHei 字体确保中文字符正常渲染
"""
import fitz
from pygments import lex
from pygments.lexers import Python3Lexer
from pygments.token import Token
from pathlib import Path
import os

# ===== 配置 =====
CODE_PATH = Path(__file__).resolve().parents[1] / "src" / "ausarum" / "app.py"
OUTPUT_PATH = Path(r"C:\Users\Wang\Desktop\el_gui_code.pdf")

# A4 页面尺寸 (points)
PAGE_W, PAGE_H = 595, 842
MARGIN_LEFT = 50
MARGIN_RIGHT = 35
MARGIN_TOP = 52
MARGIN_BOTTOM = 45
LN_WIDTH = 32

FONT_SIZE = 8.5        # 代码字号
LINE_HEIGHT = 14.5     # 行距
HEADER_H = 36

# 颜色 (RGB 0-1)
BG_COLOR = (0.98, 0.975, 0.97)
ALT_ROW_COLOR = (0.95, 0.945, 0.94)
HEADER_BG = (0.88, 0.88, 0.92)
HEADER_LINE = (0.75, 0.75, 0.80)
HEADER_TEXT = (0.25, 0.25, 0.35)
LINE_NUM_COLOR = (0.55, 0.55, 0.55)
FOOTER_COLOR = (0.6, 0.6, 0.6)

# 语法着色 (浅色主题)
TOKEN_COLORS = {
    Token.String.Doc:     (0.06, 0.46, 0.02),
    Token.String:         (0.65, 0.10, 0.10),
    Token.Name.Decorator: (0.55, 0.04, 0.55),
    Token.Name.Function:  (0.04, 0.32, 0.58),
    Token.Name.Class:     (0.06, 0.46, 0.02),
    Token.Name.Exception: (0.55, 0.04, 0.04),
    Token.Name.Builtin:   (0.20, 0.15, 0.58),
    Token.Keyword.Type:   (0.20, 0.15, 0.58),
    Token.Keyword:        (0.20, 0.15, 0.58),
    Token.Operator.Word:  (0.20, 0.15, 0.58),
    Token.Comment:        (0.50, 0.50, 0.50),
    Token.Number:         (0.04, 0.45, 0.45),
    Token.Operator:       (0.0, 0.0, 0.0),
    Token.Punctuation:    (0.0, 0.0, 0.0),
    Token.Text:           (0.0, 0.0, 0.0),
}


def token_is_subtype(sub, main):
    return sub is main or str(sub).startswith(str(main) + '.')


def get_token_color(tok_type):
    for cls, color in TOKEN_COLORS.items():
        if token_is_subtype(tok_type, cls):
            return color
    return (0.0, 0.0, 0.0)


# 查找支持中文的字体文件（优先使用 YaHei）
FONT_FILE = None
for p in [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\STXIHEI.TTF",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]:
    if os.path.exists(p):
        FONT_FILE = p
        break

# 字体名称（在 PDF 中注册的名字，不影响渲染）
PDF_FONT_NAME = "CodeFont"

# 读取代码 + 词法分析
code_text = CODE_PATH.read_text(encoding="utf-8")
lines = code_text.split("\n")

tokenized_lines = []
for line in lines:
    tokens = []
    for tok_type, tok_text in lex(line + "\n", Python3Lexer()):
        if tok_text == "\n":
            continue
        tokens.append((tok_text, get_token_color(tok_type)))
    tokenized_lines.append(tokens)

# 字符宽度基准（用于代码定位）
# 使用 len() 近似：英文 1 单位，中文 ~1.6 单位
# 但为了简单统一按 len 计算，后续可用 char_w 微调
CHAR_W = FONT_SIZE * 0.6

# 水平位置
X_LN = MARGIN_LEFT
X_CODE = MARGIN_LEFT + LN_WIDTH + 10

# 通用文本插入参数
def text_kw(fontsize=FONT_SIZE, color=(0, 0, 0)):
    kw = dict(fontname=PDF_FONT_NAME, fontsize=fontsize, color=color)
    if FONT_FILE:
        kw["fontfile"] = FONT_FILE
    return kw


# 创建 PDF 文档
doc = fitz.open()


def make_page(doc, subtitle="app.py"):
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    # 底色
    page.draw_rect(fitz.Rect(0, 0, PAGE_W, PAGE_H),
                   color=BG_COLOR, fill=BG_COLOR)
    # 页眉背景
    page.draw_rect(fitz.Rect(0, 0, PAGE_W, HEADER_H),
                   color=HEADER_BG, fill=HEADER_BG)
    page.draw_line((0, HEADER_H), (PAGE_W, HEADER_H),
                   color=HEADER_LINE)
    # 页眉文字
    page.insert_text(
        (MARGIN_LEFT, 24),
        f"{subtitle}  -  Eternity Library",
        **text_kw(fontsize=11, color=HEADER_TEXT),
    )
    return page


page = make_page(doc)
y = MARGIN_TOP

for i, line_tokens in enumerate(tokenized_lines):
    line_num = i + 1

    # 分页
    if y + LINE_HEIGHT > PAGE_H - MARGIN_BOTTOM:
        page = make_page(doc, subtitle="app.py (续)")
        y = MARGIN_TOP

    # 交替行背景
    if line_num % 2 == 0:
        page.draw_rect(
            fitz.Rect(MARGIN_LEFT - 4, y - FONT_SIZE + 1,
                       PAGE_W - MARGIN_RIGHT + 4, y + 3),
            color=ALT_ROW_COLOR, fill=ALT_ROW_COLOR,
        )

    # 行号
    page.insert_text(
        (X_LN, y), str(line_num).rjust(4),
        **text_kw(fontsize=8, color=LINE_NUM_COLOR),
    )

    # 代码（逐段着色）
    x = X_CODE
    for tok_text, color in line_tokens:
        page.insert_text((x, y), tok_text, **text_kw(fontsize=FONT_SIZE, color=color))
        # 近似字符宽度（等宽模式，中文字符宽度加倍处理）
        cjk_count = sum(1 for c in tok_text if '一' <= c <= '鿿' or '　' <= c <= '〿')
        ascii_count = len(tok_text) - cjk_count
        x += (ascii_count + cjk_count * 2) * CHAR_W

    y += LINE_HEIGHT

# 页脚（最后一页）
num_pages = doc.page_count
footer = f"- {num_pages} -     {len(lines)} 行"
doc[-1].insert_text(
    ((PAGE_W - len(footer) * 8 * 0.6) / 2, PAGE_H - 20),
    footer,
    **text_kw(fontsize=8, color=FOOTER_COLOR),
)

doc.set_metadata({"title": "app.py Source Code", "author": "Ausarum"})
doc.save(OUTPUT_PATH)
doc.close()

print("PDF {}  lines={} pages={}".format(OUTPUT_PATH, len(lines), num_pages))
