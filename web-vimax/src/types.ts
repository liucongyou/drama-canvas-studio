export type WorkflowStep = {
  id: string;
  label: string;
  status: 'pending' | 'active' | 'done' | string;
};

export type WorkflowPlan = {
  intent?: string;
  title?: string;
  summary?: string;
  stage?: string;
  current_step?: string;
  next_step?: string;
  status?: string;
  requires_confirmation?: boolean;
  waiting_for_user?: boolean;
  blockers?: string[];
  steps?: WorkflowStep[];
  allowed_tools?: string[];
  artifacts?: Record<string, unknown>;
  updated_at?: string;
};

export type SessionSummary = {
  sessionId: string;
  projectName: string;
  workingDir: string;
  stage: string;
  summary: string;
  /** 细粒度流程步骤（chars/scenes/props/voices/confirm）：按资产表实际状态推导，
   *  比项目级 stage（如 assets_pending）更精确，用于流程条定位中间进度 */
  progressStage?: string;
  idea: string;
  updatedAt: string;
  createdAt: string;
  compactionTurns: number;
  workflowPlan?: WorkflowPlan;
  /** 服务端 agent 进程存活标记：true 表示该项目正在后台运行 */
  running?: boolean;
};

export type ConfigSection = {
  model_provider?: string;
  model: string;
  base_url: string;
  api_key: string;
  has_api_key: boolean;
  api_key_hint?: string;
  max_tokens?: string;
  temperature?: string;
  reasoning_effort?: string;
  shots_per_batch?: string;
  batch_concurrency?: string;
  aspect_ratio?: string;
  resolution?: string;
  fps?: string;
  duration?: string;
  subtitles?: string;
  workspace_id?: string;
  bucket?: string;
  region?: string;
  prefix?: string;
};

export type AgentConfig = {
  sections: Record<'llm' | 'image' | 'video' | 'embedding' | 'reranker' | 'bailian' | 'tos' | 'storyboard', ConfigSection>;
};

export type ArtifactProduction = {
  /** 真实模型名（如 Doubao-Seedance-2.0-mini） */
  modelName?: string;
  /** 可编辑的模型端点 ID（如 ep-m-...） */
  modelRef?: string;
  /** 供应商回传的清晰度标签，如 480p / 720p */
  resolution?: string;
  /** 生成时长（秒） */
  duration?: number;
};

export type Artifact = {
  path: string;
  name: string;
  kind: 'image' | 'video' | 'audio' | 'document';
  size: number;
  updatedAt: string;
  url: string;
  /** 视频/音频等媒体的生产信息（可选，服务端或浏览器探测得到） */
  production?: ArtifactProduction;
};

export type WorkspaceUpload = {
  /** 展示名（用户上传时的原名，界面显示用） */
  name: string;
  /** 唯一身份：会话相对路径（`uploads/<内容指纹12位>-<原名>`），消息里的 <workspace_uploads> 用的就是它 */
  path: string;
  size: number;
  /** 磁盘上的文件名（含内容指纹前缀；排查时比 name 更准确） */
  storedName?: string;
  /** 内容指纹（sha256 前 12 位）：同一份文件重复上传会复用同一路径 */
  hash?: string;
  /** true = 命中已有同一份内容，没有重复落盘 */
  reused?: boolean;
  /**
   * 该文件实际落在哪个会话工作区（前端补齐；服务端返回体里没有这个字段）。
   * 存在它是为了在发送前把"属于别的项目"的上传件剔掉——那些路径在当前项目里读不到。
   */
  sessionId?: string;
};

export type JsonPrimitive = string | number | boolean | null;

export type JsonValue = JsonPrimitive | JsonValue[] | {[key: string]: JsonValue};

export type Message = {
  id: string;
  role: 'user' | 'assistant' | 'activity' | 'error';
  text: string;
  turnId?: string;
  kind?: 'answer' | 'commentary';
  streaming?: boolean;
  createdAt?: string;
  tool?: string;
  toolCallId?: string;
  toolArguments?: Record<string, unknown>;
  status?: 'running' | 'done' | 'error';
  stage?: string;
  artifacts?: Artifact[];
};

export type AgentEvent = {
  type?: string;
  turn_id?: string;
  round?: number;
  delta?: string;
  message?: string;
  phase?: string;
  status?: string;
  stream?: string;
  line?: string;
  assistant?: string;
  activeSessionId?: string;
  session_id?: string;
  sessions?: SessionSummary[];
  workflow_plan?: WorkflowPlan;
  tool?: {id?: string; name?: string; requested_name?: string; arguments?: Record<string, unknown>};
  progress?: {stage?: string; message?: string; metadata?: Record<string, unknown>};
  tool_result?: {name?: string; ok?: boolean; content?: string; metadata?: Record<string, unknown>};
  session?: {
    active_session_id?: string;
    session?: {
      session_id?: string;
      working_dir?: string;
      stage?: string;
      summary?: string;
      workflow_plan?: WorkflowPlan;
    } | null;
  };
  prompt_trace?: {
    total_estimated_tokens?: number;
    totals?: {total_tokens?: number; total_estimated_tokens?: number};
  };
  /** 端到端自主推进事件：阶段导演在自主模式下跨阶段自动推进 / 到达人工闸门时发出 */
  autonomy?: {
    action?: 'advance' | 'pause' | 'capped';
    from?: string;
    to?: string;
    at?: string;
    to_label?: string;
    next_step?: string;
    message?: string;
    advances_used?: number;
  };
};

export type ChatState = {
  messages: Message[];
  busy: boolean;
  turnId: string;
  promptTokens: number;
  statusText: string;
  workflowPlan?: WorkflowPlan;
};
