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
 *
 * ④ **候选排序：先"齐"，再"近"**（见 findBestCandidate）。这一条是被真实使用打回来补的：
 *    早先按 `hypot(偏差, 垂直空隙)` 排序，等于让"离得近"压过"对得齐"。画布上卡片是
 *    按列排的，想让下面一张卡的左边缘对上**正上方**那张（垂直空隙几百 px），
 *    它的 3px 偏差（score≈95）永远输给同排邻居的 20px 偏差（score=20），
 *    于是每往上挪一点就被旁边的卡吸走，主观感受就是"很难跟上面对齐"。
 *    现在的规则：偏差更小的优先；偏差相差不到 ALIGN_DELTA_EPS 时，才用空隙近的。
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
   * 这条线是「跟哪张卡」对上了（第 28 轮：给被对齐的卡片描 1px 强调色边）。
   *
   * 虚线本身只回答"对上了没有"，回答不了"跟谁对上了"——画布上一列排着五张卡时，
   * 用户看到一根线还得自己去找另一端。描边把配对关系补上：线一出现，
   * 对面那张卡同时亮起同色的 1px 边。
   *
   * 取**最近**的那张（同一条边上可能同时压着好几张卡，全描会闪成一片）。
   */
  peerId?: string;
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
/**
 * ★ 2026-11-02 第 26 轮（对齐即梦）：三档距离全部收窄。
 *   旧值 6 / 26 / 44 的问题是 `glue=26` 太宽 —— 指针在 26 屏幕 px 内移动时
 *   卡片一动不动（被钉在线上），超过 26 才突然跟手，主观感受是"卡住→猛跳"。
 *   即梦的手感是：只有极短的一小段是"吸住"，其余全程跟手、线性滑出。
 *   现在 8 / 14 / 22：吸附带更宽（8px 更好命中），钉住区分 14px 已经很短，
 *   14→22 是平滑滑出带，22px 之外完全跟随指针。
 */
export const ALIGN_ENTER_PX = 8;
export const ALIGN_GLUE_PX = 14;
export const ALIGN_RELEASE_PX = 22;
/** 对齐判定容差（画布 px）：修正后锚点与目标边距离小于它，才认为"已对齐"并画线。 */
export const ALIGN_DONE_EPS = 0.5;

/**
 * 吸附修正量的逐帧缓动系数（第 30 轮）。
 *
 * 以前求出的 dx/dy 是**一次加满**的：指针刚跨进 enter 阈值（8 屏幕 px）的那一帧，
 * 卡片瞬间横移 8px —— 主观感受是"啪一下被拽过去、还顿一下"。
 * 即梦 / Figma 的手感是"被吸过去"：线先出现，卡片再滑过去贴上。
 *
 * 每帧只补掉差值的一部分（默认一半），4 帧内收敛（60fps ≈ 67ms）；
 * 残差小于半个屏幕像素时直接取齐，避免浮点尾巴让卡片永远差 0.0x px。
 */
export const ALIGN_SNAP_EASE = 0.52;

/**
 * 把"上一帧实际施加的修正量"朝"这一帧的目标修正量"推进一帧。
 *
 * @param applied 上一帧实际施加的修正量（画布 px）
 * @param target  这一帧求解器给出的目标修正量（画布 px）
 * @param eps     收敛阈值（画布 px）：差值小于它就取齐
 * @param k       缓动系数，0 < k <= 1（1 = 不缓动，等价于旧行为）
 */
export function easeAlignSnap(
  applied: number,
  target: number,
  eps = 0.5,
  k = ALIGN_SNAP_EASE,
): number {
  if (!Number.isFinite(target)) return 0;
  const cur = Number.isFinite(applied) ? applied : 0;
  const diff = target - cur;
  if (Math.abs(diff) <= eps) return target;
  const step = Math.max(0.01, Math.min(1, k));
  const next = cur + diff * step;
  return Math.abs(target - next) <= eps ? target : next;
}

/**
 * 参考线在屏幕上的实际线宽（CSS px）。
 *
 * 100% 缩放下一根 1px 的虚线在"内容满满"的画布上是刚好的；但缩到 30% 以下时，
 * 用户离得远、一根 1px 线会被同屏的卡框和连线淹没，反馈几乎看不见。
 * 这里只在**低缩放档**把它加到 1.5px —— 不是全局变粗，正常编辑的观感一个像素都不变。
 *
 * 阈值取 35%：再低一档（小地图视角）基本只在看整体排布，需要更醒目的结构线提示。
 */
