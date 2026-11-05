# 用于实现pdf编辑、转换、合并、提取、压缩等操作
import os
import fitz
from .database import DatabaseWorker


def get_pdf_path(book_name: str) -> None:
    """
    :param book_name: 需要获取路径的书籍名称
    :return: pdf文件的路径
    - :raise: FileNotFoundError 文件不存在时抛出错误
    """
    path = DatabaseWorker()
    # file_path = path.get_book(
    #     book_name,
    #     columns='book_path'
    # )                                   # 获取pdf文件的路径,返回的格式为列表，如：[('F:/Books',)]
    # file_path = file_path[0][0]         # 将列表转换为字符串，便于后续操作
    # if not os.path.exists(file_path):
    #     raise FileNotFoundError(f"路径 {file_path} 不存在，请检查文件路径是否正确！")
    # return file_path


class PDFEdit:
    @staticmethod
    def get_pdf_text(book_name: str):
        """获取pdf文件每页的文字"""
        path = get_pdf_path(book_name)
        pdf = fitz.open(os.path.join(path, book_name))
        for page in pdf:
            print(page.get_text())
        pdf.close()

    def get_pdf_image(self, book_name: str):
        """获取pdf文件每页的图片"""


if __name__ == '__main__':
    PDFEdit.get_pdf_text('test_file.pdf')
