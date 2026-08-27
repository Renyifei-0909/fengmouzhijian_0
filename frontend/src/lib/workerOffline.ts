import { AnalyzerName, LocationSource } from "./api";

export type WorkerEvidenceDraftState = "pending" | "syncing" | "attention";

export type WorkerEvidenceDraft = {
  id: string;
  workOrderId: string;
  workerId: string;
  createdAt: string;
  deviceId: string;
  file: Blob;
  fileName: string;
  fileType: string;
  fileLastModified: number;
  analyzer: AnalyzerName;
  latitude: number | null;
  longitude: number | null;
  accuracyM: number | null;
  locationSource: LocationSource;
  synthetic: boolean;
  clientCapturedAt: string;
  notes: string;
  abnormalRemarks: string;
  safetyState: "safe" | "risk" | "paused";
  state: WorkerEvidenceDraftState;
  lastError: string | null;
};

const DB_NAME = "fengmou-worker-offline-v1";
const STORE_NAME = "evidence-drafts";

export function offlineDraftsSupported(): boolean {
  return typeof window !== "undefined" && "indexedDB" in window;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (!offlineDraftsSupported()) {
      reject(new Error("当前浏览器不支持离线草稿"));
      return;
    }
    const request = window.indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains(STORE_NAME)) {
        const store = database.createObjectStore(STORE_NAME, { keyPath: "id" });
        store.createIndex("workOrderId", "workOrderId", { unique: false });
        store.createIndex("workerId", "workerId", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("离线草稿数据库不可用"));
  });
}

function transactionRequest<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return openDatabase().then((database) => new Promise<T>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, mode);
    const request = action(transaction.objectStore(STORE_NAME));
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("离线草稿操作失败"));
    transaction.oncomplete = () => database.close();
    transaction.onerror = () => {
      database.close();
      reject(transaction.error || new Error("离线草稿事务失败"));
    };
  }));
}

export function createWorkerDraftId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `draft-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function saveWorkerEvidenceDraft(draft: WorkerEvidenceDraft): Promise<void> {
  await transactionRequest("readwrite", (store) => store.put(draft));
}

export async function listWorkerEvidenceDrafts(
  workOrderId: string,
  workerId: string,
): Promise<WorkerEvidenceDraft[]> {
  const rows = await transactionRequest<WorkerEvidenceDraft[]>("readonly", (store) => store.getAll());
  return rows
    .filter((row) => row.workOrderId === workOrderId && row.workerId === workerId)
    .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt));
}

export async function deleteWorkerEvidenceDraft(id: string): Promise<void> {
  await transactionRequest("readwrite", (store) => store.delete(id));
}

