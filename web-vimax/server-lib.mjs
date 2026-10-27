import {createHash} from 'node:crypto';
import {lstat, mkdir, open, readFile, readdir, rename, rm, stat, writeFile} from 'node:fs/promises';
import {existsSync, readdirSync} from 'node:fs';
import path from 'node:path';
import {parse as parseYaml} from 'yaml';

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif']);
const VIDEO_EXTENSIONS = new Set(['.mp4', '.webm', '.mov']);
const AUDIO_EXTENSIONS = new Set(['.wav', '.mp3', '.m4a', '.ogg', '.aac', '.flac']);
const TEXT_EXTENSIONS = new Set(['.txt', '.md', '.json']);
const MODEL_PRESENTATION = new Map([
  ['ep-m-20260911200253-v5snx', {name: 'Doubao-Seedream-5.0-pro', ref: 'ep-m-20260911200253-v5snx'}],
  ['ep-m-20260917021019-cfgc7', {name: 'Doubao-Seedance-2.0-mini', ref: 'ep-m-20260917021019-cfgc7'}],
  ['doubao-seedream-5-0-pro-260628', {name: 'Doubao-Seedream-5.0-pro', ref: 'doubao-seedream-5-0-pro-260628'}],
  ['doubao-seedance-2-0-mini-260615', {name: 'Doubao-Seedance-2.0-mini', ref: 'doubao-seedance-2-0-mini-260615'}],
  ['cosyvoice-v3.5-flash', {name: 'CosyVoice-v3.5-Flash', ref: 'cosyvoice-v3.5-flash'}],
]);

function resolveModelPresentation(model) {
  const raw = String(model || 'unknown').trim() || 'unknown';
  return MODEL_PRESENTATION.get(raw.toLowerCase()) ?? {name: raw, ref: ''};
}

