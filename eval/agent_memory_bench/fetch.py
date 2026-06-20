from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any


HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"


def fetch_hf_rows(
    *,
    dataset: str,
    config: str,
    split: str = "test",
    offset: int = 0,
    length: int = 5,
    timeout_s: float = 90.0,
    retries: int = 3,
    retry_delay_s: float = 2.0,
) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "dataset": dataset,
            "config": config,
            "split": split,
            "offset": str(offset),
            "length": str(length),
        }
    )
    url = f"{HF_ROWS_URL}?{params}"
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "akashic-agent-memory-bench/0.1"},
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(retry_delay_s * attempt, 12))
    raise RuntimeError(f"failed to fetch {dataset}/{config}: {last_error}") from last_error


def row_payloads(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [item.get("row") or {} for item in data.get("rows") or []]


def fetch_hf_all_row_payloads(
    *,
    dataset: str,
    config: str,
    split: str = "test",
    limit: int = 0,
    page_size: int = 100,
    timeout_s: float = 90.0,
    retries: int = 3,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        length = page_size
        if limit > 0:
            remaining = limit - len(rows)
            if remaining <= 0:
                break
            length = min(length, remaining)
        data = fetch_hf_rows(
            dataset=dataset,
            config=config,
            split=split,
            offset=offset,
            length=length,
            timeout_s=timeout_s,
            retries=retries,
        )
        if total is None:
            total = int(data.get("num_rows_total") or 0)
        page_rows = row_payloads(data)
        if not page_rows:
            break
        rows.extend(page_rows)
        offset += len(page_rows)
    return rows
