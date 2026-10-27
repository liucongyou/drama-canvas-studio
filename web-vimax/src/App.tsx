import {lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode} from 'react';
import {createPortal} from 'react-dom';
import {
  AlertCircle,
  CheckCircle2,
  Info,
  ArrowDown,
  ArrowUp,
  Brain,
  Braces,
  CircleStop,
  Copy,
  Clock3,
  FilePenLine,
  FileText,
  Film,
  Folder,
  FolderPlus,
  Files,
  Image as ImageIcon,
  Menu,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRight,
  Pencil,
  Pin,
  Wand2,
  Check,
  UploadCloud,
  Plus,
  Save,
  Search,
  Settings,
  BarChart3,
  ListChecks,
  Terminal,
  Trash2,
  Sun,
  Wrench,
  X,
  Clapperboard,
  Sparkles,
  History,
  ClipboardList,
  Network,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {deleteSession, getAgentConfig, getArtifacts, getHealth, getHistory, getProjectMeta, getRuntimeConfig, getSessions, HttpError, retryAgentTool, saveAgentConfig, saveProjectMeta, saveRuntimeConfig, sendMessage, startAgent, stopAgent, subscribeToEvents, testAgentConfig, uploadWorkspaceFile} from './api';
import {ArtifactsView, StoryboardPanel, mediaUrl, visualArtifactLabel} from './ArtifactViews';
import {resolveMediaOrientation, type MediaOrientation} from './artifactPresentation';
// P2-4：画布/预算/台账按视图懒加载（react-flow 等只在切到对应视图时才下载），
// 首屏主包明显减小；ArtifactsView 因 StoryboardPanel 与工作区共用保持静态引入。
const BudgetView = lazy(() => import('./BudgetView').then((module) => ({default: module.BudgetView})));
const LedgerView = lazy(() => import('./LedgerView').then((module) => ({default: module.LedgerView})));
const CanvasView = lazy(() => import('./CanvasView').then((module) => ({default: module.CanvasView})));
const ResultsView = lazy(() => import('./ResultsView').then((module) => ({default: module.default})));
import StylePicker from './StylePicker';
import {applyAgentEvent, appendLocalUser, attachMediaByCreatedTime, composeAgentPrompt, createChatState, humanizeStage, humanizeTool, isRetryableToolFailure, localizeAgentText, mergeHistoryMessages, partitionUploadsForSession, replyOptionsForMessage, resolveWorkflowStepIndex, WORKFLOW_STEPS, workflowStepIndexForStage} from './events';
import {matchingSlashCommands, shouldShowSlashCommands, type SlashCommandMatch} from './slashCommands';
import {applyTheme, resolveTheme, isDarkTheme, THEMES, THEME_STORAGE_KEY, type Theme} from './theme';
import {notify, subscribeToToasts, type ToastMessage} from './toast';
import CreationWizard from './CreationWizard';
import {hasModelPresentation, resolveModelPresentation} from './modelPresentation';
import type {AgentConfig, AgentEvent, Artifact, ChatState, ConfigSection, Message, SessionSummary, WorkflowPlan, WorkspaceUpload} from './types';

const CONTEXT_TARGET = 160_000;
const PROJECT_NAMES_KEY = 'yingjie-project-names';
const PINNED_PROJECTS_KEY = 'yingjie-pinned-projects';
const LAST_SESSION_KEY = 'yingjie-last-session';

function readStoredJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? JSON.parse(raw) as T : fallback;
  } catch {
    return fallback;
  }
}

function storeJson(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Local persistence is optional in hardened browser contexts.
  }
}

type WorkspaceView = 'workspace' | 'results' | 'artifacts' | 'budget' | 'ledger' | 'canvas' | 'settings';
type UploadItem = {id: string; name: string; status: 'uploading' | 'done' | 'error'; error?: string};

/** 批量任务进度：done/total + 首批次时间戳（用于外推预计剩余时间）。 */
type TaskProgress = {done: number; total: number; startedAt: number; label: string};

/**
 * 预计剩余时间：用"已完成批次的外推速率"估算，并给出可读文案。
 * done=0 时无法确定速率（第一批还没完成），只报"预计中"，不编造数字。
 */
