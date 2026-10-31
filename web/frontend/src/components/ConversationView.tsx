import { useEffect, useRef, useState } from "react";
import { BookOpenText, RotateCcw } from "lucide-react";
import { ResearchForm } from "./ResearchForm";
import { Results } from "./Results";
import { EvidencePanel } from "./EvidencePanel";
import type { AdvancedDraft, Conversation, ConversationMessage } from "../conversationTypes";
import type { ResearchRequest, RunResult, TaskType } from "../types";

type Props = {
  conversation: Conversation;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string) => void;
  onAdvancedSubmit: (request: ResearchRequest) => void;
  onRetry: () => void;
  onRestartPreparation: () => void;
  onAdvancedDraftChange: (patch: Partial<AdvancedDraft>) => void;
  onSpecifyPolicies: () => void;
  onOpenCatalog: () => void;
};

const labels: Record<TaskType, string> = {
  qa: "政策问答",
  extract: "要素提取",
  organize: "文献梳理",
  compare: "政策比较",
  compare_plans: "多方案分析",
};

export function ConversationView({ conversation, onModeChange, onSend, onAdvancedSubmit, onRetry, onRestartPreparation, onAdvancedDraftChange, onSpecifyPolicies, onOpenCatalog }: Props) {
  const [draft, setDraft] = useState("");
  const [evidence, setEvidence] = useState<{ messageId: string; result: RunResult; chunkId: string | null } | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const submittedDraft = useRef<string | null>(null);
  const latestMessage = conversation.messages.at(-1);
  const preparationConflict = latestMessage?.kind === "error" && latestMessage.code === "PREPARATION_CONFLICT";

  useEffect(() => {
    setDraft("");
    setEvidence(null);
  }, [conversation.id]);

  useEffect(() => {
    const latest = conversation.messages.at(-1);
    if (latest?.kind === "preparation") submittedDraft.current = null;
    if (latest?.kind === "error" && latest.code === "PREPARATION_CONFLICT" && submittedDraft.current) {
      setDraft(submittedDraft.current);
      submittedDraft.current = null;
    }
  }, [conversation.id, conversation.messages.length]);

  useEffect(() => {
    const history = historyRef.current;
    if (!history || !stickToBottom.current) return;
    if (typeof history.scrollTo === "function") history.scrollTo({ top: history.scrollHeight, behavior: "smooth" });
    else history.scrollTop = history.scrollHeight;
  }, [conversation.messages.length, conversation.busy]);

  return (
    <main className="conversation-workspace" aria-label="研究工作区">
      <header className="conversation-heading">
        <div className="conversation-heading-mark"><BookOpenText size={17} /></div>
        <div>
          <span className="conversation-eyebrow">{labels[conversation.mode]} · 当前会话</span>
          <h1>{conversation.title || "新建研究"}</h1>
        </div>
      </header>
      <div ref={historyRef} className="conversation-history" aria-live="polite" onScroll={(event) => {
        const element = event.currentTarget;
        stickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
      }}>
        {conversation.messages.length === 0 ? (
          <div className="conversation-empty">
            <strong>从一个研究问题开始</strong>
            <p>选择研究模式，用自然语言描述你希望了解的政策问题。</p>
          </div>
        ) : conversation.messages.map((message, index) => (
          <Message
            key={message.id}
            message={message}
            isLatest={index === conversation.messages.length - 1}
            conversation={conversation}
            onSend={(message) => { setDraft(""); onSend(message); }}
            onRetry={onRetry}
            onRestartPreparation={onRestartPreparation}
            onSpecifyPolicies={onSpecifyPolicies}
            onEvidence={(result, chunkId) => setEvidence({ messageId: message.id, result, chunkId })}
            evidence={evidence?.messageId === message.id ? evidence : null}
            onCloseEvidence={() => setEvidence(null)}
          />
        ))}
        {conversation.busy !== "idle" && (
          <div className="conversation-progress" role="status" aria-live="polite">
            <strong className="research-running-text">正在研究中<span className="research-running-dots" aria-hidden="true">...</span></strong>
          </div>
        )}
      </div>
      <div className="conversation-composer-wrap">
        <ResearchForm
          mode={conversation.mode}
          hideModeTabs
          value={draft}
          busy={conversation.busy !== "idle" || preparationConflict}
          onChange={setDraft}
          onModeChange={onModeChange}
          onSend={(message) => { submittedDraft.current = message; setDraft(""); onSend(message); }}
          onAdvancedSubmit={onAdvancedSubmit}
          advancedDraft={conversation.advancedDraft}
          onAdvancedDraftChange={onAdvancedDraftChange}
          onOpenCatalog={onOpenCatalog}
        />
      </div>
    </main>
  );
}