export const ALIGN_GUIDE_THICK_ZOOM = 0.35;

/** 松手后参考线原样停留的时长（ms）。 */
export const ALIGN_GUIDE_HOLD_MS = 120;
/** 停留结束后淡出的时长（ms）。 */
export const ALIGN_GUIDE_FADE_MS = 140;

/**
 * 参考线屏幕线宽：低缩放档加粗到 1.5px，其余 1px。
 * 非法 zoom 一律按 1px（宁可细，不要写进 NaN 让整根线消失）。
 */
export function alignGuideStrokePx(zoom: number): number {
  if (!Number.isFinite(zoom) || zoom <= 0) return 1;
  return zoom < ALIGN_GUIDE_THICK_ZOOM ? 1.5 : 1;
}

/**
 * 线宽 1.5px 时，像素栅格也必须跟着从 1px 降到 0.5px。
 *
 * 否则半像素宽的线永远压在整数列上，仍有 0.5px 溢到隔壁列、被背景冲淡一半 ——
 * 正是第 27 轮修掉的"虚线有深有浅"。降到 0.5px 栅格后，DPR=2 的屏幕上
 * 1.5 CSS px = 整整 3 个物理像素，颜色又回到纯色。
 */
export function alignGuideSnapQuantum(zoom: number): number {
  return alignGuideStrokePx(zoom) > 1 ? 0.5 : 1;
}

/**
 * 参考线真正要用的吸附栅格：**一个物理像素**（单位 CSS px）。
 *
 * 第 29 轮修的根因（用户原话「虚线有时候深 有时候浅，就不能跟即梦一样一直保持一样的深色吗」）：
 * 前两轮一直按「CSS 整数像素」对齐，这在 DPR=2 的屏上成立，但 Windows 常见的
 * **125% / 150% 缩放下 DPR 是小数**（1.25 / 1.5）：
 *   整数 CSS 位置 × 1.25 = 设备坐标 0 / 1.25 / 2.5 / 3.75 …… 只有 1/4 落在整数上。
 * 一个 1 CSS px 的窄矩形压在 1.25 上时，浏览器把它劈给相邻两行设备像素：
 *   0.75 + 0.5 或 1.0 + 0.25 —— **总墨量一样，但峰值亮度差一倍**，
 * 肉眼就是「有的线深、有的线浅」。截图实测三条平行线：
 *   深/浅 = 217/53、143/143、207/88，三行总墨量 445 / 556 / 455 却几乎相等 —— 这就是相位差的指纹。
 * 所以栅格必须是 1/DPR，线宽也必须是「整数个设备像素」，两条一起才恒为纯色。
 */
export function alignGuideDeviceQuantum(dpr: number): number {
  const d = Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
  return 1 / d;
}

/**
 * 参考线的屏幕线宽（CSS px），但取整到**整数个物理像素**。
 *
 * 只有整数个设备像素宽、又压在设备像素边界上，浏览器才不会做覆盖率混合 ——
 * 颜色回到纯色（--v-accent 原值），同屏所有线、所有缩放都是一个深度。
 * 例：1px @ DPR1.25 → 1 个物理像素（0.8 CSS px）；1.5px @ DPR2 → 3 个物理像素（1.5 CSS px）。
 */
export function alignGuideStrokeDevicePx(zoom: number, dpr: number): number {
  const d = Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
  const dev = Math.max(1, Math.round(alignGuideStrokePx(zoom) * d));
  return dev / d;
}

/**
 * 虚线节奏（屏幕 CSS px），同样取整到整数个物理像素。
 * 4px 实 4px 空在 DPR1.25 上是 5 个设备像素，边界正好落格；
 * 但 DPR 1.1 / 1.75 这类值会让每段虚线边缘做覆盖率混合 ——
 * 一段深一段浅，整条线看着像"渐隐"。取整后恒为纯色段。
 */
export function alignGuideDashDevicePx(dpr: number): number {
  const d = Number.isFinite(dpr) && dpr > 0 ? dpr : 1;
  return Math.max(1, Math.round(4 * d)) / d;
}

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
};