const EPISODE_HEADING_RE = /^\s*(?:#{1,6}\s*)?第\s*(?:[0-9０-９]+|[零一二三四五六七八九十百千两]+)\s*(?:集|话|章|回|节).*$/m;

function episodeBodyText(script) {
  const text = String(script || '').trim();
  const match = EPISODE_HEADING_RE.exec(text);
  return match ? text.slice(match.index) : text;
}

function normalizeAssetText(value) {
  return String(value || '')
    .normalize('NFKC')
    .replace(/[\s，。！？、,.!?;；:：“”"'《》〈〉「」『』【】[\]（）()·—…\-_/\\|]/g, '')
    .toLowerCase();
}

function assetNameVariants(name) {
  const raw = String(name || '').trim();
  const withoutParenthetical = raw.replace(/[（(].*?[)）]/g, '');
  const base = normalizeAssetText(raw);
  const compact = normalizeAssetText(withoutParenthetical);
  const noDe = compact.replace(/的/g, '');
  const strippedPrefix = compact.replace(/^(?:角色|人物|地点|场景|道具)/, '');
  const out = new Set([base, compact, noDe, strippedPrefix].filter((item) => item.length >= 2));
  for (const item of [...out]) {
    if (item.length > 2) out.add(item.slice(-2));
    if (item.length > 3) out.add(item.slice(-3));
  }
  return [...out];
}

function assetNameMatchesText(text, name) {
  const haystack = normalizeAssetText(text);
  if (!haystack) return false;
  const variants = assetNameVariants(name);
  if (variants.some((variant) => haystack.includes(variant))) return true;
  const longest = variants.sort((a, b) => b.length - a.length)[0] ?? '';
  if (longest.length < 4) return false;
  const grams = [];
  for (let index = 0; index < longest.length - 1; index += 1) grams.push(longest.slice(index, index + 2));
  const rawUnique = [...new Set(grams)];
  const unique = rawUnique.filter((gram) => /[\u4e00-\u9fa5]/.test(gram) && !/^\d+$/.test(gram));
  const hits = unique.filter((gram) => haystack.includes(gram)).length;
  if (/\d/.test(longest)) return unique.length >= 2 && hits >= 2 && hits / unique.length >= 0.4;
  if (unique.length <= 3 && hits >= 1) return true;
  return unique.length >= 3 && hits >= 2 && hits / unique.length >= 0.4;
}

function declaredCharacterText(text) {
  return String(text || '')
    .split(/\r?\n/)
    .filter((line) => /^\s*人物\s*[：:]/.test(line))
    .join('\n');
}

// 剧本场次标题写法很多，必须都认：
//   场1-1 内·南天门城管大队·办事大厅·日   ← 本项目实际格式
//   场次1：… / 场景1：… / 第1场 … / 地点：…
// 只认「场次N：」会让本集解析出 0 场→场景资产被整组过滤（画布场景不显示、也没有场景连线）。
const SCENE_HEADING_RES = [
  /^(?:场次|场景)\s*[0-9０-９]+\s*[：:]/,
  /^场\s*[0-9０-９]+(?:[-—－.][0-9０-９]+)?(?:\s|$)/,
  /^第\s*[0-9０-９]+\s*场(?:\s|$|[：:])/,
];
const isSceneHeading = (line) => SCENE_HEADING_RES.some((re) => re.test(line));

function parseEpisodeSceneGroups(text) {
  const groups = [];
  let current = null;
  for (const line of String(text || '').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (isSceneHeading(trimmed)) {
      current = {labels: [trimmed]};
      groups.push(current);
      continue;
    }
    if (/^地点\s*[：:]/.test(trimmed)) {
      if (!current) {
        current = {labels: []};
        groups.push(current);
      }
      current.labels.push(trimmed);
    }
  }
  return groups;
}

function preferredSceneLabel(group) {
  const labels = Array.isArray(group?.labels) ? group.labels : [];
  return labels.find((label) => /^地点\s*[：:]/.test(label)) || labels[0] || '';
}

function sceneLabelSimilarity(left, right) {
  const a = normalizeAssetText(left).replace(/^(?:场次[0-9０-９]+|地点|场景)/, '');
  const b = normalizeAssetText(right).replace(/^(?:场次[0-9０-９]+|地点|场景)/, '');
  if (!a || !b) return 0;
  const timePenalty = (() => {
    const parenthetical = (value) => {
      const match = String(value || '').match(/[（(]([^）)]+)[）)]/);
      return match ? normalizeAssetText(match[1]) : '';
    };
    const leftTime = parenthetical(left);
    const rightTime = parenthetical(right);
    if (!leftTime || !rightTime || leftTime === rightTime) return 1;
    if ((leftTime.includes('转') || rightTime.includes('转')) && leftTime.includes('日') && rightTime.includes('日')) return 0.35;
    return 0.72;
  })();
  if (a === b) return 1;
  if (a.length >= 3 && b.length >= 3 && (a.includes(b) || b.includes(a))) return 0.94 * timePenalty;
  const grams = (value) => {
    const out = [];
    for (let index = 0; index < value.length - 1; index += 1) out.push(value.slice(index, index + 2));
    return new Set(out);
  };
  const ga = grams(a);
  const gb = grams(b);
  if (ga.size === 0 || gb.size === 0) return 0;
  let overlap = 0;
  ga.forEach((gram) => { if (gb.has(gram)) overlap += 1; });
  return ((2 * overlap) / (ga.size + gb.size)) * timePenalty;
}

function deriveEpisodeAssetScope({
  episodeBody,
  characterNames,
  sceneFiles,
  sceneNameByImage,
  propFiles,
  propLabelByFile,
  voiceFiles,
}) {
  const text = String(episodeBody || '').trim();
  if (!text) return null;

  const characterReferenceText = declaredCharacterText(text) || text;
  const allowedCharacters = new Set(characterNames.filter((name) => {
    if (assetNameMatchesText(characterReferenceText, name)) return true;
    // 闪回/背影角色可能不在「人物：」表里，但本集正文有明确身影描述时仍应纳入。
    return /(?:模糊|身影|背影)/.test(name) && assetNameMatchesText(text, name);
  }));
  const sceneGroups = parseEpisodeSceneGroups(text);
  const allowedSceneFiles = new Set();
  const sceneNameOverrides = new Map();
  const unmatchedGroups = new Set(sceneGroups.map((_, index) => index));
  const bestSceneByGroup = new Map();
  const unregisteredSceneFiles = [];

  for (const file of sceneFiles) {
    const registeredName = sceneNameByImage.get(path.normalize(file));
    if (!registeredName) {
      unregisteredSceneFiles.push(file);
      continue;
    }
    let bestGroup = -1;
    let bestScore = 0;
    sceneGroups.forEach((group, index) => {
      for (const label of group.labels) {
        const score = sceneLabelSimilarity(registeredName, label);
        if (score > bestScore) {
          bestScore = score;
          bestGroup = index;
        }
      }
    });
    if (bestGroup >= 0 && bestScore >= 0.54) {
      const previous = bestSceneByGroup.get(bestGroup);
      if (!previous || bestScore > previous.score) bestSceneByGroup.set(bestGroup, {file, score: bestScore});
    }
  }

  bestSceneByGroup.forEach(({file}, groupIndex) => {
    allowedSceneFiles.add(file);
    unmatchedGroups.delete(groupIndex);
  });

  // 旧/中断项目可能有未写入注册表的场景图。只为当前集仍未匹配的场次按顺序补位，
  // 避免把其它集未注册图片误塞进已经匹配完整的本集。
  const pendingGroups = [...unmatchedGroups].sort((a, b) => a - b);
  for (let index = 0; index < pendingGroups.length && index < unregisteredSceneFiles.length; index += 1) {
    const file = unregisteredSceneFiles[index];
    allowedSceneFiles.add(file);
    const label = preferredSceneLabel(sceneGroups[pendingGroups[index]]);
    if (label) sceneNameOverrides.set(file, label);
  }

  // 剧本一场都没解析出来时不做场景过滤：宁可多显示，也不能让本集场景整组消失
  // （场景消失会连带丢失段/镜头的场景连线，画布上表现为"场景组空、无场景连线"）。
  if (sceneGroups.length === 0) {
    for (const file of sceneFiles) allowedSceneFiles.add(file);
  }

  const allowedProps = new Set(propFiles.filter((file) => assetNameMatchesText(text, propLabelByFile.get(path.normalize(file)) || '')));
  const allowedVoices = new Set(voiceFiles.filter((file) => {
    const label = path.basename(file, path.extname(file));
    if (assetNameMatchesText(text, label)) return true;
    return [...allowedCharacters].some((name) => {
      const a = normalizeAssetText(name);
      const b = normalizeAssetText(label);
      return a.length >= 2 && b.length >= 2 && (a === b || a.includes(b) || b.includes(a));
    });
  }));

  const confident = allowedCharacters.size > 0
    || allowedSceneFiles.size > 0
    || allowedProps.size > 0
    || allowedVoices.size > 0;
  return confident ? {allowedCharacters, allowedSceneFiles, sceneNameOverrides, allowedProps, allowedVoices} : null;
}

export async function readSessionState(repoRoot) {
  const fallback = {activeSessionId: '', sessions: []};
  try {
    const payload = JSON.parse(await readFile(path.join(repoRoot, '.vimax', 'sessions.json'), 'utf8'));
    // P4：SQLite 是项目状态的唯一权威源。sessions.json 只做兼容读取——
    // 其中的 stage/summary 可能是渲染中断等场景留下的旧值（如渲染中断后残留
    // "rendering"，而真实阶段已由磁盘扫描校正为 assets_pending）。
    // 读会话列表时用状态库的权威 stage 覆盖 JSON 旧值，避免前端流程条回退到错误步骤。
    const authoritative = await readAuthoritativeProjectStages(repoRoot);
    const records = Object.values(payload.sessions ?? {})
      .filter((record) => record && typeof record === 'object')
      .map((record) => {
        const clean = sanitizeSession(record);
        const authority = authoritative.get(clean.sessionId);
        if (authority) {
          if (authority.stage) clean.stage = authority.stage;
          if (authority.summary) clean.summary = authority.summary;
          if (authority.progressStage) clean.progressStage = authority.progressStage;
        }
        return clean;
      });
    records.sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
    return {
      activeSessionId: String(payload.active_session_id ?? ''),
      sessions: records,
    };
  } catch {
    return fallback;
  }
}

/**
 * 从项目状态库（.vimax/project_state.sqlite3）读取每个会话的权威 stage/summary。
 * 状态库不存在或不可读时返回空 Map（回退 JSON 兼容值，不影响会话列表展示）。
 */
async function readAuthoritativeProjectStages(repoRoot) {
  const out = new Map();
  const snapshots = await readFactsSnapshots(repoRoot);
  const dbPath = path.join(repoRoot, '.vimax', 'project_state.sqlite3');
  try {
    await lstat(dbPath);
  } catch {
    // 没有状态库：真相快照照样生效（无库/新项目上步骤条同样要以产物为准）
    for (const [sessionId, facts] of snapshots) {
      const entry = {stage: '', summary: '', progressStage: ''};
      applyFactsToEntry(entry, facts);
      out.set(sessionId, entry);
    }
    return out;
  }
  try {
    const {DatabaseSync} = await import('node:sqlite');
    const db = new DatabaseSync(dbPath, {readOnly: true});
    try {
      const rows = db.prepare('SELECT session_id, stage, mode FROM projects').all();
      for (const row of rows) {
        const sessionId = String(row?.session_id ?? '');
        const stage = String(row?.stage ?? '').trim();
        if (!sessionId) continue;
        const entry = {stage: stage || '', summary: '', progressStage: ''};
        const facts = snapshots.get(sessionId) ?? null;
        if (facts) applyFactsToEntry(entry, facts);
        // 细粒度流程步骤：项目级 stage（如 assets_pending）粒度太粗，无法表达
        // "角色/场景已生成、道具/音色待生成"的中间进度。按 assets 表逐类型实际
        // 状态推导流程条应停在哪一步（chars→scenes→props→voices→confirm）。
        // 只有在"真相说该进资产阶段"（或没有快照）时才用库里的细粒度进度——
        // 否则会出现"清单没抽完却显示生成角色中"。
        const factsStep = facts ? String(facts.next_step) : '';
        if (!facts || factsStep === 'assets') {
          entry.progressStage = deriveProgressStage(db, sessionId, stage);
        }
        // 渲染中断兜底：render_status.json 残留 "rendering" 但从未 render_completed 时，
        // 状态库 stage 若已是渲染后的阶段（如 assets_pending）则直接信任状态库；
        // 若状态库本身仍为 rendering 且 render_status 未完成，标注为 interrupted 语义。
        const workingDir = resolveSessionRoot(repoRoot, sessionId);
        entry.summary = await renderStatusSummaryIfInterrupted(workingDir, stage);
        out.set(sessionId, entry);
      }
      // 快照独立于状态库：库里没有该项目（甚至没有 DB）时也必须生效，
      // 否则"步骤条与实际不符"会在无库/新项目上原样复现。
      for (const [sessionId, facts] of snapshots) {
        const entry = out.get(sessionId) ?? {stage: '', summary: '', progressStage: ''};
        applyFactsToEntry(entry, facts);
        out.set(sessionId, entry);
      }
      return out;
    } finally {
      try { db.close(); } catch {}
    }
  } catch (error) {
    // SQLite 不可读（损坏/权限）：保持 JSON 兼容值，不让会话列表整体失败
    console.warn('[server-lib] readAuthoritativeProjectStages 失败，回退 JSON 兼容 stage:', error?.message ?? error);
    return out;
  }
}

/**
 * 读"产物真相快照"（Python 侧 `utils/production_state.write_facts_snapshot` 写、JS 只读）。
 *
 * 为什么不让 JS 自己算：在 JS 里再写一遍"清单抽完没/资产齐没齐"就是第二套口径——实测后果是
 * 清单只抽了 3/80、状态库 stage 却是 `assets_manifest_ready`，步骤条显示"生成角色中"，
 * 而 agent 实际做的是"按集提取"。真相只有一处计算，Web 只负责显示。
 */
async function readFactsSnapshot(repoRoot, sessionId) {
  try {
    const raw = await readFile(path.join(resolveSessionRoot(repoRoot, sessionId), 'script2video', 'production_state.json'), 'utf8');
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object' && typeof parsed.next_step === 'string' && parsed.next_step) return parsed;
  } catch {
    // 没有快照（新项目/尚未跑过 agent）：调用方回退到原有的库内推导
  }
  return null;
}

/** 一次性读全部会话的真相快照 → Map(sessionId → facts)。 */
async function readFactsSnapshots(repoRoot) {
  const out = new Map();
  let payload;
  try {
    payload = JSON.parse(await readFile(path.join(repoRoot, '.vimax', 'sessions.json'), 'utf8'));
  } catch {
    return out;
  }
  for (const record of Object.values(payload.sessions ?? {})) {
    const sessionId = String(record?.session_id ?? '');
    if (!sessionId) continue;
    const facts = await readFactsSnapshot(repoRoot, sessionId);
    if (facts) out.set(sessionId, facts);
  }
  return out;
}

/** 把快照落到展示字段上：stage 用真相里的 stage，progressStage 用"该停在哪一格"。 */
function applyFactsToEntry(entry, facts) {
  entry.facts = facts;
  if (facts.stage) entry.stage = String(facts.stage);
  const stepStage = {
    script: 'narrative_planning',
    extract: 'extract_asset_manifest',
    confirm: 'confirm',
    storyboard: 'storyboard',
    done: 'rendered',
  }[String(facts.next_step)];
  if (stepStage) entry.progressStage = stepStage;
}

/**
 * 按资产表逐类型实际状态推导前端流程条应停的步骤。
 * 项目级 stage 只用于兜底（库中无资产记录时）；有资产记录时以细粒度为准：
 * 按 chars→scenes→props→voices 顺序，停在第一个仍有待生成/生成中/有问题项的类型；
 * 若所有类型均无待生成项，再判断"已生成但未确认 → confirm"。
 */
function deriveProgressStage(db, sessionId, stage) {
  let counts;
  try {
    counts = db.prepare(`
      SELECT asset_type, status, COUNT(*) AS n FROM assets WHERE session_id = ?
      GROUP BY asset_type, status
    `).all(sessionId);
  } catch {
    return '';
  }
  const byType = {};
  for (const row of counts || []) {
    const type = String(row?.asset_type ?? '');
    const status = String(row?.status ?? '');
    if (!type) continue;
    (byType[type] ??= {})[status] = Number(row?.n ?? 0);
  }
  const pendingOf = (type) => {
    const bucket = byType[type];
    if (!bucket) return 0;
    return (bucket.pending ?? 0) + (bucket.queued ?? 0) + (bucket.generating ?? 0) +
      (bucket.rejected ?? 0) + (bucket.redoing ?? 0) + (bucket.failed ?? 0);
  };
  const assetTypeStep = {characters: 'chars', scenes: 'scenes', props: 'props', voices: 'voices'};
  const typeNames = ['characters', 'scenes', 'props', 'voices'];
  for (const type of typeNames) {
    if (pendingOf(type) > 0) return assetTypeStep[type]; // 该类型仍有待生成/生成中/有问题的项
  }
  // 所有类型均无待生成项：若存在已生成但尚未确认的项 → confirm；否则回到项目级 stage
  const anyGenerated = Object.values(byType).some((bucket) => (bucket.generated ?? 0) > 0);
  const anyConfirmed = Object.values(byType).some((bucket) => (bucket.confirmed ?? 0) > 0);
  if (anyGenerated && !anyConfirmed) return 'confirm';
  // 资产已全部生成并确认：按项目级权威阶段映射下一步流程步骤（细粒度资产表
  // 已无待生成项，不能返回空——否则前端四来源全扑空、顶部流程条整体消失）。
  const stageStep = {
    assets_confirm_pending: 'confirm',   // 待确认 → 停在"等待确认资产"
    assets_confirmed: 'storyboard',      // 已确认 → 下一步"设计分镜中"
    storyboard_planning: 'storyboard',
    storyboard_ready: 'storyboard',
    shots_decomposed: 'shots',
    camera_tree_ready: 'camera',
    render_ready: 'frames',
    rendering: 'frames',
    partially_complete: 'clips',
    completed: 'concat',
  };
  if (stageStep[String(stage ?? '')]) return stageStep[String(stage)];
  return '';
}

async function renderStatusSummaryIfInterrupted(workingDir, authoritativeStage) {
  try {
    const rsPath = path.join(workingDir, 'render_status.json');
    const raw = await readFile(rsPath, 'utf8');
    const rs = JSON.parse(raw);
    if (String(rs?.status ?? '') === 'rendering' && !rs?.render_completed) {
      // 渲染进程中断（render_status 仍 rendering 且未完成）而状态库已校正阶段：
      // 前端流程条应回到真实阶段，并给出中断提示
      if (authoritativeStage && authoritativeStage !== 'rendering') {
        return '上次渲染被中断，已回到当前阶段：' + authoritativeStage;
      }
      return '渲染被中断，等待继续';
    }
  } catch {
    // render_status.json 不存在或损坏：忽略
  }
  return '';
}


const EVENT_SESSION_TEXT_LIMIT = 160;

function compactEventText(value, limit = EVENT_SESSION_TEXT_LIMIT) {
  const text = String(value ?? '');
  if (text.length <= limit) return text;
  return `${text.slice(0, Math.max(0, limit - 1))}…`;
}

// SSE 只负责侧栏同步，不应携带完整剧本正文。旧标签页重连时若反复解析
// 数十 KB 的 idea/summary，会阻塞主线程；截断非结构化长文本即可保持兼容并显著减包。
export function compactSessionStateForEvents(state) {
  const sessions = Array.isArray(state?.sessions) ? state.sessions : [];
  return {
    activeSessionId: String(state?.activeSessionId ?? ''),
    sessions: sessions.map((session) => ({
      ...session,
      idea: compactEventText(session?.idea),
      summary: compactEventText(session?.summary),
    })),
  };
}

export async function deleteSession(repoRoot, sessionId) {
  assertSessionId(sessionId);
  const statePath = path.join(repoRoot, '.vimax', 'sessions.json');
  const payload = JSON.parse(await readFile(statePath, 'utf8'));
  const sessions = payload.sessions && typeof payload.sessions === 'object' ? payload.sessions : {};
  if (!sessions[sessionId]) throw new Error('项目不存在');

  delete sessions[sessionId];
  const remaining = Object.values(sessions)
    .filter((record) => record && typeof record === 'object')
    .sort((left, right) => String(right.updated_at ?? right.created_at ?? '').localeCompare(String(left.updated_at ?? left.created_at ?? '')));
  if (payload.active_session_id === sessionId || !sessions[payload.active_session_id]) {
    payload.active_session_id = String(remaining[0]?.session_id ?? '');
  }
  payload.sessions = sessions;

  const temporaryPath = `${statePath}.${process.pid}.tmp`;
  await writeFile(temporaryPath, `${JSON.stringify(payload, null, 2)}\n`, {mode: 0o600});
  await rename(temporaryPath, statePath);
  await rm(resolveSessionRoot(repoRoot, sessionId), {recursive: true, force: true});
  await removeSessionLogRecords(repoRoot, sessionId);
  return readSessionState(repoRoot);
}

const DEFAULT_HISTORY_TURNS = 30;
const MAX_HISTORY_TURNS = 100;

export async function readSessionHistory(repoRoot, sessionId) {
  const page = await readSessionHistoryPage(repoRoot, sessionId, {limit: 120});
  return page.messages;
}

export async function readSessionHistoryPage(repoRoot, sessionId, options = {}) {
  assertSessionId(sessionId);
  const logsRoot = path.join(repoRoot, '.vimax', 'logs');
  const before = String(options.before || '').trim();
  const requestedLimit = Number(options.limit || DEFAULT_HISTORY_TURNS);
  const limit = Math.max(1, Math.min(MAX_HISTORY_TURNS, Number.isFinite(requestedLimit) ? Math.floor(requestedLimit) : DEFAULT_HISTORY_TURNS));
  try {
    // 磁盘生成产物（关键帧/候选/分段视频/成片/定妆图/场景/道具）：刷新后 history 恢复活动
    // 消息时挂回图片，避免"实时 SSE 显示的图刷新后消失"（图从未落盘到 tool_results，只在磁盘）。
    // 翻页（before）同样要收集：产物按"写入时间落在哪一轮"归属，只给第一页收集会让更早回合
    // 的分段视频、角色定妆图、场景/道具图在历史里彻底看不到。
    const renderArtifacts = await collectRenderArtifacts(repoRoot, sessionId);
    const indexedPage = await readIndexedSessionHistoryPage(logsRoot, sessionId, {
      before,
      limit,
      renderArtifacts,
      repoRoot,
    });
    if (indexedPage) return indexedPage;

    const lines = await readLoopHistoryLines(logsRoot);
    const records = [];
    for (const line of lines) {
      let record;
      try {
        record = JSON.parse(line);
      } catch {
        continue;
      }
      records.push(record);
    }

    // A tool can create a new session in the middle of a turn. The final record then lives
    // in the new session while the initial checkpoint still lives in the old one. Resolve
    // that turn to its final session so the old project does not show a duplicate user
    // message plus a fake interruption.
    const finalSessionByTurn = new Map();
    for (const record of records) {
      const turnId = String(record.turn_id || '');
      const recordSessionId = String(record.session_id || '');
      if (!turnId || !recordSessionId) continue;
      if (isFinalHistoryRecord(record)) finalSessionByTurn.set(turnId, recordSessionId);
    }

    const turns = new Map();
    for (const record of records) {
      if (record.session_id !== sessionId || !record.raw_user_input || record.status === 'superseded') continue;
      const turnId = String(record.turn_id || `turn-${turns.size}`);
      const finalSessionId = finalSessionByTurn.get(turnId);
      if (finalSessionId && finalSessionId !== sessionId) continue;
      const previous = turns.get(turnId) || {};
      turns.set(turnId, {
        ...previous,
        ...record,
        raw_user_input: record.raw_user_input || previous.raw_user_input || '',
        assistant_turns: record.assistant_turns?.length ? record.assistant_turns : previous.assistant_turns,
        tool_rounds: record.tool_rounds?.length ? record.tool_rounds : previous.tool_rounds,
        transitions: record.transitions?.length ? record.transitions : previous.transitions,
        final_assistant_text: record.final_assistant_text || previous.final_assistant_text || '',
        created_at: previous.created_at || record.created_at || record.timestamp || '',
      });
    }

    const turnIds = [...turns.keys()];
    let endIndex = turnIds.length;
    if (before) {
      const cursorIndex = turnIds.indexOf(before);
      if (cursorIndex < 0) return {messages: [], hasMore: false, nextCursor: ''};
      endIndex = cursorIndex;
    }
    const startIndex = Math.max(0, endIndex - limit);
    const selectedTurnIds = turnIds.slice(startIndex, endIndex);
    const newerRecord = endIndex < turnIds.length ? turns.get(turnIds[endIndex]) : null;
    return await buildSessionHistoryPage(turns, selectedTurnIds, {
      hasMore: startIndex > 0,
      renderArtifacts,
      repoRoot,
      sessionId,
      windowEnd: String(newerRecord?.created_at || newerRecord?.timestamp || ''),
    });
  } catch {
    return {messages: [], hasMore: false, nextCursor: ''};
  }
}

async function readIndexedSessionHistoryPage(logsRoot, sessionId, {before, limit, renderArtifacts, repoRoot}) {
  const indexPath = path.join(logsRoot, 'log_index.sqlite3');
  let database;
  try {
    const {DatabaseSync} = await import('node:sqlite');
    database = new DatabaseSync(indexPath, {readOnly: true});

    const offsetRow = database
      .prepare("SELECT offset FROM log_offsets WHERE log_name = 'loop_history'")
      .get();
    // 索引落后或日志刚轮转时回退到完整扫描，避免这里读取到旧回合。
    if (!offsetRow || !(await isHistoryIndexCurrent(logsRoot, Number(offsetRow.offset)))) return null;

    let beforeRowId = null;
    if (before) {
      const cursorRow = database
        .prepare("SELECT rowid FROM loop_turns WHERE log_name = 'loop_history' AND session_id = ? AND turn_id = ?")
        .get(sessionId, before);
      if (!cursorRow) return {messages: [], hasMore: false, nextCursor: ''};
      beforeRowId = Number(cursorRow.rowid);
    }

    const candidateRows = beforeRowId === null
      ? database
          .prepare("SELECT rowid, turn_id, data FROM loop_turns WHERE log_name = 'loop_history' AND session_id = ? ORDER BY rowid DESC LIMIT ?")
          .all(sessionId, limit + 1)
      : database
          .prepare("SELECT rowid, turn_id, data FROM loop_turns WHERE log_name = 'loop_history' AND session_id = ? AND rowid < ? ORDER BY rowid DESC LIMIT ?")
          .all(sessionId, beforeRowId, limit + 1);

    const hasMore = candidateRows.length > limit;
    const selectedRows = candidateRows.slice(0, limit).reverse();
    if (!selectedRows.length) return {messages: [], hasMore: false, nextCursor: ''};

    const finalInAnotherSession = database.prepare(`
      SELECT 1
      FROM loop_turns
      WHERE log_name = 'loop_history'
        AND session_id != ?
        AND turn_id = ?
        AND (final_assistant_text != '' OR status IN ('completed', 'failed', 'cancelled', 'halted'))
      LIMIT 1
    `);
    const turns = new Map();
    const selectedTurnIds = [];
    for (const row of selectedRows) {
      const turnId = String(row.turn_id || '');
      if (!turnId || finalInAnotherSession.get(sessionId, turnId)) continue;
      try {
        turns.set(turnId, JSON.parse(String(row.data)));
        selectedTurnIds.push(turnId);
      } catch {
        // 单条索引损坏时跳过，历史接口继续返回其余回合。
      }
    }

    // 产物归属窗口上界 = 比本页最新一轮还新的那一轮开始时间；
    // 没有更新的一轮（本页就是最新页）则留空表示上不封顶。
    let windowEnd = '';
    const newestRowId = Number(candidateRows[0]?.rowid);
    if (Number.isFinite(newestRowId)) {
      const newerRow = database
        .prepare("SELECT data FROM loop_turns WHERE log_name = 'loop_history' AND session_id = ? AND rowid > ? ORDER BY rowid ASC LIMIT 1")
        .get(sessionId, newestRowId);
      if (newerRow?.data) {
        try {
          const newerRecord = JSON.parse(String(newerRow.data));
          windowEnd = String(newerRecord.created_at || newerRecord.timestamp || '');
        } catch {
          // 单条索引损坏时忽略边界，产物按本页内归属处理
        }
      }
    }

    return await buildSessionHistoryPage(turns, selectedTurnIds, {
      hasMore,
      renderArtifacts,
      repoRoot,
      sessionId,
      windowEnd,
    });
  } catch {
    return null;
  } finally {
    try {
      database?.close();
    } catch {
      // 只读连接关闭失败不影响页面返回。
    }
  }
}

async function isHistoryIndexCurrent(logsRoot, offset) {
  if (!Number.isFinite(offset) || offset < 0) return false;
  const logPath = path.join(logsRoot, 'loop_history.jsonl');
  let size;
  try {
    size = (await stat(logPath)).size;
  } catch {
    return offset === 0;
  }
  if (offset === size) return true;
  const trailingBytes = size - offset;
  // log_store 的历史版本有时只差最终换行符；只允许这种纯空白尾部继续使用索引。
  if (trailingBytes <= 0 || trailingBytes > 8) return false;
  const handle = await open(logPath, 'r');
  try {
    const buffer = Buffer.alloc(trailingBytes);
    await handle.read(buffer, 0, trailingBytes, offset);
    return buffer.every((value) => value === 9 || value === 10 || value === 13 || value === 32);
  } finally {
    await handle.close();
  }
}

async function buildSessionHistoryPage(
  turns,
  selectedTurnIds,
  {hasMore, renderArtifacts = [], repoRoot, sessionId, windowEnd = ''},
) {
  const turnMessages = [];
  // 每轮的产物归属信息：本轮开始时间 + 本轮各活动行在扁平消息数组里的下标
  const turnSpans = [];
  let flatCount = 0;
  for (const selectedTurnId of selectedTurnIds) {
    const record = turns.get(selectedTurnId);
    if (!record) continue;
    const turnId = String(record.turn_id || selectedTurnId);
    const turnTime = String(record.created_at || record.timestamp || '');
    const messages = [];
    messages.push({
      id: `${turnId}-user`,
      role: 'user',
      text: displayUserInput(record.raw_user_input),
      createdAt: turnTime,
    });
    const activityIndexes = [];
    for (const round of Array.isArray(record.tool_rounds) ? record.tool_rounds : []) {
      for (const result of Array.isArray(round.tool_results) ? round.tool_results : []) {
        activityIndexes.push(flatCount + messages.length);
        messages.push({
          id: `${turnId}-tool-${String(result.metadata?.call_id || messages.length)}`,
          role: 'activity',
          text: historyToolResultText(result),
          tool: String(result.name || 'tool'),
          status: result.ok === false ? 'error' : 'done',
          stage: result.ok === false ? 'failed' : 'completed',
          createdAt: turnTime,
        });
      }
    }
    if (record.final_assistant_text) {
      messages.push({
        id: `${turnId}-assistant`,
        role: record.status === 'failed' ? 'error' : 'assistant',
        text: await repairHistoryArtifactLinks(
          stripTransientHistoryMarkdown(record.final_assistant_text),
          repoRoot,
          sessionId,
        ),
        createdAt: turnTime,
      });
    } else if (record.status === 'running') {
      // 被中断的 turn：无最终回复（进程被杀），给明确占位，避免用户误以为记录丢失
      messages.push({
        id: `${turnId}-interrupted`,
        role: 'assistant',
        text: '（上次任务被中断，下次启动智能体将自动恢复执行；已完成的产物会自动跳过，不会重复生成）',
        createdAt: turnTime,
      });
    }
    turnSpans.push({startMs: historyTimeMs(turnTime), activityIndexes});
    turnMessages.push(messages);
    flatCount += messages.length;
  }
  const messages = turnMessages.flat();
  attachRenderArtifactsToTurns(messages, turnSpans, renderArtifacts, historyTimeMs(windowEnd));
  return {
    messages,
    hasMore,
    nextCursor: selectedTurnIds[0] || '',
  };
}

/**
 * 解析历史时间戳为毫秒。turn 记录里是本地时间字符串（无时区后缀），磁盘产物的
 * updatedAt 是 UTC ISO 串；两者必须换算到同一口径再比较——直接比字符串会因时区错位
 * （东八区 UTC 06:58 <= 本地 14:55 恒成立）把所有产物都算成"更早就存在"。
 */
function historyTimeMs(value) {
  const text = String(value || '').trim();
  if (!text) return Number.NaN;
  const parsed = Date.parse(text);
  return Number.isNaN(parsed) ? Number.NaN : parsed;
}

/** 生成类活动行优先承载产物（渲染/生成/拆解/资产/评审），没有则挂本轮最后一个活动行。 */
const ARTIFACT_HOST_TOOL_RE = /render|regenerat|generat|decompos|asset|critique|planning/i;

function pickArtifactHostMessage(messages, activityIndexes) {
  for (let index = activityIndexes.length - 1; index >= 0; index -= 1) {
    const message = messages[activityIndexes[index]];
    if (ARTIFACT_HOST_TOOL_RE.test(String(message?.tool || ''))) return activityIndexes[index];
  }
  return activityIndexes[activityIndexes.length - 1];
}

/**
 * 把磁盘上的生成产物挂回产生它的那一轮活动消息。
 *
 * 归属只看写入时间落在哪一轮（[本轮开始, 下一轮开始)），不再按工具名判断：此前只认名字里
 * 含 "render" 的工具，于是 vimax_regenerate / vimax_generate_assets 这类生成工具产出的单个
 * 分段视频、角色定妆图、场景图、道具图全都挂不上，历史里永远看不到（用户反馈"还有很多生成
 * 的历史记录都不显示"）。时间比较也必须走 historyTimeMs，否则产物会成堆挂在第一个渲染回合。
 */
function attachRenderArtifactsToTurns(messages, turnSpans, renderArtifacts, windowEndMs) {
  if (!renderArtifacts.length || !messages.length) return;
  const knownStarts = turnSpans.map((span) => span.startMs).filter((value) => Number.isFinite(value));
  const pageStartMs = knownStarts.length ? Math.min(...knownStarts) : Number.NaN;
  const attach = (index, artifact) => {
    const target = messages[index];
    if (!target) return;
    if (!target.artifacts) target.artifacts = [];
    if (!target.artifacts.some((item) => item.path === artifact.path)) target.artifacts.push(artifact);
  };
  const resolveOwner = (artifact) => {
    const createdMs = historyTimeMs(artifact.updatedAt);
    if (!Number.isFinite(createdMs)) return -1;
    // 早于本页第一轮 ⇒ 属于更早的一页；不早于下一轮 ⇒ 属于更新的一页（由那一页挂回）
    if (Number.isFinite(pageStartMs) && createdMs < pageStartMs) return -1;
    if (Number.isFinite(windowEndMs) && createdMs >= windowEndMs) return -1;
    let owner = -1;
    for (let index = 0; index < turnSpans.length; index += 1) {
      const startMs = turnSpans[index].startMs;
      if (!Number.isFinite(startMs) || startMs <= createdMs) owner = index;
    }
    // 时间戳全缺（老日志没有 created_at）时退化为本页第一轮，避免产物彻底丢失
    return owner < 0 ? 0 : owner;
  };
  // resolveOwner 给的是回合下标，落到消息上还要选该回合的承载活动行
  const attachToTurn = (turnIndex, artifact) => {
    const activityIndexes = turnSpans[turnIndex].activityIndexes;
    if (!activityIndexes.length) return -1;
    const messageIndex = pickArtifactHostMessage(messages, activityIndexes);
    attach(messageIndex, artifact);
    return messageIndex;
  };
  const ownerByVideoPath = new Map();
  const posters = [];
  for (const artifact of renderArtifacts) {
    // 缩略图单独延后处理：它必须跟着对应的视频走（见下），不能按自己的写入时间落位
    if (/\.poster\.jpg$/i.test(artifact.path)) {
      posters.push(artifact);
      continue;
    }
    const turnIndex = resolveOwner(artifact);
    if (turnIndex < 0) continue;
    const messageIndex = attachToTurn(turnIndex, artifact);
    if (messageIndex >= 0) ownerByVideoPath.set(artifact.path, messageIndex);
  }
  for (const poster of posters) {
    // 并发渲染时缩略图与视频的写入时间会跨回合（实测视频 14:58~15:04、缩略图 16:04），
    // 按时间落位会让缩略图和视频分属两条活动行，对话里就配不出封面图。
    const videoPath = poster.path.replace(/\.poster\.jpg$/i, '');
    const known = ownerByVideoPath.get(videoPath);
    if (known !== undefined) {
      attach(known, poster);
      continue;
    }
    const turnIndex = resolveOwner(poster);
    if (turnIndex < 0) continue;
    attachToTurn(turnIndex, poster);
  }
}


/**
 * 历史回合可能保存过渲染快照的 Markdown 图片。快照目录会在渲染结束后清理，
 * 继续渲染这些旧链接只会产生 404 和破图；只过滤临时快照，不影响正式产物图片。
 */
function stripTransientHistoryMarkdown(value) {
  return String(value || '')
    .replace(/!\[[^\]]*\]\([^)]*(?:\.|%2E)partial_render_[^)]*\)/gi, '')
    .replace(/(?:[^\s`"'()]*[/\\])?\.partial_render_[A-Za-z0-9_-]+/gi, '临时渲染快照')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * 旧项目改名或迁移后，历史回复里可能残留旧 session_id 的产物链接。
 * 当前会话存在同路径文件时重绑；旧会话仍可用时保留；两边都不存在则移除破图。
 */
async function repairHistoryArtifactLinks(value, repoRoot, sessionId) {
  const text = String(value || '');
  const matches = [...text.matchAll(/!\[([^\]]*)\]\((\/api\/artifact\?session=([^&)]+)&path=([^)]+))\)/g)];
  if (!matches.length) return text;

  const replacements = new Map();
  for (const match of matches) {
    const [full, alt, , encodedSession, encodedPath] = match;
    let oldSession;
    let relativePath;
    try {
      oldSession = decodeURIComponent(encodedSession);
      relativePath = decodeURIComponent(encodedPath);
    } catch {
      continue;
    }
    if (oldSession === sessionId) continue;

    let replacement = '';
    try {
      const currentFile = resolveArtifactPath(repoRoot, sessionId, relativePath);
      const currentInfo = await stat(currentFile).catch(() => null);
      if (currentInfo?.isFile()) {
        replacement = `![${alt}](/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodedPath})`;
      } else {
        const oldFile = resolveArtifactPath(repoRoot, oldSession, relativePath);
        const oldInfo = await stat(oldFile).catch(() => null);
        if (oldInfo?.isFile()) replacement = full;
      }
    } catch {
      replacement = '';
    }
    replacements.set(full, replacement);
  }

  let repaired = text;
  for (const [full, replacement] of replacements) repaired = repaired.split(full).join(replacement);
  return repaired
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * 生成产物所在目录（小写目录名，出现在路径的任意一层即可）。
 *
 * 这里必须覆盖全部生成目录，不能只挑几个：此前只放行 shots/segments/clips/final_video，
 * 结果角色定妆图 character_portraits/、场景图 scenes/、道具图 props/ 生成了却完全不在
 * 对话历史里出现（产物面板能看到），用户反馈的"很多生成的历史记录都不显示"即此。
 */
const GENERATED_MEDIA_DIRS = new Set([
  'shots', // 镜头首帧/候选帧
  'segments', // 段级直出视频（当前渲染主流程：segments/seg-N/video.mp4）
  'video_clips',
  'clips',
  'character_portraits', // vimax_generate_assets 产出的角色定妆图
  'characters',
  'scenes', // 场景图
  'props', // 道具图
  'videos', // novel2movie 的场景视频
  'keyframes',
  'first_frames',
  'storyboard_images',
]);

/** 判断相对路径是否为生成产物（生成目录下的媒体文件，或 final_video*）。 */
function isGeneratedMediaPath(relativePath) {
  if (/(^|\/)final_video/.test(relativePath)) return true;
  const segments = relativePath.split('/');
  // 最后一段是文件名，只看目录层
  return segments.slice(0, -1).some((segment) => GENERATED_MEDIA_DIRS.has(segment.toLowerCase()));
}

/**
 * 收集会话工作区下的生成产物（关键帧/候选帧/分段视频/成片/角色定妆图/场景/道具），
 * 供 history 恢复时挂回活动消息。 */
async function collectRenderArtifacts(repoRoot, sessionId) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const artifacts = [];
  async function walk(directory) {
    let entries;
    try {
      entries = await readdir(directory, {withFileTypes: true});
    } catch {
      return;
    }
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await walk(absolute);
        continue;
      }
      if (!entry.isFile()) continue;
      const extension = path.extname(entry.name).toLowerCase();
      const isImage = IMAGE_EXTENSIONS.has(extension);
      const isVideo = VIDEO_EXTENSIONS.has(extension);
      if (!isImage && !isVideo) continue;
      const relativePath = path.relative(sessionRoot, absolute).split(path.sep).join('/');
      if (!isGeneratedMediaPath(relativePath)) continue;
      const info = await stat(absolute);
      artifacts.push({
        path: relativePath,
        name: entry.name,
        kind: isImage ? 'image' : 'video',
        updatedAt: info.mtime.toISOString(),
        url: `/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relativePath)}`,
      });
    }
  }
  await walk(sessionRoot);
  // 排序：镜头产物（shots/N/…）按镜头 idx 升序、段级直出产物（segments/seg-N/…）按段号升序，
  // 非镜头产物（video_clips/clips/final_video 等）排最后，按时间序。
  // 视频片段是并发生成的，完成时间≠镜头/段顺序，按 mtime 排序会导致产物列表乱序（如 17→8→16→14）。
  const orderOf = (relativePath) => {
    const shot = /(^|\/)shots\/(\d+)\//.exec(relativePath);
    if (shot) return [0, Number(shot[2])];
    const segment = /(^|\/)segments\/seg-(\d+)\//i.exec(relativePath);
    if (segment) return [1, Number(segment[2])];
    return [2, Number.MAX_SAFE_INTEGER];
  };
  return artifacts.sort((left, right) => {
    const [leftGroup, leftIndex] = orderOf(left.path);
    const [rightGroup, rightIndex] = orderOf(right.path);
    if (leftGroup !== rightGroup) return leftGroup - rightGroup;
    if (leftGroup !== 2 && leftIndex !== rightIndex) return leftIndex - rightIndex;
    return left.updatedAt.localeCompare(right.updatedAt);
  });
}

export async function storeWorkspaceUpload(repoRoot, sessionId, fileName, data) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const sessionInfo = await stat(sessionRoot);
  if (!sessionInfo.isDirectory()) throw new Error('会话工作区不是目录');

  const safeName = validateUploadName(fileName);
  const uploadRoot = path.join(sessionRoot, 'uploads');
  await mkdir(uploadRoot, {recursive: true, mode: 0o700});
  const uploadRootInfo = await lstat(uploadRoot);
  if (!uploadRootInfo.isDirectory() || uploadRootInfo.isSymbolicLink()) {
    throw new Error('工作区上传目录不安全');
  }

  const payload = Buffer.isBuffer(data) ? data : Buffer.from(data);
  // 用**内容指纹**做唯一身份（12 位十六进制前缀 + 原名）：
  //  · 同名不同内容不再互相顶替（旧实现会在同名时生成 `X (2).md` / `X (3).md` 副本，
  //    实测多份同名副本会让 agent 分不清该读哪一份、也让用户分不清哪份是新的）；
  //  · 同一份文件重复上传直接复用同一路径，不再堆副本；
  //  · 路径仍然是"相对本项目"的（项目绑定不变），前端与 agent 拿到的 `path` 就是唯一身份。
  const digest = createHash('sha256').update(payload).digest('hex').slice(0, 12);
  const storedName = `${digest}-${safeName}`;
  const destination = path.join(uploadRoot, storedName);
  const relativePath = path.relative(sessionRoot, destination).split(path.sep).join('/');
  let reused = false;
  let size = payload.byteLength;
  try {
    const existing = await stat(destination);
    if (existing.isFile()) {
      reused = true;
      size = existing.size || payload.byteLength;
    }
  } catch {
    reused = false; // 还没有这份内容：照常写入
  }
  if (!reused) {
    await writeFile(destination, payload, {flag: 'wx', mode: 0o600});
  }
  return {name: safeName, storedName, path: relativePath, size, hash: digest, reused};
}

/**
 * `--new-session` 启动的 agent：服务端 spawn 时还不知道它的会话 id（先用 `new-...` 占位），
 * 只能等它自己把会话写进 `.vimax/sessions.json`。这里给出**唯一认领判据**，供 server.mjs 轮询：
 *
 * 1. 候选会话必须存在，且**没有**被别的 record 占用（占用即放弃，避免把 B 的 id 认成 A → 消息串台）；
 * 2. 候选会话的创建时间必须不早于本次 spawn（容忍 `toleranceMs`）：`.vimax/sessions.json` 的
 *    active_session_id 是**全局单值**，另一个项目的 agent 刚启动时它指向的是**别人的**项目，
 *    直接采用就会把本进程注册到别人的 key 上（旧实现踩过这个坑，也是"新建项目被算成旧项目在跑"的根因）；
 * 3. 返回创建时间最早的那个合格候选（一次只该有一个新会话）。
 *
 * 返回 '' 表示暂时还不能认领。
 */
export function pickNewSessionId(sessions, {spawnedAtMs = 0, occupied = new Set(), toleranceMs = 5000} = {}) {
  if (!Array.isArray(sessions)) return '';
  const floor = Number(spawnedAtMs || 0) - Number(toleranceMs || 0);
  const candidates = [];
  for (const session of sessions) {
    const id = String(session?.sessionId || '').trim();
    if (!id || occupied.has(id)) continue;
    const createdAt = Date.parse(String(session?.createdAt || session?.created_at || ''));
    if (!Number.isFinite(createdAt)) continue;
    if (floor > 0 && createdAt < floor) continue;
    candidates.push({id, createdAt});
  }
  if (candidates.length === 0) return '';
  candidates.sort((left, right) => left.createdAt - right.createdAt);
  return candidates[0].id;
}

const WORKSPACE_UPLOAD_BLOCK_RE = /<workspace_uploads>([\s\S]*?)<\/workspace_uploads>/;

/**
 * 解析用户消息尾部的 `<workspace_uploads>`（前端 `composeAgentPrompt` 产出，内容是相对会话
 * 工作区的路径数组）。解析失败一律返回空数组：宁可放过，也不要拿猜测去拦用户消息。
 */
export function extractWorkspaceUploadPaths(text) {
  const match = WORKSPACE_UPLOAD_BLOCK_RE.exec(String(text || ''));
  if (!match) return [];
  let parsed;
  try {
    parsed = JSON.parse(match[1]);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.map((value) => String(value ?? '').trim()).filter(Boolean);
}

/**
 * 校验上传引用确实存在于该会话工作区，返回**读不到**的路径（空数组 = 全部存在）。
 *
 * 病根（实测事故）：新建项目后立刻上传剧本，文件落进了上一个项目的 uploads/，消息却发到新
 * 项目——`<workspace_uploads>` 里的相对路径在新项目里不存在，agent 只能报 File not found，
 * 白烧一轮对话。路径是"相对某个会话工作区"的，跨项目引用必然失效，所以要在派发给 agent 之前
 * 就拦下来，并明确告诉用户去哪儿重新上传。
 *
 * 只认 `uploads/` 前缀（且必须落在该会话的 uploads/ 目录内）：其它前缀不可能是上传件，
 * 同样报缺失，避免把任意相对路径当上传件放行。
 */
export async function missingWorkspaceUploads(repoRoot, sessionId, paths) {
  const uploadRoot = path.join(resolveSessionRoot(repoRoot, sessionId), 'uploads');
  const missing = [];
  for (const raw of paths) {
    const rel = String(raw || '').replace(/\\/g, '/').trim();
    const target = rel.startsWith('uploads/') ? path.resolve(uploadRoot, '..', rel) : '';
    if (!target || !target.startsWith(`${uploadRoot}${path.sep}`)) {
      missing.push(rel);
      continue;
    }
    try {
      const info = await stat(target);
      if (!info.isFile() || info.size === 0) missing.push(rel);
    } catch {
      missing.push(rel);
    }
  }
  return missing;
}

function historyToolResultText(result) {
  if (result.ok !== false) return '已完成';
  const content = String(result.content || '工具执行失败').trim();
  try {
    const payload = JSON.parse(content);
    const detail = payload?.error ?? payload?.message;
    if (detail) return conciseText(detail);
  } catch {
    // 供应商可能返回纯文本错误。
  }
  return conciseText(content);
}

function conciseText(value) {
  const text = String(value || '工具执行失败').replace(/\s+/g, ' ').trim();
  return text.length > 280 ? `${text.slice(0, 277)}…` : text;
}

function displayUserInput(value) {
  return String(value || '')
    .replace(/\s*<workspace_uploads>.*<\/workspace_uploads>\s*$/s, '')
    .trim();
}

/**
 * 读取用量记录里的 shot_video 条目，得到每个镜头的真实生产信息（时长/清晰度/模型）。
 * 只在产物列表请求时解析，且只保留最后一次记录，避免长项目反复扫描造成开销。
 */
async function readShotVideoProduction(usageFiles, fallbackVideoModel = '') {
  const map = new Map();
  for (const file of usageFiles) {
    let text;
    try {
      text = await readFile(file, 'utf8');
    } catch {
      continue;
    }
    for (const line of text.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.includes('"video"')) continue;
      let record;
      try {
        record = JSON.parse(trimmed);
      } catch {
        continue;
      }
      if (!record || record.kind !== 'video') continue;
      const meta = record.meta && typeof record.meta === 'object' ? record.meta : {};
      if (String(meta.purpose || '') !== 'shot_video') continue;
      const shotIdx = Number(meta.shot_idx);
      if (!Number.isInteger(shotIdx) || shotIdx < 0) continue;
      const rawModel = String(record.model || meta.model || '').trim() || fallbackVideoModel;
      const presentation = rawModel ? resolveModelPresentation(rawModel) : null;
      const duration = Number(record.duration);
      map.set(shotIdx, {
        duration: Number.isFinite(duration) ? duration : 0,
        resolution: String(record.resolution || '').trim(),
        modelName: presentation ? presentation.name : '',
        modelRef: presentation ? presentation.ref : '',
      });
    }
  }
  return map;
}

export async function listSessionArtifacts(repoRoot, sessionId, options = {}) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const artifacts = [];
  const usageFiles = [];
  // P1-6：产物上限不再是"静默截断"——达到上限时置 truncated=true，由调用方提示用户；
  // 支持 {page, pageSize} 分页（按 final 排序后的全量切页），默认不分页返回全部（兼容旧调用方）。
  const MAX_ARTIFACTS = 20000;
  let truncated = false;

  async function walk(directory) {
    let entries;
    try {
      entries = await readdir(directory, {withFileTypes: true});
    } catch {
      return;
    }
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await walk(absolute);
        continue;
      }
      if (!entry.isFile()) continue;
      if (entry.name === 'usage_log.jsonl') {
        usageFiles.push(absolute);
        continue;
      }
      const extension = path.extname(entry.name).toLowerCase();
      const kind = IMAGE_EXTENSIONS.has(extension)
        ? 'image'
        : VIDEO_EXTENSIONS.has(extension)
          ? 'video'
          : AUDIO_EXTENSIONS.has(extension)
            ? 'audio'
            : TEXT_EXTENSIONS.has(extension)
              ? 'document'
              : null;
      if (!kind) continue;
      const info = await stat(absolute);
      const relativePath = path.relative(sessionRoot, absolute).split(path.sep).join('/');
      artifacts.push({
        path: relativePath,
        name: entry.name,
        kind,
        size: info.size,
        updatedAt: info.mtime.toISOString(),
        url: `/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relativePath)}`,
      });
      if (artifacts.length >= MAX_ARTIFACTS) {
        truncated = true;
        return;
      }
    }
  }

  await walk(sessionRoot);
  // 视频片段挂上生产信息（真实时长/清晰度/模型名），供产物卡片展示。
  if (usageFiles.length > 0) {
    const shotProduction = await readShotVideoProduction(usageFiles, await readConfiguredVideoModel(repoRoot));
    if (shotProduction.size > 0) {
      for (const artifact of artifacts) {
        if (artifact.kind !== 'video') continue;
        const match = /(?:^|\/)shots\/(\d+)\/video\.mp4$/i.exec(artifact.path);
        if (!match) continue;
        const production = shotProduction.get(Number(match[1]));
        if (production) artifact.production = production;
      }
    }
  }
  // 资产（角色立绘/形态立绘/场景/道具/音色）优先展示，其余按更新时间倒序；
  // 80 集项目 shots 产物量大，避免新渲染的片段把资产挤出列表。
  const ASSET_PREFIXES = ['script2video/character_portraits/', 'script2video/scenes/', 'script2video/props/', 'script2video/voices/'];
  artifacts.sort((left, right) => {
    const la = ASSET_PREFIXES.some((p) => left.path.startsWith(p)) ? 0 : 1;
    const ra = ASSET_PREFIXES.some((p) => right.path.startsWith(p)) ? 0 : 1;
    if (la !== ra) return la - ra;
    return right.updatedAt.localeCompare(left.updatedAt);
  });
  // P1-6：分页切页（默认 page=1/pageSize=0 表示不分页返回全部）；挂载元数据由调用方显式读取
  const {page = 1, pageSize = 0} = options;
  const total = artifacts.length;
  let result = artifacts;
  if (Number(pageSize) > 0) {
    const start = (Number(page) - 1) * Number(pageSize);
    result = artifacts.slice(Math.max(0, start), Math.max(0, start) + Number(pageSize));
  }
  result.total = total;
  result.truncated = truncated;
  result.page = Number(page);
  result.pageSize = Number(pageSize) || total;
  return result;
}

/** 集级进度聚合（P0-2）：读取各集目录下的 .progress.json 与集标题。 */
export async function readEpisodeProgress(repoRoot, sessionId) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const script2videoRoot = path.join(sessionRoot, 'script2video');
  const episodesRoot = path.join(script2videoRoot, 'episodes');
  const result = [];
  let entries;
  try {
    entries = await readdir(episodesRoot, {withFileTypes: true});
  } catch {
    return result;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
    const epDir = path.join(episodesRoot, entry.name);
    let progress = null;
    try {
      progress = JSON.parse(await readFile(path.join(epDir, '.progress.json'), 'utf8'));
    } catch {
      progress = null;
    }
    // 陈旧自愈：.progress.json 只在 agent 的几个阶段写入，剧本/分镜被重建（或渲染产物被清理）
    // 后就会与磁盘脱节——实测 ep01 记录「2 镜 / 段契约已就绪」，磁盘却是 32 镜且没有 segments.json。
    // 这里发现脱节就按磁盘重算并写回，否则台账/画布拿到的进度是假的。
    const stale = await progressIsStale(epDir, progress);
    if (stale) {
      try {
        progress = await recomputeEpisodeProgress(epDir);
      } catch { /* 重算失败则保留原值，不阻断读取 */ }
    }
    let title = '';
    try {
      // 集标题的**唯一口径**是 Python 侧 `_parse_episode_title` 写进清单的 title；
      // 只有旧清单没有 title 时才回退到剧本首行（实测：ep01 的剧本以剧名开头，
      // 首行会把集名显示成《铁西硬汉》剧本全集）。
      const manifest = JSON.parse(await readFile(path.join(script2videoRoot, 'assets_manifest.json'), 'utf8').catch(() => '{}'));
      // 注意别用 `entry` 命名：外层的目录项也叫 entry，内层同名会遮蔽 → TDZ 抛错 →
      // 被 catch 吞掉 → 标题恒为空（实测踩过）。
      const manifestEntry = (Array.isArray(manifest?.episodes) ? manifest.episodes : [])
        .find((item) => item && String(item.ep_id ?? '') === entry.name);
      title = String(manifestEntry?.title ?? '').trim().slice(0, 60);
      if (!title) {
        const scriptText = await readFile(path.join(epDir, 'script.txt'), 'utf8');
        const line = scriptText.split(/\r?\n/).find((l) => l.trim());
        title = (line || '').trim().slice(0, 60);
      }
    } catch {
      title = '';
    }
    // 按集资产状态（一集一集流程）："这一集还缺哪些资产"由 Python 侧唯一口径判定后落盘
    // asset_status.json（见 docs/rule_authority.md 的「按集资产口径」），这里只读不算——
    // JS 里再实现一遍按集资产判定就是第二套口径。文件缺失 = 未知，不替它下结论。
    let assetStatus = null;
    try {
      assetStatus = JSON.parse(await readFile(path.join(epDir, 'asset_status.json'), 'utf8'));
    } catch {
      assetStatus = null;
    }
    result.push({
      ep_id: entry.name,
      title,
      progress,
      assets_ready: typeof assetStatus?.ready === 'boolean' ? assetStatus.ready : null,
      assets_blocking: Array.isArray(assetStatus?.blocking) ? assetStatus.blocking : [],
      assets_missing: assetStatus?.missing && typeof assetStatus.missing === 'object' ? assetStatus.missing : null,
      assets_missing_count: Number.isFinite(Number(assetStatus?.missing_count)) ? Number(assetStatus.missing_count) : null,
      assets_updated_at: assetStatus?.updated_at ?? null,
      storyboard_ready: Boolean(progress?.storyboard?.ready),
      shot_count: progress?.storyboard?.shot_count ?? null,
      described_count: progress?.storyboard?.described_count ?? null,
      keyframes: progress?.keyframes?.count ?? 0,
      clips: progress?.clips?.count ?? 0,
      segments_total: progress?.segments?.total ?? progress?.segments?.segment_count ?? null,
      segments_rendered: progress?.segments?.rendered ?? null,
      // final_ready 只认"产物齐"的成片；残缺成片（缺段/缺镜）另标 partial_render，
      // 否则台账会把只渲了一半的集算进"已产出成片"（实测 ep01 7/14 段）。
      final_ready: Boolean(progress?.final_video?.ready && progress?.final_video?.complete !== false),
      final_video_exists: Boolean(progress?.final_video?.ready),
      partial_render: Boolean(progress?.final_video?.ready && progress?.final_video?.complete === false),
      render_gap: progress?.render_gap ?? null,
      final_video_url: progress?.final_video?.ready
        ? `/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(`script2video/episodes/${entry.name}/final_video.mp4`)}`
        : null,
      status: progress?.status ?? 'pending',
      updated_at: progress?.updated_at ?? null,
    });
  }
  result.sort((a, b) => a.ep_id.localeCompare(b.ep_id, undefined, {numeric: true}));
  return result;
}

/**
 * 分镜创作质量自评（utils/quality_assessment）：让 agent「自评」而非只跑合规机检。
 * 报告随分镜生成落盘 quality_report.json（与 storyboard.json 同目录）。
 * 双布局对齐：根级 script2video/quality_report.json，集级 script2video/episodes/<ep>/quality_report.json。
 */

// 维度权重（与 utils/quality_assessment.DIMENSION_WEIGHTS 保持一致，总和 1.0）
const QUALITY_DIMENSION_WEIGHTS = {
  hook: 0.15,
  cliffhanger: 0.10,
  pacing: 0.20,
  beats: 0.15,
  camera_language: 0.10,
  dialogue_density: 0.10,
  dramatic_function: 0.10,
  transition_variety: 0.05,
  continuity: 0.05,
};
const QUALITY_DIMENSION_ORDER = Object.keys(QUALITY_DIMENSION_WEIGHTS);

async function readJsonIfExists(filePath) {
  try {
    return JSON.parse(await readFile(filePath, 'utf8'));
  } catch {
    return null;
  }
}

async function mtimeOf(filePath) {
  try {
    return (await stat(filePath)).mtimeMs;
  } catch {
    return 0;
  }
}

function qualityReportPaths(repoRoot, sessionId, epId) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const s2v = path.join(sessionRoot, 'script2video');
  if (epId) {
    const dir = path.join(s2v, 'episodes', epId);
    return {
      dirs: [dir],
      reportCandidates: [path.join(dir, 'quality_report.json')],
      storyboardCandidates: [path.join(dir, 'storyboard.json')],
    };
  }
  return {
    dirs: [s2v],
    reportCandidates: [path.join(s2v, 'quality_report.json')],
    storyboardCandidates: [path.join(s2v, 'storyboard.json')],
  };
}

/** 报告是否过期：分镜比报告更新（分镜被改过后报告未重算）。 */
async function isQualityReportStale(reportPath, storyboardPath) {
  const reportMtime = await mtimeOf(reportPath);
  const sbMtime = await mtimeOf(storyboardPath);
  return reportMtime > 0 && sbMtime > reportMtime;
}

function aggregateQualityReports(perEp) {
  const totalShots = perEp.reduce((sum, entry) => sum + (entry.report.shot_count || 0), 0);
  const totalSegs = perEp.reduce((sum, entry) => sum + (entry.report.segment_count || 0), 0);
  const passedAll = perEp.every((entry) => entry.report.passed);
  const dimensions = {};
  for (const key of QUALITY_DIMENSION_ORDER) {
    let weighted = 0;
    let weightTotal = 0;
    for (const entry of perEp) {
      const dim = entry.report.dimensions && entry.report.dimensions[key];
      if (!dim) continue;
      const w = entry.report.shot_count || 0;
      weighted += (dim.score || 0) * w;
      weightTotal += w;
    }
    dimensions[key] = {
      score: weightTotal > 0 ? Math.round((weighted / weightTotal) * 10) / 10 : 0,
      weight: QUALITY_DIMENSION_WEIGHTS[key],
    };
  }
  let overall = 0;
  for (const key of QUALITY_DIMENSION_ORDER) {
    overall += (dimensions[key].score || 0) * dimensions[key].weight;
  }
  overall = Math.round(overall * 10) / 10;
  const issueSeen = new Set();
  const suggSeen = new Set();
  const top_issues = [];
  const revision_suggestions = [];
  for (const entry of perEp) {
    for (const it of entry.report.top_issues || []) {
      if (!issueSeen.has(it)) { issueSeen.add(it); top_issues.push(it); }
    }
    for (const sg of entry.report.revision_suggestions || []) {
      if (!suggSeen.has(sg)) { suggSeen.add(sg); revision_suggestions.push(sg); }
    }
  }
  return {
    available: true,
    aggregated: true,
    overall_score: overall,
    passed: passedAll,
    gate_threshold: perEp[0]?.report?.gate_threshold ?? 70,
    shot_count: totalShots,
    segment_count: totalSegs,
    dimensions,
    summary: `已聚合 ${perEp.length} 集质量自评（总分 ${overall}，${passedAll ? '全部达标' : '存在未达标集'}）`,
    top_issues: top_issues.slice(0, 12),
    revision_suggestions: revision_suggestions.slice(0, 12),
  };
}

export async function readQualityReport(repoRoot, sessionId, epId = null) {
  const {dirs, reportCandidates} = qualityReportPaths(repoRoot, sessionId, epId);
  if (epId) {
    const reportPath = reportCandidates[0];
    const report = await readJsonIfExists(reportPath);
    if (!report) return {available: false, session_id: sessionId, ep_id: epId};
    const stale = await isQualityReportStale(reportPath, path.join(dirs[0], 'storyboard.json'));
    return {available: true, ...report, report_path: reportPath, stale};
  }
  const rootReport = await readJsonIfExists(reportCandidates[0]);
  if (rootReport) {
    const stale = await isQualityReportStale(reportCandidates[0], path.join(dirs[0], 'storyboard.json'));
    return {available: true, ...rootReport, report_path: reportCandidates[0], stale};
  }
  const epDir = path.join(resolveSessionRoot(repoRoot, sessionId), 'script2video', 'episodes');
  let names = [];
  try {
    names = readdirSync(epDir).filter((n) => /^ep/i.test(n) || /^\d+$/.test(n));
  } catch {
    names = [];
  }
  const perEp = [];
  for (const name of names) {
    const rp = path.join(epDir, name, 'quality_report.json');
    const r = await readJsonIfExists(rp);
    if (r) perEp.push({epId: name, report: r});
  }
  if (perEp.length === 0) return {available: false, session_id: sessionId};
  return aggregateQualityReports(perEp);
}

/**
 * 定位质量重算目标：返回单个 {storyboardPath, reportPath}（或概览模式的 {multi:true, eps}）。
 * 供 Web 后端「重新评估」按钮调用——spawn venv python 跑 quality_assessment CLI 落盘报告。
 */
export function resolveQualityTargets(repoRoot, sessionId, epId) {
  const {dirs, reportCandidates, storyboardCandidates} = qualityReportPaths(repoRoot, sessionId, epId);
  for (let i = 0; i < dirs.length; i += 1) {
    if (existsSync(storyboardCandidates[i])) {
      return {storyboardPath: storyboardCandidates[i], reportPath: reportCandidates[i]};
    }
  }
  if (!epId) {
    const epDir = path.join(resolveSessionRoot(repoRoot, sessionId), 'script2video', 'episodes');
    const eps = [];
    try {
      for (const name of readdirSync(epDir)) {
        const sb = path.join(epDir, name, 'storyboard.json');
        if (existsSync(sb)) eps.push({epId: name, storyboardPath: sb, reportPath: path.join(epDir, name, 'quality_report.json')});
      }
    } catch {
      /* 无集目录 */
    }
    if (eps.length) return {multi: true, eps};
  }
  return null;
}

// ---------- 运行时配置：创作模式（自主 / 协作） ----------
// 与 agent.local.yaml 解耦，单独持久化，仅影响 agent 启动时的 VIMAX_AUTONOMOUS 环境变量。
function runtimeConfigPath(repoRoot) {
  return path.join(repoRoot, 'configs', 'runtime.local.json');
}

export async function readRuntimeConfig(repoRoot) {
  try {
    const cfg = JSON.parse(await readFile(runtimeConfigPath(repoRoot), 'utf8'));
    return {autonomous_mode: Boolean(cfg && cfg.autonomous_mode)};
  } catch {
    return {autonomous_mode: false};
  }
}

export async function writeRuntimeConfig(repoRoot, input) {
  const value = {autonomous_mode: Boolean(input && input.autonomous_mode)};
  const cfgPath = runtimeConfigPath(repoRoot);
  // 保留不在此处管理的运行时键（如 feed_prompt_char_budget 投喂字数活配）：
  // 否则前端一拨「创作模式」开关，就会把其他活配静默清空。
  let existing = {};
  try {
    const parsed = JSON.parse(await readFile(cfgPath, 'utf8'));
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) existing = parsed;
  } catch {
    /* 文件缺失/损坏 → 从空对象重建 */
  }
  const merged = {...existing, ...value};
  await mkdir(path.dirname(cfgPath), {recursive: true});
  const tmp = `${cfgPath}.${process.pid}.tmp`;
  await writeFile(tmp, JSON.stringify(merged, null, 2), {mode: 0o600});
  await rename(tmp, cfgPath);
  return merged;
}

/** 台账聚合（Ledger）：把 production_bible + 剧本 + 资产清单 + 分镜/渲染状态 + 集进度汇总成一份全剧总览。 */
export async function readLedger(repoRoot, sessionId) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const s2v = path.join(sessionRoot, 'script2video');
  const ledger = {
    meta: {session_id: sessionId},
    bible: null,
    script: null,
    assets: {characters: null, portraits: null, scenes_props: null, voices: null, manifest: null},
    storyboard: null,
    render: null,
    episodes: [],
  };
  // 项目名（台账"项目"卡应显示它，而不是根剧本首行——单集脚本被误写进根文件时，
  // 首行会变成"第 26 集：立案"，看起来整个项目变成了第 26 集）。
  try {
    const payload = JSON.parse(await readFile(path.join(repoRoot, '.vimax', 'sessions.json'), 'utf8'));
    ledger.meta.project_name = String(payload.sessions?.[sessionId]?.project_name ?? '');
  } catch {
    ledger.meta.project_name = '';
  }

  // 1) 全剧宪法（风格/影调/色彩/声音/节奏）
  try {
    ledger.bible = JSON.parse(await readFile(path.join(s2v, 'production_bible.json'), 'utf8'));
  } catch { /* 未生成 */ }

  // 1.5) 出稿机检门禁报告（utils/script_gate 写盘，此前只作为一次性进度消息出现，
  // 没有任何界面能回看——FAIL 项（合规红线词 / 台词覆盖 / 分镜改写剧本）看不见就改不了。
  const readGate = async (dir) => {
    try {
      const gate = JSON.parse(await readFile(path.join(dir, 'script_gate_report.json'), 'utf8'));
      if (typeof gate !== 'object' || gate === null) return null;
      // 旧版报告缺 fails 键（utils/script_gate 曾漏回传该字段）：此时 passed=false
      // 但列不出任何原因，必须标 legacy 让界面提示"重跑后可得完整结果"，
      // 而不是显示一个没有理由的"未通过"。
      const legacy = !Array.isArray(gate.fails);
      return {
        passed: gate.passed === true,
        legacy,
        fails: legacy ? [] : gate.fails.map((x) => String(x)),
        warns: Array.isArray(gate.warns) ? gate.warns.map((x) => String(x)) : [],
        stats: gate.stats && typeof gate.stats === 'object' ? gate.stats : null,
      };
    } catch {
      return null;
    }
  };
  ledger.script_gate = await readGate(s2v);

  // 2) 剧本摘要
  try {
    const scriptText = await readFile(path.join(s2v, 'script.txt'), 'utf8');
    const lines = scriptText.split(/\r?\n/);
    const first = lines.find((l) => l.trim());
    ledger.script = {
      title: (first || '').trim().slice(0, 80),
      lines: lines.length,
      chars: scriptText.replace(/\s/g, '').length,
    };
  } catch { /* 未生成 */ }

  // 3) 资产清单 —— 统计口径与 /api/canvas 一致（扫磁盘实况目录），JSON 仅补充语义名
  const listDirNames = async (dir, filter) => {
    try {
      const entries = await readdir(dir, {withFileTypes: true});
      return entries.filter(filter).map((e) => e.name);
    } catch {
      return [];
    }
  };
  // 角色卡（名字表）
  try {
    const c = JSON.parse(await readFile(path.join(s2v, 'characters.json'), 'utf8'));
    const arr = Array.isArray(c) ? c : (Array.isArray(c?.characters) ? c.characters : []);
    ledger.assets.characters = {
      count: arr.length,
      names: arr.map((x) => x.name ?? x.character_name ?? x.identifier_in_scene ?? '').filter(Boolean),
    };
  } catch {
    ledger.assets.characters = {count: 0, names: []};
  }
  // 计划（来自资产清单 manifest）：必须和"已生成"分开显示——否则清单里 80 个场景、
  // 101 个道具，台账只显示已生成的 1 张场景图 / 7 条登记，看起来像"数据不对"（实测 2026-10-17）。
  ledger.assets.plan = null;
  try {
    const manifest = JSON.parse(await readFile(path.join(s2v, 'assets_manifest.json'), 'utf8'));
    const asArray = (value) => (Array.isArray(value) ? value : []);
    ledger.assets.plan = {
      characters: asArray(manifest?.characters).length,
      scenes: asArray(manifest?.scenes).length,
      props: asArray(manifest?.props).length,
      voices: asArray(manifest?.voices).length,
      voice_names: asArray(manifest?.voices).map((x) => (typeof x === 'string' ? x : x?.name ?? '')).filter(Boolean),
      scene_names: asArray(manifest?.scenes)
        .map((x) => String(x?.name ?? x?.scene_name ?? x?.scene_id ?? '').trim())
        .filter(Boolean)
        .slice(0, 30),
      prop_names: asArray(manifest?.props)
        .map((x) => String(x?.name ?? x?.prop_name ?? '').trim())
        .filter(Boolean)
        .slice(0, 30),
    };
  } catch {
    ledger.assets.plan = null;
  }
  // 立绘：character_portraits/<角色目录>/ 图片数（registry JSON 兼容三种结构）
  try {
    const reg = JSON.parse(await readFile(path.join(s2v, 'character_portraits_registry.json'), 'utf8'));
    const regEntries = Array.isArray(reg) ? reg : (Array.isArray(reg.characters) ? reg.characters : Array.isArray(reg.portraits) ? reg.portraits : []);
    const regCount = regEntries.length;
    const byCharReg = {};
    for (const e of regEntries) {
      const name = e.name ?? e.character_name ?? e.character ?? '未知';
      const files = Array.isArray(e.files) ? e.files.length : (Array.isArray(e.images) ? e.images.length : 1);
      byCharReg[name] = (byCharReg[name] ?? 0) + files;
    }
    if (regCount > 0) {
      ledger.assets.portraits = {count: regCount, by_char: byCharReg};
    } else {
      throw new Error('registry 为空，回退目录扫描');
    }
  } catch {
    const charDirs = await listDirNames(path.join(s2v, 'character_portraits'), (e) => e.isDirectory());
    charDirs.sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    const byChar = {};
    for (const dirName of charDirs) {
      const imgs = await listDirNames(path.join(s2v, 'character_portraits', dirName), (e) => e.isFile() && IMAGE_EXTENSIONS.has(path.extname(e.name).toLowerCase()));
      const cleanName = dirName.replace(/^\d+_/, '') || dirName;
      byChar[cleanName] = (byChar[cleanName] ?? 0) + imgs.length;
    }
    // 只算"真的有图"的角色：空目录（今天那次立绘全失败留下的 5 个空目录）不算已生成立绘，
    // 否则台账会显示"5 张立绘"，点开每张都是 ×0（实测 2026-10-17）。
    const withImages = Object.entries(byChar).filter(([, files]) => Number(files) > 0);
    ledger.assets.portraits = {
      count: withImages.length,
      by_char: Object.fromEntries(withImages),
      missing: Object.entries(byChar).filter(([, files]) => Number(files) === 0).map(([name]) => name),
    };
  }
  // 场景：scenes/ 目录图片数（scene_props.json 兼容多场景/单场景结构，仅补充名字）
  try {
    const sp = JSON.parse(await readFile(path.join(s2v, 'scene_props.json'), 'utf8'));
    const scenesArr = Array.isArray(sp?.scenes) ? sp.scenes : (sp?.scene ? [sp.scene] : []);
    const propsArr = Array.isArray(sp?.props) ? sp.props : (Array.isArray(sp?.prop_visuals) ? sp.prop_visuals : []);
    const sceneNames = scenesArr.map((s) => s.name ?? s.scene_name ?? '').filter(Boolean);
    const propNames = propsArr.map((p) => p.name ?? p.prop_name ?? '').filter(Boolean);
    const sceneDirs = await listDirNames(path.join(s2v, 'scenes'), (e) => e.isFile() && IMAGE_EXTENSIONS.has(path.extname(e.name).toLowerCase()));
    const propFiles = await listDirNames(path.join(s2v, 'props'), (e) => e.isFile() && IMAGE_EXTENSIONS.has(path.extname(e.name).toLowerCase()));
    ledger.assets.scenes_props = {
      // "已生成"只认**图片文件**：注册表里登记了名字但没有图（今天那 7 条）不算已生成。
      scenes: sceneDirs.length,
      props: propFiles.length,
      registered_scenes: sceneNames.length,
      registered_props: propNames.length,
      scene_names: sceneNames.length > 0 ? sceneNames.slice(0, 30) : sceneDirs.map((f) => path.basename(f, path.extname(f))).slice(0, 30),
      prop_names: propNames.length > 0 ? propNames.slice(0, 30) : propFiles.map((f) => path.basename(f, path.extname(f))).slice(0, 30),
    };
  } catch {
    const sceneDirs = await listDirNames(path.join(s2v, 'scenes'), (e) => e.isFile() && IMAGE_EXTENSIONS.has(path.extname(e.name).toLowerCase()));
    const propFiles = await listDirNames(path.join(s2v, 'props'), (e) => e.isFile() && IMAGE_EXTENSIONS.has(path.extname(e.name).toLowerCase()));
    ledger.assets.scenes_props = {
      scenes: sceneDirs.length,
      props: propFiles.length,
      scene_names: sceneDirs.map((f) => path.basename(f, path.extname(f))).slice(0, 30),
      prop_names: propFiles.map((f) => path.basename(f, path.extname(f))).slice(0, 30),
    };
  }
  // 音色：voices/ 目录音频数（voice_profiles.json 兼容数组/角色名 key 对象）
  try {
    const vp = JSON.parse(await readFile(path.join(s2v, 'voice_profiles.json'), 'utf8'));
    const arr = Array.isArray(vp) ? vp : (Array.isArray(vp.voices) ? vp.voices : []);
    const names = arr.map((v) => v.name ?? v.character_name ?? v.role ?? '').filter(Boolean);
    if (arr.length === 0) throw new Error('profiles 为空，回退目录扫描');
    ledger.assets.voices = {count: arr.length, names};
  } catch {
    const voiceFiles = await listDirNames(path.join(s2v, 'voices'), (e) => e.isFile() && AUDIO_EXTENSIONS.has(path.extname(e.name).toLowerCase()));
    ledger.assets.voices = {count: voiceFiles.length, names: voiceFiles.map((f) => path.basename(f, path.extname(f)))};
  }
  // 资产清单：优先新路径 script2video/assets_manifest.json，兼容旧会话根目录
  for (const p of [path.join(s2v, 'assets_manifest.json'), path.join(sessionRoot, 'assets_manifest.json')]) {
    try {
      ledger.assets.manifest = JSON.parse(await readFile(p, 'utf8'));
      break;
    } catch { /* 尝试下一个 */ }
  }

  // 计划口径（唯一）：取**产物真相快照**里的 asset_gaps —— 这是闸门/agent 报价用的同一份数
  // （实测 2026-10-17：清单数组里是 80 场景 / 101 道具 / 6 音色，而真实缺口是
  //  135 / 103 / 37；台账显示清单数组就会和 agent 的报价完全对不上）。
  // 计划 = 缺口 + 已生成；没有快照时才退回上面的清单数组。
  try {
    const snapshot = JSON.parse(await readFile(path.join(s2v, 'production_state.json'), 'utf8'));
    const gaps = snapshot?.asset_gaps;
    if (gaps && typeof gaps === 'object') {
      const generated = {
        characters: Number(ledger.assets.portraits?.count ?? 0),
        scenes: Number(ledger.assets.scenes_props?.scenes ?? 0),
        props: Number(ledger.assets.scenes_props?.props ?? 0),
        voices: Number(ledger.assets.voices?.count ?? 0),
      };
      const names = (value) => (Array.isArray(value) ? value.map((x) => String(x)).filter(Boolean).slice(0, 30) : []);
      ledger.assets.plan = {
        characters: (Array.isArray(gaps.characters) ? gaps.characters.length : 0) + generated.characters,
        scenes: (Array.isArray(gaps.scenes) ? gaps.scenes.length : 0) + generated.scenes,
        props: (Array.isArray(gaps.props) ? gaps.props.length : 0) + generated.props,
        voices: (Array.isArray(gaps.voices) ? gaps.voices.length : 0) + generated.voices,
        char_names: names(gaps.characters),
        scene_names: names(gaps.scenes),
        prop_names: names(gaps.props),
        voice_names: names(gaps.voices),
        source: 'production_state.asset_gaps',
      };
      // 展示名单：**已生成 + 计划（缺口）**合并去重，最多列 40 条，并给出"还有 N 个未列出"。
      // 只列已生成的话，场景那栏只会显示 1 条（计划里的另外 51 个看不见）；
      // 只列前 30 条又不说明截断，会让人以为总共就这么多（实测 2026-10-17）。
      const composeDisplay = (planNames, generatedNames) => {
        const merged = [];
        for (const name of [...(generatedNames || []), ...(planNames || [])]) {
          const value = String(name ?? '').trim();
          if (value && !merged.includes(value)) merged.push(value);
        }
        // 卡片是固定高度 + 内部滚动，所以**不截断**（全部名字都能看到）；
        // 200 只是防极端项目的兜底上限，超出才提示"还有 N 个"。
        const limit = 200;
        return {names: merged.slice(0, limit), total: merged.length, hidden: Math.max(0, merged.length - limit)};
      };
      const gapList = (value) => (Array.isArray(value) ? value.map((x) => String(x)).filter(Boolean) : []);
      const generatedSceneNames = Array.isArray(ledger.assets.scenes_props?.scene_names) ? ledger.assets.scenes_props.scene_names : [];
      const generatedPropNames = Array.isArray(ledger.assets.scenes_props?.prop_names) ? ledger.assets.scenes_props.prop_names : [];
      ledger.assets.display = {
        characters: composeDisplay(gapList(gaps.characters), ledger.assets.characters?.names),
        portraits: composeDisplay([], Object.keys(ledger.assets.portraits?.by_char ?? {})),
        scenes: composeDisplay(gapList(gaps.scenes), generatedSceneNames),
        props: composeDisplay(gapList(gaps.props), generatedPropNames),
        voices: composeDisplay(gapList(gaps.voices), ledger.assets.voices?.names),
      };
    }
  } catch { /* 没有快照：保留清单数组那套兜底 */ }

  // 4) 分镜概览（storyboard 规划镜头数 + shots/ 实际落盘镜头数 + segments 段契约数）
  try {
    const sb = JSON.parse(await readFile(path.join(s2v, 'storyboard.json'), 'utf8'));
    const shots = Array.isArray(sb) ? sb : (sb.shots ?? sb.storyboard ?? []);
    ledger.storyboard = {shot_count: shots.length, ready: shots.length > 0};
  } catch {
    try {
      const dir = path.join(s2v, 'shots');
      const entries = await readdir(dir, {withFileTypes: true});
      const count = entries.filter((e) => e.isDirectory() && /^\d+$/.test(e.name)).length;
      ledger.storyboard = {shot_count: count, ready: count > 0};
    } catch {
      ledger.storyboard = {shot_count: 0, ready: false};
    }
  }
  // P1-3：规划镜头数（storyboard.json）与实际落盘镜头数（shots/ 目录）分开显示，
  // 重建/中断场景下两者不一致时不再被当作"产物丢失"。
  try {
    const dir = path.join(s2v, 'shots');
    const entries = await readdir(dir, {withFileTypes: true});
    ledger.storyboard.shot_count_on_disk = entries.filter((e) => e.isDirectory() && /^\d+$/.test(e.name)).length;
  } catch {
    ledger.storyboard.shot_count_on_disk = 0;
  }
  try {
    const seg = JSON.parse(await readFile(path.join(s2v, 'segments.json'), 'utf8'));
    const arr = Array.isArray(seg) ? seg : (seg.segments ?? []);
    ledger.storyboard.segment_count = arr.length;
  } catch { /* 未生成 */ }

  // 4.x) 多集项目：分镜/段契约/渲染状态都在 episodes/<ep>/ 下，根目录通常没有。
  // 只读根目录会让台账恒显示「0 镜 · 未就绪」（实测 32 镜的项目显示 0），
  // 用户据此判断进度会做错决定，所以这里按集聚合。
  const episodesAgg = {};
  try {
    const epEntries = (await readdir(path.join(s2v, 'episodes'), {withFileTypes: true}))
      .filter((e) => e.isDirectory() && !e.name.startsWith('.'))
      .map((e) => e.name)
      .sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    for (const epName of epEntries) {
      const epDir = path.join(s2v, 'episodes', epName);
      const item = {shot_count: 0, described_count: 0, segment_count: 0, clips: 0, ready: false, final_ready: false, render: null};
      try {
        const sb = JSON.parse(await readFile(path.join(epDir, 'storyboard.json'), 'utf8'));
        item.shot_count = (Array.isArray(sb) ? sb : (sb.shots ?? sb.storyboard ?? [])).length;
        item.ready = item.shot_count > 0;
      } catch { /* 未生成分镜 */ }
      // 分镜生成中断时 storyboard.json 的计划镜数会远大于落地镜数（实测 32 / 5），
      // 台账只报计划数会与画布对不上，这里同时给出真实完成量。
      item.described_count = await countEpisodeDescribed(epDir);
      try {
        const seg = JSON.parse(await readFile(path.join(epDir, 'segments.json'), 'utf8'));
        item.segment_count = (Array.isArray(seg) ? seg : (seg.segments ?? [])).length;
      } catch { /* 未生成段契约 */ }
      try {
        const rs = JSON.parse(await readFile(path.join(epDir, 'render_status.json'), 'utf8'));
        item.render = {
          status: rs.status ?? null,
          error_type: rs.error_type ?? null,
          missing: rs.missing ?? [],
          stage_hint: rs.stage_hint ?? null,
          timestamp: rs.timestamp ?? null,
        };
      } catch { /* 未渲染 */ }
      const finalPath = path.join(epDir, 'final_video.mp4');
      item.final_video_exists = await exists(finalPath);
      // 完整性判定收敛到 episodeRenderCoverage（★唯一权威 utils/episode_completeness）：
      // 有 final_video 但缺段/缺镜 = 残缺成片，不能记成"已成片"。
      const epCoverage = await episodeRenderCoverage(epDir);
      item.segment_rendered = epCoverage.segment_clips;
      item.segment_total = epCoverage.segment_total;
      item.partial_render = Boolean(item.final_video_exists && !epCoverage.complete);
      item.final_ready = Boolean(item.final_video_exists && epCoverage.complete);
      if (item.partial_render) item.render_gap = coverageGapMessage(epCoverage, epName);
      if (item.final_video_exists) {
        item.final_video_url = `/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(`script2video/episodes/${epName}/final_video.mp4`)}`;
      }
      item.clips = await countEpisodeClips(epDir);
      item.gate = await readGate(epDir);
      episodesAgg[epName] = item;
    }
  } catch { /* 单集项目没有 episodes/ 目录 */ }
  if (Object.keys(episodesAgg).length > 0) {
    ledger.storyboard.by_episode = episodesAgg;
    if (!ledger.storyboard.ready) {
      const sum = Object.values(episodesAgg).reduce((acc, i) => acc + (i.shot_count || 0), 0);
      if (sum > 0) {
        ledger.storyboard.shot_count = sum;
        ledger.storyboard.ready = true;
        ledger.storyboard.aggregated_from_episodes = true;
      }
      const segSum = Object.values(episodesAgg).reduce((acc, i) => acc + (i.segment_count || 0), 0);
      if (segSum > 0) ledger.storyboard.segment_count = segSum;
    }
  }

  // 5) 渲染状态 / 断点
  try {
    ledger.render = JSON.parse(await readFile(path.join(sessionRoot, 'render_status.json'), 'utf8'));
  } catch {
    // 多集项目：渲染状态写在 episodes/<ep>/render_status.json，取第一集异常状态作为全剧提示
    const firstIssue = Object.entries(episodesAgg).find(([, item]) => item.render && item.render.status && item.render.status !== 'rendered');
    if (firstIssue) {
      ledger.render = {...firstIssue[1].render, ep_id: firstIssue[0], aggregated: true};
    }
  }

  // 5.x) 预算台账：真实用量 = 实时聚合（与后端 BudgetLedger.state() 同一权威口径）。
  // 未配置预算策略时持久化 used 恒 0（reserve/commit 不触发），读文件快照会汇报失真，
  // 必须以记账流水为准：usage_log.jsonl（image/video/audio）+ metrics.jsonl（LLM tokens）。
  const usageAgg = await aggregateUsageLog(sessionRoot);
  let llmTokens = 0;
  try {
    const metricsFile = path.join(repoRoot, '.vimax', 'logs', 'metrics.jsonl');
    const content = await readFile(metricsFile, 'utf8');
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const record = JSON.parse(trimmed);
        if (String(record.session_id ?? '') !== sessionId) continue;
        llmTokens += Math.max(0, Number(record.total_tokens ?? 0) || 0);
      } catch { /* 跳过坏行 */ }
    }
  } catch { /* metrics 不存在 */ }
  let rawLedger = null;
  try {
    rawLedger = JSON.parse(await readFile(path.join(sessionRoot, 'budget_ledger.json'), 'utf8'));
  } catch { /* 无 ledger 文件 */ }
  const used = {
    image_calls: (usageAgg?.image_count ?? Number(rawLedger?.used?.image_calls ?? 0)) || 0,
    video_calls: (usageAgg?.video_count ?? Number(rawLedger?.used?.video_calls ?? 0)) || 0,
    video_seconds: (usageAgg?.video_seconds ?? Number(rawLedger?.used?.video_seconds ?? 0)) || 0,
    audio_calls: (usageAgg?.audio_count ?? Number(rawLedger?.used?.audio_calls ?? 0)) || 0,
    audio_chars: (usageAgg?.audio_chars ?? Number(rawLedger?.used?.audio_chars ?? 0)) || 0,
    llm_tokens: llmTokens,
  };
  ledger.budget = {
    used,
    configured: Boolean(rawLedger?.strategy) || rawLedger?.configured === true,
    updated_at: rawLedger?.updated_at ?? null,
  };

  // 6) 集级进度
  ledger.episodes = await readEpisodeProgress(repoRoot, sessionId);

  return ledger;
}

/** 聚合 script2video/usage/<yyyy-mm>/usage_log.jsonl 流水（image/video/audio 计数、视频秒数、音色字符）。 */
export async function aggregateUsageLog(sessionRoot) {
  const usageRoot = path.join(sessionRoot, 'script2video', 'usage');
  let image_count = 0, video_count = 0, video_seconds = 0, audio_count = 0, audio_chars = 0, hasAny = false;
  try {
    const months = await readdir(usageRoot, {withFileTypes: true});
    for (const m of months) {
      if (!m.isDirectory()) continue;
      const logFile = path.join(usageRoot, m.name, 'usage_log.jsonl');
      let content = null;
      try { content = await readFile(logFile, 'utf8'); } catch { continue; }
      for (const line of content.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const row = JSON.parse(trimmed);
          if (row.kind === 'image') { image_count += Number(row.quantity ?? 1) || 1; hasAny = true; }
          else if (row.kind === 'video') { video_count += Number(row.quantity ?? 1) || 1; video_seconds += Number(row.meta?.duration_sec ?? 0) || 0; hasAny = true; }
          else if (row.kind === 'audio') { audio_count += Number(row.quantity ?? 1) || 1; audio_chars += Number(row.meta?.tts_chars ?? 0) || 0; hasAny = true; }
        } catch { /* 跳过坏行 */ }
      }
    }
  } catch { /* usage 目录不存在 */ }
  if (!hasAny) return null;
  return {image_count, video_count, video_seconds, audio_count, audio_chars};
}

/** 画布聚合（Canvas）：把资产（立绘/场景/道具/音色）+ 镜头 + 段级契约组织成画布节点数据。 */
export async function readCanvas(repoRoot, sessionId, epId = null) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const s2v = path.join(sessionRoot, 'script2video');
  // 集级子画布：多集项目按集读取镜头/段/分镜（资产为项目级通用）；无 episodes 目录时回退根目录（单集/旧项目）
  const episodesRoot = path.join(s2v, 'episodes');
  let epRoot = s2v;
  if (epId) {
    const candidate = path.resolve(path.join(episodesRoot, epId));
    const root = path.resolve(episodesRoot);
    if (!candidate.startsWith(`${root}${path.sep}`)) throw new Error('ep_id 路径越界');
    epRoot = candidate;
  }
  const canvas = {
    meta: {session_id: sessionId, current_ep: epId ?? null, episodes: []},
    characters: [],
    scenes: [],
    props: [],
    voices: [],
    shots: [],
    segments: [],
  };

  const assetUrl = (relativePath) => `/api/artifact?session=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(relativePath)}`;
  const rel = (absolute) => path.relative(sessionRoot, absolute).split(path.sep).join('/');
  const nameOf = (fileName) => path.basename(fileName, path.extname(fileName));

  async function listImages(dir) {
    try {
      const entries = await readdir(dir, {withFileTypes: true});
      return entries
        .filter((e) => e.isFile() && IMAGE_EXTENSIONS.has(path.extname(e.name).toLowerCase()))
        .map((e) => path.join(dir, e.name));
    } catch {
      return [];
    }
  }
  async function listAudio(dir) {
    try {
      const entries = await readdir(dir, {withFileTypes: true});
      return entries
        .filter((e) => e.isFile() && AUDIO_EXTENSIONS.has(path.extname(e.name).toLowerCase()))
        .map((e) => path.join(dir, e.name));
    } catch {
      return [];
    }
  }

  // 角色卡（characters.json）：角色名表（旧字段文本回退匹配/音色绑定）+ 详情面板描述词（需在立绘读取前加载）
  const charNameByIdx = new Map();
  const charDescByName = new Map();
  const projectCharacterNames = new Set();
  const projectCharacters = [];
  try {
    const chars = JSON.parse(await readFile(path.join(s2v, 'characters.json'), 'utf8'));
    if (Array.isArray(chars)) {
      for (const c of chars) {
        const nm = c.identifier_in_scene ?? c.name ?? '';
        projectCharacters.push({raw: c, name: nm});
        if (nm) projectCharacterNames.add(nm);
        const parts = [c.static_features, c.dynamic_features].filter(Boolean);
        const desc = c.description ?? c.desc ?? c.character_description ?? parts.join('\n');
        if (nm && desc) charDescByName.set(nm, String(desc));
      }
    }
  } catch { /* 无角色卡 */ }

  // 按集镜头的 *vis_char_idxs 是相对「按集角色列表」的索引，不是全剧 characters.json 的 idx。
  // 这里用与 Python 分镜相同的顺序重建映射；旧项目/无 epId 时保留全剧 idx 语义。
  if (epId && projectCharacters.length) {
    let episodeScript = '';
    try {
      episodeScript = await readFile(path.join(epRoot, 'script.txt'), 'utf8');
    } catch { /* 无集级剧本时按全剧索引回退 */ }
    const dialogueNames = new Set();
    if (episodeScript) {
      for (const line of episodeScript.split(/\r?\n/)) {
        const match = line.trim().match(/^([^：:()（）\n]{1,16})(?:[（(][^）)]*[）)])?\s*[：:]\s*/);
        const name = match?.[1]?.trim();
        if (name && projectCharacterNames.has(name)) dialogueNames.add(name);
      }
    }
    const scoped = projectCharacters.filter(({name}) => name && (episodeScript.includes(name) || dialogueNames.has(name)));
    const effective = scoped.length ? scoped : projectCharacters;
    effective.forEach(({name}, index) => {
      if (name) charNameByIdx.set(index, name);
    });
  } else {
    for (const {raw, name} of projectCharacters) {
      if (typeof raw?.idx === 'number' && name) charNameByIdx.set(raw.idx, name);
    }
  }

  // 资产注册表：场景/道具语义名与物理文件路径映射。注册表缺失时仍按文件名回退。
  const propNameByImage = new Map();
  const sceneNameByImage = new Map();
  // 旧注册表可能把 image_path 存成相对 script2video 的路径（流水线 working_dir），
  // 也可能存成相对会话根。两个基准都注册，避免画布按文件名回退后丢失语义名/跨集过滤。
  const assetPathCandidates = (value) => {
    const raw = String(value || '').trim();
    if (!raw) return [];
    const candidates = path.isAbsolute(raw)
      ? [path.normalize(raw)]
      : [path.normalize(path.resolve(s2v, raw)), path.normalize(path.resolve(sessionRoot, raw))];
    return [...new Set(candidates)];
  };
  const registerAssetPath = (target, value, name) => {
    for (const candidate of assetPathCandidates(value)) target.set(candidate, name);
  };
  try {
    const scenePropsLoaded = JSON.parse(await readFile(path.join(s2v, 'scene_props.json'), 'utf8'));
    if (Array.isArray(scenePropsLoaded?.scenes)) {
      for (const scene of scenePropsLoaded.scenes) {
        if (!scene?.name) continue;
        if (scene.image_path) registerAssetPath(sceneNameByImage, scene.image_path, scene.name);
        if (scene.fingerprint) sceneNameByImage.set(path.normalize(path.join(s2v, 'scenes', `${scene.fingerprint}.png`)), scene.name);
      }
    }
    if (Array.isArray(scenePropsLoaded?.props)) {
      for (const prop of scenePropsLoaded.props) {
        if (prop?.name && prop?.image_path) registerAssetPath(propNameByImage, prop.image_path, prop.name);
      }
    }
  } catch { /* 无 scene_props */ }

  const portraitDir = path.join(s2v, 'character_portraits');
  const sceneFiles = await listImages(path.join(s2v, 'scenes'));
  const propFiles = await listImages(path.join(s2v, 'props'));
  const voiceFiles = await listAudio(path.join(s2v, 'voices'));
  const propLabelByFile = new Map(propFiles.map((file) => [
    path.normalize(file),
    propNameByImage.get(path.normalize(file)) ?? nameOf(file),
  ]));

  // 一集一资产范围：只按本集剧本文本解析当前集引用。物理文件仍可跨集复用，避免重复生成与扣费。
  let episodeAssetScope = null;
  if (epId) {
    try {
      const scriptText = await readFile(path.join(epRoot, 'script.txt'), 'utf8');
      episodeAssetScope = deriveEpisodeAssetScope({
        episodeBody: episodeBodyText(scriptText),
        characterNames: [...projectCharacterNames],
        sceneFiles,
        sceneNameByImage,
        propFiles,
        propLabelByFile,
        voiceFiles,
      });
    } catch { /* 无集级剧本时回退项目级资产，避免旧项目画布空白 */ }
  }
  if (episodeAssetScope) {
    canvas.meta.asset_scope = {
      ep_id: epId,
      filtered: true,
      characters: episodeAssetScope.allowedCharacters.size,
      scenes: episodeAssetScope.allowedSceneFiles.size,
      props: episodeAssetScope.allowedProps.size,
      voices: episodeAssetScope.allowedVoices.size,
    };
  }

  // 1) 角色立绘：优先 sheet.png（单张三视图合图），否则 front.png；用 registry 拿角色名
  try {
    const charDirs = (await readdir(portraitDir, {withFileTypes: true}))
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    for (const dirName of charDirs) {
      const dir = path.join(portraitDir, dirName);
      const images = await listImages(dir);
      const sheet = images.find((f) => path.basename(f).toLowerCase().startsWith('sheet'));
      const front = images.find((f) => path.basename(f).toLowerCase() === 'front.png');
      const main = sheet ?? front ?? images[0];
      if (!main) continue;
      const name = dirName.replace(/^\d+_/, '') || nameOf(main);
      if (episodeAssetScope && !episodeAssetScope.allowedCharacters.has(name)) continue;
      canvas.characters.push({
        idx: canvas.characters.length,
        name,
        image_url: assetUrl(rel(main)),
        description: charDescByName.get(name) ?? '',
      });
    }
  } catch { /* 无立绘 */ }

  // 2) 场景：优先注册表语义名；注册表缺失时保留文件名。
  for (const file of sceneFiles) {
    if (episodeAssetScope && !episodeAssetScope.allowedSceneFiles.has(file)) continue;
    const semantic = episodeAssetScope?.sceneNameOverrides.get(file) ?? sceneNameByImage.get(path.normalize(file));
    canvas.scenes.push({name: semantic ?? nameOf(file), image_url: assetUrl(rel(file))});
  }

  // 3) 道具：优先用 scene_props.json 的语义名（name），否则文件名去扩展名
  for (const file of propFiles) {
    if (episodeAssetScope && !episodeAssetScope.allowedProps.has(file)) continue;
    const semantic = propNameByImage.get(path.normalize(file));
    canvas.props.push({name: semantic ?? nameOf(file), image_url: assetUrl(rel(file))});
  }

  // 4) 音色
  const voiceOrder = new Map(canvas.characters.map((item, index) => [item.name, index]));
  const orderedVoiceFiles = [...voiceFiles].sort((left, right) => {
    const leftName = nameOf(left);
    const rightName = nameOf(right);
    const leftOrder = voiceOrder.get(leftName) ?? Number.MAX_SAFE_INTEGER;
    const rightOrder = voiceOrder.get(rightName) ?? Number.MAX_SAFE_INTEGER;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return leftName.localeCompare(rightName, undefined, {numeric: true});
  });
  for (const file of orderedVoiceFiles) {
    if (episodeAssetScope && !episodeAssetScope.allowedVoices.has(file)) continue;
    canvas.voices.push({name: nameOf(file), audio_url: assetUrl(rel(file))});
  }

  // 5) 段级契约（先于镜头加载，供镜头自动绑定场景引用）—— 集级子画布读 ep 目录；整剧总览聚合各集见下方 overview 分支
  if (epId) {
    try {
      const seg = JSON.parse(await readFile(path.join(epRoot, 'segments.json'), 'utf8'));
      const arr = Array.isArray(seg) ? seg : (seg.segments ?? []);
      canvas.segments = arr.map((s) => {
        const segVideo = path.join(epRoot, 'segments', `seg-${s.segment_id ?? canvas.segments.length}`, 'video.mp4');
        const hasSegVideo = existsSync(segVideo);
        return {
          segment_id: s.segment_id ?? canvas.segments.length,
          title: s.title ?? '',
          duration: s.duration ?? null,
          shot_idxs: Array.isArray(s.shot_idxs) ? s.shot_idxs : [],
          scene_id: s.scene_id ?? null,
          asset_binding: s.asset_binding ?? null,
          end_state: s.end_state ?? null,
          // 段级提示词原文（用于画布段卡"段提示词"弹窗回看）
          entry_state: s.entry_state ?? null,
          change_timeline: s.change_timeline ?? null,
          exit_state: s.exit_state ?? null,
          staging_and_axis: s.staging_and_axis ?? null,
          style_lighting: s.style_lighting ?? null,
          forbidden: s.forbidden ?? null,
          fixed_block: s.fixed_block ?? null,
          group_bridge: s.group_bridge ?? null,
          inheritance_rules: s.inheritance_rules ?? null,
          ep_id: epId,
          clip_url: hasSegVideo ? assetUrl(rel(segVideo)) : null,
          has_segment_video: hasSegVideo,
        };
      });
    } catch { /* 未生成 */ }
    if (canvas.meta.asset_scope) canvas.meta.asset_scope.segments = canvas.segments.length;
  }

  // 角色名表（characters.json）：旧字段文本回退匹配（charNameByIdx/charDescByName 已在立绘读取前加载）
  const voiceNameSet = new Set(canvas.voices.map((v) => v.name));
  // 集级过滤后角色索引会重新编号；把镜头里保存的项目级 idx 映射到当前集可见 idx。
  const visibleCharIndexByName = new Map(canvas.characters.map((item, index) => [item.name, index]));
  // 道具候选词：语义名（去引号/书名号）+ 文件名 + 最长汉字段 + 按“与/、/空格”切出的语义段，
  // 并附每段的后缀 2/3 字（描述文本常写简称，如“桌牌”“保温杯”“队旗”）
  const propNameCandidates = canvas.props.map((p) => {
    const clean = (s) => s.replace(/[“”《》]/g, '').replace(/^_+|_+$/g, '');
    const hanzi = (s) => s.match(/[\u4e00-\u9fa5]+/g) ?? [];
    const mainSeg = hanzi(clean(p.name)).join(''); // 最长连续汉字串（去编号等符号）
    const segs = new Set([clean(p.name), p.name, mainSeg, ...hanzi(clean(p.name)).flatMap((h) => h.split(/与|、|和|\s/)).filter((s) => s.length >= 2)]);
    const out = new Set();
    segs.forEach((s) => {
      out.add(s);
      if (s.length >= 2) out.add(s.slice(-2));
      if (s.length >= 3) out.add(s.slice(-3));
      // 前缀降级：描述常写简称，如资产「文书摞」、描述写「文书」→ 逐级截尾加入
      for (let cut = 1; cut <= 2 && s.length - cut >= 2; cut++) out.add(s.slice(0, s.length - cut));
    });
    return [...out].filter(Boolean);
  });

  // 6) 镜头：shots/<idx>/shot_description.json + 关键帧/片段存在性 + 自动资产绑定
  //    storyboard.json 作为 shot_description 缺失时的回退源（旧项目/重建中断遗留）
  // 单集/整剧总览共用的镜头加载器（epTag 传集 id；整剧总览聚合时带集前缀避免跨集冲突）
  const loadShotsForEp = async (epDir, epTag) => {
    const storyboardByIdx = new Map();
    try {
      const sb = JSON.parse(await readFile(path.join(epDir, 'storyboard.json'), 'utf8'));
      const arr = Array.isArray(sb) ? sb : (sb.shots ?? sb.storyboard ?? []);
      for (const s of arr) {
        if (typeof s?.idx === 'number') storyboardByIdx.set(s.idx, s);
      }
    } catch { /* 无分镜卡 */ }
  // 解析 storyboard.reference_assets 的“角色：X；场景：Y；道具：Z（注释）”结构化引用
  const parseRefAssets = (text) => {
    const out = {chars: [], scenes: [], props: []};
    if (!text || typeof text !== 'string') return out;
    for (const seg of text.split('；')) {
      const m = seg.match(/^([角色场景道具])\s*[:：]\s*(.*)$/);
      if (!m) continue;
      if (m[1] === '角色') out.chars.push(m[2].trim());
      else if (m[1] === '场景') out.scenes.push(m[2].trim());
      else if (m[1] === '道具') {
        for (const p of m[2].split(/[、,，]/)) {
          const annot = p.match(/[（(](.*?)[)）]/);
          if (annot && /未|无|不出现|不入/.test(annot[1])) continue; // 注释表明本镜未出现 → 跳过
          const name = p.replace(/[（(].*?[)）]/g, '').trim();
          if (name) out.props.push(name);
        }
      }
    }
    return out;
  };
  try {
    const shotDirs = (await readdir(path.join(epDir, 'shots'), {withFileTypes: true}))
      .filter((e) => e.isDirectory() && /^\d+$/.test(e.name))
      .map((e) => Number(e.name))
      .sort((a, b) => a - b);
    for (const idx of shotDirs) {
      const shotDir = path.join(epDir, 'shots', String(idx));
      let desc = null;
      try {
        desc = JSON.parse(await readFile(path.join(shotDir, 'shot_description.json'), 'utf8'));
      } catch { /* 无描述 */ }
      if (!desc && storyboardByIdx.has(idx)) {
        const s = storyboardByIdx.get(idx);
        const ref = parseRefAssets(s.reference_assets);
        const strOf = (v) => (typeof v === 'string' && v.trim() ? v : null);
        desc = {
          segment_id: s.segment_id ?? null,
          visual_desc: s.visual_desc ?? null,
          ff_desc: s.ff_desc ?? null,
          lf_desc: s.lf_desc ?? null,
          motion_desc: strOf(s.motion_desc) ?? strOf(s.camera),
          audio_desc: strOf(s.audio_desc) ?? strOf(s.dialogue),
          action_breakdown: strOf(s.action_breakdown) ?? strOf(s.action),
          transition_type: s.transition_type ?? null,
          duration: typeof s.duration === 'number' ? s.duration : null,
          cam_idx: typeof s.cam_idx === 'number' ? s.cam_idx : null,
          ff_vis_char_idxs: Array.isArray(s.char_ids) ? s.char_ids : [],
          video_prompt: strOf(s.video_prompt),
          camera_lang: strOf(s.camera_lang),
          person_layers: strOf(s.person_layers),
          _ref: ref,
          _sceneId: s.scene_id ?? null,
        };
      }
      // shot_description.json 自身就带 scene_id / reference_assets（不走 storyboard 回退分支时
      // 不会注入 _sceneId/_ref），缺了它们镜头永远绑不上场景 → 画布场景连线全空。
      if (desc && typeof desc === 'object') {
        if (!desc._ref && typeof desc.reference_assets === 'string') desc._ref = parseRefAssets(desc.reference_assets);
        if (desc._sceneId === undefined || desc._sceneId === null) desc._sceneId = desc.scene_id ?? null;
      }
      const frames = await listImages(shotDir);
      const keyframe = frames.find((f) => /^first_frame/i.test(path.basename(f))) ?? frames[0] ?? null;
      const clipFiles = [];
      try {
        const entries = await readdir(shotDir, {withFileTypes: true});
        clipFiles.push(...entries.filter((e) => e.isFile() && VIDEO_EXTENSIONS.has(path.extname(e.name).toLowerCase())).map((e) => path.join(shotDir, e.name)));
      } catch { /* 无片段 */ }

      // —— 自动对接：本镜引用的角色 / 场景 / 道具 / 音色 ——
      const resolveVisibleCharIndex = (value) => {
        const originalIndex = Number(value);
        const name = charNameByIdx.get(originalIndex);
        return name ? visibleCharIndexByName.get(name) ?? -1 : -1;
      };
      let charIdxs = (Array.isArray(desc?.ff_vis_char_idxs) ? desc.ff_vis_char_idxs : [])
        .map(resolveVisibleCharIndex)
        .filter((index) => index >= 0);
      if (desc && charIdxs.length === 0) {
        // 旧字段回退：从首帧/画面描述文本匹配角色名
        const text = [desc.ff_desc, desc.visual_desc, desc.lf_desc, desc.motion_desc].filter(Boolean).join(' ');
        charNameByIdx.forEach((name) => {
          const visibleIndex = visibleCharIndexByName.get(name);
          if (name && visibleIndex !== undefined && text.includes(name) && !charIdxs.includes(visibleIndex)) charIdxs.push(visibleIndex);
        });
        // 结构化引用回退：reference_assets 的“角色：”节（精确优先）
        for (const rname of desc._ref?.chars ?? []) {
          let hit = -1;
          charNameByIdx.forEach((name) => {
            if (hit >= 0) return;
            if (!name || !(name === rname || name.includes(rname) || rname.includes(name))) return;
            hit = visibleCharIndexByName.get(name) ?? -1;
          });
          if (hit >= 0 && !charIdxs.includes(hit)) charIdxs.push(hit);
        }
      }
      const sceneIdxs = [];
      if (canvas.scenes.length === 1) {
        sceneIdxs.push(0); // 单场景剧集：全镜头绑定该场景
      } else {
        let sceneKey = desc?._sceneId ?? null;
        if (sceneKey === null || sceneKey === undefined) {
          const seg = desc?.segment_id !== null && desc?.segment_id !== undefined
            ? canvas.segments.find((s) => String(s.segment_id) === String(desc.segment_id))
            : null;
          sceneKey = seg?.scene_id ?? null;
        }
        if (sceneKey !== null && sceneKey !== undefined) {
          const hit = canvas.scenes.findIndex((s) => String(sceneKey).includes(s.name) || s.name.includes(String(sceneKey)));
          if (hit >= 0) sceneIdxs.push(hit);
        }
        for (const rscene of desc?._ref?.scenes ?? []) {
          const hit = canvas.scenes.findIndex((s) => rscene.includes(s.name) || s.name.includes(rscene));
          if (hit >= 0 && !sceneIdxs.includes(hit)) sceneIdxs.push(hit);
        }
      }
      const propIdxs = [];
      if (desc) {
        const text = [desc.visual_desc, desc.ff_desc, desc.lf_desc, desc.motion_desc].filter(Boolean).join(' ');
        canvas.props.forEach((prop, pi) => {
          const candidates = propNameCandidates[pi] ?? [];
          if (candidates.some((c) => text.includes(c))) propIdxs.push(pi);
        });
        // 结构化引用回退：reference_assets 的“道具：”节
        for (const pname of desc._ref?.props ?? []) {
          let hit = -1;
          canvas.props.forEach((prop, pi) => {
            if (hit >= 0) return;
            const base = prop.name.replace(/[“”《》]/g, '');
            const candidates = propNameCandidates[pi] ?? [];
            if (candidates.includes(pname) || base.includes(pname) || pname.includes(base)) hit = pi;
          });
          if (hit >= 0 && !propIdxs.includes(hit)) propIdxs.push(hit);
        }
      }
      const voiceIdxs = [];
      charIdxs.forEach((ci) => {
        const cname = canvas.characters[ci]?.name;
        if (cname && voiceNameSet.has(cname)) {
          const vi = canvas.voices.findIndex((v) => v.name === cname);
          if (vi >= 0 && !voiceIdxs.includes(vi)) voiceIdxs.push(vi);
        }
      });

      canvas.shots.push({
        idx,
        ep_id: epTag || null,
        segment_id: desc?.segment_id ?? null,
        char_idxs: charIdxs,
        scene_idxs: sceneIdxs,
        prop_idxs: propIdxs,
        voice_idxs: voiceIdxs,
        cam_idx: desc?.cam_idx ?? null,
        transition_type: desc?.transition_type ?? null,
        duration: desc?.duration ?? null,
        visual_desc: desc?.visual_desc ?? null,
        visual_summary: desc?.visual_desc ? desc.visual_desc.slice(0, 160) : null,
        ff_desc: desc?.ff_desc ?? null,
        lf_desc: desc?.lf_desc ?? null,
        motion_desc: desc?.motion_desc ?? null,
        audio_desc: desc?.audio_desc ?? null,
        action_breakdown: desc?.action_breakdown ?? null,
        motion_summary: desc?.motion_desc ? desc.motion_desc.slice(0, 120) : null,
        video_prompt: desc?.video_prompt ?? null,
        camera_lang: desc?.camera_lang ?? null,
        person_layers: desc?.person_layers ?? null,
        keyframe_url: keyframe ? assetUrl(rel(keyframe)) : null,
        clip_url: clipFiles.length > 0 ? assetUrl(rel(clipFiles[0])) : null,
        // 片段过期：分镜描述比片段新 → 画面/提示词改过了，但片段还是旧的。
        // 不提示的话用户会以为"改了已经生效"，实际渲染会跳过已有片段。
        clip_stale: clipFiles.length > 0 && await clipIsStale(shotDir, clipFiles[0]),
        has_description: desc !== null,
      });
    }
  } catch (err) { /* 无镜头 */ if (process.env.VIMAX_DEBUG_CANVAS) console.error('loadShotsForEp fail', epDir, err); }
  };

  if (epId) {
    await loadShotsForEp(epRoot, epId);
    // segments.json 缺失兜底（2026-10-15）：重跑分镜指纹失效会删掉 segments.json，
    // 但 shots/ 里每镜仍带 segment_id、segments/seg-N/video.mp4 已成片。此时按镜头
    // segment_id 聚合成段卡并挂段视频 URL，画布不再是空白段。
    if (canvas.segments.length === 0 && Array.isArray(canvas.shots) && canvas.shots.length > 0) {
      const segIds = [...new Set(canvas.shots.map((s) => Number(s.segment_id)).filter((n) => Number.isFinite(n)))].sort((a, b) => a - b);
      for (const sid of segIds) {
        const shotIdxs = canvas.shots.filter((s) => Number(s.segment_id) === sid).map((s) => s.idx);
        const segVideo = path.join(epRoot, 'segments', `seg-${sid}`, 'video.mp4');
        const hasSegVideo = existsSync(segVideo);
        canvas.segments.push({
          segment_id: sid,
          title: `第${sid + 1}段（契约缺失·按镜头聚合）`,
          duration: null,
          shot_idxs: shotIdxs,
          scene_id: null,
          asset_binding: null,
          end_state: null,
          ep_id: epId,
          clip_url: hasSegVideo ? assetUrl(rel(segVideo)) : null,
          has_segment_video: hasSegVideo,
        });
      }
      if (canvas.meta.asset_scope) canvas.meta.asset_scope.segments = canvas.segments.length;
    }
  } else {
    // 整剧总览：聚合 episodes/* 各集的段契约与镜头（带集前缀，避免跨集 idx/segment_id 冲突）。
    // 资产为项目级共享（上方已按根目录加载）；段/镜头按集独立读取，段间连线按集内段序。
    let epDirs = [];
    try {
      epDirs = (await readdir(episodesRoot, {withFileTypes: true}))
        .filter((e) => e.isDirectory())
        .map((e) => e.name)
        .sort((a, b) => a.localeCompare(b, undefined, {numeric: true}));
    } catch { /* 无集目录 */ }
    const segLoaded = new Set();
    for (const epName of epDirs) {
      const epDir = path.join(episodesRoot, epName);
      try {
        const seg = JSON.parse(await readFile(path.join(epDir, 'segments.json'), 'utf8'));
        const arr = Array.isArray(seg) ? seg : (seg.segments ?? []);
        for (const s of arr) {
          const segVideo = path.join(epDir, 'segments', `seg-${s.segment_id ?? canvas.segments.length}`, 'video.mp4');
          const hasSegVideo = existsSync(segVideo);
          canvas.segments.push({
            segment_id: s.segment_id ?? canvas.segments.length,
            title: s.title ?? '',
            duration: s.duration ?? null,
            shot_idxs: Array.isArray(s.shot_idxs) ? s.shot_idxs : [],
            scene_id: s.scene_id ?? null,
            asset_binding: s.asset_binding ?? null,
            end_state: s.end_state ?? null,
            entry_state: s.entry_state ?? null,
            change_timeline: s.change_timeline ?? null,
            exit_state: s.exit_state ?? null,
            staging_and_axis: s.staging_and_axis ?? null,
            style_lighting: s.style_lighting ?? null,
            forbidden: s.forbidden ?? null,
            fixed_block: s.fixed_block ?? null,
            group_bridge: s.group_bridge ?? null,
            inheritance_rules: s.inheritance_rules ?? null,
            ep_id: epName,
            clip_url: hasSegVideo ? assetUrl(rel(segVideo)) : null,
            has_segment_video: hasSegVideo,
          });
        }
        segLoaded.add(epName);
      } catch { /* 该集无段契约 */ }
      await loadShotsForEp(epDir, epName);
    }
    if (segLoaded.size > 0) canvas.meta.overview = true;
  }

  // 集列表（前端"一集一画布"切换器用）；有 episodes 目录才填充
  try {
    canvas.meta.episodes = await readEpisodeProgress(repoRoot, sessionId);
  } catch { /* 忽略 */ }

  return canvas;
}

/** 磁盘占用统计（P1-4）：script2video 下按目录聚合大小与文件数。 */
export async function readSessionDiskUsage(repoRoot, sessionId) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const script2video = path.join(sessionRoot, 'script2video');
  const summary = {totalBytes: 0, totalFiles: 0, groups: {}};

  async function dirStats(directory) {
    let bytes = 0;
    let files = 0;
    async function walk(dir) {
      let entries;
      try {
        entries = await readdir(dir, {withFileTypes: true});
      } catch {
        return;
      }
      for (const entry of entries) {
        if (entry.name.startsWith('.')) continue;
        const abs = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          await walk(abs);
        } else if (entry.isFile()) {
          try {
            const info = await stat(abs);
            bytes += info.size;
            files += 1;
          } catch {
            /* ignore */
          }
        }
      }
    }
    await walk(directory);
    return {bytes, files};
  }

  try {
    const entries = await readdir(script2video, {withFileTypes: true});
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      if (!entry.isDirectory()) continue; // 顶层散文件不计入组（如 characters.json）
      const abs = path.join(script2video, entry.name);
      const stats = await dirStats(abs);
      if (stats.bytes === 0 && stats.files === 0) continue; // 空目录组无意义，跳过
      summary.groups[entry.name] = stats;
      summary.totalBytes += stats.bytes;
      summary.totalFiles += stats.files;
    }
  } catch {
    /* session 尚无 script2video */
  }
  return summary;
}

/** 清理指定集的渲染产物（P1-4）：删除 shots/、final_video.mp4 等可再生成的重产物，
 *  保留 script.txt、storyboard.json、segments.json、camera_tree.json、.progress.json 等文本规划。 */
export async function deleteEpisodeRenderOutputs(repoRoot, sessionId, epId) {
  if (!/^ep[A-Za-z0-9_-]{1,63}$/.test(String(epId || ''))) {
    throw new Error('ep_id 格式无效');
  }
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const epDir = path.resolve(path.join(sessionRoot, 'script2video', 'episodes', epId));
  const episodesRoot = path.resolve(path.join(sessionRoot, 'script2video', 'episodes'));
  if (!epDir.startsWith(`${episodesRoot}${path.sep}`)) {
    throw new Error('ep_id 路径越界');
  }
  const targets = ['shots', 'final_video.mp4', 'subtitles.ass', 'subtitles.srt', 'final_video_with_subtitles.mp4', 'final_video_with_subtitles.mp4.poster.jpg', 'final_video.mp4.poster.jpg'];
  let freedBytes = 0;
  for (const target of targets) {
    const abs = path.join(epDir, target);
    try {
      const info = await stat(abs);
      if (info.isDirectory()) {
        let size = 0;
        async function sumDir(dir) {
          const sub = await readdir(dir, {withFileTypes: true});
          for (const item of sub) {
            const p = path.join(dir, item.name);
            if (item.isDirectory()) await sumDir(p);
            else if (item.isFile()) {
              try {
                size += (await stat(p)).size;
              } catch {
                /* ignore */
              }
            }
          }
        }
        await sumDir(abs);
        await rm(abs, {recursive: true, force: true});
        freedBytes += size;
      } else {
        freedBytes += info.size;
        await rm(abs, {force: true});
      }
    } catch {
      /* target 不存在则跳过 */
    }
  }
  // P1-7：清理后按磁盘实况重算 .progress.json，避免 UI 残留旧的"成片已完成"状态
  await recomputeEpisodeProgress(epDir);
  return {ep_id: epId, freed_bytes: freedBytes, note: '已清理该集可再生成的渲染产物（关键帧/片段/成片）；剧本与分镜等文本规划保留，资产（立绘/场景/道具/音色）为项目级保留。'};
}

/** P1-7：纯只读扫描该集实际产物并重写 .progress.json（与 agent 侧 _update_episode_progress 口径一致）。 */
/** .progress.json 是否与磁盘脱节（缺失 / 分镜更新更晚 / 磁盘镜数与记录不符 / 成片在盘上却没标记）。 */
async function progressIsStale(epDir, progress) {
  if (!progress) return true;
  const sbPath = path.join(epDir, 'storyboard.json');
  const sbStat = await statSafe(sbPath);
  if (!sbStat) return false; // 没分镜就没得比，保持原值
  const prStat = await statSafe(path.join(epDir, '.progress.json'));
  if (!prStat || sbStat.mtimeMs > prStat.mtimeMs) return true;
  try {
    const sb = JSON.parse(await readFile(sbPath, 'utf8'));
    const count = (Array.isArray(sb) ? sb : (sb.shots || [])).length;
    if (typeof progress.storyboard?.shot_count === 'number' && progress.storyboard.shot_count !== count) return true;
  } catch { /* 分镜不可解析则不算脱节 */ }
  if (await exists(path.join(epDir, 'final_video.mp4')) && !progress.final_video?.ready) return true;
  // 成片在盘但产物不齐（缺段/缺镜）时，记录自称"complete"是假 PASS（实测 7/14 段写成 complete），
  // 必须按磁盘重算成 partial_rendered；已标记 partial 的不再重复重算。
  if (progress.final_video?.ready && progress.status !== 'partial_rendered' && progress.final_video?.complete !== false) {
    if (!(await episodeRenderCoverage(epDir)).complete) return true;
  }
  // described_count 是后加的字段：老 .progress.json 没有它，进度页就永远显示"计划镜数"，
  // 与画布实际镜头数对不上（实测 32 计划 / 5 落地）。缺字段即视为陈旧，重算补齐。
  if (typeof progress.storyboard?.described_count !== 'number') return true;
  return false;
}

async function statSafe(p) {
  try { return await stat(p); } catch { return null; }
}

/**
 * 集级渲染完整性（JS 镜像，★唯一权威是 utils/episode_completeness.py，改一处必须同步另一处）。
 *
 * 病根：只判 final_video.mp4 在不在 → segments.json 声明 14 段、盘上只有 7 段时，
 * 残缺成片被当成"整集已完成"（实测 ep01）。口径：
 *   段级直出（有段产物，或没有逐镜产物）→ 应有 = 每一段，缺任一段即不完整；
 *   逐镜 → 应有 = storyboard.json 每一镜，缺任一镜即不完整；
 *   两者都无从得知 → 以成片在盘为准（verifiable=false）。
 *   产物必须存在且非空（0 字节 = 中断半成品）。
 */
export async function episodeRenderCoverage(epDir) {
  const coverage = {
    mode: 'none',
    final_video: false,
    segment_total: 0,
    segment_clips: 0,
    shot_total: 0,
    shot_clips: 0,
    missing_segment_ids: [],
    missing_shot_idxs: [],
    expected: 0,
    rendered: 0,
    complete: false,
    verifiable: false,
  };
  const nonEmpty = async (p) => {
    try {
      const s = await stat(p);
      return s.isFile() && s.size > 0;
    } catch {
      return false;
    }
  };
  coverage.final_video = await exists(path.join(epDir, 'final_video.mp4'));
  let segments = [];
  try {
    const raw = JSON.parse(await readFile(path.join(epDir, 'segments.json'), 'utf8'));
    const arr = Array.isArray(raw) ? raw : (raw.segments ?? []);
    segments = arr.filter((seg) => seg && typeof seg === 'object');
    coverage.segment_total = segments.length;
  } catch { /* 无段契约 */ }
  let segmentClips = 0;
  try {
    const entries = await readdir(path.join(epDir, 'segments'), {withFileTypes: true});
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (await nonEmpty(path.join(epDir, 'segments', entry.name, 'video.mp4'))) segmentClips += 1;
    }
  } catch { /* 无段产物目录 */ }
  coverage.segment_clips = segmentClips;
  let shotClips = 0;
  try {
    const entries = await readdir(path.join(epDir, 'shots'), {withFileTypes: true});
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (await nonEmpty(path.join(epDir, 'shots', entry.name, 'video.mp4'))) shotClips += 1;
    }
  } catch { /* 无逐镜产物目录 */ }
  coverage.shot_clips = shotClips;
  let storyboardIdxs = [];
  try {
    const raw = JSON.parse(await readFile(path.join(epDir, 'storyboard.json'), 'utf8'));
    const shots = Array.isArray(raw) ? raw : (raw.shots ?? raw.storyboard ?? []);
    storyboardIdxs = (shots ?? [])
      .filter((shot) => shot && typeof shot === 'object')
      .map((shot, position) => (Number.isFinite(Number(shot.idx)) ? Number(shot.idx) : position));
    coverage.shot_total = storyboardIdxs.length;
  } catch { /* 无分镜 */ }
  const indexOf = (shotIdxs) => new Set((shotIdxs ?? [])
    .map((raw) => Number(String(raw).trim()))
    .filter((value) => Number.isFinite(value)));
  if (segments.length > 0 && (segmentClips > 0 || shotClips === 0)) {
    const covered = new Set();
    const missingSegments = [];
    for (let i = 0; i < segments.length; i += 1) {
      const segId = segments[i].segment_id ?? i;
      for (const idx of indexOf(segments[i].shot_idxs)) covered.add(idx);
      if (!(await nonEmpty(path.join(epDir, 'segments', `seg-${segId}`, 'video.mp4')))) missingSegments.push(segId);
    }
    // 编号口径完全无交集（0/1 起算不一致）时不做覆盖缺口判定，避免误报
    const overlaps = storyboardIdxs.some((idx) => covered.has(idx));
    const missingShots = overlaps ? storyboardIdxs.filter((idx) => !covered.has(idx)) : [];
    coverage.mode = 'segment';
    coverage.expected = segments.length;
    coverage.rendered = segments.length - missingSegments.length;
    coverage.missing_segment_ids = missingSegments;
    coverage.missing_shot_idxs = missingShots;
    coverage.complete = missingSegments.length === 0 && missingShots.length === 0;
    coverage.verifiable = true;
    return coverage;
  }
  if (storyboardIdxs.length > 0) {
    const missingShots = [];
    for (const idx of storyboardIdxs) {
      if (!(await nonEmpty(path.join(epDir, 'shots', String(idx), 'video.mp4')))) missingShots.push(idx);
    }
    coverage.mode = 'shot';
    coverage.expected = storyboardIdxs.length;
    coverage.rendered = storyboardIdxs.length - missingShots.length;
    coverage.missing_shot_idxs = missingShots;
    coverage.complete = missingShots.length === 0;
    coverage.verifiable = true;
    return coverage;
  }
  coverage.complete = coverage.final_video;
  return coverage;
}

/** 不完整时的一句中文说明（与 Python 侧 coverage_gap_message 同口径）。 */
export function coverageGapMessage(coverage, epId = '') {
  const parts = [];
  const missingSegments = coverage?.missing_segment_ids ?? [];
  const missingShots = coverage?.missing_shot_idxs ?? [];
  if (missingSegments.length > 0) {
    const head = missingSegments.slice(0, 8).map((id) => `seg-${id}`).join('、');
    const more = missingSegments.length > 8 ? ` 等 ${missingSegments.length} 段` : '';
    parts.push(`缺 ${head}${more}（${coverage.rendered ?? 0}/${coverage.expected ?? 0} 段已渲染）`);
  }
  if (missingShots.length > 0) {
    const head = missingShots.slice(0, 8).map((idx) => `#${idx}`).join('、');
    const more = missingShots.length > 8 ? ` 等 ${missingShots.length} 镜` : '';
    parts.push(`缺镜头 ${head}${more}（分镜 ${coverage.shot_total ?? 0} 镜未全部覆盖）`);
  }
  if (parts.length === 0) return epId ? `${epId} 渲染产物不完整。` : '渲染产物不完整。';
  const prefix = epId ? `${epId} ` : '';
  return `${prefix}渲染不完整：${parts.join('；')}。final_video.mp4 是残缺成片，不得记作完成。`;
}

