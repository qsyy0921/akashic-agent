import json
import pathlib
import re
import ssl
import time
import urllib.parse
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parent
BIB_PATH = ROOT / "akashic-topconf-2025-2026.bib"
OUT_DIR = ROOT / "pdfs"
MANIFEST_PATH = OUT_DIR / "download-manifest.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8",
}

PDF_OVERRIDES = {
    "huang_etal_2026_mempal": [
        "https://ojs.aaai.org/index.php/AAAI/article/view/40385/44346",
        "https://arxiv.org/pdf/2511.13410",
    ],
    "du_etal_2026_memguide": [
        "https://ojs.aaai.org/index.php/AAAI/article/view/40313/44274",
        "https://arxiv.org/pdf/2505.20231",
    ],
    "zhou_etal_2026_mem1": [
        "https://arxiv.org/pdf/2506.15841",
        "https://openreview.net/pdf?id=XY8AaxDSLb",
    ],
    "qian_etal_2025_memorag": [
        "https://arxiv.org/pdf/2409.05591",
        "https://chien.io/files/www25.pdf",
        "https://dl.acm.org/doi/pdf/10.1145/3696410.3714805",
    ],
}


def parse_bib_entries(text: str) -> list[dict[str, str]]:
    entries = []
    for match in re.finditer(r"@\w+\{([^,]+),\s*(.*?)(?=\n@\w+\{|\Z)", text, re.S):
        key, body = match.group(1), match.group(2)

        def field(name: str) -> str:
            found = re.search(
                r"\n\s*" + re.escape(name) + r"\s*=\s*\{(.*?)\}\s*,?",
                body,
                re.S,
            )
            return re.sub(r"\s+", " ", found.group(1)).strip() if found else ""

        entries.append(
            {
                "bibkey": key,
                "title": field("title"),
                "url": field("url"),
                "doi": field("doi"),
                "year": field("year"),
                "booktitle": field("booktitle"),
            }
        )
    return entries


def request_url(url: str, timeout: int = 30):
    context = ssl.create_default_context()
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout, context=context)


def is_pdf(data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF")


def ojs_candidates(article_url: str) -> list[str]:
    try:
        html = request_url(article_url, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception:
        return []

    links = re.findall(r"href=[\"']([^\"']+)[\"']", html, re.I)
    candidates: list[str] = []
    for href in links:
        full = urllib.parse.urljoin(article_url, href)
        lower = full.lower()
        if "/article/download/" in lower or ("download" in lower and "aaai" in lower):
            if full not in candidates:
                candidates.append(full)
    return candidates


def pdf_candidates(entry: dict[str, str]) -> list[str]:
    url = entry["url"]
    doi = entry["doi"]
    candidates: list[str] = list(PDF_OVERRIDES.get(entry["bibkey"], []))

    if "openreview.net/forum?id=" in url:
        paper_id = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("id", [""])[0]
        if paper_id:
            candidates.append(f"https://openreview.net/pdf?id={paper_id}")

    if "aclanthology.org/" in url:
        anthology_id = url.rstrip("/").split("/")[-1]
        candidates.append(f"https://aclanthology.org/{anthology_id}.pdf")

    if "ojs.aaai.org" in url:
        candidates.extend(ojs_candidates(url))

    if "dl.acm.org/doi" in url and doi:
        candidates.append(f"https://dl.acm.org/doi/pdf/{doi}")

    if url.lower().endswith(".pdf"):
        candidates.append(url)

    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def download_one(entry: dict[str, str]) -> dict:
    destination = OUT_DIR / f"{entry['bibkey']}.pdf"
    if destination.exists() and destination.stat().st_size > 10_000 and is_pdf(destination.read_bytes()):
        return {
            **entry,
            "status": "exists",
            "pdf_path": str(destination.relative_to(ROOT)),
            "pdf_url": None,
            "size": destination.stat().st_size,
        }

    errors = []
    candidates = pdf_candidates(entry)
    for url in candidates:
        try:
            with request_url(url, timeout=60) as response:
                final_url = response.geturl()
                content_type = response.headers.get("content-type", "")
                data = response.read()
            if is_pdf(data):
                destination.write_bytes(data)
                return {
                    **entry,
                    "status": "downloaded",
                    "pdf_path": str(destination.relative_to(ROOT)),
                    "pdf_url": final_url,
                    "size": len(data),
                }
            errors.append(f"not_pdf:{url}:content_type={content_type}:bytes={len(data)}")
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    return {
        **entry,
        "status": "failed",
        "pdf_path": str(destination.relative_to(ROOT)),
        "pdf_url": None,
        "size": 0,
        "candidates": candidates,
        "errors": errors,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = parse_bib_entries(BIB_PATH.read_text(encoding="utf-8"))
    results = []
    for entry in entries:
        results.append(download_one(entry))
        time.sleep(0.4)

    MANIFEST_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "total": len(results),
        "downloaded_or_exists": sum(1 for item in results if item["status"] in {"downloaded", "exists"}),
        "failed": [item["bibkey"] for item in results if item["status"] == "failed"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
