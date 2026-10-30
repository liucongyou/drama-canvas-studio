/**
 * 画布布局纯函数（从 CanvasView 抽取，便于单测与复用）。
 *
 * 覆盖三类易错逻辑：
 * ① 段→镜头解析：shot_idxs 缺失时的回退必须按集过滤，否则整剧总览模式下
 *    ep01/ep02 同段号会互串镜头（跨集串镜 bug）。
 * ② 节点绝对坐标：整剧总览模式下属节点（段卡是 ep-box 子节点）的 position 是
 *    相对父节点的坐标，直接当绝对坐标用会导致定位/搜索跳错位置（通常跳左上角）。
 * ③ 用户边 id：Date.now()+length 在同毫秒同长度下会碰撞，改自增序列。
 */

export type CanvasShotLike = {
  idx: number;
  segment_id?: number | null;
  ep_id?: string | null;
};

export type CanvasSegmentLike = {
  segment_id: number;
  shot_idxs?: number[] | null;
  ep_id?: string | null;
};

/** 段内镜头索引：优先 seg.shot_idxs；回退按 segment_id 匹配（总览模式追加 ep_id 守卫）。 */
export function segShotIdxsOf(
  seg: CanvasSegmentLike,
  shots: CanvasShotLike[],
  options: {overview?: boolean} = {},
): number[] {
  if (Array.isArray(seg.shot_idxs) && seg.shot_idxs.length > 0) return seg.shot_idxs;
  const overview = options.overview ?? false;
  const segEp = seg.ep_id ?? null;
  return shots
    .filter((s) => {
      if (String(s.segment_id) !== String(seg.segment_id)) return false;
      // 总览模式：同段号跨集存在，必须同集才算本段镜头
      if (overview && segEp !== null && (s.ep_id ?? null) !== segEp) return false;
      return true;
    })
    .map((s) => s.idx);
}

export type FallbackSegment = {
  segment_id: number;
  /** 段所属集：整剧总览按它区分同段号，缺失会让段卡 id 退化成 seg-ep00-N */
  ep_id?: string | undefined;
  shot_idxs: number[];
};

/**
 * Resolve the initial episode from URL and per-project local memory.
 * URL wins so a shared/bookmarked link opens the intended episode; local
 * memory restores the user's last selection; null means the caller may use
 * the project's default episode policy.
 */
export function resolveInitialEpisode(
  search: string,
  remembered: string | null | undefined,
): string | null {
  let fromUrl = '';
  try {
    fromUrl = new URLSearchParams(search).get('ep')?.trim() || '';
  } catch {
    fromUrl = '';
  }
  if (fromUrl) return fromUrl.toUpperCase();
  const stored = String(remembered || '').trim();
  return stored ? stored.toUpperCase() : null;
}

/**
 * 无 segments.json 时的兜底分组（纯函数，便于单测）。
 * 三条硬性约束：
 * ① 必须带 ep_id —— 否则总览模式段卡 id（seg-ep00-N）与时间线按镜头 ep_id 拼出的
 *    id（seg-ep01-N）对不上，点击定位静默失效。
 * ② 段号一律 0 基，与 shot.segment_id 对齐。
 * ③ 跨集同段号不得合并（ep01 段0 与 ep02 段0 是两段）。
 */
export function fallbackSegmentsOf(shots: CanvasShotLike[]): FallbackSegment[] {
  const sorted = [...shots].sort((a, b) => a.idx - b.idx);
  const epOf = (list: CanvasShotLike[]): string | undefined => {
    for (const s of list) if (s.ep_id) return s.ep_id;
    return undefined;
  };
  const multiEp = new Set(sorted.map((s) => s.ep_id ?? '')).size > 1;
  const keyOf = (s: CanvasShotLike): string => {
    const sid = typeof s.segment_id === 'number' ? s.segment_id : 0;
    return multiEp ? `${s.ep_id ?? ''}#${sid}` : String(sid);
  };
  const byKey = new Map<string, CanvasShotLike[]>();
  sorted.forEach((s) => {
    const key = keyOf(s);
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key)!.push(s);
  });
  const segIdOf = (key: string): number => {
    const raw = key.includes('#') ? key.slice(key.indexOf('#') + 1) : key;
    const n = Number(raw);
    return Number.isFinite(n) ? n : 0;
  };
  const keys = [...byKey.keys()].sort((a, b) => segIdOf(a) - segIdOf(b) || a.localeCompare(b));
  if (keys.length > 1) {
    return keys.map((key) => ({
      segment_id: segIdOf(key),
      ep_id: epOf(byKey.get(key)!),
      shot_idxs: byKey.get(key)!.map((s) => s.idx),
    }));
  }
  // 真未分段（全部同段号）：每 4 镜一段，段号 0 基
  const out: FallbackSegment[] = [];
  for (let i = 0; i < sorted.length; i += 4) {
    const chunk = sorted.slice(i, i + 4);
    out.push({segment_id: Math.floor(i / 4), ep_id: epOf(chunk), shot_idxs: chunk.map((s) => s.idx)});
  }
  return out;
}

