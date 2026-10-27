import {appendFileSync, createReadStream, createWriteStream, existsSync, mkdirSync, statSync, writeFileSync} from 'node:fs';
import {readFile, stat, writeFile} from 'node:fs/promises';
import {createServer} from 'node:http';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {readAgentConfig, saveAgentConfig, testSectionConfig} from './config-store.mjs';
import {
  aggregateSessionUsage,
  artifactContentType,
  compactSessionStateForEvents,
  deleteEpisodeRenderOutputs,
  deleteSession,
  listSessionArtifacts,
  parseByteRange,
  readEpisodeProgress,
  readLedger,
  readCanvas,
  readCanvasNotes,
  readCanvasMediaNodes,
  regenerateCharacterPortraits,
  saveCanvasShot,
  saveCanvasNotes,
  saveCanvasMediaNodes,
  readSessionDiskUsage,
  readSessionHistoryPage,
  readSessionState,
  resolveArtifactPath,
  resolveSessionRoot,
  resolveVideoPosterPath,
  resolveProductionEntryAsset,
  resolveProductionRequest,
  storeWorkspaceUpload,
  extractWorkspaceUploadPaths,
  missingWorkspaceUploads,
  pickNewSessionId,
  readRuntimeConfig,
  writeRuntimeConfig,
  readQualityReport,
  resolveQualityTargets,
} from './server-lib.mjs';

const webRoot = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webRoot, '..');
const isDev = process.argv.includes('--dev');

// 会话快照附加"是否正在运行"：agentProcesses 里存活进程的 sessionId 即运行中。
// 前端侧栏据此显示"● 运行中 · 正在…"，刷新/重连后依然准确（不依赖易丢失的 SSE 事件）。
async function readSessionsWithRunning() {
  const state = await readSessionState(repoRoot);
  const runningIds = new Set(
    [...agentProcesses.values()]
      .filter((record) => record?.child && record.child.exitCode === null)
      .map((record) => record.sessionId),
  );
  return {
    ...state,
    sessions: (state.sessions || []).map((session) => ({
      ...session,
      running: runningIds.has(session.sessionId),
    })),
  };
}

// 默认双栈监听（:: = IPv6 通配，同时接受 IPv4 与 IPv6），
// 保证用户无论用 localhost / 127.0.0.1 / [::1] 访问都能连上（旧窗口 localhost 白板的根因）
const host = process.env.VIMAX_WEB_HOST || '::';
const port = Number(process.env.VIMAX_WEB_PORT || 4173);
const configuredUploadLimit = Number(process.env.VIMAX_WEB_UPLOAD_MAX_BYTES || 100 * 1024 * 1024);
const uploadMaxBytes = Number.isFinite(configuredUploadLimit) && configuredUploadLimit > 0
  ? configuredUploadLimit
  : 100 * 1024 * 1024;
// 单活跃策略（默认）：同一时刻只允许一个项目的 agent 运行。用户是单人单机工作流，
// 多 agent 并存是"切换项目后旧项目还在后台调 LLM 烧算力 + 消息串台"的根因。
// 显式设置 VIMAX_MAX_ACTIVE_AGENTS>1 可恢复多项目并发。
const maxActiveAgents = Math.max(1, Number(process.env.VIMAX_MAX_ACTIVE_AGENTS || 1) || 1);
const maxQueuedAgents = Math.max(1, Number(process.env.VIMAX_MAX_QUEUED_AGENTS || 20) || 20);
const maxEventStreamsPerClient = Math.max(1, Number(process.env.VIMAX_MAX_EVENT_STREAMS_PER_CLIENT || 3) || 3);
const subscribers = new Map();
const eventStreamClientCounts = new Map();
const eventHistory = [];
const maxReplayEvents = Math.max(50, Number(process.env.VIMAX_EVENT_REPLAY_LIMIT || 500) || 500);
const maxReplayBytes = Math.max(64 * 1024, (Number(process.env.VIMAX_EVENT_REPLAY_MAX_KB || 256) || 256) * 1024);
let eventHistoryBytes = 0;
let eventSequence = 0;
const serverInstanceId = `${Date.now()}-${process.pid}-${Math.random().toString(36).slice(2, 8)}`;
const AGENT_CONTROL_PREFIX = '__YINGJIE_CONTROL__';
const agentIdleMs = (Number(process.env.VIMAX_AGENT_IDLE_MINUTES || 30) || 30) * 60 * 1000;
const logsDir = path.join(repoRoot, 'logs');
mkdirSync(logsDir, {recursive: true});
const agentProcesses = new Map();
const agentQueue = [];
const queuedAgentStarts = new Map();
let activeSessionId = '';

let vite = null;

