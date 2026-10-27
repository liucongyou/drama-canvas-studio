import type {AgentEvent, Artifact, ChatState, Message, WorkspaceUpload, WorkflowPlan} from './types';

export function createChatState(messages: Message[] = [], workflowPlan?: WorkflowPlan): ChatState {
  return {messages, busy: false, turnId: '', promptTokens: 0, statusText: '', workflowPlan};
}

export function mergeHistoryMessages(existing: Message[], incoming: Message[]): Message[] {
  const merged: Message[] = [];
  const indexes = new Map<string, number>();
  for (const message of [...existing, ...incoming]) {
    const index = indexes.get(message.id);
    if (index === undefined) {
      indexes.set(message.id, merged.length);
      merged.push(message);
    } else {
      merged[index] = message;
    }
  }
  return merged;
}

/**
 * 按写入时间把磁盘产物挂回产生它的那条活动行。
 *
 * 历史做法按工具名过滤（只认名字含 render 的工具）：单个重新生成的分段视频走
 * vimax_regenerate、资产图走 vimax_generate_assets，名字都不含 render，产物永远挂不上，
 * 刷新后同样看不到。这里改为纯时间归属，工具名不参与判断；已经挂在别的消息上的产物不再
 * 重复挂（后端 history 恢复已经放好位置，前端只补实时新增的那部分）。
 */
export function attachMediaByCreatedTime(messages: Message[], media: Artifact[]): Message[] {
  if (media.length === 0) return messages;
  const attached = new Set<string>();
  const next = messages.map((message) => {
    const artifacts = message.artifacts ? [...message.artifacts] : [];
    for (const artifact of artifacts) attached.add(artifact.path);
    return {...message, artifacts};
  });
  const activities = next.filter((message) => message.role === 'activity');
  if (activities.length === 0) return messages;
  const sorted = [...media].sort((left, right) => parseArtifactTime(left) - parseArtifactTime(right));
  for (const artifact of sorted) {
    if (attached.has(artifact.path)) continue;
    const created = parseArtifactTime(artifact);
    if (Number.isNaN(created)) continue;
    // 归属：时间上不晚于产物的最后一条活动行；一条都不满足时退回最早的活动行，
    // 避免"刚生成、时间戳又早于当前活动行"的产物被静默丢掉。
    let target = activities[0];
    for (const message of activities) {
      const messageTime = message.createdAt ? Date.parse(message.createdAt) : Number.NaN;
      if (!Number.isNaN(messageTime) && messageTime <= created) target = message;
    }
    target.artifacts = [...(target.artifacts || []), artifact];
    attached.add(artifact.path);
  }
  return next;
}

function parseArtifactTime(artifact: Artifact): number {
  const raw = String(artifact.updatedAt || '').trim();
  if (!raw) return Number.NaN;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? Number.NaN : parsed;
}

export function appendLocalUser(state: ChatState, text: string): ChatState {
  return {
    ...state,
    busy: true,
    statusText: '正在连接智能体…',
    messages: [...state.messages, {id: `local-user-${Date.now()}`, role: 'user', text, createdAt: new Date().toISOString()}],
  };
}

export function composeAgentPrompt(text: string, workspaceUploads: string[] = []) {
  const message = text.replace(/\s+/g, ' ').trim();
  const paths = workspaceUploads.map((path) => path.trim()).filter(Boolean);
  if (paths.length === 0) return message;
  return `${message} <workspace_uploads>${JSON.stringify(paths)}</workspace_uploads>`;
}

/**
 * 这个失败的步骤还值得"原样重试"吗？
 *
 * 实测（2026-10-16）：agent 想去读仓库根的 `configs/`，被判 `Path not found`；用户在界面上连点
 * 两轮「重试步骤」都在撞同一面墙——参数没变、环境没变，重试必然同一结果，只是白等一轮。
 * 这里只识别**确定性失败**（路径不存在 / 越界 / 附件不在本项目）；网络、限流、供应商抖动
 * 一律照旧可重试。
 */
export function isRetryableToolFailure(text: string): boolean {
  const value = String(text || '');
  if (!value) return true;
  const fatal = [
    /Path not found/i,
    /File not found/i,
    /escapes the active project workspace/i,
    /Path is not a file/i,
    /不在当前项目里/,
  ];
  return !fatal.some((pattern) => pattern.test(value));
}

/**
 * 待发送的上传件按目标项目分流。
 *
 * 病根（实测事故）：新建项目后立刻上传剧本，文件落进了**上一个项目**的 uploads/，而消息发到
 * 新项目，`<workspace_uploads>` 里的相对路径在新项目里不存在 → agent 只能报 File not found，
 * 白烧一轮对话。上传件的路径是"相对某个会话工作区"的，跨项目引用必然失效，所以发送前必须按
 * 会话分流：不在目标项目里的直接剔除，不要交给 agent 去猜。
 *
 * 没有 `sessionId` 的历史数据（旧版本上传、页面刷新前遗留）按"属于当前项目"处理，保持旧行为。
 */
