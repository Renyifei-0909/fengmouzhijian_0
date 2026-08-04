import React from "react";
import { cn } from "../../utils/cn";
import { CheckIcon, InfoIcon } from "../Icons";

interface NoticeProps {
  type?: "success" | "info" | "warning";
  message: string;
}

export const Notice: React.FC<NoticeProps> = ({ type = "info", message }) => {
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "flex items-start gap-2 rounded-2xl border px-4 py-3 text-sm shadow-sm",
        type === "success"
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : type === "warning"
            ? "border-amber-200 bg-amber-50 text-amber-900"
            : "border-sky-200 bg-sky-50 text-sky-700"
      )}
    >
      {type === "success" ? (
        <CheckIcon className="mt-0.5 h-4 w-4 shrink-0" />
      ) : (
        <InfoIcon className="mt-0.5 h-4 w-4 shrink-0" />
      )}
      <span>{message}</span>
    </div>
  );
};