const server = createServer(async (request, response) => {
  const url = new URL(request.url || '/', `http://${request.headers.host || `${host}:${port}`}`);
  try {
    if (url.pathname === '/api/events' && request.method === 'GET') {
      return await openEventStream(request, response);
    }
    if (url.pathname === '/api/sessions' && request.method === 'GET') {
      return sendJson(response, 200, await readSessionsWithRunning());
    }
    if (url.pathname === '/api/config' && request.method === 'GET') {
      return sendJson(response, 200, await readAgentConfig(repoRoot));
    }
    if (url.pathname === '/api/config' && request.method === 'PUT') {
      const config = await saveAgentConfig(repoRoot, await readJsonBody(request));
      stopAgent('config');
      return sendJson(response, 200, config);
    }
    if (url.pathname === '/api/config/test' && request.method === 'POST') {
      const {section} = await readJsonBody(request);
      const supported = ['llm', 'image', 'video', 'embedding', 'reranker', 'bailian', 'tos'];
      if (typeof section !== 'string' || !supported.includes(section)) {
        return sendJson(response, 400, {error: 'section 参数无效'});
      }
      const result = await testSectionConfig(repoRoot, section);
      return sendJson(response, 200, result);
    }
    if (url.pathname === '/api/style-library' && request.method === 'GET') {
      // 风格体系 v2：9 大类 × 子风格库（前端风格选择器渲染数据）
      return sendJson(response, 200, await readStyleLibrary(repoRoot));
    }
    if (url.pathname === '/api/style-preference' && request.method === 'GET') {
      return sendJson(response, 200, await readStylePreference(repoRoot));
    }
    if (url.pathname === '/api/style-preference' && request.method === 'PUT') {
      const body = await readJsonBody(request);
      const style = String(body.style || '').trim();
      if (style.length > 100) return sendJson(response, 400, {error: '风格文本过长'});
      return sendJson(response, 200, await saveStylePreference(repoRoot, style));
    }
    if (url.pathname === '/api/sessions' && request.method === 'DELETE') {
      const sessionId = url.searchParams.get('session') || '';
      const current = await readSessionState(repoRoot);
      if (!current.sessions.some((session) => session.sessionId === sessionId)) {
        return sendJson(response, 404, {error: '项目不存在'});
      }
      stopAgent('delete', sessionId);
      await waitForAgentStop(sessionId);
      const state = await deleteSession(repoRoot, sessionId);
      activeSessionId = state.activeSessionId;
      broadcast({type: 'sessions_changed', ...(await readSessionsWithRunning())});
      return sendJson(response, 200, state);
    }
    if (url.pathname === '/api/history' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) {
        // 未选中项目时前端仍会轮询：空 session 返回空页（字段与 readSessionHistoryPage 一致），
        // 避免 500 导致前端白板/数据拉取失败，也避免字段名不一致被前端误读。
        return sendJson(response, 200, {messages: [], hasMore: false, nextCursor: ''});
      }
      const page = await readSessionHistoryPage(repoRoot, session, {
        before: url.searchParams.get('before') || '',
        limit: url.searchParams.get('limit') || '',
      });
      return sendJson(response, 200, page);
    }
    if (url.pathname === '/api/artifacts' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 200, {artifacts: [], total: 0, truncated: false});
      const page = Number(url.searchParams.get('page') || '1');
      const pageSize = Number(url.searchParams.get('pageSize') || '0');
      const artifacts = await listSessionArtifacts(repoRoot, session, {page, pageSize});
      // P1-6：显式回传 total/truncated（不再静默截断），前端可提示"产物过多已截断"并分页加载
      return sendJson(response, 200, {
        artifacts: Array.from(artifacts),
        total: artifacts.total ?? artifacts.length,
        truncated: artifacts.truncated ?? false,
        page: artifacts.page ?? page,
        pageSize: artifacts.pageSize ?? artifacts.length,
      });
    }
    if (url.pathname === '/api/episodes' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 200, {episodes: []});
      return sendJson(response, 200, {episodes: await readEpisodeProgress(repoRoot, session)});
    }
    // P5：统一项目状态接口——前端不再自行扫描目录，直接读 SQLite 权威状态。
    if (url.pathname === '/api/project-state' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      try {
        const state = await runStateBridge(['project', repoRoot, session]);
        return sendJson(response, 200, state);
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '状态桥调用失败'});
      }
    }
    // P5：资产单项明细（状态/模型/路径/版本/依赖镜头），支持按类型过滤。
    if (url.pathname === '/api/assets' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      const assetType = url.searchParams.get('type') || '';
      try {
        const items = await runStateBridge(['assets', repoRoot, session, ...(assetType ? ['--type', assetType] : [])]);
        return sendJson(response, 200, {items});
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '状态桥调用失败'});
      }
    }
    // P5：集状态（SQLite 权威 + 数值排序）
    if (url.pathname === '/api/episodes/state' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      try {
        const episodes = await runStateBridge(['episodes', repoRoot, session]);
        return sendJson(response, 200, {episodes});
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '状态桥调用失败'});
      }
    }
    // P5：资产单项操作——确认 / 标记有问题 / 发起重做。重做只作废指定项并广播事件，
    // 实际重新生成由 agent 侧 vimax_regenerate 增量补齐（不整批作废）。
    if (url.pathname === '/api/assets/confirm' && request.method === 'POST') {
      const body = await readJsonBody(request);
      const session = String(body.session || '').trim();
      const assetType = String(body.asset_type || '').trim();
      const assetKey = String(body.asset_key || '').trim();
      if (!session || !assetType || !assetKey) {
        return sendJson(response, 400, {error: 'session/asset_type/asset_key 不能为空'});
      }
      try {
        const result = await runStateBridge(['confirm', repoRoot, session, assetType, assetKey, ...(body.ep ? ['--ep', String(body.ep)] : [])]);
        broadcast({type: 'asset_lifecycle', action: 'confirmed', session_id: session, asset_type: assetType, asset_key: assetKey, at: new Date().toISOString()});
        return sendJson(response, 200, result);
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '资产确认失败'});
      }
    }
    if (url.pathname === '/api/assets/reject' && request.method === 'POST') {
      const body = await readJsonBody(request);
      const session = String(body.session || '').trim();
      const assetType = String(body.asset_type || '').trim();
      const assetKey = String(body.asset_key || '').trim();
      if (!session || !assetType || !assetKey) {
        return sendJson(response, 400, {error: 'session/asset_type/asset_key 不能为空'});
      }
      try {
        const result = await runStateBridge(['reject', repoRoot, session, assetType, assetKey, ...(body.reason ? ['--reason', String(body.reason)] : []), ...(body.ep ? ['--ep', String(body.ep)] : [])]);
        broadcast({type: 'asset_lifecycle', action: 'rejected', session_id: session, asset_type: assetType, asset_key: assetKey, reason: String(body.reason || ''), at: new Date().toISOString()});
        return sendJson(response, 200, result);
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '资产标记失败'});
      }
    }
    if (url.pathname === '/api/assets/redo' && request.method === 'POST') {
      const body = await readJsonBody(request);
      const session = String(body.session || '').trim();
      const assetType = String(body.asset_type || '').trim();
      const assetKey = String(body.asset_key || '').trim();
      if (!session || !assetType || !assetKey) {
        return sendJson(response, 400, {error: 'session/asset_type/asset_key 不能为空'});
      }
      try {
        const result = await runStateBridge(['redo', repoRoot, session, assetType, assetKey, ...(body.reason ? ['--reason', String(body.reason)] : []), ...(body.ep ? ['--ep', String(body.ep)] : [])]);
        broadcast({type: 'asset_lifecycle', action: 'redo_requested', session_id: session, asset_type: assetType, asset_key: assetKey, reason: String(body.reason || ''), at: new Date().toISOString()});
        return sendJson(response, 200, result);
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '资产重做标记失败'});
      }
    }
    if (url.pathname === '/api/ledger' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 200, {empty: true});
      return sendJson(response, 200, await readLedger(repoRoot, session));
    }
    if (url.pathname === '/api/canvas' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 200, {empty: true});
      const ep = url.searchParams.get('ep') || null;
      return sendJson(response, 200, await readCanvas(repoRoot, session, ep));
    }
    if (url.pathname === '/api/quality-report' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 200, {available: false});
      const ep = url.searchParams.get('ep') || null;
      return sendJson(response, 200, await readQualityReport(repoRoot, session, ep));
    }
    if (url.pathname === '/api/quality-report/recompute' && request.method === 'POST') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      const ep = url.searchParams.get('ep') || null;
      try {
        return sendJson(response, 200, await recomputeQualityReport(repoRoot, session, ep));
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '质量重算失败'});
      }
    }
    if (url.pathname === '/api/runtime-config' && request.method === 'GET') {
      return sendJson(response, 200, await readRuntimeConfig(repoRoot));
    }
    if (url.pathname === '/api/creation/presets' && request.method === 'GET') {
      try {
        return sendJson(response, 200, await listCreationPresets());
      } catch (error) {
        return sendJson(response, 500, {error: error instanceof Error ? error.message : '配方库加载失败'});
      }
    }
    if (url.pathname === '/api/creation/kickoff' && request.method === 'POST') {
      try {
        const body = await readJsonBody(request);
        return sendJson(response, 200, await composeCreationKickoff(body || {}));
      } catch (error) {
        return sendJson(response, 400, {error: error instanceof Error ? error.message : '配方组装失败'});
      }
    }
    if (url.pathname === '/api/runtime-config' && request.method === 'PUT') {
      try {
        const body = await readJsonBody(request);
        const saved = await writeRuntimeConfig(repoRoot, body || {});
        broadcast({type: 'runtime_config', autonomous_mode: saved.autonomous_mode});
        return sendJson(response, 200, saved);
      } catch (error) {
        return sendJson(response, 400, {error: String(error.message || error)});
      }
    }
    if (url.pathname === '/api/canvas/shot' && request.method === 'PUT') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      const body = await readJsonBody(request);
      const shotIdx = Number(body.shotIdx);
      if (!Number.isInteger(shotIdx) || shotIdx < 0) {
        return sendJson(response, 400, {error: 'shotIdx 参数无效'});
      }
      // P1：画布集模式默认进当前集，镜头补丁必须写集级 shots（episodes/{ep}/shots/{idx}），
      // 否则渲染读集级、保存写根级，编辑不生效且污染根目录。ep 为空时保持根模式兼容单集/旧项目。
      const ep = String(url.searchParams.get('ep') || '').trim();
      try {
        return sendJson(response, 200, await saveCanvasShot(repoRoot, session, shotIdx, body.patch ?? {}, ep || null));
      } catch (error) {
        return sendJson(response, 400, {error: String(error.message || error)});
      }
    }
    if (url.pathname === '/api/canvas/notes') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      if (request.method === 'GET') {
        return sendJson(response, 200, {notes: await readCanvasNotes(repoRoot, session)});
      }
      if (request.method === 'PUT') {
        const body = await readJsonBody(request);
        try {
          return sendJson(response, 200, await saveCanvasNotes(repoRoot, session, body.notes ?? []));
        } catch (error) {
          return sendJson(response, 400, {error: String(error.message || error)});
        }
      }
      return sendJson(response, 405, {error: '不支持的方法'});
    }
    if (url.pathname === '/api/canvas/media-nodes') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      if (request.method === 'GET') {
        return sendJson(response, 200, {nodes: await readCanvasMediaNodes(repoRoot, session)});
      }
      if (request.method === 'PUT') {
        const body = await readJsonBody(request);
        try {
          return sendJson(response, 200, await saveCanvasMediaNodes(repoRoot, session, body.nodes ?? []));
        } catch (error) {
          return sendJson(response, 400, {error: String(error.message || error)});
        }
      }
      return sendJson(response, 405, {error: '不支持的方法'});
    }
    if (url.pathname === '/api/assets/regenerate' && request.method === 'POST') {
      // 角色立绘重新生成标记：清 registry + 删 sheet.png（指定角色或全部），
      // 之后用户点「继续」让 agent 重跑资产生成，管线按缺失增量补齐（新提示词模板）。
      const body = await readJsonBody(request);
      const session = String(body.session || '').trim();
      if (!session) return sendJson(response, 400, {error: 'session 参数不能为空'});
      const target = body.target;
      const validTarget = target === '*' || (Array.isArray(target) && target.length > 0 && target.every((item) => typeof item === 'string' && item.trim().length > 0));
      if (!validTarget) return sendJson(response, 400, {error: 'target 必须为 "*" 或非空角色名数组'});
      const state = await readSessionState(repoRoot);
      if (!state.sessions.some((record) => record.sessionId === session)) {
        return sendJson(response, 404, {error: '项目不存在'});
      }
      try {
        const result = await regenerateCharacterPortraits(
          repoRoot,
          session,
          target === '*' ? '*' : target.map((item) => String(item).trim()),
        );
        return sendJson(response, 200, result);
      } catch (error) {
        return sendJson(response, 400, {error: String(error.message || error)});
      }
    }
    if (url.pathname === '/api/project-meta') {
      // P1-5：项目重命名/置顶偏好服务端持久化（.working_dir/project_meta.json），
      // 换浏览器/清缓存/换设备不再丢失，多端可见。
      const metaPath = path.join(repoRoot, '.working_dir', 'project_meta.json');
      if (request.method === 'GET') {
        try {
          const raw = await readFile(metaPath, 'utf8');
          return sendJson(response, 200, JSON.parse(raw));
        } catch {
          return sendJson(response, 200, {names: {}, pinned: []});
        }
      }
      if (request.method === 'PUT') {
        const body = await readJsonBody(request);
        const names = {};
        const rawNames = (body && typeof body.names === 'object' && body.names) || {};
        for (const [key, value] of Object.entries(rawNames)) {
          if (typeof value === 'string' && value.trim()) names[key] = value.trim().slice(0, 64);
        }
        const pinned = Array.isArray(body?.pinned) ? body.pinned.filter((item) => typeof item === 'string') : [];
        try {
          mkdirSync(path.dirname(metaPath), {recursive: true});
          writeFileSync(metaPath, JSON.stringify({names, pinned}, null, 2), 'utf8');
          return sendJson(response, 200, {names, pinned});
        } catch (error) {
          return sendJson(response, 500, {error: `项目偏好保存失败：${String(error.message || error)}`});
        }
      }
      return sendJson(response, 405, {error: '不支持的方法'});
    }
    if (url.pathname === '/api/disk' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) return sendJson(response, 200, {totalBytes: 0, totalFiles: 0, groups: {}});
      return sendJson(response, 200, await readSessionDiskUsage(repoRoot, session));
    }
    if (url.pathname === '/api/cleanup-episode' && request.method === 'DELETE') {
      const session = url.searchParams.get('session') || '';
      const ep = url.searchParams.get('ep') || '';
      if (!session || !ep) return sendJson(response, 400, {error: 'session 与 ep 参数不能为空'});
      try {
        const result = await deleteEpisodeRenderOutputs(repoRoot, session, ep);
        return sendJson(response, 200, result);
      } catch (error) {
        return sendJson(response, 400, {error: String(error.message || error)});
      }
    }
    if (url.pathname === '/api/usage' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      if (!session) {
        return sendJson(response, 200, {
          entries: 0,
          image: {count: 0, models: {}},
          video: {count: 0, duration_sec: 0, by_resolution: {}},
          audio: {count: 0, tts_chars: 0, billable_count: 0},
          breakdown: {by_kind: {}, by_purpose: {}, by_model: {}, by_resolution: {}, daily: {}},
          recent: [],
          recent_by_kind: {image: [], video: [], audio: []},
        });
      }
      return sendJson(response, 200, await aggregateSessionUsage(repoRoot, session));
    }
    if (url.pathname === '/api/artifact' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      const artifactPath = url.searchParams.get('path') || '';
      if (!session) return sendJson(response, 400, {error: '缺少会话 id'});
      try {
        await streamArtifact(request, response, session, artifactPath);
      } catch (error) {
        // 越界/非法路径等一律 400；绝不裸 return promise，避免 rejection 无人处理导致请求挂起
        return sendJson(response, 400, {error: String(error.message || error)});
      }
      return;
    }
    if (url.pathname === '/api/video-poster' && request.method === 'GET') {
      const session = url.searchParams.get('session') || '';
      const artifactPath = url.searchParams.get('path') || '';
      if (!session || !artifactPath) return sendJson(response, 400, {error: '缺少会话 id 或视频路径'});
      try {
        await serveVideoPoster(request, response, session, artifactPath);
      } catch (error) {
        return sendJson(response, 400, {error: String(error.message || error)});
      }
      return;
    }
    if (url.pathname === '/api/uploads' && request.method === 'POST') {
      const sessionId = url.searchParams.get('session') || '';
      const fileName = url.searchParams.get('name') || '';
      const current = await readSessionState(repoRoot);
      if (!current.sessions.some((session) => session.sessionId === sessionId)) {
        return sendJson(response, 404, {error: '项目不存在'});
      }
      const declaredSize = Number(request.headers['content-length'] || 0);
      if (declaredSize > uploadMaxBytes) {
        return sendJson(response, 413, {error: `文件超出 ${formatByteLimit(uploadMaxBytes)} 上传限制`});
      }
      const data = await readBinaryBody(request, uploadMaxBytes);
      const file = await storeWorkspaceUpload(repoRoot, sessionId, fileName, data);
      return sendJson(response, 201, {file});
    }
    if (url.pathname === '/api/agent/start' && request.method === 'POST') {
      const body = await readJsonBody(request);
      const sessionId = typeof body.sessionId === 'string' ? body.sessionId : '';
      const projectName = typeof body.projectName === 'string' ? body.projectName.trim() : '';
      if (projectName.length > 64) {
        return sendJson(response, 400, {error: '项目名称不能超过 64 个字符'});
      }
      const agentSessionId = await startAgent({newSession: body.newSession === true, sessionId, projectName});
      return sendJson(response, 200, {ok: true, sessionId: agentSessionId});
    }
    if (url.pathname === '/api/messages' && request.method === 'POST') {
      const body = await readJsonBody(request);
      const text = String(body.text || '').trim();
      const sessionId = String(body.sessionId || activeSessionId || '');
      if (!text) return sendJson(response, 400, {error: '消息文本不能为空'});
      // 上传引用守卫：UI 把本会话 uploads/ 下的相对路径附在消息尾部。路径当前项目里读不到
      // （实测：新建项目后上传，文件进了上一个项目）就在派发给 agent 之前拦下，并说清去哪重传，
      // 而不是让 agent 烧一轮对话后报 File not found。
      const uploadPaths = extractWorkspaceUploadPaths(text);
      if (uploadPaths.length > 0) {
        let missingUploads = [];
        try {
          missingUploads = await missingWorkspaceUploads(repoRoot, sessionId, uploadPaths);
        } catch {
          missingUploads = []; // 会话目录异常时交由后续流程处理，不在这里误拦
        }
        if (missingUploads.length > 0) {
          return sendJson(response, 400, {
            error: `这些文件不在当前项目里（可能上传到了其它项目）：${missingUploads.join('、')}。请在本项目重新上传后再发送。`,
            missing_uploads: missingUploads,
          });
        }
      }
      let record = agentProcesses.get(sessionId);
      if (!record?.child?.stdin.writable) {
        try {
          const restartedId = await startAgent({sessionId});
          record = agentProcesses.get(restartedId);
        } catch (error) {
          return sendJson(response, 409, {error: error instanceof Error ? error.message : '智能体启动失败'});
        }
        if (!record?.child?.stdin.writable) return sendJson(response, 409, {error: '当前项目的智能体未在运行'});
      }
      record.child.stdin.write(`${text}\n`);
      record.lastActivity = Date.now();
      return sendJson(response, 202, {ok: true});
    }
    if (url.pathname === '/api/agent/retry-tool' && request.method === 'POST') {
      const body = await readJsonBody(request);
      const sessionId = String(body.sessionId || activeSessionId || '');
      const toolCall = body.toolCall && typeof body.toolCall === 'object' ? body.toolCall : null;
      if (!toolCall?.id || !toolCall?.name || typeof toolCall.arguments !== 'object') {
        return sendJson(response, 400, {error: '重试参数无效'});
      }
      let record = agentProcesses.get(sessionId);
      if (!record?.child?.stdin.writable) {
        try {
          const restartedId = await startAgent({sessionId});
          record = agentProcesses.get(restartedId);
        } catch (error) {
          return sendJson(response, 409, {error: error instanceof Error ? error.message : '智能体启动失败'});
        }
        if (!record?.child?.stdin.writable) return sendJson(response, 409, {error: '当前项目的智能体未在运行'});
      }
      record.child.stdin.write(`${AGENT_CONTROL_PREFIX}${JSON.stringify({type: 'retry_tool', toolCall})}\n`);
      record.lastActivity = Date.now();
      return sendJson(response, 202, {ok: true});
    }
    if (url.pathname === '/api/agent/stop' && request.method === 'POST') {
      const body = await readJsonBody(request);
      stopAgent('user', String(body.sessionId || activeSessionId || ''));
      return sendJson(response, 200, {ok: true});
    }
    if (url.pathname === '/api/health' && request.method === 'GET') {
      return sendJson(response, 200, {
        ok: true,
        instanceId: serverInstanceId,
        agentRunning: agentProcesses.size > 0,
        activeSessionId,
        runningSessionIds: [...agentProcesses.keys()],
        queuedSessionIds: [...queuedAgentStarts.keys()],
        eventStreams: subscribers.size,
        maxEventStreamsPerClient,
        eventReplayBytes: eventHistoryBytes,
      });
    }
    if (url.pathname === '/assets/vimax.png' && request.method === 'GET') {
      response.writeHead(200, {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=3600'});
      const logoStream = createReadStream(path.join(repoRoot, 'assets', 'vimax.png'));
      logoStream.on('error', (error) => {
        uncaughtLog('assetStream', error);
        response.destroy();
      });
      logoStream.pipe(response);
      return;
    }
    if (vite) {
      vite.middlewares(request, response, () => sendJson(response, 404, {error: '未找到'}));
      return;
    }
    return serveProductionApp(response, url.pathname);
  } catch (error) {
    const status = Number(error?.statusCode) || 500;
    sendJson(response, status, {error: error instanceof Error ? error.message : String(error)});
  }
});

