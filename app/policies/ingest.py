import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import PolicyChunk, PolicyDocument
from app.policies.embeddings import embed_passages

DOCS_DIR = Path(__file__).parent / "docs"


@dataclass(frozen=True)
class ParsedSection:
    section: int
    heading: str
    content: str


def parse_frontmatter(text: str) -> tuple[dict, str]:
    text = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        raise ValueError("Policy file is missing front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, m.group(2)


def split_sections(body: str) -> list[ParsedSection]:
    sections = []
    for block in re.split(r"(?m)^(?=## )", body):
        block = block.strip()
        if not block.startswith("## "):
            continue  # skips the title/intro before the first section
        heading_line = block.split("\n", 1)[0]
        m = re.match(r"## (\d+)\.\s*(.+)", heading_line)
        if not m:
            raise ValueError(f"Bad section heading: {heading_line!r}")
        sections.append(ParsedSection(int(m.group(1)), m.group(2).strip(), block))
    return sections


def ingest_all(session: Session) -> int:
    count = 0
    for path in sorted(DOCS_DIR.glob("*.md")):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        sections = split_sections(body)
        version = int(meta["version"])

        # Re-running replaces the document, so ingestion is repeatable.
        existing = session.scalar(
            select(PolicyDocument).where(
                PolicyDocument.doc_key == meta["doc_key"],
                PolicyDocument.version == version,
            )
        )
        if existing:
            session.delete(existing)
            session.flush()

        doc = PolicyDocument(
            doc_key=meta["doc_key"],
            title=meta["title"],
            version=version,
            effective_from=date.fromisoformat(meta["effective_from"]),
            effective_to=(
                date.fromisoformat(meta["effective_to"]) if meta.get("effective_to") else None
            ),
            source_uri=f"policies/docs/{path.name}",
        )
        vectors = embed_passages([f"{meta['title']}. {s.content}" for s in sections])
        for s, vec in zip(sections, vectors):
            doc.chunks.append(
                PolicyChunk(
                    ref=f"{meta['doc_key']}_v{version}:section_{s.section}",
                    section=s.section,
                    heading=s.heading,
                    content=s.content,
                    embedding=vec,
                )
            )
        session.add(doc)
        count += len(sections)

    session.commit()
    return count