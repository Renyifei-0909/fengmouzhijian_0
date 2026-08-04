import React, { useEffect, useMemo, useState } from "react";
import { api, IntegrityCheck, Proof } from "../lib/api";
import { Notice } from "../components/ui/Notice";
import { Modal } from "../components/ui/Modal";
import { BlockchainIcon, SearchIcon, ShieldIcon, CheckIcon, DownloadIcon, ClipboardIcon, XIcon } from "../components/Icons";
import { cn } from "../utils/cn";

export const TraceabilityPage: React.FC = () => {
  const [proofs, setProofs] = useState<Proof[]>([]);
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState<Proof | null>(null);
  const [integrity, setIntegrity] = useState<IntegrityCheck | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const items = await api.listProofs();
      setProofs(items);
      if (!selected && items.length) setSelected(items[0]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "档案列表加载失败");
    } finally { setLoading(false); }
  };

  useEffect(() => { void loadAll(); }, []);

  const search = async () => {
    setError(""); setNotice(""); setIntegrity(null);
    const value = keyword.trim();
    if (!value) { await loadAll(); return; }
    try {
      const matches = await api.listProofs(value);
      setProofs(matches);
      setSelected(matches[0] || null);
      if (!matches.length) setNotice("未命中任何真实档案；系统没有回退到静态通过结果。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "检索失败"); }
  };

  const verify = async () => {
    if (!selected) return;
    setVerifying(true); setError("");
    try { setIntegrity(await api.verifyProof(selected.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "完整性核验失败"); }
    finally { setVerifying(false); }
  };

  const ledger = useMemo(() => [...proofs].sort((a, b) => b.ledger_index - a.ledger_index), [proofs]);

  return <div className="space-y-5 page-enter">
    <section className="relative overflow-hidden rounded-[30px] border border-cyan-300/20 bg-[#061526] p-6 text-white shadow-[0_24px_90px_-45px_rgba(8,145,178,.85)]">
      <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-cyan-400/15 blur-3xl" />
      <div className="relative flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between"><div><div className="inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-300/10 px-3 py-1 text-xs text-cyan-100"><BlockchainIcon className="h-4 w-4" /> 本地可信交付账本</div><h2 className="mt-4 text-2xl font-semibold">档案指纹检索与逐项重算</h2><p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">支持档案编号、证据包摘要、manifest 摘要或记录哈希精确查询；核验动作会重新读取 ZIP 并检查摘要、Merkle Root 与前序链关系。</p></div><div className="rounded-3xl border border-white/10 bg-white/5 px-5 py-4"><p className="text-[10px] tracking-[.22em] text-cyan-300">LOCAL LEDGER</p><p className="mt-1 text-2xl font-semibold">{proofs.length} 条档案</p><p className="mt-1 text-xs text-slate-400">非公链 / 非可信时间戳</p></div></div>
    </section>

    {notice ? <Notice type="info" message={notice} /> : null}
    {error ? <Notice type="info" message={error} /> : null}

    <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-3 md:flex-row"><div className="flex flex-1 items-center gap-3 rounded-[20px] border border-slate-200 bg-slate-50 px-4 py-3 focus-within:border-sky-300"><SearchIcon className="h-4 w-4 text-slate-400" /><input aria-label="档案指纹" value={keyword} onChange={(event) => setKeyword(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void search(); }} className="w-full bg-transparent text-sm outline-none" placeholder="输入 ARC-… 或完整的 64 位 SHA-256" /></div><button onClick={() => void search()} className="rounded-2xl bg-sky-600 px-5 py-3 text-sm font-semibold text-white hover:bg-sky-700">查询真实档案</button><button onClick={() => { setKeyword(""); setIntegrity(null); void loadAll(); }} className="rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-600">重置</button></div>
    </section>

    <div className="grid gap-5 xl:grid-cols-[.82fr_1.18fr]">
      <section className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center justify-between"><div><h3 className="font-semibold text-slate-900">真实档案账本</h3><p className="mt-1 text-xs text-slate-500">按 ledger index 倒序</p></div><ShieldIcon className="h-5 w-5 text-sky-600" /></div><div className="mt-4 max-h-[620px] space-y-3 overflow-auto pr-1">{loading ? <p className="py-10 text-center text-sm text-slate-500">读取中…</p> : null}{!loading && ledger.length === 0 ? <p className="rounded-[22px] border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">没有匹配档案</p> : null}{ledger.map((item) => <button key={item.id} aria-pressed={selected?.id === item.id} onClick={() => { setSelected(item); setIntegrity(null); }} className={cn("w-full rounded-[22px] border p-4 text-left transition-all", selected?.id === item.id ? "border-sky-300 bg-sky-50" : "border-slate-200 bg-slate-50 hover:border-sky-200")}><div className="flex items-center justify-between gap-3"><p className="text-sm font-semibold text-slate-900">{item.archive_id}</p><span className="rounded-full bg-white px-2 py-1 text-[10px] text-slate-500">#{item.ledger_index}</span></div><p className="mt-2 truncate font-mono text-[10px] text-slate-500">{item.record_hash}</p><p className="mt-2 text-xs text-slate-500">{new Date(item.created_at).toLocaleString("zh-CN")}</p></button>)}</div></section>

      <section className="space-y-5">
        <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center justify-between"><div><h3 className="font-semibold text-slate-900">档案指纹</h3><p className="mt-1 text-xs text-slate-500">选择左侧档案后执行独立核验</p></div>{integrity ? <span className={cn("inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold", integrity.valid ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700")}>{integrity.valid ? <CheckIcon className="h-4 w-4" /> : <XIcon className="h-4 w-4" />}{integrity.valid ? "完整性通过" : "核验失败"}</span> : null}</div>
          {selected ? <div className="mt-5 grid gap-3 md:grid-cols-2"><Fingerprint label="档案编号" value={selected.archive_id} /><Fingerprint label="证据用途" value={`${selected.purpose} · ${selected.evidence_grade ? "正式" : "非正式"}`} /><div className="md:col-span-2"><Fingerprint label="证据包 SHA-256" value={selected.archive_sha256} mono /></div><div className="md:col-span-2"><Fingerprint label="Merkle Root" value={selected.merkle_root} mono /></div><div className="md:col-span-2"><Fingerprint label="账本记录哈希" value={selected.record_hash} mono /></div><div className="md:col-span-2"><Fingerprint label="前序记录哈希" value={selected.previous_record_hash} mono /></div></div> : <p className="mt-5 rounded-[22px] border border-dashed border-slate-300 p-10 text-center text-sm text-slate-500">请选择档案</p>}
          <div className="mt-4 grid gap-3 sm:grid-cols-3"><button disabled={!selected || verifying} onClick={() => void verify()} className="rounded-2xl bg-sky-600 px-4 py-3 text-sm font-semibold text-white disabled:bg-slate-300">{verifying ? "正在重算…" : "执行完整性核验"}</button><button disabled={!selected} onClick={() => selected && void navigator.clipboard.writeText(selected.archive_sha256).then(() => setNotice("完整 SHA-256 已复制。"))} className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-700 disabled:text-slate-300"><ClipboardIcon className="h-4 w-4" /> 复制摘要</button><button disabled={!selected} onClick={() => selected && void api.downloadArchive(selected.id).catch((reason) => setError(reason.message))} className="inline-flex items-center justify-center gap-2 rounded-2xl border border-slate-200 px-4 py-3 text-sm text-slate-700 disabled:text-slate-300"><DownloadIcon className="h-4 w-4" /> 下载证据包</button></div>
        </div>

        {integrity ? <div className={cn("rounded-[28px] border p-5", integrity.valid ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50")}><h3 className={cn("font-semibold", integrity.valid ? "text-emerald-800" : "text-rose-800")}>逐项核验结果</h3><div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(integrity.checks).map(([key, valid]) => <div key={key} className="rounded-2xl bg-white/80 px-3 py-2 text-xs text-slate-700">{valid ? "✓" : "×"} {key}</div>)}</div>{integrity.errors.length ? <div className="mt-3 text-xs text-rose-700">{integrity.errors.join("；")}</div> : null}</div> : null}
      </section>
    </div>
  </div>;
};

const Fingerprint: React.FC<{ label: string; value: string; mono?: boolean }> = ({ label, value, mono }) => <div className="rounded-[20px] border border-slate-200 bg-slate-50 p-4"><p className="text-xs text-slate-500">{label}</p><p className={cn("mt-2 break-all text-sm text-slate-900", mono && "font-mono text-[11px] leading-5")}>{value}</p></div>;