export type FlowNodeLike = {
  id: string;
  position: {x: number; y: number};
  parentId?: string;
};

/**
 * 节点绝对坐标：沿 parentId 链累加相对位移。
 * 顶层节点直接返回 position；子节点（段卡挂在 ep-box 下）累加父链。
 * 找不到节点时返回 null（调用方决定回退行为）。
 */
export function absolutePositionOf(
  nodeId: string,
  nodes: FlowNodeLike[],
): {x: number; y: number} | null {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  let node = byId.get(nodeId);
  if (!node) return null;
  let x = node.position.x;
  let y = node.position.y;
  let guard = 0;
  while (node?.parentId) {
    const parent = byId.get(node.parentId);
    if (!parent) break;
    x += parent.position.x;
    y += parent.position.y;
    node = parent;
    guard += 1;
    if (guard > 32) break; // 防御父链成环
  }
  return {x, y};
}

/**
 * 拖动对齐参考线（对标即梦 / Figma 的智能参考线）。
 *
 * 纯函数，便于单测。输入是「被拖动节点的盒子」与「画布上其它节点的盒子」，
 * 输出：需要施加的对齐修正量 + 应当绘制出来的参考线。
 *
 * 三条硬性约束：
 * ① **只在阈值内吸附**（默认 6px）。否则节点会被"隔空吸"到远处的线上，手感很怪。
 * ② **每个方向只取一条线**：即梦/Figma 都是拖动时最多显示 1 条竖线 + 1 条横线，
 *    取偏差最小的那条，避免节点周围糊满参考线。
 * ③ **只考虑 3 个锚点**（左/中/右、上/中/下）：够用且覆盖 90% 的对齐诉求。
 */
export type AlignBox = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
};

export type AlignGuide = {
  /** 'v' = 竖直参考线（x 相同）；'h' = 水平参考线（y 相同） */
  axis: 'v' | 'h';
  /** 参考线在画布坐标上的位置（竖线是 x，横线是 y） */
  pos: number;
  /** 参考线覆盖的区间（另一个轴上的起止），用来只画到相关节点那一段 */
  from: number;
  to: number;
  /**
   * 被拖盒子相对参考线的**剩余偏差**（画布 px）。
   * 0 = 已完全对齐；拖动中磁吸过渡带会给出非 0 值。
   * 用途：参考线上的读数标签（即梦/Figma 同款），让用户看得出"还差多少"。
   */
  delta?: number;
};

export type AlignSnapResult = {
  /** 施加到被拖动节点上的位移（画布坐标） */
  dx: number;
  dy: number;
  guides: AlignGuide[];
};

/**
 * 单轴的吸附锁定态。
 *
 * 「sticky 吸附」是即梦 / Figma 的手感关键：一旦某轴对齐到参考线，
 * 节点就被"磁"在该线上，**不会**因为指针继续移动而弹回未对齐位置；
 * 只有当指针明显拖离（超过 break 距离）才解除锁定。
 */
export type AlignAxisLock = {
  /** 锁定的轴：'v' = 竖直参考线（约束 x），'h' = 水平参考线（约束 y） */
  axis: 'v' | 'h';
  /** 被拖动盒子用的是第几个锚点（0=左/上，1=中，2=右/下） */
  anchorIdx: 0 | 1 | 2;
  /** 对齐后，被拖动盒子的该锚点应当等于 target */
  target: number;
  /** 参考线所在坐标 */
  guidePos: number;
  /** 对齐到的那个对方节点 id（用于实时重算参考线区间） */
  otherId: string;
};

/**
 * 吸附的三个距离档（均为「屏幕像素」语义，调用方需自行除以 zoom 换成画布坐标）：
 *
 * - `enter`：**吸附**阈值。指针进入这个范围就吸上去。
 * - `glue`：**磁住**范围。在范围内节点被牢牢贴在线上（指针继续移动也不会掉下来）。
 * - `release`：**释放**范围。超出后完全跟随指针。
 *
 * glue 与 release 之间是**渐变过渡带**：修正量随偏离距离线性衰减到 0，
 * 所以节点脱离参考线时是"滑出去"而不是"弹出去"——这是即梦/Figma 手感的关键。
 */
