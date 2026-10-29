from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import PolicyChunk, PolicyDocument
from app.policies.embeddings import embed_query


DEFAULT_MIN_SCORE = 0.45  # calibrated on sample queries; re-check with the eval set

@dataclass(frozen=True)
class PolicyHit:
    ref: str
    title: str
    version: int
    heading: str
    content: str
    score: float  # cosine similarity: higher is closer


def search_policy(
    session: Session,
    query: str,
    *,
    as_of: date | None = None,
    top_k: int = 4,
    min_score: float = DEFAULT_MIN_SCORE,     # calibrated on sample queries; re-check with the eval set
    doc_key: str | None = None,
) -> list[PolicyHit]:
    as_of = as_of or date.today()
    distance = PolicyChunk.embedding.cosine_distance(embed_query(query))

    stmt = (
        select(PolicyChunk, PolicyDocument, distance.label("distance"))
        .join(PolicyDocument, PolicyChunk.document_id == PolicyDocument.id)
        .where(PolicyDocument.effective_from <= as_of)
        .where(or_(PolicyDocument.effective_to.is_(None), PolicyDocument.effective_to > as_of))
        .order_by(distance)
        .limit(top_k)
    )
    if doc_key:
        stmt = stmt.where(PolicyDocument.doc_key == doc_key)

    hits = []
    for chunk, doc, dist in session.execute(stmt):
        score = 1.0 - float(dist)
        if score >= min_score:
            hits.append(PolicyHit(chunk.ref, doc.title, doc.version, chunk.heading,
                                  chunk.content, round(score, 3)))
    return hits