/** 集的"可播放片段"数：逐镜模式数 shots/<idx>/video.mp4，段级直出模式数 segments/seg-N/video.mp4。 */
async function countEpisodeClips(epDir) {
  let count = 0;
  try {
    const shots = await readdir(path.join(epDir, 'shots'), {withFileTypes: true});
    for (const entry of shots) {
      if (!entry.isDirectory()) continue;
      try { if (await exists(path.join(epDir, 'shots', entry.name, 'video.mp4'))) count += 1; } catch { /* ignore */ }
    }
  } catch { /* 无 shots 目录 */ }
  try {
    const segs = await readdir(path.join(epDir, 'segments'), {withFileTypes: true});
    for (const entry of segs) {
      if (!entry.isDirectory()) continue;
      try { if (await exists(path.join(epDir, 'segments', entry.name, 'video.mp4'))) count += 1; } catch { /* ignore */ }
    }
  } catch { /* 无 segments 目录 */ }
  return count;
}

/**
 * 片段是否已过期：分镜描述文件比片段文件新，说明提示词/画面改过了。
 *
 * 渲染端对已有片段是"存在即跳过"，所以改完描述不重出的话成片永远停在旧画面。
 * 这里只做 mtime 比较（渲染端另有基于内容的提示词指纹做精确失效判定），
 * 目的是给画布一个可见的"需重出"标记，避免用户误判改动已生效。
 */
