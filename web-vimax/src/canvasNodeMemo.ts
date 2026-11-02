import {voiceRefList, type CanvasMediaNode} from './api';

/**
 * 画布媒体卡片的渲染相等判定（从 CanvasView.tsx 抽出，便于单测）。
 *
 * 背景：React Flow 每次 `flowNodes` 重算都会给所有节点造新的 `data` 对象，
 * `memo` 的默认浅比较永远不等，于是「一个节点生成完成 → 全画布所有卡片一起
 * 重渲染」。400 张卡时，生成期间每 1.5 秒一次全树重渲染，是画布卡顿的主因。
 *
 * 判定只比较真正会改变本卡画面的字段：
 *
 * - `node`：本卡自身数据（媒体地址 / 状态 / 提示词 / 版本），引用变即重渲染；
 * - `active` / `generationBusy` / `renderMode` / `contentVisible` / `catalog`；
 * - `groupMembers`：按内容比较（每次 groupMap 重算都会给新数组，比引用必然不等）；
 * - 依赖签名：本卡 `refs` / `voiceRefs` 指向的节点「状态 + 类型 + 是否有产物 +
 *   标题 + 位置」，用于卡片上的依赖徽章（未就绪 / 待更新）和点徽标定位。只遍历
 *   本卡自己的引用，成本 O(refs)。
 *
 * 刻意**不**比较回调（onChange / onGenerate / ...）：它们都是 setState 包装，
 * 不捕获会过期的值；也不比较 `assetsById` 本身——它是每次 mediaNodes 变化都会
 * 重建的 Map，比引用必然不等，真正关心的是它里面本卡引用到的那几个节点。
 */

export type MediaNodeMemoFacets = {
  node: CanvasMediaNode;
  assetsById?: Map<string, CanvasMediaNode>;
  active?: boolean;
  generationBusy?: boolean;
  renderMode?: string;
  contentVisible?: boolean;
  catalog?: unknown;
  /** 画布层传入的"抑制节点打开"回调，稳定引用；只做相同性比较 */
  suppressNodeOpen?: unknown;
  groupMembers?: string[] | null;
};

/** 本卡引用到的节点状态摘要。引用为空时返回 ''。 */
export function mediaNodeDependencySignature(
  node: CanvasMediaNode,
  assetsById?: Map<string, CanvasMediaNode>,
): string {
  const params = node.params || {};
  const ids: string[] = [];
  const push = (value: unknown) => {
    const id = typeof value === 'string' ? value : '';
    if (id && !ids.includes(id)) ids.push(id);
  };
  (params.refs || []).forEach((item) => push(item.nodeId));
  voiceRefList(params).forEach((item) => push(item.nodeId));
  if (ids.length === 0) return '';

  let signature = '';
  for (const id of ids) {
    const source = assetsById?.get(id);
    signature += source ? `${id}${sourceSlug(source)};` : `${id}:missing;`;
  }
  return signature;
}

/**
 * 依赖节点上「会改变本卡画面」的全部字段。
 *
 * 卡片用到引用节点的四处信息，任何一处漏进签名都会变成"永远停在旧值"：
 * 1. `mediaOutputReady()` → 依赖徽标是「就绪」还是「缺 N 项」，含 kind /
 *    promptStale / mediaAvailability / mediaUrl，文本节点看 prompt 是否为空；
 * 2. `title` → 徽标 tooltip 里"点击定位缺失依赖：<标题>"；
 * 3. `x` / `y` → 点徽标要 `setCenter` 跳过去，位置不进签名就会跳到旧坐标；
 * 4. `kind` → 徽标的「N图 / N音色 / N文本」计数。
 *
 * 只取整坐标：亚像素位移不需要刷新卡片，拖动时的抖动就落在这里。
 */
function sourceSlug(source: CanvasMediaNode): string {
  const prompt = typeof source.prompt === 'string' ? source.prompt : '';
  const textReady = source.kind === 'text' ? (prompt.trim() ? '1' : '0') : '-';
  return [
    source.kind,
    source.status,
    source.promptStale ? 'stale' : 'fresh',
    source.mediaAvailability || '',
    source.mediaUrl ? 'has' : 'empty',
    textReady,
    source.title || '',
    Math.round(source.x || 0),
    Math.round(source.y || 0),
  ].join('/');
}

export function sameStringList(
  prev?: string[] | null,
  next?: string[] | null,
): boolean {
  if (prev === next) return true;
  if (!prev || !next || prev.length !== next.length) return false;
  for (let i = 0; i < prev.length; i += 1) {
    if (prev[i] !== next[i]) return false;
  }
  return true;
}

export function mediaNodeDataEqual(
  prev: MediaNodeMemoFacets,
  next: MediaNodeMemoFacets,
): boolean {
  if (prev === next) return true;
  if (prev.node !== next.node) return false;
  if (prev.active !== next.active) return false;
  if (prev.generationBusy !== next.generationBusy) return false;
  if (prev.renderMode !== next.renderMode) return false;
  if (prev.contentVisible !== next.contentVisible) return false;
  if (prev.catalog !== next.catalog) return false;
  if (prev.suppressNodeOpen !== next.suppressNodeOpen) return false;
  if (!sameStringList(prev.groupMembers, next.groupMembers)) return false;
  return mediaNodeDependencySignature(prev.node, prev.assetsById)
    === mediaNodeDependencySignature(next.node, next.assetsById);
}