function Message({
  message,
  isLatest,
  conversation,
  onSend,
  onRetry,
  onRestartPreparation,
  onSpecifyPolicies,
  onEvidence,
  evidence,
  onCloseEvidence,
}: {
  message: ConversationMessage;
  isLatest: boolean;
  conversation: Conversation;
  onSend: (message: string) => void;
  onRetry: () => void;
  onRestartPreparation: () => void;
  onSpecifyPolicies: () => void;
  onEvidence: (result: RunResult, chunkId: string) => void;
  evidence: { messageId: string; result: RunResult; chunkId: string | null } | null;
  onCloseEvidence: () => void;
}) {
  if (message.kind === "user") return <article className="conversation-message message-user"><div className="message-role">你</div><p>{message.text}</p></article>;
  if (message.kind === "advanced_guidance") return <article className="conversation-message message-system"><div className="message-role">研究助手</div><p>{message.text}</p>{isLatest && ["extract", "compare", "compare_plans"].includes(conversation.mode) && <button type="button" className="retry-inline" disabled={conversation.busy !== "idle"} onClick={onSpecifyPolicies}>指定政策对象</button>}</article>;
  if (message.kind === "execution") return null;
  if (message.kind === "error") {
    const preparationConflict = message.code === "PREPARATION_CONFLICT" || /409|PREPARATION_STATE|STATE_EXPIRED|REVISION_CONFLICT/i.test(message.code);
    const preparationBlocked = message.code === "UNSUPPORTED_MODE" || message.code === "PREPARATION_BLOCKED";
    const canRetry = isLatest && !["UNSUPPORTED_MODE", "PREPARATION_BLOCKED", "PREPARATION_RESTART", "RAG_EMPTY", "NO_EVIDENCE", "INSUFFICIENT_EVIDENCE"].includes(message.code);
    return <article className="conversation-message message-error" role="alert"><div className="message-role">暂时无法继续 · {message.code}</div><p>{message.text}</p><p>{errorExplanation(message.code)}</p>{isLatest && (preparationConflict || preparationBlocked) ? <button type="button" className="retry-inline" onClick={onRestartPreparation}>{preparationConflict ? "重新开始准备研究" : "修改条件后重新准备"}</button> : canRetry && <button type="button" className="retry-inline" onClick={onRetry}><RotateCcw size={13} />重试</button>}</article>;
  }
  if (message.kind === "result") return <ResultMessage message={message} isLatest={isLatest} onRetry={onRetry} onEvidence={onEvidence} evidence={evidence} onCloseEvidence={onCloseEvidence} />;

  if (message.response.status === "ready") return null;
  const response = message.response;
  const latestClarification = isLatest && response === conversation.preparation && response.status === "needs_clarification";
  const options = response.options ?? [];
  const primary = options[0];
  const secondary = options.slice(1);
  return (
    <article className={`conversation-message message-preparation prep-${response.status}`}>
      <div className="message-role">研究助手</div>
      {response.message && (options[0]?.question !== response.message) && <p className="preparation-message-text">{response.message}</p>}
      {response.status === "needs_clarification" && (
        <div className="clarification-block">
          {primary && <section className="clarification-question"><h2>{primary.question}</h2><div className="clarification-choices">{primary.choices.map((choice, choiceIndex) => <button type="button" key={`${primary.id}-${choiceIndex}`} disabled={!latestClarification || conversation.busy !== "idle"} onClick={() => onSend(choice)}>{choice}</button>)}</div></section>}
          {secondary.length > 0 && <details className="additional-questions"><summary>其他需要回答的问题（{secondary.length}）</summary>{secondary.map((option) => <section className="clarification-question" key={option.id}><h3>{option.question}</h3><div className="clarification-choices">{option.choices.map((choice, choiceIndex) => <button type="button" key={`${option.id}-${choiceIndex}`} disabled={!latestClarification || conversation.busy !== "idle"} onClick={() => onSend(choice)}>{choice}</button>)}</div></section>)}</details>}
          {!primary && <p className="clarification-free-text">请在下方输入你希望补充或修改的条件。</p>}
          {!latestClarification && <p className="historic-option-note">这是历史准备消息，选项已停用。请在当前输入框补充条件。</p>}
        </div>
      )}
      {response.status === "ready" && <div className="preparation-status-note">研究范围已准备完成，正在提交正式研究任务；准备完成不代表研究已完成。</div>}
      {response.status === "blocked" && <div className="preparation-status-note">当前无法自动准备研究。{["extract", "compare", "compare_plans"].includes(response.mode) ? "可在下方使用高级选择入口，明确指定政策编号后继续。" : "你可以修改输入条件，再重新开始准备。"}{isLatest && <button type="button" className="retry-inline" onClick={onRestartPreparation}>重新开始准备</button>}</div>}
      {response.warnings?.length > 0 && <ul className="preparation-warnings" aria-label="研究范围提醒">{response.warnings.map((warning, warningIndex) => <li key={`${warningIndex}-${warning}`}>{warning}</li>)}</ul>}
      {stateSummary(response.state).length > 0 && <div className="preparation-confirmed-summary"><strong>当前已确认</strong>{response.state.topic && <span>主题：{response.state.topic}</span>}{response.state.confirmed_conditions.filter((condition) => condition.text.replace(/^主题[：:]\s*/, "") !== response.state.topic).map((condition) => <span key={condition.id}>{condition.text}</span>)}</div>}
      <PreparationDetails response={response} />
    </article>
  );
}

