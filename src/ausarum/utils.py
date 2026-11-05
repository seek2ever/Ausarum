from PySide6.QtWidgets import QMessageBox


class Tips:
    @staticmethod
    def information_msg(imsg: str) -> None:
        """显示一般性的通知信息"""
        QMessageBox.information(
            None,
            "提示",
            imsg,
            QMessageBox.Ok
        )

    @staticmethod
    def question_msg(qmsg: str) -> int:
        """显示需要用户抉择的疑问性信息"""
        return QMessageBox.question(
            None,
            "提示",
            qmsg,
            QMessageBox.Yes | QMessageBox.No
        )
