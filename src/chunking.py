"""Structure-aware chunking for policy documents.

* Splits on headings, so a chunk never spans two sections.
* Packs whole paragraphs / tables / lists up to ~MAX_CHARS (tables are never cut).
* Prepends a contextual header ("Document > Section > Subsection") to the text that is
  embedded and BM25-indexed, so a chunk like "Minimum GPA 3.5" still carries "Tier A grant".
"""
import re
from pathlib import Path

MAX_CHARS = 900

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _front_matter(text: str) -> tuple[dict, str]:
    meta = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            text = text[end + 4:]
    return meta, text


def _blocks(body: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]


def chunk_markdown(path: Path) -> list[dict]:
    meta, text = _front_matter(path.read_text(encoding="utf-8"))
    title = meta.get("title", path.stem.replace("_", " ").title())
    sections: list[tuple[list[str], list[str]]] = []  # (heading path, lines)
    path_stack: list[tuple[int, str]] = []
    current: list[str] = []

    def flush():
        if any(l.strip() for l in current):
            sections.append(([h for _, h in path_stack], current.copy()))
        current.clear()

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            level, heading = len(m.group(1)), m.group(2).strip()
            path_stack[:] = [(l, h) for l, h in path_stack if l < level] + [(level, heading)]
        else:
            current.append(line)
    flush()

    chunks = []
    for heads, lines in sections:
        section = " > ".join(h for h in heads if h != title) or "Overview"
        buf = ""
        for block in _blocks("\n".join(lines)):
            if buf and len(buf) + len(block) > MAX_CHARS:
                chunks.append((section, buf))
                buf = ""
            buf = f"{buf}\n\n{block}" if buf else block
        if buf:
            chunks.append((section, buf))

    out = []
    for i, (section, content) in enumerate(chunks):
        out.append({
            "collection": "docs",
            "source": path.name,
            "title": title,
            "section": section,
            "content": content,
            "embed_text": f"Document: {title}\nSection: {section}\n\n{content}",
            "metadata": {**{k: v for k, v in meta.items() if k != "title"}, "chunk_index": i},
        })
    return out


_PDF_HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z][^.]{2,80}$")


def chunk_pdf(path: Path) -> list[dict]:
    """PDF chunking that keeps the page number (for the evidence viewer) and the numbered section heading.
    Optional sidecar <name>.meta.json supplies topic / doc_type / owner like markdown front matter."""
    import json
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    meta_file = path.with_suffix(".meta.json")
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    title = meta.pop("title", None) or (reader.metadata or {}).get("/Title") or path.stem.replace("_", " ").title()

    pieces = []  # (page, section, text)
    section = "Overview"
    for pno, page in enumerate(reader.pages, start=1):
        buf = ""
        for line in (page.extract_text() or "").splitlines():
            line = line.strip()
            if not line or line == title:
                continue
            if _PDF_HEADING.match(line):
                if buf:
                    pieces.append((pno, section, buf))
                section, buf = line, ""
                continue
            if buf and len(buf) + len(line) > MAX_CHARS:
                pieces.append((pno, section, buf))
                buf = ""
            buf = f"{buf}\n{line}" if buf else line
        if buf:
            pieces.append((pno, section, buf))

    return [{
        "collection": "docs", "source": path.name, "title": title, "section": sec,
        "content": text, "embed_text": f"Document: {title}\nSection: {sec}\nPage {p}\n\n{text}",
        "metadata": {"doc_type": "pdf", **meta, "page": p, "chunk_index": i},
    } for i, (p, sec, text) in enumerate(pieces)]


def chunk_file(path: Path) -> list[dict]:
    if path.suffix.lower() in (".md", ".txt"):
        return chunk_markdown(path)
    if path.suffix.lower() == ".pdf":
        return chunk_pdf(path)
    return []
