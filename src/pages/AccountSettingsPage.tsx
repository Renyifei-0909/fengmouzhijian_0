import React, { useState } from "react";
import { Notice } from "../components/ui/Notice";
import { UserIcon, BellIcon, ShieldIcon, CheckIcon } from "../components/Icons";

export const AccountSettingsPage: React.FC = () => {
  const [form, setForm] = useState({
    name: "张工程师",
    phone: "13800001234",
    email: "zhang@demo.com",
    department: "工程监管中心",
    role: "项目经理",
  });
  const [notify, setNotify] = useState({ alarm: true, weekly: true, login: true, sms: false });
  const [notice, setNotice] = useState("");

  return (
    <div className="space-y-5 page-enter">
      <div className="rounded-[28px] border border-sky-100 bg-gradient-to-r from-sky-700 via-blue-700 to-cyan-700 p-6 text-white shadow-[0_20px_80px_-32px_rgba(14,116,255,0.8)]">
        <h2 className="text-2xl font-semibold">账户设置</h2>
        <p className="mt-2 text-sm text-sky-100">维护个人信息、通知偏好和登录安全配置。</p>
      </div>

      {notice ? <Notice type="success" message={notice} /> : null}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[0.7fr_1.3fr]">
        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-col items-center text-center">
              <div className="flex h-20 w-20 items-center justify-center rounded-3xl bg-gradient-to-br from-sky-500 to-blue-700 text-2xl font-semibold text-white">
                张
              </div>
              <p className="mt-4 text-lg font-semibold text-slate-900">{form.name}</p>
              <p className="mt-1 text-sm text-slate-500">{form.department}</p>
              <button
                onClick={() => setNotice("头像上传功能已在 Demo 中模拟完成。")}
                className="mt-4 rounded-2xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-all hover:border-sky-200 hover:text-sky-700"
              >
                更换头像
              </button>
            </div>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">登录安全</h3>
              <ShieldIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="space-y-3 text-sm text-slate-600">
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                最近登录：2026-01-15 14:36 · 杭州
              </div>
              <div className="rounded-[22px] border border-slate-200 bg-slate-50 p-4">
                双因素认证：已开启
              </div>
              <button
                onClick={() => setNotice("已模拟发送重置密码邮件，请前往邮箱查收。")}
                className="w-full rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
              >
                重置密码
              </button>
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">基础资料</h3>
              <UserIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              {[
                ["name", "姓名", form.name],
                ["phone", "手机号", form.phone],
                ["email", "邮箱", form.email],
                ["department", "部门", form.department],
                ["role", "岗位", form.role],
              ].map(([key, label, value]) => (
                <label key={String(key)} className="block">
                  <span className="mb-2 block text-sm font-medium text-slate-700">{label}</span>
                  <input
                    value={value}
                    onChange={(event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))}
                    className="w-full rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900 outline-none transition-all focus:border-sky-300 focus:bg-white"
                  />
                </label>
              ))}
            </div>
            <button
              onClick={() => setNotice("账户资料已保存，个人档案信息已同步。")}
              className="mt-5 rounded-2xl bg-sky-600 px-4 py-3 text-sm font-medium text-white transition-all hover:bg-sky-700"
            >
              保存账户信息
            </button>
          </div>

          <div className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-base font-semibold text-slate-900">通知偏好</h3>
              <BellIcon className="h-5 w-5 text-sky-600" />
            </div>
            <div className="space-y-3">
              {[
                ["alarm", "接收严重告警通知"],
                ["weekly", "接收周报生成提醒"],
                ["login", "接收异常登录提醒"],
                ["sms", "短信同步现场处置结果"],
              ].map(([key, label]) => (
                <button
                  key={String(key)}
                  onClick={() => setNotify((prev) => ({ ...prev, [key]: !prev[key as keyof typeof prev] }))}
                  className="flex w-full items-center justify-between rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-3 text-left"
                >
                  <span className="text-sm text-slate-700">{label}</span>
                  <span className={`inline-flex h-7 w-12 items-center rounded-full p-1 transition-all ${notify[key as keyof typeof notify] ? "bg-sky-600 justify-end" : "bg-slate-300 justify-start"}`}>
                    <span className="h-5 w-5 rounded-full bg-white" />
                  </span>
                </button>
              ))}
            </div>
            <button
              onClick={() => setNotice("通知偏好已更新，后续消息将按当前配置发送。")}
              className="mt-5 rounded-2xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm font-medium text-sky-700 transition-all hover:bg-sky-100"
            >
              保存通知设置
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