function formatEta(task: TaskProgress): string {
  const {done, total, startedAt} = task;
  if (done >= total) return '即将完成';
  if (done <= 0) return '预计中…';
  const elapsed = Date.now() - startedAt;
  if (elapsed <= 0) return '预计中…';
  const remainingMs = (elapsed / done) * (total - done);
  const seconds = Math.round(remainingMs / 1000);
  if (seconds < 60) return `预计剩余 ${seconds} 秒`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `预计剩余约 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes > 0 ? `预计剩余约 ${hours} 小时 ${restMinutes} 分` : `预计剩余约 ${hours} 小时`;
}

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  // 每个 session 的实时运行文本（来自 SSE tool_progress/tool_start），侧栏据此显示"● 运行中 · 正在…"
  const [runningTextBySession, setRunningTextBySession] = useState<Record<string, string>>({});
  // 批量进度（done/total）：渲染这类长任务只有一句"正在生成第 3 批"时，用户无法判断
  // 还要等多久、是否值得继续等。记录首批次到达时间即可外推预计剩余时间。
  const [taskProgress, setTaskProgress] = useState<TaskProgress | null>(null);
  const [projectNames, setProjectNames] = useState<Record<string, string>>(() => readStoredJson(PROJECT_NAMES_KEY, {}));
  const [pinnedProjects, setPinnedProjects] = useState<string[]>(() => readStoredJson(PINNED_PROJECTS_KEY, []));
  const [selectedSessionId, setSelectedSessionId] = useState('');
  const [chat, setChat] = useState<ChatState>(() => createChatState());
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const [historyCursor, setHistoryCursor] = useState('');
  const [historyLoadingOlder, setHistoryLoadingOlder] = useState(false);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  // P1-6：产物接口分页——服务端显式回传 truncated 提示"产物过多已省略"；前端滚动加载更多
  const [artifactsTruncated, setArtifactsTruncated] = useState(false);
  const [artifactsTotal, setArtifactsTotal] = useState(0);
  const [artifactsPage, setArtifactsPage] = useState(1);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [artifactsLoadingMore, setArtifactsLoadingMore] = useState(false);
  // 成片数量（导航「我的成片」角标）：统计 final_video.mp4，按集去重
  const finalCount = useMemo(() => {
    const keys = new Set<string>();
    for (const a of artifacts) {
      const norm = a.path.replace(/\\/g, '/');
      if (!/final_video\.mp4$/i.test(norm)) continue;
      const m = norm.match(/episodes\/([^/]+)\/final_video\.mp4$/i);
      keys.add(m ? m[1] : 'main');
    }
    return keys.size;
  }, [artifacts]);
  const ARTIFACTS_PAGE_SIZE = 500;
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>('workspace');
  const [agentReady, setAgentReady] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [storyboardPanelOpen, setStoryboardPanelOpen] = useState(false);
  const [storyboardCount, setStoryboardCount] = useState(0);
  const [theme, setTheme] = useState<Theme>(() => {
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    } catch {
      // 无持久化时回退
    }
    return resolveTheme(stored ?? document.documentElement.dataset.theme, false);
  });
  const [draft, setDraft] = useState('');
  const [workspaceUploads, setWorkspaceUploads] = useState<WorkspaceUpload[]>([]);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const [uploadItems, setUploadItems] = useState<UploadItem[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [loadError, setLoadError] = useState('');
  // 首次加载态：服务重启/构建窗口期 load 可能耗时，先显示恢复提示避免白板
  const [initializing, setInitializing] = useState(true);
  // 手动重试：点击「重试」自增，触发 load effect 重新执行（无需刷新整页）
  const [loadRetry, setLoadRetry] = useState(0);
  const loadAgain = () => setLoadRetry((n) => n + 1);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectError, setNewProjectError] = useState('');
  const [creatingProject, setCreatingProject] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<SessionSummary>();
  const [deleting, setDeleting] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const knownMediaRef = useRef<Set<string>>(new Set());
  const selectedSessionRef = useRef('');
  const connectionRef = useRef(true);
  const everConnectedRef = useRef(false);
  const lastDisconnectToastAtRef = useRef(0);
  const serverInstanceRef = useRef('');
  const dragDepthRef = useRef(0);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const syncScrollToBottomState = useCallback((element: HTMLDivElement | null = scrollRef.current) => {
    if (!element) return;
    const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    setShowScrollToBottom(distanceFromBottom > 24);
  }, []);
  // 会话切换竞态防护：只接受最近一次手动选择的响应
  const sessionRequestRef = useRef(0);
  const historyCursorRef = useRef('');
  const historySessionRef = useRef('');
  // 手动锁定：用户点击侧栏项目后，服务端推送的 activeSessionId 不得覆盖用户选择
  const manualSessionRef = useRef('');

  const selectedSession = sessions.find((session) => session.sessionId === selectedSessionId);
  const getSessionTitle = useCallback((session: SessionSummary) => projectNames[session.sessionId]?.trim() || sessionTitle(session), [projectNames]);
  useEffect(() => { selectedSessionRef.current = selectedSessionId; }, [selectedSessionId]);
  // P1-5：挂载时拉取服务端项目偏好（重命名/置顶），服务端优先合并进本地状态；
  // 本地 localStorage 保留作离线兜底/首次迁移来源。
  useEffect(() => {
    let cancelled = false;
    getProjectMeta().then((meta) => {
      if (cancelled) return;
      setProjectNames((current) => {
        const next = {...current};
        for (const [sid, name] of Object.entries(meta.names || {})) {
          if (name) next[sid] = name;
        }
        return next;
      });
      setPinnedProjects((current) => Array.from(new Set([...current, ...(meta.pinned || [])])));
    }).catch(() => { /* 服务端暂不可用则保持本地偏好 */ });
    return () => { cancelled = true; };
  }, []);
  // P1-5：重命名/置顶互相读取对方最新值时使用 ref（state 回调内不便直接读另一 state）
  const pinnedProjectsRef = useRef<string[]>([]);
  const projectNamesRef = useRef<Record<string, string>>({});
  useEffect(() => { pinnedProjectsRef.current = pinnedProjects; }, [pinnedProjects]);
  useEffect(() => { projectNamesRef.current = projectNames; }, [projectNames]);
  const persistProjectMeta = useCallback((names: Record<string, string>, pinned: string[]) => {
    saveProjectMeta({names, pinned}).catch(() => { /* 网络失败不阻塞本地体验，下次操作再同步 */ });
  }, []);
  const renameProject = useCallback((sessionId: string, nextName: string) => {
    const name = nextName.trim().slice(0, 64);
    setProjectNames((current) => {
      const next = {...current};
      if (name) next[sessionId] = name;
      else delete next[sessionId];
      storeJson(PROJECT_NAMES_KEY, next);
      persistProjectMeta(next, pinnedProjectsRef.current);
      window.setTimeout(() => notify(name ? '项目已重命名' : '已恢复项目名'), 0);
      return next;
    });
  }, [persistProjectMeta]);
  const togglePinnedProject = useCallback((sessionId: string) => {
    setPinnedProjects((current) => {
      const next = current.includes(sessionId) ? current.filter((id) => id !== sessionId) : [...current, sessionId];
      storeJson(PINNED_PROJECTS_KEY, next);
      persistProjectMeta(projectNamesRef.current, next);
      window.setTimeout(() => notify(current.includes(sessionId) ? '已取消置顶' : '项目已置顶'), 0);
      return next;
    });
  }, [persistProjectMeta]);
  const slashMatches = useMemo(() => matchingSlashCommands(draft), [draft]);
  const showSlashCommands = shouldShowSlashCommands(draft, chat.busy);
  const runningActivity = useMemo(
    () => [...chat.messages].reverse().find((message) => message.role === 'activity' && message.status === 'running'),
    [chat.messages],
  );
  const runningRender = useMemo(
    () => [...chat.messages].reverse().find((message) => message.role === 'activity'
      && message.status === 'running'
      && (message.tool || '').toLowerCase().includes('render_video')),
    [chat.messages],
  );

  useEffect(() => {
    applyTheme(theme);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Theme still applies when persistence is unavailable.
    }
  }, [theme]);

  const refreshSessions = useCallback(async () => {
    const state = await getSessions();
    setSessions(state.sessions);
    return state;
  }, []);

  const refreshArtifacts = useCallback(async (sessionId: string) => {
    if (!sessionId) {
      setArtifacts([]);
      setArtifactsTruncated(false);
      setArtifactsTotal(0);
      setArtifactsPage(1);
      setArtifactsLoading(false);
      return {artifacts: [] as Artifact[]};
    }
    setArtifactsLoading(true);
    try {
      const payload = await getArtifacts(sessionId, 1, ARTIFACTS_PAGE_SIZE);
      // A slow response from the previous project must not overwrite the newly selected one.
      if (selectedSessionRef.current !== sessionId) return payload;
      setArtifacts(payload.artifacts);
      setArtifactsTotal(Number(payload.total) || payload.artifacts.length);
      setArtifactsPage(1);
      setArtifactsTruncated(Boolean(payload.truncated));
      const newMedia = payload.artifacts.filter(
        (artifact) => (artifact.kind === 'image' || artifact.kind === 'video') && !knownMediaRef.current.has(artifact.path),
      );
      newMedia.forEach((artifact) => knownMediaRef.current.add(artifact.path));
      if (newMedia.length > 0) attachMediaToLatestActivity(newMedia);
      return payload;
    } finally {
      setArtifactsLoading(false);
    }
  }, []);

  const loadMoreArtifacts = useCallback(async () => {
    const sessionId = selectedSessionRef.current;
    if (!sessionId || artifactsLoadingMore) return;
    setArtifactsLoadingMore(true);
    try {
      const nextPage = artifactsPage + 1;
      const payload = await getArtifacts(sessionId, nextPage, ARTIFACTS_PAGE_SIZE);
      if (selectedSessionRef.current !== sessionId) return;
      setArtifacts((current) => {
        const seen = new Set(current.map((a) => a.path));
        return [...current, ...payload.artifacts.filter((a) => !seen.has(a.path))];
      });
      setArtifactsPage(nextPage);
      setArtifactsTotal(Number(payload.total) || artifactsTotal);
    } finally {
      setArtifactsLoadingMore(false);
    }
  }, [artifactsPage, artifactsLoadingMore, artifactsTotal]);

  // 产物一律按写入时间挂回对应活动行（见 attachMediaByCreatedTime）。
  // 不要再按工具名判断：vimax_regenerate / vimax_generate_assets 这类名字不含 render 的
  // 生成工具产出的单个分段视频、定妆图、场景/道具图会被整批漏掉。
  function attachMediaToLatestActivity(media: Artifact[]) {
    if (media.length === 0) return;
    setChat((current) => ({...current, messages: attachMediaByCreatedTime(current.messages, media)}));
  }

  function attachSessionVideosToChat(videos: Artifact[]) {
    if (videos.length === 0) return;
    setChat((current) => ({...current, messages: attachMediaByCreatedTime(current.messages, videos)}));
  }

  const recoverHistory = useCallback(async (silent = false) => {
    const sessionId = selectedSessionRef.current;
    if (!sessionId) return;
    const requestId = sessionRequestRef.current;
    try {
      const history = await getHistory(sessionId);
      if (selectedSessionRef.current !== sessionId || requestId !== sessionRequestRef.current) return;
      setChat((current) => {
        if (!current.messages.length) return createChatState(history.messages, current.workflowPlan);
        return {...current, messages: mergeHistoryMessages(current.messages, history.messages)};
      });
      if (historySessionRef.current !== sessionId) {
        historySessionRef.current = sessionId;
        historyCursorRef.current = history.nextCursor;
        setHistoryCursor(history.nextCursor);
        setHistoryHasMore(history.hasMore);
      }
      if (!silent) notify('连接已恢复，聊天记录已同步', 'info');
    } catch (error) {
      notify(error instanceof Error ? error.message : '聊天记录同步失败', 'error');
    }
  }, []);

  const checkServerInstance = useCallback(async () => {
    try {
      const health = await getHealth();
      if (!health.instanceId) return;
      if (!serverInstanceRef.current) {
        serverInstanceRef.current = health.instanceId;
        return;
      }
      if (health.instanceId !== serverInstanceRef.current) {
        // 服务进程换了实例说明后端已重启；旧标签页继续运行旧 JS 很难自行恢复，
        // 自动刷新一次即可拿到与当前后端匹配的前端资源。
        serverInstanceRef.current = health.instanceId;
        notify('本地服务已更新，正在刷新页面', 'info');
        window.setTimeout(() => window.location.reload(), 150);
      }
    } catch {
      // SSE 连接状态会继续负责重连提示，这里不重复打扰用户。
    }
  }, []);

  useEffect(() => subscribeToEvents((event) => {
    if (event.type === 'sessions_changed') {
      const nextSessions = event.sessions || [];
      setSessions(nextSessions);
      // 清理已不在运行中的 session 的侧栏实时文本（服务端 running 字段为准）
      setRunningTextBySession((current) => {
        const liveIds = new Set(nextSessions.filter((session) => session.running).map((session) => session.sessionId));
        const stale = Object.keys(current).filter((id) => !liveIds.has(id));
        if (stale.length === 0) return current;
        const next = {...current};
        for (const id of stale) delete next[id];
        return next;
      });
      const selectedId = selectedSessionRef.current;
      const restoredSession = selectedId
        ? nextSessions.find((session) => session.sessionId === selectedId)
        : undefined;
      const restoredPlan = restoredSession?.workflowPlan?.title ? restoredSession.workflowPlan : undefined;
      // 旧标签页重连时，会话快照是恢复任务卡的主要来源之一。
      // 运行中的实时事件优先；空闲时服务端快照同时也是清掉陈旧计划的依据。
      if (restoredSession) {
        setChat((current) => current.busy ? current : {...current, workflowPlan: restoredPlan});
      }
      // 只在本地还没有选择时跟随服务端 activeSessionId。
      // 首屏 load 已经确定会话后，不能被并发到达的 sessions_changed 抢走，
      // 否则历史请求会被判定为过期，页面最终落在空工作区。
      if (event.activeSessionId && !manualSessionRef.current && !selectedSessionRef.current) {
        selectedSessionRef.current = event.activeSessionId;
        setSelectedSessionId(event.activeSessionId);
      }
      return;
    }
    const eventSessionId = event.session_id || event.session?.active_session_id || '';
    // 全局运行状态：不依赖当前选中项目，任何 session 的工具进度都记录到侧栏。
    // 之前在这里提前 return 丢弃了其他项目的事件，导致侧栏看不到"别的项目正在跑"。
    if (eventSessionId) {
      if (event.type === 'tool_start' || event.type === 'tool_progress') {
        const text = event.type === 'tool_progress'
          ? (event.progress?.message || humanizeTool(event.tool?.name || event.tool?.requested_name || 'tool'))
          : `正在执行 ${humanizeTool(event.tool?.name || event.tool?.requested_name || 'tool')}…`;
        setRunningTextBySession((current) => ({...current, [eventSessionId]: text}));
        // 批量进度：管线在 video_batch_start/done 等事件里回传 {done, total}
        const meta = event.progress?.metadata as Record<string, unknown> | undefined;
        if (meta && typeof meta.total === 'number' && typeof meta.done === 'number' && meta.total > 0) {
          const total = meta.total;
          const done = Math.min(Math.max(meta.done, 0), total);
          setTaskProgress((current) => ({
            done,
            total,
            // 同一批任务（total 相同）沿用首次时间戳；换任务则重新计时
            startedAt: current && current.total === total && done >= current.done ? current.startedAt : Date.now(),
            label: text,
          }));
        }
      } else if (event.type === 'tool_result' || event.type === 'done' || event.type === 'error') {
        setTaskProgress(null);
        setRunningTextBySession((current) => {
          if (!current[eventSessionId]) return current;
          const next = {...current};
          delete next[eventSessionId];
          return next;
        });
      } else if (event.type === 'bridge_status' && (event.status === 'stopped' || event.status === 'error')) {
        setTaskProgress(null);
        setRunningTextBySession((current) => {
          if (!current[eventSessionId]) return current;
          const next = {...current};
          delete next[eventSessionId];
          return next;
        });
      }
    }
    if (eventSessionId && selectedSessionRef.current && eventSessionId !== selectedSessionRef.current) return;
    if (event.type === 'bridge_status') {
      if (event.status === 'ready' || event.status === 'starting') setAgentReady(true);
      if (event.status === 'ready') void recoverHistory(true);
      if (event.status === 'stopped' || event.status === 'error') setAgentReady(false);
    }
    if (event.type === 'session') {
      const sessionId = event.session_id || event.session?.session?.session_id || event.session?.active_session_id || '';
      if (sessionId && !manualSessionRef.current && !selectedSessionRef.current) {
        selectedSessionRef.current = sessionId;
        setSelectedSessionId(sessionId);
        storeJson(LAST_SESSION_KEY, sessionId);
        void refreshSessions();
        void refreshArtifacts(sessionId);
      }
    }
    setChat((current) => applyAgentEvent(current, event));
  }, (connected) => {
    // 连接提示去重 + 防抖：SSE 断线重连（EventSource 自动重连）在网络抖动时会
    // 连续触发 error/open 循环。用稳定 toast id（ToastHost 按 id 去重，后弹覆盖
    // 先弹，不堆叠）且断开提示只弹一次，恢复提示只在"断开后真正恢复"时弹。
    if (!connected && connectionRef.current) {
      const now = Date.now();
      const lastDisconnectAt = lastDisconnectToastAtRef.current;
      if (!lastDisconnectAt || now - lastDisconnectAt > 3_000) {
        notify('连接已断开，正在自动重连', 'error', 'conn-lost');
        lastDisconnectToastAtRef.current = now;
      }
    }
    if (connected && !connectionRef.current && everConnectedRef.current) void recoverHistory();
    if (connected && !connectionRef.current) {
      notify('连接已恢复，聊天记录已同步', 'info', 'conn-restored');
    }
    if (connected) {
      everConnectedRef.current = true;
      void checkServerInstance();
    }
    connectionRef.current = connected;
  }), [checkServerInstance, recoverHistory, refreshArtifacts, refreshSessions]);

  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    const load = async () => {
      try {
        const state = await refreshSessions();
        if (cancelled) return;
        // 刷新恢复优先级：本地记忆的最后打开项目 > 服务端 activeSessionId。
        // 服务端 activeSessionId 是磁盘全局单值，多项目并发时会被后启动的 agent
        // set_active 覆盖，直接跟随会导致刷新后跳到别的项目（串项目）。
        const remembered = readStoredJson<string>(LAST_SESSION_KEY, '');
        const rememberedExists = remembered && state.sessions.some((session) => session.sessionId === remembered);
        const restoreId = rememberedExists ? remembered : state.activeSessionId || '';
        const activeSessionExists = Boolean(
          restoreId && state.sessions.some((session) => session.sessionId === restoreId),
        );
        // 没有有效活动会话时必须正常退出初始化态，展示空工作区；
        // 旧逻辑直接 return，会把界面永久锁在“正在恢复会话数据…”。
        if (!activeSessionExists) {
          selectedSessionRef.current = '';
          setSelectedSessionId('');
          setAgentReady(false);
          setChat(createChatState());
          setArtifacts([]);
          setInitializing(false);
          setLoadError('');
          return;
        }
        if (rememberedExists) manualSessionRef.current = remembered;
        selectedSessionRef.current = restoreId;
        setSelectedSessionId(restoreId);
        const [history, artifactState] = await Promise.all([
          getHistory(restoreId),
          refreshArtifacts(restoreId),
        ]);
        if (cancelled) return;
        if (selectedSessionRef.current === restoreId) {
          historySessionRef.current = restoreId;
          historyCursorRef.current = history.nextCursor;
          setHistoryCursor(history.nextCursor);
          setHistoryHasMore(history.hasMore);
          // 历史恢复与 SSE 实时进度合并：保留当前仍在运行的活动行
          setChat((current) => {
            const restoredPlan = state.sessions.find((session) => session.sessionId === restoreId)?.workflowPlan;
            const fresh = createChatState(history.messages, restoredPlan);
            const running = (current?.messages || []).filter((m) => m.role === 'activity' && m.status === 'running');
            if (!running.length) return fresh;
            return {...fresh, messages: [...fresh.messages, ...running]};
          });
          attachSessionVideosToChat((artifactState?.artifacts || []).filter((artifact) => artifact.kind === 'video'));
          // 刷新后恢复图像产物：按生成时间把候选帧/关键帧/定妆图等图片挂回对应活动行
          setChat((current) => {
            const images = (artifactState?.artifacts || []).filter((artifact) => artifact.kind === 'image');
            return {...current, messages: attachMediaByCreatedTime(current.messages, images)};
          });
          setAgentReady(true);
          setInitializing(false);
          // 服务在短暂重启后恢复时，必须清除上一次失败留下的提示；
          // 这里不能读取闭包里的 loadError，否则状态永远停留在“已重试 1 次”。
          setLoadError('');
        }
        // 即使初始化请求期间用户或 SSE 已切换会话，也必须退出恢复遮罩；
        // 不同会话的数据由 openSession/事件流各自接管，不能继续阻塞整个应用。
        setInitializing(false);
      } catch (error) {
        if (cancelled) return;
        // 服务重启/冷启动空窗期自动重试：窗口拉长到约 5 分钟（构建+启动通常 30–60s），
        // 重试期间持续显示"正在连接本地服务"而非直接白屏，避免重启后刷新即白板
        attempts += 1;
        if (attempts < 60) {
          const delay = Math.min(5000, 1200 * attempts);
          window.setTimeout(() => void load(), delay);
          if (attempts % 5 === 1) {
            setLoadError(`本地服务尚未就绪（已重试 ${attempts} 次），正在自动重连…`);
          }
        } else {
          setLoadError(error instanceof Error ? error.message : String(error));
          setInitializing(false);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [refreshArtifacts, refreshSessions, loadRetry]);

  useEffect(() => {
    if (!runningRender || !selectedSessionId) return;
    void refreshArtifacts(selectedSessionId);
    const interval = window.setInterval(() => void refreshArtifacts(selectedSessionId), 2_000);
    return () => {
      window.clearInterval(interval);
      void refreshArtifacts(selectedSessionId);
    };
  }, [refreshArtifacts, runningRender, selectedSessionId]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const frame = window.requestAnimationFrame(() => {
      const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
      if (distanceFromBottom <= 120) {
        element.scrollTo({top: element.scrollHeight, behavior: chat.busy ? 'smooth' : 'auto'});
      }
      syncScrollToBottomState(element);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [chat.messages, chat.busy, selectedSessionId, workspaceView, syncScrollToBottomState]);

  useEffect(() => {
    if (workspaceView !== 'workspace') return;
    const element = scrollRef.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => syncScrollToBottomState(element));
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => observer.disconnect();
  }, [selectedSessionId, workspaceView, syncScrollToBottomState]);

  function handleConversationScroll() {
    syncScrollToBottomState();
  }

  function scrollToBottomNow() {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTo({top: element.scrollHeight, behavior: 'smooth'});
    // 双保险：平滑滚动后再强制一次到位，防止内容仍在增长时停在半路
    window.setTimeout(() => {
      if (scrollRef.current) {
        scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      }
    }, 350);
    setShowScrollToBottom(false);
  }

  useEffect(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = '0px';
    textarea.style.height = `${Math.min(168, Math.max(28, textarea.scrollHeight))}px`;
  }, [draft]);

  useEffect(() => {
    setWorkspaceUploads([]);
    setUploadItems([]);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, [selectedSessionId]);

  async function openSession(sessionId: string) {
    if (!sessionId) {
      setMobileSidebarOpen(false);
      return;
    }
    const requestId = ++sessionRequestRef.current;
    manualSessionRef.current = sessionId;
    selectedSessionRef.current = sessionId;
    // 记住用户最后打开的项目：刷新/重连后优先恢复它，而不是跟随服务端全局
    // activeSessionId（该值会被并发 agent 的 set_active 覆盖，可能跳到别的项目）。
    storeJson(LAST_SESSION_KEY, sessionId);
    historySessionRef.current = sessionId;
    historyCursorRef.current = '';
    setHistoryCursor('');
    setHistoryHasMore(false);
    setHistoryLoadingOlder(false);
    setLoadError('');
    setSelectedSessionId(sessionId);
    setMobileSidebarOpen(false);
    setChat(createChatState());
    try {
      const [history, artifactState] = await Promise.all([
        getHistory(sessionId),
        refreshArtifacts(sessionId),
      ]);
      if (requestId !== sessionRequestRef.current) return; // 已被更新的选择取代，丢弃过期响应
      const restoredPlan = sessions.find((session) => session.sessionId === sessionId)?.workflowPlan;
      setChat(createChatState(history.messages, restoredPlan));
      historyCursorRef.current = history.nextCursor;
      setHistoryCursor(history.nextCursor);
      setHistoryHasMore(history.hasMore);
      attachSessionVideosToChat((artifactState?.artifacts || []).filter((artifact) => artifact.kind === 'video'));
      setAgentReady(true);
    } catch (error) {
      if (requestId !== sessionRequestRef.current) return;
      setLoadError(error instanceof Error ? error.message : String(error));
    }
  }

  async function loadOlderHistory() {
    const sessionId = selectedSessionRef.current;
    const before = historyCursorRef.current;
    if (!sessionId || !before || !historyHasMore || historyLoadingOlder) return;
    const container = scrollRef.current;
    const previousHeight = container?.scrollHeight || 0;
    setHistoryLoadingOlder(true);
    try {
      const page = await getHistory(sessionId, before);
      if (selectedSessionRef.current !== sessionId) return;
      setChat((current) => ({
        ...current,
        messages: mergeHistoryMessages(page.messages, current.messages),
      }));
      historyCursorRef.current = page.nextCursor;
      setHistoryCursor(page.nextCursor);
      setHistoryHasMore(page.hasMore);
      window.requestAnimationFrame(() => {
        if (container) {
          container.scrollTop = Math.max(0, container.scrollHeight - previousHeight);
          syncScrollToBottomState(container);
        }
      });
    } catch (error) {
      notify(error instanceof Error ? error.message : '加载更早记录失败', 'error');
    } finally {
      setHistoryLoadingOlder(false);
    }
  }

  function openNewProjectDialog() {
    setNewProjectName('');
    setNewProjectError('');
    setNewProjectOpen(true);
  }

  async function newProject() {
    const projectName = newProjectName.trim();
    if (!projectName || creatingProject) return;
    setCreatingProject(true);
    setNewProjectError('');
    setLoadError('');
    setMobileSidebarOpen(false);
    try {
      const started = await startAgent({newSession: true, projectName});
      setAgentReady(true);
      await new Promise((resolve) => window.setTimeout(resolve, 450));
      const state = await refreshSessions();
      // 选中"刚建的这个项目"：优先用服务端返回的 id（本次新建的），拿不到实体才退回全局 active。
      const createdId = String(started?.sessionId || '').trim();
      const createdExists = Boolean(createdId) && state.sessions.some((session) => session.sessionId === createdId);
      const targetId = (createdExists ? createdId : '') || state.activeSessionId || '';
      // 关键：把新项目记成"最后打开的项目"并锁定选择。
      // 旧实现既不写 LAST_SESSION_KEY、又拿"服务端全局 active 会话"来选项目 —— 新建项目后
      // 一刷新（或页面重载）就按旧记忆跳回上一个项目，界面顶部步骤条显示的是上一个项目的
      // "已全部完成"状态（实测：新建项目后步骤没有归零）。
      manualSessionRef.current = targetId;
      selectedSessionRef.current = targetId;
      setSelectedSessionId(targetId);
      if (targetId) storeJson(LAST_SESSION_KEY, targetId);
      historySessionRef.current = targetId;
      historyCursorRef.current = '';
      setHistoryCursor('');
      setHistoryHasMore(false);
      setChat(createChatState());
      setArtifacts([]);
      setWorkspaceUploads([]);
      setUploadItems([]);
      if (targetId) await refreshArtifacts(targetId);
      setWorkspaceView('workspace');
      setNewProjectOpen(false);
      setNewProjectName('');
      textareaRef.current?.focus();
    } catch (error) {
      setNewProjectError(error instanceof Error ? error.message : String(error));
    } finally {
      setCreatingProject(false);
    }
  }

  async function submit(override?: string) {
    const text = (override ?? draft).trim();
    if (!text || chat.busy || uploadingFiles) return;
    setLoadError('');
    setDraft('');
    setChat((current) => appendLocalUser(current, text));
    try {
      // 用 ref（同步）而非 state（异步）：openSession 已先把 ref 更新为目标项目，
      // 若用户点击项目后立刻发送，state 可能还是旧值，导致消息发到上一个项目。
      let targetSessionId = selectedSessionRef.current || selectedSessionId;
      if (!agentReady) {
        // 用 ref（同步）而不是 state（异步）：用户刚点选项目后立刻发送时，
        // setSelectedSessionId 可能还没生效，传空/旧值会误走 newSession 创建新会话。
        const started = await startAgent(targetSessionId ? {sessionId: targetSessionId} : {newSession: true});
        // 旧写法先把 started.sessionId 赋给 targetSessionId，再判断两者是否不同 —— 条件恒假，
        // 于是"服务端新建了会话"这条分支从来没跑过：界面仍指向旧/空会话，也不会记住新项目。
        const startedId = String(started?.sessionId || '').trim();
        if (startedId && startedId !== targetSessionId) {
          targetSessionId = startedId;
          manualSessionRef.current = startedId;
          selectedSessionRef.current = startedId;
          setSelectedSessionId(startedId);
          storeJson(LAST_SESSION_KEY, startedId);
          historySessionRef.current = startedId;
          historyCursorRef.current = '';
          setHistoryCursor('');
          setHistoryHasMore(false);
          setChat(createChatState());
          setArtifacts([]);
          await refreshSessions();
        }
        setAgentReady(true);
      }
      // 只带属于**本会话**的上传件：属于别的项目的路径在当前工作区里读不到，
      // 带过去只会让 agent 报 File not found（实测事故）。
      const {forSession, foreign} = partitionUploadsForSession(workspaceUploads, targetSessionId);
      if (foreign.length > 0) {
        notify(`${foreign.length} 个附件属于其它项目（${foreign.map((file) => file.name).join('、')}），已从本次发送中移除，请在本项目重新上传`, 'error');
      }
      const outbound = composeAgentPrompt(text, forSession.map((file) => file.path));
      try {
        await sendMessage(targetSessionId, outbound);
      } catch (error) {
        if (error instanceof HttpError && error.status === 409) {
          const restarted = await startAgent({sessionId: targetSessionId});
          if (restarted.sessionId) targetSessionId = restarted.sessionId;
          setAgentReady(true);
          await sendMessage(targetSessionId, outbound);
        } else {
          throw error;
        }
      }
      setWorkspaceUploads([]);
      setUploadItems([]);
    } catch (error) {
      const event: AgentEvent = {type: 'error', message: error instanceof Error ? error.message : String(error)};
      setChat((current) => applyAgentEvent(current, event));
    }
  }

  // 全自动创作：一键开启自主模式并让智能体端到端推进到成片。
  // 1) 写 runtime.local.json（agent 下一回合热读生效，无需重启）；
  // 2) 发送开工指令，阶段导演随即驱动 剧本→资产→分镜→渲染，人工闸门处暂停。
  async function autoCreate() {
    if (chat.busy || !selectedSessionId) return;
    setLoadError('');
    try {
      await saveRuntimeConfig({autonomous_mode: true});
      notify('已开启自主创作模式', 'success');
    } catch (error) {
      notify(`开启自主模式失败：${error instanceof Error ? error.message : String(error)}`, 'error');
      return;
    }
    const kickoff = '进入全自动创作模式：从当前阶段开始端到端自主推进（整理剧本 → 提取资产 → 生成资产 → 生成分镜 → 渲染成片），'
      + '每一步产物就绪就立即继续下一步，不要停下来向我确认；仅在「资产确认」与「成片验收」两个人工闸门暂停等我确认。现在立即开始推进。';
    await submit(kickoff);
  }

  // 一键创作向导启动：按所选模式设置运行时创作模式，再提交配方组装好的专业指令。
  async function startFromWizard(directive: string, mode: 'autonomous' | 'collaborative') {
    try {
      await saveRuntimeConfig({autonomous_mode: mode === 'autonomous'});
    } catch {
      // 运行时配置写入失败不阻断创作，agent 仍会按指令文本执行
    }
    await submit(directive);
  }

  async function uploadFiles(files: FileList | File[] | null) {
    // 用 ref（同步）而不是 state（异步）：新建/切换项目后 state 还没重渲染时上传，
    // 文件会落到上一个项目（实测事故：新建项目后上传剧本 → 文件进了旧项目的 uploads/，
    // 消息却发到新项目，agent 只能报 File not found）。
    const sessionId = selectedSessionRef.current || selectedSessionId;
    const selectedFiles = files ? Array.from(files) : [];
    if (selectedFiles.length === 0) return;
    if (!sessionId) {
      setLoadError('请先创建或选择项目再上传文件');
      return;
    }
    setUploadingFiles(true);
    setLoadError('');
    const queued = selectedFiles.map((file, index) => ({id: `${Date.now()}-${index}-${file.name}`, name: file.name, status: 'uploading' as const}));
    setUploadItems((current) => [...current, ...queued]);
    let uploadedAny = false;
    let failedCount = 0;
    let misplacedCount = 0;
    try {
      for (let index = 0; index < selectedFiles.length; index += 1) {
        const file = selectedFiles[index];
        const item = queued[index];
        try {
          const result = await uploadWorkspaceFile(sessionId, file);
          if (selectedSessionRef.current !== sessionId) {
            // 上传途中项目被切换：文件躺在别的项目里，绝不能进待发送列表（跨项目路径必然读不到）
            misplacedCount += 1;
            setUploadItems((current) => current.map((entry) => entry.id === item.id
              ? {...entry, status: 'error', error: `上传期间项目已切换，该文件在其它项目里，请在本项目重新上传`}
              : entry));
            continue;
          }
          uploadedAny = true;
          setWorkspaceUploads((current) => [
            ...current.filter((entry) => entry.path !== result.file.path),
            {...result.file, sessionId},
          ]);
          setUploadItems((current) => current.map((entry) => entry.id === item.id ? {...entry, status: 'done'} : entry));
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          failedCount += 1;
          setUploadItems((current) => current.map((entry) => entry.id === item.id ? {...entry, status: 'error', error: message} : entry));
          setLoadError(`${file.name} 上传失败：${message}`);
        }
      }
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = '';
      if (uploadedAny) await refreshArtifacts(sessionId);
      setUploadingFiles(false);
      if (misplacedCount > 0) {
        notify(`${misplacedCount} 个文件上传到了其它项目（上传期间项目被切换），请在当前项目重新上传`, 'error');
      } else {
        notify(failedCount > 0 ? `${failedCount} 个文件上传失败` : `${selectedFiles.length} 个文件已加入项目`, failedCount > 0 ? 'error' : 'success');
      }
      textareaRef.current?.focus();
    }
  }

  function handleWorkspaceDragEnter(event: DragEvent) {
    if (!event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragActive(true);
  }

  function handleWorkspaceDragLeave(event: DragEvent) {
    event.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragActive(false);
  }

  function handleWorkspaceDrop(event: DragEvent) {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    void uploadFiles(event.dataTransfer.files);
  }

  async function retryActivity(message: Message) {
    const tool = humanizeTool(message.tool || '当前步骤');
    // 确定性失败（路径不存在 / 越界 / 附件不在本项目）原样重试必然同一结果，别让用户白点。
    if (!isRetryableToolFailure(message.text)) {
      notify(
        `「${tool}」是确定性失败（路径不存在或越界），原样重试不会成功。请让它改用项目内的路径，或直接发消息让它重新读取——工具只能访问当前项目的 .working_dir/<会话>/ 目录。`,
        'error',
      );
      return;
    }
    if (message.toolCallId && message.tool && message.toolArguments) {
      try {
        try {
          await retryAgentTool(selectedSessionId, {id: message.toolCallId, name: message.tool, arguments: message.toolArguments});
        } catch (error) {
          if (error instanceof HttpError && error.status === 409) {
            const restarted = await startAgent({sessionId: selectedSessionId});
            if (restarted.sessionId) {
              selectedSessionRef.current = restarted.sessionId;
              setSelectedSessionId(restarted.sessionId);
            }
            setAgentReady(true);
            await retryAgentTool(restarted.sessionId || selectedSessionId, {id: message.toolCallId, name: message.tool, arguments: message.toolArguments});
          } else {
            throw error;
          }
        }
        notify(`正在精确重试：${tool}`, 'info');
      } catch (error) {
        notify(error instanceof Error ? error.message : '重试失败', 'error');
      }
      return;
    }
    void submit(`请重试刚才失败的步骤：${tool}。错误信息：${message.text}`);
  }

  async function stop() {
    await stopAgent(selectedSessionId);
    setAgentReady(false);
    setChat((current) => ({...current, busy: false}));
  }

  async function confirmDelete() {
    if (!pendingDelete || deleting) return;
    const sessionId = pendingDelete.sessionId;
    const deletingSelected = sessionId === selectedSessionId;
    setDeleting(true);
    setLoadError('');
    try {
      const state = await deleteSession(sessionId);
      setSessions(state.sessions);
      setPendingDelete(undefined);
      if (deletingSelected) {
        selectedSessionRef.current = '';
        historySessionRef.current = '';
        historyCursorRef.current = '';
        setHistoryCursor('');
        setHistoryHasMore(false);
        setSelectedSessionId('');
        setChat(createChatState());
        setArtifacts([]);
        setAgentReady(false);
        if (state.activeSessionId) await openSession(state.activeSessionId);
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error));
    } finally {
      setDeleting(false);
    }
  }

  const contextPercent = Math.min(100, Math.round((chat.promptTokens / CONTEXT_TARGET) * 100));
  const projectSummary = useMemo(() => {
    const videos = artifacts.filter((artifact) => artifact.kind === 'video').length;
    const images = artifacts.filter((artifact) => artifact.kind === 'image').length;
    const documents = artifacts.filter((artifact) => artifact.kind === 'document').length;
    return `${videos} 片段 · ${images} 画面 · ${documents} 文档`;
  }, [artifacts]);
  const hasConversation = chat.messages.length > 0;

  return (
    <div className="app-shell">
      <Sidebar
        open={sidebarOpen}
        mobileOpen={mobileSidebarOpen}
        sessions={sessions}
        runningTextBySession={runningTextBySession}
        pinnedProjects={pinnedProjects}
        getTitle={getSessionTitle}
        onTogglePin={togglePinnedProject}
        onRename={renameProject}
        selectedSessionId={selectedSessionId}
        activeView={workspaceView}
        onToggle={() => setSidebarOpen((value) => !value)}
        onMobileClose={() => setMobileSidebarOpen(false)}
        onNew={openNewProjectDialog}
        onSelect={(sessionId) => void openSession(sessionId)}
        onWorkspace={() => {
          setWorkspaceView('workspace');
          setMobileSidebarOpen(false);
        }}
        onResults={() => {
          setWorkspaceView('results');
          setMobileSidebarOpen(false);
        }}
        resultsCount={finalCount}
        onArtifacts={() => {
          setWorkspaceView('artifacts');
          setMobileSidebarOpen(false);
        }}
        onBudget={() => {
          setWorkspaceView('budget');
          setMobileSidebarOpen(false);
        }}
        onLedger={() => {
          setWorkspaceView('ledger');
          setMobileSidebarOpen(false);
        }}
        onCanvas={() => {
          setWorkspaceView('canvas');
          setMobileSidebarOpen(false);
        }}
        onSettings={() => {
          setWorkspaceView('settings');
          setMobileSidebarOpen(false);
        }}
        onDelete={setPendingDelete}
      />

      <main
        className="workspace-main"
        onDragEnter={handleWorkspaceDragEnter}
        onDragOver={(event) => event.dataTransfer.types.includes('Files') && event.preventDefault()}
        onDragLeave={handleWorkspaceDragLeave}
        onDrop={handleWorkspaceDrop}
      >
        {dragActive && <div className="workspace-dropzone"><UploadCloud size={30} /><strong>释放文件以上传</strong><span>支持拖拽文件到工作区</span></div>}
        {initializing && (
          <div className="app-initializing">
            <div className="app-initializing-spinner" />
            <span>{loadError ? '正在连接本地服务…' : '正在恢复会话数据…'}</span>
            <em>{loadError ? loadError : '服务重启后首次加载较慢，请稍候'}</em>
          </div>
        )}
        {workspaceView === 'workspace' ? (
          <div className="workspace-utility-bar">
            <div className="workspace-utility-row">
              <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                <Menu size={19} />
              </button>
              {!sidebarOpen && (
                <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                  <PanelLeftOpen size={18} />
                </button>
              )}
              <span className="workspace-project-summary">{projectSummary}</span>
              <AgentTaskCard plan={chat.workflowPlan} busy={chat.busy} />
              <span className="workspace-utility-spacer" />
              <div className="workspace-utility-actions">
                <button className="utility-create-btn" onClick={() => setWizardOpen(true)} title="一键创作：输入创意，按电影级标准自动完成全流程">
                  <Sparkles size={15} />
                  <span>一键创作</span>
                </button>
                <ThemeToggle theme={theme} onChange={setTheme} />
                <button className="icon-button artifact-toggle" onClick={() => setStoryboardPanelOpen((value) => !value)} aria-label="切换分镜预览">
                  <PanelRight size={18} />
                  {storyboardCount > 0 && <span className="count-badge">{storyboardCount}</span>}
                </button>
              </div>
            </div>
            {hasConversation && (
              <WorkflowSteps messages={chat.messages} stage={selectedSession?.stage} progressStage={selectedSession?.progressStage} />
            )}
          </div>
        ) : workspaceView === 'budget' ? (
          null
        ) : workspaceView === 'ledger' ? (
          null
        ) : workspaceView === 'canvas' ? (
          null
        ) : workspaceView === 'results' ? (
          <header className="workspace-header">
            <div className="workspace-title-row">
              <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                <Menu size={19} />
              </button>
              {!sidebarOpen && (
                <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                  <PanelLeftOpen size={18} />
                </button>
              )}
              <div className="workspace-title-copy">
                <strong>我的成片</strong>
                <span>专业短剧 · 只要结果</span>
              </div>
            </div>
            <ThemeToggle theme={theme} onChange={setTheme} />
          </header>
        ) : workspaceView === 'settings' ? (
          <header className="workspace-header">
            <div className="workspace-title-row">
              <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                <Menu size={19} />
              </button>
              {!sidebarOpen && (
                <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                  <PanelLeftOpen size={18} />
                </button>
              )}
              <div className="workspace-title-copy">
                <strong>设置</strong>
                <span>configs/agent.local.yaml</span>
              </div>
            </div>
            <ThemeToggle theme={theme} onChange={setTheme} />
          </header>
        ) : null}

        {workspaceView === 'workspace' ? (
          <div className="conversation" ref={scrollRef} onScroll={handleConversationScroll}>
            {!hasConversation ? (
              <EmptyState theme={theme} onPrompt={(text) => void submit(text)} onWizard={() => setWizardOpen(true)} />
            ) : (
              <>
                <div className="message-stream">
                {historyHasMore && (
                  <div className="history-toolbar">
                    <button
                      type="button"
                      className="history-load-older"
                      onClick={() => void loadOlderHistory()}
                      disabled={historyLoadingOlder || !historyCursor}
                    >
                      <History size={14} />
                      {historyLoadingOlder ? '正在加载更早记录…' : '加载更早记录'}
                    </button>
                  </div>
                )}
                <MessageFeed messages={chat.messages} onRetry={retryActivity} onQuickAction={(reply) => void submit(reply)} />
                {chat.busy && <ThinkingRow messages={chat.messages} statusText={chat.statusText} />}
                {showScrollToBottom && (
                  <button
                    type="button"
                    className="scroll-bottom-button"
                    onClick={scrollToBottomNow}
                    aria-label="回到底部"
                    title="回到底部"
                  >
                    <ArrowDown size={15} />
                    <span>回到底部</span>
                  </button>
                )}
                </div>
              </>
            )}
          </div>
        ) : workspaceView === 'artifacts' ? (
          <ArtifactsView
            session={selectedSession}
            artifactsLoading={artifactsLoading}
            artifacts={artifacts}
            artifactsTruncated={artifactsTruncated}
            artifactsTotal={artifactsTotal}
            artifactsLoadingMore={artifactsLoadingMore}
            onLoadMoreArtifacts={loadMoreArtifacts}
            onRefresh={() => void refreshArtifacts(selectedSessionId)}
            actions={(
              <>
                <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                  <Menu size={19} />
                </button>
                {!sidebarOpen && (
                  <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                    <PanelLeftOpen size={18} />
                  </button>
                )}
                <ThemeToggle theme={theme} onChange={setTheme} />
              </>
            )}
          />
        ) : workspaceView === 'budget' ? (
          <Suspense fallback={<div className="view-loading">正在加载预算视图…</div>}>
            <BudgetView
              onCreate={() => setWorkspaceView('workspace')}
              session={selectedSession}
              actions={(
                <>
                  <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                    <Menu size={19} />
                  </button>
                  {!sidebarOpen && (
                    <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                      <PanelLeftOpen size={18} />
                    </button>
                  )}
                  <ThemeToggle theme={theme} onChange={setTheme} />
                </>
              )}
            />
          </Suspense>
        ) : workspaceView === 'ledger' ? (
          <Suspense fallback={<div className="view-loading">正在加载台账视图…</div>}>
            <LedgerView
              onCreate={() => setWorkspaceView('workspace')}
              session={selectedSession?.sessionId ?? ''}
              actions={(
                <>
                  <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                    <Menu size={19} />
                  </button>
                  {!sidebarOpen && (
                    <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                      <PanelLeftOpen size={18} />
                    </button>
                  )}
                  <ThemeToggle theme={theme} onChange={setTheme} />
                </>
              )}
            />
          </Suspense>
        ) : workspaceView === 'results' ? (
          <Suspense fallback={<div className="view-loading">正在加载我的成片…</div>}>
            <ResultsView
              artifacts={artifacts}
              session={selectedSession?.sessionId ?? ''}
              onCreate={() => { setWorkspaceView('workspace'); setWizardOpen(true); }}
            />
          </Suspense>
        ) : workspaceView === 'canvas' ? (
          <Suspense fallback={<div className="view-loading">正在加载画布视图…</div>}>
            <CanvasView
              onCreate={() => setWorkspaceView('workspace')}
              session={selectedSession?.sessionId ?? ''}
              actions={(
                <>
                  <button className="icon-button mobile-only" onClick={() => setMobileSidebarOpen(true)} aria-label="打开导航">
                    <Menu size={19} />
                  </button>
                  {!sidebarOpen && (
                    <button className="icon-button desktop-only" onClick={() => setSidebarOpen(true)} aria-label="打开导航">
                      <PanelLeftOpen size={18} />
                    </button>
                  )}
                  <ThemeToggle theme={theme} onChange={setTheme} />
                </>
              )}
            />
          </Suspense>
        ) : (
          <SettingsView selectedSessionId={selectedSessionId} theme={theme} onThemeChange={setTheme} />
        )}

        {workspaceView === 'workspace' && <div className="composer-zone">
          {chat.busy && (
            <div className="task-progress" aria-live="polite">
              <span className="task-progress-pulse" aria-hidden="true" />
              <span><strong>{runningActivity ? humanizeTool(runningActivity.tool || '映界') : '映界正在执行'}</strong>{runningActivity?.text || '正在分析当前进度并准备下一步操作，请稍候…'}</span>
              <i aria-hidden="true" />
            </div>
          )}
          {/* 批量任务：完成量 + 预计剩余时间。长任务只有一句"正在生成第 3 批"时，
              用户无法判断还要等多久，也不知道进度是否卡住。 */}
          {chat.busy && taskProgress && taskProgress.total > 1 && (
            <div className="task-progress is-metered" aria-live="polite">
              <span className="task-progress-count">{taskProgress.done}/{taskProgress.total}</span>
              <span className="task-progress-bar" aria-hidden="true">
                <i style={{width: `${Math.round((taskProgress.done / taskProgress.total) * 100)}%`}} />
              </span>
              <span className="task-progress-eta">{formatEta(taskProgress)}</span>
            </div>
          )}
          {loadError && (
            <div className="inline-error" role="alert">
              <span>{loadError}</span>
              <button onClick={() => { setLoadError(''); void loadAgain(); }} aria-label="重试">重试</button>
              <button onClick={() => setLoadError('')} aria-label="Dismiss error"><X size={15} /></button>
            </div>
          )}
          {showSlashCommands && <SlashCommandMenu matches={slashMatches} contextPercent={contextPercent} onSelect={(command) => {
            setDraft(command);
            textareaRef.current?.focus();
          }} />}
          <div className={`composer ${chat.busy ? 'is-busy' : ''}`}>
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onPaste={(event) => {
                const files = Array.from(event.clipboardData.files);
                if (files.length > 0) {
                  event.preventDefault();
                  void uploadFiles(files);
                }
              }}
              onKeyDown={(event) => {
                if (event.key === 'Tab' && slashMatches[0]) {
                  event.preventDefault();
                  setDraft(slashMatches[0].name);
                  return;
                }
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void submit();
                }
              }}
              placeholder="描述你想创作的内容"
              aria-label="给映界发消息"
              disabled={chat.busy}
              rows={1}
            />
            {(workspaceUploads.length > 0 || uploadItems.length > 0 || uploadingFiles) && (
              <div className="composer-attachments" aria-live="polite">
                {workspaceUploads.map((file) => (
                  <span className="composer-attachment" key={file.path} title={file.path}>
                    <em>@</em>
                    <FileText size={13} />
                    <span>{file.name}</span>
                    <button
                      type="button"
                      onClick={() => setWorkspaceUploads((current) => current.filter((item) => item.path !== file.path))}
                      aria-label={`从消息中移除 ${file.name}`}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
                {uploadItems.filter((item) => item.status !== 'done').map((item) => (
                  <span key={item.id} className={`composer-upload-item is-${item.status}`} title={item.error || item.name}>
                    {item.status === 'uploading' ? <span className="upload-spinner" /> : <X size={12} />}
                    <span>{item.name}</span>
                    <small>{item.status === 'uploading' ? '上传中' : '失败'}</small>
                  </span>
                ))}
              </div>
            )}
            <div className="composer-controls">
              <input
                ref={fileInputRef}
                className="composer-file-input"
                type="file"
                multiple
                onChange={(event) => void uploadFiles(event.currentTarget.files)}
                tabIndex={-1}
              />
              <button
                type="button"
                className="composer-add"
                onClick={() => fileInputRef.current?.click()}
                disabled={!selectedSessionId || uploadingFiles || chat.busy}
                aria-label="上传文件到工作区"
                aria-busy={uploadingFiles}
                title={selectedSessionId ? '上传文件到工作区' : '请先创建或选择项目'}
              >
                <Plus size={20} />
              </button>
              <div className="composer-spacer" />
              <button
                type="button"
                className="composer-auto"
                onClick={() => void autoCreate()}
                disabled={chat.busy || !selectedSessionId}
                title={selectedSessionId ? '全自动创作：开启自主模式，让智能体端到端推进到成片（人工闸门处暂停）' : '请先创建或选择项目'}
              >
                <Sparkles size={15} />
                <span>全自动创作</span>
              </button>
              {chat.busy ? (
                <button className="send-button stop" onClick={() => void stop()} aria-label="停止生成" title="停止生成"><CircleStop size={18} /></button>
              ) : (
                <button className="send-button" onClick={() => void submit()} disabled={!draft.trim() || uploadingFiles} aria-label="发送消息" title="发送消息"><ArrowUp size={19} /></button>
              )}
            </div>
          </div>
        </div>}
      </main>

      <StoryboardPanel
        open={storyboardPanelOpen && workspaceView === 'workspace'}
        artifacts={artifacts}
        activeRenderStage={runningRender?.stage}
        onClose={() => setStoryboardPanelOpen(false)}
        onCountChange={setStoryboardCount}
      />
      <DeleteProjectDialog
        title={pendingDelete ? getSessionTitle(pendingDelete) : ''}
        session={pendingDelete}
        deleting={deleting}
        onCancel={() => !deleting && setPendingDelete(undefined)}
        onConfirm={() => void confirmDelete()}
      />
      <NewProjectDialog
        open={newProjectOpen}
        name={newProjectName}
        error={newProjectError}
        creating={creatingProject}
        onNameChange={(value) => {
          setNewProjectName(value);
          setNewProjectError('');
        }}
        onCancel={() => {
          if (creatingProject) return;
          setNewProjectOpen(false);
          setNewProjectError('');
        }}
        onConfirm={() => void newProject()}
      />
      <CreationWizard open={wizardOpen} onClose={() => setWizardOpen(false)} onStart={(directive, mode) => startFromWizard(directive, mode)} />
      <ToastHost />

    </div>
  );
}

function Sidebar({open, mobileOpen, sessions, runningTextBySession, selectedSessionId, activeView, pinnedProjects, getTitle, onToggle, onMobileClose, onNew, onSelect, onWorkspace, onResults, resultsCount, onArtifacts, onBudget, onLedger, onCanvas, onSettings, onDelete, onTogglePin, onRename}: {
  open: boolean;
  mobileOpen: boolean;
  sessions: SessionSummary[];
  runningTextBySession: Record<string, string>;
  selectedSessionId: string;
  activeView: WorkspaceView;
  pinnedProjects: string[];
  getTitle: (session: SessionSummary) => string;
  onToggle: () => void;
  onMobileClose: () => void;
  onNew: () => void;
  onSelect: (sessionId: string) => void;
  onWorkspace: () => void;
  onResults: () => void;
  resultsCount: number;
  onArtifacts: () => void;
  onBudget: () => void;
  onLedger: () => void;
  onCanvas: () => void;
  onSettings: () => void;
  onDelete: (session: SessionSummary) => void;
  onTogglePin: (sessionId: string) => void;
  onRename: (sessionId: string, name: string) => void;
}) {
  const [projectQuery, setProjectQuery] = useState('');
  const [renamingId, setRenamingId] = useState('');
  const [renameValue, setRenameValue] = useState('');
  const visibleSessions = useMemo(() => {
    const normalized = projectQuery.trim().toLowerCase();
    return [...sessions]
      .filter((session) => !normalized || [getTitle(session), session.idea, session.summary, session.stage].join(' ').toLowerCase().includes(normalized))
      .sort((left, right) => {
        const pinDelta = Number(pinnedProjects.includes(right.sessionId)) - Number(pinnedProjects.includes(left.sessionId));
        return pinDelta || right.updatedAt.localeCompare(left.updatedAt);
      });
  }, [getTitle, pinnedProjects, projectQuery, sessions]);

  function commitRename(sessionId: string) {
    if (renameValue.trim()) onRename(sessionId, renameValue);
    setRenamingId('');
  }

  return (
    <>
      {mobileOpen && <button className="sidebar-scrim" onClick={onMobileClose} aria-label="关闭导航" />}
      <aside className={`sidebar ${open ? 'is-open' : 'is-collapsed'} ${mobileOpen ? 'is-mobile-open' : ''}`}>
        <div className="sidebar-brand">
          <span className="sidebar-brand-logo" aria-hidden="true"><Clapperboard size={16} /></span>
          <strong>映界</strong>
          <button className="icon-button sidebar-collapse desktop-only" onClick={onToggle} aria-label="收起导航"><PanelLeftClose size={17} /></button>
          <button className="icon-button mobile-only" onClick={onMobileClose} aria-label="关闭导航"><X size={18} /></button>
        </div>
        <nav className="primary-nav" aria-label="主导航">
          <button onClick={onNew}><FolderPlus size={17} /><span>新建项目</span></button>
          <button className={activeView === 'workspace' ? 'is-active' : ''} onClick={onWorkspace} aria-current={activeView === 'workspace' ? 'page' : undefined}><Folder size={17} /><span>工作区</span></button>
          <button className={`${activeView === 'results' ? 'is-active' : ''} nav-results`} onClick={onResults} aria-current={activeView === 'results' ? 'page' : undefined}><Film size={17} /><span>我的成片</span>{resultsCount > 0 && <span className="count-badge">{resultsCount}</span>}</button>
          <button className={activeView === 'artifacts' ? 'is-active' : ''} onClick={onArtifacts} aria-current={activeView === 'artifacts' ? 'page' : undefined}><Files size={17} /><span>产物</span></button>
          <button className={activeView === 'budget' ? 'is-active' : ''} onClick={onBudget} aria-current={activeView === 'budget' ? 'page' : undefined}><BarChart3 size={17} /><span>预算</span></button>
          <button className={activeView === 'ledger' ? 'is-active' : ''} onClick={onLedger} aria-current={activeView === 'ledger' ? 'page' : undefined}><ClipboardList size={17} /><span>台账</span></button>
          <button className={activeView === 'canvas' ? 'is-active' : ''} onClick={onCanvas} aria-current={activeView === 'canvas' ? 'page' : undefined}><Network size={17} /><span>画布</span></button>
          <button className={activeView === 'settings' ? 'is-active' : ''} onClick={onSettings} aria-current={activeView === 'settings' ? 'page' : undefined}><Settings size={17} /><span>设置</span></button>
        </nav>
        <div className="session-section">
          <div className="section-label"><span>项目</span><span>{sessions.length}</span></div>
          <label className="project-search">
            <Search size={14} />
            <input value={projectQuery} onChange={(event) => setProjectQuery(event.target.value)} placeholder="搜索项目" aria-label="搜索项目" />
            {projectQuery && <button type="button" onClick={() => setProjectQuery('')} aria-label="清空项目搜索"><X size={12} /></button>}
          </label>
          <div className="session-list">
            {visibleSessions.map((session) => {
              const title = getTitle(session);
              const pinned = pinnedProjects.includes(session.sessionId);
              return (
                <div key={session.sessionId} className={`session-item ${session.sessionId === selectedSessionId ? 'is-selected' : ''} ${pinned ? 'is-pinned' : ''}`}>
                  <button className="session-open" onClick={() => onSelect(session.sessionId)}>
                    <span className={`session-project-icon is-${stageTone(session.stage)}`} aria-hidden="true"><ProjectIcon stage={session.stage} /></span>
                    <span className="session-copy">
                      {renamingId === session.sessionId ? (
                        <input
                          className="session-rename-input"
                          value={renameValue}
                          onChange={(event) => setRenameValue(event.target.value)}
                          onClick={(event) => event.stopPropagation()}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') commitRename(session.sessionId);
                            if (event.key === 'Escape') setRenamingId('');
                          }}
                          onBlur={() => commitRename(session.sessionId)}
                          autoFocus
                        />
                      ) : <strong title={title}>{title}</strong>}
                      <small>
                        {session.running
                          ? <>
                              <i className="session-status is-running"><b aria-hidden="true" />运行中</i>
                              {runningTextBySession[session.sessionId] && <em className="session-running-text" title={runningTextBySession[session.sessionId]}>{runningTextBySession[session.sessionId]}</em>}
                            </>
                          : <>{relativeTime(session.updatedAt)} · <i className={`session-status is-${stageTone(session.stage)}`}><b aria-hidden="true" />{stageLabel(session.stage)}</i></>}
                      </small>
                    </span>
                  </button>
                  <div className="session-tools">
                    <button className={pinned ? 'is-pinned' : ''} onClick={() => onTogglePin(session.sessionId)} aria-label={pinned ? `取消置顶 ${title}` : `置顶 ${title}`} title={pinned ? '取消置顶' : '置顶'}>{pinned ? <Pin size={13} fill="currentColor" /> : <Pin size={13} />}</button>
                    <button onClick={() => { setRenameValue(title); setRenamingId(session.sessionId); }} aria-label={`重命名 ${title}`} title="重命名"><Pencil size={13} /></button>
                    <button onClick={() => onDelete(session)} aria-label={`删除 ${title}`} title="删除项目"><Trash2 size={13} /></button>
                  </div>
                </div>
              );
            })}
            {visibleSessions.length === 0 && <span className="empty-list">{projectQuery ? '没有匹配项目' : '暂无项目'}</span>}
          </div>
        </div>
        <div className="sidebar-footer">
          <span className="avatar">映</span>
          <div><strong>本地工作区</strong><small>本地 · 映界</small></div>
        </div>
      </aside>
    </>
  );
}

function EmptyState({theme, onPrompt, onWizard}: {theme: Theme; onPrompt?: (text: string) => void; onWizard?: () => void}) {
  return (
    <section className="empty-state">
      <img className="empty-state-logo" src={isDarkTheme(theme) ? '/vimax-light.svg' : '/vimax-dark.svg'} alt="映界" />
      <h1>你好，今天想创作什么？</h1>
      <p className="empty-state-sub">描述你的创意，映界会自动完成剧本、分镜、资产与渲染</p>
      {onWizard && (
        <button type="button" className="empty-state-cta" onClick={onWizard}>
          <Wand2 size={18} />
          <span><strong>一键创作专业短剧</strong><small>选类型 + 给创意，电影级标准自动出片</small></span>
        </button>
      )}
      {onPrompt && (
        <div className="empty-state-suggestions">
          <button type="button" onClick={() => onPrompt('帮我创作一部古装修仙题材的短剧，先写第 1 集剧本')}>
            <i><Sparkles size={17} /></i>
            <span><strong>古装修仙</strong><small>创作第 1 集剧本</small></span>
          </button>
          <button type="button" onClick={() => onPrompt('把上传的剧本改写成 9:16 竖屏分镜，并生成角色资产')}>
            <i><Clapperboard size={17} /></i>
            <span><strong>剧本改分镜</strong><small>9:16 竖屏 + 角色资产</small></span>
          </button>
        </div>
      )}
    </section>
  );
}

function ThemeToggle({theme, onChange}: {theme: Theme; onChange: (theme: Theme) => void}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', close);
    window.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', close);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div className="theme-menu" ref={rootRef}>
      <button
        type="button"
        className="icon-button theme-toggle"
        onClick={() => setOpen((value) => !value)}
        aria-label="选择主题"
        aria-expanded={open}
        title="选择主题"
      >
        {theme === 'light' ? <Moon size={18} /> : theme === 'dark' ? <Sparkles size={18} /> : <Sun size={18} />}
      </button>
      {open && (
        <div className="theme-menu-popover" role="menu" aria-label="主题">
          {THEMES.map((item) => (
            <button key={item.key} role="menuitemradio" aria-checked={theme === item.key} className={theme === item.key ? 'is-selected' : ''} onClick={() => { onChange(item.key); setOpen(false); }}>
              <i className={`theme-swatch is-${item.key}`} aria-hidden="true"><Clapperboard size={16} /></i>
              <span><strong>{item.label}</strong><small>{item.desc}</small></span>
              {theme === item.key && <Check size={14} />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function CodeBlock({children}: {children?: ReactNode}) {
  const text = extractText(children);
  return (
    <div className="code-block">
      <button onClick={() => { void navigator.clipboard.writeText(text).then(() => notify('代码已复制')).catch(() => notify('复制失败', 'error')); }}><Copy size={13} />复制</button>
      <pre>{children}</pre>
    </div>
  );
}

function extractText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(extractText).join('');
  if (node && typeof node === 'object' && 'props' in node) return extractText((node as {props?: {children?: ReactNode}}).props?.children);
  return '';
}

// Markdown 默认把单个换行符当作空格（必须空行才分段），导致"每行一句"的剧本
// 粘贴到对话框后整段挤成一坨。此插件把 text 节点中的单个 \n 转成 hardBreak，
// 保留用户原文换行；代码块（code/inlineCode 独立节点）不受影响。
function remarkKeepBreaks() {
  return (tree: unknown) => {
    const visit = (node: {type?: string; value?: string; children?: unknown[]}) => {
      if (node.type === 'text' && typeof node.value === 'string' && node.value.includes('\n')) {
        const parts = node.value.split('\n');
        const nodes: unknown[] = [];
        parts.forEach((part, i) => {
          if (i > 0) nodes.push({type: 'break'});
          if (part) nodes.push({type: 'text', value: part});
        });
        return nodes;
      }
      if (Array.isArray(node.children)) {
        node.children = node.children.flatMap((child) => visit(child as {type?: string; value?: string; children?: unknown[]}));
      }
      return node;
    };
    visit(tree as {type?: string; value?: string; children?: unknown[]});
  };
}

/** 消息时间展示：今天显示 HH:mm，跨天显示 M月D日 HH:mm，跨年补年份。 */
function formatMessageTime(iso: string): string {
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return '';
  const now = new Date();
  const sameYear = time.getFullYear() === now.getFullYear();
  const sameDay = sameYear && time.getMonth() === now.getMonth() && time.getDate() === now.getDate();
  const pad = (value: number) => String(value).padStart(2, '0');
  const hhmm = `${pad(time.getHours())}:${pad(time.getMinutes())}`;
  if (sameDay) return hhmm;
  return sameYear ? `${time.getMonth() + 1}月${time.getDate()}日 ${hhmm}` : `${time.getFullYear()}年${time.getMonth() + 1}月${time.getDate()}日 ${hhmm}`;
}

function MessageFeed({messages, onRetry, onQuickAction}: {messages: Message[]; onRetry: (message: Message) => void; onQuickAction: (reply: string) => void}) {
  type Group = {id: string; compact: boolean; messages: Message[]};
  const groups: Group[] = [];
  for (const message of messages) {
    const compact = message.role === 'activity' && message.status === 'done' && !(message.artifacts || []).length;
    const previous = groups[groups.length - 1];
    if (compact && previous?.compact) {
      previous.messages.push(message);
      continue;
    }
    groups.push({id: message.id, compact, messages: [message]});
  }
  return (
    <>
      {groups.map((group) => group.compact ? (
        <div className="activity-grid" key={`activity-grid-${group.id}`}>
          {group.messages.map((message) => <ActivityRow key={message.id} message={message} onRetry={onRetry} />)}
        </div>
      ) : (
        <MessageRow key={group.id} message={group.messages[0]} onRetry={onRetry} onQuickAction={onQuickAction} />
      ))}
    </>
  );
}


function MessageRow({message, onRetry, onQuickAction}: {message: Message; onRetry: (message: Message) => void; onQuickAction?: (reply: string) => void}) {
  const [expanded, setExpanded] = useState(false);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const displayText = localizeAgentText(message.text);
  // 含 Markdown 图片的消息不折叠：否则画廊会被 340px 的折叠高度截断成“一行零几张”。
  const hasMarkdownMedia = /!\[[^\]]*\]\([^)]+\)/.test(displayText);
  const isLong = message.role !== 'activity' && displayText.length > 1400 && !hasMarkdownMedia;
  if (message.role === 'activity') return <ActivityRow message={message} onRetry={onRetry} />;
  const replyOptions = replyOptionsForMessage(message);
  const messageTime = message.createdAt ? formatMessageTime(message.createdAt) : '';
  return (
    <article
      className={`message-row role-${message.role}${message.kind === 'commentary' ? ' is-commentary' : ''}${message.streaming ? ' is-streaming' : ''}`}
      aria-busy={message.streaming || undefined}
    >
      <div className={`message-body ${isLong && !expanded ? 'is-collapsed' : ''}`}>
        {message.kind === 'commentary' && (
          <div className="message-kicker"><Brain size={12} />处理记录</div>
        )}
        <div className="message-content">
          <ReactMarkdown remarkPlugins={[remarkGfm, remarkKeepBreaks]} components={{
            a: (props) => {
              const href = props.href || '';
              if (href.startsWith('/api/artifact') && /\.(wav|mp3|m4a|ogg)$/i.test(href)) {
                return (
                  <span className="artifact-inline-audio">
                    <span className="artifact-inline-audio-label">{props.children}</span>
                    <audio controls preload="none" src={href}>你的浏览器不支持音频播放。</audio>
                  </span>
                );
              }
              return <a {...props} target="_blank" rel="noreferrer" />;
            },
            img: (props) => {
              const src = props.src || '';
              // 对话内 Markdown 图片统一渲染为可点击缩略图，点击放大查看（避免大图溢出）
              if (src.startsWith('/api/artifact')) {
                return (
                  <button type="button" className="md-image-button" onClick={() => setPreviewSrc(src)} title="点击放大" style={{width: 140, maxWidth: 140, flex: '0 0 140px'}}>
                    <img
                      src={src}
                      alt={props.alt || ''}
                      loading="lazy"
                      decoding="async"
                      style={{width: '100%', maxHeight: 160, objectFit: 'cover'}}
                      onError={(event) => {
                        const button = event.currentTarget.closest('button');
                        if (button) button.style.display = 'none';
                      }}
                    />
                  </button>
                );
              }
              return <img {...props} alt={props.alt || ''} loading="lazy" style={{maxWidth: 140, maxHeight: 160, borderRadius: 8, objectFit: 'cover'}} />;
            },
            pre: (props) => <CodeBlock>{props.children}</CodeBlock>,
            p: (props) => (
              <p className={extractText(props.children).trim() ? undefined : 'is-standalone-media-paragraph'}>
                {props.children}
              </p>
            ),
            table: ({children}) => (
              <div className="md-table-wrap">
                <table>{children}</table>
              </div>
            ),
          }}>
            {displayText}
          </ReactMarkdown>
        </div>
        {replyOptions.length > 0 && (
          <div className="reply-options" aria-label="快捷操作">
            {replyOptions.map((option) => (
              <button
                key={`${message.id}-${option.reply}`}
                type="button"
                className="reply-option"
                onClick={() => onQuickAction?.(option.reply)}
                disabled={message.streaming}
                title={message.streaming ? '回复生成中，请稍候再点' : `发送口令：${option.reply}`}
              >
                {message.streaming && <span className="reply-option-live" aria-hidden="true">●</span>}
                {option.label}
              </button>
            ))}
          </div>
        )}
        {isLong && <button className="message-expand" onClick={() => setExpanded((value) => !value)}>{expanded ? '收起消息' : `展开全文（${displayText.length} 字）`}</button>}
        {messageTime && <span className="message-time">{messageTime}</span>}
      </div>
      {previewSrc && createPortal(
        <div className="image-lightbox" onClick={() => setPreviewSrc(null)} role="dialog" aria-modal="true">
          <img src={previewSrc} alt="" onClick={(event) => event.stopPropagation()} />
          <button type="button" className="image-lightbox-close" onClick={() => setPreviewSrc(null)} aria-label="关闭">×</button>
        </div>,
        document.body,
      )}
    </article>
  );
}

function ActivityRow({message, onRetry}: {message: Message; onRetry: (message: Message) => void}) {
  const stage = message.stage ? humanizeStage(message.stage) : '';
  const localizedText = localizeAgentText(message.text);
  const detail = stage.toLowerCase() === localizedText.toLowerCase() ? localizedText : [stage, localizedText].filter(Boolean).join(' · ');
  const toolKind = activityToolKind(message.tool);
  const isError = message.status === 'error';
  const [expanded, setExpanded] = useState(false);
  const showToggle = isError && detail.length > 140;
  return (
    <div className={`activity-row status-${message.status || 'done'}`}>
      <span className={`activity-indicator tool-${toolKind}`}><ActivityToolIcon tool={message.tool} /></span>
      <div className="activity-content">
        <strong>{humanizeTool(message.tool || 'Workflow')}</strong>
        <span className={`activity-detail${isError ? (expanded ? ' is-expanded' : ' is-clamped') : ''}`}>{detail}</span>
        {isError && (
          <div className="activity-error-controls">
            {showToggle && (
              <button type="button" className="activity-error-toggle" onClick={() => setExpanded((value) => !value)}>
                {expanded ? '收起' : '展开完整错误'}
              </button>
            )}
            <div className="activity-actions">
              <button type="button" onClick={() => void navigator.clipboard.writeText(detail).then(() => notify('错误信息已复制')).catch(() => notify('复制失败', 'error'))}>复制错误</button>
              <button type="button" onClick={() => onRetry(message)}>重试步骤</button>
            </div>
          </div>
        )}
        {(message.artifacts || []).length > 0 && <InlineArtifacts artifacts={message.artifacts || []} />}
      </div>
    </div>
  );
}

/** 对话内嵌视频：按真实画面比例区分横屏/竖屏播放窗口，避免竖屏视频两侧出现大黑边。 */
function InlineVideo({artifact, poster, onError}: {artifact: Artifact; poster?: string; onError?: () => void}) {
  const [dimensions, setDimensions] = useState<{width: number; height: number}>();
  const orientation: MediaOrientation = dimensions ? resolveMediaOrientation(dimensions.width, dimensions.height) : 'portrait';
  return (
    <video
      src={mediaUrl(artifact)}
      poster={poster}
      controls
      preload="metadata"
      playsInline
      className={`is-${orientation}`}
      onLoadedMetadata={(event) => {
        const element = event.currentTarget;
        if (element.videoWidth && element.videoHeight) {
          setDimensions({width: element.videoWidth, height: element.videoHeight});
        }
      }}
      onError={onError}
    />
  );
}

function videoPosterFor(artifact: Artifact, images: Artifact[]): string | undefined {
  const normalizedPath = artifact.path.replace(/\\/g, '/');
  const shot = /(?:^|\/)shots\/(\d+)(?:\/|$)/i.exec(normalizedPath)?.[1];
  const poster = shot
    ? images.find((candidate) => {
      const path = candidate.path.replace(/\\/g, '/').toLowerCase();
      return new RegExp(`/shots/${shot}/first_frame\\.png$`, 'i').test(path);
    })
    : undefined;
  if (poster) return mediaUrl(poster);
  // 段级直出视频（segments/seg-N/video.mp4）旁边落盘了 <video>.poster.jpg 缩略图，
  // 与服务端 /api/video-poster 的抽帧约定一致；直接复用，避免卡片长时间黑屏。
  const siblingPoster = images.find(
    (candidate) => candidate.path.replace(/\\/g, '/') === `${normalizedPath}.poster.jpg`,
  );
  if (siblingPoster) return mediaUrl(siblingPoster);
  // 仅镜头产物沿用 <video>.mp4 → first_frame.png 的命名约定；非镜头产物没有这个约定，
  // 不要生成必然 404 的猜测路径。
  const posterPath = shot ? normalizedPath.replace(/\/video\.mp4$/i, '/first_frame.png') : normalizedPath;
  if (posterPath === normalizedPath) return undefined;
  try {
    const session = new URL(artifact.url, window.location.origin).searchParams.get('session');
    if (!session) return undefined;
    return `/api/artifact?session=${encodeURIComponent(session)}&path=${encodeURIComponent(posterPath)}`;
  } catch {
    return undefined;
  }
}

function resolveMediaPreviewLimit(total: number, compact: boolean) {
  const rowSize = compact ? 2 : 5;
  if (total <= rowSize) return total;
  // 6-10 张时用“4 张 + 更多”把首行补满；更多内容才进入第二行。
  if (total <= rowSize * 2) return rowSize - 1;
  return rowSize * 2 - 1;
}

function inlineImagePriority(artifact: Artifact): number {
  const path = artifact.path.replace(/\\/g, '/').toLowerCase();
  if (/\/first_frame\.png$/.test(path)) return 0;
  if (/\/last_frame\.png$/.test(path)) return 1;
  if (/\/first_frame_candidates\//.test(path) || /\/last_frame_candidates\//.test(path)) return 3;
  return 2;
}

function sortInlineImages(artifacts: Artifact[]): Artifact[] {
  const unique = new Map<string, Artifact>();
  artifacts.forEach((artifact) => unique.set(artifact.path, artifact));
  return [...unique.values()].sort((left, right) => {
    const priority = inlineImagePriority(left) - inlineImagePriority(right);
    if (priority !== 0) return priority;
    return left.path.localeCompare(right.path, undefined, {numeric: true, sensitivity: 'base'});
  });
}

function InlineArtifacts({artifacts}: {artifacts: Artifact[]}) {
  const [previewSrc, setPreviewSrc] = useState('');
  const [showAllImages, setShowAllImages] = useState(false);
  const [showAllVideos, setShowAllVideos] = useState(false);
  const [brokenPaths, setBrokenPaths] = useState<string[]>([]);
  const [compactMedia, setCompactMedia] = useState(false);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 760px)');
    const sync = () => setCompactMedia(media.matches);
    sync();
    media.addEventListener('change', sync);
    return () => media.removeEventListener('change', sync);
  }, []);
  // 历史记录里可能残留已被清理的图片路径，先过滤掉加载失败的，避免聊天区出现破图占位。
  // 同时防御目录/无扩展名路径（历史消息里的目录引用）——只渲染真实文件，避免 404 请求噪音。
  const isRenderableMedia = (artifact: Artifact) => {
    const path = artifact.path.replace(/\\/g, '/');
    if (/\/$/.test(path) || path.split('/').pop()?.includes('.') === false) return false;
    return /\.(png|jpe?g|gif|webp|bmp|avif|mp4|webm|mov|m4v)$/i.test(path);
  };
  const images = sortInlineImages(artifacts.filter((artifact) => artifact.kind === 'image' && isRenderableMedia(artifact) && !brokenPaths.includes(artifact.path)));
  const videos = artifacts.filter((artifact) => artifact.kind === 'video' && isRenderableMedia(artifact) && !brokenPaths.includes(artifact.path));
  const imagePreviewLimit = resolveMediaPreviewLimit(images.length, compactMedia);
  const videoPreviewLimit = resolveMediaPreviewLimit(videos.length, compactMedia);
  const visibleImages = showAllImages ? images : images.slice(0, imagePreviewLimit);
  const visibleVideos = showAllVideos ? videos : videos.slice(0, videoPreviewLimit);
  const hiddenImageCount = images.length - visibleImages.length;
  const hiddenVideoCount = videos.length - visibleVideos.length;
  const showImageMoreTile = images.length > imagePreviewLimit || showAllImages;
  const showVideoMoreTile = videos.length > videoPreviewLimit || showAllVideos;
  const markBroken = (path: string) => setBrokenPaths((current) => (current.includes(path) ? current : [...current, path]));
  if (videos.length === 0 && images.length === 0) return null;
  return (
    <div className="activity-media">
      {videos.length > 0 && (
        <div className="activity-media-group">
          <span className="activity-media-label">视频片段</span>
          <div className={`activity-media-videos${showVideoMoreTile ? ' has-more' : ''}`}>
            {visibleVideos.map((artifact) => (
              <figure className="activity-media-video" key={artifact.path}>
                <InlineVideo artifact={artifact} poster={videoPosterFor(artifact, images)} onError={() => markBroken(artifact.path)} />
                <figcaption>{visualArtifactLabel(artifact)}</figcaption>
              </figure>
            ))}
            {showVideoMoreTile && (
              <button type="button" className="activity-media-more is-video" onClick={() => setShowAllVideos((value) => !value)} title={showAllVideos ? '收起视频' : '展开全部视频'} aria-expanded={showAllVideos}>
                <Film size={19} />
                <strong>{showAllVideos ? '收起' : `+${hiddenVideoCount}`}</strong>
                <span>{showAllVideos ? `只看前 ${videoPreviewLimit} 个` : '查看全部'}</span>
              </button>
            )}
          </div>
        </div>
      )}
      {images.length > 0 && (
        <div className="activity-media-group">
          <span className="activity-media-label">画面</span>
          <div className="activity-media-images" style={{display: 'flex', flexWrap: 'wrap', gap: 6}}>
            {visibleImages.map((artifact, index) => (
              <button key={artifact.path} type="button" className="md-image-button" onClick={() => setPreviewSrc(mediaUrl(artifact))} title="点击放大" style={{width: 140, maxWidth: 140, flex: '0 0 140px'}}>
                <img
                  src={mediaUrl(artifact)}
                  alt={visualArtifactLabel(artifact)}
                  loading={showAllImages || index < 10 ? 'eager' : 'lazy'}
                  decoding="async"
                  style={{width: '100%', maxHeight: 160, objectFit: 'cover'}}
                  onError={() => markBroken(artifact.path)}
                />
              </button>
            ))}
            {showImageMoreTile && (
              <button type="button" className="activity-media-more" onClick={() => setShowAllImages((value) => !value)} title={showAllImages ? `只看前 ${imagePreviewLimit} 张` : '展开全部图片'} aria-expanded={showAllImages}>
                <ImageIcon size={19} />
                <strong>{showAllImages ? '收起' : `+${hiddenImageCount}`}</strong>
                <span>{showAllImages ? `只看前 ${imagePreviewLimit} 张` : '查看全部'}</span>
              </button>
            )}
          </div>
        </div>
      )}
      {previewSrc && createPortal(
        <div className="image-lightbox" onClick={() => setPreviewSrc('')} role="dialog" aria-modal="true">
          <img src={previewSrc} alt="" onClick={(event) => event.stopPropagation()} />
          <button type="button" className="image-lightbox-close" onClick={() => setPreviewSrc('')} aria-label="关闭">×</button>
        </div>,
        document.body,
      )}
    </div>
  );
}

function ProjectIcon({stage}: {stage?: string}) {
  const value = (stage || '').toLowerCase();
  if (value.includes('error')) return <AlertCircle size={15} />;
  if (value.includes('render') || value.includes('final')) return <Film size={15} />;
  if (value.includes('plan') || value.includes('script') || value.includes('story')) return <FilePenLine size={15} />;
  return <Folder size={15} />;
}


function ActivityToolIcon({tool}: {tool?: string}) {
  const name = (tool || '').toLowerCase();
  const props = {size: 13, strokeWidth: 1.8};
  if (name.includes('narrative_planning') || name.includes('novel_planning')) return <FilePenLine {...props} />;
  if (name.includes('render_video')) return <Film {...props} />;
  if (name === 'view_image' || name.includes('image')) return <ImageIcon {...props} />;
  if (name === 'read_json' || name === 'write_json') return <Braces {...props} />;
  if (name === 'read_file' || name === 'write_file') return <FileText {...props} />;
  if (name === 'list_files' || name === 'glob_files') return <Folder {...props} />;
  if (name === 'search_text') return <Search {...props} />;
  if (name.startsWith('memory_')) return <Brain {...props} />;
  if (name.startsWith('todo_')) return <ListChecks {...props} />;
  if (name === 'run_shell') return <Terminal {...props} />;
  if (name === 'sleep') return <Clock3 {...props} />;
  return <Wrench {...props} />;
}

function activityToolKind(tool?: string) {
  const name = (tool || '').toLowerCase();
  if (name.includes('narrative_planning') || name.includes('novel_planning')) return 'planning';
  if (name.includes('render_video')) return 'render';
  if (name === 'view_image' || name.includes('image')) return 'image';
  if (name.startsWith('memory_')) return 'memory';
  if (name.startsWith('todo_')) return 'todo';
  if (name === 'run_shell') return 'shell';
  if (name === 'sleep') return 'time';
  return 'file';
}

function ThinkingRow({messages, statusText}: {messages: Message[]; statusText?: string}) {
  const running = [...messages].reverse().find((message) => message.role === 'activity' && message.status === 'running');
  const streaming = messages.some((message) => message.role === 'assistant' && message.streaming);
  if (streaming && !running) return null;
  return (
    <div className="thinking-row">
      <span className="thinking-mark"><i /><i /><i /></span>
      <span>{running ? `${humanizeTool(running.tool || '映界')} · ${running.text}` : statusText || '智能体正在分析当前进度，规划下一步操作…'}</span>
    </div>
  );
}

function SlashCommandMenu({matches, contextPercent, onSelect}: {matches: SlashCommandMatch[]; contextPercent: number; onSelect: (command: string) => void}) {
  return (
    <div className="slash-command-menu" role="listbox" aria-label="斜杠命令">
      {matches.length > 0 ? matches.map((command) => (
        <button key={command.name} role="option" aria-selected="false" onMouseDown={(event) => event.preventDefault()} onClick={() => onSelect(command.name)}>
          <code><span><b>{command.matchedPrefix}</b><span>{command.unmatchedSuffix}</span></span>{command.name === '/compact' && <em>{contextPercent}%</em>}</code>
          <small>{command.description}</small>
        </button>
      )) : <span className="slash-command-empty">没有匹配的命令</span>}
    </div>
  );
}

const CONFIG_SECTIONS: Array<{key: keyof AgentConfig['sections']; title: string; description: string}> = [
  {key: 'llm', title: 'Agent 对话模型', description: '规划、工具选择与对话'},
  {key: 'storyboard', title: '分镜生成（速度调优）', description: '单批镜头数与并发路数；留空按 max_tokens 自动推导'},
  {key: 'image', title: '图像生成', description: '角色、关键帧与镜头画面'},
  {key: 'video', title: '视频生成', description: '镜头片段与成片'},
  {key: 'embedding', title: '向量化', description: '可选：小说检索'},
  {key: 'reranker', title: '重排模型', description: '可选：小说检索排序'},
  {key: 'bailian', title: '百炼音频（音色）', description: '声音设计创建角色音色，作为视频参考音频（按量计费）'},
  {key: 'tos', title: '对象存储 TOS（数据订阅）', description: '产物自动转存桶；原始 URL 超 24h 后自动映射桶内永久链接（信任免检持续成立）'},
];

function SettingsView({selectedSessionId, theme, onThemeChange}: {selectedSessionId: string; theme: Theme; onThemeChange: (theme: Theme) => void}) {
  const [config, setConfig] = useState<AgentConfig>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');

  useEffect(() => {
    let cancelled = false;
    void getAgentConfig()
      .then((payload) => !cancelled && setConfig(payload))
      .catch((error) => !cancelled && setStatus(error instanceof Error ? error.message : String(error)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  function update(section: keyof AgentConfig['sections'], field: keyof ConfigSection, value: string) {
    setStatus('');
    setConfig((current) => current ? {
      sections: {
        ...current.sections,
        [section]: {...current.sections[section], [field]: value},
      },
    } : current);
  }

  async function save() {
    if (!config || saving) return;
    setSaving(true);
    setStatus('');
    try {
      setConfig(await saveAgentConfig(config));
      setStatus('已保存');
      notify('配置已保存');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <div className="settings-loading">正在加载配置…</div>;
  if (!config) return <div className="settings-loading is-error">{status || '配置不可用'}</div>;
  return (
    <section className="settings-view">
      <header>
        <div><span>本地配置</span><h1>模型与存储</h1></div>
        <div className="settings-save-group">
          {status && <span className={status === '已保存' ? 'is-saved' : 'is-error'}>{status}</span>}
          <button className="settings-save" onClick={() => void save()} disabled={saving}>
            <Save size={15} />{saving ? '保存中…' : '保存'}
          </button>
        </div>
      </header>
      <div className="settings-sections">
        <section className="config-section theme-config-section">
          <header><h2>外观</h2><p>界面主题，选择后自动记住</p></header>
          <div className="theme-picker">
            {THEMES.map((item) => (
              <button
                key={item.key}
                type="button"
                className={theme === item.key ? 'is-selected' : ''}
                onClick={() => onThemeChange(item.key)}
                aria-pressed={theme === item.key}
              >
                <i className={`theme-swatch is-${item.key}`} aria-hidden="true" />
                <span><strong>{item.label}</strong><small>{item.desc}</small></span>
              </button>
            ))}
          </div>
        </section>
        <section className="config-section style-config-section">
          <header><h2>生成风格</h2><p>全局默认画风（红果短剧常用风格全覆盖），选定后自动记住并用于新项目</p></header>
          <StylePicker />
        </section>
        <CreationModeSection />
        {CONFIG_SECTIONS.map((definition) => (
          <ConfigSectionEditor
            key={definition.key}
            definition={definition}
            sectionKey={definition.key}
            value={config.sections[definition.key]}
            onChange={(field, value) => update(definition.key, field, value)}
          />
        ))}
      </div>
    </section>
  );
}

function CreationModeSection() {
  const [autonomous, setAutonomous] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState('');

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((cfg) => !cancelled && setAutonomous(cfg.autonomous_mode))
      .catch(() => {})
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, []);

  async function toggle(next: boolean) {
    setAutonomous(next);
    setSaving(true);
    setStatus('');
    try {
      await saveRuntimeConfig({autonomous_mode: next});
      setStatus(next ? '已切到自主模式（下一回合生效，无需重启）' : '已切回协作模式（下一回合生效）');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setAutonomous(!next);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="config-section creation-mode-section">
      <header>
        <h2>创作模式</h2>
        <p>控制智能体是「协作」等你指令，还是「自主」端到端推进并自评修订</p>
      </header>
      <div className="creation-mode-body">
        <div className="creation-mode-switch">
          <button
            type="button"
            className={`cmode-option ${!autonomous ? 'is-active' : ''}`}
            aria-pressed={!autonomous}
            disabled={loading || saving}
            onClick={() => void toggle(false)}
          >
            <strong>协作模式</strong>
            <span>逐阶段等你确认，资产/成片保留人工闸门</span>
          </button>
          <button
            type="button"
            className={`cmode-option ${autonomous ? 'is-active' : ''}`}
            aria-pressed={autonomous}
            disabled={loading || saving}
            onClick={() => void toggle(true)}
          >
            <strong>自主模式</strong>
            <span>分镜产出后自动评估质量门禁、自我修订后进入渲染</span>
          </button>
        </div>
        {loading ? (
          <span className="cmode-hint">正在读取创作模式…</span>
        ) : (
          <span className={`cmode-hint ${autonomous ? 'is-auto' : 'is-collab'}`}>
            {autonomous
              ? '当前：自主模式（asset 确认 / 成片验收仍保留人工闸门，防跑偏）'
              : '当前：协作模式（默认，按你指令逐步推进）'}
          </span>
        )}
        {status && <span className="cmode-status">{status}</span>}
      </div>
    </section>
  );
}

function ConfigSectionEditor({definition, sectionKey, value, onChange}: {
  definition: {title: string; description: string};
  sectionKey: keyof AgentConfig['sections'];
  value: ConfigSection;
  onChange: (field: keyof ConfigSection, value: string) => void;
}) {
  const [checkState, setCheckState] = useState<{ok: boolean; message: string} | null>(null);
  const [testState, setTestState] = useState<{testing: boolean; ok: boolean; message: string} | null>(null);

  function checkConfig() {
    if (sectionKey === 'tos') {
      const missing = [!value.bucket && 'Bucket', !value.region && 'Region'].filter(Boolean);
      setCheckState({ok: missing.length === 0, message: missing.length ? `缺少：${missing.join('、')}` : '配置完整'});
      return;
    }
    const missing = [!value.model && '模型', !value.base_url && '接口地址', !value.has_api_key && !value.api_key && 'API 密钥'].filter(Boolean);
    if (missing.length > 0) {
      setCheckState({ok: false, message: `缺少：${missing.join('、')}`});
      return;
    }
    try {
      new URL(value.base_url);
      setCheckState({ok: true, message: '配置格式正确'});
    } catch {
      setCheckState({ok: false, message: '接口地址格式不正确'});
    }
  }

  async function testConnection() {
    if (testState?.testing) return;
    setTestState({testing: true, ok: false, message: '正在连接测试…'});
    try {
      const result = await testAgentConfig(sectionKey);
      setTestState({testing: false, ok: result.ok, message: result.message});
    } catch (error) {
      setTestState({testing: false, ok: false, message: error instanceof Error ? error.message : String(error)});
    }
  }

  const actions = (
    <div className="config-check-group">
      {checkState && <span className={checkState.ok ? 'is-ok' : 'is-error'}>{checkState.message}</span>}
      {testState && <span className={testState.ok ? 'is-ok' : 'is-error'}>{testState.testing ? '正在连接测试…' : testState.message}</span>}
      <button type="button" onClick={checkConfig}>检查配置</button>
      <button type="button" className="config-test-button" title="测试的是已保存的配置；若刚修改未保存，请先点保存再测试" onClick={() => void testConnection()} disabled={testState?.testing}>
        <ActivityToolIcon tool="render_video" />{testState?.testing ? '测试中…' : '测试连接'}
      </button>
    </div>
  );

  if (sectionKey === 'storyboard') {
    return (
      <section className="config-section">
        <header><div><h2>{definition.title}</h2><p>{definition.description}</p></div></header>
        <div className="config-fields">
          <label>
            <span>单批镜头数（shots_per_batch）</span>
            <input value={value.shots_per_batch || ''} onChange={(event) => onChange('shots_per_batch', event.target.value)} placeholder="2-200，如 24；留空=按 max_tokens 自动推导" inputMode="numeric" autoComplete="off" />
          </label>
          <label>
            <span>并发路数（batch_concurrency）</span>
            <input value={value.batch_concurrency || ''} onChange={(event) => onChange('batch_concurrency', event.target.value)} placeholder="1-8，如 4；留空用默认 4" inputMode="numeric" autoComplete="off" />
          </label>
        </div>
      </section>
    );
  }

  if (sectionKey === 'tos') {
    return (
      <section className="config-section">
        <header><div><h2>{definition.title}</h2><p>{definition.description}</p></div>{actions}</header>
        <div className="config-fields">
          <label className="config-field-wide">
            <span>桶名称（Bucket）</span>
            <input value={value.bucket || ''} onChange={(event) => onChange('bucket', event.target.value)} placeholder="如 yingjie-assets" autoComplete="off" />
          </label>
          <label>
            <span>地域（Region）</span>
            <input value={value.region || ''} onChange={(event) => onChange('region', event.target.value)} placeholder="cn-beijing" autoComplete="off" />
          </label>
          <label>
            <span>对象前缀（Prefix）</span>
            <input value={value.prefix || ''} onChange={(event) => onChange('prefix', event.target.value)} placeholder="如 yingjie/" autoComplete="off" />
          </label>
        </div>
      </section>
    );
  }
  const mappedModel = hasModelPresentation(value.model);
  const modelPresentation = resolveModelPresentation(value.model);
  return (
    <section className="config-section">
      <header><div><h2>{definition.title}</h2><p>{definition.description}</p></div>{actions}</header>
      <div className="config-fields">
        {value.model_provider !== undefined && (
          <label><span>模型供应商</span><input value={value.model_provider} onChange={(event) => onChange('model_provider', event.target.value)} /></label>
        )}
        <label className={mappedModel ? 'config-model-field' : undefined}>
          <span>模型</span>
          {mappedModel && <strong className="config-model-name">{modelPresentation.name}</strong>}
          <input
            value={value.model}
            onChange={(event) => onChange('model', event.target.value)}
            aria-label={mappedModel ? `模型端点 ID（${modelPresentation.name}）` : '模型'}
            autoComplete="off"
          />
        </label>
        <label className="config-field-wide"><span>接口地址</span><input value={value.base_url} onChange={(event) => onChange('base_url', event.target.value)} inputMode="url" /></label>
        <label className="config-field-wide">
          <span>API 密钥 <i className={value.has_api_key ? 'is-configured' : ''}>{value.has_api_key ? `已配置 ${value.api_key_hint || ''}` : '未配置'}</i></span>
          <input type="password" value={value.api_key} onChange={(event) => onChange('api_key', event.target.value)} placeholder={value.has_api_key ? '留空则保留当前密钥' : '输入 API 密钥'} autoComplete="off" />
        </label>
        {sectionKey === 'llm' && (
          <>
            <label>
              <span>单次输出上限（max_tokens）</span>
              <input value={value.max_tokens || ''} onChange={(event) => onChange('max_tokens', event.target.value)} placeholder="如 196608（上限 393216；留空用配置文件原值）" inputMode="numeric" autoComplete="off" />
            </label>
            <label>
              <span>采样温度（temperature）</span>
              <input value={value.temperature || ''} onChange={(event) => onChange('temperature', event.target.value)} placeholder="如 0（0 最稳定；留空用配置文件原值）" inputMode="decimal" autoComplete="off" />
            </label>
            <label>
              <span>思考档（reasoning_effort）</span>
              <select value={value.reasoning_effort || 'default'} onChange={(event) => onChange('reasoning_effort', event.target.value)}>
                <option value="default">模型默认（不传该参数）</option>
                <option value="none">关闭思考（最快，none）</option>
                <option value="minimal">极低思考（minimal）</option>
                <option value="low">低思考（low）</option>
                <option value="medium">中等思考（medium）</option>
                <option value="high">高思考（high）</option>
                <option value="max">最高思考（max）</option>
              </select>
            </label>
          </>
        )}
        {sectionKey === 'bailian' && (
          <label className="config-field-wide">
            <span>业务空间 ID（WorkspaceId）</span>
            <input value={value.workspace_id || ''} onChange={(event) => onChange('workspace_id', event.target.value)} placeholder="用于华北2北京专属域名" autoComplete="off" />
          </label>
        )}
        {sectionKey === 'video' && (
          <>
            <label>
              <span>画幅</span>
              <select value={value.aspect_ratio || '9:16'} onChange={(event) => onChange('aspect_ratio', event.target.value)}>
                <option value="16:9">16:9 横屏</option>
                <option value="9:16">9:16 竖屏（短剧）</option>
              </select>
            </label>
            <label>
              <span>分辨率</span>
              <select value={value.resolution || '1080p'} onChange={(event) => onChange('resolution', event.target.value)}>
                <option value="480p">480P</option>
                <option value="720p">720P</option>
                <option value="1080p">1080P</option>
              </select>
            </label>
            <label>
              <span>帧率</span>
              <select value={value.fps || '16'} onChange={(event) => onChange('fps', event.target.value)}>
                <option value="16">16</option>
                <option value="24">24</option>
              </select>
            </label>
            <label>
              <span>单镜头时长</span>
              <select value={value.duration || '5'} onChange={(event) => onChange('duration', event.target.value)}>
                <option value="5">5 秒</option>
                <option value="10">10 秒</option>
              </select>
            </label>
            <label>
              <span>成片字幕</span>
              <select value={value.subtitles === '0' ? '0' : '1'} onChange={(event) => onChange('subtitles', event.target.value)}>
                <option value="1">开启（压制对白字幕）</option>
                <option value="0">关闭（纯画面）</option>
              </select>
            </label>
          </>
        )}
      </div>
    </section>
  );
}

function DeleteProjectDialog({session, title, deleting, onCancel, onConfirm}: {
  session?: SessionSummary;
  title: string;
  deleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    if (!session) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !deleting) onCancel();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [deleting, onCancel, session]);

  if (!session) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}>
      <section className="delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-project-title">
        <span className="dialog-icon"><Trash2 size={18} /></span>
        <div className="dialog-copy">
          <h2 id="delete-project-title">删除项目？</h2>
          <p><strong>{title || sessionTitle(session)}</strong> 及其生成的文件将被永久删除。</p>
        </div>
        <div className="dialog-actions">
          <button onClick={onCancel} disabled={deleting} autoFocus>取消</button>
          <button className="danger" onClick={onConfirm} disabled={deleting}>{deleting ? '删除中…' : '删除'}</button>
        </div>
      </section>
    </div>
  );
}

function NewProjectDialog({open, name, error, creating, onNameChange, onCancel, onConfirm}: {
  open: boolean;
  name: string;
  error: string;
  creating: boolean;
  onNameChange: (value: string) => void;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !creating) onCancel();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [creating, onCancel, open]);

  if (!open) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onCancel()}>
      <form className="project-dialog" role="dialog" aria-modal="true" aria-labelledby="new-project-title" onSubmit={(event) => {
        event.preventDefault();
        onConfirm();
      }}>
        <span className="dialog-icon is-create"><FolderPlus size={18} /></span>
        <div className="dialog-copy">
          <h2 id="new-project-title">新建项目</h2>
          <p>创建前请先为项目命名。</p>
        </div>
        <label className="project-name-field">
          <span>项目名称</span>
          <input
            value={name}
            onChange={(event) => onNameChange(event.target.value)}
            placeholder="未命名视频"
            maxLength={64}
            autoFocus
            disabled={creating}
          />
          {error && <small role="alert">{error}</small>}
        </label>
        <div className="dialog-actions">
          <button type="button" onClick={onCancel} disabled={creating}>取消</button>
          <button type="submit" className="primary" disabled={creating || !name.trim()}>{creating ? '创建中…' : '创建'}</button>
        </div>
      </form>
    </div>
  );
}

function sessionTitle(session?: SessionSummary) {
  if (!session) return '新视频';
  if (session.projectName) return session.projectName;
  const source = session.idea || session.summary;
  if (source) return source.length > 38 ? `${source.slice(0, 38).trim()}…` : source;
  return session.sessionId.replace(/^\d{8}-\d{6}-?/, '') || '未命名视频';
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    created: '已创建',
    narrative_planning: '规划中',
    narrative_planned: '规划完成',
    assets_manifest_ready: '资产清单已就绪',
    assets_manifest_partial: '资产清单部分就绪',
    assets_extracting: '资产清单提取中',
    assets_pending: '待生成资产',
    assets_generating: '资产生成中',
    assets_confirm_pending: '待确认资产',
    assets_confirmed: '资产已确认',
    assets_ready: '资产已就绪',
    novel_planning: '小说规划中',
    novel_planned: '小说规划完成',
    rendering: '渲染中',
    rendered: '已渲染',
    error: '需处理',
  };
  return labels[stage] || humanizeStage(stage || 'created');
}

function stageTone(stage: string): 'idle' | 'running' | 'done' | 'error' {
  if (stage === 'error') return 'error';
  if (stage === 'rendered' || stage === 'narrative_planned' || stage === 'novel_planned') return 'done';
  if (stage === 'rendering' || stage === 'narrative_planning' || stage === 'novel_planning' || stage === 'assets_manifest_ready' || stage === 'assets_manifest_partial' || stage === 'assets_ready' || stage === 'assets_pending' || stage === 'assets_generating' || stage === 'assets_confirm_pending') return 'running';
  if (stage === 'assets_confirmed') return 'done';
  return 'idle';
}

function relativeTime(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return '刚刚';
  const delta = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(delta / 60_000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes}分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时前`;
  return `${Math.floor(hours / 24)}天前`;
}

