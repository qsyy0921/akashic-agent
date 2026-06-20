from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_server() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "private_runtime"
        / "mcp_servers"
        / "arxiv_search_server.py"
    )
    spec = importlib.util.spec_from_file_location("arxiv_search_server", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_arxiv_search_query_uses_fielded_terms():
    server = _load_server()

    query = server._build_search_query(
        {
            "query": "vision language model",
            "category": "cs.CV",
            "author": "Kaiming He",
            "title": "token pruning",
        }
    )

    assert query == 'all:"vision language model" AND cat:cs.CV AND au:"Kaiming He" AND ti:"token pruning"'


def test_parse_arxiv_atom_response():
    server = _load_server()
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2501.12345v1</id>
    <updated>2025-01-02T00:00:00Z</updated>
    <published>2025-01-01T00:00:00Z</published>
    <title> Token Pruning for Vision Language Models </title>
    <summary> A compact summary. </summary>
    <author><name>Alice Example</name></author>
    <category term="cs.CV" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/2501.12345v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2501.12345v1" rel="related" type="application/pdf"/>
    <arxiv:doi>10.0000/example</arxiv:doi>
  </entry>
</feed>
"""

    papers = server._parse_arxiv_response(xml)

    assert papers == [
        {
            "id": "2501.12345v1",
            "title": "Token Pruning for Vision Language Models",
            "summary": "A compact summary.",
            "authors": ["Alice Example"],
            "published": "2025-01-01T00:00:00Z",
            "updated": "2025-01-02T00:00:00Z",
            "categories": ["cs.CV"],
            "primary_category": "cs.CV",
            "abstract_url": "http://arxiv.org/abs/2501.12345v1",
            "pdf_url": "http://arxiv.org/pdf/2501.12345v1",
            "doi": "10.0000/example",
        }
    ]
