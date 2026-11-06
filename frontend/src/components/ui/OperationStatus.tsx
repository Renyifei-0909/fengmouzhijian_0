import type { ReactNode } from "react";

export function OperationStatus({ children, detail }: { children: ReactNode; detail?: string }) {
  return <div className="fm-operation" role="status">
    <span className="fm-operation-orbit" aria-hidden="true" />
    <span><strong>{children}</strong>{detail && <small>{detail}</small>}</span>
  </div>;
}