export const ALIGN_ENTER_PX = 6;
export const ALIGN_GLUE_PX = 26;
export const ALIGN_RELEASE_PX = 44;
/** 尚未进入吸附距离时，也提前显示参考线的屏幕距离。 */
export const ALIGN_VISIBLE_PX = 20;

/** 阈值（画布坐标 px）。超过这个距离就不吸附、不画线。 */
export const ALIGN_SNAP_THRESHOLD = ALIGN_ENTER_PX;

/**
 * 单轴的吸附状态（跨帧持久）。
 */
export type AlignLock = {
  /** 'v' = 竖直参考线（约束 x）；'h' = 水平参考线（约束 y） */
  axis: 'v' | 'h';
  /** 被拖动盒子用的锚点下标（0=左/上，1=中，2=右/下） */
  anchorIdx: 0 | 1 | 2;
  /** 对齐目标坐标（对方节点的某个锚点） */
  target: number;
  /** 对齐到的对方节点 id */
  otherId: string;
};

/** 一次拖拽过程中的吸附状态。 */
export type AlignState = {
  lockV: AlignLock | null;
  lockH: AlignLock | null;
};

export function createAlignState(): AlignState {
  return {lockV: null, lockH: null};
}

function anchorOffsetX(idx: 0 | 1 | 2, w: number) {
  return idx === 0 ? 0 : idx === 1 ? w / 2 : w;
}
function anchorOffsetY(idx: 0 | 1 | 2, h: number) {
  return idx === 0 ? 0 : idx === 1 ? h / 2 : h;
}

type AlignCandidate = {
  delta: number;
  target: number;
  anchorIdx: 0 | 1 | 2;
  otherId: string;
  from: number;
  to: number;
};

function axisGap(a0: number, a1: number, b0: number, b1: number): number {
  if (a1 < b0) return b0 - a1;
  if (b1 < a0) return a0 - b1;
  return 0;
}

/** 优先选择空间距离最近的可对齐节点，而不是全局偏差最小的远端节点。 */
function findBestCandidate(
  moving: AlignBox,
  others: AlignBox[],
  axis: 'v' | 'h',
  threshold: number,
): AlignCandidate | null {
  let best: (AlignCandidate & {distance: number}) | null = null;
  const movingAnchors = axis === 'v'
    ? [moving.x, moving.x + moving.w / 2, moving.x + moving.w]
    : [moving.y, moving.y + moving.h / 2, moving.y + moving.h];
  for (const o of others) {
    const oAnchors = axis === 'v'
      ? [o.x, o.x + o.w / 2, o.x + o.w]
      : [o.y, o.y + o.h / 2, o.y + o.h];
    const lateralGap = axis === 'v'
      ? axisGap(moving.y, moving.y + moving.h, o.y, o.y + o.h)
      : axisGap(moving.x, moving.x + moving.w, o.x, o.x + o.w);
    for (let ai: 0 | 1 | 2 = 0; ai < 3; ai += 1) {
      for (let oi = 0; oi < 3; oi += 1) {
        const delta = oAnchors[oi] - movingAnchors[ai];
        const absDelta = Math.abs(delta);
        if (absDelta > threshold) continue;
        const distance = Math.hypot(absDelta, lateralGap);
        if (
          !best
          || distance < best.distance
          || (distance === best.distance && absDelta < Math.abs(best.delta))
        ) {
          best = {
            delta,
            target: oAnchors[oi],
            anchorIdx: ai as 0 | 1 | 2,
            otherId: o.id,
            from: axis === 'v'
              ? Math.min(moving.y, o.y)
              : Math.min(moving.x, o.x),
            to: axis === 'v'
              ? Math.max(moving.y + moving.h, o.y + o.h)
              : Math.max(moving.x + moving.w, o.x + o.w),
            distance,
          };
        }
      }
    }
  }
  return best;
}

/**
 * 磁吸对齐求解器（跨帧）。
 *
 * @param raw 被拖动组的包围盒（**原始指针位置**，未修正）
 * @param others 画布上其它可对齐节点
 * @param state 上一帧的状态
 * @param enter 吸附阈值（画布 px）
 * @param glue  磁住范围（画布 px）
 * @param release 释放范围（画布 px）
 */
