import type { PreparationResponse, RunResult, TaskType } from "./types";

export type ConversationBusyState = "idle" | "preparing" | "running";

export type ConversationMessage =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "preparation"; response: PreparationResponse }
  | { id: string; kind: "execution"; text: string }
  | { id: string; kind: "result"; result: RunResult }
  | { id: string; kind: "error"; code: string; text: string };

export interface Conversation {
  id: string;
  title: string;
  mode: TaskType;
  messages: ConversationMessage[];
  busy: ConversationBusyState;
  preparation: PreparationResponse | null;
}
