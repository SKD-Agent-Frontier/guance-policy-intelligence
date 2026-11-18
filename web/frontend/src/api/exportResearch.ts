import { apiBaseUrl, isRealMode } from "./config";
import type { RunResult } from "../types";

export type ExportFormat = "docx" | "xlsx";

/** Only transmit user-visible research content; never runtime model logs/secrets. */
export async function exportResearch(
  result: RunResult,
  question: string,
  format: ExportFormat,
): Promise<void> {
  if (!isRealMode) throw new Error("演示模式不支持正式研究结果导出。");
  if (result.status !== "completed") throw new Error("研究尚未完成，不能导出正式报告。");

  const { answer, claims, evidence_refs, structured, task_type, status, evidence_status } = result;
  const response = await fetch(apiBaseUrl + "/api/exports/" + format, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      result: { answer, claims, evidence_refs, structured, task_type, status, evidence_status },
    }),
  });
  if (!response.ok) {
    let reason = "导出失败，请检查后端连接。";
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
        reason = body.detail;
      }
    } catch { /* An HTTP error may lack a JSON body. */ }
    throw new Error(reason);
  }

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "guance-research-" + new Date().toISOString().slice(0, 10) + "." + format;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