export function solveAlignSnap(
  raw: AlignBox,
  others: AlignBox[],
  state: AlignState,
  enter: number = ALIGN_ENTER_PX,
  glue: number = ALIGN_GLUE_PX,
  release: number = ALIGN_RELEASE_PX,
): AlignSnapResult & {state: AlignState} {
  const next: AlignState = {lockV: state.lockV, lockH: state.lockH};
  let dx = 0;
  let dy = 0;
  const locked: {axis: 'v' | 'h'; lock: AlignLock}[] = [];

  const solveAxis = (
    axis: 'v' | 'h',
    lockIn: AlignLock | null,
  ): {lock: AlignLock | null; correction: number} => {
    const anchorOf = (idx: 0 | 1 | 2) => (axis === 'v'
      ? raw.x + anchorOffsetX(idx, raw.w)
      : raw.y + anchorOffsetY(idx, raw.h));
    const setLock = (l: AlignLock | null) => (axis === 'v' ? (next.lockV = l) : (next.lockH = l));

    if (lockIn) {
      const dev = anchorOf(lockIn.anchorIdx) - lockIn.target;
      const abs = Math.abs(dev);
      if (abs <= glue) {
        // 完全磁住：修正量把节点钉在线上
        setLock(lockIn);
        return {lock: lockIn, correction: -dev};
      }
      if (abs <= release) {
        // 过渡带：修正量线性衰减 → 节点平滑"滑出"参考线，无跳变
        const w = (release - abs) / Math.max(1e-6, release - glue);
        setLock(lockIn);
        return {lock: lockIn, correction: -dev * w};
      }
      // 超出释放范围：解除锁定，完全跟随指针
      setLock(null);
      return {lock: null, correction: 0};
    }

    // 无锁定：在吸附阈值内优先选择离当前拖动位置最近的对齐节点
    const cand = findBestCandidate(raw, others, axis, enter);
    if (!cand) return {lock: null, correction: 0};
    const lock: AlignLock = {
      axis,
      anchorIdx: cand.anchorIdx,
      target: cand.target,
      otherId: cand.otherId,
    };
    setLock(lock);
    return {lock, correction: cand.delta};
  };

  const v = solveAxis('v', next.lockV);
  const h = solveAxis('h', next.lockH);
  dx = v.correction;
  dy = h.correction;
  if (v.lock) locked.push({axis: 'v', lock: v.lock});
  if (h.lock) locked.push({axis: 'h', lock: h.lock});

  // 参考线区间：覆盖被拖动组（修正后）与对齐目标的投影
  const eff: AlignBox = {
    id: raw.id,
    x: raw.x + dx,
    y: raw.y + dy,
    w: raw.w,
    h: raw.h,
  };
  const guides: AlignGuide[] = [];
  for (const item of locked) {
    const o = others.find((it) => it.id === item.lock.otherId);
    if (!o) continue;
    // 剩余偏差：修正后锚点与参考线的距离（理论上是 0，但过渡带衰减时非 0）。
    // 用与 solveAxis 完全相同的锚点换算，避免两处算法漂移。
    const anchorOfEff = (idx: 0 | 1 | 2) => (item.axis === 'v'
      ? eff.x + anchorOffsetX(idx, eff.w)
      : eff.y + anchorOffsetY(idx, eff.h));
    const delta = Math.round((anchorOfEff(item.lock.anchorIdx) - item.lock.target) * 10) / 10;
    if (item.axis === 'v') {
      guides.push({
        axis: 'v',
        pos: item.lock.target,
        from: Math.min(eff.y, o.y),
        to: Math.max(eff.y + eff.h, o.y + o.h),
        delta,
      });
    } else {
      guides.push({
        axis: 'h',
        pos: item.lock.target,
        from: Math.min(eff.x, o.x),
        to: Math.max(eff.x + eff.w, o.x + o.w),
        delta,
      });
    }
  }

  return {dx, dy, guides, state: next};
}

/**
 * 计算拖动节点相对其它节点的对齐吸附。
 *
 * @param moving 被拖动节点当前盒子
 * @param others 画布上其它节点盒子（不含被拖动节点及其"一起拖"的兄弟节点）
 * @param threshold 吸附阈值（画布坐标 px）
 */
export function computeAlignSnap(
  moving: AlignBox,
  others: AlignBox[],
  threshold: number = ALIGN_SNAP_THRESHOLD,
): AlignSnapResult {
  if (others.length === 0) return {dx: 0, dy: 0, guides: []};
  const bestX = findBestCandidate(moving, others, 'v', threshold);
  const bestY = findBestCandidate(moving, others, 'h', threshold);

  const guides: AlignGuide[] = [];
  if (bestX) guides.push({
    axis: 'v',
    pos: bestX.target,
    from: bestX.from,
    to: bestX.to,
    delta: Math.round(bestX.delta * 10) / 10,
  });
  if (bestY) guides.push({
    axis: 'h',
    pos: bestY.target,
    from: bestY.from,
    to: bestY.to,
    delta: Math.round(bestY.delta * 10) / 10,
  });
  return {dx: bestX ? bestX.delta : 0, dy: bestY ? bestY.delta : 0, guides};
}