if (isDev) {
  vite = await (await import('vite')).createServer({
    root: webRoot,
    server: {middlewareMode: true, hmr: {server}},
    appType: 'spa',
  });
}

server.listen(port, host, () => {
  console.log(`映界 Web 服务已启动: http://${host}:${port}`);
});

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

async function startAgent({newSession, sessionId, projectName = '', restartCount = 0}) {
  if (newSession && sessionId) {
    const error = new Error('请选择新建会话或使用既有会话之一');
    error.statusCode = 400;
    throw error;
  }
  if (sessionId && queuedAgentStarts.has(sessionId)) return queuedAgentStarts.get(sessionId).promise;
  if (sessionId) {
    const existing = agentProcesses.get(sessionId);
    if (existing?.child && existing.child.exitCode === null) {
      activeSessionId = sessionId;
      broadcast({type: 'bridge_status', status: 'ready', message: '智能体已在运行', session_id: sessionId});
      return sessionId;
    }
    if (existing) agentProcesses.delete(sessionId);
  }

  // 切换项目/新建项目时，先温和停止其他所有运行中的 agent。
  // 若不停止，旧项目 agent 会在用户切走后继续在后台调用 LLM，双倍烧算力，
  // 且其事件流会与当前项目混在一起（表现为"串项目/多对话框"）。
  // 停止旧 agent 是异步的（写 .cancel 标记 + stdin 取消 + 5s SIGTERM 兜底），
  // 与启动新 agent 并行执行，不影响新项目立即开始。
  const keepId = sessionId || (newSession ? `new:${projectName || 'project'}` : '');
  for (const [otherId, otherRecord] of [...agentProcesses.entries()]) {
    if (otherId === keepId || otherRecord.stopping) continue;
    if (otherRecord.child && otherRecord.child.exitCode === null) {
      stopAgent('switch', otherId);
    }
  }

  if (agentProcesses.size >= maxActiveAgents) {
    if (agentQueue.length >= maxQueuedAgents) {
      const error = new Error(`等待启动的项目已达上限（${maxQueuedAgents}）`);
      error.statusCode = 429;
      throw error;
    }
    let resolveJob;
    let rejectJob;
    const promise = new Promise((resolve, reject) => {
      resolveJob = resolve;
      rejectJob = reject;
    });
    const key = sessionId || `new:${projectName || 'project'}:${Date.now()}:${Math.random().toString(36).slice(2, 6)}`;
    const job = {newSession, sessionId, projectName, restartCount, key, promise, resolve: resolveJob, reject: rejectJob, enqueuedAt: Date.now()};
    agentQueue.push(job);
    queuedAgentStarts.set(key, job);
    broadcast({type: 'bridge_status', status: 'queued', message: `前面还有 ${agentQueue.length - 1} 个项目，等待执行`, session_id: key});
    return promise;
  }
  const {command, args} = agentCommand();
  // 创作模式（自主/协作）：读取运行时配置注入到 agent 启动环境，决定 VIMAX_AUTONOMOUS 是否启用。
  let autonomousMode = false;
  try {
    autonomousMode = (await readRuntimeConfig(repoRoot)).autonomous_mode;
  } catch {
    autonomousMode = false;
  }
  const sessionArgs = newSession
    ? ['--new-session', ...(projectName ? ['--new-session-name', projectName] : [])]
    : sessionId
      ? ['--session', sessionId]
      : [];
  activeSessionId = restartCount ? activeSessionId : (sessionId || '');
  const child = spawn(command, [...args, 'main_agent.py', '--jsonl', '--stdin-repl', ...sessionArgs], {
    cwd: repoRoot,
    env: {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      // 自主模式：启用后 agent 在分镜产出后自动评估质量门禁并自我修订（资产确认/成片验收仍保留人工闸门）。
      VIMAX_AUTONOMOUS: autonomousMode ? '1' : '',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const initialKey = sessionId || `new-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const record = {child, sessionId: initialKey, requestedSessionId: sessionId, projectName, restartCount, stopping: false, autoRestart: true, lastActivity: Date.now(), startedAtMs: Date.now(), stopWaiters: []};
  // web.log 轮转：超过 50MB 归档为 .1（最多保留 2 份），防止日志无界增长撑满磁盘
  try {
    const webLogPath = path.join(logsDir, 'web.log');
    const maxWebLogBytes = Number(process.env.VIMAX_WEB_LOG_MAX_MB || 50) * 1024 * 1024;
    if (existsSync(webLogPath) && (await import('node:fs/promises').then((m) => m.stat(webLogPath))).size > maxWebLogBytes) {
      const rotated1 = `${webLogPath}.1`;
      const rotated2 = `${webLogPath}.2`;
      if (existsSync(rotated1)) await import('node:fs/promises').then((m) => m.rename(rotated1, rotated2).catch(() => {}));
      await import('node:fs/promises').then((m) => m.rename(webLogPath, rotated1).catch(() => {}));
    }
  } catch { /* 轮转失败不影响服务 */ }
  const logStream = createWriteStream(path.join(logsDir, 'web.log'), {flags: 'a'});
  record.logStream = logStream;
  agentProcesses.set(initialKey, record);
  let childStdoutBuffer = '';
  broadcast({type: 'bridge_status', status: 'starting', message: newSession ? '正在创建工作区' : '正在打开工作区', session_id: initialKey});
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', (chunk) => {
    if (agentProcesses.get(record.sessionId) !== record) return;
    record.lastActivity = Date.now();
    if (logStream.writable) logStream.write(`[agent:stdout] ${String(chunk)}`);
    childStdoutBuffer += String(chunk);
    const lines = childStdoutBuffer.split(/\r?\n/);
    childStdoutBuffer = lines.pop() || '';
    for (const line of lines) consumeAgentLine(line, record);
  });
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', (chunk) => {
    if (agentProcesses.get(record.sessionId) !== record) return;
    if (logStream.writable) logStream.write(`[agent:stderr] ${String(chunk)}`);
    for (const line of String(chunk).split(/\r?\n/)) {
      if (line.trim()) broadcast({type: 'terminal', stream: 'stderr', line, session_id: record.sessionId});
    }
  });
  child.on('error', (error) => {
    if (agentProcesses.get(record.sessionId) !== record) return;
    broadcast({type: 'error', message: `智能体进程出错: ${error.message}`, session_id: record.sessionId});
  });
  child.on('exit', (code, signal) => {
    if (agentProcesses.get(record.sessionId) !== record) return;
    if (record.stopTimer) clearTimeout(record.stopTimer);
    agentProcesses.delete(record.sessionId);
    for (const resolveWaiter of record.stopWaiters.splice(0)) resolveWaiter();
    record.logStream?.end();
    const shouldRestart = record.autoRestart && !record.stopping && code !== 0 && record.restartCount < 5;
    if (shouldRestart) {
      record.restartCount += 1;
      const delay = Math.min(8000, 500 * (2 ** (record.restartCount - 1)));
      broadcast({
        type: 'bridge_status',
        status: 'restarting',
        message: `智能体异常退出，将在 ${Math.ceil(delay / 1000)} 秒后自动恢复`,
        session_id: record.sessionId,
      });
      record.restartTimer = setTimeout(() => {
        startAgent({sessionId: record.sessionId, projectName: record.projectName, restartCount: record.restartCount})
          .catch((error) => broadcast({type: 'error', message: `自动恢复失败: ${error.message}`, session_id: record.sessionId}))
          .finally(drainAgentQueue);
      }, delay);
      record.restartTimer.unref?.();
    } else {
      broadcast({
        type: 'bridge_status',
        status: code === 0 || signal === 'SIGTERM' ? 'stopped' : 'error',
        message: signal ? `智能体被 ${signal} 停止` : `智能体已退出，退出码 ${code ?? 0}`,
        session_id: record.sessionId,
      });
      drainAgentQueue();
    }
  });
  return await settleAgentSession(record, sessionId);
}

function drainAgentQueue() {
  while (agentQueue.length > 0 && agentProcesses.size < maxActiveAgents) {
    const job = agentQueue.shift();
    queuedAgentStarts.delete(job.key);
    startAgent({newSession: job.newSession, sessionId: job.sessionId, projectName: job.projectName, restartCount: job.restartCount})
      .then(job.resolve, job.reject);
  }
}

function cancelQueuedAgent(sessionId) {
  const job = queuedAgentStarts.get(sessionId);
  if (!job) return false;
  const index = agentQueue.indexOf(job);
  if (index >= 0) agentQueue.splice(index, 1);
  queuedAgentStarts.delete(sessionId);
  job.resolve(sessionId);
  broadcast({type: 'bridge_status', status: 'stopped', message: '已取消排队', session_id: sessionId});
  return true;
}

async function settleAgentSession(record, requestedSessionId) {
  // 快路径 3 秒；没拿到就转后台慢轮询（最多 ~30 秒）。
  // 旧实现只有 3 秒窗口：新建项目时 agent 把会话写进 sessions.json 需要 1~3 秒，一旦超窗口，
  // record 就永远挂在占位 key 上——/api/sessions 把该项目报成"未运行"（前端显示"正在连接
  // 智能体…"），而服务端 activeSessionId 还停在别的项目上（实测事故）。
  const deadline = Date.now() + 3000;
  let resolvedSessionId = requestedSessionId || '';
  const spawnedAtMs = Number(record.startedAtMs || 0) || Date.now();
  const claim = async () => {
    const state = await readSessionState(repoRoot);
    return pickNewSessionId(state.sessions, {
      spawnedAtMs,
      occupied: new Set([...agentProcesses.keys()].filter((key) => key !== record.sessionId)),
    });
  };
  while (!resolvedSessionId && Date.now() < deadline && agentProcesses.get(record.sessionId) === record) {
    resolvedSessionId = await claim();
    if (!resolvedSessionId) await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!resolvedSessionId) {
    // 慢轮询：不阻塞启动流程，拿到就补登记（否则前端一直认为这个项目没在跑）
    for (let attempt = 0; attempt < 15; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      if (agentProcesses.get(record.sessionId) !== record) return record.sessionId;
      resolvedSessionId = await claim();
      if (resolvedSessionId) break;
    }
  }
  if (agentProcesses.get(record.sessionId) !== record) return record.sessionId;
  resolvedSessionId ||= record.sessionId;
  if (resolvedSessionId !== record.sessionId) {
    agentProcesses.delete(record.sessionId);
    record.sessionId = resolvedSessionId;
    agentProcesses.set(resolvedSessionId, record);
  }
  const state = await readSessionState(repoRoot);
  // 自动重启（restartCount>0）不应抢占"最近激活项目"：用户可能正在看另一个项目，
  // 崩溃后自动恢复的 agent 若覆盖 activeSessionId，前端刷新后会被带到错误项目。
  if (!record.restartCount) activeSessionId = resolvedSessionId;
  broadcast({type: 'sessions_changed', ...(await readSessionsWithRunning()), activeSessionId});
  broadcast({type: 'bridge_status', status: 'ready', message: '智能体已就绪', session_id: resolvedSessionId});
  return resolvedSessionId;
}

function consumeAgentLine(line, record) {
  if (!line.trim()) return;
  try {
    const event = JSON.parse(line);
    // 进程身份只认 spawn/settle 时确定的 record.sessionId，绝不根据事件内容纠正 map key。
    // 原因：agent 广播的 session 事件来自 session_index.snapshot()，其 active_session_id 与
    // session.session_id 都取自磁盘全局单值 .vimax/sessions.json 的 active_session_id。
    // 多项目并发时后启动的进程会 set_active 覆盖该值，导致 A 广播时读到 B 的 id；
    // 若用它纠正 map key，会把 A 的 record 注册到 B 的 key 上并覆盖 B 的 record，
    // 之后给 B 发消息却写进 A 的 stdin（消息串台），切换项目时正在运行的项目即被破坏。
    // record 身份在 startAgent（spawn 参数）与 settleAgentSession（带占用检查的轮询）中确定，
    // 这里只负责广播，身份标记统一用 record.sessionId。
    event.session_id = event.session_id || record.sessionId;
    // activeSessionId 只由 startAgent / stopAgent / settle 维护（"最近一次用户激活的项目"），
    // 不由 agent 广播覆盖：并发进程的广播会把全局值改来改去，前端会话列表会乱跳。
    broadcast(event);
    if (event.type === 'session') {
      readSessionsWithRunning().then((state) => broadcast({type: 'sessions_changed', ...state}));
    }
  } catch {
    broadcast({type: 'terminal', stream: 'stdout', line, session_id: record.sessionId});
  }
}

async function openEventStream(request, response) {
  const clientKey = String(request.socket?.remoteAddress || 'unknown');
  const activeForClient = eventStreamClientCounts.get(clientKey) || 0;
  if (activeForClient >= maxEventStreamsPerClient) {
    // 同一浏览器配置的每个旧标签都会建立一条 SSE。Chrome 对同源 HTTP/1.1
    // 连接池有上限，如果全部被 SSE 占满，之后 history/artifacts 请求就会一直排队。
    // 超出的连接明确要求稍后重试，始终给普通 API 留出可用连接。
    response.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'close',
      'X-Accel-Buffering': 'no',
    });
    response.write('retry: 15000\n\n');
    response.write(`event: stream_capacity\ndata: ${JSON.stringify({type: 'bridge_status', status: 'throttled', message: '实时连接较多，已自动降频并保留数据请求通道'})}\n\n`);
    response.end();
    return;
  }
  eventStreamClientCounts.set(clientKey, activeForClient + 1);
  let released = false;
  let heartbeat = null;
  const releaseClientSlot = () => {
    if (released) return;
    released = true;
    const remaining = Math.max(0, (eventStreamClientCounts.get(clientKey) || 1) - 1);
    if (remaining === 0) eventStreamClientCounts.delete(clientKey);
    else eventStreamClientCounts.set(clientKey, remaining);
  };
  const cleanup = () => {
    if (heartbeat) clearInterval(heartbeat);
    subscribers.delete(response);
    releaseClientSlot();
  };
  request.once('close', cleanup);
  response.once('close', cleanup);
  response.once('error', cleanup);

  const url = new URL(request.url || '/api/events', `http://${request.headers.host || `${host}:${port}`}`);
  const requestedLastId = Number(request.headers['last-event-id'] || url.searchParams.get('lastEventId') || 0);
  const lastEventId = Number.isFinite(requestedLastId) ? requestedLastId : 0;
  let state;
  try {
    state = await readSessionsWithRunning();
  } catch (error) {
    cleanup();
    try { response.destroy(); } catch { /* ignore */ }
    uncaughtLog('eventStreamState', error);
    return;
  }
  if (released || response.writableEnded) return;
  const stateSessionId = state.sessions.some((session) => session.sessionId === state.activeSessionId) ? state.activeSessionId : '';
  const sessionId = activeSessionId && state.sessions.some((session) => session.sessionId === activeSessionId)
    ? activeSessionId
    : stateSessionId;
  if (!activeSessionId && sessionId) activeSessionId = sessionId;
  try {
    response.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    const running = agentProcesses.size > 0;
    response.write('retry: 1000\n\n');
    // 旧标签页在服务重启后可能只重连 SSE，不重新执行入口加载；先补发当前会话，
    // 再发 ready 状态，旧版恢复逻辑也能重新同步历史而不会停在空工作区。
    if (sessionId) {
      response.write(`data: ${JSON.stringify({type: 'sessions_changed', ...compactSessionStateForEvents({...state, activeSessionId: sessionId})})}\n\n`);
      response.write(`data: ${JSON.stringify({type: 'session', session: {active_session_id: sessionId}})}\n\n`);
    }
    // idle 不会被旧版前端当作恢复信号；统一发 ready（表示本地桥接服务可用），
    // 让所有历史版本都能在 SSE 重连后重新同步。消息发送时服务端仍会自动拉起空闲智能体。
    response.write(`data: ${JSON.stringify({type: 'bridge_status', status: 'ready', message: running ? '智能体已连接' : '本地服务已就绪', session_id: sessionId, agent_running: running})}\n\n`);
    for (const entry of eventHistory) {
      if (entry.id > lastEventId) response.write(`id: ${entry.id}\ndata: ${entry.payload}\n\n`);
    }
    subscribers.set(response, {clientKey, openedAt: Date.now(), cleanup});
    try {
      // Windows 上的安全软件、代理或浏览器可能把长时间无数据的 SSE 当作死连接。
      // 5 秒心跳加 TCP keepalive，避免旧页面陷入反复断开/重连。
      response.socket?.setKeepAlive(true, 5_000);
    } catch {
      // 某些运行时没有暴露 socket keepalive；SSE 心跳仍会继续。
    }
    heartbeat = setInterval(() => {
      try {
        response.write(': keepalive\n\n');
      } catch {
        cleanup();
        try { response.destroy(); } catch { /* ignore */ }
      }
    }, 5_000);
  } catch (error) {
    cleanup();
    try { response.destroy(); } catch { /* ignore */ }
    uncaughtLog('eventStreamOpen', error);
  }
}

function broadcast(event) {
  eventSequence += 1;
  const outgoingEvent = event?.type === 'sessions_changed'
    ? {...event, ...compactSessionStateForEvents(event)}
    : event;
  const serialized = JSON.stringify({...outgoingEvent, event_id: eventSequence});
  const serializedBytes = Buffer.byteLength(serialized, 'utf8');
  if (serializedBytes <= maxReplayBytes) {
    eventHistory.push({id: eventSequence, payload: serialized, bytes: serializedBytes});
    eventHistoryBytes += serializedBytes;
  }
  while (eventHistory.length > maxReplayEvents || eventHistoryBytes > maxReplayBytes) {
    const removed = eventHistory.shift();
    eventHistoryBytes = Math.max(0, eventHistoryBytes - (removed?.bytes || 0));
  }
  const payload = `id: ${eventSequence}\ndata: ${serialized}\n\n`;
  for (const [subscriber, metadata] of subscribers) {
    if (subscriber.destroyed || subscriber.writableEnded) {
      subscribers.delete(subscriber);
      metadata?.cleanup?.();
      continue;
    }
    try {
      subscriber.write(payload);
    } catch {
      subscribers.delete(subscriber);
      metadata?.cleanup?.();
      try { subscriber.destroy(); } catch { /* ignore */ }
    }
  }
}

function stopAgent(reason, sessionId = '') {
  if (sessionId) {
    if (cancelQueuedAgent(sessionId)) return;
  } else {
    for (const job of [...agentQueue]) cancelQueuedAgent(job.sessionId || job.key);
  }
  const records = sessionId
    ? [agentProcesses.get(sessionId)].filter(Boolean)
    : [...agentProcesses.values()];
  if (records.length === 0) return;
  const message = reason === 'config'
    ? '配置已更新'
    : reason === 'shutdown'
      ? '服务关闭中'
      : reason === 'delete'
        ? '项目已删除'
        : reason === 'switch'
          ? '已切换项目，停止上一个项目的生成'
          : '生成已停止';
  for (const record of records) {
    if (record.restartTimer) clearTimeout(record.restartTimer);
    // 所有停止原因（用户停止/配置更新/项目删除/服务关闭）统一先温和取消：
    // 写会话级取消标记（.working_dir/<sessionId>/.cancel）让渲染/资产生成循环在批次
    // 边界优雅落盘 cancelled/interrupted 并保留已完成产物，再通过 stdin 下发取消指令；
    // 未命中检查点才由 5s SIGTERM 兜底，避免强杀导致 running 回合残留。
    if (record.child?.stdin?.writable) {
      record.stopping = true;
      try {
        const cancelRoot = path.join(repoRoot, '.working_dir', record.sessionId);
        mkdirSync(cancelRoot, {recursive: true});
        writeFileSync(path.join(cancelRoot, '.cancel'), JSON.stringify({reason: message, at: new Date().toISOString()}), 'utf8');
      } catch { /* 标记写入失败不影响取消主流程 */ }
      try { record.child.stdin.write(`${AGENT_CONTROL_PREFIX}${JSON.stringify({type: 'cancel', reason: message})}\n`); } catch { /* stdin 已关闭则忽略 */ }
      broadcast({type: 'bridge_status', status: 'stopping', message, session_id: record.sessionId});
      record.stopTimer = setTimeout(() => {
        if (agentProcesses.get(record.sessionId) !== record) return;
        try { record.child?.kill('SIGTERM'); } catch { /* 进程已退出则忽略 */ }
      }, 5000);
      record.stopTimer.unref?.();
      continue;
    }
    record.stopping = true;
    agentProcesses.delete(record.sessionId);
    for (const resolveWaiter of record.stopWaiters.splice(0)) resolveWaiter();
    if (record.stopTimer) clearTimeout(record.stopTimer);
    try { record.child?.kill('SIGTERM'); } catch { /* 进程已退出则忽略 */ }
    broadcast({type: 'bridge_status', status: 'stopped', message, session_id: record.sessionId});
  }
  drainAgentQueue();
}

function waitForAgentStop(sessionId, timeoutMs = 6500) {
  const record = agentProcesses.get(sessionId);
  if (!record) return Promise.resolve();
  return new Promise((resolve) => {
    let timer = null;
    const done = () => {
      if (timer) clearTimeout(timer);
      const index = record.stopWaiters.indexOf(done);
      if (index >= 0) record.stopWaiters.splice(index, 1);
      resolve();
    };
    record.stopWaiters.push(done);
    timer = setTimeout(done, timeoutMs);
    timer.unref?.();
  });
}

function agentCommand() {
  if (process.env.VIMAX_AGENT_COMMAND) {
    return {command: process.env.VIMAX_AGENT_COMMAND, args: splitArgs(process.env.VIMAX_AGENT_ARGS || '')};
  }
  const isWindows = process.platform === 'win32';
  if (isWindows) {
    // Windows 下 venv 可执行文件位于 .venv\Scripts\python.exe；直接运行即可，
    // 无需经过 uv（uv 不在 PATH 时 spawn('uv') 会报 ENOENT）。
    const venvPython = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
    if (existsSync(venvPython)) return {command: venvPython, args: []};
  } else {
    const bundledUv = process.env.VIMAX_UV_CMD || path.join(process.env.HOME || '', '.local', 'bin', 'uv');
    if (bundledUv && existsSync(bundledUv)) return {command: bundledUv, args: ['run', 'python']};
    const venvPython = path.join(repoRoot, '.venv', 'bin', 'python3');
    if (existsSync(venvPython)) return {command: venvPython, args: []};
  }
  return {command: 'uv', args: ['run', 'python']};
}

function splitArgs(value) {
  return value.split(/\s+/).map((part) => part.trim()).filter(Boolean);
}

// ---------- 质量自评重算：spawn venv python 跑 utils/quality_assessment CLI 落盘报告 ----------
async function recomputeQualityReport(repoRoot, sessionId, epId) {
  const targets = resolveQualityTargets(repoRoot, sessionId, epId);
  if (!targets) {
    throw new Error('未找到 storyboard.json，无法重新评估（请先完成分镜生成）');
  }
  const {command, args: prefixArgs} = agentCommand();
  const runOne = (storyboardPath, reportPath) =>
    new Promise((resolve, reject) => {
      const child = spawn(command, [...prefixArgs, 'utils/quality_assessment.py', storyboardPath, reportPath], {
        cwd: repoRoot,
        env: {...process.env, PYTHONIOENCODING: 'utf-8'},
        windowsHide: true,
      });
      let stderr = '';
      child.stderr.on('data', (chunk) => { stderr += String(chunk); });
      child.on('error', reject);
      child.on('close', (code) => {
        if (code === 0) resolve();
        else reject(new Error(`质量评估脚本退出码 ${code}：${stderr.slice(0, 500)}`));
      });
    });
  if (targets.multi) {
    for (const ep of targets.eps) {
      // eslint-disable-next-line no-await-in-loop
      await runOne(ep.storyboardPath, ep.reportPath);
    }
  } else {
    await runOne(targets.storyboardPath, targets.reportPath);
  }
  return readQualityReport(repoRoot, sessionId, epId);
}

// ---------- 专业创作配方：spawn venv python 跑 utils/creation_presets ----------
async function listCreationPresets() {
  const {command, args: prefixArgs} = agentCommand();
  return new Promise((resolve, reject) => {
    const child = spawn(command, [...prefixArgs, '-m', 'utils.creation_presets', 'presets'], {
      cwd: repoRoot,
      env: {...process.env, PYTHONIOENCODING: 'utf-8'},
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += String(chunk); });
    child.stderr.on('data', (chunk) => { stderr += String(chunk); });
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) return reject(new Error(`配方库退出码 ${code}：${stderr.slice(0, 500)}`));
      try { resolve(JSON.parse(stdout)); } catch (err) { reject(new Error(`配方库输出解析失败：${err.message}`)); }
    });
  });
}