export function partitionUploadsForSession(
  uploads: WorkspaceUpload[],
  sessionId: string,
): {forSession: WorkspaceUpload[]; foreign: WorkspaceUpload[]} {
  const forSession: WorkspaceUpload[] = [];
  const foreign: WorkspaceUpload[] = [];
  uploads.forEach((upload) => {
    if (!upload.sessionId || upload.sessionId === sessionId) forSession.push(upload);
    else foreign.push(upload);
  });
  return {forSession, foreign};
}

export function applyAgentEvent(state: ChatState, event: AgentEvent): ChatState {
  const turnId = event.turn_id || state.turnId || `turn-${Date.now()}`;
  switch (event.type) {
    case 'turn':
      return {
        ...state,
        busy: true,
        turnId,
        statusText: '正在分析任务并规划下一步…',
        messages: promotePendingUser(state.messages, turnId),
      };
    case 'workflow_plan':
      return {...state, workflowPlan: event.workflow_plan || state.workflowPlan};
    case 'status': {
      const cancelled = /cancel/i.test(`${event.phase || ''} ${event.message || ''}`);
      const nextState = cancelled ? freezeStreamingSegment(state, turnId) : state;
      return {...nextState, statusText: agentStatusText(event.message, event.phase)};
    }
    case 'prompt_trace': {
      const tokens = event.prompt_trace?.totals?.total_tokens
        ?? event.prompt_trace?.totals?.total_estimated_tokens
        ?? event.prompt_trace?.total_estimated_tokens
        ?? state.promptTokens;
      return {...state, promptTokens: Math.max(0, Math.round(tokens))};
    }
    case 'token':
      return appendAssistantDelta(state, turnId, event.delta || '');
    case 'tool_start': {
      const name = event.tool?.name || event.tool?.requested_name || 'tool';
      return upsertActivity({...freezeStreamingSegment(state, turnId), statusText: '正在执行工具并读取结果…'}, {
        id: activityId(turnId, event.tool?.id, name),
        role: 'activity',
        tool: name,
        toolCallId: event.tool?.id,
        toolArguments: event.tool?.arguments,
        status: 'running',
        stage: 'starting',
        text: toolStartText(name),
        turnId,
      });
    }
    case 'tool_progress': {
      const name = event.tool?.name || event.tool?.requested_name || 'tool';
      const nextState = freezeStreamingSegment(state, turnId);
      const meta = event.progress?.metadata || {};
      // 后端优先提供相对会话根的可访问 URL（media_url），跨平台更稳；
      // 兜底用绝对路径 path（旧版后端）。
      const mediaUrl = typeof meta.media_url === 'string' ? meta.media_url : '';
      const rawPath = typeof meta.path === 'string' ? meta.path : '';
      // 进度事件携带图片路径（候选关键帧/资产图）时，直接挂到活动行即时显示
      let artifacts: Artifact[] | undefined;
      if (mediaUrl || (rawPath && /\.(png|jpe?g|webp|gif)$/i.test(rawPath))) {
        const sessionId = String(meta.session_id || '');
        const displayPath = rawPath || mediaUrl;
        artifacts = [{
          path: displayPath,
          name: displayPath.split('/').pop() || displayPath,
          kind: 'image',
          size: 0,
          updatedAt: new Date().toISOString(),
          url: mediaUrl || `/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(rawPath)}`,
        }];
      }
      const preferredId = event.tool?.id ? activityId(turnId, event.tool.id, name) : '';
      return upsertLatestToolActivity({
        ...nextState,
        statusText: agentStatusText(event.progress?.message, event.progress?.stage),
      }, name, {
        role: 'activity',
        tool: name,
        toolCallId: event.tool?.id,
        toolArguments: event.tool?.arguments,
        status: 'running',
        stage: event.progress?.stage || 'running',
        text: event.progress?.message || humanizeStage(event.progress?.stage || 'running'),
        turnId,
        ...(artifacts ? {artifacts} : {}),
      }, preferredId);
    }
    case 'tool_result': {
      const name = event.tool_result?.name || 'tool';
      const ok = event.tool_result?.ok !== false;
      const progressId = String(event.tool_result?.metadata?.call_id || '');
      const preferredId = progressId ? activityId(turnId, progressId, name) : '';
      return upsertLatestToolActivity({
        ...state,
        statusText: ok ? '工具已完成，正在继续分析…' : '工具执行失败，正在整理错误…',
      }, name, {
        role: 'activity',
        tool: name,
        status: ok ? 'done' : 'error',
        // 注意：不要在这里覆盖 stage。工具结束并不等于"合成成片完成"；
        // 步骤条靠 stage 定位当前步骤，通用完成态（completed）会被误映射到最后一个步骤。
        text: ok ? '已完成' : cleanError(event.tool_result?.content || '工具执行失败'),
        turnId,
      }, preferredId);
    }
    case 'session': {
      const plan = event.session?.session?.workflow_plan;
      return plan ? {...state, workflowPlan: plan} : state;
    }
    case 'autonomy': {
      // 端到端自主推进里程碑：让用户实时看到 agent 自主跨阶段推进 / 在人工闸门暂停。
      const message = (event.autonomy?.message || '').trim();
      if (!message) return state;
      const icon = event.autonomy?.action === 'pause' ? '⏸' : event.autonomy?.action === 'capped' ? '⚠' : '🤖';
      return {
        ...freezeStreamingSegment(state, turnId),
        statusText: message,
        messages: [...state.messages, {
          id: `autonomy-${turnId}-${Date.now()}`,
          role: 'activity',
          tool: 'autonomy',
          status: 'done',
          text: `${icon} ${message}`,
          turnId,
          createdAt: new Date().toISOString(),
        }],
      };
    }
    case 'terminal':
      if (event.stream !== 'stderr') return state;
      return {
        ...state,
        messages: [...state.messages, {
          id: `terminal-${Date.now()}`,
          role: 'activity',
          status: 'error',
          tool: 'runtime',
          text: cleanError(event.line || '运行错误'),
          createdAt: new Date().toISOString(),
        }],
      };
    case 'error': {
      const nextState = freezeStreamingSegment(state, turnId);
      return {
        ...nextState,
        busy: false,
        statusText: '',
        messages: [...nextState.messages, {id: `error-${turnId}-${Date.now()}`, role: 'error', text: event.message || '未知错误', turnId, createdAt: new Date().toISOString()}],
      };
    }
    case 'done': {
      const finished = finishAssistantTurn(state, turnId, event.assistant || '');
      if (!event.status || event.status === 'completed') return finished;
      return {
        ...finished,
        messages: finished.messages.map((message) => message.role === 'activity' && message.status === 'running'
          ? {...message, status: 'error', stage: 'interrupted', text: '任务已停止，此步骤未完成'}
          : message),
      };
    }
    case 'bridge_status':
      if (event.status === 'stopping') {
        return {...state, statusText: '正在优雅停止任务并保存进度…'};
      }
      if (event.status !== 'error' && event.status !== 'stopped') return state;
      {
        const nextState = finishStreamingSegments(state);
        return {
          ...nextState,
          busy: false,
          statusText: '',
          messages: nextState.messages.map((message) => message.role === 'activity' && message.status === 'running'
          ? {...message, status: 'error', stage: 'interrupted', text: event.message || '生成已停止'}
          : message),
        };
      }
    default:
      return state;
  }
}

