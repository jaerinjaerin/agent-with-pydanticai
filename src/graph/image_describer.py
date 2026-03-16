"""
이미지 설명 생성 모듈.

Claude Vision API로 이미지를 분석하여 텍스트 설명을 생성한다.
설명은 data/image_descriptions.json에 캐싱하여 재빌드 시 API 비용을 절약한다.
"""

import base64
import json
import os
import time
from pathlib import Path

import anthropic

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DESCRIPTIONS_PATH = DATA_DIR / "image_descriptions.json"

# 이미지 확장자
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

SYSTEM_PROMPT = (
    "이 이미지의 내용을 한국어로 상세히 설명해주세요. "
    "표, 차트, 텍스트, 다이어그램, UI 스크린샷 등 모든 정보를 포함하세요. "
    "이미지에 텍스트가 있으면 가능한 한 그대로 옮겨 적어주세요."
)


def _load_cache() -> dict[str, str]:
    """캐시된 이미지 설명을 로드한다."""
    if DESCRIPTIONS_PATH.exists():
        with open(DESCRIPTIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    """이미지 설명 캐시를 저장한다."""
    DESCRIPTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(DESCRIPTIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _get_media_type(path: Path) -> str:
    """파일 확장자에서 MIME 타입을 반환한다."""
    ext = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/png")


def describe_image(
    image_path: Path,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """이미지를 Claude Vision API로 분석하여 텍스트 설명을 생성한다."""
    if not image_path.exists():
        return ""

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": _get_media_type(image_path),
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                    },
                ],
            }
        ],
    )
    return response.content[0].text


def describe_images_batch(
    image_paths: list[str],
    model: str = "claude-haiku-4-5-20251001",
    project_root: Path | None = None,
) -> dict[str, str]:
    """여러 이미지를 배치로 설명 생성. {경로: 설명} 딕셔너리 반환.

    이미 설명이 캐시에 존재하는 이미지는 건너뛴다.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parents[2]

    cache = _load_cache()
    new_count = 0

    for i, rel_path in enumerate(image_paths):
        if rel_path in cache:
            continue

        abs_path = project_root / rel_path
        if not abs_path.exists():
            print(f"  [skip] 이미지 파일 없음: {rel_path}")
            continue

        try:
            description = describe_image(abs_path, model=model)
            cache[rel_path] = description
            new_count += 1
            if new_count % 5 == 0:
                _save_cache(cache)
                print(f"  ... {new_count}장 설명 생성 완료 (전체 {i + 1}/{len(image_paths)})")
            # API 속도 제한 방지
            time.sleep(0.5)
        except Exception as e:
            print(f"  [error] 이미지 설명 생성 실패 ({rel_path}): {e}")
            cache[rel_path] = ""

    _save_cache(cache)
    print(f"  이미지 설명 생성 완료: 신규 {new_count}장 (캐시 {len(cache) - new_count}장)")
    return cache


def collect_all_image_paths(documents: list[dict]) -> list[str]:
    """문서 목록에서 모든 이미지 경로를 수집한다."""
    paths = []
    for doc in documents:
        for att in doc.get("attachments", []):
            for img_path in att.get("images", []):
                if img_path not in paths:
                    paths.append(img_path)
    return paths
