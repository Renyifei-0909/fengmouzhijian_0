import { archiveTitle } from "../lib/productCopy";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { Archive, ChevronRight, Copy, Download, Search, ShieldCheck } from "lucide-react";
import { api } from "../lib/api";
import type { IntegrityCheck, Proof } from "../lib/api";
import { RecordEmpty, RecordError, RecordMeta, RecordStatus, recordError, recordTime } from "./records-ui";

const purposeLabels: Record<string, string> = { validation: "评测归档", demo: "流程归档", workflow: "流程归档", review: "复核归档" };
const checkLabels: Record<string, string> = {
  archive_exists: "归档文件",
  archive_sha256: "归档摘要",
  manifest_sha256: "文件清单",
  member_hashes: "档案内文件",
  merkle_root: "内容指纹",
  record_hash: "记录指纹",
  ledger_chain: "台账链条",
  metadata_consistency: "归档信息",
};

type Inspection = { proofId: string; result?: IntegrityCheck; error?: string; running: boolean };

function labelPurpose(proof: Proof) {
  return purposeLabels[proof.purpose] ?? "工程归档";
}

async function copyFingerprint(value: string) {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch {
    // A secure clipboard can still be unavailable in a preview or managed browser.
  }
  const prior = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const field = document.createElement("textarea");
  field.value = value;
  field.readOnly = true;
  field.style.cssText = "position:fixed;inset:0 auto auto 0;opacity:0;pointer-events:none";
  document.body.append(field);
  field.select();
  try {
    if (!document.execCommand("copy")) throw new Error("复制失败，请展开档案详情手动复制。");
  } finally {
    field.remove();
    prior?.focus({ preventScroll: true });
  }
}

