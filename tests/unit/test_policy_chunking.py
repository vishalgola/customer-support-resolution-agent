from app.policies.ingest import parse_frontmatter, split_sections

SAMPLE = """---
doc_key: demo
title: Demo
version: 2
effective_from: 2026-01-01
---
# Title

## 1. First
Text one.

## 2. Second
Text two.
"""


def test_frontmatter_parsed():
    meta, _ = parse_frontmatter(SAMPLE)
    assert meta["doc_key"] == "demo"
    assert meta["version"] == "2"


def test_sections_split_and_numbered():
    _, body = parse_frontmatter(SAMPLE)
    sections = split_sections(body)
    assert [s.section for s in sections] == [1, 2]
    assert sections[0].heading == "First"
    assert "Text two." in sections[1].content
    assert "Text one." not in sections[1].content


def test_windows_line_endings():
    meta, body = parse_frontmatter(SAMPLE.replace("\n", "\r\n"))
    assert meta["title"] == "Demo"
    assert len(split_sections(body)) == 2