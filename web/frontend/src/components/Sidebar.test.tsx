import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";
import { createAdvancedDraft, type Conversation } from "../conversationTypes";

afterEach(() => { cleanup(); vi.restoreAllMocks(); Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal"); Reflect.deleteProperty(HTMLDialogElement.prototype, "close"); });

it("opens record actions only on context menu and applies them to the selected record", () => {
  const title = "需要查看完整名称的一条非常长的研究记录";
  const conversation: Conversation = { id: "one", title, mode: "qa", busy: "idle", preparation: null, advancedDraft: createAdvancedDraft(), messages: [{ id: "m", kind: "user", text: "问题" }] };
  const onRename = vi.fn();
  const onDelete = vi.fn();
  render(<Sidebar conversations={[conversation]} activeId="one" collapsed={false} mobileOpen={false} onExpand={vi.fn()} onToggle={vi.fn()} onCloseMobile={vi.fn()} onHome={vi.fn()} onNew={vi.fn()} onSelect={vi.fn()} onRename={onRename} onDelete={onDelete} onSettings={vi.fn()} />);
  const record = screen.getByRole("button", { name: title });
  expect(record).toHaveAttribute("title", title);
  expect(record).toHaveAttribute("aria-current", "true");
  expect(screen.queryByRole("button", { name: "重命名" })).not.toBeInTheDocument();
  fireEvent.contextMenu(record);
  vi.spyOn(window, "prompt").mockReturnValue("新名称");
  fireEvent.click(screen.getByRole("button", { name: "重命名" }));
  expect(onRename).toHaveBeenCalledWith("one", "新名称");
  fireEvent.contextMenu(record);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(onDelete).toHaveBeenCalledWith("one");
});

it("searches recent conversations and opens the selected conversation", () => {
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function (this: HTMLDialogElement) { this.setAttribute("open", ""); } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function (this: HTMLDialogElement) { this.removeAttribute("open"); } });
  const first: Conversation = { id: "one", title: "数字经济", mode: "qa", busy: "idle", preparation: null, advancedDraft: createAdvancedDraft(), messages: [{ id: "m", kind: "user", text: "北京市产业政策" }] };
  const second: Conversation = { ...first, id: "two", title: "公共交通", messages: [{ id: "n", kind: "user", text: "出行政策" }] };
  const onSelect = vi.fn();
  render(<Sidebar conversations={[first, second]} activeId={null} collapsed={false} mobileOpen={false} onExpand={vi.fn()} onToggle={vi.fn()} onCloseMobile={vi.fn()} onHome={vi.fn()} onNew={vi.fn()} onSelect={onSelect} onRename={vi.fn()} onDelete={vi.fn()} onSettings={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "搜索研究记录" }));
  const dialog = screen.getByRole("dialog", { name: "搜索对话" });
  expect(dialog).toHaveTextContent("最近对话");
  fireEvent.change(screen.getByRole("textbox", { name: "搜索研究记录" }), { target: { value: "北京市" } });
  expect(dialog).toHaveTextContent("数字经济");
  expect(dialog).not.toHaveTextContent("公共交通");
  fireEvent.click(screen.getAllByRole("button", { name: "数字经济" }).at(-1)!);
  expect(onSelect).toHaveBeenCalledWith("one");
  expect(screen.queryByRole("dialog", { name: "搜索对话" })).not.toBeInTheDocument();
});