function AgentTaskCard({plan, busy}: {plan?: WorkflowPlan; busy: boolean}) {
  if (!plan || !plan.title) return null;
  const steps = plan.steps || [];
  const doneCount = steps.filter((step) => step.status === 'done').length;
  const activeStep = steps.find((step) => step.status === 'active');
  const status = String(plan.status || 'ready');
  const failed = status === 'failed';
  const interrupted = status === 'cancelled' || status === 'halted' || status === 'stopping';
  const waiting = Boolean(plan.waiting_for_user || plan.requires_confirmation) && !busy;
  const statusText = failed ? '执行失败' : interrupted ? '等待续跑' : waiting ? '等待确认' : busy ? '执行中' : '计划已就绪';
  const tone = failed ? 'error' : interrupted ? 'warning' : waiting ? 'waiting' : busy ? 'running' : 'ready';
  const blockers = (plan.blockers || []).filter(Boolean);
  return (
    <section className={`agent-task-card is-${tone}`} aria-label="智能体任务计划" role="status">
      <span className="agent-task-icon" aria-hidden="true">
        {failed ? <AlertCircle size={16} /> : interrupted ? <Clock3 size={16} /> : waiting ? <CheckCircle2 size={16} /> : <ListChecks size={16} />}
      </span>
      <div className="agent-task-copy">
        <div className="agent-task-heading">
          <strong>{plan.title}</strong>
          <span className="agent-task-status">{statusText}</span>
          {steps.length > 0 && <span className="agent-task-progress" title={`已完成 ${doneCount} / 共 ${steps.length} 步`}>步骤 {doneCount}/{steps.length}</span>}
        </div>
        {plan.summary && <p>{plan.summary}</p>}
        <div className="agent-task-flow">
          <span className="agent-task-flow-node is-current"><b>当前</b>{activeStep?.label || plan.current_step || '准备中'}</span>
          <span className="agent-task-flow-arrow" aria-hidden="true">→</span>
          <span className="agent-task-flow-node is-next"><b>下一步</b>{plan.next_step || '等待智能体判断'}</span>
        </div>
        {blockers.length > 0 && (
          <div className="agent-task-blockers">
            {blockers.slice(0, 2).map((blocker, index) => (
              <span key={index} className="agent-task-blocker" title={blocker}>⚠ {blocker}</span>
            ))}
          </div>
        )}
      </div>
      {waiting && <span className="agent-task-waiting"><Sparkles size={13} /> 等你确认后继续</span>}
    </section>
  );
}

