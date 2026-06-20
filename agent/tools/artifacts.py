from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.tools.base import normalize_tool_result

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_LOCAL_IMAGE_RE = re.compile(
    r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]+\.(?:png|jpe?g|webp|gif)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ToolArtifact:
    type: str
    path: str
    mime: str = ""
    url: str = ""
    source_tool: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": self.type,
            "path": self.path,
        }
        if self.mime:
            data["mime"] = self.mime
        if self.url:
            data["url"] = self.url
        if self.source_tool:
            data["source_tool"] = self.source_tool
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


def extract_tool_artifacts(tool_name: str, result: object) -> list[ToolArtifact]:
    normalized = normalize_tool_result(result)
    text = normalized.text if isinstance(normalized.text, str) else ""
    artifacts: list[ToolArtifact] = []
    parsed = _parse_json_object(text)
    if isinstance(parsed, dict):
        _append_structured_artifacts(artifacts, tool_name, parsed.get("artifacts"))
        _append_legacy_image_artifacts(artifacts, tool_name, parsed)
    for match in _LOCAL_IMAGE_RE.findall(text):
        _append_image_artifact(artifacts, tool_name, match)
    return artifacts


def artifacts_to_dicts(artifacts: list[ToolArtifact]) -> list[dict[str, Any]]:
    return [artifact.as_dict() for artifact in artifacts]


def image_paths_from_artifacts(artifacts: list[ToolArtifact] | list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for artifact in artifacts:
        if isinstance(artifact, ToolArtifact):
            artifact_type = artifact.type
            path = artifact.path
        elif isinstance(artifact, dict):
            artifact_type = str(artifact.get("type") or "")
            path = str(artifact.get("path") or artifact.get("local_path") or "")
        else:
            continue
        if artifact_type != "image":
            continue
        if path and path not in paths:
            paths.append(path)
    return paths


def _parse_json_object(text: str) -> object:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _append_structured_artifacts(
    artifacts: list[ToolArtifact],
    tool_name: str,
    raw: object,
) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        if not isinstance(item, dict):
            continue
        artifact_type = str(item.get("type") or "").strip()
        path = str(item.get("path") or item.get("local_path") or "").strip()
        if artifact_type != "image":
            continue
        _append_image_artifact(
            artifacts,
            tool_name,
            path,
            url=str(item.get("url") or "").strip(),
            mime=str(item.get("mime") or "").strip(),
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )


def _append_legacy_image_artifacts(
    artifacts: list[ToolArtifact],
    tool_name: str,
    parsed: dict[str, Any],
) -> None:
    urls = parsed.get("urls")
    url_by_index = urls if isinstance(urls, list) else []
    for key in ("images", "local_paths"):
        raw = parsed.get(key)
        if isinstance(raw, list):
            for index, item in enumerate(raw):
                url = url_by_index[index] if index < len(url_by_index) else ""
                _append_image_artifact(artifacts, tool_name, item, url=str(url or ""))
    data = parsed.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            _append_image_artifact(
                artifacts,
                tool_name,
                item.get("local_path"),
                url=str(item.get("url") or ""),
            )


def _append_image_artifact(
    artifacts: list[ToolArtifact],
    tool_name: str,
    value: object,
    *,
    url: str = "",
    mime: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    if not isinstance(value, str) or not value.strip():
        return
    path = Path(value.strip())
    if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
        return
    normalized = str(path)
    if normalized in image_paths_from_artifacts(artifacts):
        return
    artifacts.append(
        ToolArtifact(
            type="image",
            path=normalized,
            mime=mime or mimetypes.guess_type(normalized)[0] or "",
            url=url,
            source_tool=tool_name,
            metadata=dict(metadata or {}),
        )
    )