export function TraceabilityPage() {
  const [params, setParams] = useSearchParams();
  const fingerprint = params.get("fingerprint")?.trim() ?? "";
  const archive = params.get("archive")?.trim() ?? "";
  const reportId = params.get("report")?.trim() ?? "";
  const [keyword, setKeyword] = useState(fingerprint);
  const [loaded, setLoaded] = useState<{ fingerprint: string; proofs: Proof[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState("");
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const requestRef = useRef(0);
  const [action, setAction] = useState<{ proofId: string; notice?: string; error?: string } | null>(null);
  const [downloadId, setDownloadId] = useState("");

  useEffect(() => setKeyword(fingerprint), [fingerprint]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void api.listProofs(fingerprint || undefined).then((items) => {
      if (active) setLoaded({ fingerprint, proofs: [...items].sort((a, b) => b.ledger_index - a.ledger_index) });
    }).catch((reason) => {
      if (active) setError(recordError(reason, "档案列表读取失败。"));
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [fingerprint, reload]);

  useEffect(() => () => { requestRef.current += 1; }, []);

  const proofs = loaded?.fingerprint === fingerprint ? loaded.proofs : [];
  const selected = archive ? proofs.find((item) => item.archive_id === archive || item.id === archive)
    : reportId ? proofs.find((item) => item.report_id === reportId) : proofs[0];
  const current = selected?.id === inspection?.proofId ? inspection : null;
  const feedback = selected?.id === action?.proofId ? action : null;
  const integrity = current?.result;
  const checks = integrity ? Object.entries(integrity.checks) : Object.keys(checkLabels).map((key) => [key, null] as const);

  function search(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const next = event ? keyword.trim() : "";
    if (!event) setKeyword("");
    setInspection(null);
    setAction(null);
    requestRef.current += 1;
    if (next === fingerprint) setReload((value) => value + 1);
    const query = new URLSearchParams();
    if (next) query.set("fingerprint", next);
    setParams(query);
  }

  function selectArchive(item: Proof) {
    if (item.id === selected?.id) return;
    setInspection(null);
    setAction(null);
    requestRef.current += 1;
    setParams((currentParams) => {
      const next = new URLSearchParams(currentParams);
      next.delete("report");
      next.set("archive", item.archive_id);
      return next;
    }, { replace: true });
  }

  async function verify() {
    if (!selected || current?.running) return;
    const proofId = selected.id;
    const request = ++requestRef.current;
    setInspection({ proofId, running: true });
    try {
      const result = await api.verifyProof(proofId);
      if (requestRef.current === request) setInspection({ proofId, result, running: false });
    } catch (reason) {
      if (requestRef.current === request) setInspection({ proofId, error: recordError(reason, "完整性核验失败。"), running: false });
    }
  }

  async function download() {
    if (!selected || downloadId) return;
    setAction(null);
    setDownloadId(selected.id);
    try {
      await api.downloadArchive(selected.id);
      setAction({ proofId: selected.id, notice: "已交给浏览器下载。解压 ZIP 后可打开 report/report.html 查看封存报告。" });
    }
    catch (reason) { setAction({ proofId: selected.id, error: recordError(reason, "证据包下载失败。") }); }
    finally { setDownloadId(""); }
  }

  async function copy() {
    if (!selected) return;
    setAction(null);
    try {
      await copyFingerprint(selected.archive_sha256);
      setAction({ proofId: selected.id, notice: "已复制证据包摘要。" });
    } catch {
      setAction({ proofId: selected.id, error: "无法自动复制，请展开档案详情手动复制。" });
    }
  }

  return <div className="fm-records fm-trace-page page-enter">
    <section className="fm-panel" aria-label="档案检索">
      <form className="fm-record-toolbar fm-record-body" onSubmit={search}>
        <div className="flex items-center gap-3">
          <Archive size={23} className="shrink-0 text-sky-300" aria-hidden="true" />
          <div><h2>档案检索</h2><p className="fm-record-muted mt-1">按档案编号或完整摘要精确查找</p></div>
        </div>
        <div className="fm-record-actions">
          <label className="fm-record-search">
            <Search size={15} aria-hidden="true" />
            <input className="fm-input" aria-label="档案编号或摘要" placeholder="输入档案编号或摘要" value={keyword} onChange={(event) => setKeyword(event.target.value)} />
          </label>
          <button className="fm-button fm-button-primary" type="submit" disabled={loading}>检索</button>
          <button className="fm-button" type="button" onClick={() => search()}>重置</button>
        </div>
      </form>
    </section>

    {error ? <RecordError message={error} /> : null}
    <div className="fm-record-split">
      <section className="fm-panel" aria-label="档案台账" aria-busy={loading}>
        <div className="fm-record-section-head">
          <h2>档案台账 <span className="fm-record-muted">· {proofs.length}</span></h2>
          <span className="fm-record-muted">最新在前</span>
        </div>
        {proofs.length ? <ul className="fm-record-list">
          {proofs.map((item) => <li key={item.id}><button type="button" className="fm-record-row" aria-pressed={selected?.id === item.id} onClick={() => selectArchive(item)}>
            <Archive size={19} strokeWidth={1.55} aria-hidden="true" />
            <span>
              <span className="fm-record-name">{archiveTitle(item.ledger_index)}</span>
              <small>{labelPurpose(item)} · {recordTime(item.created_at, true)}</small>
            </span>
            <ChevronRight size={14} aria-hidden="true" />
          </button></li>)}
        </ul> : <RecordEmpty loading={loading}>{loading ? "正在读取档案…" : error ? "档案清单暂不可用" : fingerprint ? "未找到匹配档案" : "暂无归档记录"}</RecordEmpty>}
      </section>

      <section className="fm-panel" aria-label="完整性核验">
        <div className="fm-record-section-head">
          <h2>完整性核验</h2>
          <div role="status">{integrity ? <RecordStatus tone={integrity.valid ? "green" : "red"}>{integrity.valid ? "核验通过" : "核验未通过"}</RecordStatus>
            : selected ? <RecordStatus tone={current?.running ? "blue" : "neutral"}>{current?.running ? "核验中" : "待核验"}</RecordStatus> : null}</div>
        </div>
        {selected ? <div className="fm-record-body" key={selected.id}>
          <div>
            <p className="fm-record-name text-base">{archiveTitle(selected.ledger_index)}</p>
          </div>
          <RecordMeta entries={[
            ["归档用途", labelPurpose(selected)],
            ["归档时间", recordTime(selected.created_at)],
            ["证据等级", selected.evidence_grade ? "评测证据" : "工程过程资料"],
            ["交付报告", <Link className="fm-link" to={`/reports?report=${encodeURIComponent(selected.report_id)}`}>查看报告</Link>],
          ]} />
          <div className="fm-record-actions">
            <button type="button" className="fm-button fm-button-primary" disabled={current?.running} onClick={() => void verify()}>
              <ShieldCheck size={16} aria-hidden="true" />{current?.running ? "正在核验…" : integrity ? "重新核验" : "核验档案"}
            </button>
            <button type="button" className="fm-button" disabled={!!downloadId} onClick={() => void download()}>
              <Download size={15} aria-hidden="true" />{downloadId === selected.id ? "下载中…" : "下载证据包（ZIP）"}
            </button>
            <button type="button" className="fm-button" onClick={() => void copy()}><Copy size={14} aria-hidden="true" />复制摘要</button>
          </div>
          {feedback?.notice ? <p className="text-xs text-emerald-300" role="status">{feedback.notice}</p> : null}
          {feedback?.error ? <RecordError message={feedback.error} /> : null}
          {current?.error ? <RecordError message={current.error} /> : null}
          <div className="border-t border-slate-200 pt-3" aria-busy={current?.running}>
            <h3 className="font-semibold">逐项核验</h3>
            <ul className="fm-trace-checks">
              {checks.map(([key, passed]) => {
                const skipped = key !== "archive_exists" && integrity?.checks.archive_exists === false;
                return <li key={key}><span>{checkLabels[key] ?? "其他核验项"}</span>
                  <RecordStatus tone={passed === null || skipped ? "neutral" : passed ? "green" : "red"}>
                    {passed === null ? "待检查" : skipped ? "未检查" : passed ? "通过" : "未通过"}
                  </RecordStatus></li>;
              })}
            </ul>
            {integrity?.errors.length ? <details className="mt-3 text-xs text-slate-400"><summary className="cursor-pointer">失败详情</summary>
              <ul className="mt-2 list-disc space-y-1 break-words pl-4">{integrity.errors.map((message, index) => <li key={index}>{message}</li>)}</ul>
            </details> : null}
          </div>
          <details className="fm-record-details">
            <summary>档案详情</summary>
            <RecordMeta entries={[
              ["完整档案编号", <code>{selected.archive_id}</code>],
              ["证据包摘要", <code>{selected.archive_sha256}</code>],
              ["清单摘要", <code>{selected.manifest_sha256}</code>],
              ["内容指纹", <code>{selected.merkle_root}</code>],
              ["记录指纹", <code>{selected.record_hash}</code>],
              ["前序指纹", <code>{selected.previous_record_hash}</code>],
            ]} />
          </details>
        </div> : <RecordEmpty loading={loading}>{loading ? "正在读取档案…" : archive ? "未找到指定档案，请重新选择。" : reportId ? "这份报告暂无关联档案。" : "选择档案后核验完整性"}</RecordEmpty>}
      </section>
    </div>
  </div>;
}
