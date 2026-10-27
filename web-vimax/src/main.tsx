import {useEffect, useRef, useState, type FormEvent, type MouseEvent as ReactMouseEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {createPortal} from 'react-dom';
import {Check, ChevronDown, CloudUpload, Copy, ExternalLink, FolderOpen, FolderPlus, LogOut, MoreHorizontal, Palette, Pencil, Plus, Settings, ShieldCheck, Trash2, Upload, X} from 'lucide-react';
import {CanvasView} from './CanvasView';
import {ModelSettingsDialog} from './ModelSettings';
import {TosSettingsDialog} from './TosSettings';
import {ErrorBoundary, ToastHost, installGlobalErrorHandlers} from './ErrorBoundary';
import {applyTheme, resolveTheme, THEMES, THEME_STORAGE_KEY, type Theme} from './theme';
// ★ 全局对话框：替换原生 prompt/confirm/alert（2026-10-25）
//   原生框由浏览器进程外绘制，CSS 无法触及 —— 用户截图里的白底蓝钮弹窗就是它。
import {DialogHost, dialog} from './GDialog';
import {AUTH_REQUIRED_EVENT, apiUrl, getAuthSession, loginAccount, logoutAccount} from './api';
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
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    // Theme still works without local persistence.
  }
  const prefersDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? true;
  return resolveTheme(stored, prefersDark);
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