function axisGap(a0: number, a1: number, b0: number, b1: number): number {
  if (a1 < b0) return b0 - a1;
  if (b1 < a0) return a0 - b1;
  return 0;
}

function finiteBox(b: AlignBox): boolean {
  return Number.isFinite(b.x) && Number.isFinite(b.y)
    && Number.isFinite(b.w) && Number.isFinite(b.h)
    && b.w > 0 && b.h > 0;
}

/**
 * 参考线区间：**两张卡片之间那一段**（对标即梦）。
 *
 * 这是和旧实现最本质的差别。旧实现取「被拖盒子 ∪ 目标」的并集再两端各外扩 24px，
 * 于是拖一张卡会把一条线拉过半个画布、穿过一堆无关卡片；即梦的线是
 * **只连接两个相关元素**的短线：
 *   · 两个盒在该轴上不重叠 → 只画中间那段空隙（`A 的尾巴 → B 的头`）；
 *   · 有重叠（并排的两张卡对左边缘）→ 退化成重叠区，太短就用并集兜住。
 * 这样线的长度天然携带信息："这条线是这两张卡之间的事"。
 */
function segmentOf(
  axis: 'v' | 'h',
  a: AlignBox,
  b: AlignBox,
): {from: number; to: number} {
  const a0 = axis === 'v' ? a.y : a.x;
  const a1 = axis === 'v' ? a.y + a.h : a.x + a.w;
  const b0 = axis === 'v' ? b.y : b.x;
  const b1 = axis === 'v' ? b.y + b.h : b.x + b.w;
  const lo = Math.min(a0, a1, b0, b1);
  const hi = Math.max(a0, a1, b0, b1);
  if (a1 <= b0) return {from: a1, to: b0};
  if (b1 <= a0) return {from: b1, to: a0};
  const overlapFrom = Math.max(a0, b0);
  const overlapTo = Math.min(a1, b1);
  // 重叠区太短（并排卡片只重叠几像素）时用并集，保证线看得见
  if (overlapTo - overlapFrom >= 12) return {from: overlapFrom, to: overlapTo};
  return {from: lo, to: hi};
}

/**
 * 把画布坐标吸附到最近的**整数屏幕像素**，返回吸附后的画布坐标。
 *
 * 背景（第 27 轮，用户反馈"虚线颜色有深有浅"）：参考线是 1 屏幕像素宽的窄矩形
 * （width = 1/zoom，被画布 transform 还原成 1px）。只要它压在**小数**像素边界上，
 * 浏览器就把这一列拆给相邻两列、各占约 50% 覆盖 —— 颜色被背景冲淡一半。
 * 于是同一颜色、同一宽度的线，落在整数边界的看着是深蓝，落在 x.5 的变成浅青。
 * 截图实测：深线峰值蓝通道 171，浅线被摊成 72/98 两列，肉眼一眼看出"有深有浅"。
 *
 * 吸附后偏差 ≤0.5 屏幕像素（肉眼无感），但线从此恒为整一格纯色，任何缩放都一致。
 *
 * @param world 画布坐标
 * @param zoom  画布缩放（1 = 100%）
 * @param pan   该轴的平移量（viewport.x / viewport.y）
 * @param quantum 吸附栅格，单位是**屏幕** CSS px。默认 1（整数像素）。
 *   线宽为 1.5px 时必须传 0.5：整像素栅格上放不下 1.5px 的线，仍会溢到隔壁列。
 */
export function snapToPixel(world: number, zoom: number, pan: number, quantum = 1): number {
  if (!Number.isFinite(world) || !Number.isFinite(pan)) return world;
  if (!Number.isFinite(zoom) || zoom === 0) return world;
  if (!Number.isFinite(quantum) || quantum <= 0) return (Math.round(world * zoom + pan) - pan) / zoom;
  const screen = world * zoom + pan;
  return (Math.round(screen / quantum) * quantum - pan) / zoom;
}

/** 一条待绘制的参考线（已经吸附到设备像素栅格之后）。 */
export type AlignGuideItem = {
  axis: 'v' | 'h';
  /** 屏幕坐标（画布单位），已对齐到设备像素 */
  pos: number;
  /** 线段在另一轴上的起止（画布单位） */
  from: number;
  to: number;
  peerId?: string;
};

