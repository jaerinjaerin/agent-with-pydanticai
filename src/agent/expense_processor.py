"""영수증 이미지 분석 → NaverWorks 비용처리 JSON 생성 모듈.

PydanticAI Agent + 구조화 출력으로 타입 안전한 파싱을 수행한다.
"""

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models.anthropic import AnthropicModel


class ExpenseResult(BaseModel):
    amount: str = Field(description="매입금액 (숫자만). 매입금액이 없으면 총 결제금액 사용.")
    date: str = Field(description="yyyy.mm.dd 형식 날짜")
    place: str = Field(description="사용처/가맹점명")
    item: str = Field(description="품목/용도")
    expenseCategory: str = Field(description="식대|교통비|소모품비|회의비|기타 중 하나")
    project: str = Field(default="", description="프로젝트명 (알 수 없으면 빈 문자열)")


expense_agent = Agent(
    model=AnthropicModel("claude-haiku-4-5-20251001"),
    output_type=ExpenseResult,
    system_prompt=(
        "영수증 이미지를 분석하여 비용 정보를 추출하세요.\n"
        "금액은 '매입금액'을 사용하세요. '승인금액'이 아닌 '매입금액'입니다.\n"
        "매입금액이 없으면 총 결제금액을 사용하세요.\n"
        "expenseCategory는 식대, 교통비, 소모품비, 회의비, 기타 중 하나를 선택하세요."
    ),
)


def analyze_receipt(image_bytes: bytes, mime_type: str) -> ExpenseResult:
    """Claude Vision으로 영수증을 분석하여 ExpenseResult를 반환한다."""
    result = expense_agent.run_sync(
        [
            "이 영수증을 분석해주세요.",
            BinaryContent(data=image_bytes, media_type=mime_type),
        ]
    )
    return result.output