// 自研画布工作流入口：项目选择 + 导入载荷 + CanvasView（ViMax 画布内核，对接本地后端）
function Shell({onLogout}: {onLogout?: () => void}) {
  const [projects, setProjects] = useState<{
    id: string;
    title: string;
    running_count?: number;
    failed_count?: number;
    local_blocked_count?: number;
    latest_task_status?: string;
    latest_task_phase?: string;
    latest_task_error_kind?: string;
  }[]>([]);
  const [pid, setPid] = useState<string>('');
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
  const [theme, setTheme] = useState<Theme>(bootTheme);
  const settingsWrapRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Theme still applies when persistence is unavailable.
    }
  }, [theme]);

  const loadProjects = async () => {
    try {
      const d = await fetch(apiUrl('/api/projects'), {credentials: 'include'}).then(r => r.json());
      const list = d.projects || [];
      setProjects(list);
      if (!pid && list.length) {
        const hashId = decodeURIComponent(window.location.hash.replace(/^#/, ''));
        setPid(list.some((item: {id: string}) => item.id === hashId) ? hashId : list[0].id);
      }
    } catch { /* 后端未启动 */ }
  };
  useEffect(() => { void loadProjects(); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => { void loadProjects(); }, 8000);
    return () => window.clearInterval(timer);
  }, []);

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
    setPid(d.id);
    window.location.hash = d.id;
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
      setPid(duplicated.id);
      window.location.hash = duplicated.id;
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
        setPid(fallback?.id || '');
        window.location.hash = fallback?.id || '';
      }
      setProjectOpen(false);
      await loadProjects();
    } catch (error) {
      await dialog.alert({title: '删除项目失败', message: (error as Error).message, tone: 'danger'});
    }
  };

  const beginProjectRename = (projectId = pid) => {
    if (!projectId) return;
    setRenameDraft(projects.find(p => p.id === projectId)?.title || '');
    if (projectId !== pid) {
      setPid(projectId);
      window.location.hash = projectId;
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

  const importFeed = async () => {
    if (!pid) {
      await dialog.alert({title: '还没有选中项目', message: '先选择或新建一个项目。'});
      return;
    }
    if (!feedPath.trim()) {
      await dialog.alert({
        title: '还没填路径',
        message: '请在「项目设置 → 导入投喂载荷」里填写 JSON 文件的完整路径。',
      });
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(apiUrl(`/api/projects/${pid}/import`), {
        method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({feed_path: feedPath.trim()}),
      });
      const d = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(d.error || `导入失败，HTTP ${response.status}`);
      const readiness = d.readiness as {readyToGenerate?: boolean} | undefined;
      await dialog.alert({
        title: readiness?.readyToGenerate ? '导入完成 · 已就绪' : '导入完成 · 待准备素材',
        message: importResultMessage(d),
        tone: readiness?.readyToGenerate ? 'success' : 'default',
      });
      setPid(pid);
      window.location.hash = pid;
      window.location.reload();
    } catch (e) {
      await dialog.alert({title: '导入失败', message: (e as Error).message, tone: 'danger'});
    } finally { setBusy(false); }
  };

  const importFeedFile = async (file: File) => {
    if (!pid) {
      await dialog.alert({title: '还没有选中项目', message: '先选择或新建一个项目。'});
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      await dialog.alert({title: '文件过大', message: '投喂载荷 JSON 不能超过 8MB。'});
      return;
    }
    setBusy(true);
    try {
      const text = (await file.text()).replace(/^\uFEFF/, '');
      const feed = JSON.parse(text);
      if (!feed || typeof feed !== 'object' || !Array.isArray(feed.asset_prompts) || !Array.isArray(feed.segments)) {
        throw new Error('不是有效投喂载荷：缺少 asset_prompts 或 segments');
      }
      const response = await fetch(apiUrl(`/api/projects/${pid}/import`), {
        method: 'POST', credentials: 'include', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({feed_json: feed}),
      });
      const d = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(d.error || `导入失败，HTTP ${response.status}`);
      setFeedPath(file.name);
      const readiness = d.readiness as {readyToGenerate?: boolean} | undefined;
      await dialog.alert({
        title: readiness?.readyToGenerate ? '导入完成 · 已就绪' : '导入完成 · 待准备素材',
        message: importResultMessage(d),
        tone: readiness?.readyToGenerate ? 'success' : 'default',
      });
      setPid(pid);
      window.location.hash = pid;
      window.location.reload();
    } catch (error) {
      await dialog.alert({
        title: '导入失败',
        message: error instanceof Error ? error.message : String(error),
        tone: 'danger',
      });
    } finally {
      setBusy(false);
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
                    onClick={() => { setProjectOpen(false); setProjectActionMenu(null); setPid(p.id); window.location.hash = p.id; }}
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
      <div id="canvas-topbar-slot" className="shell-canvas-slot" />

      <div className="shell-topbar-right">
        <div className="canvas-menu-wrap" ref={settingsWrapRef}>
          <button
            type="button"
            className={`shell-topbar-btn ${settingsOpen ? 'is-open' : ''}`}
            onClick={() => setSettingsOpen((value) => !value)}
            title="主题、模型接口与项目设置"
            aria-expanded={settingsOpen}
          >
            <Settings size={13} strokeWidth={1.9} aria-hidden="true" />
            设置<ChevronDown size={12} strokeWidth={2.4} aria-hidden="true" />
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
                      onClick={() => setTheme(item.key)}
                    >
                      <i className={`theme-swatch is-${item.key}`} aria-hidden="true" />
                      <span>{item.label}</span>
                      {theme === item.key && <Check size={12} strokeWidth={2.3} aria-hidden="true" />}
                    </button>
                  ))}
                </div>
              </div>

              <div className="shell-settings-section">
                <div className="shell-tools-menu-head">模型接口</div>
                <button
                  type="button"
                  className="shell-tools-menu-item"
                  onClick={() => { setSettingsOpen(false); setModelSettingsOpen(true); }}
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
                  onClick={() => { setSettingsOpen(false); setTosSettingsOpen(true); }}
                >
                  <CloudUpload size={13} strokeWidth={1.9} aria-hidden="true" />
                  <span>对象存储 TOS</span>
                </button>
              </div>

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

  return (
    <div style={{display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--v-canvas)', color: 'var(--v-text)'}}>
      {actions}
      <div style={{flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column'}}>
        {pid ? (
          // 画布单独一层边界：画布崩了顶栏还在，能直接切项目自救，不用整页重开
          <ErrorBoundary scope="canvas" label="画布" key={pid}>
            <CanvasView session={pid} />
          </ErrorBoundary>
        ) : <div style={{color: '#8a8a8f', textAlign: 'center', marginTop: 80}}>← 选择或新建项目</div>}
      </div>
      {modelSettingsOpen && <ModelSettingsDialog onClose={() => setModelSettingsOpen(false)} />}
      {tosSettingsOpen && <TosSettingsDialog onClose={() => setTosSettingsOpen(false)} />}
    </div>
  );
}

function AuthGate() {
  const [state, setState] = useState<'checking' | 'anonymous' | 'authenticated'>('checking');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getAuthSession()
      .then((session) => {
        if (!cancelled) setState(session.authenticated ? 'authenticated' : 'anonymous');
      })
      .catch(() => {
        if (!cancelled) {
          setState('anonymous');
          setError('无法连接认证服务，请稍后重试。');
        }
      });
    const onRequired = () => setState('anonymous');
    window.addEventListener(AUTH_REQUIRED_EVENT, onRequired);
    return () => {
      cancelled = true;
      window.removeEventListener(AUTH_REQUIRED_EVENT, onRequired);
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await loginAccount(username.trim(), password);
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

  return <Shell onLogout={() => void logout()} />;
}

// 全局异常兜底必须在首次 render 之前装好（否则挂载期抛的错接不到）
installGlobalErrorHandlers();

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