/**
 * 把**同一轴上挨在一起**的参考线合并成一条（第 29 轮补丁）。
 *
 * 根因（用户第三次反馈「辅助虚线还是会出现明显深浅」时的截图实测）：
 * 两条线各自都只有 1 个物理像素宽，但如果它们的目标边只差 ~1 个像素，
 * 吸附之后会落到**相邻两列**——画出来就是一条 2 像素的粗线。
 * 更糟的是这两条线是两个独立元素，虚线相位不同（实测一列亮了 157 行、另一列 118 行），
 * 两套虚线互相穿插，看起来就是「一根深一根浅、还断断续续」。
 *
 * 处理：同轴、间距 ≤ 1 个设备像素（quantum）的线合并成一条，区间取并集，
 * 位置保留先出现的那条（它已经在栅格上，取平均反而会重新引入半像素）。
 * 超过 1 个设备像素的不合并 —— 那是两条真的不同的边，用户需要看到它们分开。
 */
export function mergeAdjacentAlignGuides(
  items: readonly AlignGuideItem[],
  quantum: number,
): AlignGuideItem[] {
  const tol = Number.isFinite(quantum) && quantum > 0 ? quantum : 1;
  const out: AlignGuideItem[] = [];
  for (const it of items) {
    const near = out.find(
      (m) => m.axis === it.axis && Math.abs(m.pos - it.pos) <= tol + 1e-6,
    );
    if (!near) {
      out.push({...it});
      continue;
    }
    near.from = Math.min(near.from, it.from);
    near.to = Math.max(near.to, it.to);
    if (!near.peerId) near.peerId = it.peerId;
  }
  return out;
}

/**
 * 收集**所有**已经对齐的锚点对，生成虚线参考段（对标即梦）。
 *
 * 即梦一次会画出多组线：两张等高的卡片并排时，上边缘、中线、下边缘三条同时出现。
 * 旧实现每根轴只取一条"最佳"，信息量太少 —— 用户看不到"到底哪几条边对上了"。
 * 这里按「修正后位置」判定，只有真的对齐（|差| ≤ eps）才画，最多各轴 4 条。
 */
export function alignGuideSegments(
  moving: AlignBox,
  others: readonly AlignBox[],
  eps: number = ALIGN_DONE_EPS,
): AlignGuide[] {
  if (!finiteBox(moving)) return [];
  const out: AlignGuide[] = [];
  for (const axis of ['v', 'h'] as const) {
    const mAnchors = axis === 'v'
      ? [moving.x, moving.x + moving.w / 2, moving.x + moving.w]
      : [moving.y, moving.y + moving.h / 2, moving.y + moving.h];
    // ★ 同一条边经常同时命中好几张卡（用户项目里 x=0 上有 5 张、x=640 上有 3 张）。
    //   旧实现每命中一次就 push 一条，这些线在屏幕上**完全重合**、互相覆盖，
    //   还把这根轴 4 条的名额全部吃光 —— 真正不同的边一条都画不出来，
    //   表现就是用户说的"对齐时少了一根虚线"。
    //   现在按位置聚合：同一条边只画一根，区间取并集（把所有命中的卡都覆盖到），
    //   名额留给真正不同的边。位置量化到 0.5 画布像素，容忍浮点尾差。
    const groups = new Map<number, {pos: number; from: number; to: number; gap: number; peerId: string}>();
    for (const o of others) {
      if (!finiteBox(o) || o.id === moving.id) continue;
      const oAnchors = axis === 'v'
        ? [o.x, o.x + o.w / 2, o.x + o.w]
        : [o.y, o.y + o.h / 2, o.y + o.h];
      const gap = axis === 'v'
        ? axisGap(moving.y, moving.y + moving.h, o.y, o.y + o.h)
        : axisGap(moving.x, moving.x + moving.w, o.x, o.x + o.w);
      for (const ma of mAnchors) {
        for (const oa of oAnchors) {
          if (Math.abs(oa - ma) > eps) continue;
          const key = Math.round(oa * 2) / 2;
          const {from, to} = segmentOf(axis, moving, o);
          const hit = groups.get(key);
          if (hit) {
            hit.from = Math.min(hit.from, from);
            hit.to = Math.max(hit.to, to);
            // 描边只描**最近**的那张：一整列五张卡同时亮边会闪成一片
            if (gap < hit.gap) {
              hit.gap = gap;
              hit.peerId = o.id;
            }
          } else {
            groups.set(key, {pos: oa, from, to, gap, peerId: o.id});
          }
        }
      }
    }
    // 卡片附近的先画：超过 4 条时被挤掉的是远处的
    const list = [...groups.values()].sort((a, b) => a.gap - b.gap).slice(0, 4);
    for (const it of list) out.push({axis, pos: it.pos, from: it.from, to: it.to, peerId: it.peerId});
  }
  return out;
}

