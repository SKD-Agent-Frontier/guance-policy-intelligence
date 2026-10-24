"""独立于Agent SDK的业务错误；接入方按status映射ToolResult。"""


class RetrievalError(Exception):
    status = "backend_error"


class InvalidArgument(RetrievalError, ValueError):
    status = "invalid_argument"


class KBNotFound(RetrievalError, LookupError):
    status = "not_found"

    def __init__(self, planid=None, section_idx=None, chunk_id=None, source_id=None):
        self.planid, self.section_idx, self.chunk_id = planid, section_idx, chunk_id
        self.source_id = source_id
        location = ", ".join(f"{key}={value}" for key, value in
                             (("planid", planid), ("chunk_id", chunk_id), ("source_id", source_id), ("section_idx", section_idx)) if value is not None)
        super().__init__("未找到指定文档、块、章节或冻结版本" + ("（" + location + "）" if location else ""))


class KBNoText(RetrievalError):
    status = "no_text"

    def __init__(self, planid, title):
        self.planid, self.title = planid, title
        super().__init__("指定文档没有可用正文")


class RetrievalTimeout(RetrievalError, TimeoutError):
    status = "timeout"


class BackendError(RetrievalError):
    pass
