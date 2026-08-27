import React, { useEffect, useState } from "react";
import { Check, ShieldCheck, UserRound } from "lucide-react";
import { useWorkerIdentity } from "../lib/workerIdentity";

export const WorkerProfilePage: React.FC = () => {
  const { profile, pinned, updateProfile } = useWorkerIdentity();
  const [workerId, setWorkerId] = useState(profile.id);
  const [name, setName] = useState(profile.name);
  const [teams, setTeams] = useState(profile.teams.join("、"));
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    setWorkerId(profile.id);
    setName(profile.name);
    setTeams(profile.teams.join("、"));
  }, [profile]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!workerId.trim() || pinned) return;
    updateProfile({
      id: workerId,
      name,
      teams: teams.split(/[、,，]/).map((value) => value.trim()).filter(Boolean),
    });
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2200);
  };

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <section className="border-b border-slate-200 pb-5">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-slate-900 text-white">
            <UserRound className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold">作业身份</h2>
            <p className="mt-1 text-sm leading-6 text-slate-600">
              工单仅按当前人员编号和所属班组展示。正式环境应由登录系统下发并锁定身份。
            </p>
          </div>
        </div>
      </section>

      {pinned ? (
        <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">
          <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-semibold">身份已由部署配置锁定</p>
            <p className="mt-1">如需变更人员或班组，请联系系统管理员。</p>
          </div>
        </div>
      ) : null}

      <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <label className="block">
          <span className="text-sm font-medium text-slate-800">人员编号</span>
          <input
            value={workerId}
            onChange={(event) => setWorkerId(event.target.value)}
            disabled={pinned}
            required
            autoComplete="username"
            className="mt-2 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-800">姓名</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={pinned}
            className="mt-2 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-slate-800">所属班组</span>
          <input
            value={teams}
            onChange={(event) => setTeams(event.target.value)}
            disabled={pinned}
            placeholder="多个班组用顿号分隔"
            className="mt-2 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200 disabled:bg-slate-100"
          />
        </label>
        {!pinned ? (
          <button
            type="submit"
            disabled={!workerId.trim()}
            className="inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-slate-950 px-4 text-sm font-semibold text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            {saved ? <Check className="h-4 w-4" /> : <UserRound className="h-4 w-4" />}
            {saved ? "已保存" : "保存身份"}
          </button>
        ) : null}
      </form>
    </div>
  );
};

