from pydantic import BaseModel, ConfigDict, StrictStr


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    question: StrictStr
    task_type: StrictStr = "qa"
    targets: list[StrictStr] | None = None
    dimensions: list[StrictStr] | None = None
    session_id: StrictStr | None = None
