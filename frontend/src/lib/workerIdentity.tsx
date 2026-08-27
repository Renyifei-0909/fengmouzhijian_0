import React, { createContext, useContext, useMemo, useState } from "react";

export type WorkerProfile = {
  id: string;
  name: string;
  teams: string[];
};

type WorkerIdentityContextValue = {
  profile: WorkerProfile;
  pinned: boolean;
  updateProfile: (next: WorkerProfile) => void;
};

const STORAGE_KEY = "fengmou.worker-profile.v1";
const configuredId = String(import.meta.env.VITE_WORKER_ID || "").trim();
const configuredName = String(import.meta.env.VITE_WORKER_NAME || "").trim();
const configuredTeams = String(import.meta.env.VITE_WORKER_TEAMS || "")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);

const emptyProfile: WorkerProfile = {
  id: configuredId,
  name: configuredName || configuredId,
  teams: configuredTeams,
};

function loadProfile(): WorkerProfile {
  if (configuredId) return emptyProfile;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyProfile;
    const parsed = JSON.parse(raw) as Partial<WorkerProfile>;
    const id = typeof parsed.id === "string" ? parsed.id.trim() : "";
    const name = typeof parsed.name === "string" ? parsed.name.trim() : "";
    const teams = Array.isArray(parsed.teams)
      ? parsed.teams.filter((value): value is string => typeof value === "string").map((value) => value.trim()).filter(Boolean)
      : [];
    return { id, name: name || id, teams };
  } catch {
    return emptyProfile;
  }
}

const WorkerIdentityContext = createContext<WorkerIdentityContextValue | null>(null);

export const WorkerIdentityProvider: React.FC<React.PropsWithChildren> = ({ children }) => {
  const [profile, setProfile] = useState<WorkerProfile>(loadProfile);
  const pinned = Boolean(configuredId);

  const value = useMemo<WorkerIdentityContextValue>(() => ({
    profile,
    pinned,
    updateProfile: (next) => {
      if (pinned) return;
      const normalized = {
        id: next.id.trim(),
        name: next.name.trim() || next.id.trim(),
        teams: next.teams.map((team) => team.trim()).filter(Boolean),
      };
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(normalized));
      setProfile(normalized);
    },
  }), [pinned, profile]);

  return <WorkerIdentityContext.Provider value={value}>{children}</WorkerIdentityContext.Provider>;
};

export function useWorkerIdentity(): WorkerIdentityContextValue {
  const value = useContext(WorkerIdentityContext);
  if (!value) throw new Error("useWorkerIdentity must be used inside WorkerIdentityProvider");
  return value;
}

export function workerProfileScope(profile: WorkerProfile): string {
  return [profile.id, ...profile.teams].map((value) => value.trim()).filter(Boolean).sort().join("|");
}

