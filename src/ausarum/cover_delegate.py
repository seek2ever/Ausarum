# 绘制层
from PySide6.QtCore import (
    QRect,
    QSize,
    Qt
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem
)

from .cover_model import CoverCardModel


class CoverCardDelegate(QStyledItemDelegate):
    """封面卡片的绘制代理

    核心原则：paint() 里不做任何创建对象的操作（不 new 控件、不读文件）。
    所有可复用的资源（字体、颜色、画笔）在 __init__ 中创建好。
    """

    # ── 卡片几何参数（像素） ──
    CARD_W = 180
    CARD_H = 260
    COVER_W = 140
    COVER_H = 180
    PADDING = 8
    RADIUS = 8  # 圆角半径

    def __init__(self):
        super().__init__()

        # 预创建所有绘制资源 —— 不在 paint() 里重复创建
        self.title_font = QFont("Microsoft YaHei", 10, QFont.Bold)
        self.info_font = QFont("Microsoft YaHei", 9)
        self.placeholder_font = QFont("Microsoft YaHei", 12)

        self.color_card_bg = QColor("#f8f9fa")
        self.color_card_border = QColor("#dee2e6")
        self.color_card_hover_bg = QColor("#e9ecef")
        self.color_card_hover_border = QColor("#adb5bd")
        self.color_placeholder_bg = QColor("#ced4da")
        self.color_placeholder_fg = QColor("#6c757d")
        self.color_info = QColor("#6c757d")
        self.color_title = QColor("#212529")

    # ── QStyledItemDelegate 必须实现的 3 个方法 ──

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        """绘制单个卡片。这是最核心的方法。

        Args:
            painter: Qt传入的 QPainter（画笔），已经设置好了裁剪区域
            option:  包含条目的矩形区域、状态（hover/selected/focus 等）
            index:   条目的模型索引，通过 index.data(role) 获取数据
        """
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # ── 判断鼠标悬停状态 ──
        is_hover = bool(option.state & QStyle.State_MouseOver)

        # ── 卡片背景矩形（在grid cell 内居中，留出间距） ──
        card_rect = QRect(
            option.rect.left() + (option.rect.width() - self.CARD_W) // 2,
            option.rect.top() + 4,
            self.CARD_W,
            self.CARD_H,
        )

        # ── 背景填充 ──
        bg = self.color_card_hover_bg if is_hover else self.color_card_bg
        border = self.color_card_hover_border if is_hover else self.color_card_border
        painter.setPen(QPen(border, 1))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(card_rect, self.RADIUS, self.RADIUS)

        # ── 封面区域 ──
        cover_x = card_rect.left() + (self.CARD_W - self.COVER_W) // 2
        cover_y = card_rect.top() + self.PADDING
        cover_rect = QRect(cover_x, cover_y, self.COVER_W, self.COVER_H)

        # 尝试获取真实封面图（如有）
        pixmap = index.data(Qt.DecorationRole)
        if pixmap and isinstance(pixmap, QPixmap) and not pixmap.isNull():
            scaled = pixmap.scaled(
                self.COVER_W, self.COVER_H,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            px = cover_rect.center().x() - scaled.width() // 2
            py = cover_rect.center().y() - scaled.height() // 2
            painter.drawPixmap(px, py, scaled)
        else:
            # 占位封面
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.color_placeholder_bg)
            painter.drawRoundedRect(cover_rect, 4, 4)
            painter.setPen(self.color_placeholder_fg)
            painter.setFont(self.placeholder_font)
            painter.drawText(cover_rect, Qt.AlignCenter, "封面")

        # ── 书名 ──
        title_rect = QRect(
            card_rect.left() + 6,
            cover_rect.bottom() + 6,
            self.CARD_W - 12,
            46,
        )
        name = index.data(CoverCardModel.BookNameRole) or ""
        painter.setFont(self.title_font)
        painter.setPen(self.color_title)
        # drawText 自动按 WordWrap 换行，超出的被 clip
        painter.drawText(title_rect, Qt.AlignHCenter | Qt.TextWordWrap, name)

        # ── 副信息（作者/类型） ──
        info_rect = QRect(
            card_rect.left() + 6,
            title_rect.bottom(),
            self.CARD_W - 12,
            22,
        )
        author = index.data(CoverCardModel.AuthorRole) or ""
        book_type = index.data(CoverCardModel.BookTypeRole) or ""
        sub_text = author if author != "未知作者" else book_type

        painter.setFont(self.info_font)
        painter.setPen(self.color_info)
        painter.drawText(info_rect, Qt.AlignHCenter, sub_text)

        painter.restore()

    def sizeHint(self, option, index):
        """返回每个条目的建议尺寸。

        IconMode 下配合 setGridSize() 使用时，这个值被 grid size 覆盖。
        但保留正确实现以便切换到其他模式。
        """
        return QSize(self.CARD_W + 16, self.CARD_H + 10)

    # ── 可选：处理交互 ──
    # 如需更复杂的鼠标交互（如卡片内的按钮点击），需要重写 editorEvent()
    # 对于点击整张卡片打开详情这种需求，直接使用 View 的 clicked 信号即可