/**
 * 候选排序（见文件头 ④）：**先"齐"，再"近"**。
 *
 * 1) `|delta|` 更小的一方赢 —— 这是"对齐精度"，用户要的就是它；
 * 2) 两者精度相差不到 `ALIGN_DELTA_EPS` 时，才用 `lateralGap`（垂直于参考线方向的空隙）
 *    挑更近的那个，避免在等价对齐里拉一条横跨半个画布的长线。
 *
 * 早先这里按 `hypot(|delta|, lateralGap)` 排序，垂直空隙直接参与打分，
 * 结果是"正上方差 3px"（≈95 分）永远输给"同排差 20px"（20 分），
 * 用户往上一挪就被旁边的卡吸走 —— 就是"很难跟上面对齐"的根因。
 */
const ALIGN_DELTA_EPS = 0.75;

function findBestCandidate(
  moving: AlignBox,
  others: AlignBox[],
  axis: 'v' | 'h',
  threshold: number,
): AlignCandidate | null {
  let best: (AlignCandidate & {lateralGap: number}) | null = null;
  const movingAnchors = axis === 'v'
    ? [moving.x, moving.x + moving.w / 2, moving.x + moving.w]
    : [moving.y, moving.y + moving.h / 2, moving.y + moving.h];
  for (const o of others) {
    // ★ 尺寸未知的节点必须排除：RF 的 measured 在节点还没渲染/被虚拟化时是 undefined，
    //   sizeOf 兜底会得到 undefined → `o.x + undefined = NaN`。而 NaN 的比较恒为 false
    //   （`NaN > threshold` 也是 false），于是它会一路穿过所有守卫成为"最佳候选"，
    //   把参考线位置和吸附量一起污染成 NaN —— 表现就是线卡住不动、卡片跟手时突然僵住。
    if (!finiteBox(o)) continue;
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
        const bestAbs = best ? Math.abs(best.delta) : Number.POSITIVE_INFINITY;
        const better = !best
          // 明显对得更齐 → 直接换（哪怕它离得远）
          || absDelta < bestAbs - ALIGN_DELTA_EPS
          // 精度打平 → 挑空间上更近的那个
          || (Math.abs(absDelta - bestAbs) <= ALIGN_DELTA_EPS && lateralGap < best.lateralGap);
        if (!better) continue;
        best = {
          delta,
          target: oAnchors[oi],
          anchorIdx: ai as 0 | 1 | 2,
          otherId: o.id,
          lateralGap,
        };
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

  // 参考线段按「修正后的真实位置」重算：一次把所有已对齐的锚点对都画出来
  // （上/中/下同时对齐时就是三条线，和即梦一致）。
  const eff: AlignBox = {
    id: raw.id,
    x: raw.x + dx,
    y: raw.y + dy,
    w: raw.w,
    h: raw.h,
  };
  const guides = alignGuideSegments(eff, others, ALIGN_DONE_EPS);

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
  if (others.length === 0 || !finiteBox(moving)) return {dx: 0, dy: 0, guides: []};
  const bestX = findBestCandidate(moving, others, 'v', threshold);
  const bestY = findBestCandidate(moving, others, 'h', threshold);
  const dx = bestX ? bestX.delta : 0;
  const dy = bestY ? bestY.delta : 0;
  const eff: AlignBox = {id: moving.id, x: moving.x + dx, y: moving.y + dy, w: moving.w, h: moving.h};
  return {dx, dy, guides: alignGuideSegments(eff, others, ALIGN_DONE_EPS)};
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

/**
 * A loaded canvas is always an editable episode canvas. URL/local memory are
 * only hints; the server workspace is authoritative. Keep the first valid hint
 * so stale cross-project episode ids do not leak into write requests.
 */
export function resolveLoadedEpisodeId(input: {
  activeEpisodeId?: string | null;
  episodes?: Array<{ep_id?: string | null}> | null;
  preferredEpisodeId?: string | null;
  fallbackEpisodeId?: string | null;
}): string {
  const available = new Set(
    (input.episodes ?? [])
      .map((episode) => String(episode?.ep_id || '').trim().toUpperCase())
      .filter(Boolean),
  );
  const candidates = [
    input.activeEpisodeId,
    input.preferredEpisodeId,
    input.fallbackEpisodeId,
  ];
  for (const candidate of candidates) {
    const normalized = String(candidate || '').trim().toUpperCase();
    if (normalized && (available.size === 0 || available.has(normalized))) {
      return normalized;
    }
  }
  return [...available].sort(epIdCompare)[0] || 'EP001';
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

export function canvasIdlePresentation(input: {
  dataReady: boolean;
  mediaCount: number;
  noteCount: number;
}): {
  showEmptyWorkspace: boolean;
} {
  return {
    showEmptyWorkspace: (
      input.dataReady
      && input.mediaCount === 0
      && input.noteCount === 0
    ),
  };
}

export function layoutPositionsForExport(input: {
  persisted?: Record<string, {x: number; y: number}> | null;
  mediaNodes?: Array<{id: string; x: number; y: number}>;
  notes?: Array<{id: string; x: number; y: number}>;
}): Record<string, {x: number; y: number}> {
  const positions: Record<string, {x: number; y: number}> = {
    ...(input.persisted || {}),
  };
  for (const node of input.mediaNodes || []) {
    positions[`media-${node.id}`] = {x: node.x, y: node.y};
  }
  for (const note of input.notes || []) {
    positions[`note-${note.id}`] = {x: note.x, y: note.y};
  }
  return positions;
}

export function initialViewportForScope<Scope extends {scope: string}>(
  scopes: Scope[],
  activeScope: string,
): Scope | null {
  const scoped = scopes.find((item) => item.scope === activeScope);
  return scoped ?? scopes.find((item) => item.scope === '__project__') ?? null;
}

export function viewportIndexForSave<Entry extends {scope: string}>(
  entries: Entry[] | null | undefined,
  scope: string,
  entry: Entry,
): Entry[] {
  return [
    ...(Array.isArray(entries)
      ? entries.filter((item) => item && item.scope !== scope)
      : []),
    entry,
  ];
}

export function canvasViewportIndexKey(projectId: string): string {
  return `vimax.canvas.viewports.${projectId}`;
}

export function viewportFromPayload(
  payload: {episodeViewports?: Record<string, unknown>} | null | undefined,
  projectId: string,
  episodeId?: string | null,
): unknown {
  if (!payload || typeof payload.episodeViewports !== 'object') return null;
  const normalizedEpisode = String(episodeId || '').trim().toUpperCase();
  if (normalizedEpisode) {
    return payload.episodeViewports?.[normalizedEpisode] ?? null;
  }
  const legacy = payload.episodeViewports?.[projectId];
  return legacy && typeof legacy === 'object' ? legacy : null;
}

export function applyLayoutPositions<
  Media extends {id: string; x: number; y: number},
  Note extends {id: string; x: number; y: number},
>(input: {
  positions: Record<string, {x: number; y: number}>;
  mediaNodes: Media[];
  notes: Note[];
}): {
  mediaNodes: Media[];
  notes: Note[];
  changedMedia: Media[];
  changedNotes: Note[];
} {
  const changedMedia: Media[] = [];
  const changedNotes: Note[] = [];
  const mediaNodes = input.mediaNodes.map((node) => {
    const position = input.positions[`media-${node.id}`];
    if (!position || (node.x === position.x && node.y === position.y)) return node;
    const next = {...node, x: position.x, y: position.y};
    changedMedia.push(next);
    return next;
  });
  const notes = input.notes.map((note) => {
    const position = input.positions[`note-${note.id}`];
    if (!position || (note.x === position.x && note.y === position.y)) return note;
    const next = {...note, x: position.x, y: position.y};
    changedNotes.push(next);
    return next;
  });
  return {mediaNodes, notes, changedMedia, changedNotes};
}
