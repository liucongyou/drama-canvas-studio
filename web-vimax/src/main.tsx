import {lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState, type FormEvent, type MouseEvent as ReactMouseEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {Check, ChevronDown, CloudUpload, Copy, Download, ExternalLink, FolderOpen, FolderPlus, History, KeyRound, LogOut, MoreHorizontal, Palette, Pencil, Plus, Settings, ShieldCheck, Trash2, Upload, X} from 'lucide-react';
import {CanvasView} from './CanvasView';
import {ModelSettingsDialog} from './ModelSettings';
import {TosSettingsDialog} from './TosSettings';
import {ErrorBoundary, ToastHost, installGlobalErrorHandlers} from './ErrorBoundary';
import {installNetworkStatusToasts} from './networkStatus';
import {applyTheme, normalizeThemePreference, THEMES, type StoredThemePreference, type Theme} from './theme';
import {mergeSavedPreferences, type ShellPreferences} from './settingsSnapshot';
import {reconcileSelectedProjectId} from './projectSelection';
// ★ 全局对话框：替换原生 prompt/confirm/alert（2026-10-25）
//   原生框由浏览器进程外绘制，CSS 无法触及 —— 用户截图里的白底蓝钮弹窗就是它。
import {DialogHost, dialog} from './GDialog';
import {AUTH_REQUIRED_EVENT, apiUrl, changePassword, createManagedAccount, getAuthSession, getManagedAccounts, getModelCatalog, getStorageConfig, loginAccount, logoutAccount, resetManagedAccountPassword, saveTenantPreferences, setManagedAccountStatus, type ManagedAccount, type ManagedAccountsSnapshot, type ModelCatalog, type StorageSpec} from './api';
import {APP_VERSION, RELEASE_SEEN_KEY} from './changelog';
// 更新记录弹窗连带 55KB 发布文本一起异步加载，首屏不再背这个包袱。
const loadChangelogDialog = () => import('./ChangelogDialog');
const ChangelogDialog = lazy(loadChangelogDialog);
import './styles.css';
import './ui-refinements.css';
import './immersive-theme.css';
import './nav-actions.css';
import './activity-layout.css';
import './chat-layout.css';
import './brand-themes.css';
import './error-boundary.css';
// ★★ 全局设计令牌（2026-10-25）：必须**最后**引入。
//   它是全站唯一的颜色真源，靠加载顺序盖住前面几个文件里历史遗留的硬编码色
//   （styles.css 与 ui-refinements.css 各有数百处 #19xx/#20xx 中性灰，
//    且同一元素常被 2~4 条规则反复覆盖，最终生效值散在 5 个不同灰阶上）。
//   以后加新组件请直接用 theme-tokens.css 里的 var(--v-*)，不要再手写色值。
import './theme-tokens.css';

function initialCanvasTheme(): Theme {
  return 'immersive';
}

const bootTheme = initialCanvasTheme();
applyTheme(bootTheme);

function importResultMessage(result: Record<string, unknown>) {
  const readiness = result.readiness as {
    dependencyTotal?: number;
    dependencyReady?: number;
    dependencyPending?: number;
    videoTotal?: number;
    videoReady?: number;
  } | undefined;
  const base = `当前项目共 ${Number(result.episodes || 1)} 集、${Number(result.nodes || 0)} 个节点、${Number(result.connections || 0)} 条连线；复用已有资产 ${Number(result.reusedAssets || 0)} 个，其中已生成 ${Number(result.reusedGeneratedAssets || 0)} 个。`;
  if (!readiness) return base;
  const dependencies = `依赖就绪 ${Number(readiness.dependencyReady || 0)}/${Number(readiness.dependencyTotal || 0)}`;
  const videos = `可生成视频 ${Number(readiness.videoReady || 0)}/${Number(readiness.videoTotal || 0)}`;
  const pending = Number(readiness.dependencyPending || 0);
  return `${base} ${dependencies}，${videos}。${pending > 0 ? `仍有 ${pending} 个引用素材未生成，生成视频前系统会明确拦截。` : '当前集已具备生成条件。'}页面即将刷新。`;
}

function importIssuesMessage(result: Record<string, unknown>) {
  const issues = Array.isArray(result.issues) ? result.issues : [];
  if (issues.length === 0) return String(result.error || '导入校验失败');
  const lines = issues.slice(0, 12).map((raw, index) => {
    const item = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
    const location = [
      item.path,
      item.segment !== null && item.segment !== undefined ? `段${Number(item.segment) + 1}` : '',
      item.role,
      item.nodeCode,
    ].filter(Boolean).join(' · ');
    return `${index + 1}. [${String(item.code || 'ERROR')}] ${String(item.message || '')}${location ? `（${location}）` : ''}`;
  });
  return lines.join('\n');
}

function importPreviewMessage(preview: Record<string, unknown>) {
  const summary = preview.summary && typeof preview.summary === 'object'
    ? preview.summary as Record<string, unknown>
    : {};
  const diff = summary.diff && typeof summary.diff === 'object'
    ? summary.diff as Record<string, unknown>
    : {};
  const warnings = Array.isArray(preview.warnings) ? preview.warnings : [];
  const lines = [
    `集数：${Number(summary.episodesBefore || 0)} → ${Number(summary.episodesAfter || 0)}`,
    `节点新增：${Number(diff.nodesAdded || 0)}，更新：${Number(diff.nodesChanged || 0)}，删除：${Number(diff.nodesRemoved || 0)}`,
    `复用资产：${Number(summary.assetsReused || 0)}，其中已生成 ${Number(summary.assetsReusedGenerated || 0)}`,
  ];
  if (warnings.length > 0) {
    lines.push('', `警告（${warnings.length}）`);
    warnings.forEach((raw) => {
      const item = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
      lines.push(`• [${String(item.code || 'WARN')}] ${String(item.message || '')}`);
    });
  } else {
    lines.push('', '没有警告。');
  }
  return lines.join('\n');
}

// 自研画布工作流入口：项目选择 + 导入载荷 + CanvasView（ViMax 画布内核，对接本地后端）
function Shell({
  onLogout,
  isAdmin,
  localMode = false,
  preferences,
  onPreferencesSaved,
}: {
  onLogout?: () => void;
  isAdmin?: boolean;
  /** 本机单用户模式：没有账号表，别再去轮询 /api/admin/accounts。 */
  localMode?: boolean;
  preferences?: ShellPreferences;
  onPreferencesSaved?: (preferences: ShellPreferences) => void;
}) {
  const preferencesRef = useRef<ShellPreferences>(preferences || {});
  preferencesRef.current = preferences || {};
  const backgroundPreference = preferences?.canvasBackgroundMode;
  const themePreference = preferences?.theme;
  const shellStartedAtRef = useRef<number>(
    typeof performance !== 'undefined' ? performance.now() : Date.now(),
  );
  const [projects, setProjects] = useState<{
    id: string;
    title: string;
    running_count?: number;
    failed_count?: number;
    local_blocked_count?: number;
    node_count?: number;
    episode_count?: number;
    latest_task_status?: string;
    latest_task_phase?: string;
    latest_task_error_kind?: string;
  }[]>([]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  // 本机模式不轮询在线人数（在线数由下面那个 effect 直接置 1），否则顶栏会永久停在「在线状态同步中」
  const [onlineCount, setOnlineCount] = useState(localMode ? 1 : 0);
  const [pid, setPid] = useState<string>('');
  const [canvasEpoch, setCanvasEpoch] = useState(0);
  const [feedPath, setFeedPath] = useState('');
  const [busy, setBusy] = useState(false);
  const feedFileRef = useRef<HTMLInputElement | null>(null);
  // 顶部项目弹层（对标即梦：左上角「项目名 ▾」点开即项目列表 + 新建，不再是一条裸 select）
  const [projectOpen, setProjectOpen] = useState(false);
  const [projectActionMenu, setProjectActionMenu] = useState<{id: string; left: number; top: number} | null>(null);
  const [renamingProject, setRenamingProject] = useState(false);
  const [renameDraft, setRenameDraft] = useState('');
  const projectWrapRef = useRef<HTMLDivElement | null>(null);
  // 导入载荷是低频高级操作，收进「⋯」里，顶部只留项目切换 + 状态
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelSettingsOpen, setModelSettingsOpen] = useState(false);
  const [tosSettingsOpen, setTosSettingsOpen] = useState(false);
  const [modelCatalogSnapshot, setModelCatalogSnapshot] = useState<ModelCatalog | null>(null);
  const [storageSnapshot, setStorageSnapshot] = useState<StorageSpec | null>(null);
  const [accountsSnapshot, setAccountsSnapshot] = useState<ManagedAccountsSnapshot | null>(null);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [adminAccountsOpen, setAdminAccountsOpen] = useState(false);
  const [changelogOpen, setChangelogOpen] = useState(false);
  const [releaseSeen, setReleaseSeen] = useState(() => {
    try {
      return window.localStorage.getItem(RELEASE_SEEN_KEY) === APP_VERSION;
    } catch {
      return false;
    }
  });
  const serverTheme = normalizeThemePreference(themePreference);
  const [themeOverride, setThemeOverride] = useState<Theme | null>(null);
  const theme: Theme = themeOverride ?? serverTheme;
  const themeSaveRef = useRef<Promise<unknown>>(Promise.resolve());
  const latestThemeRequestRef = useRef(0);
  const [canvasBackgroundMode, setCanvasBackgroundMode] = useState<
    'dots' | 'lines' | 'blank'
  >(backgroundPreference || 'blank');
  const settingsWrapRef = useRef<HTMLDivElement | null>(null);
  const settingsRequestsRef = useRef<Record<string, Promise<unknown> | undefined>>({});
  const settingsOpeningRef = useRef(false);

  useEffect(() => {
    const next = backgroundPreference;
    if (next === 'dots' || next === 'lines' || next === 'blank') {
      setCanvasBackgroundMode(next);
    }
  }, [backgroundPreference]);

  useLayoutEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const changeTheme = async (next: Theme) => {
    const savedTheme = normalizeThemePreference(next);
    const requestId = latestThemeRequestRef.current + 1;
    latestThemeRequestRef.current = requestId;
    setThemeOverride(savedTheme);
    const save = themeSaveRef.current
      .catch(() => undefined)
      .then(() => saveTenantPreferences({theme: savedTheme}));
    themeSaveRef.current = save;
    try {
      const result = await save;
      const committed = normalizeThemePreference(result.preferences?.theme);
      onPreferencesSaved?.(
        mergeSavedPreferences(preferencesRef.current, result.preferences || {}),
      );
      if (latestThemeRequestRef.current === requestId) {
        setThemeOverride(committed);
      }
    } catch (error) {
      if (latestThemeRequestRef.current !== requestId) return;
      await dialog.alert({
        title: '主题保存失败',
        message: error instanceof Error ? error.message : String(error),
        tone: 'danger',
      });
      if (latestThemeRequestRef.current === requestId) {
        setThemeOverride(
          normalizeThemePreference(preferencesRef.current.theme),
        );
      }
    }
  };

  const openChangelog = () => {
    setSettingsOpen(false);
    setChangelogOpen(true);
    setReleaseSeen(true);
    try {
      window.localStorage.setItem(RELEASE_SEEN_KEY, APP_VERSION);
    } catch {
      // 无持久化环境只影响“新版本”标记，不影响更新记录本身。
    }
  };

  const pidRef = useRef(pid);
  useEffect(() => { pidRef.current = pid; }, [pid]);

  const selectProject = (projectId: string) => {
    pidRef.current = projectId;
    setPid(projectId);
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete('ep');
      url.hash = projectId;
      window.history.replaceState(
        null,
        '',
        `${url.pathname}${url.search}${url.hash}`,
      );
    } catch {
      window.location.hash = projectId;
    }
  };

  const reconcileSelectedProject = (list: {id: string}[]) => {
    const hashId = decodeURIComponent(window.location.hash.replace(/^#/, ''));
    const nextPid = reconcileSelectedProjectId(
      pidRef.current,
      list.map((item) => item.id),
      hashId,
    );
    if (nextPid === pidRef.current) return;
    pidRef.current = nextPid;
    selectProject(nextPid);
  };

  const loadProjects = async () => {
    try {
      const d = await loadProjectsDeduped();
      const list = d.projects;
      setProjects(list);
      reconcileSelectedProject(list);
      if (!projectsLoaded && typeof performance !== 'undefined') {
        const elapsed = performance.now() - shellStartedAtRef.current;
        performance.mark('canvas-projects-loaded', {detail: {elapsed}});
        if (elapsed > 1500) {
          console.warn(`[canvas-load] project list took ${Math.round(elapsed)}ms`);
        }
      }
      setProjectsLoaded(true);
    } catch { /* 后端未启动 */ }
  };
  const projectsRequestRef = useRef<Promise<{
    projects: typeof projects;
  }> | null>(null);
  const loadProjectsDeduped = useCallback(async () => {
    if (projectsRequestRef.current) return projectsRequestRef.current;
    const request = fetch(apiUrl('/api/projects'), {credentials: 'include'})
      .then((response) => response.json())
      .then((payload) => ({
        projects: Array.isArray(payload?.projects) ? payload.projects : [],
      }))
      .finally(() => {
        if (projectsRequestRef.current === request) {
          projectsRequestRef.current = null;
        }
      });
    projectsRequestRef.current = request;
    return request;
  }, []);
  useEffect(() => {
    const loadAfterProjectChange = () => { void loadProjects(); };
    void loadProjects();
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadProjects();
    }, 15_000);
    window.addEventListener('canvas-projects-changed', loadAfterProjectChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('canvas-projects-changed', loadAfterProjectChange);
    };
  }, []);

  useEffect(() => {
    // 本机单用户模式没有账号体系：这个接口一次都不该发。
    // 以前这里无条件每 30 秒拉一次，只为拿 onlineCount（本机恒为 1），
    // 服务端还会回 403，控制台每 30 秒一条红字。
    //
    // 注意：localMode 是 AuthGate 里 useSession 回来后异步置位的，首帧一定是 false，
    // 所以既不能把它写进 useState 初值（那个初值只算一次，之后不会再算），
    // 也不能只写在下面的 return 条件里 —— 依赖数组漏了它，等它变 true 时
    // 这个 effect 不会重跑，30 秒轮询会一直挂着。两处都补。
    if (localMode) {
      setOnlineCount(1);
      return;
    }
    if (!isAdmin) return;
    let cancelled = false;
    const refresh = () => {
      void getManagedAccounts()
        .then((snapshot) => {
          if (!cancelled) setOnlineCount(snapshot.onlineCount);
        })
        .catch((error) => {
          if (!cancelled && (error instanceof Error) && error.message.includes('管理员')) {
            setOnlineCount(0);
          }
        });
    };
    const timer = window.setInterval(refresh, 30_000);
    refresh();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isAdmin, localMode]);

  const prefetchSettings = async (kind: 'models' | 'storage' | 'accounts') => {
    if (kind === 'models') {
      const snapshot = await getModelCatalog();
      setModelCatalogSnapshot(snapshot);
      return;
    }
    if (kind === 'storage') {
      const snapshot = await getStorageConfig();
      setStorageSnapshot(snapshot);
      return;
    }
    const snapshot = await getManagedAccounts();
    setAccountsSnapshot(snapshot);
    setOnlineCount(snapshot.onlineCount);
  };

  const requestSettings = (kind: 'models' | 'storage' | 'accounts') => {
    let request = settingsRequestsRef.current[kind] as Promise<unknown> | undefined;
    if (!request) {
      request = prefetchSettings(kind).finally(() => {
        if (settingsRequestsRef.current[kind] === request) {
          delete settingsRequestsRef.current[kind];
        }
      });
      settingsRequestsRef.current[kind] = request;
    }
    return request;
  };

  useEffect(() => {
    // Preload while the canvas is idle so opening settings is a pure state switch.
    const preload = () => {
      void requestSettings('storage').catch(() => undefined);
      void requestSettings('models').catch(() => undefined);
      void loadChangelogDialog().catch(() => undefined);
      if (isAdmin && !localMode) void requestSettings('accounts').catch(() => undefined);
    };
    const timer = window.setTimeout(preload, 0);
    return () => window.clearTimeout(timer);
  }, [isAdmin, localMode]);

  const openSettings = async (kind: 'models' | 'storage' | 'accounts') => {
    if (settingsOpeningRef.current) return;
    settingsOpeningRef.current = true;
    setSettingsOpen(false);
    const cached = kind === 'models'
      ? modelCatalogSnapshot
      : kind === 'storage'
        ? storageSnapshot
        : accountsSnapshot;
    if (cached) {
      if (kind === 'models') setModelSettingsOpen(true);
      if (kind === 'storage') setTosSettingsOpen(true);
      if (kind === 'accounts') setAdminAccountsOpen(true);
      settingsOpeningRef.current = false;
      return;
    }
    try {
      await requestSettings(kind);
      if (kind === 'models') setModelSettingsOpen(true);
      if (kind === 'storage') setTosSettingsOpen(true);
      if (kind === 'accounts') setAdminAccountsOpen(true);
    } catch (error) {
      await dialog.alert({
        title: '设置读取失败',
        message: error instanceof Error ? error.message : String(error),
        tone: 'danger',
      });
    } finally {
      settingsOpeningRef.current = false;
    }
  };

  // 点外面收起项目与设置弹层（捕获阶段：内部面板会 stopPropagation）
  useEffect(() => {
    if (!projectOpen && !settingsOpen && !projectActionMenu) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element;
      if (projectOpen && !projectWrapRef.current?.contains(t)) setProjectOpen(false);
      if (settingsOpen && !settingsWrapRef.current?.contains(t)) setSettingsOpen(false);
      if (projectActionMenu && !t.closest('.canvas-project-action-menu') && !t.closest('.canvas-project-action-trigger')) {
        setProjectActionMenu(null);
      }
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [projectOpen, projectActionMenu, settingsOpen]);

  const createProject = async () => {
    // ★ 2026-10-25 用户截图「新建画布窗口要做精致丑死了」。三处结构性修正：
    //   ① 头部原来是**问号图标**（tone=default 默认落到 HelpCircle），
    //      一个"创建"动作挂问号，语义完全错位 —— 现在传 FolderPlus；
    //   ② 正文原来写「给这个画布起一个名字，之后可以在「项目设置」里改。」
    //      两层套嵌的引号 + 一句废话说明，读起来像系统提示。改成一句短句；
    //   ③ 单输入框的弹窗内容太薄，加三个常用名字做快捷片，
    //      顺手把「取消/创建」的节奏撑起来（也真的省一次打字）。
    const title = await dialog.prompt({
      title: '新建画布项目',
      icon: FolderPlus,
      message: '起个名字，方便之后在项目列表里找到它。',
      placeholder: '例如：铁西硬汉 · 第01集',
      defaultValue: '画布工作流',
      suggestions: ['铁西硬汉 · 第01集', '角色设定集', '分镜草稿'],
      confirmText: '创建',
    });
    if (!title) return;
    const d = await fetch(apiUrl('/api/projects'), {
      method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title}),
    }).then(r => r.json());
    await loadProjects();
    selectProject(d.id);
    window.dispatchEvent(new Event('canvas-projects-changed'));
  };

  const openProjectActionMenu = (event: ReactMouseEvent<HTMLButtonElement>, id: string) => {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    setProjectActionMenu((current) => current?.id === id ? null : {
      id,
      left: Math.min(window.innerWidth - 184, rect.right + 8),
      top: Math.min(window.innerHeight - 180, rect.top),
    });
  };

  const duplicateProject = async (id: string) => {
    setProjectActionMenu(null);
    setProjectOpen(false);
    try {
      const duplicated = await fetch(apiUrl(`/api/projects/${id}/duplicate`), {
        method: 'POST',
        credentials: 'include',
        headers: {'Content-Type': 'application/json'},
        body: '{}',
      }).then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.error || `复制失败，HTTP ${response.status}`);
        return payload;
      });
      await loadProjects();
      selectProject(duplicated.id);
      window.dispatchEvent(new Event('canvas-projects-changed'));
    } catch (error) {
      await dialog.alert({title: '复制项目失败', message: (error as Error).message, tone: 'danger'});
    }
  };

  const deleteProject = async (id: string) => {
    const project = projects.find((item) => item.id === id);
    if (!project) return;
    setProjectActionMenu(null);
    const confirmed = await dialog.confirm({
      title: `删除「${project.title}」？`,
      message: '项目画布、节点、连线和任务记录会一起删除，此操作不可撤销。',
      tone: 'danger',
      confirmText: '删除项目',
      cancelText: '取消',
    });
    if (!confirmed) return;
    try {
      const response = await fetch(apiUrl(`/api/projects/${id}`), {method: 'DELETE', credentials: 'include'});
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `删除失败，HTTP ${response.status}`);
      const fallback = projects.find((item) => item.id !== id);
      if (pid === id) {
        selectProject(fallback?.id || '');
      }
      setProjectOpen(false);
      await loadProjects();
      window.dispatchEvent(new Event('canvas-projects-changed'));
    } catch (error) {
      await dialog.alert({title: '删除项目失败', message: (error as Error).message, tone: 'danger'});
    }
  };

  const beginProjectRename = (projectId = pid) => {
    if (!projectId) return;
    setRenameDraft(projects.find(p => p.id === projectId)?.title || '');
    if (projectId !== pid) {
      selectProject(projectId);
    }
    setProjectOpen(false);
    setProjectActionMenu(null);
    setRenamingProject(true);
  };

  const commitProjectRename = async (value = renameDraft) => {
    const cur = projects.find(p => p.id === pid)?.title || '';
    const title = value.trim();
    if (!pid || !title || title === cur) {
      setRenamingProject(false);
      return;
    }
    try {
      const d = await fetch(apiUrl(`/api/projects/${pid}`), {
        method: 'PUT', credentials: 'include', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title}),
      }).then(r => r.json());
      if (!d.ok) throw new Error(d.error || '重命名失败');
      await loadProjects();
      setRenamingProject(false);
    } catch (e) {
      await dialog.alert({title: '重命名失败', message: (e as Error).message, tone: 'danger'});
    }
  };

  const runImportPlan = async (body: Record<string, unknown>, sourceName: string) => {
    if (!pid) {
      await dialog.alert({title: '还没有选中项目', message: '先选择或新建一个项目。'});
      return false;
    }
    setBusy(true);
    try {
      const requestBody = {
        ...body,
        source: {name: sourceName},
      };
      const previewResponse = await fetch(apiUrl(`/api/projects/${pid}/import/preview`), {
        method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(requestBody),
      });
      const preview = await previewResponse.json().catch(() => ({}));
      if (!previewResponse.ok) {
        throw new Error(importIssuesMessage(preview));
      }
      const confirmed = await dialog.confirm({
        title: '导入预检通过',
        message: importPreviewMessage(preview),
        confirmText: '确认导入',
        cancelText: '取消',
      });
      if (!confirmed) return false;

      const commitResponse = await fetch(apiUrl(`/api/projects/${pid}/import`), {
        method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          ...requestBody,
          if_revision: preview.revision,
        }),
      });
      const result = await commitResponse.json().catch(() => ({}));
      if (!commitResponse.ok) {
        throw new Error(importIssuesMessage(result));
      }
      setFeedPath(sourceName);
      const readiness = result.readiness as {readyToGenerate?: boolean} | undefined;
      const commitWarnings = Array.isArray(result.warnings) ? result.warnings : [];
      const warningText = commitWarnings.length > 0
        ? `\n\n导入警告：\n${commitWarnings.map((raw: unknown) => {
            const item = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {};
            return `• [${String(item.code || 'WARN')}] ${String(item.message || '')}`;
          }).join('\n')}`
        : '';
      await dialog.alert({
        title: readiness?.readyToGenerate ? '导入完成 · 已就绪' : '导入完成 · 待准备素材',
        message: `${importResultMessage(result)}${warningText}`,
        tone: readiness?.readyToGenerate ? 'success' : 'default',
      });
      await loadProjects();
      setPid(pid);
      window.location.hash = pid;
      setCanvasEpoch(value => value + 1);
      return true;
    } catch (error) {
      await dialog.alert({
        title: '导入失败',
        message: error instanceof Error ? error.message : String(error),
        tone: 'danger',
      });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const importFeed = async () => {
    if (!feedPath.trim()) {
      await dialog.alert({
        title: '还没填路径',
        message: '请在「项目设置 → 导入投喂载荷」里填写 JSON 文件的完整路径。',
      });
      return;
    }
    await runImportPlan({feed_path: feedPath.trim()}, feedPath.trim().split(/[\\/]/).pop() || 'feed.json');
  };

  const importFeedFile = async (file: File) => {
    if (file.size > 8 * 1024 * 1024) {
      await dialog.alert({title: '文件过大', message: '投喂载荷 JSON 不能超过 8MB。'});
      return;
    }
    try {
      const text = (await file.text()).replace(/^\uFEFF/, '');
      const feed = JSON.parse(text);
      if (!feed || typeof feed !== 'object') {
        throw new Error('不是有效 JSON 文件');
      }
      await runImportPlan({feed_json: feed}, file.name);
    } catch (error) {
      await dialog.alert({
        title: '导入失败',
        message: error instanceof Error ? error.message : String(error),
        tone: 'danger',
      });
    }
  };

  const exportAssetRecord = async () => {
    if (!pid) {
      await dialog.alert({title: '还没有选中项目', message: '先选择或新建一个项目。'});
      return;
    }
    try {
      const response = await fetch(apiUrl(`/api/projects/${pid}/assets/export`), {
        credentials: 'include',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `导出失败，HTTP ${response.status}`);
      const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json;charset=utf-8'});
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const title = projects.find(item => item.id === pid)?.title || '画布资产';
      anchor.href = url;
      anchor.download = `${title.replace(/[\\/:*?"<>|]+/g, '_')}.资产记录.json`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (error) {
      await dialog.alert({
        title: '资产记录导出失败',
        message: error instanceof Error ? error.message : String(error),
        tone: 'danger',
      });
    }
  };

  const currentTitle = projects.find(p => p.id === pid)?.title || '未命名项目';

  const actions = (
    <div className="shell-topbar">
      <div className="canvas-project-switch" ref={projectWrapRef}>
        {renamingProject ? (
          <form
            className="canvas-project-rename"
            onSubmit={(event) => {
              event.preventDefault();
              void commitProjectRename(String(new FormData(event.currentTarget).get('title') || ''));
            }}
          >
            <FolderOpen size={15} strokeWidth={1.8} aria-hidden="true" />
            <input
              name="title"
              value={renameDraft}
              autoFocus
              onChange={(event) => setRenameDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Escape') {
                  event.preventDefault();
                  setRenamingProject(false);
                }
              }}
              aria-label="重命名当前项目"
            />
            <button type="submit" title="保存名称"><Check size={13} strokeWidth={2.3} /></button>
            <button type="button" title="取消重命名" onClick={() => setRenamingProject(false)}><X size={13} strokeWidth={2.3} /></button>
          </form>
        ) : (
          <>
            <button
              type="button"
              className={`canvas-project-btn ${projectOpen ? 'is-open' : ''}`}
              onClick={() => setProjectOpen(v => !v)}
              onDoubleClick={(event) => { event.preventDefault(); beginProjectRename(); }}
              title="切换项目 / 双击重命名"
            >
              <span className="canvas-project-ico" aria-hidden="true">
                <FolderOpen size={15} strokeWidth={1.8} />
              </span>
              <span className="canvas-project-name">{currentTitle}</span>
              <span className={`canvas-project-caret ${projectOpen ? 'is-open' : ''}`} aria-hidden="true">
                <ChevronDown size={12} strokeWidth={2.4} />
              </span>
            </button>
            <button type="button" className="canvas-project-rename-trigger" onClick={() => beginProjectRename()} title="重命名当前项目" aria-label="重命名当前项目">
              <Pencil size={12} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </>
        )}
        {projectOpen && (
          <div className="canvas-project-menu">
            <div className="canvas-project-menu-title">项目</div>
            <div className="canvas-project-menu-list">
              {projects.length === 0 && <p className="canvas-project-menu-empty">还没有项目</p>}
              {projects.map(p => (
                <div
                  key={p.id}
                  className={`canvas-project-row ${p.id === pid ? 'is-current' : ''} ${projectActionMenu?.id === p.id ? 'has-action-open' : ''}`}
                >
                  <button
                    type="button"
                    className="canvas-project-item-main"
                    onClick={() => { setProjectOpen(false); setProjectActionMenu(null); selectProject(p.id); }}
                    title={p.title}
                  >
                    <span className="canvas-project-thumb" aria-hidden="true">
                      <FolderOpen size={17} strokeWidth={1.7} />
                    </span>
                    <span className="canvas-project-menu-label">
                      <strong>{p.title}</strong>
                      <small>
                        {(p.running_count || 0) > 0
                          ? `${p.running_count} 个生成中`
                          : (p.node_count || 0) > 0
                          ? `${p.episode_count || 1} 集 · ${p.node_count || 0} 节点`
                          : (p.local_blocked_count || 0) > 0
                            ? `${p.local_blocked_count} 个素材待补`
                            : (p.failed_count || 0) > 0
                            ? `${p.failed_count} 个节点失败`
                            : p.latest_task_status === 'succeeded'
                              ? '最近任务已完成'
                              : '暂无异常'}
                      </small>
                    </span>
                  </button>
                  <span className="canvas-project-row-tail">
                    {p.id === pid && <span className="canvas-project-menu-check"><Check size={13} strokeWidth={2.4} aria-hidden="true" /></span>}
                    <button
                      type="button"
                      className="canvas-project-action-trigger"
                      title="项目操作"
                      aria-label={`项目操作：${p.title}`}
                      onClick={(event) => openProjectActionMenu(event, p.id)}
                    >
                      <MoreHorizontal size={15} strokeWidth={2.2} aria-hidden="true" />
                    </button>
                  </span>
                </div>
              ))}
            </div>
            <button type="button" className="canvas-project-menu-new" onClick={() => { setProjectOpen(false); void createProject(); }}>
              <Plus size={13} strokeWidth={2.4} aria-hidden="true" /> 新建画布项目
            </button>
          </div>
        )}
      </div>

      {projectActionMenu && createPortal(
        <div
          className="canvas-project-action-menu"
          role="menu"
          style={{left: projectActionMenu.left, top: projectActionMenu.top}}
          onClick={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            onClick={() => beginProjectRename(projectActionMenu.id)}
          >
            <Pencil size={14} strokeWidth={1.9} aria-hidden="true" />
            重命名项目
          </button>
          <button
            type="button"
            onClick={() => {
              const id = projectActionMenu.id;
              setProjectActionMenu(null);
              setProjectOpen(false);
              window.open(`${window.location.origin}${window.location.pathname}#${encodeURIComponent(id)}`, '_blank', 'noopener,noreferrer');
            }}
          >
            <ExternalLink size={14} strokeWidth={1.9} aria-hidden="true" />
            在新窗口打开
          </button>
          <button type="button" onClick={() => void duplicateProject(projectActionMenu.id)}>
            <Copy size={14} strokeWidth={1.9} aria-hidden="true" />
            复制项目
          </button>
          <button type="button" className="is-danger" onClick={() => void deleteProject(projectActionMenu.id)}>
            <Trash2 size={14} strokeWidth={1.9} aria-hidden="true" />
            删除项目
          </button>
        </div>,
        document.body,
      )}

      <span className="shell-topbar-meta">{projects.length} 个项目</span>
      {isAdmin && (
        <span className="shell-online-meta" title="最近 90 秒内活跃的成员账号">
          <i aria-hidden="true" />
          {onlineCount > 0 ? `${onlineCount} 人在线` : '在线状态同步中'}
        </span>
      )}
      <div id="canvas-topbar-slot" className="shell-canvas-slot" />

      <div className="shell-topbar-right">
        <div className="canvas-menu-wrap" ref={settingsWrapRef}>
          <button
            type="button"
            className={`shell-topbar-btn ${settingsOpen ? 'is-open' : ''} ${releaseSeen ? '' : 'has-release'}`}
            onClick={() => {
              setSettingsOpen((value) => {
                if (!value) void loadChangelogDialog();
                return !value;
              });
            }}
            title="主题、模型接口与项目设置"
            aria-expanded={settingsOpen}
          >
            <Settings size={13} strokeWidth={1.9} aria-hidden="true" />
            设置<ChevronDown size={12} strokeWidth={2.4} aria-hidden="true" />
            {!releaseSeen && <i className="shell-release-dot" aria-label="有新版本" />}
          </button>
          {settingsOpen && (
            <div className="shell-settings-menu" role="menu">
              <div className="shell-settings-section">
                <div className="shell-tools-menu-head"><Palette size={12} strokeWidth={1.9} aria-hidden="true" />界面主题</div>
                <div className="shell-settings-themes" role="radiogroup" aria-label="界面主题">
                  {THEMES.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      role="radio"
                      aria-checked={theme === item.key}
                      className={`shell-theme-option ${theme === item.key ? 'is-active' : ''}`}
                      title={item.desc}
                      onClick={() => void changeTheme(item.key)}
                    >
                      <i className={`theme-swatch is-${item.key}`} aria-hidden="true" />
                      <span>{item.label}</span>
                      {theme === item.key && <Check size={12} strokeWidth={2.3} aria-hidden="true" />}
                    </button>
                  ))}
                </div>
              </div>

              <div className="shell-settings-section">
                <div className="shell-tools-menu-head">版本</div>
                <button
                  type="button"
                  className="shell-tools-menu-item shell-release-entry"
                  onClick={openChangelog}
                >
                  <History size={13} strokeWidth={1.9} aria-hidden="true" />
                  <span>更新记录</span>
                  <em>v{APP_VERSION}</em>
                  {!releaseSeen && <b>新</b>}
                </button>
              </div>

              <div className="shell-settings-section">
                <div className="shell-tools-menu-head">模型接口</div>
                <button
                  type="button"
                  className="shell-tools-menu-item"
                  onClick={() => void openSettings('models')}
                >
                  <Settings size={13} strokeWidth={1.9} aria-hidden="true" />
                  <span>供应商、模型和参数范围</span>
                </button>
              </div>

              <div className="shell-settings-section">
                <div className="shell-tools-menu-head">素材存储</div>
                <button
                  type="button"
                  className="shell-tools-menu-item"
                  onClick={() => void openSettings('storage')}
                >
                  <CloudUpload size={13} strokeWidth={1.9} aria-hidden="true" />
                  <span>对象存储 TOS</span>
                </button>
              </div>

              {/* 本机单用户模式没有账号表：改密要 account_id（服务端会回 401「请先登录」），
                  创建账号更是无凭无据。两项都按事实隐藏，避免点进去撞死路。 */}
              {!localMode && (
                <div className="shell-settings-section">
                  <div className="shell-tools-menu-head">账号安全</div>
                  <button
                    type="button"
                    className="shell-tools-menu-item"
                    onClick={() => { setSettingsOpen(false); setPasswordOpen(true); }}
                  >
                    <KeyRound size={13} strokeWidth={1.9} aria-hidden="true" />
                    <span>修改密码</span>
                  </button>
                </div>
              )}

              {isAdmin && !localMode && (
                <div className="shell-settings-section">
                  <div className="shell-tools-menu-head">管理员</div>
                  <button
                    type="button"
                    className="shell-tools-menu-item"
                    onClick={() => void openSettings('accounts')}
                  >
                    <Plus size={13} strokeWidth={1.9} aria-hidden="true" />
                    <span>创建账号</span>
                  </button>
                </div>
              )}

              <div className="shell-settings-section">
                <div className="shell-tools-menu-head">导入投喂载荷</div>
                <div className="shell-tools-field">
                  <input
                    value={feedPath}
                    onChange={e => setFeedPath(e.target.value)}
                    placeholder="载荷 JSON 路径，或选择本地文件"
                    title={feedPath || '例如 D:\\proj\\feed.json'}
                    spellCheck={false}
                    onKeyDown={e => { if (e.key === 'Enter' && !busy) void importFeed(); }}
                  />
                  <button
                    type="button"
                    className="shell-tools-pick"
                    disabled={busy}
                    title="选择本地投喂载荷 JSON"
                    aria-label="选择本地投喂载荷 JSON"
                    onClick={() => feedFileRef.current?.click()}
                  >
                    <FolderOpen size={14} strokeWidth={1.9} aria-hidden="true" />
                  </button>
                  <button type="button" className="shell-tools-submit" disabled={busy} onClick={() => void importFeed()}>
                    <Upload size={12} strokeWidth={2.1} aria-hidden="true" />
                    {busy ? '导入中…' : '导入'}
                  </button>
                </div>
                <input
                  ref={feedFileRef}
                  type="file"
                  accept="application/json,.json"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) void importFeedFile(file);
                  }}
                />
                <button
                  type="button"
                  className="shell-tools-menu-item"
                  onClick={() => void exportAssetRecord()}
                >
                  <Download size={13} strokeWidth={1.9} aria-hidden="true" />
                  <span>导出资产记录 JSON</span>
                </button>
              </div>
            </div>
          )}
        </div>
        {onLogout && (
          <button
            type="button"
            className="shell-topbar-btn shell-logout-btn"
            onClick={onLogout}
            title="退出登录"
            aria-label="退出登录"
          >
            <LogOut size={13} strokeWidth={1.9} aria-hidden="true" />
            退出
          </button>
        )}
      </div>
    </div>
  );

  const handleCanvasBackgroundModeChange = useCallback(async (mode: 'dots' | 'lines' | 'blank') => {
    const saved = await saveTenantPreferences({
      canvasBackgroundMode: mode,
    });
    const committed = saved.preferences?.canvasBackgroundMode;
    setCanvasBackgroundMode(
      committed === 'dots'
        || committed === 'lines'
        || committed === 'blank'
        ? committed
        : mode,
    );
    onPreferencesSaved?.(
      mergeSavedPreferences(preferencesRef.current, saved.preferences || {}),
    );
  }, []);

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--v-canvas)', color: 'var(--v-text)'}}>
      {actions}
      <div style={{flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column'}}>
        {!projectsLoaded && !pid ? (
          <div className="canvas-empty-state" aria-live="polite">
            <span className="canvas-empty-icon"><FolderOpen size={22} strokeWidth={1.7} /></span>
            <h2>正在恢复工作台</h2>
            <p>读取项目列表并打开上次的画布。</p>
            <button type="button" onClick={() => void loadProjects()}>
              重新读取
            </button>
          </div>
        ) : pid ? (
          // 画布单独一层边界：画布崩了顶栏还在，能直接切项目自救，不用整页重开
          <ErrorBoundary scope="canvas" label="画布" key={pid}>
            <CanvasView
              key={`${pid}:${canvasEpoch}`}
              session={pid}
              initialBackgroundMode={canvasBackgroundMode}
              onBackgroundModeChange={handleCanvasBackgroundModeChange}
            />
          </ErrorBoundary>
        ) : (
          <div className="canvas-empty-state">
            <span className="canvas-empty-icon"><FolderPlus size={22} strokeWidth={1.7} /></span>
            <h2>还没有项目</h2>
            <p>创建第一个项目，开始搭建你的画布工作流。</p>
            <button type="button" onClick={() => void createProject()}>
              <Plus size={15} strokeWidth={2.2} aria-hidden="true" />
              创建项目
            </button>
          </div>
        )}
      </div>
      {modelCatalogSnapshot && (
        <div
          className={`settings-dialog-slot ${modelSettingsOpen ? 'is-open' : 'is-closed'}`}
          aria-hidden={!modelSettingsOpen}
        >
          <ErrorBoundary scope="dialog" label="模型接口设置" onDismiss={() => setModelSettingsOpen(false)}>
            <ModelSettingsDialog
              initialCatalog={modelCatalogSnapshot}
              onCatalogChange={setModelCatalogSnapshot}
              onClose={() => setModelSettingsOpen(false)}
            />
          </ErrorBoundary>
        </div>
      )}
      {storageSnapshot && (
        <div
          className={`settings-dialog-slot ${tosSettingsOpen ? 'is-open' : 'is-closed'}`}
          aria-hidden={!tosSettingsOpen}
        >
          <ErrorBoundary scope="dialog" label="对象存储设置" onDismiss={() => setTosSettingsOpen(false)}>
            <TosSettingsDialog
              initialStorage={storageSnapshot}
              onStorageChange={setStorageSnapshot}
              onClose={() => setTosSettingsOpen(false)}
            />
          </ErrorBoundary>
        </div>
      )}
      {passwordOpen && (
        <ErrorBoundary scope="dialog" label="修改密码" onDismiss={() => setPasswordOpen(false)}>
          <ChangePasswordDialog onClose={() => setPasswordOpen(false)} />
        </ErrorBoundary>
      )}
      {isAdmin && adminAccountsOpen && (
        <ErrorBoundary scope="dialog" label="账号管理" onDismiss={() => setAdminAccountsOpen(false)}>
          <AdminAccountsDialog
            initialSnapshot={accountsSnapshot ?? undefined}
            onSnapshotChange={setAccountsSnapshot}
            onClose={() => setAdminAccountsOpen(false)}
          />
        </ErrorBoundary>
      )}
      {changelogOpen && (
        // 异步 chunk 在发版后可能 404（用户没刷新页面），这里一并兜住，
        // 否则点击「更新记录」会让整棵树抛错。
        <ErrorBoundary scope="dialog" label="更新记录" onDismiss={() => setChangelogOpen(false)}>
          <Suspense fallback={null}>
            <ChangelogDialog onClose={() => setChangelogOpen(false)} />
          </Suspense>
        </ErrorBoundary>
      )}
    </div>
  );
}

