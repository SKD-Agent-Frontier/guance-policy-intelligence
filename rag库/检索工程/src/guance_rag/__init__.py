"""不依赖Agent SDK的同步只读知识库。"""
from .config import RuntimeOptions
from .errors import BackendError, InvalidArgument, KBNoText, KBNotFound, RetrievalError, RetrievalTimeout
from .models import ChunkSpan, ContextSpan, HitV03, PassageV03
from .media_models import MediaHitV04, MediaPassageV04, ImageDataV04

__all__ = ["RuntimeOptions", "RagKnowledgeBase", "HitV03", "PassageV03", "ChunkSpan", "ContextSpan",
           "MediaHitV04", "MediaPassageV04", "ImageDataV04",
           "RetrievalError", "InvalidArgument", "KBNotFound", "KBNoText", "RetrievalTimeout", "BackendError"]


def __getattr__(name):
    if name == "RagKnowledgeBase":
        from .runtime import RagKnowledgeBase

        return RagKnowledgeBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
