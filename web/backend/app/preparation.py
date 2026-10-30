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
    messages: list[str]
    topic: str | None
    time_scope: TimeScope | None
    area: str | None
    area_text: str | None
    area_conflict: bool
    area_choice_stage: str | None
    area_conflict_specific_areas: list[str]
    base_time_text: str | None
    dimensions: list[str]
    time_text: str | None
    area_unrestricted: bool
    time_unrestricted: bool
    exact_time_pending: bool
    exact_time_confirmed: bool
    ambiguous_time: bool
    ambiguous_calendar_text: str | None
    ambiguous_period: str | None
    unstructured_time_message: str | None
    unparsed_time_change: str | None
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
            entry.messages.append(request.message)
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
            messages=[request.message],
            topic=self._topic(request.message),
            time_scope=None,
            area=None,
            area_text=None,
            area_conflict=False,
            area_choice_stage=None,
            area_conflict_specific_areas=[],
            base_time_text=None,
            dimensions=[],
            time_text=None,
            area_unrestricted=False,
            time_unrestricted=False,
            exact_time_pending=False,
            exact_time_confirmed=False,
            ambiguous_time=False,
            ambiguous_calendar_text=None,
            ambiguous_period=None,
            unstructured_time_message=None,
            unparsed_time_change=None,
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
        prior_area_choice_stage = entry.area_choice_stage
        updated_time = False
        prior_topic = entry.topic
        topic = self._topic(message)
        explicit_topic_change = re.match(
            r"^(?:研究)?主题\s*(?:改为|改成|调整为)", message
        ) is not None
        awaiting_topic = entry.response and any(
            condition.id == "topic" for condition in entry.response.state.pending_conditions
        )
        if topic and (initial or awaiting_topic or explicit_topic_change):
            entry.topic = topic
            if awaiting_topic and not initial:
                entry.completed_questions.append(CompletedQuestion(question_id="topic", answer=message))
        elif prior_topic:
            entry.topic = prior_topic

        time_choice = None
        if entry.ambiguous_time and "按公历年份" in message:
            time_choice = "calendar"
            entry.time_text = entry.ambiguous_calendar_text
            entry.ambiguous_time = False
            entry.exact_time_pending = False
        elif entry.ambiguous_time and "按规划时期" in message:
            time_choice = "period"
            period_match = next((period for period in PERIODS if period in (entry.ambiguous_period or "")), None)
            if period_match:
                entry.time_text = period_match
                start, end = PERIODS[period_match]
                entry.time_scope = TimeScope(start=start, end=end, precision="range", timezone=None)
            entry.ambiguous_time = False
            entry.exact_time_pending = False
        elif entry.ambiguous_time and "两者都作为文字要求" in message:
            time_choice = "both"
            entry.ambiguous_time = False
            entry.exact_time_pending = False
        if time_choice:
            entry.unstructured_time_message = None
            entry.unparsed_time_change = None
            entry.completed_questions.append(CompletedQuestion(question_id="time_scope", answer=message))
            entry.ambiguous_calendar_text = None
            entry.ambiguous_period = None
        elif re.search(r"不限(?:于)?(?:时间|年份|日期)|不限定(?:时间|年份|日期)", message):
            entry.unstructured_time_message = None
            entry.unparsed_time_change = None
            entry.time_unrestricted = True
            entry.time_scope = TimeScope(start=None, end=None, precision="unspecified", timezone=None)
            entry.time_text = "不限时间"
        elif time_match := self._time(message):
            updated_time = True
            entry.time_scope, entry.time_text, exact, entry.ambiguous_time = time_match
            entry.unstructured_time_message = None
            entry.unparsed_time_change = None
            if entry.time_scope.precision == "unspecified" and re.search(
                r"(?<!\d)(?:\d{4}年\d{1,2}月|\d{4}-\d{2})(?!\d)", message
            ):
                entry.unstructured_time_message = message
            entry.time_unrestricted = False
            entry.exact_time_confirmed = False
            entry.exact_time_pending = exact and not entry.exact_time_confirmed
            if entry.ambiguous_time:
                candidates = entry.time_text.split("，", maxsplit=1)
                entry.ambiguous_calendar_text = next(
                    (candidate for candidate in candidates if re.search(r"\d{4}", candidate)), None
                )
                entry.ambiguous_period = next(
                    (candidate for candidate in candidates if any(period in candidate for period in PERIODS)), None
                )
            if initial:
                entry.base_time_text = entry.time_text
        elif re.search(
            r"(?:时间|年份|日期|时间范围).{0,8}(?:改为|改成|调整为)|"
            r"(?:改为|改成|调整为).{0,8}(?:时间|年份|日期|时间范围)",
            message,
        ):
            updated_time = True
            entry.unstructured_time_message = None
            entry.unparsed_time_change = message
            entry.time_scope = None
            entry.time_text = None
            entry.time_unrestricted = False
            entry.exact_time_pending = False
            entry.exact_time_confirmed = False
            entry.ambiguous_time = False
            entry.ambiguous_calendar_text = None
            entry.ambiguous_period = None

        areas = self._areas(message)
        national_scope = re.search(r"全国范围(?:内)?|全国政策", message) is not None
        national_link = bool(areas) and any(
            re.search(r"全国\s*(?:和|与|、|，|,)\s*" + re.escape(area), message)
            for area in areas
        )
        national_scope = national_scope or national_link
        area_choice_completed = False
        area_completion_id = "area_scope"
        if prior_area_choice_stage == "national_or_specific" and "全国范围" in message:
            entry.area = None
            entry.area_text = "全国范围"
            entry.area_unrestricted = False
            entry.area_conflict = False
            entry.area_choice_stage = None
            entry.area_conflict_specific_areas = []
            area_choice_completed = True
        elif prior_area_choice_stage == "national_or_specific" and "具体地区" in message:
            specific_areas = entry.area_conflict_specific_areas
            entry.area_unrestricted = False
            area_choice_completed = True
            if len(specific_areas) == 1:
                entry.area = specific_areas[0]
                entry.area_text = specific_areas[0]
                entry.area_conflict = False
                entry.area_choice_stage = None
                entry.area_conflict_specific_areas = []
            else:
                entry.area = None
                entry.area_text = "、".join(specific_areas)
                entry.area_conflict = True
                entry.area_choice_stage = "choose_specific"
        elif prior_area_choice_stage == "choose_specific" and areas:
            selected = next((area for area in areas if area in entry.area_conflict_specific_areas), None)
            if selected:
                entry.area = selected
                entry.area_text = selected
                entry.area_unrestricted = False
                entry.area_conflict = False
                entry.area_choice_stage = None
                entry.area_conflict_specific_areas = []
                area_choice_completed = True
                area_completion_id = "area_detail"
        if national_scope and areas:
            if not (prior_area_choice_stage and area_choice_completed):
                entry.area = None
                entry.area_text = "全国范围、" + "、".join(areas)
                entry.area_unrestricted = False
                entry.area_conflict = True
                entry.area_choice_stage = "national_or_specific"
                entry.area_conflict_specific_areas = areas
        elif national_scope:
            entry.area = None
            entry.area_text = "全国范围"
            entry.area_unrestricted = False
            entry.area_conflict = False
            if not (prior_area_choice_stage and area_choice_completed):
                entry.area_choice_stage = None
                entry.area_conflict_specific_areas = []
        elif re.search(r"不限(?:于)?(?:地区|地域|范围)|不限定(?:地区|地域)", message):
            entry.area = None
            entry.area_text = None
            entry.area_unrestricted = True
            entry.area_conflict = False
            entry.area_choice_stage = None
            entry.area_conflict_specific_areas = []
        elif areas:
            if not (prior_area_choice_stage and area_choice_completed):
                entry.area = areas[0] if len(areas) == 1 else None
                entry.area_text = "、".join(areas)
                entry.area_unrestricted = False
                entry.area_conflict = False
                entry.area_choice_stage = None
                entry.area_conflict_specific_areas = []
        elif area := self._explicit_area(message):
            entry.area = None
            entry.area_text = area
            entry.area_unrestricted = False
            entry.area_conflict = False
            entry.area_choice_stage = None
            entry.area_conflict_specific_areas = []

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
        if area_choice_completed and not initial:
            entry.completed_questions.append(CompletedQuestion(question_id=area_completion_id, answer=message))

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
        periods = [name for name in PERIODS if name in message]
        calendar_range = re.search(r"(\d{4})\s*(?:年)?\s*[—–-至到]\s*(\d{4})\s*年?", message)
        date_boundary = re.search(
            r"(截至\s*)(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
            r"|\d{4}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}时\d{1,2}分(?:\d{1,2}秒)?)?)",
            message,
        )
        date_match = date_boundary or re.search(
            r"(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
            r"|\d{4}年\d{1,2}月\d{1,2}日(?:\s*\d{1,2}时\d{1,2}分(?:\d{1,2}秒)?)?)",
            message,
        )
        date_text = date_boundary.group(2) if date_boundary else date_match.group(1) if date_match else None
        calendar_limit = None if date_match else re.search(
            r"(截至\s*\d{4}年?|\d{4}年?(?:以前|之前|前|之后|以后|以来))", message
        )
        month_match = None if date_match or calendar_range or calendar_limit else re.search(
            r"(?<!\d)(\d{4}年\d{1,2}月|\d{4}-\d{2})(?!\d)", message
        )
        year_match = (
            None
            if date_match or calendar_range or calendar_limit or month_match
            else re.search(r"(\d{4})年", message)
        )

        candidates: list[tuple[int, str]] = []
        if calendar_range:
            candidates.append((calendar_range.start(), calendar_range.group(0)))
        if date_match:
            candidates.append((date_match.start(), date_match.group(0)))
        if calendar_limit:
            candidates.append((calendar_limit.start(), calendar_limit.group(1)))
        if year_match:
            candidates.append((year_match.start(), year_match.group(0)))
        for period in periods:
            period_match = re.search(rf"{period}(?:规划)?(?:为背景)?", message)
            candidates.append((period_match.start(), period_match.group(0)) if period_match else (message.find(period), period))

        if len(candidates) > 1:
            candidates.sort(key=lambda candidate: candidate[0])
            value = "，".join(dict.fromkeys(candidate[1] for candidate in candidates))
            if date_match and date_text:
                timezone = re.search(r"(Z|[+-]\d{2}:?\d{2})$", date_text)
                timestamp = date_text[: -len(timezone.group(1))] if timezone else date_text
                scope = TimeScope(start=timestamp, end=timestamp, precision="exact", timezone=timezone.group(1) if timezone else None)
            elif calendar_range:
                scope = TimeScope(start=calendar_range.group(1), end=calendar_range.group(2), precision="range", timezone=None)
            elif calendar_limit:
                scope = TimeScope(start=None, end=None, precision="unspecified", timezone=None)
            elif year_match:
                scope = TimeScope(start=year_match.group(1), end=year_match.group(1), precision="year", timezone=None)
            else:
                start, end = PERIODS[periods[0]]
                scope = TimeScope(start=start, end=end, precision="range", timezone=None)
            return scope, value, False, True

        if date_match and date_text:
            timezone = re.search(r"(Z|[+-]\d{2}:?\d{2})$", date_text)
            timestamp = date_text[: -len(timezone.group(1))] if timezone else date_text
            return (
                TimeScope(start=timestamp, end=timestamp, precision="exact", timezone=timezone.group(1) if timezone else None),
                date_match.group(0),
                True,
                False,
            )
        if calendar_limit:
            return TimeScope(start=None, end=None, precision="unspecified", timezone=None), calendar_limit.group(1), False, False
        if calendar_range:
            return TimeScope(start=calendar_range.group(1), end=calendar_range.group(2), precision="range", timezone=None), calendar_range.group(0), False, False
        if month_match:
            return (
                TimeScope(start=None, end=None, precision="unspecified", timezone=None),
                month_match.group(1),
                False,
                False,
            )
        if periods:
            name = periods[0]
            start, end = PERIODS[name]
            period_match = re.search(rf"{name}(?:规划)?(?:为背景)?", message)
            return TimeScope(start=start, end=end, precision="range", timezone=None), period_match.group(0) if period_match else name, False, False
        if year_match:
            year = year_match.group(1)
            return TimeScope(start=year, end=year, precision="year", timezone=None), year_match.group(0), False, False
        if "近十年" in message:
            return TimeScope(start=None, end=None, precision="range", timezone=None), "近十年", True, False
        if relative_time := re.search(
            r"(?:近|过去|最近)\s*(?:\d+|[零〇一二两三四五六七八九十百千]+)\s*(?:年|个月|月)",
            message,
        ):
            return (
                TimeScope(start=None, end=None, precision="range", timezone=None),
                relative_time.group(0),
                True,
                False,
            )
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
        text = re.sub(r"^全国范围(?:内)?", "", text).strip(" ，,。；;：:")
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
            question = f"不限地区梳理{topic}"
        elif entry.area_text == "全国范围":
            question = f"全国范围内梳理{topic}"
        else:
            question = f"{area}范围内梳理{topic}" if area else "梳理" + topic
        if entry.time_text:
            question += "，" + entry.time_text
        if entry.dimensions:
            question += "，按" + "、".join(entry.dimensions) + "分类"
        if entry.unstructured_time_message:
            question += "；时间条件原文：" + entry.unstructured_time_message
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
        if entry.area_conflict:
            if entry.area_choice_stage == "choose_specific":
                options.append(PreparationOption(
                    id="area_detail", question="请选择要保留的具体地区。",
                    choices=entry.area_conflict_specific_areas,
                ))
            else:
                options.append(PreparationOption(
                    id="area_scope",
                    question="请求同时限定全国范围和具体地区，请确认保留哪一种范围。",
                    choices=["全国范围", "具体地区"],
                ))
            pending.append(Condition(id="area", text=entry.area_text or "地区范围冲突待确认"))
        if entry.unparsed_time_change:
            options.append(PreparationOption(
                id="time_scope",
                question="无法识别新的时间范围，请补充明确时间条件或说明不限时间。",
                choices=[],
            ))
            pending.append(Condition(id="time_scope", text=entry.unparsed_time_change))
        elif entry.ambiguous_time:
            options.append(PreparationOption(
                id="time_scope",
                question="请求同时提到公历年份和五年规划时期，请确认筛选条件；也可保留为文字要求。",
                choices=["按公历年份", "按规划时期", "两者都作为文字要求"],
            ))
            pending.append(Condition(id="time_scope", text=entry.time_text or "时间范围冲突待确认"))
        if entry.exact_time_pending:
            options.append(PreparationOption(
                id="continue_request_only", question="该精确时间无法保证按发布日期过滤，是否按原条件作为文字要求继续，或调整时间范围？", choices=["确认继续", "调整条件"]
            ))
            pending.append(Condition(id="publication_time", text=entry.time_text or "待确认的时间条件"))

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
        full_corpus_intent = None
        for message in entry.messages:
            message_intent = self._full_corpus_intent(message)
            if message_intent is not None:
                full_corpus_intent = message_intent
        if entry.mode == "qa" and full_corpus_intent is True:
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
    def _full_corpus_intent(message: str) -> bool | None:
        negative = re.search(
            r"(?:无需|不必|不要|不需要)\s*"
            r"(?:(?:列出|罗列|提供|给出|统计|汇总)\s*)?"
            r"(?:全部|所有)(?:的)?(?:列出|罗列|政策|文件)?|"
            r"(?:无需|不必|不要|不需要)\s*(?:给出|统计|汇总)?\s*"
            r"(?:完整(?:的)?(?:政策)?清单|准确(?:统计|篇数|数量)|"
            r"(?:总共|一共|共有|总计).{0,8}多少篇|多少篇(?:政策|文件)?)|"
            r"(?:只|仅)(?:分析|研究|梳理|整理)|"
            r"(?:只|仅)(?:给|提供|举出|举)\s*(?:三|三个|[1-9]\d*)\s*个?例子",
            message,
        )
        if negative:
            return False
        positive = re.search(
            r"(?:全部|所有)(?:的)?(?:列出|罗列|政策|文件)|"
            r"(?:列出|罗列|统计|汇总|提供|给出).{0,16}(?:全部|所有|完整清单|全量清单)|"
            r"完整(?:的)?(?:政策)?清单|准确(?:统计|篇数|数量)|"
            r"(?:总共|一共|共有|总计).{0,8}多少篇|多少篇(?:政策|文件)?",
            message,
        )
        return True if positive else None

    @staticmethod
    def _statuses(entry: _Entry, pending: list[Condition]) -> list[ConditionExecutionStatus]:
        values: list[ConditionExecutionStatus] = []
        if entry.area_unrestricted:
            values.append(ConditionExecutionStatus(
                condition_id="area", status="unrestricted", value="不限地区",
                reason="由用户确认开放地区范围",
            ))
        elif entry.area:
            executable_area = entry.area.endswith("省") or entry.area in {"北京市", "天津市", "上海市", "重庆市"}
            values.append(ConditionExecutionStatus(
                condition_id="area",
                status="executable_filter" if executable_area else "request_only",
                value=entry.area,
                reason=(
                    "单一省级地区可由现有确定性路由识别"
                    if executable_area
                    else "城市级地区保留在请求中，不假定不完整地区词表可硬过滤"
                ),
            ))
        elif entry.area_conflict:
            values.append(ConditionExecutionStatus(
                condition_id="area", status="pending", value=entry.area_text,
                reason="全国范围与具体地区同时出现，等待用户确认",
            ))
        elif entry.area_text:
            values.append(ConditionExecutionStatus(
                condition_id="area", status="request_only", value=entry.area_text,
                reason=("全国范围是明确国家级范围，不等同于不限地区" if entry.area_text == "全国范围" else
                        "全国范围与具体地区同时出现，等待用户确认" if entry.area_conflict else
                        "多个或未能确认的地区只保留在自然语言请求中"),
            ))
        if entry.time_unrestricted:
            values.append(ConditionExecutionStatus(
                condition_id="time_scope", status="unrestricted", value="不限时间", reason="由用户确认开放时间范围",
            ))
        elif entry.time_text:
            period = entry.time_text in PERIODS
            status = "pending" if entry.ambiguous_time or entry.exact_time_pending else "executable_filter" if period else "request_only"
            reason = (
                "公历年份与规划时期冲突，等待用户确认"
                if entry.ambiguous_time
                else "当前路由支持的具名五年规划时期"
                if period
                else "时间表述不保证按发布日期精确过滤"
            )
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
