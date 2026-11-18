import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { TopicsPage } from "./TopicsPage";
import { TopicPicker } from "./TopicPicker";
import { addPolicyToTopic, newTopicCollection, removePolicyFromTopic, type TopicCollection } from "../topicStore";
import { builtInThemes } from "../policyTopics";
import { catalogClient } from "../api/catalogClient";

vi.mock("../api/catalogClient", () => ({ catalogClient: { list: vi.fn() } }));

afterEach(cleanup);

const published = (items: { planid: string; title: string; area: string; fyp: string; plantype_label: string; matches: never[] }[], total_pages = 1) => ({
  dataset: { generation_id: "release-1", revision: 1, total_documents: items.length, partial: null, source: "current_published_frozen_release" },
  query: { q: null, area: null, fyp: null, plantype: null, match_mode: null },
  matched_documents: items.length, page: 1, page_size: 100, total_pages, items,
});
const comprehensivePolicy = { planid: "P-1", title: "国民经济和社会发展总体规划", area: "海西州", fyp: "十四五", plantype_label: "规划", matches: [] as never[] };
const digitalPolicy = { planid: "P-2", title: "数字科技创新规划", area: "北京市", fyp: "十四五", plantype_label: "规划", matches: [] as never[] };

beforeEach(() => {
  vi.mocked(catalogClient.list).mockReset();
  vi.mocked(catalogClient.list).mockResolvedValue(published([comprehensivePolicy, digitalPolicy]) as never);
});