async function clipIsStale(dirPath, clipPath) {
  try {
    const descPath = path.join(dirPath, 'shot_description.json');
    const [descStat, clipStat] = await Promise.all([stat(descPath), stat(clipPath)]);
    return descStat.mtimeMs > clipStat.mtimeMs + 1000; // 1s 容差：同一批写入不算过期
  } catch {
    return false; // 任一文件缺失/不可读 → 不误报
  }
}

/** 已落地 shot_description.json 的镜头数（真实完成量，区别于 storyboard.json 的计划量）。 */
async function countEpisodeDescribed(epDir) {
  let count = 0;
  try {
    const shots = await readdir(path.join(epDir, 'shots'), {withFileTypes: true});
    for (const entry of shots) {
      if (!entry.isDirectory()) continue;
      try { if (await exists(path.join(epDir, 'shots', entry.name, 'shot_description.json'))) count += 1; } catch { /* ignore */ }
    }
  } catch { /* 无 shots 目录 */ }
  return count;
}

export async function recomputeEpisodeProgress(epDir) {
  let progress = {};
  try {
    const sbPath = path.join(epDir, 'storyboard.json');
    if (await exists(sbPath)) {
      const sb = JSON.parse(await readFile(sbPath, 'utf8'));
      const shots = Array.isArray(sb) ? sb : (sb.shots || []);
      progress.storyboard = {ready: true, shot_count: shots.length};
    }
  } catch { /* ignore */ }
  try {
    const segPath = path.join(epDir, 'segments.json');
    if (await exists(segPath)) {
      const segs = JSON.parse(await readFile(segPath, 'utf8'));
      progress.segments = {ready: true, segment_count: Array.isArray(segs) ? segs.length : ((segs.segments || []).length)};
    }
  } catch { /* ignore */ }
  let kfCount = 0;
  let shotClipCount = 0;
  let describedCount = 0;
  const shotsDir = path.join(epDir, 'shots');
  try {
    const shotEntries = await readdir(shotsDir, {withFileTypes: true});
    for (const shotEntry of shotEntries) {
      if (!shotEntry.isDirectory()) continue;
      try {
        if (await exists(path.join(shotsDir, shotEntry.name, 'first_frame.png'))) kfCount += 1;
        if (await exists(path.join(shotsDir, shotEntry.name, 'video.mp4'))) shotClipCount += 1;
        // 已落地 shot_description.json 的镜头数：storyboard.json 写的是"计划镜头数"，
        // 分镜生成中断时会远大于实际（实测 32 镜计划 / 5 镜落地）。只报计划数会让
        // 台账显示 32 镜而画布只有 5 张镜头卡，用户以为数据丢了。
        if (await exists(path.join(shotsDir, shotEntry.name, 'shot_description.json'))) describedCount += 1;
      } catch { /* ignore */ }
    }
  } catch { /* shots 目录不存在（已清理或未渲染） */ }
  if (progress.storyboard) progress.storyboard.described_count = describedCount;
  // 段级直出：产物在 segments/seg-N/video.mp4，shots/<idx>/video.mp4 根本不存在。
  // 不统计这段会让「片段数」恒为 0，续跑判定和台账进度全部失真。
  let segmentClipCount = 0;
  const segmentsDir = path.join(epDir, 'segments');
  try {
    const segEntries = await readdir(segmentsDir, {withFileTypes: true});
    for (const segEntry of segEntries) {
      if (!segEntry.isDirectory()) continue;
      try {
        if (await exists(path.join(segmentsDir, segEntry.name, 'video.mp4'))) segmentClipCount += 1;
      } catch { /* ignore */ }
    }
  } catch { /* 段级产物目录不存在 */ }
  const segmentTotal = progress.segments?.segment_count ?? 0;
  if (segmentTotal > 0 || segmentClipCount > 0) {
    progress.segments = {...(progress.segments ?? {}), ready: segmentTotal > 0, rendered: segmentClipCount, total: segmentTotal};
  }
  progress.keyframes = {count: kfCount};
  progress.clips = {count: shotClipCount + segmentClipCount, by_shot: shotClipCount, by_segment: segmentClipCount};
  // 成片字段拆三态：ready（在盘可播）／complete（产物齐才算成片完成）／partial（残缺成片）。
  // 只写 ready 会让 segments.json 声明 14 段、盘上只有 7 段的残缺成片被当成"已完成"（实测 ep01）。
  const coverage = await episodeRenderCoverage(epDir);
  const finalReady = coverage.final_video;
  progress.final_video = {
    ready: finalReady,
    complete: Boolean(finalReady && coverage.complete),
    partial: Boolean(finalReady && !coverage.complete),
  };
  progress.render_coverage = {
    mode: coverage.mode,
    expected: coverage.expected,
    rendered: coverage.rendered,
    missing_segments: coverage.missing_segment_ids,
    missing_shots: coverage.missing_shot_idxs,
  };
  if (finalReady && !coverage.complete) progress.render_gap = coverageGapMessage(coverage, path.basename(epDir));
  // 状态按磁盘实况逐级推导（此前只看 storyboard，段级模式下永远停在"分镜就绪"）
  if (finalReady) progress.status = coverage.complete ? 'complete' : 'partial_rendered';
  else if (shotClipCount > 0 || segmentClipCount > 0) progress.status = 'clips_ready';
  else if (kfCount > 0) progress.status = 'keyframes_ready';
  else if (progress.storyboard?.ready) progress.status = 'storyboard_ready';
  else progress.status = 'extracted';
  progress.updated_at = new Date().toISOString();
  try {
    await writeFile(path.join(epDir, '.progress.json'), JSON.stringify(progress, null, 2), 'utf8');
  } catch { /* 目录不可写时静默，读侧有容错 */ }
  return progress;
}

