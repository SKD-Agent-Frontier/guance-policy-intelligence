import { useEffect, useState } from "react";
import { Check, Search } from "lucide-react";

import { catalogClient } from "../api/catalogClient";
import type { CitationReference, PolicyListItem } from "../types";

type Props = {
  selected: CitationReference[];
  onToggle: (citation: CitationReference) => void;
};

function toCitation(item: PolicyListItem): CitationReference {
  return {
    planid: item.planid,
    title: item.title,
    area: item.area,
    fyp: item.fyp,
  };
}

export function CitationPicker({ selected, onToggle }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PolicyListItem[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setStatus("loading");
      catalogClient
        .list({ q: query.trim() || undefined, page_size: 8 }, controller.signal)
        .then(response => {
          setResults(response.items);
          setStatus("ready");
        })
        .catch(() => {
          if (!controller.signal.aborted) setStatus("error");
        });
    }, 240);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [query]);

  return (
    <>
      <label className="citation-search">
        <Search size={16} aria-hidden="true" />
        <input
          type="search"
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder="搜索政策名称、地区或关键词"
          aria-label="搜索引用文献"
        />
      </label>
      <div className="citation-results" role="group" aria-label="可引用文献">
        {status === "loading" && <p className="citation-status">正在检索政策文献…</p>}
        {status === "error" && <p className="citation-status">目录检索不可用，请稍后重试。</p>}
        {status === "ready" && results.length === 0 && (
          <p className="citation-status">没有找到匹配的政策文献。</p>
        )}
        {status === "ready" && results.map(item => {
          const active = selected.some(reference => reference.planid === item.planid);
          return (
            <button
              type="button"
              key={item.planid}
              className={`citation-result${active ? " is-selected" : ""}`}
              aria-pressed={active}
              onClick={() => onToggle(toCitation(item))}
            >
              <span className="citation-result-content">
                <strong>{item.title ?? "未命名政策文献"}</strong>
                <span>{[item.area, item.fyp].filter(Boolean).join(" · ") || "目录文献"}</span>
              </span>
              {active && <Check size={16} aria-hidden="true" />}
            </button>
          );
        })}
      </div>
    </>
  );
}
