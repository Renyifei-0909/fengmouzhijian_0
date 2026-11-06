import { useEffect, useState } from "react";
import { Link } from "react-router";
import { ArrowUpRight, PanelLeft, RefreshCw, Server } from "lucide-react";
import { SIDEBAR_DEFAULT_WIDTH, SIDEBAR_MAX_WIDTH, SIDEBAR_MIN_WIDTH } from "../components/sidebar/Sidebar";
import { api } from "../lib/api";
import type { CapabilityMeta } from "../lib/api";
import { RecordError, RecordMeta, RecordStatus, recordError, recordTime } from "./records-ui";

type AccountSettingsProps = {
  sidebarCollapsed?: boolean;
  sidebarWidth?: number;
  onSidebarCollapsedChange?: (collapsed: boolean) => void;
  onSidebarWidthChange?: (width: number) => void;
};

type ServiceSnapshot = {
  readiness: "ready" | "unavailable";
  meta: CapabilityMeta | null;
  checkedAt: string;
  error?: string;
};

export function AccountSettingsPage({
  sidebarCollapsed = false,
  sidebarWidth = SIDEBAR_DEFAULT_WIDTH,
  onSidebarCollapsedChange,
  onSidebarWidthChange,
}: AccountSettingsProps) {
  const [snapshot, setSnapshot] = useState<ServiceSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void Promise.allSettled([api.health(), api.meta()]).then(([health, meta]) => {
      if (!active) return;
      setSnapshot({
        readiness: health.status === "fulfilled" && health.value.status === "ready" ? "ready" : "unavailable",
        meta: meta.status === "fulfilled" ? meta.value : null,
        checkedAt: new Date().toISOString(),
        error: health.status === "rejected" ? recordError(health.reason, "服务状态读取失败。")
          : meta.status === "rejected" ? recordError(meta.reason, "服务信息读取失败。") : undefined,
      });
      setLoading(false);
    });
    return () => { active = false; };
  }, [revision]);

  function restoreNavigation() {
    onSidebarCollapsedChange?.(false);
    onSidebarWidthChange?.(SIDEBAR_DEFAULT_WIDTH);
  }

  const schema = snapshot?.meta?.database_schema;
  const execution = snapshot?.meta?.verification_execution.mode;
  const canConfigure = Boolean(onSidebarCollapsedChange && onSidebarWidthChange);
  return <div className="fm-records fm-settings-page page-enter">
    <section className="fm-panel">
      <div className="fm-record-section-head">
        <h2 className="inline-flex items-center gap-2"><PanelLeft size={17} className="text-sky-300" aria-hidden="true" />导航与布局</h2>
        {canConfigure ? <button className="fm-button" type="button" disabled={!sidebarCollapsed && sidebarWidth === SIDEBAR_DEFAULT_WIDTH} onClick={restoreNavigation}>恢复默认</button> : null}
      </div>
      <div className="fm-record-body">
        <div className="fm-settings-row">
          <div><h3>侧栏模式</h3><p className="fm-record-muted">设置本机工作台的导航显示方式</p></div>
          {onSidebarCollapsedChange ? <div className="fm-record-segments" role="group" aria-label="侧栏模式">
            <button type="button" aria-pressed={!sidebarCollapsed} onClick={() => onSidebarCollapsedChange(false)}>展开</button>
            <button type="button" aria-pressed={sidebarCollapsed} onClick={() => onSidebarCollapsedChange(true)}>收起</button>
          </div> : <RecordStatus>{sidebarCollapsed ? "收起" : "展开"}</RecordStatus>}
        </div>
        <div className="fm-settings-row">
          <div><h3><label htmlFor="fm-settings-sidebar-width">侧栏展开宽度</label></h3><p className="fm-record-muted">展开导航后应用</p></div>
          {onSidebarWidthChange ? <div className="fm-settings-range">
            <input id="fm-settings-sidebar-width" type="range" min={SIDEBAR_MIN_WIDTH} max={SIDEBAR_MAX_WIDTH} step={1} value={Math.round(sidebarWidth)}
              aria-valuetext={`${Math.round(sidebarWidth)} 像素`} onChange={(event) => onSidebarWidthChange(Number(event.target.value))} />
            <output htmlFor="fm-settings-sidebar-width" className="fm-record-numeric">{Math.round(sidebarWidth)} 像素</output>
          </div> : <span className="fm-record-numeric">{Math.round(sidebarWidth)} 像素</span>}
        </div>
      </div>
    </section>
    <section className="fm-panel" aria-busy={loading}>
      <div className="fm-record-section-head">
        <h2 className="inline-flex items-center gap-2"><Server size={17} className="text-sky-300" aria-hidden="true" />服务连接</h2>
        <div className="fm-record-actions">
          <RecordStatus tone={loading ? "blue" : snapshot?.readiness === "ready" ? "green" : "red"}>
            {loading ? "正在检查" : snapshot?.readiness === "ready" ? "服务就绪" : "服务暂不可用"}
          </RecordStatus>
          <button className="fm-button" type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)}>
            <RefreshCw size={15} aria-hidden="true" />刷新状态
          </button>
        </div>
      </div>
      <div className="fm-record-body">
        <RecordMeta entries={[
          ["服务版本", snapshot?.meta?.service_version],
          ["核验执行", execution === "external" ? "独立执行节点" : execution === "inline" ? "服务内执行" : null],
          ["数据结构", schema ? schema.at_head && schema.drift_free ? "已同步" : "待检查" : null],
          ["检查时间", recordTime(snapshot?.checkedAt)],
        ]} />
        {snapshot?.error ? <RecordError message={snapshot.error} /> : null}
        <Link className="fm-link inline-flex items-center gap-1" to="/backend-workflow">前往核验中心 <ArrowUpRight size={14} aria-hidden="true" /></Link>
      </div>
    </section>
  </div>;
}