async function exists(p) {
  try { await stat(p); return true; } catch { return false; }
}

async function readConfiguredVideoModel(repoRoot) {
  try {
    const raw = await readFile(path.join(repoRoot, 'configs', 'agent.local.yaml'), 'utf8');
    const config = parseYaml(raw);
    return String(config?.video?.model || '').trim();
  } catch {
    return '';
  }
}

/** 聚合当前项目（会话工作区，含各子场景工作区）下所有用量记录。 */
export async function aggregateSessionUsage(repoRoot, sessionId) {
  const recentLimit = 30;
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const totals = {
    entries: 0,
    image: {count: 0, models: {}},
    video: {count: 0, duration_sec: 0, by_resolution: {}},
    audio: {count: 0, tts_chars: 0, billable_count: 0},
    breakdown: {by_kind: {}, by_purpose: {}, by_model: {}, by_resolution: {}, daily: {}},
    recent: [],
    recent_by_kind: {image: [], video: [], audio: []},
  };
  const fallbackVideoModel = await readConfiguredVideoModel(repoRoot);
  const recentAll = [];
  const daily = {};
  const modelDetail = {};

  const bump = (target, key, quantity) => {
    target[key] = (target[key] || 0) + quantity;
  };

  const queue = [sessionRoot];
  while (queue.length) {
    const directory = queue.pop();
    let entries;
    try {
      entries = await readdir(directory, {withFileTypes: true});
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        queue.push(absolute);
        continue;
      }
      if (!entry.isFile() || entry.name !== 'usage_log.jsonl') continue;
      let text;
      try {
        text = await readFile(absolute, 'utf8');
      } catch {
        continue;
      }
      for (const line of text.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        let record;
        try {
          record = JSON.parse(trimmed);
        } catch {
          continue;
        }
        const kind = record && typeof record.kind === 'string' ? record.kind : '';
        if (kind !== 'image' && kind !== 'video' && kind !== 'audio') continue;
        const quantity = Number.isFinite(Number(record.quantity)) ? Math.max(1, Number(record.quantity)) : 1;
          const rawModel = String(record.model || '').trim();
          const metaModel = String(record.meta && typeof record.meta === 'object' ? record.meta.model || '' : '').trim();
          const model = rawModel || metaModel || (kind === 'video' ? fallbackVideoModel : '') || 'unknown';
          const modelPresentation = resolveModelPresentation(model);
        const resolution = String(record.resolution || 'unknown');
        const purpose = String(record.meta && typeof record.meta === 'object' ? record.meta.purpose || 'other' : 'other');

        totals.entries += 1;
        bump(totals.breakdown.by_kind, kind, quantity);
          bump(totals.breakdown.by_model, modelPresentation.name, quantity);
          const detail = modelDetail[modelPresentation.name] || (modelDetail[modelPresentation.name] = {name: modelPresentation.name, ref: modelPresentation.ref, count: 0});
          detail.count += quantity;
          if (!detail.ref && modelPresentation.ref) detail.ref = modelPresentation.ref;
        bump(totals.breakdown.by_purpose, purpose, quantity);
        bump(totals.breakdown.by_resolution, resolution, quantity);

        const ts = Number.isFinite(Number(record.ts)) ? Number(record.ts) : 0;
        let dateKey = '';
        if (ts > 0) {
          try {
            dateKey = new Date(ts * 1000).toISOString().slice(0, 10);
          } catch {
            dateKey = '';
          }
        }
        if (dateKey) {
          const day = daily[dateKey] || (daily[dateKey] = {date: dateKey, count: 0, image: 0, video: 0, audio: 0});
          day.count += quantity;
          day[kind] = (day[kind] || 0) + quantity;
        }

        if (kind === 'image') {
          totals.image.count += quantity;
            totals.image.models[modelPresentation.name] = (totals.image.models[modelPresentation.name] || 0) + quantity;
        } else if (kind === 'video') {
          const duration = Number.isFinite(Number(record.duration)) ? Number(record.duration) : 0;
          totals.video.count += quantity;
          totals.video.duration_sec += duration * quantity;
          totals.video.by_resolution[resolution] = (totals.video.by_resolution[resolution] || 0) + quantity;
        } else if (kind === 'audio') {
          totals.audio.count += quantity;
          const ttsChars = Math.max(0, Number(record.meta && typeof record.meta === 'object' ? record.meta.tts_chars : 0) || 0);
          totals.audio.tts_chars += ttsChars * quantity;
          if (ttsChars > 0) totals.audio.billable_count += quantity;
        }

        recentAll.push({
           ts,
           kind,
           model,
           model_name: modelPresentation.name,
           model_ref: modelPresentation.ref,
           quantity,
           duration: Number.isFinite(Number(record.duration)) ? Number(record.duration) : 0,
           resolution,
           purpose,
           tts_chars: Math.max(0, Number(record.meta && typeof record.meta === 'object' ? record.meta.tts_chars : 0) || 0),
         });
      }
    }
  }
  totals.breakdown.daily = Object.values(daily).sort((left, right) => left.date.localeCompare(right.date));
  // 模型明细（供预算页展示“真实模型名 + 端点 ID”双行），按调用量倒序
  totals.breakdown.by_model_detail = Object.values(modelDetail).sort((left, right) => right.count - left.count);
  recentAll.sort((left, right) => right.ts - left.ts);
  totals.recent = recentAll.slice(0, recentLimit);
  totals.recent_by_kind = {
    image: recentAll.filter((entry) => entry.kind === 'image').slice(0, recentLimit),
    video: recentAll.filter((entry) => entry.kind === 'video').slice(0, recentLimit),
    audio: recentAll.filter((entry) => entry.kind === 'audio').slice(0, recentLimit),
  };
  totals.generated_at = new Date().toISOString();
  return totals;
}

