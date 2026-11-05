from PySide6.QtWidgets import QStyledItemDelegate


class TableDelegate(QStyledItemDelegate):
    """自定义表格单元格的显示和编辑行为"""
    def __init__(self, parent=None):
        super().__init__(parent)