function WorkflowSteps({messages, stage, progressStage}: {messages: Message[]; stage?: string; progressStage?: string}) {
  const currentIndex = useMemo(() => {
    // 定位口径唯一在 events.resolveWorkflowStepIndex（含"清单没抽完时不认资产进度"那条），
    // 这里只渲染，避免 UI 里再长出一套优先级。
    return resolveWorkflowStepIndex(messages, stage, progressStage);
  }, [messages, stage, progressStage]);
  // 实时流：取最新一条带 stage 的进度消息文本，显示在步骤条下方
  const latestActivity = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.stage && m.text && m.text.trim()) return m.text.trim();
    }
    return '';
  }, [messages]);
  if (currentIndex < 0) return null;
  return (
    <div className="workflow-steps" aria-label="生成视频步骤" role="list">
      {WORKFLOW_STEPS.map((step, index) => {
        const done = index < currentIndex;
        const active = index === currentIndex;
        return (
          <div key={step.id} className="workflow-step-item" role="listitem">
            <div
              className={`workflow-step${done ? ' is-done' : ''}${active ? ' is-active' : ''}`}
              title={active ? step.name : done ? step.doneName : step.name}
            >
              <span className="workflow-step-badge">{done ? <Check size={11} strokeWidth={3} /> : <span className="workflow-step-dot" />}</span>
              <span className="workflow-step-label">{done ? step.doneName : step.name}</span>
            </div>
            {index < WORKFLOW_STEPS.length - 1 && (
              <span className={`workflow-step-line${active ? ' is-active' : ''}${done ? ' is-done' : ''}`} />
            )}
          </div>
        );
      })}
      {latestActivity && <div className="workflow-steps-live">● {latestActivity}</div>}
    </div>
  );
}

function ToastHost() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => subscribeToToasts((toast) => {
    setToasts((current) => [...current.filter((item) => item.id !== toast.id), toast].slice(-4));
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== toast.id)), 3200);
  }), []);

  return (
    <div className="toast-stack" aria-live="polite" aria-label="操作提示">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast-item is-${toast.tone}`}>
          {toast.tone === 'success' ? <CheckCircle2 size={16} /> : toast.tone === 'error' ? <AlertCircle size={16} /> : <Info size={16} />}
          <span>{toast.message}</span>
          <button onClick={() => setToasts((current) => current.filter((item) => item.id !== toast.id))} aria-label="关闭提示"><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}
