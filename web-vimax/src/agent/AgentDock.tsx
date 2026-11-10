import {useCallback, useEffect, useRef, useState} from 'react';
import {
  AtSign, Check, ChevronRight, Download, FileText, History, Image as ImageIcon, Maximize2, Music2,
  Plus, RefreshCw, RotateCcw, Search, Send, Settings, SlidersHorizontal, Sparkles, Square,
  Type, Upload, Video, Wand2, X,
} from 'lucide-react';
import {agentApi, type AgentContentBlock, type AgentDraft, type AgentFile, type AgentLock, type AgentModel, type AgentRun, type AgentSkill, type AgentSkillDetail} from './agentApi';
import {AgentComposer} from './AgentComposer';
import {AgentMessageContent} from './AgentMessageContent';

type AgentDockProps = {
  projectId: string | null;
  enabled?: boolean;
};

type AgentReference = {
  id: string;
  title: string;
  type: string;
  nodeId?: string;
  assetId?: string;
  seriesId?: string;
  variantKey?: string;
};

const STORAGE_KEY = 'canvas.agentDock.open';
const WIDTH_KEY = 'canvas.agentDock.width';

const MODEL_LABELS: Record<string, string> = {
  'deepseek-flash-1017': 'DeepSeek V4.1 Flash 1017',
  'deepseek-flash': 'DeepSeek Flash',
  'deepseek-flash-free': 'DeepSeek Flash Free',
  'deepseek-v4-flash': 'DeepSeek V4 Flash',
  'deepseek-v4-flash-free': 'DeepSeek V4 Flash Free',
};

const LOCK_DEFAULTS = {
  track: '国内剧',
  source: '原创',
  modelVersion: 'Seedance 2.0',
  aspect: '9:16 竖屏',
  delivery: '15s 单段',
  seam: '无缝',
};

const FALLBACK_LOCKS: AgentLock[] = [
  {key: 'track', label: '赛道', options: ['国内剧', '海外剧', '解说', '漫剧']},
  {key: 'source', label: '素材来源', options: ['原创', '对标', '小说改编']},
  {key: 'modelVersion', label: '模型版本', options: ['Seedance 2.0', 'Seedance 2.5']},
  {key: 'aspect', label: '画幅', options: ['9:16 竖屏', '16:9 横屏']},
  {key: 'delivery', label: '交付形态', options: ['15s 单段', '多段拼接']},
  {key: 'seam', label: '段缝', options: ['无缝', '硬切', '转场']},
];

function cleanRunText(content: string): string {
  let text = String(content || '').trim();
  if (!text) return '';
  const repairIndex = text.indexOf('上次画布预检问题');
  if (repairIndex >= 0) text = text.slice(0, repairIndex).trim();
  if (/^(生成完成，但|生成失败|导入预检未通过|投喂稿已通过预检)/.test(text)) return '';
  return text;
}

