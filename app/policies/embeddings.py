from functools import lru_cache

from fastembed import TextEmbedding

MODEL_NAME = "BAAI/bge-small-en-v1.5"  # 384 dimensions


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(MODEL_NAME)


def embed_passages(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _model().embed(texts)]


def embed_query(text: str) -> list[float]:
    return next(iter(_model().query_embed(text))).tolist()