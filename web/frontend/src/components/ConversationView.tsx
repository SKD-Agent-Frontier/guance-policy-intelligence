import { useEffect, useRef, useState, type RefObject } from "react";
import { BookOpenText, Check, Copy, Pencil, RotateCcw } from "lucide-react";
import { ResearchForm } from "./ResearchForm";
import { Results } from "./Results";
import { EvidenceOriginalReader, SourceHoverCard } from "./EvidenceOriginalReader";
import type { AdvancedDraft, Conversation, ConversationMessage } from "../conversationTypes";
import type { EvidenceRef, ResearchRequest, RunResult, TaskType } from "../types";

type Props = {
  conversation: Conversation;
  onModeChange: (mode: TaskType) => void;
  onSend: (message: string) => void;
  onAdvancedSubmit: (request: ResearchRequest) => void;
  onRetry: () => void;
  onRegenerate?: (messageId: string) => void;
  onEditAndResend?: (messageId: string, text: string) => void;
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

export function ConversationView({ conversation, onModeChange, onSend, onAdvancedSubmit, onRetry, onRegenerate = () => {}, onEditAndResend = () => {}, onRestartPreparation, onAdvancedDraftChange, onSpecifyPolicies, onOpenCatalog }: Props) {
  const [draft, setDraft] = useState("");
  const [reader, setReader] = useState<{ result: RunResult; reference: EvidenceRef } | null>(null);
  const [hovered, setHovered] = useState<{ result: RunResult; reference: EvidenceRef; top: number; left: number } | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scrollBeforeReader = useRef<number | null>(null);
  const historyRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const submittedDraft = useRef<string | null>(null);
  const latestMessage = conversation.messages.at(-1);
  const preparationConflict = latestMessage?.kind === "error" && latestMessage.code === "PREPARATION_CONFLICT";

  useEffect(() => {
    setDraft("");
    setReader(null);
    setHovered(null);
    setSourceError(null);
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
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

  const cancelHoverTimer = () => {
    if (hoverTimer.current) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };
  const closeHover = () => {
    cancelHoverTimer();
    hoverTimer.current = setTimeout(() => setHovered(null), 180);
  };
  const previewFromButton = (button: HTMLButtonElement, delay = 180) => {
    cancelHoverTimer();
    const messageId = button.closest<HTMLElement>("[data-result-message-id]")?.dataset.resultMessageId;
    const message = conversation.messages.find(item => item.id === messageId);
    if (message?.kind !== "result") return;
    const reference = message.result.evidence_refs?.find(ref => ref.chunk_id === button.dataset.chunkId);
    if (!reference) return;
    const rect = button.getBoundingClientRect();
    const width = Math.min(390, window.innerWidth - 24);
    const left = Math.min(Math.max(12, rect.left - 100), Math.max(12, window.innerWidth - width - 12));
    const top = rect.bottom + 350 < window.innerHeight ? rect.bottom + 9 : Math.max(12, rect.top - 360);
    hoverTimer.current = setTimeout(() => setHovered({ result: message.result, reference, left, top }), delay);
  };
  const openEvidence = (result: RunResult, chunkId: string) => {
    cancelHoverTimer();
    setHovered(null);
    const reference = result.evidence_refs?.find(ref => ref.chunk_id === chunkId);
    if (!reference) {
      setSourceError("该引文没有关联可定位的政策原文，无法打开原文页面。");
      return;
    }
    setSourceError(null);
    scrollBeforeReader.current = historyRef.current?.scrollTop ?? null;
    setReader({ result, reference });
  };
  const backFromReader = () => {
    setReader(null);
    requestAnimationFrame(() => {
      if (historyRef.current && scrollBeforeReader.current !== null) {
        historyRef.current.scrollTop = scrollBeforeReader.current;
      }
      scrollBeforeReader.current = null;
    });
  };
  if (reader) return <EvidenceOriginalReader source={reader} onBack={backFromReader} />;

  return (
    <main className="conversation-workspace" aria-label="研究工作区">
      <header className="conversation-heading">
        <div className="conversation-heading-mark"><BookOpenText size={17} /></div>
        <div>
          <span className="conversation-eyebrow">{labels[conversation.mode]} · 当前会话</span>
          <h1>{conversation.title || "新建研究"}</h1>
        </div>
      </header>
      <div ref={historyRef} className="conversation-history" aria-live="polite"
        onMouseOver={(event) => {
          const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".result-citation[data-chunk-id]");
          if (button && !(event.relatedTarget instanceof Node && button.contains(event.relatedTarget))) previewFromButton(button);
        }}
        onMouseOut={(event) => {
          const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".result-citation[data-chunk-id]");
          if (button && !(event.relatedTarget instanceof Node && button.contains(event.relatedTarget))) closeHover();
        }}
        onFocusCapture={(event) => {
          const button = (event.target as HTMLElement).closest<HTMLButtonElement>(".result-citation[data-chunk-id]");
          if (button) previewFromButton(button, 0);
        }}
        onBlurCapture={(event) => {
          if ((event.target as HTMLElement).closest(".result-citation[data-chunk-id]")) closeHover();
        }}
        onScroll={(event) => {
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
            onRegenerate={onRegenerate}
            onEditAndResend={onEditAndResend}
            onRestartPreparation={onRestartPreparation}
            onSpecifyPolicies={onSpecifyPolicies}
            onEvidence={openEvidence}
          />
        ))}
        {conversation.busy !== "idle" && (
          <div className="conversation-progress" role="status" aria-live="polite">
            <strong className="research-running-text">正在研究中<span className="research-running-dots" aria-hidden="true">...</span></strong>
          </div>
        )}
      </div>
      {sourceError && <p className="source-reader-warning" role="alert">{sourceError}</p>}
      {hovered && <SourceHoverCard source={hovered} style={{ top: hovered.top, left: hovered.left }}
        onMouseEnter={cancelHoverTimer} onMouseLeave={closeHover}
        onOpen={() => openEvidence(hovered.result, hovered.reference.chunk_id)} />}
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
  onRegenerate,
  onEditAndResend,
  onRestartPreparation,
  onSpecifyPolicies,
  onEvidence,
}: {
  message: ConversationMessage;
  isLatest: boolean;
  conversation: Conversation;
  onSend: (message: string) => void;
  onRetry: () => void;
  onRegenerate: (messageId: string) => void;
  onEditAndResend: (messageId: string, text: string) => void;
  onRestartPreparation: () => void;
  onSpecifyPolicies: () => void;
  onEvidence: (result: RunResult, chunkId: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [editedText, setEditedText] = useState(message.kind === "user" ? message.text : "");
  const contentRef = useRef<HTMLDivElement>(null);
  const canChangeHistory = conversation.busy === "idle";
  if (message.kind === "user") return (
    <div className="message-user-wrap">
      {editing ? (
        <form className="conversation-edit-form" onSubmit={(event) => {
          event.preventDefault();
          if (!editedText.trim() || !canChangeHistory) return;
          onEditAndResend(message.id, editedText);
          setEditing(false);
        }}>
          <textarea aria-label="编辑消息内容" autoFocus value={editedText} rows={Math.min(8, Math.max(3, editedText.split("\\n").length))}
            onChange={(event) => setEditedText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") { event.preventDefault(); setEditing(false); }
              if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
            }} />
          <div className="conversation-edit-buttons">
            <button type="button" onClick={() => setEditing(false)}>取消</button>
            <button type="submit" disabled={!editedText.trim() || !canChangeHistory}>保存并发送</button>
          </div>
        </form>
      ) : <>
        <article className="conversation-message message-user"><p>{message.text}</p></article>
        <MessageActions text={message.text} onEdit={() => { setEditedText(message.text); setEditing(true); }} disabled={!canChangeHistory} />
      </>}
    </div>
  );
  if (message.kind === "advanced_guidance") return <article className="conversation-message message-system">
    <div ref={contentRef}><p>{message.text}</p>{isLatest && ["extract", "compare", "compare_plans"].includes(conversation.mode) && <button type="button" className="retry-inline" disabled={!canChangeHistory} onClick={onSpecifyPolicies}>指定政策对象</button>}</div>
    <MessageActions text={message.text} onRegenerate={() => onRegenerate(message.id)} disabled={!canChangeHistory} />
  </article>;
  if (message.kind === "execution") return null;
  if (message.kind === "error") {
    const preparationConflict = message.code === "PREPARATION_CONFLICT" || /409|PREPARATION_STATE|STATE_EXPIRED|REVISION_CONFLICT/i.test(message.code);
    const preparationBlocked = message.code === "UNSUPPORTED_MODE" || message.code === "PREPARATION_BLOCKED";
    const canRetry = isLatest && !["UNSUPPORTED_MODE", "PREPARATION_BLOCKED", "PREPARATION_RESTART", "RAG_EMPTY", "NO_EVIDENCE", "INSUFFICIENT_EVIDENCE"].includes(message.code);
    return <article className="conversation-message message-error" role="alert">
      <div className="message-role">暂时无法继续 · {message.code}</div><p>{message.text}</p><p>{errorExplanation(message.code)}</p>
      {isLatest && (preparationConflict || preparationBlocked) ? <button type="button" className="retry-inline" onClick={onRestartPreparation}>{preparationConflict ? "重新开始准备研究" : "修改条件后重新准备"}</button> : canRetry && <button type="button" className="retry-inline" onClick={onRetry}><RotateCcw size={13} />重试</button>}
      <MessageActions text={message.text + "\\n" + errorExplanation(message.code)}
        onRegenerate={message.code === "PREPARATION_RESTART" ? undefined : () => onRegenerate(message.id)}
        disabled={!canChangeHistory} />
    </article>;
  }
  if (message.kind === "result") return <ResultMessage message={message} isLatest={isLatest} onRetry={onRetry} onRegenerate={() => onRegenerate(message.id)} disabled={!canChangeHistory} onEvidence={onEvidence} />;

  if (message.response.status === "ready") return null;
  const response = message.response;
  const latestClarification = isLatest && response === conversation.preparation && response.status === "needs_clarification";
  const options = response.options ?? [];
  const primary = options[0];
  const secondary = options.slice(1);
  return (
    <article className={`conversation-message message-preparation prep-${response.status}`}>
      <div ref={contentRef}>
      {response.message && (options[0]?.question !== response.message) && <p className="preparation-message-text">{response.message}</p>}
      {response.status === "needs_clarification" && (
        <div className="clarification-block">
          {primary && <section className="clarification-question"><h2>{primary.question}</h2><div className="clarification-choices">{primary.choices.map((choice, choiceIndex) => <button type="button" key={`${primary.id}-${choiceIndex}`} disabled={!latestClarification || conversation.busy !== "idle"} onClick={() => onSend(choice)}>{choice}</button>)}</div></section>}
          {secondary.length > 0 && <details className="additional-questions"><summary>其他需要回答的问题（{secondary.length}）</summary>{secondary.map((option) => <section className="clarification-question" key={option.id}><h3>{option.question}</h3><div className="clarification-choices">{option.choices.map((choice, choiceIndex) => <button type="button" key={`${option.id}-${choiceIndex}`} disabled={!latestClarification || conversation.busy !== "idle"} onClick={() => onSend(choice)}>{choice}</button>)}</div></section>)}</details>}
          {!primary && <p className="clarification-free-text">请在下方输入你希望补充或修改的条件。</p>}
        </div>
      )}
      {response.status === "blocked" && <div className="preparation-status-note">当前无法自动准备研究。{["extract", "compare", "compare_plans"].includes(response.mode) ? "可在下方使用高级选择入口，明确指定政策编号后继续。" : "你可以修改输入条件，再重新开始准备。"}{isLatest && <button type="button" className="retry-inline" onClick={onRestartPreparation}>重新开始准备</button>}</div>}
      </div>
      <MessageActions contentRef={contentRef} onRegenerate={() => onRegenerate(message.id)} disabled={!canChangeHistory} />
    </article>
  );
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
  onRegenerate,
  disabled,
  onEvidence,
}: {
  message: Extract<ConversationMessage, { kind: "result" }>;
  isLatest: boolean;
  onRetry: () => void;
  onRegenerate: () => void;
  disabled: boolean;
  onEvidence: (result: RunResult, chunkId: string) => void;
}) {
  const result = message.result;
  const bodyRef = useRef<HTMLDivElement>(null);
  return <article className="conversation-message result-message" data-result-message-id={message.id}>
    <div ref={bodyRef} className="result-message-body"><Results result={result} onEvidence={(chunkId) => onEvidence(result, chunkId)} /></div>
    {(result.evidence_status !== "verified" || result.warnings?.length > 0 || result.error) && (
      <details className="result-followup-notes">
        <summary>研究提示与核验信息</summary>
        {result.evidence_status !== "verified" && <p>本轮未形成足够的可核验证据，不能据此判断政策不存在。</p>}
        {result.error && <p>{result.error.code}：{result.error.message}。{errorExplanation(result.error.code)}</p>}
        {result.warnings?.length > 0 && <ul>{result.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
      </details>
    )}
    {isLatest && result.status !== "completed" && <button type="button" className="retry-inline" onClick={onRetry}><RotateCcw size={13} />重试本次研究</button>}
    <MessageActions contentRef={bodyRef} text={result.answer} onRegenerate={onRegenerate} disabled={disabled} />
  </article>;
}


/** Chat-style controls live beneath message content and are excluded from copied text. */
function MessageActions({
  text, contentRef, onRegenerate, onEdit, disabled = false,
}: {
  text?: string;
  contentRef?: RefObject<HTMLElement | null>;
  onRegenerate?: () => void;
  onEdit?: () => void;
  disabled?: boolean;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (clearTimer.current) clearTimeout(clearTimer.current); }, []);

  const copyMessage = async () => {
    const content = (contentRef?.current?.innerText || contentRef?.current?.textContent || text || "").trim();
    if (!content) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content);
      } else {
        const field = document.createElement("textarea");
        field.value = content;
        field.setAttribute("readonly", "");
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        const success = document.execCommand?.("copy");
        field.remove();
        if (!success) throw new Error("Clipboard unavailable");
      }
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
    if (clearTimer.current) clearTimeout(clearTimer.current);
    clearTimer.current = setTimeout(() => setCopyState("idle"), 1800);
  };

  return <div className="message-actions" aria-label="消息操作">
    <button type="button" title={copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制"}
      aria-label={copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制消息"}
      onClick={() => void copyMessage()}>{copyState === "copied" ? <Check size={15} /> : <Copy size={15} />}</button>
    {onRegenerate && <button type="button" title="重新生成（后续对话将被替换）" aria-label="重新生成"
      disabled={disabled} onClick={onRegenerate}><RotateCcw size={15} /></button>}
    {onEdit && <button type="button" title="编辑消息" aria-label="编辑消息"
      disabled={disabled} onClick={onEdit}><Pencil size={15} /></button>}
  </div>;
}