export function resolveArtifactPath(repoRoot, sessionId, relativePath) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const candidate = path.resolve(sessionRoot, String(relativePath || ''));
  if (candidate === sessionRoot || !candidate.startsWith(`${sessionRoot}${path.sep}`)) {
    throw new Error('产物路径超出当前活动会话');
  }
  return candidate;
}

// P2：服务端视频缩略图（poster）——ffmpeg 抽帧缓存到 <video>.poster.jpg，
// 前端产物卡片只展示 poster，不再逐视频 seek+canvas 截帧（多视频页面不再长期转圈）。
export function resolveVideoPosterPath(sessionRoot, relativePath) {
  const abs = path.join(sessionRoot, String(relativePath || '').split('/').join(path.sep));
  return `${abs}.poster.jpg`;
}

export function artifactContentType(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  return {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.gif': 'image/gif',
    '.mp4': 'video/mp4',
    '.webm': 'video/webm',
    '.mov': 'video/quicktime',
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.ogg': 'audio/ogg',
    '.aac': 'audio/aac',
    '.flac': 'audio/flac',
    '.json': 'application/json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
}[extension] ?? 'application/octet-stream';
}

/** Parse a single HTTP byte range. Returns null for malformed/unsupported ranges. */
export function parseByteRange(rangeHeader, size) {
  const total = Number(size);
  if (!Number.isFinite(total) || total <= 0) return null;
  const match = /^bytes=(\d*)-(\d*)$/i.exec(String(rangeHeader || '').trim());
  if (!match) return null;
  const [, rawStart, rawEnd] = match;
  if (!rawStart && !rawEnd) return null;

  let start;
  let end;
  if (!rawStart) {
    const suffixLength = Number(rawEnd);
    if (!Number.isInteger(suffixLength) || suffixLength <= 0) return null;
    start = Math.max(0, total - suffixLength);
    end = total - 1;
  } else {
    start = Number(rawStart);
    end = rawEnd ? Number(rawEnd) : total - 1;
    if (!Number.isInteger(start) || !Number.isInteger(end)) return null;
    if (start < 0 || start >= total || end < start) return null;
    end = Math.min(end, total - 1);
  }
  return {start, end, length: end - start + 1};
}

