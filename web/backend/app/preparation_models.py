from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)

ResearchMode = Literal["qa", "extract", "organize", "compare", "compare_plans"]
PreparationStatus = Literal["needs_clarification", "ready", "blocked"]
ConditionStatus = Literal["executable_filter", "request_only", "unrestricted", "pending"]
TimePrecision = Literal["exact", "year", "range", "unspecified"]

NonBlankString = Annotated[StrictStr, StringConstraints(min_length=1)]
MessageString = Annotated[StrictStr, StringConstraints(min_length=1, max_length=4000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PreparationToken(StrictModel):
    state_id: NonBlankString
    revision: Annotated[StrictInt, Field(ge=0)]


class ResearchPrepareRequest(StrictModel):
    mode: ResearchMode
    message: MessageString
    state: PreparationToken | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_state(cls, value):
        if isinstance(value, dict) and "state" in value and value["state"] is None:
            raise ValueError("state must be an object when provided")
        return value

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value


class PreparationOption(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: StrictStr
    question: StrictStr
    choices: list[StrictStr]


class TimeScope(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    start: StrictStr | None
    end: StrictStr | None
    precision: TimePrecision
    timezone: StrictStr | None


class Condition(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    id: StrictStr
    text: StrictStr


class CompletedQuestion(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    question_id: StrictStr
    answer: StrictStr


class ConditionExecutionStatus(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    condition_id: StrictStr
    status: ConditionStatus
    value: StrictStr | None
    reason: StrictStr


class PreparationState(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    state_id: StrictStr
    revision: Annotated[StrictInt, Field(ge=0)]
    expires_at: StrictStr
    mode: ResearchMode
    original_request: StrictStr
    topic: StrictStr | None
    time_scope: TimeScope | None
    area: StrictStr | None
    dimensions: list[StrictStr]
    confirmed_conditions: list[Condition]
    pending_conditions: list[Condition]
    completed_questions: list[CompletedQuestion]
    clarification_count: Annotated[StrictInt, Field(ge=0)]
    condition_statuses: list[ConditionExecutionStatus]


class PreparedRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    task_type: ResearchMode
    question: StrictStr
    targets: list[StrictStr] | None = None
    dimensions: list[StrictStr] | None = None


class ResearchPrepareResponse(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: PreparationStatus
    mode: ResearchMode
    message: StrictStr
    options: list[PreparationOption]
    state: PreparationState
    request: PreparedRequest | None
    warnings: list[StrictStr]
