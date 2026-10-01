"""管理员推送；导入不初始化存储或连接外部服务。"""


class PushError(ValueError):
    """可安全返回给管理台的业务错误。"""

    def __init__(self, message, code="validation_error", status=400):
        super().__init__(message)
        self.code = code
        self.status = status
