import { useState } from "react";
import { Check, FolderPlus, Plus } from "lucide-react";
import type { TopicCollection } from "../topicStore";

type Props = {
  planid: string;
  collections: TopicCollection[];
  onAdd: (topicId: string) => void;
  onCreate: (name: string) => string | null;
};

export function TopicPicker({ planid, collections, onAdd, onCreate }: Props) {
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const count = collections.filter((topic) => topic.policies.some((item) => item.planid === planid)).length;

  return <div className="topic-picker">
    <button type="button" className="topic-picker-trigger" aria-expanded={open}
      onClick={() => { setOpen((value) => !value); setError(""); }}>
      <FolderPlus size={16} aria-hidden="true" /> 添加至专题{count ? ` · 已加入 ${count}` : ""}
    </button>
    {open && <div className="topic-picker-popover" role="group" aria-label="添加文献至专题">
      <div className="topic-picker-heading">选择研究专题</div>
      {collections.length ? <div className="topic-picker-options">
        {collections.map((topic) => {
          const added = topic.policies.some((item) => item.planid === planid);
          return <button type="button" key={topic.id} disabled={added} onClick={() => { onAdd(topic.id); setOpen(false); }}>
            <span>{topic.name}</span>{added ? <Check size={15} aria-label="已添加" /> : <Plus size={15} aria-hidden="true" />}
          </button>;
        })}
      </div> : <p className="topic-picker-hint">还没有个人专题，可直接新建。</p>}
      {creating ? <form onSubmit={(event) => {
        event.preventDefault();
        const created = onCreate(name);
        if (!created) { setError("专题名称无效或已存在，请修改后重试。"); return; }
        onAdd(created);
        setName(""); setError(""); setCreating(false); setOpen(false);
      }}>
        <label htmlFor={`new-topic-${planid}`}>新专题名称</label>
        <input id={`new-topic-${planid}`} autoFocus maxLength={60}
          value={name} onChange={(event) => { setName(event.target.value); setError(""); }}
          placeholder="例如：京津冀数字经济研究" />
        {error && <p role="alert">{error}</p>}
        <div className="topic-picker-form-actions">
          <button type="button" onClick={() => { setCreating(false); setError(""); }}>取消</button>
          <button type="submit" disabled={!name.trim()}>新建并添加</button>
        </div>
      </form> : <button type="button" className="topic-picker-create" onClick={() => setCreating(true)}>
        <Plus size={15} aria-hidden="true" /> 新建专题
      </button>}
    </div>}
  </div>;
}
