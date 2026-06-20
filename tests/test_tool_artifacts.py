import json
from pathlib import Path

from agent.tools.artifacts import (
    extract_tool_artifacts,
    image_paths_from_artifacts,
)


def test_extract_tool_artifacts_prefers_structured_artifacts(tmp_path: Path):
    image = tmp_path / "structured.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = json.dumps(
        {
            "success": True,
            "artifacts": [
                {
                    "type": "image",
                    "path": str(image),
                    "mime": "image/png",
                    "url": "http://127.0.0.1:3208/images/structured.png",
                }
            ],
        },
        ensure_ascii=False,
    )

    artifacts = extract_tool_artifacts("image_tool", result)

    assert image_paths_from_artifacts(artifacts) == [str(image)]
    assert artifacts[0].mime == "image/png"
    assert artifacts[0].url.endswith("/structured.png")


def test_extract_tool_artifacts_supports_legacy_image_fields(tmp_path: Path):
    image = tmp_path / "legacy.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    result = json.dumps({"success": True, "images": [str(image)]}, ensure_ascii=False)

    artifacts = extract_tool_artifacts("image_tool", result)

    assert image_paths_from_artifacts(artifacts) == [str(image)]