describe("TopicsPage", () => {
  it("places the new topic action at the right of 我的专题, not in the page header", () => {
    const { container } = render(<TopicsPage collections={[]} onCreate={() => null}
      onAddPolicy={vi.fn()} onRemovePolicy={vi.fn()} onDelete={vi.fn()} onOpenPolicy={vi.fn()} />);
    const mainHeader = container.querySelector(".topics-page-header");
    const personalSection = screen.getByRole("region", { name: "我的专题" });
    const personalHeading = personalSection.querySelector(".topics-personal-heading");
    expect(mainHeader?.querySelector(".topics-primary-action")).toBeNull();
    expect(within(personalHeading as HTMLElement).getByRole("button", { name: "新建专题" }))
      .toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "新建专题" })).toHaveLength(1);
    fireEvent.click(within(personalHeading as HTMLElement).getByRole("button", { name: "新建专题" }));
    expect(within(personalSection).getByRole("textbox", { name: "专题名称" })).toBeInTheDocument();
    fireEvent.click(within(personalSection).getByRole("button", { name: "取消" }));
    expect(within(personalSection).queryByRole("textbox", { name: "专题名称" })).toBeNull();
  });

  it("loads every published-catalog page before counting themes and allows opening a policy", async () => {
    vi.mocked(catalogClient.list)
      .mockResolvedValueOnce(published([comprehensivePolicy], 2) as never)
      .mockResolvedValueOnce(published([digitalPolicy], 2) as never);
    const onOpenPolicy = vi.fn();
    render(<TopicsPage collections={[]} onCreate={() => null} onAddPolicy={vi.fn()}
      onRemovePolicy={vi.fn()} onDelete={vi.fn()} onOpenPolicy={onOpenPolicy} />);
    expect(screen.getByRole("main", { name: "研究专题" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /综合发展与政府工作/ })).toBeInTheDocument();
    expect(catalogClient.list).toHaveBeenNthCalledWith(1, { page: 1, page_size: 100 });
    expect(catalogClient.list).toHaveBeenNthCalledWith(2, { page: 2, page_size: 100 });
    fireEvent.click(screen.getByRole("button", { name: new RegExp(builtInThemes[0].name) }));
    expect(screen.getByRole("button", { name: "返回全部专题" })).toBeInTheDocument();
    const documents = screen.getAllByRole("button", { name: /planid/ });
    expect(documents.length).toBeGreaterThan(0);
    fireEvent.click(documents[0]);
    expect(onOpenPolicy).toHaveBeenCalledWith(expect.any(String));
  });

  it("keeps the return action, heading and description together for sticky scrolling", async () => {
    const { container } = render(<TopicsPage collections={[]} onCreate={() => null}
      onAddPolicy={vi.fn()} onRemovePolicy={vi.fn()} onDelete={vi.fn()} onOpenPolicy={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(builtInThemes[0].name) }));
    const pinned = container.querySelector<HTMLElement>(".topics-detail-sticky-header");
    expect(pinned).not.toBeNull();
    expect(within(pinned!).getByRole("button", { name: "返回全部专题" })).toBeInTheDocument();
    expect(within(pinned!).getByRole("heading", { name: builtInThemes[0].name })).toBeInTheDocument();
    expect(within(pinned!).getByText(builtInThemes[0].description)).toBeInTheDocument();
    expect(screen.queryByText("系统主题分类 · 标题归类")).not.toBeInTheDocument();
    expect(pinned!.querySelector(".topics-document-tools, .topics-documents")).toBeNull();
    expect(container.querySelector(".topics-detail > .topics-document-tools")).not.toBeNull();
    fireEvent.click(within(pinned!).getByRole("button", { name: "返回全部专题" }));
    expect(container.querySelector(".topics-detail-sticky-header")).toBeNull();
  });

  it("creates a personal topic, adds a policy, and removes it", async () => {
    function Harness() {
      const [collections, setCollections] = useState<TopicCollection[]>([]);
      return <TopicsPage collections={collections}
        onCreate={(name) => {
          const topic = newTopicCollection(name);
          setCollections((all) => [...all, topic]);
          return topic.id;
        }}
        onAddPolicy={(id, policy) => setCollections((all) => addPolicyToTopic(all, id, policy))}
        onRemovePolicy={(id, planid) => setCollections((all) => removePolicyFromTopic(all, id, planid))}
        onDelete={(id) => setCollections((all) => all.filter((topic) => topic.id !== id))}
        onOpenPolicy={vi.fn()} />;
    }
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "新建专题" }));
    fireEvent.change(screen.getByRole("textbox", { name: "专题名称" }), { target: { value: "数字经济研究" } });
    fireEvent.click(screen.getByRole("button", { name: "创建专题" }));
    expect(screen.getByRole("heading", { name: "数字经济研究" })).toBeInTheDocument();
    expect(screen.getByText(/这个专题还没有文献/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回全部专题" }));
    fireEvent.click(await screen.findByRole("button", { name: /数字经济与科技/ }));
    const picker = screen.getAllByRole("button", { name: /添加至专题/ })[0];
    fireEvent.click(picker);
    const box = screen.getAllByRole("group", { name: "添加文献至专题" })[0];
    fireEvent.click(within(box).getByRole("button", { name: "数字经济研究" }));
    fireEvent.click(screen.getByRole("button", { name: "返回全部专题" }));
    fireEvent.click(screen.getByRole("button", { name: /数字经济研究/ }));
    expect(screen.getAllByRole("button", { name: "移出专题" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "移出专题" }));
    expect(screen.getByText(/这个专题还没有文献/)).toBeInTheDocument();
  });

  it("keeps personal topics usable if the published catalog cannot load", async () => {
    vi.mocked(catalogClient.list).mockRejectedValue(new Error("目录连接失败"));
    render(<TopicsPage collections={[]} onCreate={() => null} onAddPolicy={vi.fn()}
      onRemovePolicy={vi.fn()} onDelete={vi.fn()} onOpenPolicy={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("目录连接失败");
    expect(screen.getByRole("region", { name: "我的专题" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /综合发展与政府工作/ })).not.toBeInTheDocument();
  });
});

describe("TopicPicker", () => {
  it("creates and immediately adds selected policy to a new topic", () => {
    const onCreate = vi.fn(() => "topic-new");
    const onAdd = vi.fn();
    render(<TopicPicker planid="23132" collections={[]} onCreate={onCreate} onAdd={onAdd} />);
    fireEvent.click(screen.getByRole("button", { name: "添加至专题" }));
    fireEvent.click(screen.getByRole("button", { name: "新建专题" }));
    fireEvent.change(screen.getByRole("textbox", { name: "新专题名称" }), { target: { value: "海西州规划研究" } });
    fireEvent.click(screen.getByRole("button", { name: "新建并添加" }));
    expect(onCreate).toHaveBeenCalledWith("海西州规划研究");
    expect(onAdd).toHaveBeenCalledWith("topic-new");
  });
});
