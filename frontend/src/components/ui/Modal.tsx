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
  placement?: "center" | "right";
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
  placement = "center",
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
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]'
        )
      ).filter((element) => {
        const closedDetails = element.closest("details:not([open])");
        return element.tabIndex >= 0
          && !element.closest('[hidden], [inert], [aria-hidden="true"]')
          && (!closedDetails || closedDetails.firstElementChild === element);
      });
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
    <div className={cn(
      "fixed inset-0 z-[100] flex overflow-y-auto bg-slate-950/70",
      placement === "right" ? "justify-end" : "items-start justify-center p-4 sm:items-center"
    )}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "flex w-full flex-col overflow-hidden border border-slate-200 bg-white shadow-2xl outline-none",
          placement === "right" ? "h-dvh border-y-0 border-r-0" : "max-h-[calc(100dvh-2rem)] rounded-lg",
          sizeClassMap[size]
        )}
      >
        <div className="shrink-0 border-b border-slate-200 bg-slate-50 px-6 py-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h3 id={titleId} className="text-lg font-semibold text-slate-900">{title}</h3>
              {description ? <p id={descriptionId} className="mt-1 text-sm text-slate-500">{description}</p> : null}
            </div>
            <button
              type="button"
              aria-label="关闭弹窗"
              onClick={onClose}
              className="flex size-9 shrink-0 items-center justify-center rounded-md border border-slate-200 bg-white text-slate-500 hover:border-sky-200 hover:text-sky-600"
            >
              <CloseIcon className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>

        {footer ? <div className="shrink-0 border-t border-slate-200 bg-slate-50 px-6 py-4">{footer}</div> : null}
      </div>
    </div>,
    document.querySelector(".fm-shell") ?? document.body
  );
};