const RESERVED_PRODUCTION_PREFIXES = [
  '/api',
  '/assets',
  '/src',
  '/@fs',
  '/@react-refresh',
  '/@vite',
  '/node_modules',
];

export function resolveProductionRequest(distRoot, pathname, fileExists) {
  const resolvedDistRoot = path.resolve(distRoot);
  const indexPath = path.join(resolvedDistRoot, 'index.html');
  const requestPath = String(pathname || '/');
  const requested = requestPath === '/' ? 'index.html' : requestPath.replace(/^\/+/, '');
  const candidate = path.resolve(resolvedDistRoot, requested);
  const withinDist = candidate === resolvedDistRoot || candidate.startsWith(`${resolvedDistRoot}${path.sep}`);

  if (!withinDist) {
    return {kind: 'missing', filePath: '', cacheControl: 'no-store'};
  }

  if (fileExists(candidate)) {
    const isHtml = path.extname(candidate).toLowerCase() === '.html';
    return {
      kind: 'file',
      filePath: candidate,
      cacheControl: isHtml ? 'no-cache, no-store, must-revalidate' : 'public, max-age=31536000, immutable',
    };
  }

  const isReserved = RESERVED_PRODUCTION_PREFIXES.some(
    (prefix) => requestPath === prefix || requestPath.startsWith(`${prefix}/`),
  );
  const hasFileExtension = path.extname(requestPath) !== '';
  if (isReserved || hasFileExtension) {
    return {kind: 'missing', filePath: '', cacheControl: 'no-store'};
  }

  return {
    kind: 'spa',
    filePath: indexPath,
    cacheControl: 'no-cache, no-store, must-revalidate',
  };
}