async function composeCreationKickoff(payload) {
  const {command, args: prefixArgs} = agentCommand();
  return new Promise((resolve, reject) => {
    const child = spawn(command, [...prefixArgs, '-m', 'utils.creation_presets', 'kickoff'], {
      cwd: repoRoot,
      env: {...process.env, PYTHONIOENCODING: 'utf-8'},
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += String(chunk); });
    child.stderr.on('data', (chunk) => { stderr += String(chunk); });
    child.stdin.on('error', () => {});
    child.stdin.write(JSON.stringify(payload || {}));
    child.stdin.end();
    child.on('error', reject);
    child.on('close', (code) => {
      if (code !== 0) {
        try {
          const parsed = JSON.parse(stdout);
          return reject(new Error(parsed.error || `配方组装退出码 ${code}`));
        } catch {
          return reject(new Error(`配方组装退出码 ${code}：${stderr.slice(0, 500)}`));
        }
      }
      try { resolve(JSON.parse(stdout)); } catch (err) { reject(new Error(`配方组装输出解析失败：${err.message}`)); }
    });
  });
}

// ---------- 风格体系 v2：风格库 + 默认风格偏好 ----------
// P5：状态桥——spawn venv python 调 agent_runtime.state_bridge 输出 SQLite 权威状态 JSON。
// 复用 agentCommand()（与 main_agent.py / visual_style.py 相同的解释器解析）。
function runStateBridge(args, timeoutMs = 15000) {
  return new Promise((resolvePromise, rejectPromise) => {
    const {command, args: prefixArgs} = agentCommand();
    const child = spawn(command, [...prefixArgs, 'agent_runtime/state_bridge.py', ...args], {
      cwd: repoRoot,
      env: {...process.env, PYTHONIOENCODING: 'utf-8'},
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    const timer = setTimeout(() => {
      try { child.kill('SIGTERM'); } catch { /* 已退出则忽略 */ }
      rejectPromise(new Error('state bridge timeout'));
    }, timeoutMs);
    child.on('close', (code) => {
      clearTimeout(timer);
      if (code === 0 && stdout.trim()) {
        try {
          resolvePromise(JSON.parse(stdout.trim()));
        } catch {
          rejectPromise(new Error(`state bridge 输出非 JSON：${stdout.slice(0, 200)}`));
        }
      } else {
        rejectPromise(new Error(`state bridge 退出码 ${code ?? 'null'}：${stderr.trim().slice(0, 300)}`));
      }
    });
  });
}

function runStyleCli(args, timeoutMs = 15000) {
  return new Promise((resolvePromise) => {
    const {command, args: prefixArgs} = agentCommand();    const child = spawn(command, [...prefixArgs, 'utils/visual_style.py', ...args], {
      cwd: repoRoot,
      env: {...process.env, PYTHONIOENCODING: 'utf-8'},
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    const timer = setTimeout(() => {
      try { child.kill('SIGTERM'); } catch { /* 已退出则忽略 */ }
      resolvePromise({ok: false, error: 'style cli timeout'});
    }, timeoutMs);
    child.on('error', (err) => { clearTimeout(timer); resolvePromise({ok: false, error: String(err?.message || err)}); });
    child.on('close', () => {
      clearTimeout(timer);
      try {
        resolvePromise({ok: true, data: JSON.parse(stdout)});
      } catch {
        resolvePromise({ok: false, error: stderr.trim() || 'style cli 输出非 JSON'});
      }
    });
  });
}

async function readStyleLibrary(repoRootPath) {
  const result = await runStyleCli(['list']);
  if (!result.ok) return {categories: [], error: result.error};
  return {categories: result.data, error: ''};
}

const stylePreferenceFile = (repoRootPath) => path.join(repoRootPath, '.vimax', 'style_preference.json');

async function readStylePreference(repoRootPath) {
  const file = stylePreferenceFile(repoRootPath);
  try {
    const text = await readFile(file, 'utf8');
    const parsed = JSON.parse(text);
    return {style: String(parsed.style || ''), error: ''};
  } catch {
    return {style: '', error: ''};
  }
}

async function saveStylePreference(repoRootPath, style) {
  const dir = path.join(repoRootPath, '.vimax');
  mkdirSync(dir, {recursive: true});
  const file = stylePreferenceFile(repoRootPath);
  await writeFile(file, JSON.stringify({style, updatedAt: new Date().toISOString()}), 'utf8');
  return {style, error: ''};
}

async function readJsonBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  if (!chunks.length) return {};
  const text = Buffer.concat(chunks).toString('utf8');
  if (text.length > 1_000_000) throw new Error('请求体过大');
  return JSON.parse(text);
}

async function readBinaryBody(request, maxBytes) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > maxBytes) {
      const error = new Error(`文件超出 ${formatByteLimit(maxBytes)} 上传限制`);
      error.statusCode = 413;
      throw error;
    }
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, size);
}

