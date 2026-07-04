import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { get, isAuthenticated, post, setTokens } from "../api/client";
import type { Org, Project, TokenPair } from "../api/types";

interface AuthState {
  authed: boolean;
  orgs: Org[];
  org: Org | null;
  projects: Project[];
  project: Project | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  selectProject: (p: Project) => void;
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState(isAuthenticated());
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [org, setOrg] = useState<Org | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(isAuthenticated());

  const reload = useCallback(async () => {
    if (!isAuthenticated()) { setLoading(false); return; }
    setLoading(true);
    try {
      const orgList = (await get<{ items: Org[] }>("/orgs")).items;
      setOrgs(orgList);
      const selectedOrg = orgList[0] ?? null;
      setOrg(selectedOrg);
      if (selectedOrg) {
        const projList = (await get<{ items: Project[] }>(`/orgs/${selectedOrg.id}/projects`)).items;
        setProjects(projList);
        const savedId = localStorage.getItem("cg_project");
        setProject(projList.find((p) => p.id === savedId) ?? projList[0] ?? null);
      }
      setAuthed(true);
    } catch {
      setAuthed(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await post<TokenPair>("/auth/login", { email, password });
    setTokens(tokens.access_token, tokens.refresh_token);
    await reload();
  }, [reload]);

  const logout = useCallback(() => {
    setTokens(null, null);
    localStorage.removeItem("cg_project");
    setAuthed(false);
    setOrgs([]); setOrg(null); setProjects([]); setProject(null);
  }, []);

  const selectProject = useCallback((p: Project) => {
    localStorage.setItem("cg_project", p.id);
    setProject(p);
  }, []);

  return (
    <AuthContext.Provider value={{ authed, orgs, org, projects, project, loading,
                                   login, logout, selectProject, reload }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