function appendAssistantDelta(state: ChatState, turnId: string, delta: string): ChatState {
  if (!delta) return state;
  const id = `streaming-${turnId}`;
  const index = state.messages.findIndex((message) => message.id === id);
  if (index < 0) {
    return {
      ...state,
      statusText: agentStatusText('', 'sampling_assistant'),
      messages: [...state.messages, {id, role: 'assistant', text: delta, turnId, kind: 'answer', streaming: true, createdAt: new Date().toISOString()}],
    };
  }
  const messages = [...state.messages];
  messages[index] = {...messages[index], text: `${messages[index].text}${delta}`};
  return {...state, statusText: agentStatusText('', 'sampling_assistant'), messages};
}

function promotePendingUser(messages: Message[], turnId: string): Message[] {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role !== 'user' || !messages[index].id.startsWith('local-user-')) continue;
    const next = [...messages];
    next[index] = {...next[index], id: `${turnId}-user`, turnId};
    return next;
  }
  return messages;
}

function freezeStreamingSegment(state: ChatState, turnId: string): ChatState {
  const index = state.messages.findIndex((message) => Boolean(message.streaming) && message.turnId === turnId);
  if (index < 0) return state;
  const messages = [...state.messages];
  messages[index] = {
    ...messages[index],
    id: `${turnId}-commentary-${index}`,
    kind: 'commentary',
    streaming: false,
  };
  return {...state, messages};
}

function finishStreamingSegments(state: ChatState): ChatState {
  return {
    ...state,
    messages: state.messages.map((message) => message.streaming
      ? {...message, kind: 'commentary', streaming: false}
      : message),
  };
}

function finishAssistantTurn(state: ChatState, turnId: string, authoritativeText: string): ChatState {
  const finalId = `${turnId}-assistant`;
  const existingError = state.messages.find((message) => message.role === 'error' && message.turnId === turnId);
  if (existingError && (!authoritativeText || existingError.text.trim() === authoritativeText.trim())) {
    return {...state, busy: false, statusText: ''};
  }
  const streamingIndex = state.messages.findIndex((message) => Boolean(message.streaming) && message.turnId === turnId);
  const streamingText = streamingIndex >= 0 ? state.messages[streamingIndex].text : '';
  const finalText = authoritativeText || streamingText;
  if (streamingIndex >= 0) {
    const finalMessage: Message = {
      ...state.messages[streamingIndex],
      id: finalId,
      role: 'assistant',
      text: finalText || state.messages[streamingIndex].text,
      turnId,
      kind: 'answer',
      streaming: false,
    };
    return {
      ...state,
      busy: false,
      statusText: '',
      messages: [...state.messages.filter((message, index) => index !== streamingIndex && message.id !== finalId), finalMessage],
    };
  }
  const existingIndex = state.messages.findIndex((message) => message.id === finalId);
  if (existingIndex >= 0) {
    const messages = [...state.messages];
    messages[existingIndex] = {...messages[existingIndex], text: finalText || messages[existingIndex].text, streaming: false, kind: 'answer'};
    return {...state, busy: false, statusText: '', messages};
  }
  if (!finalText) return {...state, busy: false, statusText: ''};
  return {
    ...state,
    busy: false,
    statusText: '',
    messages: [...state.messages, {id: finalId, role: 'assistant', text: finalText, turnId, kind: 'answer', createdAt: new Date().toISOString()}],
  };
}

