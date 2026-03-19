"""영수증 이미지 분석 → NaverWorks 비용처리 JSON 생성 모듈."""

import base64
import json

import anthropic


def analyze_receipt(image_bytes: bytes, mime_type: str) -> dict:
    """Claude Vision으로 영수증을 분석하여 NaverWorksExpenseForm JSON을 반환한다."""
    client = anthropic.Anthropic()
    b64 = base64.standard_b64encode(image_bytes).decode()

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type,
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            "이 영수증을 분석하여 다음 JSON 형식으로 추출하세요. "
                            "반드시 JSON만 출력하세요.\n"
                            "금액은 '매입금액'을 사용하세요. '승인금액'이 아닌 '매입금액'입니다. "
                            "매입금액이 없으면 총 결제금액을 사용하세요.\n"
                            '{"amount": "매입금액(숫자만)", "date": "yyyy.mm.dd", '
                            '"place": "사용처/가맹점명", "item": "품목/용도", '
                            '"expenseCategory": "식대|교통비|소모품비|회의비|기타", '
                            '"project": ""}'
                        ),
                    },
                ],
            }
        ],
    )

    text = response.content[0].text
    # JSON 블록 추출 (코드 펜스로 감싸진 경우)
    if "```" in text:
        text = text.split("```")[1].removeprefix("json").strip()
    return json.loads(text)