function AgentDock({projectId, enabled = true}: AgentDockProps) {
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch { return false; }
  });
  const [closing, setClosing] = useState(false);
  const [width, setWidth] = useState(() => {
    try { return Math.max(380, Math.min(760, Number(localStorage.getItem(WIDTH_KEY)) || 480)); } catch { return 480; }
  });
  const [resizing, setResizing] = useState(false);
  const [models, setModels] = useState<AgentModel[]>([]);
  const [modelId, setModelId] = useState('');
  const [skills, setSkills] = useState<AgentSkill[]>([]);
  const [skill, setSkill] = useState<AgentSkill | null>(null);
  const [routeId, setRouteId] = useState('');
  const [projectTitle, setProjectTitle] = useState('');
  const [prompt, setPrompt] = useState('');
  const [attachments, setAttachments] = useState<AgentFile[]>([]);
  const [run, setRun] = useState<AgentRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState('');
  const [managerOpen, setManagerOpen] = useState(false);
  const [managerQuery, setManagerQuery] = useState('');
  const [managerSkill, setManagerSkill] = useState<AgentSkill | null>(null);
  const [managerDetail, setManagerDetail] = useState<AgentSkillDetail | null>(null);
  const [modelMenu, setModelMenu] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [atOpen, setAtOpen] = useState(false);
  const [atCategory, setAtCategory] = useState('subject');
  const [atNodes, setAtNodes] = useState<Array<Record<string, unknown>>>([]);
  const [pendingPrompt, setPendingPrompt] = useState('');
  const [locks, setLocks] = useState<Record<string, string>>({...LOCK_DEFAULTS});
  const [historyOpen, setHistoryOpen] = useState(false);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [threadRuns, setThreadRuns] = useState<AgentRun[]>([]);
  const [conversationId, setConversationId] = useState('default');
  const [conversationReady, setConversationReady] = useState(false);
  const [traceOpen, setTraceOpen] = useState(false);
  const [atQuery, setAtQuery] = useState('');
  const [references, setReferences] = useState<AgentReference[]>([]);

  const panelRef = useRef<HTMLElement | null>(null);
  const widthRef = useRef(width);
  const dragRef = useRef<{startX: number; startWidth: number} | null>(null);
  const rafRef = useRef<number | null>(null);
  const chatRef = useRef<HTMLElement | null>(null);
  const promptRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const skillFileRef = useRef<HTMLInputElement | null>(null);
  const hydratedProjectRef = useRef<string | null>(null);
  const restoredRunRef = useRef<string | null>(null);
  const idempotencyKeyRef = useRef('');
  const draftTimerRef = useRef<number | null>(null);
  const closeTimerRef = useRef<number | null>(null);

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, open ? '1' : '0'); } catch {}
  }, [open]);

  useEffect(() => {
    const el = chatRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [run && run.messages.length, run && run.status, pendingPrompt]);

  useEffect(() => {
    widthRef.current = width;
    if (panelRef.current) panelRef.current.style.width = width + 'px';
  }, [width]);

  useEffect(() => {
    if (!projectId) return;
    try {
      localStorage.setItem('canvas.agent.prefs.' + projectId, JSON.stringify({
        modelId,
        routeId,
        skillId: skill ? skill.id : '',
        attachments,
        references,
      }));
    } catch {}
  }, [projectId, modelId, routeId, skill, attachments]);

  useEffect(() => {
    if (!projectId || !run) return;
    try { localStorage.setItem('canvas.agent.run.' + projectId, run.id); } catch {}
  }, [projectId, run]);

  useEffect(() => {
    if (!projectId || !open) return;
    restoredRunRef.current = null;
    try {
      const runId = localStorage.getItem('canvas.agent.run.' + projectId);
      if (runId) {
        restoredRunRef.current = runId;
        void agentApi.run(runId).then((data) => setRun(data.run)).catch(() => {
          try { localStorage.removeItem('canvas.agent.run.' + projectId); } catch {}
        });
      }
    } catch {}
  }, [projectId, open]);

  const refreshMetadata = useCallback(async () => {
    try {
      const [modelData, skillData, projectData] = await Promise.all([
        agentApi.models(),
        agentApi.skills(),
        projectId ? agentApi.project(projectId).catch(() => null) : Promise.resolve(null),
      ]);
      setModels(modelData.models || []);
      setSkills(skillData.skills || []);
      if (projectData) {
        setProjectTitle(projectData.project.title || (projectData.project.payload && projectData.project.payload.title) || projectId || '');
      }
      const shouldHydrate = hydratedProjectRef.current !== projectId;
      let saved: Record<string, unknown> = {};
      if (shouldHydrate && projectId) {
        try { saved = JSON.parse(localStorage.getItem('canvas.agent.prefs.' + projectId) || '{}') as Record<string, unknown>; } catch {}
      }
      const savedSkillId = shouldHydrate ? String(saved.skillId || '') : '';
      const restoredSkill = savedSkillId
        ? (skillData.skills || []).find((item) => item.id === savedSkillId) || null
        : null;
      if (shouldHydrate) {
        setSkill(restoredSkill);
        setRouteId(String(saved.routeId || ''));
        setModelId(String(saved.modelId || modelData.defaultModelId || (modelData.models[0] && modelData.models[0].id) || ''));
        setAttachments(Array.isArray(saved.attachments) ? saved.attachments as AgentFile[] : []);
        setReferences(Array.isArray(saved.references) ? saved.references as AgentReference[] : []);
        hydratedProjectRef.current = projectId || null;
      } else {
        setModelId((current) => current || modelData.defaultModelId || (modelData.models[0] && modelData.models[0].id) || '');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [projectId]);

  useEffect(() => {
    if (!open) return;
    setRun(null);
    setPendingPrompt('');
    setReferences([]);
    setError('');
    void refreshMetadata();
  }, [open, refreshMetadata, projectId]);

  useEffect(() => {
    if (!open || !projectId) return;
    let alive = true;
    void agentApi.runs(projectId).then((data) => { if (alive) setRuns(data.runs || []); }).catch(() => {});
    return () => { alive = false; };
  }, [open, projectId, run && run.status]);

  const applyDraft = useCallback((draft: AgentDraft) => {
    setPrompt(String(draft.text || ''));
    setAttachments(Array.isArray(draft.attachments) ? draft.attachments : []);
    setReferences(
      Array.isArray(draft.references)
        ? draft.references.map((item, index) => ({
            id: String(item.id || item.nodeId || item.assetId || 'ref-' + index),
            title: String(item.title || item.displayName || item.assetId || item.nodeId || '素材'),
            type: String(item.type || 'image'),
            assetId: item.assetId,
            seriesId: item.seriesId,
            variantKey: item.variantKey,
          }))
        : [],
    );
    if (draft.modelId) setModelId(draft.modelId);
    if (draft.routeId) setRouteId(draft.routeId);
    if (draft.locks && Object.keys(draft.locks).length) setLocks(draft.locks);
  }, []);

  useEffect(() => {
    if (!open || !projectId) {
      setConversationReady(false);
      return;
    }
    let alive = true;
    setConversationReady(false);
    const restore = async () => {
      let stored = '';
      try { stored = localStorage.getItem('canvas.agent.conversation.' + projectId) || ''; } catch {}
      let conversation = stored
        ? await agentApi.conversation(stored).then((data) => data.conversation).catch(() => null)
        : null;
      if (!conversation) {
        conversation = (await agentApi.createConversation(projectId)).conversation;
      }
      if (!alive) return;
      setConversationId(conversation.id);
      if (conversation.messages && conversation.messages.length) {
        const latest = [...conversation.messages].reverse().find((message) => message.runId);
        if (latest?.runId) {
          void agentApi.run(latest.runId).then((data) => { if (alive) setRun(data.run); }).catch(() => {});
        }
      }
      try { localStorage.setItem('canvas.agent.conversation.' + projectId, conversation.id); } catch {}
      const draft = await agentApi.draft(conversation.id).then((data) => data.draft).catch(() => null);
      if (alive && draft) {
        applyDraft(draft);
        if (draft.skillId) {
          const detail = await agentApi.skill(draft.skillId).catch(() => null);
          if (alive && detail) {
            setSkill({
              ...detail.skill,
              locks: detail.locks || detail.skill.locks || FALLBACK_LOCKS,
            });
          }
        }
      }
      if (alive) setConversationReady(true);
    };
    void restore().catch((err) => {
      if (alive) {
        setError(err instanceof Error ? err.message : String(err));
        setConversationReady(true);
      }
    });
    return () => { alive = false; };
  }, [open, projectId]);

  useEffect(() => {
    if (!open || !conversationReady || !conversationId) return;
    if (draftTimerRef.current !== null) window.clearTimeout(draftTimerRef.current);
    draftTimerRef.current = window.setTimeout(() => {
      draftTimerRef.current = null;
      void agentApi.saveDraft(conversationId, {
        text: prompt,
        attachments,
        references,
        skillId: skill?.id || '',
        routeId,
        modelId,
        locks,
      }).catch(() => {});
    }, 500);
    return () => {
      if (draftTimerRef.current !== null) {
        window.clearTimeout(draftTimerRef.current);
        draftTimerRef.current = null;
      }
    };
  }, [open, conversationReady, conversationId, prompt, attachments, references, skill && skill.id, routeId, modelId, locks]);

  useEffect(() => {
    if (!open || !projectId || !conversationId) return;
    let alive = true;
    void agentApi.runs(projectId, conversationId).then(async (data) => {
      const summaries = data.runs || [];
      const full = await Promise.all(summaries.slice(0, 12).map((item) =>
        agentApi.run(item.id).then((result) => result.run).catch(() => item)
      ));
      if (alive) {
        setThreadRuns(full);
        if (full[0]) setRun((current) => current || full[0]);
      }
    }).catch(() => {});
    return () => { alive = false; };
  }, [open, projectId, conversationId]);

  useEffect(() => {
    setTraceOpen(Boolean(run && (run.status === 'queued' || run.status === 'running')));
  }, [run && run.id, run && run.status]);

  useEffect(() => {
    if (!skill || (skill.locks && skill.locks.length)) return;
    let alive = true;
    void agentApi.skill(skill.id).then((detail) => {
      if (!alive) return;
      const locks = detail.locks || detail.skill.locks || FALLBACK_LOCKS;
      setSkill((current) => current && current.id === skill.id ? {...current, locks} : current);
      const defaults: Record<string, string> = {};
      locks.forEach((field) => { defaults[field.key] = field.default || field.options[0]; });
      setLocks((current) => Object.keys(current).length ? current : defaults);
    }).catch(() => {});
    return () => { alive = false; };
  }, [skill && skill.id, skill && skill.locks]);

  const refreshRun = useCallback(async (id: string) => {
    try {
      const data = await agentApi.run(id);
      setRun(data.run);
      setThreadRuns((current) => current.map((item) => item.id === data.run.id ? data.run : item));
      if (data.run.status === 'failed' && data.run.error) setError(data.run.error);
    } catch {}
  }, []);

  const closePanel = useCallback(() => {
    if (closeTimerRef.current !== null) return;
    setClosing(true);
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      setOpen(false);
      setClosing(false);
    }, 160);
  }, []);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
    }
  }, []);

  useEffect(() => {
    if (!run || !run.id) return;
    let alive = true;
    let scheduled: number | null = null;
    const refresh = () => { if (alive) void refreshRun(run.id); };
    const schedule = () => {
      if (!alive || scheduled !== null) return;
      scheduled = window.setTimeout(() => {
        scheduled = null;
        refresh();
      }, 80);
    };
    const source = new EventSource(agentApi.eventsUrl(run.id), {withCredentials: true});
    source.onmessage = schedule;
    source.onerror = schedule;
    const timer = window.setInterval(refresh, 1800);
    return () => {
      alive = false;
      source.close();
      window.clearInterval(timer);
      if (scheduled !== null) window.clearTimeout(scheduled);
    };
  }, [run && run.id, refreshRun]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (managerOpen) { setManagerOpen(false); return; }
      if (pickerOpen || modelMenu || settingsOpen || atOpen) {
        setPickerOpen(false); setModelMenu(false); setSettingsOpen(false); setAtOpen(false); return;
      }
      closePanel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [managerOpen, pickerOpen, modelMenu, settingsOpen, atOpen, closePanel]);

  useEffect(() => {
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target) return;
      if (target.closest('.agent-popover') || target.closest('.agent-modal-backdrop') || target.closest('[data-agent-trigger]')) return;
      setPickerOpen(false);
      setModelMenu(false);
      setSettingsOpen(false);
      setAtOpen(false);
    };
    document.addEventListener('pointerdown', onPointer, true);
    return () => document.removeEventListener('pointerdown', onPointer, true);
  }, []);

  const startResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {startX: event.clientX, startWidth: widthRef.current};
    setResizing(true);
  }, []);

  const moveResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    event.preventDefault();
    if (rafRef.current !== null) return;
    const clientX = event.clientX;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const next = Math.max(380, Math.min(760, drag.startWidth + (drag.startX - clientX)));
      widthRef.current = next;
      if (panelRef.current) panelRef.current.style.width = next + 'px';
    });
  }, []);

  const endResize = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    event.preventDefault();
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setResizing(false);
    setWidth(widthRef.current);
    try { localStorage.setItem(WIDTH_KEY, String(widthRef.current)); } catch {}
  }, []);

  const autoGrow = useCallback(() => {
    const el = promptRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.max(64, Math.min(128, el.scrollHeight)) + 'px';
  }, []);

  const reset = useCallback(() => {
    setRun(null);
    setThreadRuns([]);
    setPrompt('');
    setAttachments([]);
    setPendingPrompt('');
    setReferences([]);
    setError('');
    if (projectId) {
      void agentApi.createConversation(projectId, '新会话').then((data) => {
        setConversationId(data.conversation.id);
        setConversationReady(true);
        try {
          localStorage.setItem('canvas.agent.conversation.' + projectId, data.conversation.id);
          localStorage.removeItem('canvas.agent.run.' + projectId);
        } catch {}
      }).catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
      });
    }
    window.requestAnimationFrame(autoGrow);
  }, [autoGrow, projectId]);

  const openPicker = useCallback(() => {
    setPickerQuery('');
    setPickerOpen(true);
    setModelMenu(false);
    setSettingsOpen(false);
    setAtOpen(false);
  }, []);

  const selectSkill = useCallback(async (next: AgentSkill) => {
    setPendingPrompt('');
    setSkill(next);
    setRouteId('');
    setPickerOpen(false);
    setManagerOpen(false);
    try {
      const detail = await agentApi.skill(next.id);
      const locks = detail.locks || detail.skill.locks || FALLBACK_LOCKS;
      setSkill({...next, locks});
      const defaults: Record<string, string> = {};
      locks.forEach((field) => { defaults[field.key] = field.default || field.options[0]; });
      setLocks(defaults);
    } catch {
      setLocks({...LOCK_DEFAULTS});
    }
    window.requestAnimationFrame(() => promptRef.current?.focus());
  }, []);

  const openManager = useCallback(async () => {
    setPickerOpen(false);
    setModelMenu(false);
    setSettingsOpen(false);
    setAtOpen(false);
    setManagerOpen(true);
    try {
      const data = await agentApi.skills();
      setSkills(data.skills || []);
      const first = data.skills && data.skills[0];
      if (first) {
        setManagerSkill(first);
        const [detail, versions] = await Promise.all([
          agentApi.skill(first.id),
          agentApi.versions(first.id).catch(() => ({versions: []})),
        ]);
        setManagerDetail({...detail, versions: versions.versions || []});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const selectManagerSkill = useCallback(async (next: AgentSkill) => {
    setManagerSkill(next);
    try {
      const [detail, versions] = await Promise.all([
        agentApi.skill(next.id),
        agentApi.versions(next.id).catch(() => ({versions: []})),
      ]);
      setManagerDetail({...detail, versions: versions.versions || []});
    } catch {
      setManagerDetail(null);
    }
  }, []);

  const uploadSkill = useCallback(async (file: File) => {
    setBusy(true);
    setError('');
    try {
      await agentApi.uploadSkill(file);
      await refreshMetadata();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [refreshMetadata]);

  const uploadAttachment = useCallback(async (file: File) => {
    setBusy(true);
    setError('');
    try {
      const data = await agentApi.uploadFile(file);
      setAttachments((current) => [...current, data.file]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const requestRun = useCallback(() => {
    if (!projectId) { setError('先选择项目'); return; }
    if (!skill) { openPicker(); return; }
    if (!prompt.trim()) { setError('先写目标'); return; }
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = 'msg_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 10);
    }
    setPendingPrompt(prompt);
    setError('');
  }, [skill, prompt, openPicker]);

  const confirmRun = useCallback(async () => {
    if (!skill || !pendingPrompt) return;
    setBusy(true);
    setError('');
    try {
      let activeConversationId = conversationId;
      if (!conversationReady || !activeConversationId || activeConversationId === 'default') {
        const created = await agentApi.createConversation(projectId || '', '新会话');
        activeConversationId = created.conversation.id;
        setConversationId(activeConversationId);
        try { localStorage.setItem('canvas.agent.conversation.' + projectId, activeConversationId); } catch {}
      }
      const content: AgentContentBlock[] = [{type: 'text', text: pendingPrompt}];
      const data = await agentApi.appendMessage(
        activeConversationId,
        {
          content,
          skillId: skill.id,
          modelId,
          routeId,
          locks,
          fileIds: attachments.map((file) => file.id),
          referenceNodeIds: references.map((item) => item.nodeId || item.id),
        },
        idempotencyKeyRef.current,
      );
      setThreadRuns((current) => [data.run, ...current.filter((item) => item.id !== data.run.id)]);
      setRun(data.run);
      setPendingPrompt('');
      setPrompt('');
      setAttachments([]);
      setReferences([]);
      idempotencyKeyRef.current = '';
      window.requestAnimationFrame(autoGrow);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [skill, pendingPrompt, projectId, modelId, routeId, locks, attachments, references, conversationId, conversationReady, autoGrow]);

  const importRun = useCallback(async () => {
    if (!run) return;
    setBusy(true);
    setError('');
    try {
      const data = await agentApi.importRun(run.id);
      setRun(data.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [run]);

  const openHistory = useCallback(async () => {
    if (!projectId) return;
    setHistoryOpen(true);
    try {
      const data = await agentApi.runs(projectId);
      setRuns(data.runs || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [projectId]);

  const cancelCurrentRun = useCallback(async () => {
    if (!run) return;
    try {
      await agentApi.cancel(run.id);
      await refreshRun(run.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [run, refreshRun]);

  const retryCurrentRun = useCallback(async (repair = false) => {
    if (!run) return;
    const artifact = run.artifacts.filter((item) => item.kind === 'canvas_feed').slice(-1)[0];
    const payload = artifact ? artifact.payload as Record<string, unknown> : {};
    const rawIssues = payload && Array.isArray(payload.errors) ? payload.errors as Array<Record<string, unknown>> : [];
    const issues = rawIssues.map((item) => String(item.message || item.code || '')).filter(Boolean);
    const basePrompt = run.prompt.split('\n\n上次画布预检问题')[0].trim() || run.prompt;
    const nextPrompt = repair && issues.length
      ? basePrompt + '\n\n上次画布预检问题，请修复后重新输出完整 JSON：\n' + issues.slice(0, 12).join('\n')
      : basePrompt;
    setBusy(true);
    setError('');
    try {
      const data = await agentApi.createRun({
        projectId: run.projectId,
        skillId: run.skillId,
        prompt: nextPrompt,
        modelId: run.modelId,
        routeId: run.routeId,
        locks: run.locks || {},
        fileIds: run.fileIds || [],
        referenceNodeIds: run.referenceNodeIds || [],
        conversationId: run.conversationId || conversationId,
      });
      setThreadRuns((current) => [data.run, ...current.filter((item) => item.id !== data.run.id)]);
      setRun(data.run);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [run]);

  const openAtMenu = useCallback(async () => {
    setAtOpen(true);
    setAtCategory('subject');
    setAtQuery('');
    setPickerOpen(false);
    setSettingsOpen(false);
    setModelMenu(false);
    if (!projectId) return;
    try {
      const data = await agentApi.project(projectId);
      const nodes = data.project.payload && Array.isArray(data.project.payload.nodes) ? data.project.payload.nodes : [];
      setAtNodes(nodes.filter((node) => node && ['image', 'video', 'audio', 'text'].includes(String(node.type || ''))));
    } catch {
      setAtNodes([]);
    }
  }, [projectId]);

  const insertAt = useCallback((node: Record<string, unknown>) => {
    const id = String(node.id || '');
    const title = String(node.title || id);
    const type = String(node.type || '');
    const meta = node.metadata && typeof node.metadata === 'object' ? node.metadata as Record<string, unknown> : {};
    const assetId = String(meta.assetId || '');
    const seriesId = String(meta.seriesId || '');
    const variantKey = String(meta.variantKey || '');
    if (id) {
      const refId = assetId || id;
      setReferences((current) => current.some((item) => (item.assetId || item.id) === refId)
        ? current
        : [...current, {id, nodeId: id, title, type, assetId, seriesId, variantKey}]);
    }
    setPrompt((current) => current + '@' + title + ' ');
    setAtOpen(false);
    window.requestAnimationFrame(() => { autoGrow(); promptRef.current?.focus(); });
  }, [autoGrow]);

  const nodeThumb = (node: Record<string, unknown>): string => {
    const meta = node.metadata && typeof node.metadata === 'object' ? node.metadata as Record<string, unknown> : {};
    const raw = String(meta.thumbUrl || meta.content || '');
    if (!raw) return '';
    if (raw.startsWith('data:')) return raw;
    if (raw.startsWith('http')) return raw;
    return window.location.origin + raw;
  };

  const classify = (node: Record<string, unknown>) => {
    const type = String(node.type || '');
    const title = String(node.title || '');
    const meta = node.metadata && typeof node.metadata === 'object' ? node.metadata as Record<string, unknown> : {};
    if (type === 'image') {
      if (/角色|人物|立绘|主角|主体/.test(title) || String(meta.assetKey || '').startsWith('chr_')) return 'subject';
      return 'image';
    }
    return type;
  };

  const atItems = atNodes.filter((node) => classify(node) === atCategory && (!atQuery || String(node.title || '').toLowerCase().includes(atQuery.toLowerCase())));
  const statusMessage = run ? [...run.messages].reverse().find((message) => message.role === 'status') : undefined;
  const currentStages = run ? (run.stages && run.stages.length ? run.stages : run.steps) : [];
  const activeStage = currentStages.find((stage) => stage.status === 'running')?.name || '';
  const streamLabel = activeStage === 'brief' ? '整理项目上下文' : activeStage === 'locks' ? '锁定创作参数' : activeStage === 'validate' ? '校验画布契约' : activeStage === 'deliver' ? '准备导入' : '生成投喂稿';
  const selectedModel = models.find((item) => item.id === modelId);
  const feedArtifact = run ? run.artifacts.filter((item) => item.kind === 'canvas_feed').slice(-1)[0] : null;
  const lockFields = skill && skill.locks && skill.locks.length ? skill.locks : FALLBACK_LOCKS;
  const feedPayload = feedArtifact ? feedArtifact.payload as Record<string, unknown> : null;
  const feedErrors = feedPayload && Array.isArray(feedPayload.errors) ? feedPayload.errors as Array<Record<string, unknown>> : [];
  const feedPreview = feedPayload && feedPayload.preview && typeof feedPayload.preview === 'object' ? feedPayload.preview as Record<string, unknown> : {};
  const displayContent = cleanRunText;
  const visibleMessages = (run ? run.messages || [] : [])
    .filter((message) => message.role !== 'status')
    .map((message) => ({...message, display: displayContent(message.content)}))
    .filter((message) => message.display);
  const processArtifacts = run ? run.artifacts.filter((item) => item.kind !== 'canvas_feed') : [];
  const friendlyRunError = (() => {
    if (!run) return '';
    const text = String(run.error || '');
    if (/10060|连接|连接尝试失败|timed out|timeout/i.test(text)) return '模型连接超时，锁题和附件已保留，可以直接重试。';
    if (feedErrors.length) {
      const messages = Array.from(new Set(feedErrors.map((item) => String(item.message || item.code || '')).filter(Boolean)));
      return '投喂稿还差 ' + messages.length + ' 处契约问题：' + messages.slice(0, 3).join('；');
    }
    return text || '这次生成没有完成。';
  })();
  const visibleSkills = skills.filter((item) => {
    const text = (item.name + item.id).toLowerCase();
    return !pickerQuery || text.includes(pickerQuery.toLowerCase());
  });
  const managerSkills = skills.filter((item) => {
    const text = (item.name + item.id).toLowerCase();
    return !managerQuery || text.includes(managerQuery.toLowerCase());
  });

  if (!enabled) return null;

  return (
    <>
      {!open && (
        <button type="button" className="agent-fab" onClick={() => { setClosing(false); setOpen(true); }} aria-label="与 AI 对话">
          <Sparkles size={16} strokeWidth={2} />
          <span>与 AI 对话</span>
        </button>
      )}
      {open && (
        <aside
          ref={panelRef}
          className={'agent-panel' + (resizing ? ' is-resizing' : '') + (closing ? ' is-closing' : '')}
          style={{width, right: 0}}
          aria-label="AI 创作面板"
        >
          <div className="agent-resize" onPointerDown={startResize} onPointerMove={moveResize} onPointerUp={endResize} onPointerCancel={endResize} />
          <header className="agent-head">
            <button type="button" className="agent-new" onClick={reset}>新会话</button>
            <button type="button" className="agent-history-link" onClick={() => void openHistory()}><History size={13} /><span>历史</span>{runs.length > 0 && <b>{runs.length}</b>}</button>
            <div className="agent-head-actions">
              <button type="button" className="agent-icon" title="历史运行" aria-label="历史运行" onClick={() => void openHistory()}><History size={15} /></button>
              <button type="button" className="agent-icon" title="技能库" aria-label="技能库" onClick={() => void openManager()}><Sparkles size={15} /></button>
              <button type="button" className="agent-icon" title="刷新" aria-label="刷新" onClick={() => { void refreshMetadata(); if (run) void refreshRun(run.id); }}><RefreshCw size={15} /></button>
              <button type="button" className="agent-icon" title={width > 600 ? '收窄' : '加宽'} aria-label={width > 600 ? '收窄' : '加宽'} onClick={() => setWidth((current) => current > 600 ? 480 : 720)}><Maximize2 size={15} /></button>
              <button type="button" className="agent-icon" title="关闭" aria-label="关闭" onClick={closePanel}><X size={16} /></button>
            </div>
          </header>

          <div className="agent-context">
            <span>{projectTitle || projectId || '未选择项目'}</span>
            {skill && <span className="agent-context-skill">@{skill.name}</span>}
          </div>

          <main className="agent-chat" ref={chatRef}>
            {!run && !pendingPrompt && (
              <div className="agent-welcome">
                <h2>探索更多专业创作模式</h2>
                <div className="agent-chips">
                  {['视频拆解', '创作分镜', '全流程广告片导演', '剧本开发', '剧情短片'].map((item) => (
                    <button key={item} type="button" className="agent-chip" onClick={() => {
                      setPrompt(item === '视频拆解' ? '拆解这条参考视频：从镜头、节奏、光影、声音四个维度分析，并给出可复用的创作公式。'
                        : item === '创作分镜' ? '把这个剧本拆成分镜：按段落给出景别、机位、构图、运动、节奏和转场。'
                        : item === '剧本开发' ? '开发一个短剧剧本：先给一句话故事、人物小传、三幕结构，再写第一集。'
                        : item === '剧情短片' ? '做一条 60 秒剧情短片：从冲突开场、反转、爽点到收尾，直接给可投喂的分镜。'
                        : '按全流程广告片导演的方式，从创意、脚本、分镜到投喂稿，做一条 30 秒广告片。');
                      window.requestAnimationFrame(() => { autoGrow(); promptRef.current?.focus(); });
                    }}>{item}</button>
                  ))}
                </div>
              </div>
            )}

            {pendingPrompt && (
              <div className="agent-lock-card">
                <div className="agent-lock-head"><strong>先锁题，再生成</strong><span>人工门禁</span></div>
                <div className="agent-lock-sub">这几项会写进本轮 run，模型不允许自行改。</div>
                <div className="agent-lock-grid">
                  {lockFields.map((field) => (
                    <label key={field.key} className="agent-lock-field">
                      <span>{field.label}</span>
                      <select value={locks[field.key] || field.default || field.options[0]} onChange={(event) => setLocks((current) => ({...current, [field.key]: event.target.value}))}>
                        {field.options.map((option) => <option key={option} value={option}>{option}</option>)}
                      </select>
                    </label>
                  ))}
                </div>
                <div className="agent-lock-actions">
                  <button type="button" className="agent-primary" onClick={() => void confirmRun()} disabled={busy}>确认锁题</button>
                  <button type="button" className="agent-ghost" onClick={() => setPendingPrompt('')}>取消</button>
                </div>
              </div>
            )}

            {threadRuns.filter((item) => item.id !== (run && run.id)).slice().reverse().map((item) => (
              <PastRun key={item.id} run={item} />
            ))}

            {run && (
              <div className="agent-run">
                {(run.status === 'queued' || run.status === 'running') && (
                  <AgentProgress label={statusMessage ? statusMessage.content : streamLabel} stage={activeStage} />
                )}
                {visibleMessages.map((message) => (
                  <div key={message.id} className={'agent-msg ' + (message.role === 'user' ? 'is-user' : 'is-assistant')}>
                    <span className="agent-msg-role">{message.role === 'user' ? '你' : 'Agent'}</span>
                    <div className="agent-msg-text"><AgentMessageContent blocks={message.blocks} fallback={message.display} /></div>
                  </div>
                ))}
                {run.status === 'failed' && (
                  <div className="agent-note is-error">
                    <span>{friendlyRunError}</span>
                    <div className="agent-note-actions">
                      <button type="button" onClick={() => void retryCurrentRun(false)}><RotateCcw size={12} /> 重试</button>
                      {feedErrors.length > 0 && <button type="button" onClick={() => void retryCurrentRun(true)}><Wand2 size={12} /> 让 Agent 补</button>}
                    </div>
                  </div>
                )}
                {(currentStages.length > 0 || processArtifacts.length > 0) && (
                  <details className="agent-run-trace" open={traceOpen} onToggle={(event) => setTraceOpen(event.currentTarget.open)}>
                    <summary>
                      <span className={'agent-stage-dot ' + (run.status === 'done' ? 'done' : run.status === 'failed' ? 'failed' : 'running')} />
                      <span>{run.status === 'done' ? '已完成' : run.status === 'failed' ? '已中断' : '进行中'}</span>
                      <small>{currentStages.map((stage) => stage.name).join(' · ')}</small>
                      <ChevronRight size={12} className="agent-chev" />
                    </summary>
                    <div className="agent-run-trace-body">
                      {currentStages.length > 0 && (
                        <div className="agent-stage-line">
                          {currentStages.map((stage) => <span key={String(stage.id || stage.name)} className={'agent-stage-name ' + stage.status}>{stage.name}</span>)}
                        </div>
                      )}
                      {run.toolCalls && run.toolCalls.length > 0 && <ToolTrace calls={run.toolCalls} />}
                      {processArtifacts.length > 0 && (
                        <JsonPreview value={processArtifacts.reduce((acc, item) => ({...acc, [item.kind]: item.payload}), {} as Record<string, unknown>)} label="查看过程产物" />
                      )}
                    </div>
                  </details>
                )}
                {run.status !== 'failed' && (
                  <div className="agent-run-actions">
                    {(run.status === 'queued' || run.status === 'running') && (
                      <button type="button" className="agent-ghost" onClick={() => void cancelCurrentRun()}><Square size={12} /> 停止</button>
                    )}
                  </div>
                )}
                {feedArtifact && !feedErrors.length && (
                  <div className="agent-result">
                    <div className="agent-result-title">
                      <strong>{run.status === 'done' ? '已导入画布' : '投喂稿已就绪'}</strong>
                      <span>revision {String(feedPreview.revision || '?')}</span>
                    </div>
                    {Boolean(feedPayload && feedPayload.autoHealed) && <div className="agent-result-note">系统自动补全了缺失音色，建议导入前人工确认。</div>}
                    <div className="agent-result-actions">
                      {run.status === 'waiting_user' && <button type="button" className="agent-primary" onClick={() => void importRun()} disabled={busy}>确认导入画布</button>}
                      <button type="button" className="agent-ghost" onClick={() => {
                        const blob = new Blob([JSON.stringify(feedPayload && feedPayload.feed || {}, null, 2)], {type: 'application/json'});
                        const url = URL.createObjectURL(blob);
                        const link = document.createElement('a');
                        link.href = url; link.download = 'canvas-feed.json'; link.click(); URL.revokeObjectURL(url);
                      }}><Download size={12} /> 下载 JSON</button>
                    </div>
                    <div className="agent-followups">
                      <button type="button" onClick={() => { setPrompt('基于刚才的投喂稿，再改一版：'); window.requestAnimationFrame(() => { autoGrow(); promptRef.current?.focus(); }); }}>再改一版</button>
                      <button type="button" onClick={() => { setPrompt('保持故事不变，调整运镜与镜头节奏：'); window.requestAnimationFrame(() => { autoGrow(); promptRef.current?.focus(); }); }}>调整运镜</button>
                      <button type="button" onClick={() => void retryCurrentRun(false)}>重新生成</button>
                    </div>
                  </div>
                )}
              </div>
            )}
          </main>

          {error && (!run || run.status !== 'failed') && <div className="agent-error agent-composer-error">{error}</div>}
          <AgentComposer
            value={prompt}
            disabled={!conversationReady}
            running={Boolean(run && (run.status === 'queued' || run.status === 'running'))}
            onChange={(value) => {
              if (value.endsWith('/')) {
                openPicker();
                setPrompt(value.slice(0, -1));
                return;
              }
              setPrompt(value);
            }}
            onSubmit={requestRun}
            onStop={() => void cancelCurrentRun()}
            onUploadFiles={(files) => files.forEach((file) => void uploadAttachment(file))}
            chips={(skill || attachments.length > 0 || references.length > 0) ? (
              <div className="agent-chips-row">
                {skill && <span className="agent-skill-chip"><Sparkles size={11} />{skill.name}{routeId && skill.routes ? ' · ' + (skill.routes.find((route) => route.id === routeId)?.name || routeId) : ''}<button type="button" onClick={() => { setSkill(null); setRouteId(''); }}><X size={11} /></button></span>}
                {references.map((item) => <span key={item.assetId || item.id} className="agent-file-chip"><AtSign size={11} />{item.title}<button type="button" onClick={() => setReferences((current) => current.filter((ref) => (ref.assetId || ref.id) !== (item.assetId || item.id)))}><X size={11} /></button></span>)}
                {attachments.map((file) => <span key={file.id} className="agent-file-chip"><FileText size={11} />{file.name}<button type="button" onClick={() => setAttachments((current) => current.filter((item) => item.id !== file.id))}><X size={11} /></button></span>)}
              </div>
            ) : null}
            toolbar={(
              <>
                <button type="button" className="agent-tool" title="上传剧本/参考文件" aria-label="上传剧本/参考文件" onClick={() => fileInputRef.current?.click()}><Plus size={15} /></button>
                <button type="button" className="agent-tool" title="选择技能" aria-label="选择技能" data-agent-trigger="skill" onClick={() => { pickerOpen ? setPickerOpen(false) : openPicker(); }}><Sparkles size={15} /></button>
                <button type="button" className="agent-tool" title="创作设置" aria-label="创作设置" data-agent-trigger="settings" onClick={() => { setSettingsOpen((current) => !current); setModelMenu(false); setPickerOpen(false); setAtOpen(false); }}><SlidersHorizontal size={15} /></button>
                <button type="button" className="agent-tool" title="引用素材" aria-label="引用素材" data-agent-trigger="at" onClick={() => { atOpen ? setAtOpen(false) : void openAtMenu(); }}><AtSign size={15} /></button>
                <button type="button" className="agent-model" aria-label="选择模型" data-agent-trigger="model" onClick={() => { setModelMenu((current) => !current); setPickerOpen(false); setSettingsOpen(false); setAtOpen(false); }}>
                  {selectedModel ? (MODEL_LABELS[selectedModel.id] || selectedModel.label) : '模型'} <ChevronRight size={12} />
                </button>
              </>
            )}
          />

          <input ref={fileInputRef} type="file" hidden accept=".txt,.md,.markdown,.docx,.doc,.json,.csv,.log" onChange={(event) => { const file = event.target.files && event.target.files[0]; if (file) void uploadAttachment(file); event.target.value = ''; }} />
          <input ref={skillFileRef} type="file" hidden accept=".zip" onChange={(event) => { const file = event.target.files && event.target.files[0]; if (file) void uploadSkill(file); event.target.value = ''; }} />

          {pickerOpen && (
            <div className="agent-popover agent-skill-picker">
              <div className="agent-popover-head"><Search size={13} /><input autoFocus value={pickerQuery} onChange={(event) => setPickerQuery(event.target.value)} placeholder="搜索技能" /></div>
              <div className="agent-picker-list">
                {visibleSkills.map((item) => (
                  <button key={item.id} type="button" className={'agent-picker-item' + (skill && skill.id === item.id ? ' is-selected' : '')} onClick={() => void selectSkill(item)}>
                    <Sparkles size={13} /><span><strong>{item.name}</strong><small>{item.activeVersion || ''} · {(item.routes || []).length} 个分支</small></span>
                    {skill && skill.id === item.id && <Check size={13} />}
                  </button>
                ))}
                {visibleSkills.length === 0 && <div className="agent-hint">没有匹配技能</div>}
              </div>
              <div className="agent-picker-actions">
                <button type="button" onClick={() => skillFileRef.current?.click()}><Plus size={13} /> 新建技能</button>
                <button type="button" onClick={() => void openManager()}><Settings size={13} /> 管理技能</button>
              </div>
            </div>
          )}

          {modelMenu && (
            <div className="agent-popover agent-model-menu">
              {models.map((item) => (
                <button key={item.id} type="button" className={item.id === modelId ? 'is-selected' : ''} onClick={() => { setModelId(item.id); setModelMenu(false); }}>
                  <span>{MODEL_LABELS[item.id] || item.label}{item.providerName ? ' · ' + item.providerName : ''}</span>{item.id === modelId && <Check size={13} />}
                </button>
              ))}
            </div>
          )}

          {settingsOpen && (
            <div className="agent-popover agent-settings">
              <div className="agent-popover-title">创作设置 <button type="button" onClick={() => setSettingsOpen(false)}><X size={13} /></button></div>
              <label>技能分支
                <select value={routeId} onChange={(event) => { setRouteId(event.target.value); setPendingPrompt(''); }} disabled={!skill}>
                  <option value="">总控</option>
                  {(skill && skill.routes || []).map((route) => <option key={route.id} value={route.id}>{route.name}</option>)}
                </select>
              </label>
              <button type="button" className="agent-ghost" onClick={() => skillFileRef.current?.click()}><Upload size={13} /> 上传技能包</button>
            </div>
          )}

          {atOpen && (
            <div className="agent-popover agent-at-menu">
              <div className="agent-popover-head"><Search size={13} /><input autoFocus value={atQuery} onChange={(event) => setAtQuery(event.target.value)} placeholder="搜索当前画布素材" /></div>
              <div className="agent-at-body">
                <div className="agent-at-cats">
                  {([['subject', '主体', UserIcon], ['image', '图片', ImageIcon], ['video', '视频', Video], ['audio', '音频', Music2], ['text', '文本', Type]] as Array<[string, string, typeof Type]>).map(([key, label, Icon]) => (
                    <button key={key} type="button" className={atCategory === key ? 'is-active' : ''} onClick={() => setAtCategory(key)}>
                      <Icon size={13} />{label}<span>{atNodes.filter((node) => classify(node) === key).length}</span>
                    </button>
                  ))}
                </div>
                <div className="agent-at-items">
                  {atItems.slice(0, 80).map((node, index) => {
                    const thumb = nodeThumb(node);
                    return (
                      <button key={String(node.id || index)} type="button" onClick={() => insertAt(node)}>
                        {thumb ? <img className="agent-at-thumb" src={thumb} alt="" loading="lazy" /> : <span className="agent-at-thumb agent-at-thumb-empty"><FileText size={13} /></span>}
                        <span>{String(node.title || node.id || '')}</span>
                        <small>{String(node.type || '')} · {String(node.id || '')}</small>
                      </button>
                    );
                  })}
                  {atItems.length === 0 && <div className="agent-hint">暂无相关节点</div>}
                </div>
              </div>
            </div>
          )}

          {historyOpen && (
            <div className="agent-modal-backdrop" onPointerDown={(event) => { if (event.target === event.currentTarget) setHistoryOpen(false); }}>
              <div className="agent-modal agent-history-modal" role="dialog" aria-modal="true" aria-label="历史运行">
                <div className="agent-history-head"><strong>历史运行</strong><button type="button" onClick={() => setHistoryOpen(false)}><X size={15} /></button></div>
                <div className="agent-history-list">
                  {runs.length === 0 && <div className="agent-hint">这个项目还没有运行记录</div>}
                  {runs.map((item) => (
                    <button key={item.id} type="button" className="agent-history-row" onClick={() => { setHistoryOpen(false); void agentApi.run(item.id).then((data) => setRun(data.run)).catch(() => {}); }}>
                      <span className={'agent-badge ' + (item.status === 'done' ? 'ok' : item.status === 'failed' ? 'bad' : '')}>{item.status}</span>
                      <span className="agent-history-prompt">{displayContent(item.prompt) || item.prompt.slice(0, 120)}</span>
                      <small>{item.skillId} · {item.createdAt || ''}</small>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {managerOpen && (
            <div className="agent-modal-backdrop" onPointerDown={(event) => { if (event.target === event.currentTarget) setManagerOpen(false); }}>
              <div className="agent-modal" role="dialog" aria-modal="true" aria-label="技能管理">
                <aside className="agent-modal-side">
                  <div className="agent-modal-head"><strong>技能库</strong><button type="button" onClick={() => setManagerOpen(false)}><X size={15} /></button></div>
                  <div className="agent-popover-head"><Search size={13} /><input value={managerQuery} onChange={(event) => setManagerQuery(event.target.value)} placeholder="搜索技能" /></div>
                  <button type="button" className="agent-modal-new" onClick={() => skillFileRef.current?.click()}><Plus size={13} /> 新建技能</button>
                  <div className="agent-modal-list">
                    {managerSkills.map((item) => (
                      <button key={item.id} type="button" className={managerSkill && managerSkill.id === item.id ? 'is-active' : ''} onClick={() => void selectManagerSkill(item)}>
                        <Sparkles size={13} /><span><strong>{item.name}</strong><small>{item.activeVersion || ''} · {(item.routes || []).length} 个分支</small></span>
                      </button>
                    ))}
                  </div>
                </aside>
                <section className="agent-modal-detail">
                  <div className="agent-modal-detail-head">
                    <div><h3>{managerSkill ? managerSkill.name : '选择一个技能'}</h3><p>{managerSkill ? managerSkill.id + ' · ' + (managerSkill.activeVersion || '') : ''}</p></div>
                    <button type="button" className="agent-primary" disabled={!managerSkill} onClick={() => { if (managerSkill) void selectSkill(managerSkill); }}>使用</button>
                  </div>
                  <div className="agent-modal-label">技能内容</div>
                  <pre className="agent-modal-entry">{managerDetail ? managerDetail.entry || '内容未公开' : '选择左侧技能查看内容'}</pre>
                  {managerDetail && managerDetail.versions && managerDetail.versions.length > 0 && (
                    <div className="agent-versions">
                      <div className="agent-modal-label">版本 / 权限</div>
                      {managerDetail.versions.map((version) => (
                        <div key={version.version} className="agent-version-row">
                          <span>{version.version}</span>
                          {version.active && <span className="agent-badge ok">active</span>}
                          <span className="agent-hint">{Object.keys(version.permissions || {}).join(' / ') || 'prompt-only'}</span>
                          {!version.active && managerSkill && (
                            <button
                              type="button"
                              className="agent-ghost"
                              onClick={async () => {
                                setBusy(true);
                                try {
                                  await agentApi.activate(managerSkill.id, version.version);
                                  await selectManagerSkill(managerSkill);
                                  await refreshMetadata();
                                } catch (err) {
                                  setError(err instanceof Error ? err.message : String(err));
                                } finally {
                                  setBusy(false);
                                }
                              }}
                              disabled={busy}
                            >启用</button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              </div>
            </div>
          )}
        </aside>
      )}
    </>
  );
}

function PastRun({run}: {run: AgentRun}) {
  const stages = run.stages && run.stages.length ? run.stages : run.steps || [];
  const messages = (run.messages || [])
    .filter((message) => message.role !== 'status')
    .map((message) => ({...message, display: cleanRunText(message.content)}))
    .filter((message) => message.display);
  const artifacts = (run.artifacts || []).filter((item) => item.kind !== 'canvas_feed');
  const prompt = cleanRunText(run.prompt) || run.prompt.slice(0, 160);
  if (run.status === 'failed' || run.status === 'cancelled') {
    return (
      <div className="agent-past agent-past-failed">
        <details className="agent-run-trace">
          <summary>
            <span className={'agent-stage-dot ' + (run.status === 'failed' ? 'failed' : '')} />
            <span>{run.status === 'failed' ? '已中断' : '已停止'}</span>
            <small>{prompt}</small>
            <ChevronRight size={12} className="agent-chev" />
          </summary>
          <div className="agent-run-trace-body">
            {prompt && <div className="agent-past-prompt">{prompt}</div>}
            {stages.length > 0 && <div className="agent-stage-line">{stages.map((stage) => <span key={String(stage.id || stage.name)} className={'agent-stage-name ' + stage.status}>{stage.name}</span>)}</div>}
            {run.toolCalls && run.toolCalls.length > 0 && <ToolTrace calls={run.toolCalls} />}
            {artifacts.length > 0 && <JsonPreview value={artifacts.reduce((acc, item) => ({...acc, [item.kind]: item.payload}), {} as Record<string, unknown>)} label="查看过程产物" />}
          </div>
        </details>
      </div>
    );
  }
  return (
    <div className="agent-past">
      {prompt && <div className="agent-msg is-user"><span className="agent-msg-role">你</span><div className="agent-msg-text"><AgentMessageContent fallback={prompt} /></div></div>}
      {messages.filter((message) => message.role !== 'user').map((message) => (
        <div key={message.id} className={'agent-msg ' + (message.role === 'user' ? 'is-user' : 'is-assistant')}>
          <span className="agent-msg-role">{message.role === 'user' ? '你' : 'Agent'}</span>
          <div className="agent-msg-text"><AgentMessageContent blocks={message.blocks} fallback={message.display} /></div>
        </div>
      ))}
      <details className="agent-run-trace">
        <summary>
          <span className={'agent-stage-dot ' + (run.status === 'done' ? 'done' : run.status === 'failed' ? 'failed' : 'running')} />
          <span>{run.status === 'done' ? '已完成' : run.status === 'failed' ? '已中断' : run.status === 'waiting_user' ? '待确认' : '进行中'}</span>
          <small>{stages.map((stage) => stage.name).join(' · ')}</small>
          <ChevronRight size={12} className="agent-chev" />
        </summary>
        <div className="agent-run-trace-body">
          {stages.length > 0 && <div className="agent-stage-line">{stages.map((stage) => <span key={String(stage.id || stage.name)} className={'agent-stage-name ' + stage.status}>{stage.name}</span>)}</div>}
          {run.toolCalls && run.toolCalls.length > 0 && <ToolTrace calls={run.toolCalls} />}
          {artifacts.length > 0 && <JsonPreview value={artifacts.reduce((acc, item) => ({...acc, [item.kind]: item.payload}), {} as Record<string, unknown>)} label="查看过程产物" />}
        </div>
      </details>
      {run.status === 'failed' && <div className="agent-past-fail">这一轮没有跑通</div>}
    </div>
  );
}

function AgentProgress({label, stage}: {label: string; stage: string}) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="agent-progress">
      <div className="agent-progress-top">
        <span className="agent-progress-dot" />
        <strong>Agent 正在工作</strong>
        <span className="agent-progress-time">{seconds}s</span>
      </div>
      <div className="agent-progress-stage">{stage || 'prepare'}</div>
      <div className="agent-progress-label">{label}</div>
      <div className="agent-progress-bar"><i /></div>
    </div>
  );
}

function StreamingPlaceholder({label}: {label: string}) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="agent-streaming">
      <span className="agent-stream-bars"><i /><i /><i /></span>
      <span>{label}…</span>
      <small>{seconds}s · 模型可能正在缓冲</small>
    </div>
  );
}

function ToolTrace({calls}: {calls: NonNullable<AgentRun['toolCalls']>}) {
  return (
    <details className="agent-tool-trace">
      <summary>
        <ChevronRight size={12} />
        <span>工具调用</span>
        <small>{calls.length} 次</small>
      </summary>
      <div className="agent-tool-trace-list">
        {calls.map((call) => (
          <div key={call.id} className="agent-tool-trace-row">
            <span className={'agent-stage-dot ' + (call.status === 'done' ? 'done' : call.status === 'failed' ? 'failed' : 'running')} />
            <strong>{call.name}</strong>
            <span>{call.status}</span>
            {typeof call.resultBytes === 'number' && <small>{call.resultBytes} B</small>}
          </div>
        ))}
      </div>
    </details>
  );
}

function JsonPreview({value, label}: {value: unknown; label: string}) {
  const [text, setText] = useState('');
  const download = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    const blob = new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'canvas-feed.json';
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <details className="agent-stage-artifact" onToggle={(event) => {
      if (event.currentTarget.open && !text) {
        const rendered = JSON.stringify(value, null, 2);
        setText(rendered.length > 60000 ? rendered.slice(0, 60000) + '\n\n… 已截断，下载查看完整 JSON' : rendered);
      }
    }}>
      <summary>
        <span>{label}</span>
        <button type="button" className="agent-json-download" onClick={download}><Download size={12} /> 下载</button>
      </summary>
      {text && <pre>{text}</pre>}
    </details>
  );
}

function UserIcon(props: {size?: number}) {
  return <svg width={props.size || 13} height={props.size || 13} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></svg>;
}

export {AgentDock};
