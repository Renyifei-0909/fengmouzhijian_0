import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ChevronRightIcon, CloseIcon, ProjectIcon, SearchIcon } from "../Icons";
import { api, type Project } from "../../lib/api";
import { PRIMARY_NAV, PRODUCT, projectDisplayName } from "../../lib/productCopy";

export function Header({ title = PRODUCT.name, subtitle }: { title?: string; subtitle?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectState, setProjectState] = useState<"loading" | "ready" | "error">("loading");

  function openSearch() {
    if (!dialog.current || dialog.current.open) return;
    setQuery("");
    setProjects([]);
    setProjectState("loading");
    setSearching(true);
    dialog.current.showModal();
    input.current?.focus();
  }

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openSearch();
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);

  useEffect(() => {
    if (!searching) return;
    let active = true;
    api.listProjects().then((items) => {
      if (!active) return;
      setProjects(items);
      setProjectState("ready");
    }).catch(() => {
      if (active) setProjectState("error");
    });
    return () => { active = false; };
  }, [searching]);

  const search = query.trim().toLocaleLowerCase();
  const routes = PRIMARY_NAV.filter((item) => item.label.includes(search));
  const matches = projects.filter((item) => [item.name, projectDisplayName(item.name), item.code, item.location].some((value) => value.toLocaleLowerCase().includes(search))).slice(0, 6);

  return (
    <>
      <header className="fm-header">
        <div className="fm-header-heading">
          <img src="/brand/app-logo-integrated.webp" alt={PRODUCT.name} className="fm-mobile-brand" width={34} height={34} />
          <div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
        </div>
        <div className="fm-header-actions">
          <button type="button" className="fm-header-search" onClick={openSearch} aria-label="查找项目或页面">
            <SearchIcon className="size-4" /><span>查找项目或页面</span><kbd>Ctrl / ⌘ K</kbd>
          </button>
          <Link to="/worker" className="fm-button fm-field-link">现场作业端 <ChevronRightIcon className="size-4" /></Link>
        </div>
      </header>
      <dialog ref={dialog} className="fm-search-dialog" aria-label="查找项目或页面" onClose={() => setSearching(false)}>
        <div className="fm-search-input">
          <SearchIcon className="size-5 shrink-0" />
          <input ref={input} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="输入项目名称、编号或页面名称" aria-label="搜索词" />
          <button type="button" className="fm-button" aria-label="关闭搜索" onClick={() => dialog.current?.close()}><CloseIcon className="size-4" /></button>
        </div>
        <div className="fm-search-results">
          {routes.length > 0 && <section aria-label="页面结果"><p className="fm-label">前往页面</p>
            {routes.map((route) => <Link key={route.path} to={route.path} onClick={() => dialog.current?.close()}>
              <span>{route.label}</span><ChevronRightIcon className="size-4" />
            </Link>)}
          </section>}
          <section aria-label="项目结果"><p className="fm-label">项目</p>
            {matches.map((project) => <Link key={project.id} to={`/projects/${project.id}`} onClick={() => dialog.current?.close()}>
              <ProjectIcon className="size-4 shrink-0" /><span>{projectDisplayName(project.name)}</span><small>{project.location}</small>
            </Link>)}
            {projectState === "loading" && <p role="status">正在读取项目…</p>}
            {projectState === "error" && <p role="status">项目暂时无法加载</p>}
            {projectState === "ready" && matches.length === 0 && <p>暂无匹配项目</p>}
          </section>
        </div>
      </dialog>
    </>
  );
}