const STATUS_LABELS: Array<[RegExp, string]> = [
  [/compact/i, '正在压缩上下文，为后续步骤腾出空间…'],
  [/sampling_assistant|sampling/i, '正在生成回复…'],
  [/executing_tools|tool/i, '正在执行工具并读取结果…'],
  [/retrying_tool|retry/i, '正在重试失败的步骤…'],
  [/cancelled|cancel/i, '正在停止并保存中断记录…'],
];

function agentStatusText(message?: string, phase?: string): string {
  const source = `${phase || ''} ${message || ''}`.trim();
  for (const [pattern, label] of STATUS_LABELS) {
    if (pattern.test(source)) return label;
  }
  const clean = (message || '').trim();
  if (clean && !/^[a-z0-9_\-\s]+$/i.test(clean)) return clean;
  return '正在处理…';
}

function upsertActivity(state: ChatState, message: Message): ChatState {
  const index = state.messages.findIndex((item) => item.id === message.id);
  if (index < 0) return {...state, messages: [...state.messages, {...message, createdAt: message.createdAt || new Date().toISOString()}]};
  const messages = [...state.messages];
  messages[index] = {...messages[index], ...message};
  return {...state, messages};
}

function upsertLatestToolActivity(state: ChatState, tool: string, update: Omit<Message, 'id'>, preferredId = ''): ChatState {
  const messages = [...state.messages];
  if (preferredId) {
    const preferredIndex = messages.findIndex((message) => message.id === preferredId && message.role === 'activity');
    if (preferredIndex >= 0) {
      const merged = {...messages[preferredIndex], ...update};
      // artifacts 合并去重：同一工具多次进度事件各带一张图时，必须逐张累积，
      // 不能用最新一张覆盖已贴出的图（否则生成多张资产图只显示最后一张）。
      if (update.artifacts && (messages[preferredIndex].artifacts || []).length) {
        const existing = new Set((messages[preferredIndex].artifacts || []).map((artifact) => artifact.path));
        merged.artifacts = [
          ...(messages[preferredIndex].artifacts || []),
          ...update.artifacts.filter((artifact) => !existing.has(artifact.path)),
        ];
      }
      messages[preferredIndex] = merged;
      return {...state, messages};
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'activity' && messages[index].tool === tool && messages[index].status === 'running') {
      const merged = {...messages[index], ...update};
      // artifacts 合并去重，避免同一活动行被后续进度事件覆盖掉已贴的图
      if (update.artifacts && (messages[index].artifacts || []).length) {
        const existing = new Set((messages[index].artifacts || []).map((artifact) => artifact.path));
        merged.artifacts = [
          ...(messages[index].artifacts || []),
          ...update.artifacts.filter((artifact) => !existing.has(artifact.path)),
        ];
      }
      messages[index] = merged;
      return {...state, messages};
    }
  }
  return {...state, messages: [...messages, {id: preferredId || `activity-${tool}-${Date.now()}`, ...update, createdAt: update.createdAt || new Date().toISOString()}]};
}

function activityId(turnId: string, toolId: string | undefined, name: string) {
  return `${turnId}-tool-${toolId || name}`;
}

const TOOL_IDENTIFIER_PATTERN = /\b(?:vimax_narrative_planning|vimax_novel_planning|vimax_render_video|view_image|read_json|write_json|read_file|write_file|list_files|glob_files|search_text|run_shell|sleep|memory_read|memory_write|todo_read|todo_write)\b/g;

function finishLocalizedText(value: string): string {
  return value
    .replace(TOOL_IDENTIFIER_PATTERN, (match) => humanizeTool(match))
    .replace(/\bAgent\b/g, '智能体');
}

const ERROR_TYPE_LABELS: Record<string, string> = {
  AccountOverdueError: '账户欠费',
  RateLimitError: '请求频率超限',
  rate_limit_error: '请求频率超限',
  AuthenticationError: '身份验证失败',
  PermissionDeniedError: '权限不足',
  InvalidParameter: '参数不符合要求',
  invalid_request_error: '请求参数有误',
  TimeoutError: '请求超时',
};

function localizedErrorType(value?: string): string {
  if (!value) return '';
  return ERROR_TYPE_LABELS[value] || value;
}