function formatByteLimit(bytes) {
  return `${Math.max(1, Math.round(bytes / (1024 * 1024)))} MB`;
}

function sendJson(response, status, payload, extraHeaders = {}) {
  if (response.writableEnded) return;
  response.writeHead(status, {'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', ...extraHeaders});
  response.end(JSON.stringify(payload));
}

// P2：服务端视频缩略图。ffmpeg 抽帧生成 <video>.poster.jpg 缓存（视频旁同名文件），
// 命中缓存直接返回图片；无 ffmpeg 或抽帧失败返回 404，前端回退浏览器截帧。
async function serveVideoPoster(request, response, sessionId, relativePath) {
  if (!/\.(mp4|webm|mov)$/i.test(relativePath)) {
    return sendJson(response, 400, {error: '仅支持视频产物生成缩略图'});
  }
  const videoPath = resolveArtifactPath(repoRoot, sessionId, relativePath);
  if (!existsSync(videoPath)) return sendJson(response, 404, {error: '视频产物不存在'});
  const posterPath = resolveVideoPosterPath(resolveSessionRoot(repoRoot, sessionId), relativePath);
  if (!existsSync(posterPath) || statSync(posterPath).mtimeMs < statSync(videoPath).mtimeMs) {
    try {
      mkdirSync(path.dirname(posterPath), {recursive: true});
      const result = spawnSync('ffmpeg', ['-ss', '0.2', '-i', videoPath, '-frames:v', '1', '-q:v', '3', '-y', posterPath], {encoding: 'utf8', timeout: 20000});
      if (result.status !== 0 || !existsSync(posterPath)) {
        return sendJson(response, 404, {error: '缩略图生成失败（ffmpeg 不可用或视频无法解码）'});
      }
    } catch {
      return sendJson(response, 404, {error: '缩略图生成失败（ffmpeg 不可用或视频无法解码）'});
    }
  }
  const info = statSync(posterPath);
  response.writeHead(200, {
    'Content-Type': 'image/jpeg',
    'Content-Length': String(info.size),
    'Cache-Control': 'private, max-age=3600, no-transform',
  });
  createReadStream(posterPath).pipe(response);
}