const EMPTY_ACCOUNTS_SNAPSHOT: ManagedAccountsSnapshot = {
  accounts: [],
  onlineCount: 0,
  onlineUsers: [],
  generatedAt: '',
};

function AdminAccountsDialog({
  onClose,
  initialSnapshot,
  onSnapshotChange,
}: {
  onClose: () => void;
  initialSnapshot?: ManagedAccountsSnapshot;
  onSnapshotChange?: (snapshot: ManagedAccountsSnapshot) => void;
}) {
  // 打开入口没有预取快照，这里必须容忍 undefined：直接读 initialSnapshot.accounts
  // 会让管理员点「账号管理」瞬间 TypeError 白屏。首屏用空快照，挂载后立即拉真实数据。
  const [accounts, setAccounts] = useState<ManagedAccount[]>(
    () => initialSnapshot?.accounts ?? EMPTY_ACCOUNTS_SNAPSHOT.accounts,
  );
  const [onlineCount, setOnlineCount] = useState(initialSnapshot?.onlineCount ?? 0);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [resetTarget, setResetTarget] = useState<ManagedAccount | null>(null);
  const [resetPassword, setResetPassword] = useState('');
  const [resetConfirm, setResetConfirm] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const knownOnlineRef = useRef<Set<string>>(new Set());
  const onlineInitializedRef = useRef(false);

  const load = async () => {
    try {
      const snapshot = await getManagedAccounts();
      onSnapshotChange?.(snapshot);
      setAccounts(snapshot.accounts);
      setOnlineCount(snapshot.onlineCount);
      const current = new Set(snapshot.onlineUsers.map((account) => account.id));
      if (onlineInitializedRef.current) {
        const joined = snapshot.onlineUsers.filter(
          (account) => !knownOnlineRef.current.has(account.id),
        );
        if (joined.length > 0) {
          setNotice(
            joined.length === 1
              ? `${joined[0].username} 已上线`
              : `${joined.map((account) => account.username).join('、')} 已上线`,
          );
        }
      }
      knownOnlineRef.current = current;
      onlineInitializedRef.current = true;
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '读取账号失败');
    }
  };

  useEffect(() => {
    // 首次挂载立刻拉一次，不能只靠 30 秒轮询——否则打开弹窗后要空等半分钟才有内容。
    void load();
    const timer = window.setInterval(() => { void load(); }, 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError('');
    setNotice('');
    setBusy(true);
    try {
      const created = await createManagedAccount(username.trim(), password);
      setUsername('');
      setPassword('');
      setNotice(`账号 ${created.username} 已创建，独立空间：${created.slug}`);
      await load();
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : '创建账号失败');
    } finally {
      setBusy(false);
    }
  };

  const toggleStatus = async (account: ManagedAccount) => {
    if (busy) return;
    const nextStatus = account.status === 'active' ? 'disabled' : 'active';
    if (nextStatus === 'disabled') {
      const confirmed = await dialog.confirm({
        title: `停用账号「${account.username}」？`,
        message: '停用后该账号的现有会话会立即失效，项目与配置数据会保留。',
        tone: 'danger',
        confirmText: '停用账号',
      });
      if (!confirmed) return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await setManagedAccountStatus(account.id, nextStatus);
      setNotice(`${account.username} 已${nextStatus === 'active' ? '启用' : '停用'}`);
      await load();
    } catch (statusError) {
      setError(statusError instanceof Error ? statusError.message : '账号状态更新失败');
    } finally {
      setBusy(false);
    }
  };

  const submitReset = async () => {
    if (!resetTarget || busy) return;
    if (resetPassword.length < 8) {
      setError('新密码至少需要 8 位');
      return;
    }
    if (resetPassword !== resetConfirm) {
      setError('两次输入的密码不一致');
      return;
    }
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await resetManagedAccountPassword(resetTarget.id, resetPassword);
      setNotice(`${resetTarget.username} 的密码已重置，旧会话已失效`);
      setResetTarget(null);
      setResetPassword('');
      setResetConfirm('');
      await load();
    } catch (resetError) {
      setError(resetError instanceof Error ? resetError.message : '密码重置失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="canvas-admin-dialog" role="dialog" aria-modal="true" aria-labelledby="admin-accounts-title">
        <header>
          <div>
            <h2 id="admin-accounts-title">账号管理</h2>
            <p>创建、重置密码、启用或停用成员账号；当前 {onlineCount} 人在线，每 30 秒刷新。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={16} strokeWidth={2} /></button>
        </header>
        <form onSubmit={submit}>
          <label>
            <span>登录账号</span>
            <input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="off" placeholder="例如 usera" required />
          </label>
          <label>
            <span>初始密码</span>
            <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="new-password" placeholder="至少 8 位" required />
          </label>
          <button type="submit" className="primary" disabled={busy || !username.trim() || password.length < 8}>
            {busy ? '创建中…' : '创建独立账号'}
          </button>
        </form>
        {error && <div className="canvas-password-error" role="alert">{error}</div>}
        {notice && <div className="canvas-admin-notice">{notice}</div>}
        <div className="canvas-admin-accounts">
          {accounts.map((account) => (
            <div key={account.id} className="canvas-admin-account-row">
              <div className="canvas-admin-account-copy">
                <strong>
                  {account.username}
                  <i className={account.online ? 'is-online' : 'is-offline'}>
                    {account.online ? '在线' : '离线'}
                  </i>
                  <i className={account.status === 'active' ? 'is-active' : 'is-disabled'}>
                    {account.status === 'active' ? '已启用' : '已停用'}
                  </i>
                </strong>
                <small>
                  {account.slug} · {account.role === 'admin' ? '管理员' : '成员'} · {account.lastLoginAt ? `最近登录 ${new Date(account.lastLoginAt).toLocaleString()}` : '尚未登录'}
                  {account.lastSeenAt ? ` · 最后活动 ${new Date(account.lastSeenAt).toLocaleString()}` : ''}
                </small>
              </div>
              <div className="canvas-admin-account-actions">
                <button type="button" disabled={busy} onClick={() => {
                  setResetTarget(account);
                  setResetPassword('');
                  setResetConfirm('');
                  setError('');
                  setNotice('');
                }}>重置密码</button>
                <button
                  type="button"
                  className={account.status === 'active' ? 'is-danger' : 'is-enable'}
                  disabled={busy}
                  onClick={() => void toggleStatus(account)}
                >
                  {account.status === 'active' ? '停用' : '启用'}
                </button>
              </div>
            </div>
          ))}
          {accounts.length === 0 && <p>暂无账号</p>}
        </div>
        {resetTarget && (
          <div className="canvas-admin-reset-panel">
            <header>
              <strong>重置「{resetTarget.username}」的密码</strong>
              <button type="button" onClick={() => setResetTarget(null)} aria-label="取消重置"><X size={13} /></button>
            </header>
            <div>
              <label>
                <span>新密码</span>
                <input type="password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} autoComplete="new-password" placeholder="至少 8 位" />
              </label>
              <label>
                <span>确认密码</span>
                <input type="password" value={resetConfirm} onChange={(event) => setResetConfirm(event.target.value)} autoComplete="new-password" />
              </label>
            </div>
            <footer>
              <button type="button" onClick={() => setResetTarget(null)}>取消</button>
              <button type="button" className="primary" disabled={busy || resetPassword.length < 8 || !resetConfirm} onClick={() => void submitReset()}>
                {busy ? '重置中…' : '确认重置'}
              </button>
            </footer>
          </div>
        )}
      </section>
    </div>
  );
}

function ChangePasswordDialog({onClose}: {onClose: () => void}) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (newPassword.length < 8) {
      setError('新密码至少需要 8 位');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('两次输入的新密码不一致');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await changePassword(currentPassword, newPassword);
      onClose();
    } catch (changeError) {
      setError(changeError instanceof Error ? changeError.message : '修改失败，请重试');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className="canvas-password-dialog" onSubmit={submit}>
        <header>
          <span><KeyRound size={17} strokeWidth={1.9} aria-hidden="true" /></span>
          <div>
            <h2>修改密码</h2>
            <p>修改后当前会话会自动续签，其他会话将失效。</p>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={16} strokeWidth={2} aria-hidden="true" /></button>
        </header>
        <label>
          <span>当前密码</span>
          <input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" autoFocus required />
        </label>
        <label>
          <span>新密码</span>
          <input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" required />
        </label>
        <label>
          <span>确认新密码</span>
          <input type="password" value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" required />
        </label>
        {error && <div className="canvas-password-error" role="alert">{error}</div>}
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>取消</button>
          <button type="submit" className="primary" disabled={busy || !currentPassword || !newPassword || !confirmPassword}>
            {busy ? '修改中…' : '确认修改'}
          </button>
        </footer>
      </form>
    </div>
  );
}