function localizeProviderFailure(text: string): string | null {
  const httpStatus = text.match(/\bHTTP\s+(\d{3})\b/i)?.[1];
  const provider = /Volcano Ark|Volcengine Ark|火山方舟/i.test(text)
    ? '火山方舟'
    : /OpenRouter/i.test(text)
      ? 'OpenRouter'
      : /Bailian|百炼/i.test(text)
        ? '百炼'
        : '上游生成服务';
  const candidate = text.match(/All\s+(\d+)\s+(image|video)\s+candidates?\s+failed/i);
  const requestId = text.match(/(?:request[_\s-]?id|请求编号)\s*[:：=]?\s*["']?([A-Za-z0-9._:-]+)/i)?.[1];
  const errorType = text.match(/\bcode\s*[:=]\s*["']?([A-Za-z][A-Za-z0-9_.-]*)/i)?.[1]
    || text.match(/\b([A-Za-z][A-Za-z0-9_.-]*(?:Error|Exception))\b/i)?.[1];
  const hasKnownFailure = Boolean(httpStatus || candidate || /generation failed|request failed|AccountOverdueError|rate_limit_error|RateLimitError|Unauthorized|Forbidden/i.test(text));
  if (!hasKnownFailure) return null;

  const status = httpStatus ? `（HTTP ${httpStatus}）` : '';
  let subject = '上游生成请求失败';
  if (candidate) {
    subject = `${candidate[1]} ${candidate[2].toLowerCase() === 'image' ? '张候选画面' : '段候选视频'}全部生成失败`;
  } else if (/image generation failed/i.test(text)) {
    subject = '图像生成失败';
  } else if (/video generation failed/i.test(text)) {
    subject = '视频生成失败';
  }

  let reason = `${provider}返回错误，请查看错误信息后重试。`;
  if (/AccountOverdueError|overdue balance|account has an overdue balance/i.test(text)) {
    reason = `${provider}返回账户欠费状态，生成服务已暂停，请充值后重试。`;
  } else if (/rate_limit_error|RateLimitError|too many requests|HTTP\s+429/i.test(text)) {
    const current = text.match(/current concurrency is\s*(\d+)/i)?.[1];
    const limit = text.match(/concurrency limit of\s*(\d+)/i)?.[1];
    reason = current && limit
      ? `${provider}请求过于频繁，当前并发数为 ${current}，已超过账户允许的并发上限 ${limit}，请降低并发或充值后重试。`
      : `${provider}请求过于频繁，请稍后重试。`;
  } else if (/Unauthorized|authentication|invalid api key|HTTP\s+401/i.test(text)) {
    reason = `${provider}身份验证失败，请检查 API Key 是否正确。`;
  } else if (/Forbidden|permission|HTTP\s+403/i.test(text)) {
    reason = `${provider}没有调用权限或账户状态异常，请检查账户、模型权限和欠费状态。`;
  } else if (/invalid_request|invalid parameter|bad request|HTTP\s+400/i.test(text)) {
    reason = `${provider}请求参数不符合要求，请检查模型、分辨率、参考图和提示词。`;
  } else if (/timed?\s*out|timeout/i.test(text)) {
    reason = `${provider}请求超时，任务可能仍在处理中；请稍后重试或查询任务状态。`;
  } else if (/connection|network|fetch failed|ECONN|DNS|proxy/i.test(text)) {
    reason = `无法连接到${provider}，请检查网络、代理和接口地址后重试。`;
  } else if (httpStatus) {
    reason = `${provider}返回 HTTP ${httpStatus}，请求未成功。`;
  }

  const type = localizedErrorType(errorType);
  const details = [
    type ? `错误类型：${type}` : '',
    requestId ? `请求编号：${requestId}` : '',
  ].filter(Boolean).join('，');
  return `${subject}${status}：${reason}${details ? `（${details}）。` : ''}`;
}

export function localizeAgentText(value: string): string {
  // 只替换正文中的品牌词，不改写 URL、文件名和会话 id（例如 ...-vimax）。
  const text = String(value || '').replace(/(?<![A-Za-z0-9_?=&%./\\-])vimax\b/gi, '映界');
  if (!text) return text;
  const code = text.match(/Error code:\s*(\d{3})/i)?.[1];
  const requestId = text.match(/request_id:\s*([^)'"}\s]+)/i)?.[1];
  const providerFailure = localizeProviderFailure(text);
  if (providerFailure && !/Agent LLM request failed/i.test(text)) {
    return finishLocalizedText(providerFailure);
  }
  const llmFailure = /Agent LLM request failed|Error code:|rate_limit_error|RateLimitError/i.test(text);
  if (!llmFailure) return finishLocalizedText(text);
  if (code === '429' || /too many requests|rate_limit_error|RateLimitError/i.test(text)) {
    const current = text.match(/current concurrency is\s*(\d+)/i)?.[1];
    const limit = text.match(/concurrency limit of\s*(\d+)/i)?.[1];
    let message = '请求过于频繁，模型服务暂时拒绝处理。';
    if (current && limit) message = `请求过于频繁，当前并发数为 ${current}，已超过账户允许的并发上限 ${limit}。请充值额度或降低并发后重试。`;
    else if (/top up your balance/i.test(text)) message += '请充值额度或稍后重试。';
    if (requestId) message += `（请求编号：${requestId}）`;
    return finishLocalizedText(message);
  }
  if (code) return finishLocalizedText(`映界请求失败（错误码 ${code}）。请稍后重试${requestId ? `（请求编号：${requestId}）` : ''}。`);
  return finishLocalizedText(text.replace(/^Agent LLM request failed:\s*/i, '映界请求失败：').replace(/^Error code:/i, '错误码：'));
}


export function humanize(value: string) {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
    .replace(/\bVimax\b/g, '映界');
}

const TOOL_LABELS: Record<string, string> = {
  vimax_narrative_planning: '叙事规划',
  vimax_novel_planning: '小说规划',
  vimax_render_video: '渲染视频',
  view_image: '查看图像',
  read_json: '读取JSON',
  write_json: '写入JSON',
  read_file: '读取文件',
  write_file: '写入文件',
  list_files: '列出文件',
  glob_files: '搜索文件',
  search_text: '文本搜索',
  run_shell: '运行命令',
  sleep: '等待',
  workflow: '工作流',
  runtime: '运行时',
  tool: '工具',
  autonomy: '自主推进',
};

const TOOL_PREFIX_LABELS: Array<[string, string]> = [
  ['memory_', '记忆'],
  ['todo_', '任务清单'],
];

const STAGE_LABELS: Record<string, string> = {
  starting: '开始',
  running: '运行中',
  completed: '完成',
  failed: '失败',
  interrupted: '已中断',
  initializing_llm: '正在初始化对话模型（带预算上限）',
  develop_story: '创作故事：正在生成剧情框架与情节走向',
  extract_characters: '提取角色：正在解析剧本人物、性格与形象锚点',
  extract_scene_props: '提取场景与道具：正在盘点场景、器物与道具清单',
  extract_asset_manifest: '提取资产清单：正在汇总角色/场景/道具/音色清单',
  split_episodes: '按集拆分剧本：正在切分剧集并整理集级脚本',
  episodes_ready: '剧本已按集拆分，集级脚本就绪',
  plan_production_bible: '制定全剧创作宪法：正在规划风格锚、光影、色彩与连续性约束',
  script_gate_report: '出稿机检：正在扫描红线词、台词覆盖与分镜完整性',
  plan_storyboard: '生成分镜：正在解析剧本段落、规划镜头清单与机位',
  assets_manifest_ready: '资产清单已就绪，等待确认后进入分镜',
  assets_episode_scope: '按集补资产：只生成这一集缺的角色/场景/道具/音色，已有资产直接复用',
  assets_ready: '资产已就绪，等待确认后进入分镜',
  assets_timeout: '资产生成超时，已生成的资产已保留（重跑只补未生成的部分）',
  asset_timeout: '资产生成超时，已生成的资产已保留（重跑只补未生成的部分）',
  assets_cancelled: '资产生成已取消，已生成的资产已保留',
  asset_cancelled: '资产生成已取消，已生成的资产已保留',
  asset_failed: '资产生成失败，已保留部分产物',
  write_script: '编写剧本：正在生成剧本正文',
  design_storyboard: '设计分镜：正在为每个镜头设计景别/机位/光影/表演',
  decompose_shots: '拆解镜头描述：正在为每个镜头生成首末帧与运动描述',
  construct_camera_tree: '构建机位规划：正在梳理镜头机位与轴线关系',
  generate_frames: '生成关键帧：正在为镜头生成候选画面并择优',
  video_clip: '生成视频片段：正在逐镜调用视频模型渲染并质检',
  concat: '合成成片：正在按转场拼接片段、压制字幕、混合音轨',
  render_done: '渲染完成，成片已输出',
  render: '渲染',
};

export function humanizeTool(tool: string): string {
  const key = tool.toLowerCase();
  if (TOOL_LABELS[key]) return TOOL_LABELS[key];
  for (const [prefix, label] of TOOL_PREFIX_LABELS) {
    if (key.startsWith(prefix)) return label;
  }
  return humanize(key || 'Workflow');
}

export function humanizeStage(stage: string): string {
  const key = stage.toLowerCase();
  return STAGE_LABELS[key] || humanize(key);
}

// ===== 傻瓜式步骤条 =====
// 按用户可理解的粒度定义生成视频的完整步骤；每步关联触发它的 stage 集合。
// 顺序敏感：渲染时按出现顺序推进，当前步骤高亮、已完成打勾、未完成置灰。
export interface WorkflowStepDef {
  id: string;
  name: string;          // 进行中文案（"生成角色中"）
  doneName: string;      // 已完成文案（"角色生成已完成"）
  stages: Array<string>; // 触发/归属该步骤的 stage（小写）
}

export const WORKFLOW_STEPS: WorkflowStepDef[] = [
  {id: 'analyze', name: '分析剧本中', doneName: '剧本分析完成', stages: ['starting', 'initializing_llm', 'chat_model_ready', 'develop_story', 'idea_pipeline_ready', 'script_pipeline_ready', 'plan_script', 'plan_scene', 'planning_failed', 'narrative_planning', 'narrative_planned', 'novel_planning', 'novel_planned']},
  {id: 'split', name: '剧本拆解中', doneName: '剧本拆解完成', stages: ['split_episodes', 'episodes_ready']},
  {id: 'extract', name: '提取资产清单中', doneName: '资产清单提取完成', stages: ['plan_production_bible', 'extract_asset_manifest', 'extract_characters', 'extract_scene_props', 'assets_extracting', 'assets_pending', 'assets_manifest_partial', 'assets_manifest_ready']},
  {id: 'chars', name: '生成角色中', doneName: '角色生成已完成', stages: ['chars', 'character_portraits_start', 'character_portraits_loaded', 'assets_start', 'asset_characters_start', 'character_portrait_start', 'character_portrait_sheet_start', 'variant_portrait_start', 'variant_portrait_sheet_start', 'character_portrait_sheet_done', 'variant_portrait_done', 'character_portrait_done', 'asset_characters_done', 'assets_regen_start']},
  {id: 'scenes', name: '生成场景中', doneName: '场景生成已完成', stages: ['scenes', 'scene_prop_extract_start', 'scene_prop_extract_done', 'scene_asset_start', 'asset_scenes_start', 'scene_asset_done', 'asset_scenes_done']},
  {id: 'props', name: '生成道具中', doneName: '道具生成已完成', stages: ['props', 'prop_asset_start', 'scene_prop_assets_reused', 'scene_asset_exists', 'scene_prop_assets_done', 'asset_props_start', 'prop_asset_done', 'asset_props_done']},
  {id: 'voices', name: '生成音色中', doneName: '音色生成已完成', stages: ['voices', 'voice_desc_start', 'voice_create_start', 'voice_assets_filtered', 'voice_assets_skipped', 'voice_assets_done', 'asset_voices_start', 'voice_create_done', 'voice_assets_loaded', 'asset_voices_done']},
  {id: 'confirm', name: '等待确认资产', doneName: '资产已确认', stages: ['confirm', 'assets_ready', 'assets_reused', 'cleared', 'render_start', 'assets_confirm_pending', 'assets_confirmed', 'assets_episode_scope', 'assets_timeout', 'asset_timeout', 'assets_cancelled', 'asset_cancelled', 'asset_failed']},
  {id: 'storyboard', name: '设计分镜中', doneName: '分镜设计完成', stages: ['plan_storyboard', 'design_storyboard', 'load_storyboard', 'storyboard_ready', 'script_gate_report', 'revising', 'storyboard_pending', 'storyboard_planning']},
  {id: 'shots', name: '拆解镜头中', doneName: '镜头拆解完成', stages: ['decompose_shots', 'load_shot_descriptions', 'shot_descriptions_ready', 'shots_decomposed']},
  {id: 'camera', name: '构建机位中', doneName: '机位规划完成', stages: ['construct_camera_tree', 'load_camera_tree', 'camera_tree_ready']},
  {id: 'frames', name: '生成关键帧中', doneName: '关键帧生成完成', stages: ['frames_start', 'generate_frames', 'keyframes_start', 'keyframe_done', 'rendering', 'render_interrupted_detected', 'resume_counts', 'partial_render', 'render_timeout_guard', 'render_ready']},
  {id: 'clips', name: '生成视频片段中', doneName: '视频片段生成完成', stages: ['video_clip', 'clip_start', 'clip_done', 'batch_render_episode', 'partially_complete']},
  {id: 'concat', name: '合成成片中', doneName: '成片已合成', stages: ['concat', 'render_done', 'rendered', 'completed']},
];

export function workflowStepIndexForStage(stage: string): number {
  const key = (stage || '').toLowerCase();
  for (let i = 0; i < WORKFLOW_STEPS.length; i++) {
    if (WORKFLOW_STEPS[i].stages.includes(key)) return i;
  }
  return -1;
}

/** 「输入准备」族（分析/拆集/提取清单）：这几步没跑完时，状态库里的资产进度都是假进度。 */
const PREP_STEP_IDS = new Set(['analyze', 'split', 'extract']);

const RUNNING_IGNORED_STAGES = new Set(['starting', 'completed', 'failed', 'interrupted']);

/**
 * 步骤条该停在哪一格（唯一的定位口径，App 只负责渲染）。
 *
 * 优先级：
 *   0) 最新一条**正在运行**的消息若落在「输入准备」族（分析/拆集/提取清单）——它压过
 *      `progressStage`。实测事故（2026-10-16）：资产清单只抽到 3/80 集，状态库却把项目当成
 *      进了资产阶段、`progressStage` 给出 `chars` → 步骤条显示"生成角色中"，而实际正在做的是
 *      "按集提取场景与道具"。清单没抽完时的资产进度是假进度，必须以正在跑的那一步为准。
 *   1) `progressStage`（服务端按资产表实际状态推导，资产阶段内最精确）。
 *   2) 最新一条正在运行且可映射的业务 stage（覆盖会话 stage 的滞后/超前）。
 *   3) 会话 stage（无进行中工具时：等待确认/流程完成）。
 *   4) 历史消息里的业务 stage（刷新后 running 丢失时的兜底）。
 */
export function resolveWorkflowStepIndex(
  messages: Message[],
  stage?: string,
  progressStage?: string,
): number {
  const runningStages = (requireRunning: boolean) => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const item = messages[i];
      if (!item.stage || RUNNING_IGNORED_STAGES.has(item.stage)) continue;
      if (requireRunning && item.status !== 'running') continue;
      const index = workflowStepIndexForStage(item.stage);
      if (index >= 0) return index;
    }
    return -1;
  };
  const latestRunning = runningStages(true);
  if (latestRunning >= 0 && PREP_STEP_IDS.has(WORKFLOW_STEPS[latestRunning].id)) return latestRunning;
  if (progressStage) {
    const index = workflowStepIndexForStage(progressStage);
    if (index >= 0) return index;
  }
  if (latestRunning >= 0) return latestRunning;
  if (stage) {
    const index = workflowStepIndexForStage(stage);
    if (index >= 0) return index;
  }
  return runningStages(false);
}

const TOOL_START_TEXT: Record<string, string> = {
  vimax_narrative_planning: '开始叙事规划：解析剧本 → 提取人物/场景/道具 → 规划段落与镜头',
  vimax_novel_planning: '开始小说规划：解析小说 → 规划分集与剧情结构',
  vimax_render_video: '开始渲染：检查依赖 → 生成关键帧 → 逐镜出片 → 合成成片',
  view_image: '查看图像',
  read_json: '读取 JSON 数据',
  write_json: '写入 JSON 数据',
  read_file: '读取文件',
  write_file: '写入文件',
  list_files: '列出文件',
  glob_files: '搜索文件',
  search_text: '文本搜索',
  run_shell: '运行命令',
  sleep: '等待',
};

function toolStartText(tool: string): string {
  const key = tool.toLowerCase();
  if (TOOL_START_TEXT[key]) return TOOL_START_TEXT[key];
  for (const [prefix, label] of TOOL_PREFIX_LABELS) {
    if (key.startsWith(prefix)) return `${label}：开始读写任务数据`;
  }
  return `开始${humanizeTool(key)}`;
}

function cleanError(value: string) {
  return value.replace(/\s+/g, ' ').trim();
}

// ===== 口令选项解析 =====
// agent 回复末尾的"回复【1 继续分镜】"类口令，解析为可点击的快捷操作按钮，
// 用户点一下即发送对应口令，不用手敲。
export type ReplyOption = {
  /** 按钮上显示的文字（不含"回复"前缀，如「1 继续分镜」） */
  label: string;
  /** 点击后发送给 agent 的口令文本（如「1」） */
  reply: string;
  /** 解析出来的序号（1/2/3…），用于排序与去重 */
  index: number;
};

const REPLY_OPTION_BRACKET_PATTERN = /(?:回复\s*)?【\s*([^】]+)】/g;
const REPLY_OPTION_BARELESS_PATTERN = /^回复\s+(\d+)\s*[.、)）:：]?\s*([^，。,;；!！\n]+)/;

/**
 * 从 agent 回复文本中解析口令选项。兼容多种写法：
 *  - 「回复【1 继续分镜】」（标准，【】后跟解释文字也可）
 *  - 「【1 继续分镜】」（独立方括号行）
 *  - 「回复 1 全部确认，确认资产，开始分镜」（无括号，取首个标点前的短文本）
 * 规则：
 *  - 只取行首或独立行上的口令，避免误伤正文里提到的示例；
 *  - 同一序号只保留第一个（后续同序号视为重复/占位）；
 *  - 「全量渲染」等不带数字的文本口令也保留；
 *  - 返回的选项按序号升序，数字优先、文本口令在后。
 */
export function parseReplyOptions(text: string): ReplyOption[] {
  const source = String(text || '');
  const seen = new Set<string>();
  const options: ReplyOption[] = [];
  for (const rawLine of source.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!/^回复/.test(line) && !/^【/.test(line)) continue;
    // 1) 方括号格式：回复【1 继续分镜】 / 【1 继续分镜】
    const bracketMatcher = new RegExp(REPLY_OPTION_BRACKET_PATTERN.source, 'g');
    let match: RegExpExecArray | null;
    let matchedBracket = false;
    while ((match = bracketMatcher.exec(line)) !== null) {
      matchedBracket = true;
      const raw = match[1].trim();
      if (!raw) continue;
      const numbered = /^(\d+)\s*[.、)）:：]?\s*(.*)$/.exec(raw);
      const index = numbered ? Number(numbered[1]) : NaN;
      const label = numbered ? `${index} ${numbered[2].trim()}`.trim() : raw;
      const reply = Number.isFinite(index) ? String(index) : raw;
      const dedupeKey = Number.isFinite(index) ? `#${index}` : `text:${raw}`;
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      options.push({label, reply, index: Number.isFinite(index) ? index : Number.MAX_SAFE_INTEGER});
    }
    if (matchedBracket) continue;
    // 2) 无括号格式：回复 1 全部确认，确认资产，开始分镜
    const bare = REPLY_OPTION_BARELESS_PATTERN.exec(line);
    if (bare) {
      const index = Number(bare[1]);
      const label = `${index} ${bare[2].trim()}`.trim();
      const dedupeKey = `#${index}`;
      if (seen.has(dedupeKey)) continue;
      seen.add(dedupeKey);
      options.push({label, reply: String(index), index});
    }
  }
  return options.sort((a, b) => {
    if (a.index !== b.index) return a.index - b.index;
    return a.label.localeCompare(b.label);
  });
}

/** 取消息里可点击的口令选项；仅助手正式回复（含 answer/无 kind 的 assistant 消息）适用。 */
export function replyOptionsForMessage(message: Message): ReplyOption[] {
  if (message.role !== 'assistant') return [];
  const text = message.text || '';
  const options = parseReplyOptions(text);
  // 去掉重复内容（解析出的数字口令与文本口令内容相同则只留数字版）
  return options;
}
