import React, { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { cn } from "../../utils/cn";
import { CloseIcon } from "../Icons";

interface ModalProps {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: "md" | "lg" | "xl";
}

const sizeClassMap = {
  md: "max-w-lg",
  lg: "max-w-3xl",
  xl: "max-w-5xl",
};

export const Modal: React.FC<ModalProps> = ({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  size = "lg",
}) => {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = window.requestAnimationFrame(() => dialogRef.current?.focus({ preventScroll: true }));
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      ).filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-slate-950/60 p-4 backdrop-blur-sm sm:items-center">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "flex max-h-[calc(100dvh-2rem)] w-full flex-col overflow-hidden rounded-3xl border border-sky-100 bg-white shadow-2xl shadow-sky-950/20 outline-none",
          sizeClassMap[size]
        )}
      >
        <div className="shrink-0 border-b border-slate-200 bg-gradient-to-r from-sky-50 via-white to-cyan-50 px-6 py-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 id={titleId} className="text-lg font-semibold text-slate-900">{title}</h3>
              {description ? <p id={descriptionId} className="mt-1 text-sm text-slate-500">{description}</p> : null}
            </div>
            <button
              type="button"
              aria-label="关闭弹窗"
              onClick={onClose}
              className="flex h-10 w-10 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-500 transition-all hover:border-sky-200 hover:text-sky-600"
            >
              <CloseIcon className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>

        {footer ? <div className="shrink-0 border-t border-slate-200 bg-slate-50 px-6 py-4">{footer}</div> : null}
      </div>
    </div>,
    document.body
  );
};
