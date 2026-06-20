from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


DATASETS = {
    "groupmembench": {
        "repo": "kimperyang/GroupMemBench",
        "files": [
            "README.md",
            "data/final/Finance/synthetic_domain_channels_rolevariants_Finance.json",
            "data/final/Technology/synthetic_domain_channels_rolevariants_Technology.json",
            "data/final/Healthcare/synthetic_domain_channels_rolevariants_Healthcare.json",
            "data/final/Manufacturing/synthetic_domain_channels_rolevariants_Manufacturing.json",
        ],
    },
    "evermembench_dynamic": {
        "repo": "EverMind-AI/EverMemBench-Dynamic",
        "files": [
            "README.md",
            "EverMemBench_Dialogues.json",
            "EverMemBench_Dialogues_1m.json",
            "EverMemBench_QAR.json",
            "EverMemBench_QAR_1m.json",
            "profiles.json",
            "unique_profiles.json",
            "01/dialogue.json",
            "01/qa_01.json",
            "02/dialogue.json",
            "02/qa_02.json",
            "03/dialogue.json",
            "03/qa_03.json",
            "04/dialogue.json",
            "04/qa_04.json",
            "05/dialogue.json",
            "05/qa_05.json",
            "dataset/004/dialogue_en.json",
            "dataset/004/qa_004.json",
            "dataset/005/dialogue_en.json",
            "dataset/005/qa_005.json",
            "dataset/010/dialogue_en.json",
            "dataset/010/qa_010.json",
            "dataset/011/dialogue_en.json",
            "dataset/011/qa_011.json",
            "dataset/016/dialogue_en.json",
            "dataset/016/qa_016.json",
        ],
    },
    "socialmembench": {
        "repo": "anon4data/socialmembench",
        "files": [
            "README.md",
            "networks.parquet",
            "personas.parquet",
            "conversations.parquet",
            "qa.parquet",
        ],
    },
}


def _url(repo: str, filename: str) -> str:
    return f"https://huggingface.co/datasets/{repo}/resolve/main/{filename}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, *, retries: int, timeout_s: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()

    cmd = [
        "curl.exe",
        "--fail",
        "--location",
        "--retry",
        str(retries),
        "--retry-delay",
        "3",
        "--connect-timeout",
        "30",
        "--max-time",
        str(timeout_s),
        "--output",
        str(tmp),
        url,
    ]
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        cmd[1:1] = ["--proxy", proxy]

    last_error: subprocess.CalledProcessError | None = None
    for attempt in range(1, retries + 1):
        try:
            subprocess.run(cmd, check=True)
            tmp.replace(dest)
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if tmp.exists():
                tmp.unlink()
            if attempt < retries:
                time.sleep(min(5 * attempt, 20))
    raise RuntimeError(f"download failed after {retries} attempts: {url}") from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description="Download full benchmark datasets.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("eval/datasets"),
        help="Dataset root directory.",
    )
    parser.add_argument(
        "--dataset",
        choices=[*DATASETS.keys(), "all"],
        default="all",
    )
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    names = list(DATASETS) if args.dataset == "all" else [args.dataset]
    manifest: dict[str, list[dict[str, object]]] = {}

    for name in names:
        spec = DATASETS[name]
        repo = spec["repo"]
        target_root = args.output_dir / name
        manifest[name] = []
        print(f"== {name} ==")
        for filename in spec["files"]:
            dest = target_root / filename
            if dest.exists() and dest.stat().st_size > 0 and not args.force:
                print(f"skip {filename} ({dest.stat().st_size} bytes)")
            else:
                print(f"download {filename}")
                _download(_url(repo, filename), dest, retries=args.retries, timeout_s=args.timeout)
            manifest[name].append(
                {
                    "repo": repo,
                    "file": filename,
                    "path": str(dest),
                    "bytes": dest.stat().st_size,
                    "sha256": _sha256(dest),
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
