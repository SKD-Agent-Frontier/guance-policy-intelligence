import { useEffect, useState } from "react";
import { CheckCircle2, FileSpreadsheet, LoaderCircle, Play, UploadCloud, XCircle } from "lucide-react";
import { isRealMode } from "../api/config";
import { validationBatchApi, type ValidationJob, type ValidationPreview } from "../api/validationBatch";
import "./validationBatch.css";

const STORAGE_KEY = "guance.validation.job";
const terminal = (state: string) => ["finished", "cancelled", "failed"].includes(state);
function loadStoredJobId(): string {
  try { return sessionStorage.getItem(STORAGE_KEY) || ""; }
  catch { return ""; }
}
function storeJobId(jobId: string): void {
  try { sessionStorage.setItem(STORAGE_KEY, jobId); }
  catch { /* Keep the current job usable in memory when session storage is unavailable. */ }
}
function clearStoredJobId(): void {
  try { sessionStorage.removeItem(STORAGE_KEY); }
  catch { /* Clearing local persistence must not block the in-memory reset. */ }
}
const stateNames: Record<string, string> = {
  queued: "排队中", running: "执行中", cancelling: "取消中",
  finished: "已完成", cancelled: "已取消", failed: "执行异常",
};

export function ValidationBatchPage() {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ValidationPreview | null>(null);
  const [jobId, setJobId] = useState(loadStoredJobId);
  const [job, setJob] = useState<ValidationJob | null>(null);
  const [expiredJob, setExpiredJob] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!jobId) return;
    let disposed = false;
    let querying = false;
    const refresh = async () => {
      if (querying) return;
      querying = true;
      try {
        const next = await validationBatchApi.status(jobId);
        if (!disposed) { setJob(next); setError(""); }
      } catch (exception) {
        if (!disposed) {
          const notFound = exception instanceof Error
            && (("status" in exception && exception.status === 404)
              || /任务不存在或已过期/i.test(exception.message));
          if (notFound) {
            clearStoredJobId();
            setJobId("");
            setJob(null);
            setExpiredJob(true);
            setError("上次批量任务已过期或不存在，请重新上传验证集。");
          } else {
            setError(exception instanceof Error ? exception.message : "无法读取任务进度");
          }
        }
      } finally { querying = false; }
    };
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [jobId]);

  const running = Boolean(job && !terminal(job.state)) || (Boolean(jobId) && !job);
  const finished = Boolean(job && terminal(job.state));
  const completed = job?.completed ?? 0;
  const total = job?.total ?? preview?.total ?? 0;

  const inspect = async () => {
    if (!file) return;
    setBusy(true); setError("");
    try { setPreview(await validationBatchApi.preview(file)); }
    catch (exception) {
      setPreview(null);
      setError(exception instanceof Error ? exception.message : "文件解析失败");
    } finally { setBusy(false); }
  };

  const start = async () => {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      const response = await validationBatchApi.start(preview.upload_id);
      setJobId(response.job_id);
      setExpiredJob(false);
      storeJobId(response.job_id);
      setJob(null); setPreview(null);
    } catch (exception) {
      setError(exception instanceof Error ? exception.message : "批量任务启动失败");
    } finally { setBusy(false); }
  };

  const cancel = async () => {
    if (!jobId) return;
    setBusy(true); setError("");
    try {
      const response = await validationBatchApi.cancel(jobId);
      setJob((current) => current ? { ...current, state: response.state } : current);
    }
    catch (exception) {
      setError(exception instanceof Error ? exception.message : "取消失败");
    } finally { setBusy(false); }
  };

  const download = async () => {
    if (!jobId) return;
    setBusy(true); setError("");
    try { await validationBatchApi.download(jobId); }
    catch (exception) {
      setError(exception instanceof Error ? exception.message : "导出失败");
    } finally { setBusy(false); }
  };

  const newUpload = () => {
    if (running) return;
    setFile(null); setPreview(null); setJob(null); setJobId(""); setError("");
    setExpiredJob(false);
    clearStoredJobId();
  };

  return <main className="batch-page" aria-label="验证集批量评测">
    <div className="batch-header">
      <p className="batch-eyebrow">验证集 · 批量评测</p>
      <h1>验证集批量评测</h1>
      <p>上传比赛模板 4，逐题调用政策研究 Agent，完成后按照模板 5 导出结果。</p>
    </div>

    {!isRealMode && <div className="batch-alert" role="alert">
      当前为演示模式；批量评测仅支持已配置真实模型和知识库的服务。
    </div>}
    {error && <div className="batch-alert" role="alert">{error}</div>}

    <section className="batch-card" aria-label="上传验证集">
      <div className="batch-step"><span>01</span><h2>上传文件</h2></div>
      <p className="batch-subtitle">支持 .xlsx；第一行必须是「问题」「预期答案」，每次最多 500 道题。预期答案只用于结果回填，不会提供给模型。</p>
      <label className="batch-file-select">
        <UploadCloud size={20} aria-hidden="true" />
        <span>{file ? file.name : "选择验证集 Excel 文件"}</span>
        <input type="file" accept=".xlsx" disabled={busy || running || !isRealMode}
          onChange={(event) => { setFile(event.target.files?.[0] ?? null); setPreview(null); setError(""); }} />
      </label>
      <div className="batch-actions">
        <button type="button" className="batch-primary" disabled={!file || busy || running || !isRealMode}
          onClick={() => { void inspect(); }}>解析并预览</button>
        {(finished || preview || expiredJob) && <button type="button" className="batch-secondary"
          disabled={running || busy} onClick={newUpload}>重新上传</button>}
      </div>
    </section>

    {preview && <section className="batch-card" aria-label="验证集预览">
      <div className="batch-step"><span>02</span><h2>核对题目</h2></div>
      <p className="batch-subtitle">已识别 {preview.total} 道题。以下展示前 10 道，执行顺序与源 Excel 保持一致。</p>
      <div className="batch-table-wrap"><table className="batch-table">
        <thead><tr><th>序号</th><th>问题</th><th>预期答案</th></tr></thead>
        <tbody>{preview.preview.map((row, index) => <tr key={index}>
          <td>{index + 1}</td><td>{row.question}</td><td>{row.expected || "—"}</td>
        </tr>)}</tbody>
      </table></div>
      <div className="batch-actions">
        <button type="button" className="batch-primary" disabled={busy || !isRealMode}
          onClick={() => { void start(); }}><Play size={15} />开始批量评测</button>
      </div>
    </section>}

    {(jobId || job) && <section className="batch-card" aria-label="批量任务进度">
      <div className="batch-step"><span>03</span><h2>执行进度</h2></div>
      <div className="batch-status">
        {running ? <LoaderCircle className="batch-spin" size={19} /> :
          job?.state === "finished" ? <CheckCircle2 size={19} /> : <XCircle size={19} />}
        <strong>{job ? (stateNames[job.state] ?? job.state) : "正在读取任务进度…"}</strong>
        <span>{completed} / {total}</span>
      </div>
      <progress className="batch-progress" max={total || 1} value={completed} />
      <p className="batch-subtitle">任务只记录每题是否执行成功，不判断答案是否正确；请下载结果表结合预期答案人工核验。关闭页面后可在同一浏览器会话查看进度，服务重启后内存任务不会保留。</p>
      {job?.state === "cancelling" && <p role="status" className="batch-cancel-note">取消请求已发送。正在处理的题目可能需要服务端响应或超时后，才能确认停止。</p>}
      {job && <div className="batch-table-wrap"><table className="batch-table">
        <thead><tr><th>序号</th><th>问题</th><th>执行状态</th><th>答案评估</th><th>耗时(秒)</th></tr></thead>
        <tbody>{job.rows.slice(0, 25).map((row) => <tr key={row.index}>
          <td>{row.index}</td><td>{row.question}</td>
          <td>{row.status}</td><td>{row.evaluation ?? "未评估"}</td><td>{row.duration ?? "—"}</td>
        </tr>)}</tbody>
      </table></div>}
      <div className="batch-actions">
        {running && <button type="button" className="batch-secondary" disabled={busy}
          onClick={() => { void cancel(); }}>停止后续题目</button>}
        {finished && <button type="button" className="batch-primary" disabled={busy}
          onClick={() => { void download(); }}><FileSpreadsheet size={17} />导出模板 5 Excel</button>}
        {finished && <button type="button" className="batch-secondary" disabled={busy}
          onClick={newUpload}>新的验证集</button>}
      </div>
    </section>}
  </main>;
}
