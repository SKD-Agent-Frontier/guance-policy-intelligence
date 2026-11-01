import { useMemo, useState } from "react";
import { ArrowLeft, BookOpenText, FolderPlus, Plus, Trash2 } from "lucide-react";
import { builtInThemes, themedPolicies } from "../policyTopics";
import type { TopicCollection, TopicPolicy } from "../topicStore";
import { TopicPicker } from "./TopicPicker";
import "../topics.css";

type Props = {
  collections: TopicCollection[];
  onCreate: (name: string) => string | null;
  onAddPolicy: (id: string, policy: TopicPolicy) => void;
  onRemovePolicy: (id: string, planid: string) => void;
  onDelete: (id: string) => void;
  onOpenPolicy: (planid: string) => void;
  selection?: TopicSelection;
  onSelectionChange?: (selection: TopicSelection) => void;
};

export type TopicSelection = { kind: "public" | "personal"; id: string } | null;

export function TopicsPage({ collections, onCreate, onAddPolicy, onRemovePolicy, onDelete, onOpenPolicy, selection, onSelectionChange }: Props) {
  const [internalSelected, setInternalSelected] = useState<TopicSelection>(null);
  const selected = selection === undefined ? internalSelected : selection;
  const [newName, setNewName] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(30);
  const [filter, setFilter] = useState("");

  const theme = selected?.kind === "public" ? builtInThemes.find((item) => item.id === selected.id) : undefined;
  const personal = selected?.kind === "personal" ? collections.find((item) => item.id === selected.id) : undefined;
  const publicDocuments = useMemo(() => theme ? themedPolicies(theme.id) : [], [theme?.id]);
  const documents = (personal ? personal.policies : publicDocuments).filter((item) =>
    `${item.title} ${item.area || ""}`.includes(filter.trim()));
  const showSelection = (next: TopicSelection) => {
    if (onSelectionChange) onSelectionChange(next);
    else setInternalSelected(next);
    setFilter(""); setVisible(30);
  };
  const create = () => {
    const id = onCreate(newName);
    if (!id) { setError("专题名称不能为空、不能重复，且不能超过 60 字。"); return; }
    setShowNew(false); setNewName(""); setError("");
    showSelection({ kind: "personal", id });
  };

  return <main className="topics-page" aria-label="研究专题">
    <header className="topics-page-header">
      <div className="library-heading">
        <p className="library-heading-kicker">RESEARCH LIBRARY</p>
        <h1 className="library-heading-title">研究专题</h1>
      </div>
    </header>

    {selected && (theme || personal) ? <div className="topics-detail">
      <div className="topics-detail-sticky-header">
        <button type="button" className="topics-back" onClick={() => showSelection(null)}><ArrowLeft size={15} /> 返回全部专题</button>
        <div className="topics-detail-topline">
          <div>
            {personal && <span>我的专题 · 本地保存</span>}
            <h2>{theme?.name || personal?.name}</h2>
            {theme && <p>{theme.description}</p>}
          </div>
          {personal && <button className="topics-destructive" type="button" onClick={() => {
            if (window.confirm(`确定删除专题“${personal.name}”？该操作不会删除政策库中的原文。`)) {
              onDelete(personal.id); showSelection(null);
            }
          }}><Trash2 size={15} /> 删除专题</button>}
        </div>
      </div>
      <div className="topics-document-tools"><strong>{documents.length} 篇文献</strong>
        <input type="search" aria-label="搜索专题内文献" value={filter}
          placeholder="筛选标题或地区" onChange={(event) => { setFilter(event.target.value); setVisible(30); }} />
      </div>
      {documents.length ? <div className="topics-documents">
        {documents.slice(0, visible).map((doc) => (
          <article className="topics-document" key={doc.planid}>
            <button type="button" className="topics-document-link" onClick={() => onOpenPolicy(doc.planid)}>
              <BookOpenText size={17} aria-hidden="true" />
              <span><strong>{doc.title}</strong><small>{doc.area || "地区未提供"} · planid {doc.planid}</small></span>
            </button>
            {personal ? <button type="button" className="topics-remove" onClick={() => onRemovePolicy(personal.id, doc.planid)}>移出专题</button>
              : <TopicPicker planid={doc.planid} collections={collections} onCreate={onCreate}
                onAdd={(id) => onAddPolicy(id, { planid: doc.planid, title: doc.title, area: doc.area,
                  fyp: doc.fyp, plantype_label: null, generation_id: null })} />}
          </article>
        ))}
        {documents.length > visible && <button type="button" className="topics-more" onClick={() => setVisible((n) => n + 30)}>加载更多（剩余 {documents.length - visible} 篇）</button>}
      </div> : <div className="topics-empty">{filter ? "没有匹配的文献。" : "这个专题还没有文献，可从文献目录预览或正文页添加。"}</div>}
      {theme && <p className="topics-note">分类基于样本标题，允许多标签；点击文献后，以当前发布知识库的正文和元数据为准。</p>}
    </div> : <>
      <section className="topics-section" aria-label="系统主题分类">
        <div className="topics-section-heading"><h2>主题分类</h2><span>{builtInThemes.length} 个主题</span></div>
        <div className="topics-grid">
          {builtInThemes.map((item) => <button type="button" className="topics-card" key={item.id}
            onClick={() => showSelection({ kind: "public", id: item.id })}>
            <span className="topics-card-heading"><span className="topics-card-symbol" aria-hidden="true">◈</span><strong>{item.name}</strong></span>
            <span className="topics-card-description">{item.description}</span><small>{item.count} 篇文献 <span aria-hidden="true">↗</span></small>
          </button>)}
        </div>
      </section>
      <section className="topics-section" aria-label="我的专题">
        <div className="topics-section-heading topics-personal-heading">
          <h2>我的专题</h2><span>{collections.length} 个专题</span>
          <button className="topics-primary-action" type="button" onClick={() => setShowNew(true)}>
            <Plus size={17} /> 新建专题
          </button>
        </div>
        {showNew && <form className="topics-new-form" onSubmit={(event) => { event.preventDefault(); create(); }}>
          <label htmlFor="topics-new-name">专题名称</label>
          <input id="topics-new-name" autoFocus maxLength={60}
            placeholder="例如：京津冀算力政策研究" value={newName}
            onChange={(event) => { setNewName(event.target.value); setError(""); }} />
          <button type="submit" disabled={!newName.trim()}>创建专题</button>
          <button type="button" onClick={() => { setShowNew(false); setError(""); }}>取消</button>
          {error && <p role="alert">{error}</p>}
        </form>}
        {collections.length ? <div className="topics-grid topics-grid-personal">
          {collections.map((topic) => <button type="button" key={topic.id} className="topics-card topics-personal-card"
            onClick={() => showSelection({ kind: "personal", id: topic.id })}>
            <span className="topics-card-heading"><FolderPlus size={21} aria-hidden="true" /><strong>{topic.name}</strong></span>
            <span className="topics-card-description">个人研究资料集合</span><small>{topic.policies.length} 篇文献 <span aria-hidden="true">↗</span></small>
          </button>)}
        </div> : <div className="topics-empty">尚未创建个人专题。可以点击右侧“新建专题”，也可以直接从文献预览添加。</div>}
        <p className="topics-note">个人专题目前仅保存在此浏览器的本地存储中，清理浏览器数据或更换设备可能导致记录丢失；尚不支持云端同步。</p>
      </section>
    </>}
  </main>;
}