async function streamArtifact(request, response, sessionId, relativePath) {
  const filePath = resolveArtifactPath(repoRoot, sessionId, relativePath);
  if (!existsSync(filePath)) return sendJson(response, 404, {error: '产物不存在'});
  const info = await stat(filePath);
  if (!info.isFile()) return sendJson(response, 404, {error: '产物不是文件'});
  const rangeHeader = request.headers.range || '';
  const range = rangeHeader ? parseByteRange(rangeHeader, info.size) : null;
  if (rangeHeader && !range) {
    response.writeHead(416, {
      'Content-Range': `bytes */${info.size}`,
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'no-store',
    });
    return response.end();
  }
  const start = range?.start ?? 0;
  const end = range?.end ?? Math.max(0, info.size - 1);
  response.writeHead(range ? 206 : 200, {
    'Content-Type': artifactContentType(filePath),
    'Content-Length': String(range?.length ?? info.size),
    'Accept-Ranges': 'bytes',
    ...(range ? {'Content-Range': `bytes ${start}-${end}/${info.size}`} : {}),
    'Cache-Control': 'private, max-age=60, no-transform',
  });
  const fileStream = createReadStream(filePath, info.size > 0 ? {start, end} : undefined);
  const destroyStream = () => {
    if (!fileStream.destroyed) fileStream.destroy();
  };
  const destroyOnDisconnect = () => {
    if (!response.writableEnded) destroyStream();
  };
  request.once('aborted', destroyOnDisconnect);
  response.once('close', destroyOnDisconnect);
  fileStream.on('error', (error) => {
    uncaughtLog('artifactStream', error);
    if (!response.headersSent) sendJson(response, 500, {error: '读取产物失败'});
    else response.destroy();
  });
  fileStream.once('close', () => {
    request.off('aborted', destroyOnDisconnect);
    response.off('close', destroyOnDisconnect);
  });
  fileStream.pipe(response);
}

