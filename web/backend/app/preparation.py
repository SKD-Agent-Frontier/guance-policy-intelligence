import asyncio
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import ValidationError

from app.preparation_models import (
    CompletedQuestion,
    Condition,
    ConditionExecutionStatus,
    PreparationOption,
    PreparationState,
    PreparedRequest,
    ResearchMode,
    ResearchPrepareRequest,
    ResearchPrepareResponse,
    TimeScope,
)

INVALID_DETAIL = "请求参数无效"
CONFLICT_DETAIL = "研究准备状态已失效或冲突"
TIME_WARNING = "不保证按发布日期精确过滤"
PERIODS = {
    "十一五": ("2006", "2010"),
    "十二五": ("2011", "2015"),
    "十三五": ("2016", "2020"),
    "十四五": ("2021", "2025"),
    "十五五": ("2026", "2030"),
}
ADMIN_AREAS = (
    "北京市", "天津市", "上海市", "重庆市", "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省", "河南省", "湖北省", "湖南省",
    "广东省", "海南省", "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省", "台湾省",
    "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区",
    "苏州市", "杭州市", "南京市", "广州市", "深圳市", "成都市", "武汉市", "西安市", "郑州市",
    "长沙市", "合肥市", "福州市", "厦门市", "济南市", "青岛市", "大连市", "宁波市", "无锡市",
)


class PreparationStateError(Exception):
    def __init__(self, detail: str = CONFLICT_DETAIL):
        self.detail = detail
        super().__init__(detail)


@dataclass
class _Entry:
    state_id: str
    mode: ResearchMode
    original_request: str
    topic: str | None
    time_scope: TimeScope | None
    area: str | None
    area_text: str | None
    base_time_text: str | None
    dimensions: list[str]
    time_text: str | None
    area_unrestricted: bool
    time_unrestricted: bool
    exact_time_pending: bool
    exact_time_confirmed: bool
    completed_questions: list[CompletedQuestion]
    clarification_count: int
    revision: int
    expires_at: datetime
    response: ResearchPrepareResponse | None = None
    previous: tuple[int, ResearchMode, str, ResearchPrepareResponse] | None = None
    question_text: str = ""