function PreparationDetails({ response }: { response: Extract<ConversationMessage, { kind: "preparation" }>['response'] }) {
  const state = response.state;
  const scope = [
    state.topic && ["主题", state.topic],
    state.area && ["地区", state.area],
    state.time_scope && ["时间范围", [state.time_scope.start, state.time_scope.end].filter(Boolean).join("—") || "未限定"],
    state.dimensions.length > 0 && ["维度", state.dimensions.join("、")],
  ].filter(Boolean) as [string, string][];
  const statuses = state.condition_statuses ?? [];
  return (
    <details className="preparation-details">
      <summary>查看已确认研究条件与执行边界</summary>
      {scope.length > 0 && <dl className="preparation-scope">{scope.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
      {state.confirmed_conditions.length > 0 && <section className="condition-list"><h3>已确认条件</h3>{state.confirmed_conditions.map((condition) => <p key={condition.id}>{condition.text}</p>)}</section>}
      {state.pending_conditions.length > 0 && <section className="condition-list condition-pending"><h3>尚待确认</h3>{state.pending_conditions.map((condition) => <p key={condition.id}>{condition.text}</p>)}</section>}
      {statuses.length > 0 && <section className="condition-list"><h3>条件执行边界</h3>{statuses.map((item) => <div className="condition-status" key={item.condition_id}><span className={`condition-status-label status-${item.status}`}>{statusLabel(item.status)}</span><p>{item.value ? `${item.value} · ` : ""}{item.reason}{item.status === "executable_filter" ? "（具备后续执行能力，不代表已完成筛选）" : ""}</p></div>)}</section>}
      {response.status === "ready" && response.request && <p className="prepared-request-note">已准备的正式请求：{response.request.question}</p>}
    </details>
  );
}

function statusLabel(status: string) {
  switch (status) {
    case "executable_filter": return "可执行筛选";
    case "request_only": return "仅作为请求条件";
    case "unrestricted": return "未作限制";
    case "pending": return "待确认";
    default: return status;
  }
}

function stateSummary(state: Extract<ConversationMessage, { kind: "preparation" }>['response']['state']) {
  return [state.topic, ...state.confirmed_conditions.map((condition) => condition.text)].filter(Boolean);
}

function errorExplanation(code: string) {
  if (code === "OUTPUT_PARSE") return "模型输出未通过结构校验；不能据此判断知识库是否有相关政策。可以重试本次研究。";
  if (code === "MAX_TURNS") return "研究步骤达到上限；不能据此判断知识库是否有相关内容。可调整问题后重新准备。";
  if (code === "RUN_TIMEOUT") return "研究执行超时，可以重试本次研究。";
  if (code === "TOOL_BUDGET") return "研究工具调用达到上限，可以缩小问题范围后重试。";
  if (/NUMERIC|NUMBER/i.test(code)) return "数字核验未通过，请勿将未核验的数值作为确定结论。";
  if (/CITATION|QUOTE/i.test(code)) return "引文核验未通过，请查看原文证据并谨慎使用关联结论。";
  if (/EVIDENCE|VERIFY/i.test(code)) return "本次未能完成证据核验，请结合页面显示的证据状态谨慎使用。";
  if (/TIMEOUT/i.test(code)) return "请求超时，可以重试本次研究。";
  if (/NETWORK|FETCH|HTTP/i.test(code)) return "网络请求未能完成，请检查连接后重试。";
  return "本次请求未完成。错误类型会保留在上方，便于区分处理。";
}

function ResultMessage({
  message,
  isLatest,
  onRetry,
  onEvidence,
  evidence,
  onCloseEvidence,
}: {
  message: Extract<ConversationMessage, { kind: "result" }>;
  isLatest: boolean;
  onRetry: () => void;
  onEvidence: (result: RunResult, chunkId: string) => void;
  evidence: { messageId: string; result: RunResult; chunkId: string | null } | null;
  onCloseEvidence: () => void;
}) {
  const result = message.result;
  return <article className="conversation-message result-message" data-result-message-id={message.id}>
    <div className="result-message-body"><Results result={result} onEvidence={(chunkId) => onEvidence(result, chunkId)} /></div>
    {(result.evidence_status !== "verified" || result.warnings?.length > 0 || result.error) && (
      <details className="result-followup-notes">
        <summary>研究提示与核验信息</summary>
        {result.evidence_status !== "verified" && <p>本轮未形成足够的可核验证据，不能据此判断政策不存在。</p>}
        {result.error && <p>{result.error.code}：{result.error.message}。{errorExplanation(result.error.code)}</p>}
        {result.warnings?.length > 0 && <ul>{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
      </details>
    )}
    {isLatest && result.status !== "completed" && <button type="button" className="retry-inline" onClick={onRetry}><RotateCcw size={13} />重试本次研究</button>}
  </article>;
}