async function serveProductionApp(response, pathname) {
  const distRoot = path.resolve(webRoot, 'dist');
  let route = resolveProductionRequest(distRoot, pathname, existsSync);
  if (route.kind === 'missing') {
    // 旧标签页可能仍引用上一轮构建的入口包；若旧指纹文件已清理，
    // 用当前同类型入口包兜底，避免刷新后 index.html 仍在但 JS/CSS 404 变白板。
    try {
      const currentEntryAsset = resolveProductionEntryAsset(
        await readFile(path.join(distRoot, 'index.html'), 'utf8'),
        pathname,
      );
      if (currentEntryAsset) {
        const fallbackPath = path.resolve(distRoot, currentEntryAsset.replace(/^\/+/, ''));
        if (fallbackPath.startsWith(`${distRoot}${path.sep}`) && existsSync(fallbackPath)) {
          route = {kind: 'file', filePath: fallbackPath, cacheControl: 'no-cache, no-store, must-revalidate'};
        }
      }
    } catch {
      // 构建产物正在切换时保持原始 404/503 行为，不影响正常页面。
    }
  }
  if (route.kind === 'missing') {
    return sendJson(response, 404, {error: '资源不存在'}, {'Cache-Control': 'no-store'});
  }
  if (!existsSync(route.filePath)) {
    // dist 缺失/构建中断的防御：返回明确 503 而非未捕获 ENOENT 导致白板
    // no-store：严禁浏览器缓存错误响应，避免服务恢复后旧窗口仍拿到错误页
    return sendJson(response, 503, {error: '前端资源尚未就绪，请稍后刷新（服务正在构建/重启）'}, {'Cache-Control': 'no-store'});
  }
  const body = await readFile(route.filePath);
  // HTML/SPA 回退禁止缓存；带内容指纹的 /assets/ 资源长缓存（immutable）
  response.writeHead(200, {
    'Content-Type': artifactContentType(route.filePath),
    'Cache-Control': route.cacheControl,
  });
  response.end(body);
}