/**
 * 旧标签页可能还引用上一轮构建的 /assets/index-*.js|css。
 * 当旧文件已经被新构建淘汰时，从当前 index.html 找到同类型入口包继续服务，
 * 避免用户刷新后因指纹资源 404 停在白板。
 */
export function resolveProductionEntryAsset(indexHtml, pathname) {
  const requestPath = String(pathname || '');
  const extension = path.extname(requestPath).toLowerCase();
  if (!/^\/assets\/index-[A-Za-z0-9._-]+\.(?:js|css)$/i.test(requestPath)) return '';
  const candidates = [];
  const attributePattern = extension === '.js'
    ? /<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi
    : /<link\b[^>]*\bhref=["']([^"']+)["'][^>]*>/gi;
  for (const match of String(indexHtml || '').matchAll(attributePattern)) {
    const raw = String(match[1] || '').trim();
    if (!raw) continue;
    try {
      const parsed = new URL(raw, 'http://localhost');
      if (parsed.pathname.startsWith('/assets/index-') && parsed.pathname.toLowerCase().endsWith(extension)) {
        candidates.push(parsed.pathname);
      }
    } catch {
      // 忽略无法解析的资源路径。
    }
  }
  return candidates[0] || '';
}

export function resolveSessionRoot(repoRoot, sessionId) {
  assertSessionId(sessionId);
  const workingRoot = path.resolve(repoRoot, '.working_dir');
  const candidate = path.resolve(workingRoot, sessionId);
  if (!candidate.startsWith(`${workingRoot}${path.sep}`)) {
    throw new Error('Session path escapes .working_dir');
  }
  return candidate;
}

/**
 * 标记角色立绘重新生成：清除注册表记录 + 删除已落盘的 sheet.png（含形态变体）。
 * 幂等判据是 registry key（存在即跳过生成），所以"重新生成"必须同时清 registry 与文件，
 * 之后 agent 重跑资产生成阶段，管线会按缺失增量补齐（用最新提示词模板）。
 * @param {string} repoRoot 项目根
 * @param {string} sessionId 会话 id
 * @param {'*' | string[]} target '*'=全部角色；数组=指定角色名
 */
export async function regenerateCharacterPortraits(repoRoot, sessionId, target) {
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const assetRoot = path.join(sessionRoot, 'script2video');
  const registryPath = path.join(assetRoot, 'character_portraits_registry.json');
  const portraitRoot = path.join(assetRoot, 'character_portraits');

  let registry = {};
  try {
    registry = JSON.parse(await readFile(registryPath, 'utf8'));
  } catch {
    registry = {};
  }

  async function removeSheetsForDir(dirPath) {
    await rm(path.join(dirPath, 'sheet.png'), {force: true}).catch(() => {});
    const variantsDir = path.join(dirPath, 'variants');
    try {
      const variants = await readdir(variantsDir, {withFileTypes: true});
      for (const variant of variants) {
        if (variant.isDirectory()) {
          await rm(path.join(variantsDir, variant.name, 'sheet.png'), {force: true}).catch(() => {});
        }
      }
    } catch { /* 无 variants 目录 */ }
  }

  if (target === '*') {
    // 全部重生成：整表清空 + 全角色产物删除（count 按角色短名统计，registry 缺失时不误报 0）
    await rm(registryPath, {force: true}).catch(() => {});
    const allNames = Object.keys(registry);
    const dirNames = [];
    try {
      const entries = await readdir(portraitRoot, {withFileTypes: true});
      for (const entry of entries) {
        if (entry.isDirectory()) {
          dirNames.push(entry.name.includes('_') ? entry.name.slice(entry.name.indexOf('_') + 1) : entry.name);
          await removeSheetsForDir(path.join(portraitRoot, entry.name));
        }
      }
    } catch { /* 无角色目录 */ }
    const names = [...new Set([...allNames, ...dirNames])];
    return {ok: true, mode: 'all', regenerated: names, count: names.length};
  }

  const names = Array.from(new Set((Array.isArray(target) ? target : []).map(String).filter(Boolean)));
  const removed = [];
  for (const name of names) {
    let found = false;
    // registry：主形态 key 与「角色::形态」前缀 key 一并清除
    for (const key of Object.keys(registry)) {
      if (key === name || key.startsWith(`${name}::`)) {
        delete registry[key];
        found = true;
      }
    }
    // 目录匹配 {idx}_{name}（也可能仅 name），删除该角色全部角度图与形态图
    try {
      const entries = await readdir(portraitRoot, {withFileTypes: true});
      for (const entry of entries) {
        if (!entry.isDirectory()) continue;
        const dirName = entry.name;
        const shortName = dirName.includes('_') ? dirName.slice(dirName.indexOf('_') + 1) : dirName;
        if (shortName !== name) continue;
        await removeSheetsForDir(path.join(portraitRoot, dirName));
        found = true;
      }
    } catch { /* 无角色目录 */ }
    if (found) removed.push(name);
  }
  if (removed.length > 0) {
    await writeFile(registryPath, JSON.stringify(registry, null, 2), 'utf8').catch(() => {});
  }
  return {ok: true, mode: 'partial', regenerated: removed, count: removed.length};
}

function assertSessionId(sessionId) {
  if (!/^[A-Za-z0-9][A-Za-z0-9-]{0,95}$/.test(String(sessionId || ''))) {
    throw new Error('无效的会话 id');
  }
}

function validateUploadName(fileName) {
  const value = String(fileName || '').normalize('NFC').trim();
  if (!value || value === '.' || value === '..') throw new Error('需要有效的文件名');
  if (value.length > 180) throw new Error('文件名不能超过 180 个字符');
  if (/[\\/\u0000-\u001f\u007f]/.test(value)) throw new Error('文件名包含不支持的字符');
  return value;
}

async function removeSessionLogRecords(repoRoot, sessionId) {
  const logsRoot = path.join(repoRoot, '.vimax', 'logs');
  let entries;
  try {
    entries = await readdir(logsRoot, {withFileTypes: true});
  } catch {
    return;
  }
  for (const entry of entries) {
    if (!entry.isFile() || !/\.jsonl(?:\.\d+)?$/i.test(entry.name)) continue;
    const logPath = path.join(logsRoot, entry.name);
    const lines = (await readFile(logPath, 'utf8')).split(/\r?\n/).filter(Boolean);
    const retained = lines.filter((line) => {
      try {
        const record = JSON.parse(line);
        const recordSessionId = record.session_id
          ?? record.sessionId
          ?? record.session?.session_id
          ?? record.context?.session_id
          ?? record.metadata?.session_id;
        return recordSessionId !== sessionId;
      } catch {
        return true;
      }
    });
    if (retained.length === lines.length) continue;
    const temporaryPath = `${logPath}.${process.pid}.tmp`;
    await writeFile(temporaryPath, retained.length ? `${retained.join('\n')}\n` : '', {mode: 0o600});
    await rename(temporaryPath, logPath);
  }
  await removeSessionIndexRecords(logsRoot, sessionId);
}

async function removeSessionIndexRecords(logsRoot, sessionId) {
  const indexPath = path.join(logsRoot, 'log_index.sqlite3');
  try {
    await stat(indexPath);
  } catch {
    return;
  }
  let database;
  try {
    const {DatabaseSync} = await import('node:sqlite');
    database = new DatabaseSync(indexPath);
    database.exec('PRAGMA busy_timeout = 5000');
    database
      .prepare("DELETE FROM loop_turns WHERE log_name = 'loop_history' AND session_id = ?")
      .run(sessionId);
  } catch {
    // 没有索引时仍按 JSONL 回退，删除流程不受影响。
  } finally {
    try {
      database?.close();
    } catch {
      // ignore close failure
    }
  }
}

async function readLoopHistoryLines(logsRoot) {
  let entries;
  try {
    entries = await readdir(logsRoot, {withFileTypes: true});
  } catch {
    return [];
  }

  const files = entries
    .filter((entry) => entry.isFile() && /^loop_history\.jsonl(?:\.\d+)?$/i.test(entry.name))
    .map((entry) => ({
      name: entry.name,
      rank: entry.name === 'loop_history.jsonl' ? 0 : Number(entry.name.slice('loop_history.jsonl.'.length)),
    }))
    .sort((left, right) => right.rank - left.rank);

  const lines = [];
  for (const file of files) {
    try {
      const text = await readFile(path.join(logsRoot, file.name), 'utf8');
      lines.push(...text.split(/\r?\n/).filter(Boolean));
    } catch {
      // Rotation can briefly remove the current file while the next one is created.
      // Other archives remain readable, so do not turn the whole history into a blank page.
    }
  }
  return lines;
}

function isFinalHistoryRecord(record) {
  if (!record || typeof record !== 'object') return false;
  const status = String(record.status || '');
  return Boolean(record.final_assistant_text)
    || status === 'completed'
    || status === 'failed'
    || status === 'cancelled'
    || status === 'halted';
}

function sanitizeSession(record) {
  return {
    sessionId: String(record.session_id ?? ''),
    projectName: String(record.project_name ?? ''),
    workingDir: String(record.working_dir ?? ''),
    stage: String(record.stage ?? 'created'),
    summary: String(record.summary ?? ''),
    progressStage: String(record.progressStage ?? ''),
    idea: String(record.idea ?? ''),
    updatedAt: String(record.updated_at ?? record.created_at ?? ''),
    createdAt: String(record.created_at ?? ''),
    compactionTurns: Number(record.compacted_turns ?? 0),
    workflowPlan: record.workflow_plan && typeof record.workflow_plan === 'object' && !Array.isArray(record.workflow_plan)
      ? record.workflow_plan
      : {},
  };
}

// ---------- 画布：保存镜头编辑（生产画布写回分镜文件） ----------
const CANVAS_SHOT_EDITABLE_FIELDS = [
  'visual_desc',
  'ff_desc',
  'lf_desc',
  'motion_desc',
  'audio_desc',
  'action_breakdown',
  'transition_type',
  // 连贯表演提示词：渲染时作为本镜主叙事注入（此前只读，画布上改了也不生效）
  'video_prompt',
];

export async function saveCanvasShot(repoRoot, sessionId, shotIdx, patch, epId = null) {
  assertSessionId(sessionId);
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  // P1：集模式（epId 非空）写集级 shots；根模式写 script2video/shots（单集/旧项目兼容）。
  // 与 readCanvas 的读取路径严格同源：epId 时读 episodes/{ep}/shots/{idx}。
  // epId 来自 URL 查询参数，必须与 readCanvas/清理接口一致做格式与越界校验，
  // 否则 "../.." 之类的值会绕过 episodes/ 写到工作区其它目录。
  if (epId && !/^ep[A-Za-z0-9_-]{1,63}$/.test(String(epId))) {
    throw new Error('ep_id 格式无效');
  }
  const shotsRoot = epId
    ? path.join(sessionRoot, 'script2video', 'episodes', String(epId), 'shots')
    : path.join(sessionRoot, 'script2video', 'shots');
  if (epId) {
    const episodesRoot = path.resolve(sessionRoot, 'script2video', 'episodes');
    const resolvedShots = path.resolve(shotsRoot);
    if (!resolvedShots.startsWith(`${episodesRoot}${path.sep}`)) {
      throw new Error('ep_id 路径越界');
    }
  }
  const shotDir = path.join(shotsRoot, String(shotIdx));
  const descPath = path.join(shotDir, 'shot_description.json');
  let desc = {};
  try {
    desc = JSON.parse(await readFile(descPath, 'utf8'));
  } catch {
    // 文件不存在时以空对象起始
  }
  if (typeof desc !== 'object' || desc === null) desc = {};

  const next = {...desc};
  let changed = false;
  for (const field of CANVAS_SHOT_EDITABLE_FIELDS) {
    if (typeof patch[field] === 'string') {
      const value = patch[field].trim();
      if (String(next[field] ?? '') !== value) {
        next[field] = value;
        changed = true;
      }
    }
  }
  // 数值字段：duration / cam_idx（null = 清空；非空才做数值转换）
  for (const field of ['duration', 'cam_idx']) {
    if (patch[field] === null) {
      if (next[field] !== null) {
        next[field] = null;
        changed = true;
      }
      continue;
    }
    if (patch[field] !== undefined) {
      const raw = Number(patch[field]);
      const value = Number.isFinite(raw) ? raw : null;
      if (next[field] !== value) {
        next[field] = value;
        changed = true;
      }
    }
  }
  if (!changed) return {ok: true, changed: false, idx: shotIdx};

  try {
    await mkdir(shotDir, {recursive: true});
    const temporaryPath = `${descPath}.${process.pid}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(next, null, 2)}\n`, 'utf8');
    await rename(temporaryPath, descPath);
  } catch (error) {
    throw new Error(`保存镜头 ${shotIdx} 失败：${String(error.message || error)}`);
  }
  return {ok: true, changed: true, idx: shotIdx};
}

// ---------- 画布：文本备注节点（LibTV 风格 Manual Text，落盘持久化） ----------
const CANVAS_NOTE_MAX = 200;

export async function readCanvasNotes(repoRoot, sessionId) {
  assertSessionId(sessionId);
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const notesPath = path.join(sessionRoot, '.working_dir', 'canvas_notes.json');
  try {
    const payload = JSON.parse(await readFile(notesPath, 'utf8'));
    if (!Array.isArray(payload)) return [];
    return payload.filter((note) => note && typeof note.id === 'string').map((note) => ({
      id: String(note.id),
      text: String(note.text ?? ''),
      x: Number.isFinite(Number(note.x)) ? Number(note.x) : 0,
      y: Number.isFinite(Number(note.y)) ? Number(note.y) : 0,
      updatedAt: String(note.updatedAt ?? ''),
    })).slice(0, CANVAS_NOTE_MAX);
  } catch {
    return [];
  }
}

export async function saveCanvasNotes(repoRoot, sessionId, notes) {
  assertSessionId(sessionId);
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  if (!Array.isArray(notes)) throw new Error('notes 必须是数组');
  const clean = notes
    .filter((note) => note && typeof note.id === 'string')
    .slice(0, CANVAS_NOTE_MAX)
    .map((note) => ({
      id: String(note.id).slice(0, 64),
      text: String(note.text ?? '').slice(0, 4000),
      x: Number.isFinite(Number(note.x)) ? Number(note.x) : 0,
      y: Number.isFinite(Number(note.y)) ? Number(note.y) : 0,
      updatedAt: String(note.updatedAt ?? new Date().toISOString()),
    }));
  const dir = path.join(sessionRoot, '.working_dir');
  const notesPath = path.join(dir, 'canvas_notes.json');
  try {
    await mkdir(dir, {recursive: true});
    const temporaryPath = `${notesPath}.${process.pid}.tmp`;
    await writeFile(temporaryPath, `${JSON.stringify(clean, null, 2)}\n`, 'utf8');
    await rename(temporaryPath, notesPath);
  } catch (error) {
    throw new Error(`保存画布备注失败：${String(error.message || error)}`);
  }
  return {ok: true, count: clean.length};
}

// 通用内容节点（复刻 BeefTV 画布：文本/图片/音频/视频空节点 + 节点上生成）
const CANVAS_MEDIA_NODE_MAX = 200;

export async function readCanvasMediaNodes(repoRoot, sessionId) {
  assertSessionId(sessionId);
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  const path_ = path.join(sessionRoot, '.working_dir', 'canvas_media_nodes.json');
  try {
    const payload = JSON.parse(await readFile(path_, 'utf8'));
    if (!Array.isArray(payload)) return [];
    return payload
      .filter((n) => n && typeof n.id === 'string')
      .map((n) => ({
        id: String(n.id),
        kind: ['text', 'image', 'audio', 'video'].includes(n.kind) ? n.kind : 'image',
        title: String(n.title ?? '').slice(0, 80),
        prompt: String(n.prompt ?? '').slice(0, 4000),
        mediaUrl: typeof n.mediaUrl === 'string' ? n.mediaUrl.slice(0, 500) : '',
        status: ['idle', 'generating', 'done', 'failed'].includes(n.status) ? n.status : 'idle',
        x: Number.isFinite(Number(n.x)) ? Number(n.x) : 0,
        y: Number.isFinite(Number(n.y)) ? Number(n.y) : 0,
        updatedAt: String(n.updatedAt ?? ''),
        refIds: Array.isArray(n.refIds)
          ? n.refIds.filter((r) => typeof r === 'string').map((r) => r.slice(0, 64)).slice(0, 24)
          : [],
      }))
      .slice(0, CANVAS_MEDIA_NODE_MAX);
  } catch {
    return [];
  }
}

export async function saveCanvasMediaNodes(repoRoot, sessionId, nodes) {
  assertSessionId(sessionId);
  const sessionRoot = resolveSessionRoot(repoRoot, sessionId);
  if (!Array.isArray(nodes)) throw new Error('mediaNodes 必须是数组');
  const clean = nodes
    .filter((n) => n && typeof n.id === 'string')
    .slice(0, CANVAS_MEDIA_NODE_MAX)
    .map((n) => ({
      id: String(n.id).slice(0, 64),
      kind: ['text', 'image', 'audio', 'video'].includes(n.kind) ? n.kind : 'image',
      title: String(n.title ?? '').slice(0, 80),
      prompt: String(n.prompt ?? '').slice(0, 4000),
      mediaUrl: typeof n.mediaUrl === 'string' ? n.mediaUrl.slice(0, 500) : '',
      status: ['idle', 'generating', 'done', 'failed'].includes(n.status) ? n.status : 'idle',
      x: Number.isFinite(Number(n.x)) ? Number(n.x) : 0,
      y: Number.isFinite(Number(n.y)) ? Number(n.y) : 0,
      updatedAt: String(n.updatedAt ?? new Date().toISOString()),
      refIds: Array.isArray(n.refIds)
        ? n.refIds.filter((r) => typeof r === 'string').map((r) => r.slice(0, 64)).slice(0, 24)
        : [],
    }));
  const dir = path.join(sessionRoot, '.working_dir');
  const file = path.join(dir, 'canvas_media_nodes.json');
  await mkdir(dir, {recursive: true});
  const tmp = `${file}.${process.pid}.tmp`;
  await writeFile(tmp, `${JSON.stringify(clean, null, 2)}\n`, 'utf8');
  await rename(tmp, file);
  return {ok: true, count: clean.length};
}