/** 用户边 id 生成器：自增序列，同会话内不碰撞（替代 Date.now()+length）。 */
export function createUserEdgeIdFactory(prefix = 'e-user'): () => string {
  let seq = 0;
  return () => {
    seq += 1;
    return `${prefix}-${seq}-${Math.random().toString(36).slice(2, 8)}`;
  };
}

export function validUserConnections<T extends {
  id?: string;
  fromNodeId: string;
  toNodeId: string;
}>(connections: readonly T[], nodeIds: Iterable<string>): T[] {
  const validNodeIds = new Set(nodeIds);
  const seen = new Set<string>();
  const result: T[] = [];
  const pairKey = (fromNodeId: string, toNodeId: string) => (
    fromNodeId < toNodeId
      ? `${fromNodeId}\u0000${toNodeId}`
      : `${toNodeId}\u0000${fromNodeId}`
  );
  for (const connection of connections) {
    const fromNodeId = String(connection.fromNodeId || '').trim();
    const toNodeId = String(connection.toNodeId || '').trim();
    if (!fromNodeId || !toNodeId || fromNodeId === toNodeId) continue;
    if (!validNodeIds.has(fromNodeId) || !validNodeIds.has(toNodeId)) continue;
    const pair = pairKey(fromNodeId, toNodeId);
    if (seen.has(pair)) continue;
    seen.add(pair);
    result.push({...connection, fromNodeId, toNodeId});
  }
  return result;
}

export type CanvasEpisodeMetaLike = {
  ep_id: string;
  shot_count?: number | null;
  keyframes?: number | null;
  clips?: number | null;
  storyboard_ready?: boolean | null;
  final_ready?: boolean | null;
  updated_at?: string | null;
};

/** 集号自然序比较（ep2 < ep10，字符串比较会反）。 */
function epIdCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, {numeric: true});
}

/**
 * 默认进入哪一集（纯函数，便于单测）。
 *
 * 三条必须同时成立的约束，缺一个就会踩用户可见的坑：
 * ① **必须选有内容的集**。旧逻辑只看 updated_at —— 刚拆完 120 集剧本时最后被
 *    触碰的一集往往只有 0 镜（实测 ep02），于是画布默认开在空集上，让人以为
 *    「场景/镜头都不显示」。现在先筛出有镜头/关键帧/片段的集。
 * ② 有内容的集里选最近活动的（updated_at 最新），符合「继续上次在做的一集」。
 * ③ **全空时回退集号最小的一集**（第 1 集）。旧逻辑回退 episodes[0] 靠目录读取顺序，
 *    不稳定；而 updated_at 全空时 reduce 会取到最后一集（120 集剧本直接跳第 120 集）。
 */
export function pickDefaultEpisode(episodes: CanvasEpisodeMetaLike[] | null | undefined): string | null {
  const list = (episodes ?? []).filter((ep) => Boolean(ep?.ep_id));
  if (list.length === 0) return null;
  const hasContent = (ep: CanvasEpisodeMetaLike): boolean =>
    (ep.shot_count ?? 0) > 0 ||
    (ep.keyframes ?? 0) > 0 ||
    (ep.clips ?? 0) > 0 ||
    Boolean(ep.storyboard_ready) ||
    Boolean(ep.final_ready);
  const withContent = list.filter(hasContent);
  if (withContent.length === 0) {
    return [...list].sort((a, b) => epIdCompare(a.ep_id, b.ep_id))[0].ep_id;
  }
  const withTime = withContent.filter((ep) => Boolean(ep.updated_at));
  if (withTime.length > 0) {
    return withTime.reduce((a, b) => (String(a.updated_at) >= String(b.updated_at) ? a : b)).ep_id;
  }
  // 有内容但缺时间戳：按产出量排序（片段 > 关键帧 > 镜头），平手取集号小的
  const amount = (ep: CanvasEpisodeMetaLike): number =>
    (ep.clips ?? 0) * 1000 + (ep.keyframes ?? 0) * 10 + (ep.shot_count ?? 0);
  return withContent.reduce((a, b) => {
    if (amount(b) !== amount(a)) return amount(b) > amount(a) ? b : a;
    return epIdCompare(b.ep_id, a.ep_id) < 0 ? b : a;
  }).ep_id;
}

export function workspaceScopeMatches(
  requested: {session: string; episode: string | null},
  active: {session: string; episode: string | null},
): boolean {
  return (
    requested.session === active.session
    && (requested.episode || null) === (active.episode || null)
  );
}