function shutdown() {
  stopAgent('shutdown');
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 1_000).unref();
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

// 全局兜底：任何未捕获异常/拒绝都不得让服务崩溃（记录日志并保持存活）。
// 之前服务"莫名消失"的根因之一：SSE 断连瞬间 write 抛错等单点错误直接打穿进程。
const uncaughtLog = (kind, error) => {
  try {
    const line = `[${new Date().toISOString()}] ${kind}: ${error instanceof Error ? (error.stack || error.message) : String(error)}\n`;
    appendFileSync(path.join(logsDir, 'web-node.err.log'), line, 'utf8');
  } catch {
    // 日志写入失败也绝不能再次抛错
  }
};
process.on('uncaughtException', (error) => {
  uncaughtLog('uncaughtException', error);
});
process.on('unhandledRejection', (reason) => {
  uncaughtLog('unhandledRejection', reason);
});
server.on('clientError', (error, socket) => {
  // HTTP 解析错误（畸形请求）无害化处理：记录后销毁 socket，不触发崩溃
  try {
    uncaughtLog('clientError', error);
    socket.destroy();
  } catch {
    // ignore
  }
});

server.on('error', (error) => {
  uncaughtLog('serverError', error);
  if (error?.code === 'EADDRINUSE') {
    process.exitCode = 1;
  }
});

// 空闲回收：长时间无活动的智能体进程自动停止，释放资源（再次发送消息会自动拉起）
const idleReaper = setInterval(() => {
  try {
    const now = Date.now();
    for (const record of agentProcesses.values()) {
      if (record.stopping || record.stopTimer) continue;
      if (record.child?.exitCode !== null) continue;
      if (now - (record.lastActivity || 0) > agentIdleMs) {
        broadcast({type: 'bridge_status', status: 'stopping', message: '智能体已空闲，正在回收（再次发送消息会自动重启）', session_id: record.sessionId});
        record.stopping = true;
        try { record.child?.kill('SIGTERM'); } catch { /* 进程已退出则忽略 */ }
      }
    }
  } catch (error) {
    uncaughtLog('idleReaper', error);
  }
}, 60_000);
idleReaper.unref?.();