function AuthGate() {
  const [state, setState] = useState<'checking' | 'anonymous' | 'authenticated'>('checking');
  const [isAdmin, setIsAdmin] = useState(false);
  const [localMode, setLocalMode] = useState(false);
  const [preferences, setPreferences] = useState<ShellPreferences>({});
  const rememberSavedPreferences = useCallback((saved: ShellPreferences) => {
    setPreferences((current) => mergeSavedPreferences(current, saved));
  }, []);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getAuthSession()
      .then((session) => {
        if (!cancelled) {
          setIsAdmin(Boolean(session.isAdmin || session.role === 'admin'));
          setLocalMode(Boolean(session.local));
          setPreferences(session.preferences || {});
          setState(session.authenticated ? 'authenticated' : 'anonymous');
        }
      })
      .catch(() => {
        if (!cancelled) {
          setState('anonymous');
          setError('无法连接认证服务，请稍后重试。');
        }
      });
    const heartbeat = window.setInterval(() => {
      if (!cancelled) void getAuthSession().catch(() => undefined);
    }, 60_000);
    const onRequired = () => setState('anonymous');
    window.addEventListener(AUTH_REQUIRED_EVENT, onRequired);
    return () => {
      cancelled = true;
      window.clearInterval(heartbeat);
      window.removeEventListener(AUTH_REQUIRED_EVENT, onRequired);
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const session = await loginAccount(username.trim(), password);
      setIsAdmin(Boolean(session.isAdmin || session.role === 'admin'));
      setPreferences(session.preferences || {});
      setPassword('');
      setState('authenticated');
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : '登录失败，请重试');
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    try {
      await logoutAccount();
    } catch {
      // The local session may already be invalid; still return to login.
    }
    setIsAdmin(false);
    setState('anonymous');
  };

  if (state === 'checking') {
    return (
      <div className="auth-gate">
        <div className="auth-gate-loading"><ShieldCheck size={18} />正在验证登录状态…</div>
      </div>
    );
  }

  if (state === 'anonymous') {
    return (
      <div className="auth-gate">
        <form className="auth-gate-card" onSubmit={submit}>
          <div className="auth-gate-mark"><ShieldCheck size={22} strokeWidth={1.7} /></div>
          <h1>登录创作工作台</h1>
          <p>使用分配给你的账号进入独立工作区。</p>
          <label>
            <span>账号</span>
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </label>
          <label>
            <span>密码</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && <div className="auth-gate-error" role="alert">{error}</div>}
          <button type="submit" disabled={busy || !username.trim() || !password}>
            {busy ? '登录中…' : '进入工作台'}
          </button>
        </form>
      </div>
    );
  }

  return (
    <Shell
      onLogout={() => void logout()}
      isAdmin={isAdmin}
      localMode={localMode}
      preferences={preferences}
      onPreferencesSaved={rememberSavedPreferences}
    />
  );
}

// 全局异常兜底必须在首次 render 之前装好（否则挂载期抛的错接不到）
installGlobalErrorHandlers();
// 断网时给出明确原因，避免用户把「请求失败」误判成服务坏了。
// 此刻 ToastHost 还没挂载，但 toast 层会把提示排队，挂载后自动回放。
installNetworkStatusToasts();

createRoot(document.getElementById('root')!).render(
  <>
    <ErrorBoundary scope="root">
      <AuthGate />
    </ErrorBoundary>
    <ToastHost />
    {/* 全局对话框宿主：16 处原生 prompt/confirm/alert 的替代实现 */}
    <DialogHost />
  </>,
);
