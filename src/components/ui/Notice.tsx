import React from "react";
import { cn } from "../../utils/cn";
import { CheckIcon, InfoIcon } from "../Icons";

interface NoticeProps {
  type?: "success" | "info";
  message: string;
}

export const Notice: React.FC<NoticeProps> = ({ type = "info", message }) => {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-2xl border px-4 py-3 text-sm shadow-sm",
        type === "success"
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border-sky-200 bg-sky-50 text-sky-700"
      )}
    >
      {type === "success" ? <CheckIcon className="h-4 w-4" /> : <InfoIcon className="h-4 w-4" />}
      <span>{message}</span>
    </div>
  );
};