class ResearchPreparationService:
    def __init__(
        self,
        *,
        ttl_seconds: float = 1800,
        max_states: int = 1000,
        max_clarifications: int = 5,
    ):
        if ttl_seconds <= 0 or max_states < 1 or max_clarifications < 1:
            raise ValueError("invalid preparation service limits")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_states = max_states
        self._max_clarifications = max_clarifications
        self._states: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    async def prepare(self, payload: ResearchPrepareRequest | dict) -> ResearchPrepareResponse:
        try:
            request = payload if isinstance(payload, ResearchPrepareRequest) else ResearchPrepareRequest.model_validate(payload)
        except (ValidationError, ValueError, TypeError) as exc:
            raise PreparationStateError(INVALID_DETAIL) from exc

        async with self._lock:
            now = datetime.now(UTC)
            self._prune(now)
            if request.state is None:
                return self._create(request, now)

            entry = self._states.get(request.state.state_id)
            if entry is None or entry.expires_at <= now:
                raise PreparationStateError()
            if entry.mode != request.mode:
                raise PreparationStateError()
            previous = entry.previous
            if previous and previous[:3] == (request.state.revision, request.mode, request.message):
                return previous[3]
            if request.state.revision != entry.revision:
                raise PreparationStateError()

            entry.previous = (entry.revision, entry.mode, request.message, None)  # Filled after response construction.
            entry.revision += 1
            entry.clarification_count += 1
            self._apply_message(entry, request.message)
            response = self._render(entry, request.message, now)
            entry.previous = (entry.revision - 1, entry.mode, request.message, response)
            entry.response = response
            self._states.move_to_end(request.state.state_id)
            return response

    def _prune(self, now: datetime) -> None:
        expired = [key for key, value in self._states.items() if value.expires_at <= now]
        for key in expired:
            del self._states[key]

    def _create(self, request: ResearchPrepareRequest, now: datetime) -> ResearchPrepareResponse:
        while len(self._states) >= self._max_states:
            self._states.popitem(last=False)
        state_id = uuid4().hex
        entry = _Entry(
            state_id=state_id,
            mode=request.mode,
            original_request=request.message,
            topic=self._topic(request.message),
            time_scope=None,
            area=None,
            area_text=None,
            base_time_text=None,
            dimensions=[],
            time_text=None,
            area_unrestricted=False,
            time_unrestricted=False,
            exact_time_pending=False,
            exact_time_confirmed=False,
            completed_questions=[],
            clarification_count=0,
            revision=0,
            expires_at=now + self._ttl,
            question_text=request.message,
        )
        self._apply_message(entry, request.message, initial=True)
        response = self._render(entry, request.message, now)
        entry.response = response
        self._states[state_id] = entry
        return response

    def _apply_message(self, entry: _Entry, message: str, *, initial: bool = False) -> None:
        was_exact_time_pending = entry.exact_time_pending
        updated_time = False
        prior_topic = entry.topic
        topic = self._topic(message)
        awaiting_topic = entry.response and any(
            condition.id == "topic" for condition in entry.response.state.pending_conditions
        )
        if topic and (initial or awaiting_topic or any(word in message for word in ("改为", "改成", "主题", "梳理", "整理"))):
            entry.topic = topic
            if awaiting_topic and not initial:
                entry.completed_questions.append(CompletedQuestion(question_id="topic", answer=message))
        elif prior_topic:
            entry.topic = prior_topic

        if re.search(r"不限(?:于)?(?:时间|年份|日期)|不限定(?:时间|年份|日期)", message):
            entry.time_unrestricted = True
            entry.time_scope = TimeScope(start=None, end=None, precision="unspecified", timezone=None)
            entry.time_text = "不限时间"
        elif time_match := self._time(message):
            updated_time = True
            entry.time_scope, entry.time_text, exact = time_match
            entry.time_unrestricted = False
            entry.exact_time_pending = exact and not entry.exact_time_confirmed
            if initial:
                entry.base_time_text = entry.time_text

        nationwide = re.search(r"全国(?:范围(?:内)?)?", message) is not None
        if nationwide:
            entry.area = None
            entry.area_text = "全国"
            entry.area_unrestricted = True
        elif re.search(r"不限(?:于)?(?:地区|地域|范围)|不限定(?:地区|地域)", message):
            entry.area = None
            entry.area_text = None
            entry.area_unrestricted = True
        elif areas := self._areas(message):
            entry.area = areas[0] if len(areas) == 1 else None
            entry.area_text = "、".join(areas)
            entry.area_unrestricted = False
        elif area := self._explicit_area(message):
            entry.area = None
            entry.area_text = area
            entry.area_unrestricted = False

        if dims := self._dimensions(message):
            entry.dimensions = dims
        awaiting_aspect = entry.response and any(
            condition.id == "aspect" for condition in entry.response.state.pending_conditions
        )
        if entry.mode == "qa" and awaiting_aspect and not initial:
            aspect_answer = message.replace(entry.time_text, "") if updated_time and entry.time_text else message
            aspect_answer = aspect_answer.strip(" ，,。；;、")
            if aspect_answer:
                entry.completed_questions.append(
                    CompletedQuestion(question_id="aspect", answer=aspect_answer)
                )

        if not initial:
            if entry.exact_time_pending and re.search(r"确认|继续|按原条件|照此", message):
                entry.exact_time_pending = False
                entry.exact_time_confirmed = True
                entry.completed_questions.append(
                    CompletedQuestion(question_id="continue_request_only", answer=message)
                )
            elif was_exact_time_pending and updated_time and re.search(r"调整|改为|改成", message):
                entry.completed_questions.append(
                    CompletedQuestion(question_id="publication_time", answer=message)
                )
            elif entry.mode == "organize" and "scope" not in {item.question_id for item in entry.completed_questions}:
                if entry.area or entry.area_unrestricted or entry.time_text or entry.time_unrestricted or entry.dimensions:
                    entry.completed_questions.append(CompletedQuestion(question_id="scope", answer=message))

        entry.question_text = self._compose_question(entry, message, initial)

    @staticmethod
    def _is_question(message: str) -> bool:
        return any(
            mark in message
            for mark in ("？", "?", "什么", "如何", "为什么", "怎样", "哪些", "查询", "请问")
        )

    @staticmethod
    def _time(message: str):
        for name, (start, end) in PERIODS.items():
            if name in message:
                return TimeScope(start=start, end=end, precision="range", timezone=None), name, False
        if match := re.search(r"(\d{4})\s*(?:年)?\s*[—–-至到]\s*(\d{4})\s*年?", message):
            return TimeScope(start=match.group(1), end=match.group(2), precision="range", timezone=None), match.group(0), False
        if match := re.search(r"(截至\s*\d{4}年?|\d{4}年?(?:以前|之前|前|之后|以后|以来))", message):
            return TimeScope(start=None, end=None, precision="unspecified", timezone=None), match.group(1), False
        if match := re.search(
            r"(\d{4}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}时\d{1,2}分(?:\d{1,2}秒)?)?)",
            message,
        ):
            date_text = match.group(1)
            return TimeScope(start=date_text, end=date_text, precision="exact", timezone=None), date_text, True
        if match := re.search(
            r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?(?:Z|[+-]\d{2}:?\d{2})?)",
            message,
        ):
            date_text = match.group(1)
            timezone = re.search(r"(Z|[+-]\d{2}:?\d{2})$", date_text)
            timestamp = date_text[: -len(timezone.group(1))] if timezone else date_text
            return (
                TimeScope(
                    start=timestamp,
                    end=timestamp,
                    precision="exact",
                    timezone=timezone.group(1) if timezone else None,
                ),
                date_text,
                True,
            )
        if match := re.search(r"(\d{4})年", message):
            return TimeScope(start=match.group(1), end=match.group(1), precision="year", timezone=None), match.group(0), False
        if "近十年" in message:
            return TimeScope(start=None, end=None, precision="range", timezone=None), "近十年", True
        return None

    @staticmethod
    def _areas(message: str) -> list[str]:
        matches = [(message.find(area), area) for area in ADMIN_AREAS if area in message]
        return [area for _, area in sorted(matches)]

    @staticmethod
    def _explicit_area(message: str) -> str | None:
        match = re.search(
            r"(?:地区|区域|范围|位于|限定在|限定为)(?:为)?\s*[:：]?\s*"
            r"([\u4e00-\u9fff]{2,8}(?:省|市|区|县|自治州|盟|旗))",
            message,
        )
        return match.group(1) if match else None

    @staticmethod
    def _dimensions(message: str) -> list[str]:
        match = re.search(r"(?:按|按照)(.+?)(?:分类|梳理|整理|分析|汇总|$)", message)
        if not match:
            return []
        text = re.sub(r"^(?:维度|类别|方面)[:：]?", "", match.group(1)).strip(" ，,、和及")
        return list(dict.fromkeys(part.strip() for part in re.split(r"[、，,和及与]", text) if part.strip()))

    @staticmethod
    def _topic(message: str) -> str | None:
        text = message.strip()
        text = re.sub(r"^(?:研究)?主题\s*(?:改为|改成|调整为)\s*", "", text)
        text = re.sub(r"^(?:改为|改成|调整为)\s*", "", text)
        text = re.sub(r"^(?:请)?(?:帮我)?(?:梳理|整理|归纳|研究|分析|查询|查找|了解|问一下)\s*", "", text)
        if time_match := ResearchPreparationService._time(message):
            text = text.replace(time_match[1], "")
        if area := ResearchPreparationService._explicit_area(message):
            text = re.sub(
                r"(?:地区|区域|范围|位于|限定在|限定为)(?:为)?\s*[:：]?\s*"
                + re.escape(area),
                "",
                text,
            )
        text = re.sub(r"(?:不限(?:于)?(?:时间|年份|日期|地区|地域)|不限定(?:时间|年份|日期|地区|地域)).*$", "", text)
        text = re.sub(r"(?:限定在?|范围为?)\s*\d{4}.*$", "", text)
        text = re.sub(r"(?:按|按照).*$", "", text)
        for area in ResearchPreparationService._areas(message):
            text = text.replace(area, "")
        text = re.sub(r"^全国(?:范围(?:内)?)?", "", text).strip(" ，,。；;：:")
        text = re.sub(r"^(?:梳理|整理|归纳|研究|分析|查询|查找|了解)\s*", "", text)
        text = text.strip("和及与并")
        return None if text in {"政策", "政策梳理", "相关政策"} else text or None

    def _compose_question(self, entry: _Entry, message: str, initial: bool) -> str:
        if initial and entry.mode != "organize":
            return message
        if entry.mode == "qa":
            question = entry.original_request
            if entry.base_time_text and entry.time_text and entry.time_text != entry.base_time_text:
                question = question.replace(entry.base_time_text, entry.time_text)
            elif entry.time_text and not entry.base_time_text:
                question += f"；时间范围：{entry.time_text}"
            aspect = next((item.answer for item in entry.completed_questions if item.question_id == "aspect"), None)
            return f"{question}；关注{aspect}" if aspect else question
        topic = entry.topic or "政策"
        area = entry.area_text or entry.area
        if entry.area_unrestricted:
            area_label = "全国范围内" if entry.area_text == "全国" else "不限地区"
            question = f"{area_label}梳理{topic}"
        else:
            question = f"{area}范围内梳理{topic}" if area else "梳理" + topic
        if entry.time_text:
            question += "，" + entry.time_text
        if entry.dimensions:
            question += "，按" + "、".join(entry.dimensions) + "分类"
        return question

    def _render(self, entry: _Entry, latest_message: str, now: datetime) -> ResearchPrepareResponse:
        unsupported = entry.mode not in {"qa", "organize"}
        options: list[PreparationOption] = []
        pending: list[Condition] = []
        if (
            entry.mode == "qa"
            and entry.topic
            and not self._is_question(entry.original_request)
            and "aspect" not in {item.question_id for item in entry.completed_questions}
        ):
            options.append(PreparationOption(
                id="aspect", question="你想了解这个主题的哪个方面？", choices=["概念与发展", "产业政策", "发展现状", "其他"]
            ))
            pending.append(Condition(id="aspect", text="希望了解的方面"))
        if entry.mode == "organize" and not entry.topic:
            options.append(PreparationOption(id="topic", question="请说明要整理的政策主题。", choices=[]))
            pending.append(Condition(id="topic", text="待明确研究主题"))
        elif entry.mode == "organize" and not (
            entry.area or entry.area_unrestricted or entry.time_text or entry.time_unrestricted or entry.dimensions
        ):
            options.append(PreparationOption(
                id="scope", question="希望覆盖哪些地区和时间，并按哪些维度整理？", choices=[]
            ))
            pending.extend([Condition(id="scope", text="待明确整理范围和维度")])
        if entry.exact_time_pending:
            options = [PreparationOption(
                id="continue_request_only", question="该精确时间无法保证按发布日期过滤，是否按原条件作为文字要求继续，或调整时间范围？", choices=["确认继续", "调整条件"]
            )]
            pending = [Condition(id="publication_time", text=entry.time_text or "待确认的时间条件")]

        blocked_limit = entry.clarification_count >= self._max_clarifications and bool(options)
        if unsupported:
            status = "blocked"
            message = f"{entry.mode} 当前不能通过研究准备接口执行。你可以另建 qa 或 organize 模式的请求。"
        elif blocked_limit:
            status = "blocked"
            message = "澄清轮数已达上限，请整理已知条件后新建请求。"
            options = []
            pending = []
        elif options:
            status = "needs_clarification"
            message = options[0].question
        else:
            status = "ready"
            message = "研究请求已准备完成。"

        statuses = self._statuses(entry, pending)
        warnings = (
            [TIME_WARNING]
            if entry.time_text and entry.time_text not in PERIODS and not entry.time_unrestricted
            else []
        )
        if entry.mode == "qa" and re.search(
            r"(?:列出|罗列|清单).{0,12}(?:全部|所有)|(?:全部|所有)(?:的)?(?:政策|文件)|"
            r"准确(?:统计|篇数|数量)|(?:总共|一共|共有|总计).{0,8}多少篇|多少篇(?:政策|文件)?",
            entry.original_request,
        ):
            status = "blocked"
            message = "当前 Top-K 全库能力无法保证返回全部政策或准确篇数。"
            options = []
            pending = []

        state = PreparationState(
            state_id=entry.state_id,
            revision=entry.revision,
            expires_at=entry.expires_at.isoformat().replace("+00:00", "Z"),
            mode=entry.mode,
            original_request=entry.original_request,
            topic=entry.topic,
            time_scope=entry.time_scope or TimeScope(start=None, end=None, precision="unspecified", timezone=None),
            area=entry.area,
            dimensions=entry.dimensions,
            confirmed_conditions=[Condition(id=s.condition_id, text=s.value or s.reason) for s in statuses if s.status in {"executable_filter", "request_only", "unrestricted"}],
            pending_conditions=pending,
            completed_questions=entry.completed_questions,
            clarification_count=entry.clarification_count,
            condition_statuses=statuses,
        )
        request = None
        if status == "ready":
            question = self._compose_question(entry, latest_message, initial=False)
            request = PreparedRequest(task_type=entry.mode, question=question, dimensions=entry.dimensions or None)
        return ResearchPrepareResponse(
            status=status, mode=entry.mode, message=message, options=options, state=state,
            request=request, warnings=warnings,
        )

    @staticmethod
    def _statuses(entry: _Entry, pending: list[Condition]) -> list[ConditionExecutionStatus]:
        values: list[ConditionExecutionStatus] = []
        if entry.area_unrestricted:
            area_value = "全国范围" if entry.area_text == "全国" else "不限地区"
            values.append(ConditionExecutionStatus(
                condition_id="area", status="unrestricted", value=area_value,
                reason="由用户明确选择全国范围" if entry.area_text == "全国" else "由用户确认开放地区范围",
            ))
        elif entry.area:
            values.append(ConditionExecutionStatus(
                condition_id="area", status="executable_filter", value=entry.area,
                reason="单一行政地区可由现有确定性路由识别",
            ))
        elif entry.area_text:
            values.append(ConditionExecutionStatus(
                condition_id="area", status="request_only", value=entry.area_text,
                reason=(
                    "多个地区不能作为单一地区硬过滤条件"
                    if "、" in entry.area_text
                    else "该地区表达未能与可执行地区目录安全匹配"
                ),
            ))
        if entry.time_unrestricted:
            values.append(ConditionExecutionStatus(
                condition_id="time_scope", status="unrestricted", value="不限时间", reason="由用户确认开放时间范围",
            ))
        elif entry.time_text:
            period = entry.time_text in PERIODS
            status = "pending" if entry.exact_time_pending else "executable_filter" if period else "request_only"
            reason = "当前路由支持的具名五年规划时期" if period else "时间表述不保证按发布日期精确过滤"
            values.append(ConditionExecutionStatus(
                condition_id="time_scope", status=status, value=entry.time_text, reason=reason,
            ))
        for condition in pending:
            if condition.id in {item.condition_id for item in values}:
                continue
            values.append(ConditionExecutionStatus(
                condition_id=condition.id, status="pending", value=None, reason="等待用户补充或确认",
            ))
        return values
