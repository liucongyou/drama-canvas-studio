import {startTransition, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type ReactNode, type RefObject, type SyntheticEvent} from 'react';
import * as ReactDOM from 'react-dom';
import {createPortal} from 'react-dom';
import {toPng} from 'html-to-image';
import {
  BaseEdge,
  Background,
  EdgeLabelRenderer,
  Handle,
  MiniMap,
  NodeResizer,
  Position,
  ReactFlow,
  ReactFlowProvider,
  SelectionMode,
  ViewportPortal,
  getBezierPath,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useStore,
  useStoreApi,
  type Connection,
  type Edge,
  type EdgeProps,
  type Node as FlowNode,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
// 统一图标体系（全部 SVG 矢量，取代早前的 ▤/♪/▷/✕/↻/＋/－/▾ 等字符图标：
// 字符图标在不同系统字体下粗细/大小/基线都不一致，看起来像"打出来的符号"而不是 UI 图标）。
import {
  AlignHorizontalDistributeCenter,
  AlignHorizontalJustifyCenter,
  AlignHorizontalJustifyEnd,
  AlignHorizontalJustifyStart,
  AlignVerticalDistributeCenter,
  AlignVerticalJustifyCenter,
  AlignVerticalJustifyEnd,
  AlignVerticalJustifyStart,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpToLine,
  AtSign,
  Box,
  Brain,
  BringToFront,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Clapperboard,
  ClipboardList,
  Copy,
  Download,
  Eraser,
  Eye,
  EyeOff,
  Film,
  Group,
  Hand,
  History,
  LayoutGrid,
  LibraryBig,
  Link2,
  ListChecks,
  Magnet,
  Maximize2,
  Minimize2,
  Minus,
  MoreHorizontal,
  MousePointer2,
  Mountain,
  Music,
  Package,
  Pause,
  Pencil,
  Play,
  Plus,
  RotateCw,
  Rows3,
  Ruler,
  Repeat2,
  ScanSearch,
  Scissors,
  Search,
  SendToBack,
  SlidersHorizontal,
  Sparkles,
  Star,
  Spline,
  StickyNote,
  Trash2,
  Type,
  Ungroup,
  Upload,
  UserRound,
  Video,
  Volume2,
  VolumeX,
  Wallet,
  Wand2,
  Image as ImageGlyph,
  AudioLines,
  X,
} from 'lucide-react';
import {getCanvas, getCanvasNotes, getCanvasMediaNodes, projectEventsUrl, mediaSourceUrl, getCanvasWorkspace, getGenerationTasks, cancelGenerationTask, getModelCatalog, getQualityReport, markRefRemoved, clearRefRemoved, recomputeQualityReport, saveCanvasNotes, saveCanvasMediaNodes, saveCanvasMediaNodePositions, saveCanvasConnections, createCanvasEpisode, deleteCanvasEpisode, patchMediaNodeFields, patchMediaNodesFields, deleteCanvasMediaNode, saveCanvasShot, sendMessage, generateCanvasNodeNow, isLiveProviderGeneration, subscribeToEvents, uploadWorkspaceFile, uploadWorkspaceFileWithProgress, voiceRefList, withVoiceRefs, mediaVersionList, type Canvas, type CanvasAssetItem, type CanvasNote, type CanvasMediaNode, type CanvasMediaNodeKind, type CanvasMediaParams, type CanvasSegment, type CanvasShot, type GenerationTask, type ModelCatalog, type ModelSpec, type QualityReport} from './api';
import {generationCostAmount, generationCostHint, modelPricingNote, pricingCurrencySymbol} from './modelPricing';
import {
  createProjectVersion,
  getBudgetStatus,
  getProjectVersions,
  previewProjectVersion,
  restoreProjectVersion,
  saveBudgetConfig,
  type BudgetConfig,
  type BudgetStatus,
  type ProjectVersion,
  type ProjectVersionPreview,
} from './api';
import {useDialogFocusTrap} from './useDialogFocusTrap';
import {dialog} from './GDialog';
import {reconcileSucceededNode} from './generationReconcile';
import {taskOutcomePresentation} from './taskOutcomePresentation';
import {
  generationDurationLabel,
  generationPhaseLabel,
  generationProgressLabel,
  hasTrustedProviderProgress,
} from './generationPresentation';
import {
  inheritedGenerationParams,
  inheritModelParams,
  migrateUnavailableModelParams,
} from './modelParamInheritance';
import {validateModelParams} from './modelParamContract';
import {VideoParamsPanel} from './VideoParamsPanel';
import {
  evaluateBatchGeneration,
  evaluateReferenceLimits,
  modelMaxRefs,
  type BatchGenerationCandidate,
} from './batchGeneration';
import {absolutePositionOf, ALIGN_ENTER_PX, ALIGN_GLUE_PX, ALIGN_RELEASE_PX, ALIGN_VISIBLE_PX, computeAlignSnap, createAlignState, createUserEdgeIdFactory, fallbackSegmentsOf, pickDefaultEpisode, resolveInitialEpisode, segShotIdxsOf, solveAlignSnap, type AlignBox, type AlignGuide, type AlignState} from './canvasLayout';
import {
  CANVAS_VIEWPORT_POLICY_VERSION,
  filterMediaNodesInBounds,
  focusedNodeVisibleBounds,
  LARGE_CANVAS_OVERVIEW_CHUNK_SIZE,
  popupRevealShift,
  shouldUseLargeCanvasOverview,
  viewportForCanvasBounds,
  visibleBoundsForViewport,
  type CanvasViewport,
  type CanvasVisibleBounds,
} from './canvasViewport';

type Props = {
  session: string;
  actions?: ReactNode;
  onCreate?: () => void;
};

const COL_X = 330;
const dragPointer = (event: MouseEvent | TouchEvent): {x: number; y: number} => {
  if ('touches' in event && event.touches.length > 0) {
    return {x: event.touches[0].clientX, y: event.touches[0].clientY};
  }
  const mouse = event as MouseEvent;
  return {x: mouse.clientX, y: mouse.clientY};
};
const SHOT_ROW = 400;

type PreviewPlacement = {left: number; top?: number; bottom?: number};

function previewPlacement(
  rect: DOMRect,
  align: 'center' | 'right' = 'center',
  panelRect?: DOMRect,
): PreviewPlacement {
  const width = 250;
  const left = align === 'right'
    ? Math.min(window.innerWidth - width - 8, Math.max(8, rect.right + 10))
    : Math.min(window.innerWidth - width - 8, Math.max(8, rect.left + rect.width / 2 - width / 2));
  // Preview inside the prompt editor belongs above the whole editor panel, not
  // above the thumbnail row inside it. Anchoring to the panel top prevents the
  // preview from covering the editor content while still following the hovered
  // thumbnail horizontally.
  const topBoundary = panelRect?.top ?? rect.top;
  if (topBoundary >= 120) {
    return {left, bottom: window.innerHeight - topBoundary + 8};
  }
  return {left, top: Math.min(window.innerHeight - 220, rect.bottom + 10)};
}
// Scene 宫格卡：顶部横条(160px 竖版视频+信息) + 下方镜头格
const SEG_ROW = 360;

function newMediaNodeId(): string {
  const random = globalThis.crypto?.randomUUID?.().slice(0, 8)
    || Math.random().toString(36).slice(2, 10);
  return `m-${Date.now().toString(36)}-${random}`;
}

// 可删除连线组件：**单击选中**这条线时在连线中点显示剪刀（手动线/自动线通用）。
//
// 以前是 hovered || selected：鼠标划过就冒剪刀，批量框选节点时一屏全是剪刀图标。
// 用户要求与即梦一致 —— 只有单击选中才给删除入口，所以这里不再监听 hover。
function CanvasEdgeWithDelete(props: EdgeProps) {
  const {id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, selected, style, data, source, target} = props;
  const [edgePath, labelX, labelY] = getBezierPath({sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition});
  // ★★★ 第 20 轮：删除回调优先从**模块级注册表**取，其次才回退到 data.onDelete。
  //   原因见 CANVAS_EDGE_TYPES 上方那段注释：data.onDelete 是函数，JSON.stringify 写
  //   localStorage 时会被整块丢掉，刷新恢复出来的边 data 只剩 `{}`，
  //   旧写法 `if (onDelete) onDelete(id)` 就静默什么都不做 —— 用户看到的就是"剪刀点了没反应"。
  //   注册表活在内存里、每次挂载重新登记，不参与序列化，因此不会再丢。
  const dataOnDelete = (data as {onDelete?: (edgeId: string) => void} | undefined)?.onDelete;
  const onDelete = dataOnDelete || EDGE_DELETE_REG.current || undefined;
  // ★ 只要画布里还有节点处于选中态（框选/多选），或者同时选了多条线，就不给剪刀。
  //   根因：React Flow 的框选会把「与选中节点相连的所有边」一并置 selected
  //   （见 Pane 的 commitUserSelectionRect），于是框选 22 个节点时一屏 60+ 个剪刀。
  //   即梦的做法是：只有「单独点中一条线」才给删除入口。
  const soloEdgeSelected = useStore((s) => {
    if (!s.edgeLookup.get(id)?.selected) return false;
    if (s.nodes.some((n) => n.selected)) return false;
    let picked = 0;
    for (const e of s.edges) if (e.selected) { picked += 1; if (picked > 1) return false; }
    return picked === 1;
  });
  // ── 选中节点的相连线高亮（对标即梦）───────────────────────────────
  // 选中一张卡片时，所有连到它的线都点亮流光 —— 即梦就是这么做的（用户明确要求）。
  // 用 useStore 只订阅「两个端点的 selected」，而不是让父级重算 edges 数组：
  //   父级重算会让 React Flow 拿到全新 edge 对象 → 整张边图重建 → 拖动掉帧。
  // 返回布尔值，选择状态没命中本边时不会触发本组件重渲染。
  const linkedSelected = useStore((s) => {
    const sel = s.nodeLookup;
    return !!(sel.get(source)?.selected || sel.get(target)?.selected);
  });
  // 生成中高亮直接订阅端点媒体节点的运行状态，而不是依赖来回重建 edge.data。
  // 这样“重新生成 -> 连线立刻亮起”不依赖边数组是否碰巧重算；文本节点保持流式，
  // 不触发流星动画。
  const busyFromNodes = useStore((state) => {
    const sourceData = state.nodeLookup.get(source)?.data as {
      node?: CanvasMediaNode;
      generationBusy?: boolean;
    } | undefined;
    const targetData = state.nodeLookup.get(target)?.data as {
      node?: CanvasMediaNode;
      generationBusy?: boolean;
    } | undefined;
    return [sourceData, targetData].some((item) => {
      if (!item?.node || item.node.kind === 'text') return false;
      return item.generationBusy === true || item.node.status === 'generating';
    });
  });
  const busy = busyFromNodes || !!(data as {busy?: boolean} | undefined)?.busy;
  return (
    <>
      <g>
        <circle cx={sourceX} cy={sourceY} r={3} className="canvas-edge-source-dot" />
        {/* ★★ 第 20 轮：命中层宽度 26 → 14，并交给 CSS 加 `vector-effect: non-scaling-stroke`。
            ── 用户报的「有些连线剪不断」就是这里：
            BaseEdge 生成的 `.react-flow__edge-interaction` **没有 vector-effect**，
            所以 `interactionWidth` 是 **画布单位**而不是屏幕像素。
            全览 19% 时 26 个画布单位只剩 **4.9 屏幕像素**（实测 computedSW=26px × zoom 0.189），
            细到基本点不中；同时 26 又足够宽，从同一端口扇出/同一卡片汇聚的几条线
            会在中段互相覆盖 —— 被盖住的那条**整条路径上一个点都点不到**。
            实测（63 条边，沿路径每 6px 采样一次 elementFromPoint）：
              26 画布单位 → 6 条边 0 命中（真·点不中）
              26 屏幕像素 → 同样 6 条（宽 vs 宽的互相覆盖才是主因）
              18 屏幕像素 → 2 条
              14 屏幕像素 → **0 条**
            所以定 14：屏幕恒定、够点、又不抢邻线。宽度在 styles.css 里统一声明。 */}
        <BaseEdge id={id} path={edgePath} markerEnd={markerEnd} style={style} interactionWidth={14} />
        {/* ★★ 第 19 轮：**递减拖尾彗星**（三层，右端严格对齐）
            ① .canvas-edge-flow-tail —— 最长最暗的焰尾（54px，22%）
            ② .canvas-edge-flow      —— 中亮彗身（24px，55%）
            ③ .canvas-edge-flow-head —— 近白过曝彗头（5px，100%）
            三层右端重合、左端依次缩短 ⇒ 叠出"从白到透明"的拖尾。
            ★ 顺序不能反：DOM 顺序就是图层顺序，头必须最后画（盖在最上面）。
            vector-effect 必须给：non-scaling-stroke 让线宽/虚线间距恒为屏幕像素，
            否则缩到 40% 时 2.2px 只剩 0.9px，屏幕上一片模糊。 */}
        <path d={edgePath} className={`canvas-edge-flow-tail ${busy ? 'is-busy' : ''} ${linkedSelected ? 'is-linked' : ''}`} vectorEffect="non-scaling-stroke" />
        <path d={edgePath} className={`canvas-edge-flow ${busy ? 'is-busy' : ''} ${linkedSelected ? 'is-linked' : ''}`} vectorEffect="non-scaling-stroke" />
        <path d={edgePath} className={`canvas-edge-flow-head ${busy ? 'is-busy' : ''} ${linkedSelected ? 'is-linked' : ''}`} vectorEffect="non-scaling-stroke" />
        {/* 终点火花：贴着 target 端点的一颗呼吸光点，强化"流到这里了"的方向感。
            用 circle 而非 path —— 位置就是 targetX/targetY，不需要跟着 dash 跑。 */}
        <circle
          cx={targetX} cy={targetY} r={2.6}
          className={`canvas-edge-spark ${busy ? 'is-busy' : ''} ${linkedSelected ? 'is-linked' : ''}`}
        />
      </g>
      {/* 剪刀只在「单独单击选中这一条线」时出现（批量选中节点 / 多选连线一律不出）。 */}
      {soloEdgeSelected && (
        <EdgeLabelRenderer>
          <button
            type="button"
            className="canvas-edge-delete"
            style={{transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px) scale(var(--edge-ui-scale, 1))`}}
            onClick={(event) => {
              event.stopPropagation();
              // 走到这里说明剪刀已经渲染且被点了，绝不能再静默 return：
              // 早期版本 `if (onDelete) onDelete(id)` 在 onDelete 丢失时既不删也不提示，
              // 用户唯一的感受就是"点了没反应"（本轮用户原话）。
              const fn = onDelete || EDGE_DELETE_REG.current;
              if (fn) fn(id);
            }}
            title="剪断此连线（也可点选后按 Del）"
          ><Scissors size={12} strokeWidth={2.1} aria-hidden="true" /></button>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
const CANVAS_EDGE_TYPES = {deletable: CanvasEdgeWithDelete};
// ★★★ 第 20 轮（用户原话「不是盖住的问题，是功能问题，我放大看到剪刀按钮就是点了没反应」）：
//
// 剪不断连线的**真正根因**在这里 —— 不是命中带宽度、不是遮挡，是 onDelete 丢了。
//
//   1) 连线对象把删除回调塞在 `edge.data.onDelete`（一个函数）；
//   2) persistLayout 用 `JSON.stringify({userEdges: edges, ...})` 写 localStorage，
//      而 **JSON 序列化会直接丢弃函数值**（实测：`{"data":{"onDelete":fn}}` → `{"data":{}}`）；
//   3) 刷新后 loadPersistedLayout 把这份 JSON 原样 setUserEdges 恢复回来，
//      于是这些边拿到的是 `data = {}`；
//   4) CanvasEdgeWithDelete 的点击是 `if (onDelete) onDelete(id)` ——
//      onDelete 是 undefined 时**不报错、不提示、什么都不做**，
//      剪刀按钮照常渲染、点下去毫无反应。
//
// 为什么症状偏偏集中在「视频节点 → 视频节点」：
//   flowEdges 只为 image/audio → video/audio 生成自动线（video→video 被 `ok` 判定拦掉），
//   所以视频到视频的线**只能靠手动拖端口**连出来，也就是 e-user- 手动边；
//   而自动线每次渲染都由 flowEdges 重新生成、onDelete 是新的，永远正常。
//   ⇒ 只有手动连出来的线会中招，而且必须**刷新过一次**才中招（刷新前 data 还在内存里）。
//   这完全对应用户说的「有些连线剪不断」「放大看剪刀点了没反应」。
//
// 修法：删除能力不再依赖可序列化的数据，改成模块级注册表。
//   CanvasView 挂载时把 deleteEdgeById 注册进来，边组件在**点击那一刻**现取。
//   好处：不经过 JSON、不随布局持久化丢失、也不怕快照/导入导出把 data 洗空。
const EDGE_DELETE_REG: {current: ((edgeId: string) => void) | null} = {current: null};
/** 给恢复/导入回来的边补上运行期字段（type/className 可能被旧数据写丢，data 里的函数更是必然丢） */
function hydrateUserEdge(edge: Edge): Edge {
  const raw = edge as Edge & {className?: string};
  return {
    ...raw,
    type: 'deletable',
    className: raw.className || 'canvas-edge-user',
    // 这里放的是**稳定引用**的转发函数：即使这份对象之后又被 JSON.stringify 洗掉，
    // 下次 hydrate 会重新补上，真正干活的是模块级注册表。
    data: {...(raw.data as Record<string, unknown> | undefined), onDelete: (id: string) => EDGE_DELETE_REG.current?.(id)},
  };
}
function userEdgeFromConnection(connection: {
  id?: string;
  fromNodeId: string;
  toNodeId: string;
}): Edge {
  return hydrateUserEdge({
    id: connection.id || nextUserEdgeId(),
    source: `media-${connection.fromNodeId}`,
    target: `media-${connection.toNodeId}`,
    type: 'deletable',
    className: 'canvas-edge-user',
  } as Edge);
}
// 用户边 id 生成器：自增序列（模块级，跨组件实例保持唯一），替代 Date.now()+length 防同毫秒碰撞
const nextUserEdgeId = createUserEdgeIdFactory('e-user');
const ASSET_ROW = 150;
// 等距分布：放不下时回退用的固定间距（画布坐标）。60 在 190~300 宽卡片上是"看起来分得开、
// 又不至于把整排拉到屏幕外"的折中值。
const DIST_GAP = 60;
// 流光参数随缩放注入（见 .canvas-edge-flow 的注释）：
//   线宽恒为屏幕像素（non-scaling-stroke），但 dash 的"屏幕像素密度"必须随 zoom 调整，
//   否则放大后亮段相对路径变稀 → 用户感觉"流光没了"。
// zoom 越大，亮段越长、空隙越短；同时提高不透明度，让放大后仍然明显。
function edgeFlowVars(zoom: number): CSSProperties {
  const z = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  // 线宽：屏幕像素（non-scaling-stroke 保证不被画布 transform 放大）
  const width = Math.round(Math.min(2.6, 1.4 + (z - 0.4) * 0.55) * 10) / 10;
  const op = Math.round(Math.min(0.62, Math.max(0.3, 0.3 + (z - 0.4) * 0.22)) * 100) / 100;

  // ★★★ 第 19 轮定稿：**递减拖尾彗星**
  //
  //   ★★★★ 本轮最贵的一课：`stroke-dasharray` 没有"空隙在前"这种写法。
  //     SVG 的 dasharray 永远是 **从实线段开始**、实线/空隙交替。
  //     我一度按 `gap len` 去理解（以为 `621 73.7` 是"先空 621 再亮 73.7"），
  //     实际渲染是"亮 621 + 空 73.7" ⇒ 占空比 95% ⇒ 视觉上就是一条实线。
  //     这个错误骗过了所有 CSS 断言（dasharray 值确实是我写的那样），
  //     只有**沿路径逐点采样真实像素亮度**才抓出来：
  //       彗星占空比 95.0%、只有 1 段连续亮段 624px、亮度恒为 253
  //     诊断脚本见 .screens/diag_sample_along_path.py。
  //
  //   ★ 正确做法：每层都写标准的 `dash gap`，
  //     再用 **stroke-dashoffset 初值**把各层的"段尾"对齐到同一个位置。
  //     dashoffset 为负 = 图案整体向前平移，所以：
  //       tail  len 36   phase 0                    → 段 [0,36]
  //       body  len 14   phase -(36-14) = -22       → 段 [22,36]  ✓ 右端对齐
  //       head  len 4.5  phase -(36-4.5) = -31.5    → 段 [31.5,36] ✓ 右端对齐
  //     三层右端重合、左端依次缩短 ⇒ 叠出「白→蓝→透明」的连续拖尾。
  //
  //   ★ 为什么右端是"前端"：dashoffset 动画向负方向推进 = 图案朝路径正向
  //     （source→target）移动，所以段尾站在运动方向前面。头必须在段尾，
  //     否则整条光看起来在**倒着飞**。
  //
  //   ★★★ dasharray 不吃 non-scaling-stroke（实测确认）：
  //     `vector-effect: non-scaling-stroke` 只让 stroke-width 按屏幕像素算，
  //     dasharray 始终按**画布单位**解析。所以所有长度必须除以 zoom，
  //     否则全览 19% 时屏幕周期只有 25px（密成刻度尺）、放大 200% 时 264px（稀成断线）。
  //     实测修复后三档缩放的屏幕周期稳定在 131.3 / 131.7 / 131.3px。
  const cycleScreen = 132;
  const tailScreen = 36;
  const bodyScreen = 14;
  const headScreen = 4.5;
  // 除以 zoom → 画布单位
  const toCanvas = (screenPx: number) => Math.round((screenPx / z) * 10) / 10;
  const cycle = toCanvas(cycleScreen);
  const tailLen = toCanvas(tailScreen);
  const bodyLen = toCanvas(bodyScreen);
  const headLen = toCanvas(headScreen);
  // 相位（负值 = 图案前移），单位为画布单位
  const bodyPhase = -Math.round((tailLen - bodyLen) * 10) / 10;
  const headPhase = -Math.round((tailLen - headLen) * 10) / 10;

  return {
    '--edge-flow-cycle': `${cycle}px`,
    // ★ 标准 dash gap：实线段在前、空隙在后
    '--edge-flow-tail': `${tailLen} ${Math.round((cycle - tailLen) * 10) / 10}`,
    '--edge-flow-body': `${bodyLen} ${Math.round((cycle - bodyLen) * 10) / 10}`,
    '--edge-flow-head': `${headLen} ${Math.round((cycle - headLen) * 10) / 10}`,
    '--edge-flow-tail-phase': '0px',
    '--edge-flow-body-phase': `${bodyPhase}px`,
    '--edge-flow-head-phase': `${headPhase}px`,
    '--edge-flow-width': String(width),
    '--edge-flow': String(op),
  } as CSSProperties;
}

type MouseMode = 'select' | 'hand' | 'connect';

type SearchHit = {
  kind: '角色' | '场景' | '道具' | '音色' | '视频' | '资产图' | '段' | '镜头' | '备注' | '文本' | '图片' | '音频' | '节点';
  label: string;
  sub?: string;
  nodeId: string;
};

type HistorySnapshot = {
  edges: Edge[];
  notes: CanvasNote[];
  deletedAuto: string[];
  media: CanvasMediaNode[];
  /** Flow-layer snapshot so note/child-card coordinates can also be undone. */
  flowNodes?: FlowNode[];
  /** Position-only undo must restore coordinates instead of preserving current ones. */
  positions?: boolean;
  /** Node-creation undo removes nodes that did not exist in the pre-action snapshot. */
  removeAddedMedia?: boolean;
};

type MetadataHistoryEntry = {
  nodeId: string;
  before: CanvasMediaNode;
  after: CanvasMediaNode;
  keys: string;
  at: number;
};

type ShotDraft = {
  visual_desc: string;
  motion_desc: string;
  audio_desc: string;
  duration: string;
  cam_idx: string;
  transition_type: string;
  ff_desc: string;
  lf_desc: string;
  action_breakdown: string;
  video_prompt: string;
};

function emptyDraft(shot: CanvasShot): ShotDraft {
  return {
    visual_desc: shot.visual_desc ?? '',
    motion_desc: shot.motion_desc ?? '',
    audio_desc: shot.audio_desc ?? '',
    video_prompt: shot.video_prompt ?? '',
    duration: shot.duration !== null ? String(shot.duration) : '',
    cam_idx: shot.cam_idx !== null ? String(shot.cam_idx) : '',
    transition_type: shot.transition_type ?? '',
    ff_desc: shot.ff_desc ?? '',
    lf_desc: shot.lf_desc ?? '',
    action_breakdown: shot.action_breakdown ?? '',
  };
}

// 视频真实方向：按 naturalHeight/naturalWidth（比值 r=h/w）判断横屏/竖屏/方图。
// 画布缩略图与播放框跟着真实方向走——横屏视频给横框、竖屏视频给竖框，
// 不再一律按 9:16 竖屏硬裁。ratio 未知（metadata 未到）时按横屏兜底。
type VideoOrient = 'landscape' | 'portrait' | 'square';
function videoOrientOf(ratioHw?: number): VideoOrient {
  if (!ratioHw || ratioHw <= 0 || !Number.isFinite(ratioHw)) return 'landscape';
  if (ratioHw < 0.8) return 'landscape';
  if (ratioHw > 1.2) return 'portrait';
  return 'square';
}
type VideoMetaHandler = (event: SyntheticEvent<HTMLVideoElement>) => void;

// 各表面尺寸策略（displayW/displayH 为像素；横屏默认宽框，竖屏默认高框）
// 集泳道小缩略图：统一矮条高度 ~80px，竖屏收窄成竖条
function epVideoThumbStyle(ratioHw?: number): CSSProperties {
  const o = videoOrientOf(ratioHw);
  if (o === 'portrait') return {width: 46, height: 82, objectFit: 'cover'};
  if (o === 'square') return {width: 62, height: 62, objectFit: 'cover'};
  return {width: 82, height: 46, objectFit: 'cover'};
}
// 集成片播放框
function epFinalVideoStyle(ratioHw?: number): CSSProperties {
  const o = videoOrientOf(ratioHw);
  if (o === 'portrait') return {width: 96, height: 170, objectFit: 'contain'};
  if (o === 'square') return {width: 150, height: 150, objectFit: 'contain'};
  return {width: 200, height: 113, objectFit: 'contain'};
}
// 段卡主视频：横屏给宽框，竖屏保持原竖框
function segMainVideoStyle(ratioHw?: number): CSSProperties {
  const o = videoOrientOf(ratioHw);
  if (o === 'portrait') return {width: 170, aspectRatio: '9 / 16', objectFit: 'cover'};
  if (o === 'square') return {width: 180, aspectRatio: '1 / 1', objectFit: 'cover'};
  return {width: 280, aspectRatio: '16 / 9', objectFit: 'cover'};
}
// 段内镜头小缩略图：横屏 96×54，竖屏 54×96（格内居中）
function segShotMediaStyle(ratioHw?: number): CSSProperties {
  const o = videoOrientOf(ratioHw);
  if (o === 'portrait') return {width: 54, height: 96, objectFit: 'cover', margin: '0 auto'};
  if (o === 'square') return {width: 64, height: 64, objectFit: 'cover', margin: '0 auto'};
  return {width: 96, height: 54, objectFit: 'cover'};
}
// 镜头卡主视频：竖屏收窄居中，横屏占满宽度
function mainClipStyle(ratioHw?: number): CSSProperties {
  const o = videoOrientOf(ratioHw);
  if (o === 'portrait') return {width: 150, height: Math.round(150 * (ratioHw ?? 16 / 9)), margin: '0 auto', objectFit: 'contain'};
  if (o === 'square') return {width: 160, height: 160, margin: '0 auto', objectFit: 'contain'};
  return {width: '100%', height: 'auto', maxHeight: 260, objectFit: 'contain'};
}

function AssetThumb({url, name, onZoom}: {url: string; name: string; onZoom: () => void}) {
  if (/\.(mp4|webm|mov|m4v)(\?.*)?$/i.test(url)) {
    return <video className="canvas-thumb is-video nodrag nowheel" src={url} controls preload="metadata" aria-label={name} />;
  }
  return (
    <button type="button" className="canvas-thumb" onDoubleClick={onZoom} title="双击放大查看">
      <img src={url} alt={name} loading="lazy" />
    </button>
  );
}

type ShotNodeData = {
  shot: CanvasShot;
  draft: ShotDraft;
  dirty: boolean;
  saving: boolean;
  savedFlash: boolean;
  boundAssets: {key: string; name: string; kind: string; image_url?: string; audio_url?: string}[];
  onDraftChange: (field: keyof ShotDraft, value: string) => void;
  onSave: (idx: number) => void;
  onPickReference: (idx: number) => void;
  onUnbind: (assetKey: string, shotIdx: number) => void;
  onZoom: (url: string) => void;
  /** 视频真实宽高比表（url → h/w），用于按横/竖屏显示 */
  videoRatios: Record<string, number>;
  /** <video> onloadedmetadata 回调：读真实宽高比 */
  onVideoMeta: VideoMetaHandler;
};

function EditableBlock({
  label,
  value,
  placeholder,
  onChange,
  rows = 3,
}: {
  label: string;
  value: string;
  placeholder: string;
  onChange: (value: string) => void;
  rows?: number;
}) {
  return (
    <label className="canvas-prompt-block is-editable">
      <span className="canvas-prompt-label">{label}</span>
      <textarea
        value={value}
        placeholder={placeholder}
        rows={rows}
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
      />
    </label>
  );
}

function SvgCheckMark({checked}: {checked: boolean}) {
  return (
    <span className={`canvas-svg-checkbox${checked ? ' is-on' : ''}`} aria-hidden="true">
      {checked && <Check size={11} strokeWidth={2.8} />}
    </span>
  );
}

// 质量自评维度中文名（与 utils/quality_assessment.DIMENSION_WEIGHTS 的键对应）
const QUALITY_DIM_LABEL: Record<string, string> = {
  hook: '开场钩子',
  cliffhanger: '结尾留钩',
  pacing: '节奏分布',
  beats: '爆点密度',
  camera_language: '镜头语言',
  dialogue_density: '对白密度',
  dramatic_function: '戏剧功能',
  transition_variety: '转场多样',
  continuity: '连续性',
};

// 镜头卡上的质量弱项色点：每个被标记维度一个红点，hover 显示维度名。
function QualityFlagDots({flags}: {flags?: string[] | null}) {
  if (!flags || flags.length === 0) return null;
  return (
    <span className="canvas-quality-flags" title={`质量弱项：${flags.map((f) => QUALITY_DIM_LABEL[f] ?? f).join('、')}`}>
      {flags.map((f) => (
        <span key={f} className="canvas-quality-dot" data-dim={f} aria-label={QUALITY_DIM_LABEL[f] ?? f} />
      ))}
    </span>
  );
}

function ShotNode({data}: NodeProps<FlowNode<ShotNodeData>>) {
  const {shot, draft, dirty, saving, savedFlash, boundAssets, onDraftChange, onSave, onPickReference, onUnbind, onZoom, videoRatios, onVideoMeta} = data;
  const [collapsed, setCollapsed] = useState(true);
  const toggleCollapse = (event: React.MouseEvent) => {
    event.stopPropagation();
    setCollapsed((c) => !c);
  };
  return (
    <div
      className={`canvas-node canvas-shot ${shot.has_description ? '' : 'is-empty'} ${dirty ? 'is-dirty' : ''}`}
      onDoubleClick={(event) => { event.stopPropagation(); setCollapsed((c) => !c); }}
    >
      <Handle type="target" position={Position.Left} />
      <header>
        <button type="button" className="canvas-shot-collapse" onClick={toggleCollapse} title={collapsed ? '展开摘要' : '折叠摘要'} aria-label={collapsed ? '展开' : '折叠'}>
          <ChevronRight size={13} strokeWidth={2.2} aria-hidden="true" className={collapsed ? '' : 'is-open'} />
        </button>
        <strong>镜头 {shot.idx}</strong>
        {shot.duration !== null && <span className="canvas-badge is-param">{shot.duration}s</span>}
        {shot.cam_idx !== null && <span className="canvas-badge is-param">机位{shot.cam_idx}</span>}
        {shot.transition_type && <span className="canvas-badge is-param">{shot.transition_type}</span>}
        {shot.clip_url
          ? (shot.clip_stale
            ? <span className="canvas-badge is-stale" title="分镜描述比片段新：提示词/画面已改动，渲染时会判定失效并重出该镜">片段待重出</span>
            : <span className="canvas-badge is-clip">片段<Check size={10} strokeWidth={2.6} aria-hidden="true" /></span>)
          : shot.keyframe_url ? <span className="canvas-badge is-param">关键帧</span> : null}
      </header>
      {shot.clip_url ? (
        <video
          className="canvas-clip-preview is-main nodrag nowheel"
          src={shot.clip_url}
          controls
          preload="metadata"
          aria-label={`镜头${shot.idx} 片段`}
          onLoadedMetadata={onVideoMeta}
          style={mainClipStyle(videoRatios[shot.clip_url])}
        />
      ) : shot.keyframe_url ? (
        <AssetThumb url={shot.keyframe_url} name={`镜头${shot.idx} 关键帧`} onZoom={() => onZoom(shot.keyframe_url as string)} />
      ) : (
        <div className="canvas-thumb is-empty-frame">未渲染</div>
      )}
      {collapsed && (
        <div className="canvas-shot-summary">
          {[['画面', draft.visual_desc], ['动作', draft.motion_desc], ['音频', draft.audio_desc]]
            .filter((entry) => entry[1] && (entry[1] as string).trim().length > 0)
            .map((entry) => (
              <div key={entry[0] as string} className="canvas-shot-summary-row">
                <span className="canvas-shot-summary-label">{entry[0]}</span>
                <span className="canvas-shot-summary-text">{(entry[1] as string).trim()}</span>
              </div>
            ))}
          {!draft.visual_desc?.trim() && !draft.motion_desc?.trim() && !draft.audio_desc?.trim() && <p className="canvas-shot-summary-empty">无提示词，点击节点在弹窗中编辑</p>}
        </div>
      )}
      {!collapsed && (
        <div className="canvas-shot-prompts is-readonly">
          <EditableBlock label="画面" value={draft.visual_desc} placeholder="画面描述…" onChange={() => undefined} rows={3} />
          <EditableBlock label="动作" value={draft.motion_desc} placeholder="动作/运镜…" onChange={() => undefined} rows={2} />
          <EditableBlock label="音频" value={draft.audio_desc} placeholder="对白/音效…" onChange={() => undefined} rows={2} />
          <p className="canvas-shot-prompts-tip">提示词编辑请在选中镜头后的弹窗中进行</p>
        </div>
      )}
      {boundAssets.length > 0 && (
        <div className="canvas-shot-refs">
          {boundAssets.map((asset) => (
            <button
              key={asset.key}
              type="button"
              className="canvas-ref-chip"
              title={`${asset.kind}：${asset.name}（点击断开绑定）`}
              onClick={() => onUnbind(asset.key, shot.idx)}
            >
              {asset.image_url ? (
                <img src={asset.image_url} alt="" loading="lazy" />
              ) : asset.audio_url ? (
                <span className="canvas-ref-media"><Music size={12} strokeWidth={1.9} aria-hidden="true" /></span>
              ) : (
                <span className="canvas-ref-media">{asset.kind.slice(0, 1)}</span>
              )}
              <span className="canvas-ref-meta">
                <span className="canvas-ref-kind">{asset.kind}</span>
                <span className="canvas-ref-name">{asset.name}</span>
              </span>
              <X size={11} strokeWidth={2.4} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
      <footer>
        <button type="button" className="canvas-ref-btn" onClick={() => onPickReference(shot.idx)}><Plus size={12} strokeWidth={2.2} aria-hidden="true" />参考</button>
        <button
          type="button"
          className={`canvas-save-btn ${dirty ? 'is-dirty' : ''}`}
          onClick={() => onSave(shot.idx)}
          disabled={!dirty || saving}
        >
          {saving ? '保存中…' : savedFlash ? <>已保存<Check size={11} strokeWidth={2.6} aria-hidden="true" /></> : dirty ? '保存' : '已同步'}
        </button>
      </footer>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

type SegmentNodeData = {
  segment: CanvasSegment;
  shots: CanvasShot[];
  boundAssets: {key: string; name: string; kind: string; image_url?: string; audio_url?: string}[];
  onSelectShot: (idx: number) => void;
  onOpenPrompt: (shot: CanvasShot) => void;
  onZoom: (url: string) => void;
  /** 段卡"生成本段"按钮：以画布当前内容为准发起段级渲染 */
  onRenderSegment: (segmentId: number) => void;
  /** 段卡"段提示词"按钮：弹窗回看本段完整生成提示词 */
  onOpenSegmentPrompt: (segment: CanvasSegment) => void;
  /** 逐镜质量弱项映射：镜号 → 维度名列表（来自质量自评报告 shot_issues） */
  qualityFlags?: Record<number, string[]>;
  /** 视频真实宽高比表（url → h/w），用于按横/竖屏显示 */
  videoRatios: Record<string, number>;
  /** <video> onloadedmetadata 回调：读真实宽高比 */
  onVideoMeta: VideoMetaHandler;
};

type EpBoxNodeData = {label: string; count: number; shots: number; assets: SegmentNodeData['boundAssets']; videoShots: CanvasShot[]; onOpenPrompt: (shot: CanvasShot) => void; qualityFlags?: Record<number, string[]>; final_video_url?: string | null; videoRatios: Record<string, number>; onVideoMeta: VideoMetaHandler;};

// 整剧总览的集分组框：AniShort 风格"一集一画布"分组的容器节点。
// 工业级泳道：header（集标题）→ 资产泳道（绑定资产 chips 单行滚动）→ 视频组泳道（已出片段单行滚动）。
// 注意：泳道高度必须与 EP_HEADER_H/EP_VIDEO_LANE_EXTRA 常量对齐——段卡子节点按该高度绝对定位。
function EpBoxNode({data}: NodeProps<FlowNode<EpBoxNodeData>>) {
  const {label, count, shots, assets, videoRatios, onVideoMeta} = data;
  const [assetsCollapsed, setAssetsCollapsed] = useState(true);
  const kinds = ['角色', '场景', '道具', '音色'] as const;
  return (
    <div className="canvas-node canvas-ep-box">
      <header>
        <strong>{label}</strong>
        <span className="canvas-badge">{count} 段</span>
        <span className="canvas-badge is-clip">{shots} 镜</span>
      </header>
      <div className={`canvas-ep-lanes ${data.final_video_url ? 'has-final' : ''}`}>
        {data.final_video_url && (
          <div className="canvas-ep-lane is-final">
            <span className="canvas-ep-lane-label">成片</span>
            <video
              className="canvas-ep-final-video nodrag nowheel"
              src={data.final_video_url}
              controls
              preload="metadata"
              aria-label={`${label} 成片`}
              onLoadedMetadata={onVideoMeta}
              style={epFinalVideoStyle(videoRatios[data.final_video_url])}
            />
          </div>
        )}
        <div className="canvas-ep-lanes-main">
          <div className="canvas-ep-lane is-assets">
            <span className="canvas-ep-lane-label">资产</span>
            <button
              type="button"
              className="canvas-ep-lane-toggle"
              onClick={(event) => { event.stopPropagation(); setAssetsCollapsed((c) => !c); }}
              title={assetsCollapsed ? '展开该集绑定资产' : '收起资产列表'}
            >
              {assets.length} 项绑定
              <ChevronDown size={12} strokeWidth={2.2} aria-hidden="true" className={assetsCollapsed ? 'is-collapsed' : ''} />
            </button>
            <div className="canvas-ep-asset-chips">
              {(assetsCollapsed ? assets.slice(0, 8) : assets).map((a) => (
                <span key={`${a.kind}-${a.key}`} className={`canvas-ep-asset-chip is-${a.kind}`} title={`${a.name}（${kinds.includes(a.kind as (typeof kinds)[number]) ? a.kind : '资产'}）`}>
                  {a.image_url && <img src={a.image_url} alt="" loading="lazy" />}
                  {a.audio_url && <span className="canvas-ep-asset-voice"><AudioLines size={11} strokeWidth={2} aria-hidden="true" /></span>}
                  <span className="canvas-ep-asset-name">{a.name}</span>
                </span>
              ))}
              {assets.length === 0 && <span className="canvas-ep-asset-empty">本集暂无绑定资产</span>}
            </div>
          </div>
          {data.videoShots.length > 0 && (
          <div className="canvas-ep-lane is-video">
            <span className="canvas-ep-lane-label">视频组</span>
            <span className="canvas-ep-lane-hint">已出 {data.videoShots.length} 镜 · 点击看提示词</span>
            <div className="canvas-ep-video-row">
              {data.videoShots.map((shot) => (
                <button
                  key={`${shot.ep_id ?? ''}-${shot.idx}`}
                  type="button"
                  className="canvas-ep-video-cell"
                  title={`镜头 ${shot.idx} · ${shot.duration !== null ? `${shot.duration}s` : ''} · 点击查看提示词/资产引用`}
                  onClick={(event) => { event.stopPropagation(); data.onOpenPrompt(shot); }}
                >
                  <video
                    className="canvas-ep-video-thumb nodrag nowheel"
                    src={shot.clip_url as string}
                    muted
                    preload="metadata"
                    onLoadedMetadata={onVideoMeta}
                    style={epVideoThumbStyle(videoRatios[shot.clip_url as string])}
                    onClick={(event) => { event.stopPropagation(); data.onOpenPrompt(shot); }}
                  />
                  <span className="canvas-ep-video-idx">镜{shot.idx}</span>
                  <QualityFlagDots flags={data.qualityFlags?.[shot.idx]} />
                </button>
              ))}
            </div>
          </div>
          )}
        </div>
      </div>
    </div>
  );
}

function SegmentNode({data}: NodeProps<FlowNode<SegmentNodeData>>) {
  const {segment, shots, boundAssets, onSelectShot, onZoom, videoRatios, onVideoMeta} = data;
  const [collapsed, setCollapsed] = useState(false);
  const sortedShots = [...shots].sort((a, b) => a.idx - b.idx);
  const clipCount = sortedShots.filter((s) => s.clip_url).length;
  const segVideoUrl = segment.clip_url ?? null;
  const segVideoRatio = segVideoUrl ? videoRatios[segVideoUrl] : undefined;
  return (
    <div className={`canvas-node canvas-segment canvas-seg-scene ${collapsed ? 'is-collapsed' : ''}`}>
      <Handle type="target" position={Position.Left} className="canvas-seg-handle" />
      <Handle type="source" position={Position.Right} className="canvas-seg-handle" />
      <header>
        <button
          type="button"
          className="canvas-shot-collapse"
          onClick={(event) => { event.stopPropagation(); setCollapsed((c) => !c); }}
          title={collapsed ? '展开段内镜头' : '折叠镜头列表'}
          aria-label={collapsed ? '展开' : '折叠'}
        >
          <ChevronRight size={13} strokeWidth={2.2} aria-hidden="true" className={collapsed ? '' : 'is-open'} />
        </button>
        <strong>Scene{segment.segment_id}</strong>
        {segment.duration !== null && <span className="canvas-badge is-param">{segment.duration}s</span>}
        <span className="canvas-badge">{sortedShots.length} 镜</span>
        {segVideoUrl ? (
          <span className="canvas-badge is-clip" title="段级直出视频已生成（一段一个提示词一次生成）">段视频<Check size={10} strokeWidth={2.6} aria-hidden="true" /></span>
        ) : clipCount > 0 ? (
          <span className="canvas-badge is-clip">{clipCount} 已出片段</span>
        ) : null}
        <span className="canvas-badge is-fullref" title="视频生成使用全能参考模式：角色/场景/道具/音色参考图直传，非首尾帧">全能参考</span>
        <button
          type="button"
          className="canvas-seg-prompt-btn"
          title="查看本段视频生成所用的完整提示词（资产绑定/机位/光线/时间线/禁项）"
          onClick={(event) => { event.stopPropagation(); data.onOpenSegmentPrompt(segment); }}
        >
          <ClipboardList size={13} strokeWidth={1.9} aria-hidden="true" />
          段提示词
        </button>
        <button
          type="button"
          className="canvas-seg-render-btn"
          title="以当前画布内容为准生成本段视频（段级直出，480p 竖屏）；与对话框出片同一入口，完成后视频自动挂回本段"
          onClick={(event) => { event.stopPropagation(); data.onRenderSegment(segment.segment_id); }}
        >
          {segVideoUrl
            ? <><RotateCw size={12} strokeWidth={2} aria-hidden="true" />重出本段</>
            : <><Play size={12} strokeWidth={2} aria-hidden="true" />生成本段</>}
        </button>
      </header>
      {collapsed ? (
        <div className="canvas-seg-scene-body">
          {segVideoUrl && (
            <video
              className="canvas-seg-video-main nodrag nowheel"
              src={segVideoUrl}
              muted
              controls
              preload="metadata"
              aria-label={`段${segment.segment_id} 视频`}
              onLoadedMetadata={onVideoMeta}
              style={segMainVideoStyle(segVideoRatio)}
            />
          )}
          {segment.title && <p className="canvas-segment-title">{segment.title}</p>}
        </div>
      ) : (
        <div className="canvas-seg-scene-body">
          <div className="canvas-seg-toprow">
            {segVideoUrl && (
              <video
                className="canvas-seg-video-main nodrag nowheel"
                src={segVideoUrl}
                muted
                controls
                preload="metadata"
                aria-label={`段${segment.segment_id} 视频`}
                onLoadedMetadata={onVideoMeta}
                style={segMainVideoStyle(segVideoRatio)}
              />
            )}
            <div className="canvas-seg-info">
              {segment.title && <p className="canvas-seg-scene-title">{segment.title}</p>}
              <div className="canvas-seg-params">
                <span className="canvas-param-tag">Seedance 2.0</span>
                <span className="canvas-param-tag is-mode">全能参考</span>
                <span className="canvas-param-tag">720p·9:16</span>
                <span className="canvas-param-tag is-ref">以连入参考图为准</span>
              </div>
              {boundAssets.length > 0 && (
                <div className="canvas-shot-refs">
                  {boundAssets.map((asset) => (
                    <span key={asset.key} className="canvas-ref-chip is-static" title={`${asset.kind}：${asset.name}`}>
                      {asset.image_url ? (
                        <img src={asset.image_url} alt="" loading="lazy" />
                      ) : asset.audio_url ? (
                        <span className="canvas-ref-media"><Music size={12} strokeWidth={1.9} aria-hidden="true" /></span>
                      ) : (
                        <span className="canvas-ref-media">{asset.kind.slice(0, 1)}</span>
                      )}
                      <span className="canvas-ref-meta">
                        <span className="canvas-ref-kind">{asset.kind}</span>
                        <span className="canvas-ref-name">{asset.name}</span>
                      </span>
                    </span>
                  ))}
                </div>
              )}
              {boundAssets.length === 0 && <p className="canvas-seg-shots-empty">暂无参考资产（角色/场景/道具确认后自动连入）</p>}
              <div className="canvas-seg-inline-prompt">
                {segment.change_timeline && (
                  <section>
                    <h4>动作时间线</h4>
                    <p>{segment.change_timeline}</p>
                  </section>
                )}
                {segment.staging_and_axis && (
                  <section>
                    <h4>机位与轴线</h4>
                    <p>{segment.staging_and_axis}</p>
                  </section>
                )}
                {segment.style_lighting && (
                  <section>
                    <h4>光线与美术</h4>
                    <p>{segment.style_lighting}</p>
                  </section>
                )}
                {!segment.change_timeline && !segment.staging_and_axis && !segment.style_lighting && (
                  <p className="canvas-seg-shots-empty">本段提示词未落盘（重跑分镜后自动写入 segments.json）</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
      <Handle type="source" position={Position.Right} className="canvas-seg-handle" />
    </div>
  );
}

type AssetNodeData = {item: CanvasAssetItem; onZoom: (url: string) => void};

function AssetNode({data, kind, selected}: NodeProps<FlowNode<AssetNodeData>> & {kind: 'char' | 'scene' | 'prop' | 'voice'}) {
  const {item, onZoom} = data;
  const label = kind === 'char' ? '角色' : kind === 'scene' ? '场景' : kind === 'prop' ? '道具' : '音色';
  return (
    <div className={`canvas-node canvas-asset is-${kind}`}>
      <Handle type="target" position={Position.Left} />
      {selected && (
        <NodeResizer
          isVisible={true}
          minWidth={150}
          minHeight={110}
          maxWidth={640}
          maxHeight={520}
          keepAspectRatio={false}
          lineClassName="canvas-node-resize-line"
          handleClassName="canvas-node-resize-handle"
        />
      )}
      <header>
        <span className="canvas-kind">{label}</span>
      </header>
      {item.image_url ? (
        <AssetThumb url={item.image_url} name={`${item.name} ${label}`} onZoom={() => onZoom(item.image_url as string)} />
      ) : item.audio_url ? (
        <audio controls src={item.audio_url} preload="none" />
      ) : null}
      <p className="canvas-asset-name" title={item.name}>{item.name}</p>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

type AssetColData = {label: string; count: number};

// 资产生成组（即梦式）：分组框为父节点（统一拖动管理），每张资产图为独立子节点（端口锚定在卡片右缘，连线从资产图旁精确发出）
type AssetGroupNodeData = {
  kind: 'char' | 'scene' | 'prop' | 'voice';
  label: string;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
};

function AssetGroupNode({data}: NodeProps<FlowNode<AssetGroupNodeData>>) {
  const {kind, label, count, collapsed, onToggle} = data;
  const kindLabel = kind === 'char' ? '角色' : kind === 'scene' ? '场景' : kind === 'prop' ? '道具' : '音色';
  return (
    <div className={`canvas-node canvas-asset-group is-${kind}${collapsed ? ' is-collapsed' : ''}`}>
      <header className="canvas-asset-group-header">
        <button type="button" className="canvas-asset-group-collapse" onClick={(event) => { event.stopPropagation(); onToggle(); }} title={collapsed ? '展开该组资产' : '折叠该组资产'} aria-label={collapsed ? '展开' : '折叠'}>
          <ChevronRight size={13} strokeWidth={2.2} aria-hidden="true" className={collapsed ? '' : 'is-open'} />
        </button>
        <span className="canvas-kind">{kindLabel}</span>
        <strong className="canvas-asset-group-title">{label}生成组</strong>
        <span className="canvas-asset-group-count">{count}</span>
      </header>
      {collapsed && <div className="canvas-asset-group-collapsed-hint">已折叠 · {count} 项资产（点击箭头展开）</div>}
    </div>
  );
}

type AssetCardNodeData = {
  kindLabel: string;
  item: CanvasAssetItem;
  refs: number;
  clips: number;
  onDetail: (kind: string, item: CanvasAssetItem) => void;
  onZoom: (url: string) => void;
};

// 资产卡片子节点：图片/音频 + 名称 + 引用徽章，右侧一个 source 端口（hover 显示）
function AssetCardNode({data}: NodeProps<FlowNode<AssetCardNodeData>>) {
  const {kindLabel, item, refs, clips, onDetail, onZoom} = data;
  return (
    <div className={`canvas-asset-card ${item.image_url ? '' : 'is-audio'}`} title={item.name}>
      {item.image_url ? (
        <img
          className="canvas-asset-card-img"
          src={item.image_url}
          alt={item.name}
          loading="lazy"
          onClick={(event) => { event.stopPropagation(); onDetail(kindLabel, item); }}
          onDoubleClick={(event) => { event.stopPropagation(); onZoom(item.image_url as string); }}
        />
      ) : item.audio_url ? (
        <audio className="canvas-asset-card-audio" controls src={item.audio_url} preload="none" onClick={(event) => event.stopPropagation()} />
      ) : null}
      <span className="canvas-asset-card-name">{item.name}</span>
      {refs > 0 && (
        <span className={`canvas-asset-card-badge ${clips > 0 ? 'is-clip' : ''}`} title={clips > 0 ? `被 ${refs} 个镜头引用，其中 ${clips} 镜已出片` : `被 ${refs} 个镜头引用`}>
          {clips > 0 ? `${refs} 镜·已出片` : `${refs} 镜`}
        </span>
      )}
      <Handle type="source" position={Position.Right} className="canvas-asset-card-handle" />
    </div>
  );
}

function AssetColNode({data}: NodeProps<FlowNode<AssetColData>>) {
  const {label, count} = data;
  return (
    <div className="canvas-asset-col">
      <span className="canvas-asset-col-name">{label}</span>
      <span className="canvas-asset-col-count">{count}</span>
    </div>
  );
}

/**
 * （已移除）编组卡片的「双击判定」注册表 GROUP_DBL_WINDOW / groupClickStamp。
 *
 * 第 15 轮第 5 次修正：用户明确要求「单击节点就还是选中单个不影响单击弹窗」——
 * 组内卡片单击恢复为**单卡选中 + 打开自己弹窗**，于是"单击选组、双击进单张"这套
 * 双击判定彻底不需要了（批量选择由组框承担）。留着空壳会误导后续维护者，
 * 所以连同模块级 Map 一起删掉。
 */

type NoteNodeData = {
  note: CanvasNote;
  onChange: (id: string, text: string) => void;
  /** 编组：本备注所属组的成员 flow id（含自己） */
  groupMembers?: string[] | null;
  /** 按压即把整组写进 React Flow 内部选中集合（与媒体卡同一套，见 MediaNodeData 注释） */
  onPressGroup?: (members: string[]) => void;
};

function NoteNode({data}: NodeProps<FlowNode<NoteNodeData>>) {
  const {note, onChange, groupMembers, onPressGroup} = data;
  return (
    // 备注同样参与编组：按压时先把整组选中，否则 React Flow 的 startDrag 会把选中收敛成这一个
    <div
      className="canvas-node canvas-note"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        if (e.shiftKey || e.ctrlKey || e.metaKey) return;
        if (groupMembers && groupMembers.length >= 2 && onPressGroup) onPressGroup(groupMembers);
      }}
    >
      {/* ★ 端口必须复用媒体卡的 .canvas-media-port（第 23 轮）：
          备注原来用的是 React Flow 默认 handle，落在 ui-refinements.css 那条
          `.react-flow__handle{background:#3d3d3d;border:1px solid #121212}` 上 ——
          画布缩小后就是两侧各一个 8px 灰疙瘩，和媒体卡那套「深玻璃 + SVG ＋ 圆点」
          完全不是一个东西（用户反馈"跟整个系统不搭"里的其中一处）。
          换成同一个类名后，两个节点的端口在尺寸、配色、悬停高亮上自动一致，
          包括 --port-shrink / --edge-ui-scale 那套缩放补偿也一并继承。
          ★★ 但只有类名不够 —— 媒体卡端口的 ＋ 号是**子元素 <svg class="canvas-port-icon">**，
             不是 CSS 生成的（见下方 MediaNode 里那两处 Handle）。
             只换类名不补 svg，就得到"圆圈里空空如也"（用户截图：节点加号不显示）。
             所以这里把同一个 SVG 一起带上，两端完全同构。 */}
      <Handle type="target" position={Position.Left} className="canvas-media-port is-target">
        <svg className="canvas-port-icon" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1.6V10.4M1.6 6H10.4" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </Handle>
      <header>
        <span className="canvas-kind">文本备注</span>
      </header>
      <textarea
        className="canvas-note-text nodrag"
        value={note.text}
        placeholder="创意 / 提示 / 备注…"
        onChange={(event) => onChange(note.id, event.target.value)}
        spellCheck={false}
      />
      <Handle type="source" position={Position.Right} className="canvas-media-port is-source">
        <svg className="canvas-port-icon" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1.6V10.4M1.6 6H10.4" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </Handle>
    </div>
  );
}

const MEDIA_KIND_META: Record<CanvasMediaNodeKind, {label: string; w: number; h: number}> = {
  text: {label: '文本节点', w: 200, h: 120},
  image: {label: '图片节点', w: 280, h: 180},
  audio: {label: '音频节点', w: 200, h: 130},
  video: {label: '视频节点', w: 180, h: 280},
};

// 媒体类型图标：统一走 lucide 的矢量路径（替代早前的字符占位 ▤/▧/♪/▷ 和手写的近似 SVG）。
// 统一 24 网格 + 1.75 描边 + round 端点，与界面其它图标严格同源，不会再出现
// "有的图标粗、有的图标歪、基线不齐"的问题。
const KIND_ICON: Record<CanvasMediaNodeKind, typeof Type> = {
  text: StickyNote,
  image: ImageGlyph,
  audio: Music,
  video: Video,
};
const AUDIO_WAVE_BARS = [28, 44, 62, 36, 76, 52, 88, 58, 42, 70, 34, 64, 82, 48, 72, 38, 56, 30];

function KindIcon({kind, size = 20, className}: {kind: CanvasMediaNodeKind; size?: number; className?: string}) {
  const Glyph = KIND_ICON[kind];
  return <Glyph className={className} size={size} strokeWidth={1.75} aria-hidden="true" />;
}

function LargeCanvasOverview({
  nodes,
  edges,
  onFocus,
  onClose,
}: {
  nodes: CanvasMediaNode[];
  edges: Edge[];
  onFocus: (node: CanvasMediaNode) => void;
  onClose: () => void;
}) {
  const nodeKey = useMemo(
    () => `${nodes.length}:${nodes[0]?.id || ''}:${nodes[nodes.length - 1]?.id || ''}`,
    [nodes],
  );
  const [renderedCount, setRenderedCount] = useState(() => (
    Math.min(nodes.length, LARGE_CANVAS_OVERVIEW_CHUNK_SIZE)
  ));
  useEffect(() => {
    setRenderedCount(Math.min(nodes.length, LARGE_CANVAS_OVERVIEW_CHUNK_SIZE));
  }, [nodeKey, nodes.length]);
  useEffect(() => {
    if (renderedCount >= nodes.length) return;
    const timer = window.setTimeout(() => {
      startTransition(() => {
        setRenderedCount((count) => (
          Math.min(nodes.length, count + LARGE_CANVAS_OVERVIEW_CHUNK_SIZE)
        ));
      });
    }, 16);
    return () => window.clearTimeout(timer);
  }, [nodes.length, renderedCount]);
  const layout = useMemo(() => {
    if (nodes.length === 0) {
      return {width: 1, height: 1, byId: new Map<string, CanvasMediaNode>()};
    }
    const width = Math.max(...nodes.map((node) => node.x + MEDIA_KIND_META[node.kind].w)) + 160;
    const height = Math.max(...nodes.map((node) => node.y + MEDIA_KIND_META[node.kind].h + 80)) + 160;
    return {
      width,
      height,
      byId: new Map(nodes.map((node) => [`media-${node.id}`, node])),
    };
  }, [nodes]);
  const renderedNodes = useMemo(
    () => nodes.slice(0, renderedCount),
    [nodes, renderedCount],
  );
  const renderedNodeIds = useMemo(
    () => new Set(renderedNodes.map((node) => `media-${node.id}`)),
    [renderedNodes],
  );
  const visibleEdges = useMemo(
    () => edges.filter((edge) => (
      renderedNodeIds.has(edge.source) && renderedNodeIds.has(edge.target)
    )),
    [edges, renderedNodeIds],
  );
  return (
    <section
      className="canvas-large-overview"
      aria-label="大型画布总览"
      aria-busy={renderedCount < nodes.length}
    >
      <header className="canvas-large-overview-head">
        <span><LayoutGrid size={15} strokeWidth={1.9} aria-hidden="true" />大型画布总览</span>
        <small>{nodes.length} 个节点 · 点击卡片进入工作区</small>
        <button type="button" onClick={onClose}><Maximize2 size={13} strokeWidth={1.9} aria-hidden="true" />进入工作区</button>
      </header>
      <div className="canvas-large-overview-map">
        <svg
          className="canvas-large-overview-edges"
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          {visibleEdges.map((edge) => {
            const source = layout.byId.get(edge.source);
            const target = layout.byId.get(edge.target);
            if (!source || !target) return null;
            return (
              <line
                key={edge.id}
                x1={source.x + MEDIA_KIND_META[source.kind].w}
                y1={source.y + MEDIA_KIND_META[source.kind].h / 2}
                x2={target.x}
                y2={target.y + MEDIA_KIND_META[target.kind].h / 2}
              />
            );
          })}
        </svg>
        {renderedNodes.map((node) => (
          <button
            key={node.id}
            type="button"
            className={`canvas-large-overview-node is-${node.kind} is-${node.status || 'idle'}`}
            style={{
              left: `${(node.x / layout.width) * 100}%`,
              top: `${(node.y / layout.height) * 100}%`,
              width: `${Math.max(0.8, (MEDIA_KIND_META[node.kind].w / layout.width) * 100)}%`,
              height: `${Math.max(1.2, ((MEDIA_KIND_META[node.kind].h + 44) / layout.height) * 100)}%`,
            }}
            title={`${node.title || MEDIA_KIND_META[node.kind].label} · ${node.status || 'idle'}`}
            onClick={() => onFocus(node)}
          >
            <KindIcon kind={node.kind} size={12} />
            <span>{node.title || MEDIA_KIND_META[node.kind].label}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

type MediaNodeData = {
  node: CanvasMediaNode;
  renderMode?: 'overview' | 'compact' | 'full';
  onChange: (
    id: string,
    patch: Partial<CanvasMediaNode>,
    options?: {mediaVersionSelection?: boolean},
  ) => void;
  onGenerate: (node: CanvasMediaNode) => boolean;
  onDelete: (id: string) => void;
  onZoom: (url: string) => void;
  onReadText: (node: CanvasMediaNode) => void;
  onCopyText: (node: CanvasMediaNode) => void;
  /** 点击卡片 → 弹窗编辑 */
  onEdit: (node: CanvasMediaNode) => void;
  /** 可引用资产候选（image/audio 节点），@资产 选择器用 */
  assets?: CanvasMediaNode[];
  catalog?: ModelCatalog | null;
  /** 提示词栏打开时高亮本卡片（即梦风：底部居中编辑栏 + 选中节点高亮） */
  active?: boolean;
  generationBusy?: boolean;
  /** 编组：本卡所属组的成员 flow id（含自己）。点一下就整组选中，从而"按组拖动" */
  groupMembers?: string[] | null;
  /** 把整组一起选中（编组后按组拖动依赖它） */
  onSelectGroup?: (members: string[]) => void;
  /**
   * 按压即把整组写进 React Flow 的**内部**选中集合（编组后「按组拖动」的真正入口）。
   *
   * ★ 为什么只靠 onClick 选中整组不够 —— 这是 React Flow 内部的时序，不是我们没选中：
   *   拖拽由 d3-drag 接管，它在 pointermove 越过阈值时调 `startDrag()`，里面先执行
   *   `handleNodeClick(id)`：若该节点此刻在 store 里**未被选中**，走 `addSelectedNodes([id])`
   *   的非多选分支 → `getSelectionChanges(nodeLookup, new Set([id]))`，**把选中集合收敛成只剩这一张**；
   *   紧接着 `getDragItems()` 只遍历 `node.selected` 的节点 → 天然只拖一张。
   *   而 React 的 onClick 是合成事件，时序在 pointerdown/pointermove 之后，救不回来。
   *   实测印证：按住组内一张卡拖动，`selected` 数量在拖动开始时从 2 掉到 1，另一张位移为 0。
   *
   * ★ 因此必须在 **pointerdown**（早于 d3 的 dragstart）把整组写进 `nodeLookup`。
   *   用 ref 直改 store 而不是 setState：setState 要走一次 React 调度，赶不上同一帧里的 dragstart。
   */
  onPressGroup?: (members: string[]) => void;
  onUploadLocal: (
    node: CanvasMediaNode,
    file: File,
    options: {
      signal?: AbortSignal;
      onProgress?: (percent: number) => void;
    },
  ) => Promise<void>;
  suppressNodeOpen: () => void;
  isNodeOpenSuppressed: () => boolean;
};

// ---- 生成参数面板（详细属性：模型/清晰度/比例/推理强度/引用资产/音色） ----
const IMAGE_MODELS = [
  'doubao-seedream-5-0-pro',
];
const VIDEO_MODELS = [
  'doubao-seedance-2-0', 'doubao-seedance-2-0-mini', 'doubao-seedance-2-0-fast', 'doubao-seedance-2-5',
];
const TEXT_MODELS = ['deepseek-flash-1017'];
const AUDIO_MODELS = ['cosyvoice-v3-5-flash'];
const RATIOS = ['1:1', '16:9', '9:16', '4:3', '3:4', '21:9', 'adaptive'];
const CLARITIES = ['1.5K', '2K', '4K'];
const GUIDANCES = ['轻度', '中等', '高', '极高', '最高'];
const RESOLUTIONS = ['480P', '720P', '1080P'];
/**
 * 音频（TTS）的可批量项兜底选项（第 33 轮，用户「音频没有加批量设置」）。
 * ★ 与模型目录 `cosyvoice-v3-5-flash` 的 params 一一对应；
 *   catalog 到位时一律以 schema 为准，这三个只是目录没加载时的兜底，
 *   避免"目录一慢就整块空白"。
 */
const AUDIO_FORMATS = ['mp3', 'wav'];
const AUDIO_SAMPLE_RATES = ['16000', '22050', '24000', '44100', '48000'];
const AUDIO_MODES = ['design', 'speech'];
/** 采样率 → 更易读的显示（24000 → 24 kHz） */
const audioRateLabel = (v: string) => (/^\d+$/.test(v) ? `${Math.round(Number(v) / 1000)} kHz` : v);
const AUDIO_MODE_LABELS: Record<string, string> = {design: '音色设计', speech: '指定音色'};
// 清晰度 × 比例 → size（长边对齐；1.5K = 长边 1536 ≈ ≤261 万像素 0.3 元）
const CLARITY_SIZES: Record<string, Record<string, string>> = {
  '1K': {'1:1': '1024x1024', '16:9': '1024x576', '9:16': '576x1024', '4:3': '1024x768', '3:4': '768x1024', '21:9': '1024x438'},
  '1.5K': {'1:1': '1536x1536', '16:9': '1536x864', '9:16': '864x1536', '4:3': '1536x1152', '3:4': '1152x1536', '21:9': '1536x658'},
  '2K': {'1:1': '2048x2048', '16:9': '2048x1152', '9:16': '1152x2048', '4:3': '2048x1536', '3:4': '1536x2048', '21:9': '2048x878'},
  '4K': {'1:1': '4096x4096', '16:9': '4096x2304', '9:16': '2304x4096', '4:3': '4096x3072', '3:4': '3072x4096', '21:9': '4096x1755'},
};
const DURATION_OPTS = ['4s', '5s', '10s', '15s', '20s', '30s'];
// 文本节点「生成长度」：映射到后端 max_tokens。
// 400 ≈ 短句/一句话种子，1200 ≈ 一段梗概，3000 ≈ 完整分镜说明。
const TEXT_LEN_OPTS = [
  {label: '短', tokens: 400},
  {label: '中', tokens: 1200},
  {label: '长', tokens: 3000},
];
// 文本采样温度的三个常用档位（对应单卡弹窗 0~2 滑块的"稳/中/活"三档）。
// 单卡是连续滑块，批量这边给离散档 —— 批量操作要能一眼比出差异，
// 三个值足够覆盖"更贴提示词 / 平衡 / 更发散"这三种实际诉求。
const TEXT_TEMP_OPTS = ['0.2', '0.7', '1.2'];

// 模型元数据（即梦风下拉：友好名 + 徽章色 + 描述；存储仍写完整 model id）
// 模型元数据。tone 只用于「模型按钮上的小圆点」和「徽章底色」，
// ★ 配色规范：全站强调色只有一支蓝 #4d9fff（见 ui-refinements.css 顶部色板）。
//   早期这里用了紫/绿/橙/蓝四色，模型下拉一展开就是一片彩色徽章，与其它浮层的
//   中性深灰完全不搭（用户截图反馈"颜色搭配不统一"）。现在统一收敛到蓝色单色系，
//   用明度区分档位：Pro/最新 = 亮蓝，标准 = 中蓝，轻量/Fast = 灰蓝。
const TONE_STRONG = '#4d9fff';
const TONE_MID = '#7fb0ff';
const TONE_SOFT = '#8fa3c0';
const MODEL_META: Record<string, {label: string; badge: string; tone: string; desc: string}> = {
  'doubao-seedream-5-0-pro': {label: 'Seedream 5.0 Pro', badge: 'Pro', tone: TONE_STRONG, desc: '顶级画质 · 细节与构图最强'},
  'doubao-seedance-2-0': {label: 'Seedance 2.0', badge: '2.0', tone: TONE_STRONG, desc: '标准视频 · 最高 1080P'},
  'doubao-seedance-2-0-mini': {label: 'Seedance 2.0 mini', badge: 'mini', tone: TONE_SOFT, desc: '轻量快速 · 最高 720P / 15 秒'},
  'doubao-seedance-2-0-fast': {label: 'Seedance 2.0 fast', badge: 'fast', tone: TONE_SOFT, desc: '极速出片 · 最高 720P / 15 秒'},
  'doubao-seedance-2-5': {label: 'Seedance 2.5', badge: '2.5', tone: TONE_MID, desc: '新一代视频 · 最高 1080P / 30 秒'},
  'agnes-image-2.5-flash': {label: 'Agnes Image 2.5 Flash', badge: '25F', tone: TONE_MID, desc: '高信息密度 · 多参考图与图生图'},
  'agnes-video-2.5-flash': {label: 'Agnes Video 2.5 Flash', badge: '25F', tone: TONE_MID, desc: '720P · 4–12 秒 · 图片与音频参考'},
  'deepseek-flash-1017': {label: 'DeepSeek V4.1 Flash', badge: 'Text', tone: TONE_STRONG, desc: 'TeamRouter · OpenAI 兼容对话'},
  'cosyvoice-v3-5-flash': {label: 'CosyVoice v3.5 Flash', badge: 'TTS', tone: TONE_MID, desc: '阿里云百炼 · 音色设计与语音合成'},
};
const MODEL_FAVORITES_KEY = 'vimax.model.favorites';
const MODEL_RECENT_KEY = 'vimax.model.recent';

function readStoredStringArray(key: string): string[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed.filter((value): value is string => typeof value === 'string') : [];
  } catch {
    return [];
  }
}

function writeStoredStringArray(key: string, values: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify([...new Set(values)].slice(0, 30)));
  } catch {
    // localStorage is optional; model selection must still work without it.
  }
}

const LEGACY_MODEL_IDS: Record<string, string> = {
  'doubao-seedream-5-0-pro-260628': 'doubao-seedream-5-0-pro',
  'doubao-seedance-2-0-260128': 'doubao-seedance-2-0',
  'doubao-seedance-2-0-mini-260615': 'doubao-seedance-2-0-mini',
  'doubao-seedance-2-0-mini': 'doubao-seedance-2-0-mini',
  'doubao-seedance-2-0-fast-260128': 'doubao-seedance-2-0-fast',
  'doubao-seedance-2-5-260628': 'doubao-seedance-2-5',
  'cosyvoice-v3.5-flash': 'cosyvoice-v3-5-flash',
};

function canonicalModelId(value?: string): string {
  const raw = String(value || '').trim();
  return LEGACY_MODEL_IDS[raw] || raw;
}

function fallbackModelSpecs(kind: CanvasMediaNodeKind): ModelSpec[] {
  const ids = kind === 'image' ? IMAGE_MODELS
    : kind === 'video' ? VIDEO_MODELS
      : kind === 'text' ? TEXT_MODELS
        : AUDIO_MODELS;
  return ids.map((id) => {
    const meta = MODEL_META[id] || {label: id, badge: 'M', desc: ''};
    return {
      id,
      providerId: kind === 'text' ? 'teamorouter' : kind === 'audio' ? 'aliyun_bailian' : 'ark',
      capability: kind,
      label: meta.label,
      remoteModel: id,
      adapter: kind === 'image' ? 'ark_image' : kind === 'video' ? 'ark_video' : kind === 'text' ? 'openai_chat' : 'dashscope_tts',
      description: meta.desc,
      badge: meta.badge,
      enabled: true,
      defaultParams: kind === 'image'
        ? {ratio: '1:1', clarity: '1.5K', size: '1536x1536', guidance: '中等'}
        : kind === 'video'
          ? {ratio: '9:16', resolution: '480P', duration: 5, guidance: '中等'}
          : kind === 'text'
            ? {maxTokens: 3000, temperature: 0.7}
            : {ttsMode: 'design', outputFormat: 'mp3', sampleRate: 24000, previewDuration: 4.5},
      params: {},
      limits: {},
    };
  });
}

function friendlyTaskError(raw?: string): string {
  const text = String(raw || '').trim();
  const lower = text.toLowerCase();
  if (!text) return '';
  const providerHttp = text.match(/供应商接口 HTTP (\d{3})[：:]\s*([\s\S]*)/i);
  if (providerHttp) {
    const status = Number(providerHttp[1]);
    const detail = providerHttp[2].replace(/\s+/g, ' ').trim();
    return `供应商返回 HTTP ${status}：${detail.slice(0, 500)}`;
  }
  if (lower.includes('没有返回任务号') && lower.includes('server_error')) {
    return `New API 中转未返回任务号，提交结果未知。请先核对供应商账单/任务记录，确认前不要重复提交。${text ? `详情：${text.slice(0, 220)}` : ''}`;
  }
  if (lower.includes('http 429') || lower.includes('rate limit') || lower.includes('too many') || text.includes('过于频繁')) {
    return '供应商限流。请等待 30–60 秒，系统会优先复用已有任务，不要连续重复提交。';
  }
  if (lower.includes('video_queue_full') || text.includes('视频队列已满') || lower.includes('queue_full')) {
    return `供应商视频队列已满，本次未创建任务。请等待 30–60 秒后再试。${text ? `详情：${text.slice(0, 180)}` : ''}`;
  }
  if (lower.includes('overdue') || lower.includes('insufficient') || lower.includes('balance') || lower.includes('quota') || text.includes('余额') || text.includes('欠费') || text.includes('额度')) {
    return '供应商账户余额或额度不足。请先充值或更换供应商，再重新生成。';
  }
  if (lower.includes('model_not_found') || lower.includes('no available channel') || text.includes('无可用通道')) {
    return `供应商没有该模型的可用通道，请检查模型是否启用或分组路由是否正确。${text ? `详情：${text.slice(0, 180)}` : ''}`;
  }
  if (lower.includes('reference') || text.includes('参考素材') || text.includes('参考图片') || text.includes('参考音频') || text.includes('参考视频')) {
    return `参考素材不符合当前模型要求，请检查数量、格式、大小和槽位顺序。${text ? `详情：${text.slice(0, 180)}` : ''}`;
  }
  if (lower.includes('timeout') || lower.includes('timed out') || text.includes('超时')) {
    return '供应商响应较慢，后台仍在继续处理。请到任务中心查看最终结果；不要连续重复提交，以免重复付费。';
  }
  if (lower.includes('invalid api key') || lower.includes('unauthorized') || lower.includes('forbidden') || text.includes('鉴权')) {
    return '供应商鉴权失败。请检查 API Key、工作空间和模型权限。';
  }
  return text.length > 260 ? `${text.slice(0, 260)}…` : text;
}

function mediaOutputReady(node?: CanvasMediaNode | null): boolean {
  if (!node) return false;
  if (node.kind === 'text') return Boolean(node.prompt.trim());
  if ((node.kind === 'image' || node.kind === 'audio') && node.promptStale) return false;
  return Boolean(String(node.mediaUrl || '').trim());
}

function mediaDependencyBlockers(node: CanvasMediaNode | null, pool: CanvasMediaNode[]) {
  if (!node) return [];
  const params = node.params || {};
  const refs = [
    ...(params.refs || []),
    ...voiceRefList(params),
  ];
  const byId = new Map(pool.map((item) => [item.id, item]));
  const seen = new Set<string>();
  const blockers: {nodeId: string; title: string; reason: string}[] = [];
  for (const ref of refs) {
    const nodeId = String(ref.nodeId || '');
    if (!nodeId || seen.has(nodeId)) continue;
    seen.add(nodeId);
    const source = byId.get(nodeId);
    if (!source) {
      blockers.push({nodeId, title: ref.label || nodeId, reason: '引用节点不存在'});
      continue;
    }
    if (mediaOutputReady(source)) continue;
    blockers.push({
      nodeId,
      title: source.title,
      reason: source.promptStale
        ? '源提示词已更新，需重新生成'
        : (source.kind === 'text' ? '文本为空' : '尚未生成'),
    });
  }
  return blockers;
}

// 提示词快捷模板（✦ 随机灵感 / ＋ 模板菜单）
const PROMPT_TEMPLATES: Partial<Record<CanvasMediaNodeKind, {label: string; text: string}[]>> = {
  image: [
    {label: '角色定妆', text: '，角色定妆正面半身像，纯色背景，柔和棚拍灯光，五官清晰，服装细节完整，电影级质感'},
    {label: '场景空镜', text: '，场景全景空镜，环境光自然，层次分明，电影级构图，细节丰富，无人物'},
    {label: '道具特写', text: '，道具特写，微距质感，柔光投射，材质纹理清晰，干净背景'},
  ],
  video: [
    {label: '固定镜头', text: '，固定机位，人物自然表演，稳定流畅，电影级光影，画面清晰'},
    {label: '推轨运镜', text: '，镜头缓慢推近主体，运动平稳，景深过渡自然，电影感光影'},
    {label: '手持跟拍', text: '，手持跟拍，轻微呼吸感晃动，跟随主体动作，现场感强'},
  ],
  audio: [
    {label: '沉稳旁白', text: '沉稳男声，语速缓慢，底纹平稳，中频厚实'},
    {label: '清亮对白', text: '清亮青年音，吐字清晰，语速适中，情绪自然'},
  ],
  // ★ 文本节点此前没有模板 → 弹窗右下角「随机灵感」按钮点了完全没反应（空数组直接 return）。
  //   补三条最常用的卡片骨架，按钮才有意义。
  text: [
    {label: '剧本梗概', text: '【本段梗概】\n人物：\n场景：\n冲突：\n落点：'},
    {label: '分镜说明', text: '【分镜说明】\n景别：\n机位：\n运镜：\n光影：\n时长：'},
    {label: '参数备注', text: '【参数备注】\n模型：\n画幅：\n影像基调：\n禁止项：'},
  ],
};

// 比例图标（下拉网格用）
const RatioIcon = ({r}: {r: string}) => {
  const m = /^(\d+):(\d+)$/.exec(r);
  if (!m) return <span className="jim-ratio-ico" />;
  const w = Number(m[1]); const h = Number(m[2]);
  const bw = w >= h ? 15 : Math.max(5, Math.round((15 * w) / h));
  const bh = h >= w ? 15 : Math.max(5, Math.round((15 * h) / w));
  return (
    <svg className="jim-ratio-ico" width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
      <rect x={(15 - bw) / 2} y={(15 - bh) / 2} width={bw} height={bh} rx="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
};

type PromptMentionToken = {label: string; url?: string; kind: CanvasMediaNodeKind};
type PromptMentionMatch = PromptMentionToken & {start: number; end: number; exact: boolean};

function escapePromptRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function promptMentionMatches(value: string, mentions: PromptMentionToken[]): PromptMentionMatch[] {
  const matches: PromptMentionMatch[] = [];
  for (const mention of mentions) {
    if (!mention.label) continue;
    const token = `@${mention.label}`;
    let from = 0;
    while (from < value.length) {
      const start = value.indexOf(token, from);
      if (start < 0) break;
      matches.push({...mention, start, end: start + token.length, exact: true});
      from = start + token.length;
    }
  }

  // 兼容旧导入数据里的「名称（图N）」写法。渲染时直接显示资产胶囊，
  // 下一次编辑保存会自然写回标准 @资产节点名。
  for (const mention of mentions) {
    const figure = /[（(]\s*(?:参考)?图\s*(\d+)\s*[）)]/.exec(mention.label);
    if (!figure) continue;
    const withoutPrefix = mention.label.replace(/^[^｜|]*[｜|]\s*/, '');
    const base = withoutPrefix.replace(/[（(]\s*(?:参考)?图\s*\d+\s*[）)]\s*$/, '').trim();
    const candidates = [...new Set([base, base.split(/[·・]/)[0]?.trim() || ''].filter(Boolean))];
    for (const candidate of candidates) {
      const pattern = new RegExp(
        `(?<![A-Za-z0-9_\\u3400-\\u9fff@｜|])${escapePromptRegExp(candidate)}`
        + `\\s*[（(]\\s*(?:参考)?图\\s*${figure[1]}\\s*[）)]`,
        'gu',
      );
      for (const match of value.matchAll(pattern)) {
        const start = match.index ?? 0;
        matches.push({...mention, start, end: start + match[0].length, exact: false});
      }
    }
  }

  // 标准 @资产名 优先，并且同一段文字只渲染一次胶囊。
  matches.sort((a, b) => a.start - b.start || Number(b.exact) - Number(a.exact) || b.end - a.end);
  const accepted: PromptMentionMatch[] = [];
  let consumed = -1;
  for (const match of matches) {
    if (match.start < consumed) continue;
    accepted.push(match);
    consumed = match.end;
  }
  return accepted;
}

function promptTextFromNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
  if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
    return [...node.childNodes].map(promptTextFromNode).join('');
  }
  if (node instanceof HTMLElement) {
    if (node.dataset.mention) return node.dataset.mention;
    if (node.tagName === 'BR') return '\n';
    return [...node.childNodes].map(promptTextFromNode).join('');
  }
  return '';
}

function promptTextFromEditor(editor: HTMLElement): string {
  return [...editor.childNodes].map(promptTextFromNode).join('').replace(/\u00a0/g, ' ');
}

function removePromptMention(prompt: string, label: string): string {
  if (!label) return prompt;
  return prompt
    .split(`@${label}`)
    .join('')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\s+$/g, '');
}

function promptCaretOffset(editor: HTMLElement): number {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || !editor.contains(selection.anchorNode)) {
    return promptTextFromEditor(editor).length;
  }
  const range = document.createRange();
  range.selectNodeContents(editor);
  try {
    range.setEnd(selection.anchorNode!, selection.anchorOffset);
  } catch {
    return promptTextFromEditor(editor).length;
  }
  return promptTextFromNode(range.cloneContents()).length;
}

function promptCaretFollowsAt(editor: HTMLElement, value: string): boolean {
  const offset = promptCaretOffset(editor);
  return offset > 0 && value.slice(Math.max(0, offset - 1), offset) === '@';
}

function promptSetCaret(editor: HTMLElement, offset: number): void {
  let remaining = Math.max(0, offset);
  let placed = false;
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();

  const walk = (parent: Node): void => {
    if (placed) return;
    if (parent.nodeType === Node.TEXT_NODE) {
      const length = (parent.textContent || '').length;
      if (remaining <= length) {
        range.setStart(parent, remaining);
        range.collapse(true);
        placed = true;
      } else {
        remaining -= length;
      }
      return;
    }
    if (parent instanceof HTMLElement && parent.dataset.mention) {
      const length = parent.dataset.mention.length;
      if (remaining <= length) {
        if (remaining <= 0) range.setStartBefore(parent);
        else range.setStartAfter(parent);
        range.collapse(true);
        placed = true;
      } else {
        remaining -= length;
      }
      return;
    }
    if (parent instanceof HTMLBRElement) {
      if (remaining <= 0) {
        range.setStartBefore(parent);
        range.collapse(true);
        placed = true;
      } else {
        remaining -= 1;
      }
      return;
    }
    for (const child of [...parent.childNodes]) walk(child);
  };

  walk(editor);
  if (!placed) {
    range.selectNodeContents(editor);
    range.collapse(false);
  }
  selection.removeAllRanges();
  selection.addRange(range);
}

function buildPromptEditorContent(editor: HTMLElement, value: string, mentions: PromptMentionToken[]): void {
  const matches = promptMentionMatches(value, mentions);
  const root = document.createDocumentFragment();
  let cursor = 0;
  for (const found of matches) {
    if (found.start > cursor) root.append(document.createTextNode(value.slice(cursor, found.start)));
    const chip = document.createElement('span');
    chip.className = `jim-prompt-mention is-${found.kind}`;
    chip.contentEditable = 'false';
    chip.dataset.mention = `@${found.label}`;
    chip.title = found.label;
    if ((found.kind === 'image' || found.kind === 'audio') && found.url) {
      chip.classList.add('has-preview');
      chip.dataset.previewUrl = found.url;
      const image = document.createElement('img');
      image.src = found.url;
      image.alt = found.label;
      chip.append(image);
    } else {
      const glyph = document.createElement('i');
      glyph.textContent = found.kind === 'audio' ? '声' : found.kind === 'video' ? '影' : found.kind === 'text' ? '文' : '图';
      chip.append(glyph);
    }
    const label = document.createElement('b');
    label.textContent = found.label;
    chip.append(label);
    root.append(chip);
    cursor = found.end;
  }
  if (cursor < value.length) root.append(document.createTextNode(value.slice(cursor)));
  if (value.length === 0) root.append(document.createTextNode(''));
  editor.replaceChildren(root);
  editor.dataset.promptValue = value;
}

function RichPromptEditor({
  editorRef,
  value,
  mentions,
  placeholder,
  onChange,
  onKeyDown,
  onRendered,
  onMentionPreview,
  onMentionRemoved,
}: {
  editorRef: RefObject<HTMLDivElement>;
  value: string;
  mentions: PromptMentionToken[];
  placeholder: string;
  onChange: (value: string) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
  onRendered?: () => void;
  onMentionPreview?: (preview: ({url: string; label: string} & PreviewPlacement) | null) => void;
  onMentionRemoved?: (labels: string[]) => void;
}) {
  const mentionSignature = mentions.map((item) => `${item.kind}:${item.label}:${item.url || ''}`).join('|');
  const lastEditorValueRef = useRef('');
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const active = document.activeElement === editor;
    const offset = active ? promptCaretOffset(editor) : null;
    const current = promptTextFromEditor(editor);
    const currentMentions = [...editor.querySelectorAll<HTMLElement>('[data-mention]')]
      .map((item) => item.dataset.mention || '')
      .join('|');
    const wantedMentions = promptMentionMatches(value, mentions)
      .map((item) => `@${item.label}`)
      .join('|');
    const mentionsChanged = currentMentions !== wantedMentions;
    if (active && !mentionsChanged && value === lastEditorValueRef.current) return;
    if (!active && current === value && !mentionsChanged) return;
    buildPromptEditorContent(editor, value, mentions);
    lastEditorValueRef.current = value;
    if (active && offset !== null) promptSetCaret(editor, Math.min(offset, value.length));
    onRendered?.();
  }, [editorRef, mentionSignature, value, mentions]);

  return (
    <div
      ref={editorRef}
      className="jim-prompt"
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      role="textbox"
      aria-multiline="true"
      aria-label={placeholder}
      data-placeholder={placeholder}
      onInput={(event) => {
        const next = promptTextFromEditor(event.currentTarget);
        const previous = lastEditorValueRef.current;
        if (next.trim() && next.length < previous.length && onMentionRemoved) {
          const removed = promptMentionMatches(previous, mentions)
            .filter((mention) => !next.includes(`@${mention.label}`))
            .map((mention) => mention.label);
          if (removed.length) onMentionRemoved(removed);
        }
        event.currentTarget.dataset.promptValue = next;
        lastEditorValueRef.current = next;
        onChange(next);
      }}
      onBlur={(event) => {
        const next = promptTextFromEditor(event.currentTarget);
        event.currentTarget.dataset.promptValue = next;
        lastEditorValueRef.current = next;
        onChange(next);
      }}
      onKeyDown={(event) => {
        onKeyDown(event);
        if (event.defaultPrevented) return;
        if (event.key === 'Enter' && !event.ctrlKey && !event.metaKey) {
          event.preventDefault();
          const selection = window.getSelection();
          if (!selection || selection.rangeCount === 0) return;
          const range = selection.getRangeAt(0);
          range.deleteContents();
          const lineBreak = document.createTextNode('\n');
          range.insertNode(lineBreak);
          range.setStartAfter(lineBreak);
          range.collapse(true);
          selection.removeAllRanges();
          selection.addRange(range);
          const next = promptTextFromEditor(event.currentTarget);
          event.currentTarget.dataset.promptValue = next;
          lastEditorValueRef.current = next;
          onChange(next);
        }
      }}
      onPaste={(event) => {
        event.preventDefault();
        const text = event.clipboardData.getData('text/plain');
        document.execCommand('insertText', false, text);
        const next = promptTextFromEditor(event.currentTarget);
        event.currentTarget.dataset.promptValue = next;
        lastEditorValueRef.current = next;
        onChange(next);
      }}
      onPointerOver={(event) => {
        const chip = (event.target as Element | null)?.closest<HTMLElement>('.jim-prompt-mention.has-preview');
        const url = chip?.dataset.previewUrl;
        if (!chip || !url || !onMentionPreview) return;
        const panel = chip.closest<HTMLElement>('.jim-bar');
        onMentionPreview({
          url,
          label: chip.title || chip.dataset.mention || '',
          ...previewPlacement(
            chip.getBoundingClientRect(),
            'center',
            panel?.getBoundingClientRect(),
          ),
        });
      }}
      onPointerOut={(event) => {
        const chip = (event.target as Element | null)?.closest('.jim-prompt-mention');
        const next = event.relatedTarget as Node | null;
        if (chip && (!next || !chip.contains(next))) onMentionPreview?.(null);
      }}
      onMouseLeave={() => onMentionPreview?.(null)}
      onScroll={() => onMentionPreview?.(null)}
    />
  );
}

function localFileKind(file: File): CanvasMediaNodeKind | null {
  const mime = String(file.type || '').toLowerCase();
  if (mime.startsWith('image/')) return 'image';
  if (mime.startsWith('audio/')) return 'audio';
  if (mime.startsWith('video/')) return 'video';
  const ext = file.name.split('.').pop()?.toLowerCase() || '';
  if (['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'avif'].includes(ext)) return 'image';
  if (['mp3', 'wav', 'ogg', 'm4a', 'flac', 'aac'].includes(ext)) return 'audio';
  if (['mp4', 'webm', 'mov'].includes(ext)) return 'video';
  return null;
}

function nearestCanvasRatio(width: number, height: number): string {
  if (!(width > 0) || !(height > 0)) return '';
  const actual = width / height;
  const options = ['1:1', '16:9', '9:16', '4:3', '3:4', '21:9'];
  return options.reduce((best, value) => {
    const [w, h] = value.split(':').map(Number);
    const target = w / h;
    const [bestW, bestH] = best.split(':').map(Number);
    return Math.abs(Math.log(actual / target)) < Math.abs(Math.log(actual / (bestW / bestH)))
      ? value
      : best;
  }, '1:1');
}

async function inspectLocalMedia(file: File): Promise<{
  width?: number;
  height?: number;
  duration?: number;
  ratio?: string;
}> {
  const kind = localFileKind(file);
  const objectUrl = URL.createObjectURL(file);
  try {
    if (kind === 'image') {
      if (typeof createImageBitmap === 'function') {
        try {
          const bitmap = await createImageBitmap(file);
          const result = {
            width: bitmap.width,
            height: bitmap.height,
            ratio: nearestCanvasRatio(bitmap.width, bitmap.height),
          };
          bitmap.close();
          return result;
        } catch {
          // Fall through to the <img> decoder for older WebViews.
        }
      }
      return await new Promise((resolve) => {
        const image = new Image();
        image.onload = () => resolve({
          width: image.naturalWidth,
          height: image.naturalHeight,
          ratio: nearestCanvasRatio(image.naturalWidth, image.naturalHeight),
        });
        image.onerror = () => resolve({});
        image.src = objectUrl;
      });
    }
    if (kind === 'video' || kind === 'audio') {
      return await new Promise((resolve) => {
        const media = document.createElement(kind);
        const finish = () => {
          const width = kind === 'video' ? (media as HTMLVideoElement).videoWidth : undefined;
          const height = kind === 'video' ? (media as HTMLVideoElement).videoHeight : undefined;
          resolve({
            width,
            height,
            duration: Number.isFinite(media.duration) ? media.duration : undefined,
            ratio: width && height ? nearestCanvasRatio(width, height) : undefined,
          });
        };
        const timer = window.setTimeout(() => resolve({}), 1800);
        media.onloadedmetadata = () => {
          window.clearTimeout(timer);
          finish();
        };
        media.onerror = () => {
          window.clearTimeout(timer);
          resolve({});
        };
        media.preload = 'metadata';
        media.src = objectUrl;
      });
    }
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
  return {};
}

function MediaNode({data, selected}: NodeProps<FlowNode<MediaNodeData>>) {
  const {node, renderMode = 'full', onChange, onGenerate, onDelete, onEdit, onZoom, onReadText, onCopyText, assets, catalog, active, generationBusy, groupMembers, onSelectGroup, onPressGroup, onUploadLocal, suppressNodeOpen, isNodeOpenSuppressed} = data;
  const [menu, setMenu] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const videoShellRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const [videoPlaying, setVideoPlaying] = useState(false);
  const [videoDuration, setVideoDuration] = useState(0);
  const [videoCurrentTime, setVideoCurrentTime] = useState(0);
  const [videoMuted, setVideoMuted] = useState(false);
  const [videoVolume, setVideoVolume] = useState(1);
  const [videoLoop, setVideoLoop] = useState(false);
  const [audioPlaying, setAudioPlaying] = useState(false);
  const [audioDuration, setAudioDuration] = useState(0);
  const [audioCurrentTime, setAudioCurrentTime] = useState(0);
  const [audioWaveBars, setAudioWaveBars] = useState(AUDIO_WAVE_BARS);
  const [busyClock, setBusyClock] = useState(Date.now());
  const localFileInputRef = useRef<HTMLInputElement>(null);
  const localUploadAbortRef = useRef<AbortController | null>(null);
  const localUploadFileRef = useRef<File | null>(null);
  const [localUploading, setLocalUploading] = useState(false);
  const [localUploadPercent, setLocalUploadPercent] = useState(0);
  const [localUploadError, setLocalUploadError] = useState('');
  const [localDragOver, setLocalDragOver] = useState(false);
  const meta = MEDIA_KIND_META[node.kind];
  const isText = node.kind === 'text';
  const localAccept = node.kind === 'image'
    ? 'image/*,.png,.jpg,.jpeg,.webp,.gif,.bmp,.avif'
    : node.kind === 'audio'
      ? 'audio/*,.mp3,.wav,.ogg,.m4a,.flac,.aac'
      : node.kind === 'video'
        ? 'video/*,.mp4,.webm,.mov'
        : '';
  const uploadLocalFile = async (file: File) => {
    const fileKind = localFileKind(file);
    if (fileKind !== node.kind) {
      localUploadFileRef.current = null;
      const expected = node.kind === 'image' ? '图片' : node.kind === 'audio' ? '音频' : '视频';
      setLocalUploadError(`请选择${expected}文件`);
      return;
    }
    localUploadFileRef.current = file;
    localUploadAbortRef.current?.abort();
    const controller = new AbortController();
    localUploadAbortRef.current = controller;
    setLocalUploading(true);
    setLocalUploadPercent(0);
    setLocalUploadError('');
    try {
      await onUploadLocal(node, file, {
        signal: controller.signal,
        onProgress: setLocalUploadPercent,
      });
      setLocalUploadPercent(100);
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setLocalUploadError(error instanceof Error ? error.message : '上传失败');
      }
    } finally {
      if (localUploadAbortRef.current === controller) {
        localUploadAbortRef.current = null;
        setLocalUploading(false);
      }
    }
  };
  const openLocalPicker = () => {
    suppressNodeOpen();
    cancelOpen();
    setLocalUploadError('');
    localFileInputRef.current?.click();
  };
  const handleLocalDrop = (event: React.DragEvent) => {
    if (isText) return;
    const file = Array.from(event.dataTransfer.files || [])[0];
    if (!file) return;
    event.preventDefault();
    event.stopPropagation();
    setLocalDragOver(false);
    void uploadLocalFile(file);
  };
  const dependencyLocateCursorRef = useRef(0);
  const dependencyPool = [node, ...(assets || [])];
  const dependencyBlockers = mediaDependencyBlockers(node, dependencyPool);
  const dependencyIds = [...new Set([
    ...(node.params?.refs || []).map((item) => item.nodeId),
    ...voiceRefList(node.params).map((item) => item.nodeId),
  ])].filter(Boolean);
  const dependencySources = dependencyIds
    .map((id) => dependencyPool.find((item) => item.id === id))
    .filter((item): item is CanvasMediaNode => !!item);
  const dependencyImageCount = dependencySources.filter((item) => item.kind === 'image').length;
  const dependencyAudioCount = dependencySources.filter((item) => item.kind === 'audio').length;
  const dependencyTextCount = dependencySources.filter((item) => item.kind === 'text').length;
  const dependencyLabel = [
    dependencyImageCount > 0 ? `${dependencyImageCount}图` : '',
    dependencyAudioCount > 0 ? `${dependencyAudioCount}音色` : '',
    dependencyTextCount > 0 ? `${dependencyTextCount}文本` : '',
  ].filter(Boolean).join(' + ');
  const assetState = node.kind === 'image' || node.kind === 'audio'
    ? node.promptStale
      ? {label: '待更新', className: 'is-stale', title: node.promptStaleReason || '源提示词已更新，当前素材仍是旧版本'}
      : !mediaOutputReady(node)
        ? {label: '待生成', className: 'is-pending', title: '当前资产尚未生成'}
        : (node.reusedAsset || (node.sharedEpisodeCount || 1) > 1)
          ? {label: '跨集复用', className: 'is-shared', title: node.reusedAsset ? '复用已有跨集资产' : `此资产被 ${node.sharedEpisodeCount} 集引用`}
          : {label: '本集新建', className: 'is-new', title: '当前集新建资产'}
    : null;
  // ★ 端口是否"已连过线"：用来让端口默认点亮，告诉用户这张卡已经接入了素材。
  //   refs / voiceRefs / refIds 任一非空即算 —— 三者分别对应图片引用、音色引用、派生边。
  const hasLink = !!((node.params?.refs?.length) || (node.params?.voiceRefs?.length)
    || node.params?.voiceRef || (node.refIds?.length));
  const {getZoom, setCenter} = useReactFlow();
  // 文本节点：单击选中（并延迟打开弹窗）、双击进入行内编辑（对齐即梦 / Figma 的交互）
  const [editingText, setEditingText] = useState(false);
  const textBodyRef = useRef<HTMLTextAreaElement>(null);
  // 右下角拖拽缩放：过程中先用本地尺寸（避免每帧写库 + 整树重算），松手才提交
  const [dragSize, setDragSize] = useState<{w: number; h: number} | null>(null);
  const resizeRef = useRef<{sx: number; sy: number; w: number; h: number; zoom: number; cur: {w: number; h: number}} | null>(null);  // 单击延迟打开弹窗：留一个双击窗口，双击进入编辑就取消（否则双击会先弹窗）
  // 单击延迟打开弹窗：留一个双击窗口，双击进入编辑就取消（否则双击会先弹窗）
  const clickTimer = useRef<number | null>(null);
  // （编组双击判定已移除：组内卡片单击即单卡 + 弹窗，批量选择改由组框承担）
  useEffect(() => () => {
    if (clickTimer.current) window.clearTimeout(clickTimer.current);
  }, []);
  const scheduleOpen = () => {
    if (isNodeOpenSuppressed()) return;
    if (clickTimer.current) window.clearTimeout(clickTimer.current);
    clickTimer.current = window.setTimeout(() => {
      clickTimer.current = null;
      if (!isNodeOpenSuppressed()) onEdit(node);
    }, 240);
  };
  const cancelOpen = () => {
    if (clickTimer.current) { window.clearTimeout(clickTimer.current); clickTimer.current = null; }
  };
  useEffect(() => {
    setVideoPlaying(false);
    setVideoDuration(0);
    setVideoCurrentTime(0);
    setAudioPlaying(false);
    setAudioDuration(0);
    setAudioCurrentTime(0);
    setAudioWaveBars(AUDIO_WAVE_BARS);
  }, [node.mediaUrl]);
  useEffect(() => {
    if (node.kind !== 'audio' || !node.mediaUrl || node.mediaUrl.startsWith('data:')) return;
    if (node.source?.type === 'local' && Number(node.source.size || 0) > 16 * 1024 * 1024) {
      setAudioWaveBars(AUDIO_WAVE_BARS);
      return;
    }
    let cancelled = false;
    let context: AudioContext | null = null;
    const controller = new AbortController();
    const closeContext = () => {
      const current = context;
      context = null;
      if (!current || current.state === 'closed') return;
      void current.close().catch(() => {
        // Older WebViews may transition to closed between the state check and close().
      });
    };
    void fetch(node.mediaUrl, {credentials: 'include', cache: 'force-cache', signal: controller.signal})
      .then((response) => response.arrayBuffer())
      .then(async (buffer) => {
        const AudioContextCtor = window.AudioContext || (window as unknown as {webkitAudioContext?: typeof AudioContext}).webkitAudioContext;
        if (!AudioContextCtor || cancelled) return;
        context = new AudioContextCtor();
        const decoded = await context.decodeAudioData(buffer.slice(0));
        if (cancelled) return;
        const samples = decoded.getChannelData(0);
        const bars = AUDIO_WAVE_BARS.length;
        const block = Math.max(1, Math.floor(samples.length / bars));
        const peaks = Array.from({length: bars}, (_, index) => {
          let peak = 0;
          const start = index * block;
          const end = Math.min(samples.length, start + block);
          for (let i = start; i < end; i += Math.max(1, Math.floor(block / 80))) {
            peak = Math.max(peak, Math.abs(samples[i] || 0));
          }
          return peak;
        });
        const maxPeak = Math.max(0.001, ...peaks);
        setAudioWaveBars(peaks.map((peak) => Math.round(18 + (peak / maxPeak) * 82)));
      })
      .catch(() => {
        // Cross-origin or unsupported codecs keep the deterministic fallback bars.
      })
      .finally(closeContext);
    return () => {
      cancelled = true;
      controller.abort();
      closeContext();
    };
  }, [node.kind, node.mediaUrl]);
  const toggleVideoPlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => setVideoPlaying(false));
    } else {
      video.pause();
    }
  };
  const seekVideo = (value: number) => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
    video.currentTime = Math.max(0, Math.min(video.duration, value));
    setVideoCurrentTime(video.currentTime);
  };
  const setVideoAudioVolume = (value: number) => {
    const volume = Math.max(0, Math.min(1, value));
    setVideoVolume(volume);
    setVideoMuted(volume === 0);
    if (videoRef.current) {
      videoRef.current.volume = volume;
      videoRef.current.muted = volume === 0;
    }
  };
  const toggleVideoFullscreen = () => {
    const shell = videoShellRef.current;
    if (!shell) return;
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void shell.requestFullscreen?.();
    }
  };
  const videoProgressPercent = videoDuration > 0
    ? Math.max(0, Math.min(100, (videoCurrentTime / videoDuration) * 100))
    : 0;
  const formatVideoTime = (value: number) => {
    if (!Number.isFinite(value) || value <= 0) return '0:00';
    const total = Math.floor(value);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  };
  const toggleAudioPlayback = () => {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      void audio.play().catch(() => setAudioPlaying(false));
    } else {
      audio.pause();
    }
  };
  const seekAudio = (value: number) => {
    const audio = audioRef.current;
    if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0) return;
    audio.currentTime = Math.max(0, Math.min(audio.duration, value));
    setAudioCurrentTime(audio.currentTime);
  };
  const audioProgressPercent = audioDuration > 0
    ? Math.max(0, Math.min(100, (audioCurrentTime / audioDuration) * 100))
    : 0;
  // ★ 卡片右下角「更多」菜单：点外面任意地方 / 按 Esc 都要收起。
  //   旧代码**完全没有**这层监听 —— 菜单一旦展开，除了再点一次那个「⋯」按钮，
  //   点画布、点别的卡、拖别的卡它都赖着不走（用户"很多地方都一样要点击空白关闭"）。
  //   注意：卡片根节点上的 onPointerDown 会做整组预备选中，这里必须是捕获阶段，
  //   且判定用「目标是否还在菜单容器内」，避免菜单自己的按钮点击先被关掉。
  useEffect(() => {
    if (!menu) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (!t.closest('.canvas-media-foot-more')) setMenu(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); setMenu(false); }
    };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [menu]);
  // 缩放拖动：滚轮/缩放系数必须换算（节点尺寸是画布坐标，鼠标位移是屏幕像素）
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const cur = size;
    resizeRef.current = {sx: e.clientX, sy: e.clientY, w: cur.width, h: cur.height, zoom: getZoom() || 1, cur: {w: cur.width, h: cur.height}};
    setDragSize({w: cur.width, h: cur.height});
    const onMove = (ev: PointerEvent) => {
      const st = resizeRef.current;
      if (!st) return;
      const z = st.zoom || 1;
      // 右下角：向右下拖 = 变大；限定 140×90 起、1600×1200 止
      const w = Math.round(Math.min(1600, Math.max(140, st.w + (ev.clientX - st.sx) / z)));
      const h = Math.round(Math.min(1200, Math.max(90, st.h + (ev.clientY - st.sy) / z)));
      st.cur = {w, h};
      setDragSize({w, h});
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      const st = resizeRef.current;
      resizeRef.current = null;
      setDragSize(null);
      if (st) onChange(node.id, {params: {...(node.params || {}), textW: st.cur.w, textH: st.cur.h}});
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
  useEffect(() => {
    if (!editingText) return;
    const el = textBodyRef.current;
    if (!el) return;
    el.focus();
    // 光标落到末尾，避免双击时浏览器自己选中一个词（会出现蓝色选中块）
    const end = el.value.length;
    el.setSelectionRange(end, end);
  }, [editingText]);
  // 外部切换节点（弹窗打开别的卡等）时退出编辑态，免得残留聚焦
  useEffect(() => { if (!isText) setEditingText(false); }, [isText]);
  // finalizePending：供应商已经出结果、后端还在落盘归档。此时遮罩会把画面挡住，
  // 而用户要看的正是这张画面，所以只要已有地址就不再盖一层「生成中」。
  const liveProviderGeneration = isLiveProviderGeneration(node);
  const busy = (node.status === 'generating' || liveProviderGeneration || !!generationBusy)
    && !(node.finalizePending && !!node.mediaUrl);
  const done = node.status === 'done' && !liveProviderGeneration;
  const failed = node.status === 'failed'
    && !liveProviderGeneration
    && !node.mediaUrl;
  useEffect(() => {
    if (!busy) return;
    setBusyClock(Date.now());
    const timer = window.setInterval(() => setBusyClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [busy, node.generationStartedAt]);
  const generationStartedMs = Date.parse(node.generationStartedAt || '');
  const generationElapsedSeconds = Number.isFinite(generationStartedMs)
    ? Math.max(0, Math.floor((busyClock - generationStartedMs) / 1000))
    : 0;
  const generationElapsedLabel = `${Math.floor(generationElapsedSeconds / 60)}:${String(generationElapsedSeconds % 60).padStart(2, '0')}`;
  const providerProgress = Number(node.providerTaskProgress);
  const hasProviderProgress = hasTrustedProviderProgress(
    node.providerTaskStatus,
    providerProgress,
    node.providerTaskProgressChangedAt,
  );
  const providerTaskPhase = node.providerTaskPhase
    || '生成中';
  const visibleProviderTaskPhase = liveProviderGeneration ? '生成中' : providerTaskPhase;
  const busyLabel = generationProgressLabel(
    visibleProviderTaskPhase,
    node.providerTaskStatus,
    providerProgress,
    node.providerTaskProgressChangedAt,
  );
  const p = node.params || {};
  const mediaVersions = mediaVersionList(node);
  const matchedMediaVersionIndex = node.mediaUrl
    ? mediaVersions.findIndex((version) => version.url === node.mediaUrl)
    : -1;
  const currentMediaVersionIndex = matchedMediaVersionIndex >= 0
    ? matchedMediaVersionIndex
    : Math.min(
        Math.max(0, Number(p.mediaVersionIndex ?? p.videoVersionIndex ?? mediaVersions.length - 1)),
        Math.max(0, mediaVersions.length - 1),
      );
  const selectMediaVersion = (nextIndex: number) => {
    if (mediaVersions.length < 2) return;
    const normalized = (nextIndex + mediaVersions.length) % mediaVersions.length;
    const version = mediaVersions[normalized];
    if (!version?.url) return;
    videoRef.current?.pause();
    audioRef.current?.pause();
    onChange(
      node.id,
      {
        mediaUrl: version.url,
        actualDuration: version.actualDuration ?? node.actualDuration,
        source: version.sourceType === 'local'
          ? {
              type: 'local',
              fileName: version.fileName,
              mime: version.mime,
              size: version.size,
              sha256: version.sha256,
              width: version.width,
              height: version.height,
              uploadedAt: version.generatedAt,
            }
          : version.sourceType === 'generated'
            ? {...(node.source || {}), type: 'generated'}
            : node.source,
        params: {
          ...p,
          mediaVersionIndex: normalized,
          ...(node.kind === 'video' ? {videoVersionIndex: normalized} : {}),
        },
      },
      {mediaVersionSelection: true},
    );
  };
  const cardModel = catalog?.models.find((item) =>
    item.enabled !== false
    && item.capability === node.kind
    && (item.id === p.model || item.remoteModel === p.model || item.id === p.ttsModel || item.remoteModel === p.ttsModel)
  ) || catalog?.models.find((item) => item.enabled !== false && item.capability === node.kind);
  const cardPromptLimit = Math.max(1, Number(cardModel?.limits?.maxPromptChars || 4000));
  // 卡片尺寸 = 输入框里选的画面比例（等比缩放进"最大框"），改比例卡片即时变形：
  // 16:9 → 宽扁 / 9:16 → 竖高 / 21:9 → 超宽扁，长边统一到该类型的最大框，短边等比。
  // 不用 aspectRatio（React style 序列化不可靠），直接算 px 宽高写在 style 上。
  const CARD_BOX: Record<CanvasMediaNodeKind, {w: number; h: number}> = {
    text: {w: 200, h: 120}, image: {w: 280, h: 300}, audio: {w: 220, h: 140}, video: {w: 230, h: 320},
  };
  const box = CARD_BOX[node.kind];
  const rm = /(\d+)\s*:\s*(\d+)/.exec((node.kind === 'image' || node.kind === 'video')
    ? (p.ratio || (node.kind === 'image' ? '1:1' : '9:16'))
    : '') || null;
  const cardSize = rm && Number(rm[2]) > 0
    ? (() => {
      const rw = Number(rm[1]); const rh = Number(rm[2]);
      const s = Math.min(box.w / rw, box.h / rh);
      return {width: Math.ceil(rw * s), height: Math.ceil(rh * s)};
    })()
    : {width: box.w, height: box.h};
  // 文本节点：尺寸由 params.textW/textH 决定（曾拖过右下角），拖拽中优先用本地临时值
  const textSize = {width: p.textW || cardSize.width, height: p.textH || cardSize.height};
  const size = isText ? (dragSize ? {width: dragSize.w, height: dragSize.h} : textSize) : cardSize;
  const cardStyle = {width: size.width, height: size.height};
  // ★★ 节点宽度必须等于卡片的**真实外框宽度**（第 17 轮修，两处都踩过）。
  //   为什么：连线端口是绝对定位挂在**节点容器**上的（left/right: 0）。
  //   端口圆心 = 容器边缘；若卡片比容器宽，端口圆心就会陷进卡片内侧，
  //   视觉上是「连线起点埋进卡片里」，拖线时也抓不准。
  //   ★ 实测（getComputedStyle + getBoundingClientRect / zoom）：
  //     `.canvas-media-card`          content-box，padding 5px，border 1px
  //                                   → style 写 280，真实外框 292，比容器宽 12
  //     `.is-text-card`               content-box，padding 14px 16px，border 1px
  //                                   → 水平方向 16*2 + 1*2 = 34
  //   所以按盒模型**分类**补偿：普通卡 +12，文本卡 +34。两处都补到 0 溢出。
  //   ★ 2026-10-25：文本卡内边距从 10/12 加到 14/16（去掉边框后靠留白撑质感），
  //     所以这里同步从 26 → 34。**改 CSS padding 必须回来改这个数**，
  //     否则端口圆心会陷进卡片 8px 内，视觉上连线起点埋进卡片。
  const cardBoxExtra = isText ? 34 : 12; // 文本卡 padding 16*2+边框2；普通卡 padding 5*2+边框2

  const nodeW = Math.max(size.width + cardBoxExtra, 170);
  // 即梦风卡片：标题在上、图在中间、状态/参数信息条在下（⋯ 菜单收拢编辑/重生成/删除）
  const selectedStorage = mediaVersions[currentMediaVersionIndex]?.storage || node.storage;
  const storageMeta = node.kind === 'image' || node.kind === 'video'
    ? selectedStorage?.status === 'stored'
      ? ' · TOS原始'
      : node.storageError
        ? ' · TOS待转存'
        : ''
    : '';
  const localFileSize = node.source?.size
    ? node.source.size >= 1048576
      ? `${(node.source.size / 1048576).toFixed(1)}MB`
      : `${Math.max(1, Math.round(node.source.size / 1024))}KB`
    : '';
  const localFootMeta = node.kind === 'video' || node.kind === 'audio'
    ? `本地 · ${Number(node.actualDuration || 0).toFixed(1)}s${localFileSize ? ` · ${localFileSize}` : ''}`
    : `本地 · ${node.source?.width && node.source?.height ? `${node.source.width}×${node.source.height}` : p.ratio || '图片'}${localFileSize ? ` · ${localFileSize}` : ''}`;
  const footMeta = node.source?.type === 'local'
    ? localFootMeta
    : node.kind === 'image'
    ? `${p.ratio || '1:1'} · ${p.clarity || '1.5K'}${storageMeta}`
    : node.kind === 'video'
      ? `${p.ratio || '9:16'} · ${p.resolution || '480P'} · ${Number(node.actualDuration || p.duration || 5).toFixed(node.actualDuration ? 1 : 0)}s${storageMeta}${
          node.videoValidation?.status === 'valid'
            ? ' · 文件正常'
            : node.videoValidation?.status === 'warning' || node.videoValidation?.status === 'invalid'
              ? ` · ${node.videoValidation.message || '校验异常'}`
              : ''
        }`
      : node.kind === 'audio'
        ? (p.ttsMode === 'speech' ? `音色 ${p.voiceId || p.ttsVoice || '未设置'}` : '音色设计')
        : `${(node.prompt || '').trim().length} / ${cardPromptLimit} 字`;
  const footSubText = failed && node.lastError ? friendlyTaskError(node.lastError) : footMeta;
  // 文本节点现在也支持 AI 生成（用户要求），所以状态条同样要有「生成中/失败」语义；
  // 只有 idle 时显示「文本」而不是「待生成」—— 手写的文本本来就算完成。
  const statusText = localUploading
    ? `上传 ${localUploadPercent}%`
    : busy && node.providerTaskStatus === 'retrying'
      ? '查询重试中'
    : isText
    ? (busy ? '生成中' : failed ? '生成失败' : done ? '已生成' : '文本')
    : busy ? '生成中' : done ? '已生成' : failed ? '生成失败' : '待生成';
  const statusCls = localUploading || busy ? 'is-busy' : done ? 'is-done' : failed ? 'is-failed' : 'is-idle';
  if (renderMode === 'overview' && !active) {
    return (
      <div
        className={`canvas-node canvas-media-node canvas-media-overview-node ${statusCls}`}
        style={{width: nodeW}}
      >
        <Handle type="target" position={Position.Left} className={`canvas-media-port is-target${hasLink ? ' is-linked' : ''}`}>
          <svg className="canvas-port-icon" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M6 1.6V10.4M1.6 6H10.4" fill="none" stroke="currentColor"
              strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </Handle>
        <button
          type="button"
          className="canvas-media-overview-card"
          title={`${node.title || meta.label} · ${statusText}`}
          onClick={(event) => {
            if (event.shiftKey || event.ctrlKey || event.metaKey) return;
            event.stopPropagation();
            onEdit(node);
          }}
        >
          <KindIcon kind={node.kind} size={16} className="canvas-media-overview-icon" />
          <span className="canvas-media-overview-title">{node.title || meta.label}</span>
          {(assetState || node.source?.type === 'local') && (
            <span className="canvas-media-overview-badge">
              {assetState?.label || '本地'}
            </span>
          )}
          <i className="canvas-media-overview-status" />
        </button>
        <Handle type="source" position={Position.Right} className={`canvas-media-port is-source${hasLink ? ' is-linked' : ''}`}>
          <svg className="canvas-port-icon" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M6 1.6V10.4M1.6 6H10.4" fill="none" stroke="currentColor"
              strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </Handle>
      </div>
    );
  }
  return (
    <div
      className={`canvas-node canvas-media-node ${isText ? 'is-text-node' : ''} ${active ? 'is-active' : ''} ${statusCls}`}
      style={{width: nodeW}}
      // ★★ 编组「按组拖动」最关键的一步，且**必须挂在节点根元素上**（不能挂里面的 .canvas-media-card）：
      //   节点结构是 [节点根] > [标题栏 .canvas-media-head] + [图区 .canvas-media-card]，
      //   而用户拖动卡片时最自然的位置就是标题栏 —— 挂在图区上等于"拖标题栏时整组失效"。
      //
      //   为什么必须在 pointerdown（而不是 onClick）：
      //   拖拽由 d3-drag 接管，越过阈值时调 startDrag() → handleNodeClick(id)，
      //   此时若本卡在 store 里未被选中，会 addSelectedNodes([id]) 走非多选分支
      //   → 选中集合被收敛成只剩这一张，紧接着 getDragItems() 只收集被选中的节点
      //   → 结果就只拖动一张（实测：拖动开始 selected 从 2 掉到 1，同组另一张位移 0）。
      //   React 的 onClick 时序在 pointerdown/pointermove 之后，救不回来。
      onPointerDown={(e) => {
        // 右键 / 中键不是拖拽，别动选中态（否则右键菜单会把组打散成单选）
        if (e.button !== 0) return;
        // Shift/Ctrl/⌘ 是"逐张累加多选"，交给 React Flow 原生逻辑
        if (e.shiftKey || e.ctrlKey || e.metaKey) return;
        // ★★ 第 15 轮第 5 次修正（用户原话，语义最终定版）：
        //   「即梦的组框每个地方都可以拖动，相当于就是批量选择节点一样的道理，
        //     **单击节点就还是选中单个不影响单击弹窗**」
        //
        //   → 组内卡片的 pointerdown **不再**扩散成整组。上一版为了让"拖一张卡带走整组"
        //     在这里调 pressGroup(全组)，代价是"点一张卡就选中 3 张、弹窗还被顶掉"，
        //     与用户要的"单击=单个"直接冲突（实测 G6：点一张卡 selected 从 1 变 3、modal=false）。
        //
        //   现在「拖整组」的唯一入口是**组框**（框内任意空白 / 边带 / 组名标签，见下面
        //   document pointerdown 那段 → 命中框就拖全部成员）。卡片自己回到纯单卡行为：
        //   单击 = 选它一个 + 开它自己的提示词弹窗；拖动 = 只动它一个。
        //   这正好就是用户说的"相当于批量选择节点一样的道理" —— 组框承担批量，卡片保持个体。
      }}
    >
      {/* ★★ 连线端口（第 17 轮重做，对标用户截图里的「卡片外侧大圆 + ＋ 图标」）：
          用户原话「连线画面小的时候完全不好连线，要很精准才能连线到」。
          根因：React Flow 默认 handle 是 6×6px 的小圆点，且贴着卡片边框内侧，
          画布缩到 17%（本项目的常态）时屏幕上只有 ~1px，鼠标根本点不中。
          改法见 styles.css 的 .canvas-media-port：
            ① 视觉尺寸 22px、命中区 46px（用 ::before 透明外扩，不吃布局高度）；
            ② **常态就完全可见**（第 17 轮修：原先是常态半透明、hover 才显形，
               用户实感是"只有鼠标悬停时才显示连线端口"，等于每次连线都要先找一遍）；
            ③ 位置在卡片左右外侧，天然避开卡片内部点击区。 */}
      {/* ★★ 连线端口（第 18 轮定稿：SVG ＋ 图标 + 随缩放收缩的尺寸策略）：
          用户原话「连线画面小的时候完全不好连线」→ 需要端口在屏幕上够大、
          「节点中间的+号做精致的SVG，画布缩小后节点不要太大」→ 需要端口别喧宾夺主。
          两者的平衡点：**端口尺寸随卡片一起缩，但有下限**（见 styles.css --port-size）。
          端口宿主盒子恒 1px（React Flow 锚点依据，绝不能随 zoom 变），
          视觉圆 / 命中区 / ＋ 全部由 ::before / ::after 与本 SVG 承担。 */}
      <Handle type="target" position={Position.Left} className={`canvas-media-port is-target${hasLink ? ' is-linked' : ''}`}>
        <svg className="canvas-port-icon" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1.6V10.4M1.6 6H10.4" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </Handle>
      <div className="canvas-media-head" title={node.title}>
        <span className="canvas-media-head-name">{node.title || meta.label}</span>
        {assetState && (
          <span className={`canvas-media-reuse-badge ${assetState.className}`} title={assetState.title}>
            {assetState.label}
          </span>
        )}
        {node.source?.type === 'local' && (
          <span className="canvas-media-source-badge" title={node.source.fileName || '本地素材'}>
            本地
          </span>
        )}
        {node.kind === 'video' && dependencyLabel && (
          <button
            type="button"
            className={`canvas-media-dependency-chip nodrag ${dependencyBlockers.length ? 'is-blocked' : 'is-ready'}`}
            title={dependencyBlockers.length
              ? `点击定位缺失依赖：${dependencyBlockers.map((item) => item.title).join('、')}`
              : '全部参考依赖已就绪'}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              const targets = dependencyBlockers
                .map((item) => dependencyPool.find((source) => source.id === item.nodeId))
                .filter((item): item is CanvasMediaNode => !!item);
              if (!targets.length) return;
              const index = dependencyLocateCursorRef.current % targets.length;
              dependencyLocateCursorRef.current = (index + 1) % targets.length;
              const target = targets[index];
              setCenter(target.x + 120, target.y + 90, {
                zoom: Math.max(getZoom(), 0.65),
                duration: 420,
              });
            }}
          >
            <Link2 size={10} strokeWidth={2} aria-hidden="true" />
            {dependencyLabel}
            {dependencyBlockers.length > 0 ? ` · 缺${dependencyBlockers.length}项` : ' · 就绪'}
          </button>
        )}
      </div>
      <div
        className={`canvas-media-card${isText ? ' is-text-card' : ''}${isText && editingText ? ' is-text-editing' : ''}${localDragOver ? ' is-local-drop-target' : ''}`}
        style={cardStyle}
        onDragEnter={(event) => {
          if (isText || !Array.from(event.dataTransfer.types || []).includes('Files')) return;
          event.preventDefault();
          event.stopPropagation();
          setLocalDragOver(true);
        }}
        onDragOver={(event) => {
          if (isText || !Array.from(event.dataTransfer.types || []).includes('Files')) return;
          event.preventDefault();
          event.stopPropagation();
          event.dataTransfer.dropEffect = 'copy';
          if (!localDragOver) setLocalDragOver(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setLocalDragOver(false);
        }}
        onDrop={handleLocalDrop}
        // 点卡片：
        //   文本节点：单击 → 延迟开弹窗（双击则取消，改为进入行内编辑）
        //   其它节点：单击 → 直接打开参数弹窗
        //   按住 Shift/Ctrl/⌘ → 交给 React Flow 做多选：这里必须**不**拦截事件，
        //     否则 stopPropagation 会让卡片的 selected 永远不置位，
        //     多选/框选之外的"逐张累加选择"就彻底失效（浮出操作条也就无从谈起）。
        onClick={(e) => {
          if (e.shiftKey || e.ctrlKey || e.metaKey) return;
          e.stopPropagation();
          if (isNodeOpenSuppressed()) {
            cancelOpen();
            return;
          }
          if (node.kind === 'image' && node.mediaUrl && e.detail >= 2) {
            cancelOpen();
            onZoom(node.mediaUrl);
            return;
          }
          // ★★ 第 15 轮第 5 次修正：组内卡片**恢复单卡行为** —— 单击只选自己 + 打开自己的弹窗。
          //   用户原话：「单击节点就还是选中单个不影响单击弹窗」。
          //
          //   ★ 必须显式收敛选中集（onSelectGroup([node.id])）：
          //     组内多选状态下单击其中一张，React Flow 原生逻辑**不会**把多选收缩成单选
          //     （它认为"点在已选中的元素上"= 要拖整片），于是 selected 仍是整组，
          //     弹窗虽然会开，但"只选中这一张"不成立 —— 实测 G4 就是 sel=10 / modal=false。
          //     这里主动把选中集写成 [node.id]，即 Figma/即梦 的"单击=单选、拖框=批量"。
          if (groupMembers && groupMembers.length >= 2 && onSelectGroup) {
            onSelectGroup([node.id]);
          }
          if (isText || node.kind === 'image' || node.kind === 'video') {
            if (!editingText) scheduleOpen();
            return;
          }
          onEdit(node);
        }}
        onDoubleClick={(e) => {
          // 双击进入文本编辑；Shift/Ctrl 双击是"多选"，不抢
          if (e.shiftKey || e.ctrlKey || e.metaKey) return;
          e.stopPropagation();
          if (node.kind === 'image' && node.mediaUrl) {
            cancelOpen();
            onZoom(node.mediaUrl);
            return;
          }
          if (node.kind === 'video' && node.mediaUrl) {
            cancelOpen();
            toggleVideoFullscreen();
            return;
          }
          // 第 15 轮第 5 次修正后：组内卡片和普通卡片行为完全一致 ——
          // 双击就是"文本节点进入行内编辑"，不再有"组内双击才进单张"的特殊分支
          // （单击已经是单卡了，不需要双击这个绕行入口）。
          if (!isText) return;
          cancelOpen();
          setEditingText(true);
        }}
        title={
          isText
            ? (editingText ? '正在编辑，按 Esc 或点空白退出' : '单击编辑提示词 · 双击直接改文字 · 右下角拖动改大小')
            : node.kind === 'image'
              ? '单击编辑提示词 / 参数 · 双击放大查看原图'
            : '点击编辑：提示词 / 参数 / 生成'
        }
      >
        {isText ? (
          // 文本节点：平时只读展示（单击=选中，双击=编辑），编辑态才换成 textarea。
          // 正文存在 prompt 里，编辑即时 onChange 自动保存；不打开生成弹窗（红线：弹窗不动）
          editingText ? (
            <textarea
              ref={textBodyRef}
              className="canvas-text-body nodrag"
              value={node.prompt || ''}
              placeholder="输入文本内容…"
              maxLength={cardPromptLimit}
              onChange={(e) => onChange(node.id, {prompt: e.target.value.slice(0, cardPromptLimit)})}
              // blur 即退出编辑态：点空白 / 点别的节点都不会留下"一直可输入"的假象
              onBlur={() => setEditingText(false)}
              onKeyDown={(e) => {
                // Esc 退出编辑（不冒泡给画布的 Esc 清选中逻辑，由 keydown 自己处理）
                if (e.key === 'Escape') { e.stopPropagation(); setEditingText(false); }
              }}
              // 不能让 pointerdown 冒泡被 React Flow 当成拖动；nodrag 只管拖，
              // 这里再用 stopPropagation 保证双击选词/光标定位不被画布抢走。
              onPointerDown={(e) => { if (editingText) e.stopPropagation(); }}
              // 滚轮拦截，避免滚动长文本时误缩放画布
              onWheel={(e) => { e.stopPropagation(); }}
            />
          ) : (
            <div className={`canvas-text-view${(node.prompt || '').trim() ? '' : ' is-empty'}`}>
              {(node.prompt || '').trim() ? node.prompt : '双击输入文本…'}
            </div>
          )
        ) : node.mediaUrl ? (
          node.kind === 'image'
            // Image cards fill the content area; rounded clipping stays on the card.
            ? <img className="canvas-media-preview is-photo" src={node.mediaUrl} alt={node.title} loading="lazy" decoding="async" />
            : node.kind === 'video'
              ? <div
                  ref={videoShellRef}
                  className={`canvas-video-shell ${videoPlaying ? 'is-playing' : 'is-paused'}`}
                  onDoubleClick={(event) => {
                    event.stopPropagation();
                    cancelOpen();
                    toggleVideoFullscreen();
                  }}
                >
                  <video
                    ref={videoRef}
                    className="canvas-media-preview is-video nodrag nowheel"
                    src={node.mediaUrl}
                    playsInline
                    muted={videoMuted}
                    loop={videoLoop}
                    preload="metadata"
                    aria-label={node.title}
                    onLoadedMetadata={(event) => {
                      setVideoDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
                    }}
                    onDurationChange={(event) => {
                      setVideoDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
                    }}
                    onTimeUpdate={(event) => setVideoCurrentTime(event.currentTarget.currentTime)}
                    onPlay={() => setVideoPlaying(true)}
                    onPause={() => setVideoPlaying(false)}
                    onEnded={() => {
                      setVideoPlaying(false);
                      setVideoCurrentTime(0);
                      if (videoRef.current) videoRef.current.currentTime = 0;
                    }}
                    onWheel={(event) => event.stopPropagation()}
                  />
                  <button
                    type="button"
                    className="canvas-video-center-play nodrag"
                    title={videoPlaying ? '暂停预览（双击视频全屏）' : '播放预览（双击视频全屏）'}
                    aria-label={videoPlaying ? '暂停预览' : '播放预览'}
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleVideoPlayback();
                    }}
                    onPointerEnter={() => {
                      if (videoRef.current) videoRef.current.preload = 'auto';
                    }}
                    onPointerDown={(event) => event.stopPropagation()}
                  >
                    {videoPlaying
                      ? <Pause size={17} fill="currentColor" strokeWidth={1.6} aria-hidden="true" />
                      : <Play size={17} fill="currentColor" strokeWidth={1.6} aria-hidden="true" />}
                  </button>
                  <div
                    className="canvas-video-controls nodrag"
                    onClick={(event) => event.stopPropagation()}
                    onDoubleClick={(event) => event.stopPropagation()}
                    onPointerDown={(event) => event.stopPropagation()}
                  >
                    <input
                      className="canvas-video-progress"
                      type="range"
                      min={0}
                      max={videoDuration || 0}
                      step={0.01}
                      value={Math.min(videoCurrentTime, videoDuration || 0)}
                      aria-label="视频进度"
                      style={{'--video-progress': `${videoProgressPercent}%`} as CSSProperties}
                      onChange={(event) => seekVideo(Number(event.target.value))}
                    />
                    <div className="canvas-video-control-row">
                      <button
                        type="button"
                        className="canvas-video-toggle"
                        title={videoPlaying ? '暂停' : '播放'}
                        aria-label={videoPlaying ? '暂停' : '播放'}
                        onClick={toggleVideoPlayback}
                      >
                        {videoPlaying
                          ? <Pause size={11} fill="currentColor" strokeWidth={1.8} aria-hidden="true" />
                          : <Play size={11} fill="currentColor" strokeWidth={1.8} aria-hidden="true" />}
                      </button>
                      <span className="canvas-video-time">
                        {formatVideoTime(videoCurrentTime)} / {formatVideoTime(videoDuration)}
                      </span>
                      <span className="canvas-video-control-spacer" />
                      <div className="canvas-video-volume">
                        <button
                          type="button"
                          className="canvas-video-icon-btn"
                          title={videoMuted ? '取消静音' : '静音'}
                          aria-label={videoMuted ? '取消静音' : '静音'}
                          onClick={() => {
                            const next = !videoMuted;
                            setVideoMuted(next);
                            if (videoRef.current) videoRef.current.muted = next;
                          }}
                        >
                          {videoMuted ? <VolumeX size={10} /> : <Volume2 size={10} />}
                        </button>
                        <input
                          className="canvas-video-volume-range"
                          type="range"
                          min={0}
                          max={1}
                          step={0.05}
                          value={videoMuted ? 0 : videoVolume}
                          aria-label="视频音量"
                          onChange={(event) => setVideoAudioVolume(Number(event.target.value))}
                        />
                      </div>
                      <button
                        type="button"
                        className={`canvas-video-icon-btn ${videoLoop ? 'is-active' : ''}`}
                        title={videoLoop ? '关闭循环播放' : '循环播放'}
                        aria-label={videoLoop ? '关闭循环播放' : '循环播放'}
                        onClick={() => {
                          const next = !videoLoop;
                          setVideoLoop(next);
                          if (videoRef.current) videoRef.current.loop = next;
                        }}
                      >
                        <Repeat2 size={10} />
                      </button>
                      <button
                        type="button"
                        className="canvas-video-icon-btn"
                        title="全屏播放"
                        aria-label="全屏播放"
                        onClick={toggleVideoFullscreen}
                      >
                        <Maximize2 size={10} />
                      </button>
                    </div>
                  </div>
                </div>
              : (
                  <div className={`canvas-audio-shell ${audioPlaying ? 'is-playing' : ''}`}>
                    <audio
                      ref={audioRef}
                      className="canvas-audio-engine"
                      src={node.mediaUrl}
                      preload="metadata"
                      onLoadedMetadata={(event) => {
                        setAudioDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
                      }}
                      onDurationChange={(event) => {
                        setAudioDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0);
                      }}
                      onTimeUpdate={(event) => setAudioCurrentTime(event.currentTarget.currentTime)}
                      onPlay={() => setAudioPlaying(true)}
                      onPause={() => setAudioPlaying(false)}
                      onEnded={() => {
                        setAudioPlaying(false);
                        setAudioCurrentTime(0);
                        if (audioRef.current) audioRef.current.currentTime = 0;
                      }}
                    />
                    <div className="canvas-audio-head">
                      <span className="canvas-audio-note"><Music size={12} strokeWidth={1.9} aria-hidden="true" /></span>
                      <span className="canvas-audio-title">{node.title || '音频'}</span>
                    </div>
                    <div className="canvas-audio-wave" aria-hidden="true">
                      {audioWaveBars.map((height, index) => (
                        <i
                          key={index}
                          className={(index / Math.max(1, audioWaveBars.length - 1)) * 100 <= audioProgressPercent ? 'is-played' : ''}
                          style={{height: `${height}%`}}
                        />
                      ))}
                    </div>
                    <div
                      className="canvas-audio-controls nodrag"
                      onClick={(event) => event.stopPropagation()}
                      onDoubleClick={(event) => event.stopPropagation()}
                      onPointerDown={(event) => event.stopPropagation()}
                    >
                      <button
                        type="button"
                        title={audioPlaying ? '暂停' : '播放'}
                        aria-label={audioPlaying ? '暂停' : '播放'}
                        onClick={toggleAudioPlayback}
                      >
                        {audioPlaying
                          ? <Pause size={10} fill="currentColor" strokeWidth={1.8} aria-hidden="true" />
                          : <Play size={10} fill="currentColor" strokeWidth={1.8} aria-hidden="true" />}
                      </button>
                      <input
                        className="canvas-audio-progress"
                        type="range"
                        min={0}
                        max={audioDuration || 0}
                        step={0.01}
                        value={Math.min(audioCurrentTime, audioDuration || 0)}
                        aria-label="音频进度"
                        style={{'--audio-progress': `${audioProgressPercent}%`} as CSSProperties}
                        onChange={(event) => seekAudio(Number(event.target.value))}
                      />
                      <span>{formatVideoTime(audioCurrentTime)} / {formatVideoTime(audioDuration)}</span>
                    </div>
                  </div>
                )
        ) : (
          <div
            className="canvas-media-placeholder is-ai-ready"
            title="单击设置提示词并使用 AI 生成；右键或拖入文件可导入本地素材"
          >
            <KindIcon kind={node.kind} className="canvas-media-placeholder-ico" />
            <span className="canvas-media-placeholder-tip">点击设置提示词</span>
            <small>右键可导入本地素材</small>
          </div>
        )}
        {!isText && (
          <input
            ref={localFileInputRef}
            type="file"
            accept={localAccept}
            style={{display: 'none'}}
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) void uploadLocalFile(file);
            }}
          />
        )}
        {localDragOver && !isText && (
          <div className="canvas-local-drop-hint">
            <Upload size={18} strokeWidth={1.9} aria-hidden="true" />
            <span>{node.mediaUrl ? '松开以添加为新版本' : '松开以导入本地文件'}</span>
          </div>
        )}
        {(localUploading || localUploadError) && (
          <div className={`canvas-local-upload ${localUploadError ? 'is-error' : ''}`}>
            {localUploading ? (
              <>
                <span className="canvas-local-upload-head">
                  <b>{localUploadPercent >= 100 ? '正在保存素材' : `正在上传 ${localUploadPercent}%`}</b>
                  <button
                    type="button"
                    className="nodrag"
                    onClick={(event) => {
                      event.stopPropagation();
                      localUploadAbortRef.current?.abort();
                    }}
                  >
                    取消
                  </button>
                </span>
                <span className="canvas-local-upload-track">
                  <i style={{width: `${localUploadPercent}%`}} />
                </span>
              </>
            ) : (
              <>
                <span>{localUploadError}</span>
                {localUploadFileRef.current && (
                  <button
                    type="button"
                    className="nodrag"
                    onClick={(event) => {
                      event.stopPropagation();
                      const file = localUploadFileRef.current;
                      if (file) void uploadLocalFile(file);
                    }}
                  >
                    重试
                  </button>
                )}
              </>
            )}
          </div>
        )}
        {busy && (
          <div className="canvas-media-busy-mask">
            <span className="canvas-media-spin is-lg" />
            <span>{busyLabel}</span>
            {generationElapsedSeconds > 0 && (
              <small>已等待 {generationElapsedLabel}</small>
            )}
          </div>
        )}
        <div className="canvas-media-card-menu">
          {isText && (
            <>
              <button type="button" className="nodrag" title="展开全文" onClick={(e) => { e.stopPropagation(); onReadText(node); }}>
                <Maximize2 size={13} strokeWidth={1.9} aria-hidden="true" />
              </button>
              <button type="button" className="nodrag" title="复制全文" onClick={(e) => { e.stopPropagation(); onCopyText(node); }}>
                <Copy size={13} strokeWidth={1.9} aria-hidden="true" />
              </button>
            </>
          )}
          {!isText && (
            <button type="button" className="nodrag" title="编辑" onClick={(e) => { e.stopPropagation(); onEdit(node); }}>
              <Pencil size={13} strokeWidth={1.9} aria-hidden="true" />
            </button>
          )}
          {/* 文本节点也能 AI 生成（用户要求）—— 以前这条被 !isText 挡掉，
              卡片上没有任何生成入口，只能进弹窗。 */}
          <button type="button" className="nodrag" title={isText ? '按当前内容 AI 生成/改写' : '重新生成'} onClick={(e) => { e.stopPropagation(); onGenerate(node); }}>
            <RotateCw size={13} strokeWidth={1.9} aria-hidden="true" />
          </button>
          <button type="button" className="nodrag is-del" title="删除节点" onClick={(e) => { e.stopPropagation(); onDelete(node.id); }}>
            <X size={13} strokeWidth={2.1} aria-hidden="true" />
          </button>
        </div>
        {/* 文本节点右下角缩放把手：即梦/Figma 都从这里拖大小。
            只在选中或悬停时显形 —— 常显会在多卡片并排时糊成一片。
            ★ 图标是手绘 SVG 三段阶梯（不是字符、不是单折角）：
              Figma/即梦 的 resize 角标都是「由小到大的三条平行短线」，
              单根 L 形折角在深色卡片上会被当成边框的一部分，看不出可拖拽。 */}
        {isText && (
          <span
            className={`canvas-text-resize nodrag ${selected || editingText ? 'is-on' : ''}`}
            role="presentation"
            title="拖动改变文本节点大小"
            onPointerDown={startResize}
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
          >
            <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true" fill="none">
              {/* 三根阶梯短杠（长 → 中 → 点），端点圆头。
                  单根 L 形折角太容易和卡片圆角边框混成一体，看不出"可拖拽"；
                  阶梯式是 Figma / 即梦 resize 角标的通用语义，缩到 60% 也能认出。 */}
              <path d="M12 4.6 4.6 12" stroke="currentColor" strokeWidth="1.45" strokeLinecap="round" />
              <path d="M12 8.5 8.5 12" stroke="currentColor" strokeWidth="1.45" strokeLinecap="round" opacity=".8" />
              <path d="M11.3 11.9h.02" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" opacity=".55" />
            </svg>
          </span>
        )}
      </div>
      <div className="canvas-media-foot">
        <KindIcon kind={node.kind} className="canvas-media-foot-ico" />
        <span className="canvas-media-foot-text">
          <span className={`canvas-media-foot-main ${statusCls}`}><i className="canvas-media-dot" />{statusText}</span>
          <span className={`canvas-media-foot-sub${failed && node.lastError ? ' is-error' : ''}`} title={footSubText}>{footSubText}</span>
        </span>
        {!isText && mediaVersions.length > 1 && (
          <div
            className="canvas-media-version-nav nodrag"
            title={`生成版本 ${currentMediaVersionIndex + 1}/${mediaVersions.length}`}
            onClick={(event) => event.stopPropagation()}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              aria-label="上一个生成版本"
              title="上一个生成版本"
              onClick={(event) => {
                event.stopPropagation();
                selectMediaVersion(currentMediaVersionIndex - 1);
              }}
            >
              <ChevronLeft size={10} strokeWidth={2.3} aria-hidden="true" />
            </button>
            <span>{currentMediaVersionIndex + 1}/{mediaVersions.length}</span>
            <button
              type="button"
              aria-label="下一个生成版本"
              title="下一个生成版本"
              onClick={(event) => {
                event.stopPropagation();
                selectMediaVersion(currentMediaVersionIndex + 1);
              }}
            >
              <ChevronRight size={10} strokeWidth={2.3} aria-hidden="true" />
            </button>
          </div>
        )}
        <div className="canvas-media-foot-more">
          <button type="button" className="nodrag" title="更多" onClick={(e) => { e.stopPropagation(); setMenu((v) => !v); }}>
            <MoreHorizontal size={14} strokeWidth={2} aria-hidden="true" />
          </button>
          {menu && (
            <div className="canvas-media-foot-pop">
              {!isText && (
                <button type="button" className="nodrag" onClick={(e) => { e.stopPropagation(); setMenu(false); openLocalPicker(); }}>
                  {node.mediaUrl ? '从本地添加版本' : '导入本地文件'}
                </button>
              )}
              <button type="button" className="nodrag" onClick={(e) => { e.stopPropagation(); setMenu(false); onEdit(node); }}>编辑提示词</button>
              <button type="button" className="nodrag" onClick={(e) => { e.stopPropagation(); setMenu(false); onGenerate(node); }}>{isText ? 'AI 生成/改写' : '重新生成'}</button>
              <button type="button" className="nodrag is-del" onClick={(e) => { e.stopPropagation(); setMenu(false); onDelete(node.id); }}>删除节点</button>
            </div>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Right} className={`canvas-media-port is-source${hasLink ? ' is-linked' : ''}`}>
        <svg className="canvas-port-icon" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M6 1.6V10.4M1.6 6H10.4" fill="none" stroke="currentColor"
            strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </Handle>
    </div>
  );
}

/** 媒体节点提示词栏（即梦风：底部居中浮动编辑栏 + 内联参数 pill + 图标下拉面板） */
function MediaNodeModal({node, assets, edges, catalog, onChange, onGenerate, onDelete, onClose, onRemoveRef, onUploadRef, onPickRef, onBackdropPick, onRevealAt, anchor}: {
  node: CanvasMediaNode | null;
  assets: CanvasMediaNode[];
  edges: Edge[];
  catalog: ModelCatalog | null;
  onChange: (id: string, patch: Partial<CanvasMediaNode>) => void;
  onGenerate: (n: CanvasMediaNode) => boolean;
  onDelete: (id: string) => void;
  onClose: (reason: 'preserve' | 'clear') => void;
  // 移除引用时必须连带删掉画布上对应的那条连线，否则下次打开弹窗
  // openMediaModal 会按残留连线把引用又预填回来（用户看到的"删不掉"就是这个）
  onRemoveRef?: (refNodeId: string) => void;
  // 上传文件并作为引用加入（真上传后写进 params.refs）
  onUploadRef?: (node: CanvasMediaNode, file: File) => Promise<void>;
  // 「从画布选择」：把画布上某个已存在的节点加为引用
  onPickRef?: (node: CanvasMediaNode, refNodeId: string) => void;
  // ★ 弹窗开着时点「另一张卡片」→ 直接切到那张卡（即梦行为），而不是先关掉再点一次。
  //   参数是画布 flow 节点 id（media-xxx）。返回 true 表示已接管这次点击。
  onBackdropPick?: (flowNodeId: string) => boolean;
  onRevealAt?: (requiredBottom: number) => void;
  anchor?: {x: number; y: number; w: number; h: number} | null;
}) {
  const [openMenu, setOpenMenu] = useState<string | null>(null);   // model/ratio/clarity/style/res/dur/tpl/more
  const [prepareOpen, setPrepareOpen] = useState(false);
  const [prepareSelected, setPrepareSelected] = useState<string[]>([]);
  const [prepareMessage, setPrepareMessage] = useState('');
  const [modelSearch, setModelSearch] = useState('');
  const [favoriteModelIds, setFavoriteModelIds] = useState<string[]>(() => readStoredStringArray(MODEL_FAVORITES_KEY));
  const [recentModelIds, setRecentModelIds] = useState<string[]>(() => readStoredStringArray(MODEL_RECENT_KEY));
  const [draggedRefIndex, setDraggedRefIndex] = useState<number | null>(null);
  const [refDropIndex, setRefDropIndex] = useState<number | null>(null);
  const [refDragGhost, setRefDragGhost] = useState<{
    url: string;
    label: string;
    role: string;
    x: number;
    y: number;
  } | null>(null);
  const refPointerDragRef = useRef<{from: number; startX: number; startY: number; active: boolean} | null>(null);
  const [mentionPreview, setMentionPreview] = useState<({url: string; label: string} & PreviewPlacement) | null>(null);
  const [atOpen, setAtOpen] = useState(false);                    // @ 资产候选
  const [atIdx, setAtIdx] = useState(0);
  // 提示词框默认 4 行，点放大按钮才撑到 30vh；之前是无条件自适应，一打开就铺满半屏。
  const [taExpanded, setTaExpanded] = useState(false);
  // 弹窗贴卡片下方还是上方：由渲染后的实际高度决定（下方放不下就翻上去）
  // 弹窗相对卡片的定位形态：below（卡片下方，默认）/ above（翻到上方）/ center（夹在视口中央）
  const [popupMode, setPopupMode] = useState<'below' | 'above' | 'center'>('below');
  const barRef = useRef<HTMLDivElement>(null);
  useDialogFocusTrap(barRef);
  const promptAreaRef = useRef<HTMLDivElement>(null);
  const paramsPanelRef = useRef<HTMLDivElement>(null);
  const atButtonRef = useRef<HTMLButtonElement>(null);
  const atPopRef = useRef<HTMLDivElement>(null);
  const [atPopPos, setAtPopPos] = useState<{left: number; top: number; maxHeight: number} | null>(null);
  const atViewportShiftedRef = useRef(0);
  const atRevealDoneRef = useRef(false);
  const [paramsPanelPos, setParamsPanelPos] = useState<{left: number; top: number} | null>(null);
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  const [canvasPickOpen, setCanvasPickOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [refNotice, setRefNotice] = useState('');
  const [closing, setClosing] = useState(false);
  const closeTimerRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);
  const addMenuRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLDivElement>(null);
  // ★ 所有 hook 必须在条件 return 之前调用（曾在此处踩坑：整树崩溃）
  const busy = node?.status === 'generating';
  const p = node?.params || {};
  const isImg = node?.kind === 'image';
  const isVid = node?.kind === 'video';
  const isAud = node?.kind === 'audio';
  // 文本节点：不参与生成，弹窗里不能出现模型/比例/清晰度/费用/生成按钮（点了也没意义）。
  const isTxt = node?.kind === 'text';
  const atCands = (assets || []).filter((a) => {
    if (!node) return false;
    if (node.kind === 'video') return ['image', 'video', 'audio', 'text'].includes(a.kind);
    if (node.kind === 'image') return ['image', 'text'].includes(a.kind);
    if (node.kind === 'audio') return ['image', 'text'].includes(a.kind);
    return a.kind === 'text';
  });
  const openMenuRef = useRef(openMenu); openMenuRef.current = openMenu;
  const atOpenRef = useRef(atOpen); atOpenRef.current = atOpen;
  // 展开态也要给 growTa 读最新值（它在 onChange 里被调用，闭包可能是旧的）
  const taExpandedRef = useRef(taExpanded); taExpandedRef.current = taExpanded;
  const addMenuOpenRef = useRef(addMenuOpen); addMenuOpenRef.current = addMenuOpen;
  const canvasPickRef = useRef(canvasPickOpen); canvasPickRef.current = canvasPickOpen;
  const requestClose = useCallback((reason: 'preserve' | 'clear' = 'preserve') => {
    if (closing) return;
    setClosing(true);
    closeTimerRef.current = window.setTimeout(() => onClose(reason), 170);
  }, [closing, onClose]);
  useEffect(() => () => {
    if (closeTimerRef.current) window.clearTimeout(closeTimerRef.current);
  }, []);
  useEffect(() => {
    if (closeTimerRef.current) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
    setClosing(false);
  }, [node?.id]);
  // 最近一次写入的 params：同一 tick 内连发 patch（选比例 = ratio + size）时要基于它叠加，
  // 否则第二份快照里的旧值会把第一次写的覆盖掉（★ hook 必须在条件 return 之前）
  const paramsRef = useRef<CanvasMediaParams>(node?.params || {});

  useLayoutEffect(() => {
    if (openMenu !== 'params') {
      setParamsPanelPos(null);
      return;
    }
    const placePanel = () => {
      const panel = paramsPanelRef.current;
      const bar = barRef.current;
      if (!panel || !bar) return;
      const panelRect = panel.getBoundingClientRect();
      const barRect = bar.getBoundingClientRect();
      const margin = 8;
      const sideGap = 10;
      let left = barRect.right + sideGap;
      if (left + panelRect.width > window.innerWidth - margin) {
        left = barRect.left - panelRect.width - sideGap;
      }
      let top = barRect.top;
      if (left < margin || left + panelRect.width > window.innerWidth - margin) {
        left = Math.max(
          margin,
          Math.min(
            window.innerWidth - panelRect.width - margin,
            barRect.left + 12,
          ),
        );
        top = barRect.top - panelRect.height - margin;
        if (top < margin) top = barRect.bottom + margin;
      }
      top = Math.max(margin, Math.min(window.innerHeight - panelRect.height - margin, top));
      setParamsPanelPos({left, top});
    };
    /**
     * ★ 第 36 轮：先**同步**定位一次，再挂 rAF 兜底。
     * 原来只走 `requestAnimationFrame`：那一帧不一定赶得上（实测开面板后要等
     * 一次 resize 事件才会落位，期间面板停在 `left/top:-9999` 且 `visibility:hidden`，
     * 用户看到的就是"点了没反应"）。同步跑一次即可保证首帧就带着正确坐标绘制，
     * rAF 仍然保留，用来吸收字体/滚动条导致的二次尺寸变化。
     */
    placePanel();
    const frame = window.requestAnimationFrame(placePanel);
    window.addEventListener('resize', placePanel);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', placePanel);
    };
  }, [openMenu, node?.id]);

  useLayoutEffect(() => {
    if (!atOpen) {
      setAtPopPos(null);
      return;
    }
    const place = () => {
      const button = atButtonRef.current;
      const panel = atPopRef.current;
      if (!button) return;
      const rect = button.getBoundingClientRect();
      const width = panel?.offsetWidth || 360;
      const height = panel?.offsetHeight || 280;
      const margin = 8;
      const left = Math.max(
        margin,
        Math.min(rect.left, window.innerWidth - width - margin),
      );
      const below = rect.bottom + margin;
      const top = below + height <= window.innerHeight - margin
        ? below
        : Math.max(margin, rect.top - height - margin);
      const shift = popupRevealShift(
        {top, bottom: top + height},
        window.innerHeight,
        {topInset: 12, bottomInset: 12},
      );
      if (onRevealAt && shift !== 0) {
        const delta = shift - atViewportShiftedRef.current;
        if (Math.abs(delta) >= 1) {
          onRevealAt(delta);
          atViewportShiftedRef.current = shift;
        }
      }
      setAtPopPos({
        left,
        top,
        maxHeight: Math.max(120, window.innerHeight - top - margin),
      });
    };
    place();
    const frame = window.requestAnimationFrame(place);
    window.addEventListener('resize', place);
    document.addEventListener('scroll', place, true);
    window.addEventListener('scroll', place, true);
    const observer = typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver(place)
      : null;
    const popup = atPopRef.current;
    if (popup) observer?.observe(popup);
    observer?.observe(document.body);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', place);
      document.removeEventListener('scroll', place, true);
      window.removeEventListener('scroll', place, true);
      observer?.disconnect();
    };
  }, [atOpen, atCands.length, onRevealAt]);
  useEffect(() => {
    if (!atOpen) {
      atRevealDoneRef.current = false;
      atViewportShiftedRef.current = 0;
    }
  }, [atOpen]);

  // 提示词框高度：**由内容决定**（对齐即梦 —— 用户明确要求"没内容的时候高度和有内容的高度不一样"）。
  //   空态只有一行占位文案 → 框很矮；写下内容后随行数长高，到上限才内部滚动。
  //   上限：普通卡 132px（约 4~5 行）、文本卡 220px（正文就是成品，常 100~200 字）、
  //   放大态 46vh（适合长提示词通读）。
  //   ★ 不要再写死 min-height —— CSS 的 min-height 会压过这里设的 inline height，
  //     那正是"空输入框也占满一大块"的原因。
  const collapsedH = () => (node?.kind === 'text' ? 220 : 132);
  // 空内容时的底线高度（一行文字 + 上下 padding），保证不至于塌成一条缝
  const baseH = 52;
  // 统一的高度计算：空内容 → 底线高度；有内容 → 按 scrollHeight 长，封顶 cap。
  // ★ 为什么要单独判空：textarea 的 scrollHeight 在**空内容**时并不等于一行高度 ——
  //   受 padding / line-height / 父级 flex 影响，实测空文本节点也能报到 132px，
  //   于是"空框和有内容一样高"（用户要的正是两者不同）。空内容一律 return baseH。
  // ★ 必须先把 height 置 auto 再读 scrollHeight，否则读到的是上一次设定值的残留。
  const calcTaHeight = (ta: HTMLElement, cap: number): number => {
    if (!promptTextFromEditor(ta).trim()) return baseH;
    const prev = ta.style.height;
    ta.style.height = 'auto';
    const need = ta.scrollHeight;
    ta.style.height = prev;
    return Math.max(baseH + 8, Math.min(need, cap));
  };
  // 放大态是大浮层（居中占屏幕），输入框可以直接吃 46vh
  const EXPANDED_CAP = () => Math.max(240, Math.min(window.innerHeight * 0.46, 420));

  // 打开弹窗只做高度计算，不自动聚焦提示词。用户点击卡片进入时应先查看内容，
  // 只有主动点击输入框后才出现编辑光标。
  useEffect(() => {
    if (!node) return;
    setTaExpanded(false);
    setMentionPreview(null);
    const ta = taRef.current;
    if (ta) {
      if (document.activeElement === ta) ta.blur();
      ta.style.height = `${calcTaHeight(ta, collapsedH())}px`;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node?.id]);

  // Esc 逐层关闭：下拉面板 / + 菜单 / 画布选择 → @ 候选 → 整栏
  useEffect(() => {
    if (!node) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (openMenuRef.current) setOpenMenu(null);
      else if (addMenuOpenRef.current) setAddMenuOpen(false);
      else if (canvasPickRef.current) setCanvasPickOpen(false);
      else if (atOpenRef.current) setAtOpen(false);
      else requestClose('preserve');
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [node, requestClose]);

  // 点空白处关掉「+」菜单与画布选择浮层（和 @ 候选一致的行为）
  useEffect(() => {
    if (!addMenuOpen && !canvasPickOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (addMenuOpen && addMenuRef.current && !addMenuRef.current.contains(t)) setAddMenuOpen(false);
      if (canvasPickOpen && !t?.closest('.jim-pick-pop, .jim-add')) setCanvasPickOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [addMenuOpen, canvasPickOpen]);

  // ★ 任何浮层（模型/比例/清晰度/时长/长度/更多 …）都必须「点外面就关」
  //   —— 用户反馈"点开后关闭窗口不会自动关闭"，根因是旧代码只给「+」菜单和画布选择
  //   装了外部点击监听，.jim-pop 那一批下拉完全没有：一旦展开，只有再点一次那个按钮
  //   或按 Esc 才收得回，视觉上就像卡住了。
  //
  // ★★ 第 15 轮修正（用户截图："画幅比例"展开着，点输入框/弹窗里别处都不关）：
  //   上一版判定是「点击目标是否还在 .jim-bar 内」→ **只要点在弹窗里就一律不关**，
  //   于是用户在弹窗内部随便点（改提示词、看参考图、点别处）浮层都赖着不走。
  //   正确判定不是"在不在弹窗里"，而是"**点的是不是这个浮层自己、或打开它的那个按钮**"：
  //     · 在浮层内（.jim-pop / .jim-add-pop / .jim-at-pop）→ 不关（还要在里面选东西）
  //     · 在打开它的那个按钮上（[data-pop-owner="<当前开着的 key>"]）→ 不关（按钮自己 toggle 收起）
  //     · 其它任何位置，**包括弹窗内部的输入框、标题栏、空白**→ 关
  //   这样以后新增下拉只要给触发按钮加 data-pop-owner 就自动生效，不用再记白名单。
  useEffect(() => {
    if (!openMenu && !atOpen && !addMenuOpen) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      // 落在任意浮层内部：让用户继续在浮层里操作
      if (t.closest('.jim-pop, .jim-add-pop, .jim-at-pop')) return;
      // 落在"打开当前浮层的那个按钮"上：交给按钮自己 toggle，避免关了又立刻被打开
      if (openMenu && t.closest(`[data-pop-owner="${openMenu}"]`)) return;
      if (atOpen && t.closest('[data-pop-owner="at"]')) return;
      if (addMenuOpen && t.closest('[data-pop-owner="add"]')) return;
      // 其余一律关闭：弹窗内部其它区域、画布、工具栏都算"点外面"
      setOpenMenu(null);
      setAtOpen(false);
      setAddMenuOpen(false);
    };
    document.addEventListener('mousedown', onDown, true);
    return () => document.removeEventListener('mousedown', onDown, true);
  }, [openMenu, atOpen, addMenuOpen]);

  // ★ 切换卡片时必须清空浮层状态。
  //   弹窗组件在 editNodeId 变化时不会卸载（同一个 <MediaNodeModal>），局部 state 会整份留着，
  //   于是"点了 A 打开模型下拉 → 直接点 B → B 的弹窗里模型下拉也是开着的"（用户截图反馈）。
  //   这里以 node.id 为界做一次全量复位。
  useEffect(() => {
    setOpenMenu(null);
    setAtOpen(false);
    setAtIdx(0);
    setAddMenuOpen(false);
    setCanvasPickOpen(false);
    setUploading(false);
    setTaExpanded(false);
    setPrepareOpen(false);
    setPrepareSelected([]);
    setPrepareMessage('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node?.id]);

  // 锚定方向：弹窗渲染后用真实高度判断「卡片下方还放得下吗」。
  // 上一版用 CSS max-height 硬裁，结果弹窗只有 118px 高、内容照常溢出压到画布上。
  //
  // ★ 三态（用户连续两轮反馈"弹窗遮挡还是没解决"）：
  //   ① 卡片下方放得下 → 贴在卡片下方（常规）
  //   ② 下方放不下、上方放得下 → 翻到卡片上方（data-flip=1）
  //   ③ 上下都放不下（卡片很高 / 弹窗很长 / 窗口矮）→ **居中并夹进视口**
  //     以前只有①②：③ 时会翻到上方，而 top 是负值的主角 —— 弹窗顶出视口、
  //     被顶部工具条盖住上半截，用户看到的就是"弹窗被遮挡、上面的字看不见"。
  //   居中态的 max-height 由下面的 CSS 统一夹紧，内容区内部滚动，不会出屏。
  const refCount = node?.params?.refs?.length ?? 0;
  useEffect(() => {
    if (!node || !anchor || taExpanded) { setPopupMode('below'); return; }
    const el = barRef.current;
    if (!el) return;
    const need = el.scrollHeight;                       // 内容真实需要的高度
    const spaceBelow = window.innerHeight - (anchor.y + anchor.h) - 12 - 8;
    if (spaceBelow >= need) { setPopupMode('below'); return; }
    const spaceAbove = anchor.y - 12 - 8;
    // 上方也放不下（差 24px 以上）→ 居中夹紧，别硬翻上去顶出屏幕
    setPopupMode(spaceAbove >= need ? 'above' : (spaceAbove > spaceBelow ? 'center' : 'center'));
  }, [node, anchor, taExpanded, refCount]);

  if (!node) return null;
  const meta = MEDIA_KIND_META[node.kind];
  const generateAndClose = () => {
    if (onGenerate(node)) requestClose();
  };
  paramsRef.current = node.params || {};
  const patch = (key: keyof CanvasMediaParams, value: unknown) => {
    const next: CanvasMediaParams = {...paramsRef.current, [key]: value};
    paramsRef.current = next;   // 同一 tick 内的下一次 patch 基于本次结果，避免互相覆盖
    onChange(node.id, {params: next} as Partial<CanvasMediaNode>);
  };
  const selectModel = (spec: ModelSpec) => {
    const next = inheritModelParams(paramsRef.current, spec);
    const defaults = spec.defaultParams || {};
    if (spec.capability === 'image' && defaults.size) {
      next.size = CLARITY_SIZES[String(next.clarity || defaults.clarity || '1.5K')]
        ?.[String(next.ratio || defaults.ratio || '1:1')]
        || defaults.size;
    }
    paramsRef.current = next;
    onChange(node.id, {params: next});
    const nextRecent = [spec.id, ...recentModelIds.filter((id) => id !== spec.id)].slice(0, 12);
    setRecentModelIds(nextRecent);
    writeStoredStringArray(MODEL_RECENT_KEY, nextRecent);
    setOpenMenu(null);
  };
  // 模型选项来自后端目录。旧节点里保存的真实模型 ID 会先做一次兼容映射。
  const catalogModels = (catalog?.models || []).filter((item) => item.enabled !== false && item.capability === node.kind);
  // Fallback metadata is only for a temporarily unavailable catalog. When the
  // catalog loaded successfully but a provider/model was disabled, showing the
  // built-in list again would let users select a model the backend cannot use.
  const modelOptions = catalog ? catalogModels : fallbackModelSpecs(node.kind);
  const normalizedModelSearch = modelSearch.trim().toLowerCase();
  const visibleModelOptions = modelOptions
    .filter((item) => !normalizedModelSearch || [
      item.label, item.id, item.remoteModel, item.description, item.providerId,
    ].join(' ').toLowerCase().includes(normalizedModelSearch))
    .sort((left, right) => {
      const favoriteDelta = Number(favoriteModelIds.includes(right.id)) - Number(favoriteModelIds.includes(left.id));
      if (favoriteDelta) return favoriteDelta;
      const leftRecent = recentModelIds.indexOf(left.id);
      const rightRecent = recentModelIds.indexOf(right.id);
      if (leftRecent !== rightRecent) {
        if (leftRecent < 0) return 1;
        if (rightRecent < 0) return -1;
        return leftRecent - rightRecent;
      }
      return 0;
    });
  const storedModel = canonicalModelId(p.model || p.ttsModel);
  const requestedProvider = String(p.provider || '');
  const curModelSpec = modelOptions.find((item) => item.id === storedModel || item.remoteModel === storedModel)
    || modelOptions.find((item) => item.providerId === requestedProvider)
    || modelOptions[0];
  const curModel = curModelSpec?.id || storedModel || '';
  const promptLimit = Math.max(1, Number(curModelSpec?.limits?.maxPromptChars || 4000));
  const overPromptLimit = node.prompt.length > promptLimit;
  const activeProvider = catalog?.providers.find((item) => item.id === curModelSpec?.providerId);
  const missingProviderKey = Boolean(catalog && activeProvider && !activeProvider.has_api_key);
  const referenceEvaluation = evaluateReferenceLimits(
    node,
    assets || [],
    edges,
    curModelSpec || null,
  );
  const effectiveNode = referenceEvaluation.node;
  const dependencyBlockers = mediaDependencyBlockers(effectiveNode, assets || []);
  const dependenciesBlocked = dependencyBlockers.length > 0;
  const prepareTargets = dependencyBlockers.map((blocker) => {
    const source = (assets || []).find((item) => item.id === blocker.nodeId);
    if (!source || (source.kind !== 'image' && source.kind !== 'audio')) return null;
    const candidates = (catalog?.models || []).filter(
      (item) => item.enabled !== false && item.capability === source.kind,
    );
    const stored = canonicalModelId(source.params?.model || source.params?.ttsModel);
    const spec = candidates.find((item) => item.id === stored || item.remoteModel === stored)
      || candidates.find((item) => item.providerId === source.params?.provider)
      || candidates[0]
      || null;
    const hasVideoInput = (source.params?.refs || []).some(
      (ref) => (assets || []).find((item) => item.id === ref.nodeId)?.kind === 'video',
    );
    return {
      source,
      spec,
      cost: spec ? generationCostAmount(source, spec, {hasVideoInput}) : null,
      costLabel: spec ? generationCostHint(source, spec, {hasVideoInput}) : '没有可用模型',
    };
  }).filter((item): item is NonNullable<typeof item> => !!item);
  const selectedPrepareTargets = prepareTargets.filter((item) => prepareSelected.includes(item.source.id));
  const selectedKnownCost = selectedPrepareTargets.reduce(
    (sum, item) => sum + (item.cost ?? 0),
    0,
  );
  const selectedUnknownCost = selectedPrepareTargets.some((item) => item.cost === null);
  const modelParamIssues = validateModelParams(p, curModelSpec);
  const nodeNotices: string[] = [];
  if (catalog && modelOptions.length === 0) nodeNotices.push('当前类型没有可用模型，请到「模型接口」中启用或添加模型');
  if (missingProviderKey) nodeNotices.push(`供应商「${activeProvider?.name || curModelSpec?.providerId}」尚未配置 API Key`);
  if (overPromptLimit) nodeNotices.push(`提示词 ${node.prompt.length} / ${promptLimit} 字，已超过当前模型上限`);
  if (referenceEvaluation.issue) nodeNotices.push(referenceEvaluation.message);
  if (dependenciesBlocked) {
    const names = dependencyBlockers.slice(0, 4).map((item) => `${item.title}（${item.reason}）`).join('、');
    nodeNotices.push(`还有 ${dependencyBlockers.length} 项引用未就绪：${names}${dependencyBlockers.length > 4 ? '…' : ''}`);
  }
  if (node.promptStale) nodeNotices.push(node.promptStaleReason || '源提示词已更新，当前素材仍是旧版本');
  if (modelParamIssues.length) nodeNotices.push(modelParamIssues[0].message);
  if (node.status === 'failed' && node.lastError) nodeNotices.push(friendlyTaskError(node.lastError));
  const schema = curModelSpec?.params || {};
  const builtInParamKeys = new Set<string>(isTxt
    ? ['maxTokens', 'temperature', 'system']
    : isAud
      ? ['ttsMode', 'ttsVoice', 'voiceId', 'outputFormat', 'sampleRate', 'previewDuration', 'previewText', 'voicePrompt']
      : isVid
        ? ['ratio', 'resolution', 'duration', 'count', 'guidance']
        : ['ratio', 'clarity', 'size', 'guidance', 'watermark']);
  const extraParamSpecs = Object.entries(schema).filter(([key]) => !builtInParamKeys.has(key));
  const paramLabel = (key: string) => ({
    mode: '生成模式',
    firstFrame: '首帧地址',
    lastFrame: '尾帧地址',
    responseFormat: '返回格式',
    generateAudio: '生成音频',
    watermark: '水印',
    seed: '随机种子',
  } as Record<string, string>)[key] || key;
  const paramOptionLabel = (option: string | number) => ({
    auto: '自动',
    text: '纯文本',
    reference: '参考素材',
    keyframe: '首尾帧',
    url: 'URL',
    b64_json: 'Base64',
  } as Record<string, string>)[String(option)] || String(option);
  const renderExtraParamControls = () => extraParamSpecs.map(([key, spec]) => {
    const value = (p as Record<string, unknown>)[key];
    if (spec.type === 'boolean') {
      return (
        <div key={key} className="jim-param-control">
          <div className="jim-param-label">{paramLabel(key)}</div>
          <div className="jim-chip-row">
            <button type="button" className={`jim-chip ${value === true ? 'is-active' : ''}`} onClick={() => patch(key as keyof CanvasMediaParams, true)}>开启</button>
            <button type="button" className={`jim-chip ${value === false || value === undefined ? 'is-active' : ''}`} onClick={() => patch(key as keyof CanvasMediaParams, false)}>关闭</button>
          </div>
        </div>
      );
    }
    if (spec.type === 'select' && Array.isArray(spec.options)) {
      return (
        <div key={key} className="jim-param-control">
          <div className="jim-param-label">{paramLabel(key)}</div>
          <div className="jim-chip-row">
            {spec.options.map((option) => (
              <button key={String(option)} type="button" className={`jim-chip ${String(value ?? spec.default ?? '') === String(option) ? 'is-active' : ''}`} onClick={() => patch(key as keyof CanvasMediaParams, option)}>{paramOptionLabel(option)}</button>
            ))}
          </div>
        </div>
      );
    }
    return (
      <label key={key} className="jim-param-control">
        <span className="jim-param-label">{paramLabel(key)}</span>
        <input
          className="jim-param-input"
          type={spec.type === 'range' ? 'number' : 'text'}
          min={spec.min}
          max={spec.max}
          step={spec.step}
          value={String(value ?? spec.default ?? '')}
          onChange={(event) => patch(
            key as keyof CanvasMediaParams,
            spec.type === 'range' ? Number(event.target.value) : event.target.value,
          )}
        />
      </label>
    );
  });
  const optionsOf = <T extends string | number>(key: string, fallback: T[]): T[] => {
    const values = schema[key]?.options;
    return Array.isArray(values) && values.length ? values as T[] : fallback;
  };
  const ratioOptions = optionsOf('ratio', isVid ? ['9:16', '16:9', '1:1'] : ['1:1', '16:9', '9:16', '4:3', '3:4']);
  const clarityOptions = optionsOf('clarity', CLARITIES);
  const guidanceOptions = optionsOf('guidance', GUIDANCES);
  const resolutionOptions = optionsOf('resolution', RESOLUTIONS);
  const countOptions = optionsOf<number>('count', [1]);
  const durationOptions = optionsOf<number>('duration', [4, 5, 10, 15]);
  const durationMin = Number(schema.duration?.min ?? Math.min(...durationOptions, 4));
  const durationMax = Number(schema.duration?.max ?? Math.max(...durationOptions, 15));
  const durationStep = Number(schema.duration?.step ?? 1);
  const durationTicks = Array.from(
    {length: Math.floor(durationMax / 5) + 1},
    (_, index) => index * 5,
  );
  if (durationTicks[durationTicks.length - 1] !== durationMax) durationTicks.push(durationMax);
  const durationTickPercent = (tick: number) =>
    durationMax > 0 ? (tick / durationMax) * 100 : 0;
  const chooseOption = (raw: unknown, values: Array<string | number>, fallback: string | number) =>
    values.map(String).includes(String(raw ?? '')) ? String(raw) : String(fallback);
  const ratioLabel = chooseOption(p.ratio, ratioOptions, curModelSpec?.defaultParams?.ratio || (isVid ? '9:16' : '1:1'));
  const clarityLabel = chooseOption(p.clarity, clarityOptions, curModelSpec?.defaultParams?.clarity || '1.5K');
  const resLabel = chooseOption(p.resolution, resolutionOptions, curModelSpec?.defaultParams?.resolution || '480P');
  const durLabel = `${p.duration ?? curModelSpec?.defaultParams?.duration ?? 5}s`;
  const countLabel = String(p.count ?? curModelSpec?.defaultParams?.count ?? 1);
  // 文本节点生成长度（短/中/长）：p.maxTokens 默认 1200
  const curLen = TEXT_LEN_OPTS.find((o) => o.tokens === (p.maxTokens ?? 1200)) || TEXT_LEN_OPTS[1];
  const lenLabel = `长度 ${curLen.label}`;
  const guidanceLabel = chooseOption(p.guidance, guidanceOptions, curModelSpec?.defaultParams?.guidance || '中等');
  const paramSummary = isAud
    ? `${(p.ttsMode || 'design') === 'design' ? '音色设计' : '指定音色'} · ${String(p.outputFormat || 'mp3').toUpperCase()}`
    : isTxt
      ? lenLabel
      : isVid
        ? `${ratioLabel} · ${resLabel} · ${countLabel}`
        : `${ratioLabel} · ${clarityLabel}`;
  const refs = p.refs || [];
  // ★★ voiceRef/voiceRefs（音色引用）必须**全部**渲染（第 17 轮修）。
  //   历史问题：早期 voiceRef **完全没有渲染**（"手动连接的节点为什么资产引用那不显示"）；
  //   后来补渲染了，但 voiceRef 是**单值**字段，连 4 个音频节点时后连的覆盖先连的，
  //   于是只剩 1 个 —— 用户这次的原话是「明明连线的是 4 个节点，为什么引用资产只显示一个」。
  //   现在用 voiceRefList() 把 voiceRefs(多值) 与老 voiceRef(单值) 合并去重，
  //   与普通 refs 合并成同一条素材行，顺序 = 连线先后，一个都不丢。
  const voiceEntries = voiceRefList(p);
  const voiceOnly = voiceEntries.filter((v) => !refs.some((r) => r.nodeId === v.nodeId));
  const refEntries: {nodeId: string; label?: string; url?: string; isVoice?: boolean}[] = [
    ...refs,
    ...voiceOnly.map((v) => ({...v, isVoice: true})),
  ];
  // ★ 缩略图地址兜底：payload 里 metadata.refs 只存 {nodeId,label}，**没有 url**
  //   （见 data/canvas.db 里的真实数据结构）。直接读 r.url 永远是空 →
  //   缩略图全部退化成"资产""角色"两字文字块（用户反馈"这里就是识别节点的引用啊"）。
  //   所以按 nodeId 回源到画布节点上取 mediaUrl / 缩略图。
  const urlById = new Map<string, string>();
  const labelById = new Map<string, string>();
  const kindById = new Map<string, CanvasMediaNodeKind>();
  const portraitByAssetKey = new Map<string, string>();
  const assetKeys = (title?: string): string[] => {
    const key = String(title || '').replace(/^[^｜|]*[｜|]\s*/, '').trim();
    const withoutFigure = key.replace(/[（(]\s*图\s*\d+\s*[)）]/gi, '').trim();
    return [...new Set([key, withoutFigure].filter(Boolean))];
  };
  for (const a of assets || []) {
    const u = a.mediaUrl || (a.params as {thumbUrl?: string} | undefined)?.thumbUrl;
    if (u) urlById.set(a.id, u);
    if (a.title) labelById.set(a.id, a.title);
    kindById.set(a.id, a.kind);
    if (a.kind === 'image' && u && a.title) {
      for (const key of assetKeys(a.title)) portraitByAssetKey.set(key, u);
    }
  }
  const hasVideoInput = (effectiveNode.params?.refs || [])
    .some((ref) => kindById.get(ref.nodeId) === 'video');
  // ★ 缩略图地址：**以源节点当前 mediaUrl 为准**，ref 自带的 url 只作兜底。
  //   为什么优先级要反过来（原来 r.url 优先）：ref.url 是「写引用那一刻」的快照，
  //   源节点重新生成后它不会更新，甚至可能是早已失效的 blob: 会话地址
  //   （实测库里就有一条 blob:http://localhost:8891/… 的僵尸引用）。
  //   用户看到的「引用资产对不上」有一部分就是：缩略图显示的是旧图/空占位，
  //   而真正进模型的却是源节点的新图 —— 界面与投喂不一致。
  const refUrl = (r: {nodeId: string; url?: string}) => {
    const live = urlById.get(r.nodeId);
    if (live) return live;
    const own = r.url || '';
    // blob: 地址只在当前浏览器会话内有效，且刷新后必然失效 → 不拿它当缩略图
    return own.startsWith('blob:') ? '' : own;
  };
  // 显示名也回源：payload 里的 label 常是「资产图」「角色音色」这类泛称，
  // hover 提示要看得出具体是哪张（即梦显示的是节点名）。
  const refLabel = (r: {nodeId: string; label?: string}) => labelById.get(r.nodeId) || r.label || '';
  const refRoleLabel = (r: {nodeId: string; label?: string}) => {
    const label = refLabel(r);
    if (/^音频/.test(label)) return '音色';
    const role = /^(角色|场景|道具)/.exec(label)?.[1];
    if (role) return role;
    const kind = refKind(r);
    return kind === 'text' ? '文本' : kind === 'audio' ? '音色' : kind === 'video' ? '视频' : '参考图';
  };
  // 空引用占位：源节点还没生成图（现有 62 个引用里 61 个如此）时，
  // 不显示"资产""角色"这类文字，而是显示该引用**目标类型的图标** ——
  // 文本 T / 图片 / 音频波形 / 视频播放，一眼看出这个位子引用的是什么种类。
  const refKind = (r: {nodeId: string}): CanvasMediaNodeKind =>
    kindById.get(r.nodeId) || (isAud ? 'audio' : 'image');
  const removeRef = (ref: {nodeId: string; label?: string; url?: string}) => {
    onRemoveRef?.(ref.nodeId);
    const next = withVoiceRefs(
      {...paramsRef.current},
      voiceRefList(paramsRef.current).filter((item) => item.nodeId !== ref.nodeId),
    );
    next.refs = (next.refs || refs).filter((item) => item.nodeId !== ref.nodeId);
    paramsRef.current = next;
    onChange(node.id, {
      prompt: removePromptMention(node.prompt, refLabel(ref)),
      params: next,
    });
  };
  const reorderRefs = (from: number, to: number) => {
    if (from === to || from < 0 || to < 0) return;
    const ordered = [...refEntries];
    const [moved] = ordered.splice(from, 1);
    if (!moved) return;
    ordered.splice(to, 0, moved);
    const next = {
      ...paramsRef.current,
      refs: ordered
        .filter((item) => !item.isVoice)
        .map(({isVoice: _isVoice, ...item}) => ({
          nodeId: item.nodeId,
          label: item.label || item.nodeId,
          url: item.url,
        })),
    };
    const voices = ordered
      .filter((item) => item.isVoice)
      .map(({isVoice: _isVoice, ...item}) => ({
        nodeId: item.nodeId,
        label: item.label || item.nodeId,
        url: item.url,
      }));
    const withVoices = withVoiceRefs(next, voices);
    paramsRef.current = withVoices;
    onChange(node.id, {
      params: withVoices,
    });
    setDraggedRefIndex(null);
    setRefDropIndex(null);
  };
  const portraitForAudioNode = (node: CanvasMediaNode): string => {
    const direct = (node.params as {thumbUrl?: string} | undefined)?.thumbUrl;
    if (direct) return direct;
    for (const key of assetKeys(node.title)) {
      const url = portraitByAssetKey.get(key);
      if (url) return url;
    }
    return '';
  };
  const refVisualUrl = (ref: {nodeId: string; label?: string; url?: string}) => {
    if (refKind(ref) === 'audio') {
      const title = labelById.get(ref.nodeId) || ref.label || '';
      for (const key of assetKeys(title)) {
        const url = portraitByAssetKey.get(key);
        if (url) return url;
      }
      return '';
    }
    return refUrl(ref);
  };
  const showAssetPreview = (url: string, label: string, element: HTMLElement) => {
    if (!url) return;
    const panel = element.closest<HTMLElement>('.jim-bar');
    setMentionPreview({
      url,
      label,
      ...previewPlacement(
        element.getBoundingClientRect(),
        'center',
        panel?.getBoundingClientRect(),
      ),
    });
  };
  const templates = PROMPT_TEMPLATES[node.kind] || [];
  // 提示词框的 placeholder 按节点类型说话：
  // ★ 之前三类节点共用一句"描述你想生成的画面内容" —— 文本节点是内容卡（不生成图）、
  //   音频节点是 TTS 台词，提示语和实际用途对不上（体检时用户能直接看出别扭）。
  const promptPlaceholder = isTxt
    ? '写下你想让 AI 写/改写的内容（如「本集梗概：赵铁柱回厂讨说法…」），回车生成；输入 @ 可引用其它文本节点作为上下文'
    : isAud
      ? (p.ttsMode === 'speech'
          ? '输入要用指定音色合成的台词或旁白；音色 ID 在上方参数栏填写'
          : '描述角色的音色特征（年龄、音区、音色、语速、情绪），百炼会据此创建音色并返回试听')
      : '描述你想生成的画面内容，输入 @ 可快速调用参考内容（如 @图片1 模仿 @视频1 的动作，音色参考 @音频1）';
  const barRect = barRef.current?.getBoundingClientRect();
  const promptAreaRect = promptAreaRef.current?.getBoundingClientRect();
  const atPopBottom = barRect && promptAreaRect
    ? Math.max(18, promptAreaRect.bottom - barRect.top + 14)
    : undefined;
  const atPopMaxHeight = barRect
    ? Math.max(80, Math.min(320, barRect.top - 24))
    : 280;

  // 提示词框的两种高度已在 hook 之前定义。这里只保留自适应逻辑，读取 taExpanded 最新值。
  const growTa = () => {
    const ta = taRef.current;
    if (!ta) return;
    const cap = taExpandedRef.current ? EXPANDED_CAP() : collapsedH();
    ta.style.height = `${calcTaHeight(ta, cap)}px`;
  };
  // 展开/收起：直接把高度切到目标值，不依赖 onChange 时机
  const applyTaHeight = (expanded: boolean) => {
    const ta = taRef.current;
    if (!ta) return;
    const cap = expanded ? EXPANDED_CAP() : collapsedH();
    ta.style.height = `${calcTaHeight(ta, cap)}px`;
  };
  const toggle = (k: string) => setOpenMenu((cur) => (cur === k ? null : k));
  // 上传参考素材：真上传 → 写入 refs（音频卡同时写 voiceRef）
  const addUploadedRef = async (file: File) => {
    if (!onUploadRef) return;
    setUploading(true);
    try {
      await onUploadRef(node, file);
    } finally {
      setUploading(false);
    }
  };
  // 「从画布选择」候选：画布上其它可作参考的节点（排除自己）。
  // 图片/音频/视频/文本四类都收 —— 素材行的 T 与 ▶ 入口也走这里。
  const pickCands = atCands.filter((a) => a.kind !== 'text' || a.prompt?.trim());
  const insertTemplate = (text: string) => {
    const base = node.prompt.replace(/\s+$/, '');
    onChange(node.id, {prompt: base ? `${base}${text}` : text.replace(/^，/, '')});
    setOpenMenu(null);
    growTa();
  };
  const pickAt = (a: CanvasMediaNode) => {
    const nextRefs = [...refs];
    const alreadyPicked = nextRefs.some((r) => r.nodeId === a.id)
      || voiceRefList(p).some((r) => r.nodeId === a.id);
    const maxRefs = modelMaxRefs(curModelSpec);
    if (!alreadyPicked && refs.length + voiceRefList(p).length >= maxRefs) {
      setRefNotice(`当前模型最多引用 ${maxRefs} 个素材`);
      return;
    }
    setRefNotice('');
    if (!(isVid && a.kind === 'audio') && !nextRefs.some((r) => r.nodeId === a.id)) {
      nextRefs.push({nodeId: a.id, label: a.title || a.kind, url: a.mediaUrl});
    }
    const params: CanvasMediaParams = {...p, refs: nextRefs};
    // ★ 第 17 轮：@ 引用音频时同样**累加**（旧写法整体覆盖 voiceRef，连多个音色只剩最后一个）
    const finalParams = (isVid && a.kind === 'audio')
      ? withVoiceRefs(params, [...voiceRefList(params), {nodeId: a.id, label: a.title || a.kind, url: a.mediaUrl}])
      : params;
    const mention = `@${a.title || a.kind}`;
    const hasText = node.prompt.includes(mention);
    const input = taRef.current;
    const start = input ? promptCaretOffset(input) : node.prompt.length;
    const end = start;
    let before = node.prompt.slice(0, start);
    const after = node.prompt.slice(end);
    // The menu is opened from the caret position, so consume exactly the
    // trigger character immediately before it. Requiring whitespace here made
    // mid-prompt insertions leave a stray "@" behind.
    if (before.endsWith('@')) before = before.slice(0, -1);
    const needsSpaceBefore = before && !/\s$/.test(before);
    const insertion = `${needsSpaceBefore ? ' ' : ''}${mention} `;
    const prompt = hasText ? `${before}${after}` : `${before}${insertion}${after}`;
    if (onPickRef) {
      onPickRef(node, a.id);
      onChange(node.id, {prompt});
    } else {
      onChange(node.id, {prompt, params: finalParams});
    }
    const restoreFocus = () => {
      const cursor = before.length + insertion.length;
      input?.focus();
      if (input) promptSetCaret(input, cursor);
      growTa();
    };
    // Rebuild happens on the next React render. Restoring focus twice keeps
    // rapid "select asset then type again" input from landing on stale DOM.
    window.requestAnimationFrame(() => {
      restoreFocus();
      window.setTimeout(restoreFocus, 0);
    });
    setAtOpen(false);
    setAtIdx(0);
  };

  return ReactDOM.createPortal(
    <div className={`jim-backdrop ${closing ? 'is-closing' : ''}`} onClick={(e) => {
      // ★ 点击落在「另一张卡片」上 → 切换过去，不要关掉再点一次。
      //   根因：backdrop 是 inset:0 的全屏层，把画布整个盖住了，所以点卡片时
      //   命中的永远是这个 backdrop，卡片自己的 onClick 根本收不到；
      //   旧行为就是无条件 onClose → 用户"点下一张卡，弹窗先消失，还得再点一下"，
      //   连点两下还会被当成双击进文本编辑，体感非常差（即梦是一点即切）。
      //
      //   这里用 elementsFromPoint 穿透取真实命中：backdrop 在最上层，
      //   忽略它之后第一个 .react-flow__node 就是用户真正想点的那张卡。
      if (onBackdropPick) {
        const under = document.elementsFromPoint(e.clientX, e.clientY);
        const hit = under.find((el): el is HTMLElement =>
          el instanceof HTMLElement && el.classList.contains('react-flow__node'));
        const flowId = hit?.dataset?.id;
        if (flowId && onBackdropPick(flowId)) return;
      }
      requestClose('clear');
    }}>
      {refDragGhost && (
        <div
          className="jim-ref-drag-ghost"
          style={{left: refDragGhost.x + 14, top: refDragGhost.y - 28}}
          aria-hidden="true"
        >
          {refDragGhost.url
            ? <img src={refDragGhost.url} alt="" />
            : <span className="jim-ref-drag-ghost-placeholder"><ImageGlyph size={17} strokeWidth={1.8} /></span>}
          <span>
            <b>{refDragGhost.label}</b>
            <small>{refDragGhost.role}</small>
          </span>
        </div>
      )}
      <div
        className={`jim-bar ${closing ? 'is-closing' : ''}`}
        ref={barRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`${meta.label}生成参数`}
        data-kind={node.kind}
        onClick={(e) => e.stopPropagation()}
        // 只有用户主动点击提示词输入框时才进入编辑态。点击弹窗空白区域只阻止
        // 浏览器把焦点甩到 body，不再主动 focus 提示词，避免“没点输入框光标却进去”。
        onMouseDown={(e) => {
          if (e.button !== 0) return;
          const t = e.target as HTMLElement | null;
          if (!t) return;
          // 可聚焦的交互元素：按钮/输入/链接/各类浮层内的可点项 —— 一律放行原生行为
          const interactive = t.closest(
            'button, a, input, textarea, select, [contenteditable="true"], [tabindex]:not([tabindex="-1"])',
          );
          if (interactive) return;
          // 点的是空白：退出提示词编辑态并清掉文本选区。
          // 注意不是把焦点送进提示词，而是确保“点空白”不会留下光标。
          e.preventDefault();
          const active = document.activeElement;
          if (
            active instanceof HTMLElement
            && (active === taRef.current || active.closest('[contenteditable="true"]'))
          ) {
            active.blur();
          }
          const selection = window.getSelection();
          selection?.removeAllRanges();
        }}
        // 跟随节点：弹窗贴在选中卡片正下方（即梦是跟着节点走的，不是钉死在屏幕底部）。
        // 用 CSS 变量传坐标，实际夹紧逻辑在 CSS 里用 min()/max() 完成，避免这里算错视口。
        style={anchor ? ({
          '--jim-anchor-x': `${anchor.x}px`,
          '--jim-anchor-bottom': `${anchor.y + anchor.h}px`,
          '--jim-anchor-top': `${anchor.y}px`,
          '--jim-anchor-w': `${anchor.w}px`,
          '--jim-anchor-h': `${anchor.h}px`,
        } as CSSProperties) : undefined}
        data-anchored={anchor ? '1' : '0'}
        data-flip={popupMode === 'above' ? '1' : '0'}
        data-center={popupMode === 'center' ? '1' : '0'}
        /* 放大态 = 抬到最顶层居中的大浮层：不受卡片位置约束、也不被卡片遮挡。
           这是即梦/Figma 的通用做法 —— 长文本编辑时先给足屏幕空间。 */
        data-expanded={taExpanded ? '1' : '0'}
      >
        {/* 顶部细行：节点信息 + 放大/收起 + 更多菜单（删除节点）
            放大按钮放这里而不是单独占一行 —— 成熟编辑器（即梦/Figma）都把它收进顶栏。 */}
        <div className="jim-bar-head">
          <span className="jim-head-title" title={node.title}>{node.title || meta.label}</span>
          <span className={`jim-head-badge${overPromptLimit ? ' is-over-limit' : ''}`}>{isTxt ? `${(node.prompt || '').trim().length} / ${promptLimit} 字` : isAud ? (p.ttsMode === 'speech' ? `音色 ${p.voiceId || p.ttsVoice || '未设置'}` : '音色设计') : isVid ? `${ratioLabel} · ${resLabel}` : `${ratioLabel} · ${clarityLabel}`}</span>
          <div className="jim-head-tools">
            <button
              type="button"
              className={`jim-head-expand ${taExpanded ? 'is-on' : ''}`}
              title={taExpanded ? '收起输入框' : '放大输入框'}
              aria-label={taExpanded ? '收起输入框' : '放大输入框'}
              aria-pressed={taExpanded}
              onClick={() => {
                const next = !taExpanded;
                setTaExpanded(next);
                // React 状态异步：这里直接用目标值算高度，不等下一次渲染
                applyTaHeight(next);
              }}
            >
              {taExpanded
                ? <Minimize2 size={14} strokeWidth={1.9} aria-hidden="true" />
                : <Maximize2 size={14} strokeWidth={1.9} aria-hidden="true" />}
            </button>
            <button
              type="button"
              className={`jim-head-more-btn ${openMenu === 'more' ? 'is-on' : ''}`} data-pop-owner="more"
              onClick={() => toggle('more')}
              title="更多"
            ><MoreHorizontal size={15} strokeWidth={2} aria-hidden="true" /></button>
            {openMenu === 'more' && (
              <div className="jim-pop jim-pop-more">
                <button type="button" className="is-del" onClick={() => { onDelete(node.id); requestClose(); }}>删除节点</button>
              </div>
            )}
          </div>
        </div>

        {/* 提示词区 */}
        <div ref={promptAreaRef} className={`jim-prompt-area ${taExpanded ? 'is-expanded' : ''}`}>
          {/* 参考素材行（对标即梦）：已有引用缩略图 + 按类型的添加入口 + 「+」更多。
              全部用同尺寸扁平方块，不用虚线/实线描边 —— 旧版重边框看着像表单控件。 */}
          <div className="jim-ref-row">
            {refEntries.map((r, i) => (
              <span
                key={`${r.nodeId}-${i}`}
                className={`jim-ref-item ${draggedRefIndex === i ? 'is-dragging' : ''} ${refDropIndex === i && draggedRefIndex !== i ? 'is-drop-target' : ''}`}
                data-ref-index={i}
                onPointerDown={(event) => {
                  if (event.button !== 0 || (event.target as Element).closest('button')) return;
                  refPointerDragRef.current = {
                    from: i,
                    startX: event.clientX,
                    startY: event.clientY,
                    active: false,
                  };
                  setRefDragGhost(null);
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onPointerMove={(event) => {
                  const drag = refPointerDragRef.current;
                  if (!drag) return;
                  if (!drag.active && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 6) return;
                  drag.active = true;
                  setDraggedRefIndex(drag.from);
                  setRefDragGhost({
                    url: refVisualUrl(r),
                    label: refLabel(r),
                    role: refRoleLabel(r),
                    x: event.clientX,
                    y: event.clientY,
                  });
                  event.preventDefault();
                  const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('.jim-ref-item');
                  const targetIndex = Number(target?.dataset.refIndex);
                  setRefDropIndex(Number.isInteger(targetIndex) ? targetIndex : null);
                }}
                onPointerUp={(event) => {
                  const drag = refPointerDragRef.current;
                  refPointerDragRef.current = null;
                  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                    event.currentTarget.releasePointerCapture(event.pointerId);
                  }
                  if (!drag) return;
                  setRefDragGhost(null);
                  const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('.jim-ref-item');
                  const targetIndex = Number(target?.dataset.refIndex);
                  if (drag.active && Number.isInteger(targetIndex) && targetIndex !== drag.from) {
                    reorderRefs(drag.from, targetIndex);
                  } else {
                    setDraggedRefIndex(null);
                    setRefDropIndex(null);
                  }
                }}
                onPointerCancel={() => {
                  refPointerDragRef.current = null;
                  setDraggedRefIndex(null);
                  setRefDropIndex(null);
                  setRefDragGhost(null);
                }}
              >
                <span
                  className={`jim-ref-thumb is-${refKind(r)}${refKind(r) === 'audio' ? ' is-voice' : ''}`}
                  title={`参考槽 ${i + 1} · ${refRoleLabel(r)} · ${refLabel(r)}${refKind(r) === 'audio' ? '（音色）' : ''} · 拖动调整顺序`}
                  onPointerEnter={(event) => showAssetPreview(refVisualUrl(r), refLabel(r), event.currentTarget)}
                  onPointerLeave={() => setMentionPreview(null)}
                >
                  <em className="jim-ref-slot">{i + 1}</em>
                  {(refKind(r) === 'image' || refKind(r) === 'audio') && refVisualUrl(r) ? <img src={refVisualUrl(r)} alt="" loading="lazy" draggable={false} /> : (
                    // 源节点尚未生成媒体：显示类型图标占位（不显示"资产/角色"文字）
                    <i className="jim-ref-ph" aria-label={refLabel(r)}>
                      {refKind(r) === 'text' ? <Type size={15} strokeWidth={1.7} aria-hidden="true" />
                        : refKind(r) === 'audio' ? <AudioLines size={15} strokeWidth={1.7} aria-hidden="true" />
                        : refKind(r) === 'video' ? <Video size={15} strokeWidth={1.7} aria-hidden="true" />
                        : <ImageGlyph size={15} strokeWidth={1.7} aria-hidden="true" />}
                    </i>
                  )}
                  {refKind(r) === 'audio' && refVisualUrl(r) && (
                    <em className="jim-ref-audio-badge" aria-hidden="true">
                      <AudioLines size={9} strokeWidth={2} />
                    </em>
                  )}
                  <button type="button" title="移除引用" onClick={() => removeRef(r)}><X size={8} strokeWidth={2.6} aria-hidden="true" /></button>
                </span>
                <small className="jim-ref-role">{refRoleLabel(r)}</small>
              </span>
            ))}
            {/* 只有「+」一个添加入口。前面那些方块是**已有引用的类型标识**
                （按 refs 逐个渲染），不是固定的四个加号按钮 —— 
                固定摆一排只会让人以为"这里能加四类素材"。 */}
            <div className="jim-add-wrap" ref={addMenuRef}>
              <button
                type="button"
                className={`jim-add ${addMenuOpen ? 'is-open' : ''}`} data-pop-owner="add"
                title="更多添加方式"
                aria-label="更多添加方式"
                onClick={() => { setOpenMenu(null); setAtOpen(false); setAddMenuOpen((v) => !v); }}
              ><Plus size={16} strokeWidth={1.7} aria-hidden="true" /></button>
              {addMenuOpen && (
                <div className="jim-add-pop">
                  <button type="button" onClick={() => { setAddMenuOpen(false); fileInputRef.current?.click(); }}>
                    <Upload size={13} strokeWidth={1.9} aria-hidden="true" />上传图片
                  </button>
                  <button type="button" onClick={() => { setAddMenuOpen(false); audioInputRef.current?.click(); }}>
                    <AudioLines size={13} strokeWidth={1.9} aria-hidden="true" />上传音频
                  </button>
                  <button type="button" onClick={() => { setAddMenuOpen(false); videoInputRef.current?.click(); }}>
                    <Video size={13} strokeWidth={1.9} aria-hidden="true" />上传视频
                  </button>
                  <button type="button" onClick={() => { setAddMenuOpen(false); setAtOpen(true); setAtIdx(0); }}>
                    <LibraryBig size={13} strokeWidth={1.9} aria-hidden="true" />从资产库添加
                  </button>
                  <button type="button" onClick={() => { setAddMenuOpen(false); setCanvasPickOpen(true); }}>
                    <Spline size={13} strokeWidth={1.9} aria-hidden="true" />从画布选择
                  </button>
                </div>
              )}
            </div>
            {uploading && <span className="jim-add-busy">上传中…</span>}
            {refNotice && <span className="jim-add-busy">{refNotice}</span>}
          </div>
          {/* 即梦同款引导文案：告诉用户 @ 能干什么。
              ★ 现在直接写进输入框的 placeholder（见下方 textarea），不再单独占一行 ——
              单独一行会把提示词框往下压，长提示词一多弹窗就变成两屏（用户反馈）。 */}
          {/* 隐藏的 file input：图片/音频各一个。
              ★ 不用「点击前改 accept」的单 input 方案：React 的 attribute diff
                不会把被 JS 改过的 accept 复原，第二次点选会沿用上一次的类型。 */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            style={{display: 'none'}}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';    // 允许连续选同一个文件
              if (f) void addUploadedRef(f);
            }}
          />
          <input
            ref={audioInputRef}
            type="file"
            accept="audio/*"
            style={{display: 'none'}}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) void addUploadedRef(f);
            }}
          />
          <input
            ref={videoInputRef}
            type="file"
            accept="video/*"
            style={{display: 'none'}}
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) void addUploadedRef(f);
            }}
          />
          <div className="jim-prompt-row">
            <RichPromptEditor
              editorRef={taRef}
              value={node.prompt}
              mentions={refEntries.map((ref) => ({
                label: refLabel(ref),
                url: refVisualUrl(ref),
                kind: refKind(ref),
              }))}
              placeholder={promptPlaceholder}
              onChange={(next) => {
                const limited = next.slice(0, promptLimit);
                onChange(node.id, {prompt: limited});
                const editor = taRef.current;
                setAtOpen(Boolean(editor && promptCaretFollowsAt(editor, limited)));
                setAtIdx(0);
                growTa();
              }}
              onKeyDown={(event) => {
                if (atOpen && event.key === 'Escape') {
                  event.preventDefault();
                  setAtOpen(false);
                  setAtIdx(0);
                  return;
                }
                if (atOpen && atCands.length > 0) {
                  if (event.key === 'ArrowDown') { event.preventDefault(); setAtIdx((index) => Math.min(index + 1, atCands.length - 1)); }
                  else if (event.key === 'ArrowUp') { event.preventDefault(); setAtIdx((index) => Math.max(index - 1, 0)); }
                  else if (event.key === 'Enter') { event.preventDefault(); pickAt(atCands[atIdx]); }
                  return;
                }
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  if (
                    node.prompt.trim()
                    && !busy
                    && !overPromptLimit
                    && !referenceEvaluation.issue
                    && curModelSpec
                    && !missingProviderKey
                  ) {
                    generateAndClose();
                  }
                }
              }}
              onRendered={() => window.requestAnimationFrame(growTa)}
              onMentionPreview={setMentionPreview}
              onMentionRemoved={(labels) => {
                for (const label of labels) {
                  const ref = refEntries.find((item) => refLabel(item) === label);
                  if (ref) removeRef(ref);
                }
              }}
            />
            <div className="jim-prompt-side">
              <button type="button" className="is-clear" title="清空提示词" onClick={() => onChange(node.id, {prompt: ''})}>
                <Eraser size={14} strokeWidth={1.7} aria-hidden="true" />
              </button>
              {/* ★ 只有该类型配了模板才渲染：文本节点原来没模板，按钮悬在那里点不动（死按钮） */}
              {templates.length > 0 && (
                <button
                  type="button"
                  title="随机灵感"
                  onClick={() => {
                    const list = PROMPT_TEMPLATES[node.kind];
                    if (list?.length) insertTemplate(list[Math.floor(Math.random() * list.length)].text);
                  }}
                ><Sparkles size={15} strokeWidth={1.9} aria-hidden="true" /></button>
              )}
              {isVid && (
                <button
                  type="button"
                  className={`is-style ${openMenu === 'style' ? 'is-active' : ''}`}
                  data-pop-owner="style"
                  title="推理强度"
                  onClick={() => toggle('style')}
                >
                  <Brain size={14} strokeWidth={1.9} aria-hidden="true" />
                </button>
              )}
              {extraParamSpecs.length > 0 && (
                <button
                  type="button"
                  className={`is-extra-params ${openMenu === 'advanced' ? 'is-active' : ''}`}
                  data-pop-owner="advanced"
                  title={`高级参数（${extraParamSpecs.length}）`}
                  onClick={() => toggle('advanced')}
                >
                  <SlidersHorizontal size={14} strokeWidth={1.9} aria-hidden="true" />
                  <i className="jim-side-count">{extraParamSpecs.length}</i>
                </button>
              )}
            </div>
          </div>
          {atOpen && (
            <div
              className="jim-at-pop"
              style={{bottom: atPopBottom, maxHeight: atPopMaxHeight}}
            >
              <div className="jim-at-title">
                <span>@ 引用素材（点击插入）</span>
                <span className="jim-at-kbd">↑↓ 选择 · 回车 插入 · Esc 关闭</span>
              </div>
              <div className="jim-at-list">
              {atCands.length === 0 && (
                <div className="jim-pick-empty">当前画布没有可引用的素材</div>
              )}
              {atCands.map((a, i) => (
                <button
                  key={a.id}
                  type="button"
                  className={`jim-at-item ${i === atIdx ? 'is-active' : ''}`}
                  onMouseEnter={() => setAtIdx(i)}
                  onPointerEnter={(event) => {
                    const url = a.kind === 'audio' ? portraitForAudioNode(a) : a.kind === 'image' ? (a.mediaUrl || '') : '';
                    showAssetPreview(url, a.title || a.kind, event.currentTarget);
                  }}
                  onPointerLeave={() => setMentionPreview(null)}
                  onClick={() => pickAt(a)}
                >
                  {(a.kind === 'image' && a.mediaUrl) || (a.kind === 'audio' && portraitForAudioNode(a))
                    ? <img src={a.kind === 'audio' ? portraitForAudioNode(a) : a.mediaUrl} alt="" loading="lazy" />
                    : <span className={`jim-at-ico ${a.kind === 'audio' ? 'is-audio' : ''}`}>
                        {a.kind === 'audio'
                          ? <Music size={13} strokeWidth={1.9} aria-hidden="true" />
                          : a.kind === 'video'
                            ? <Video size={13} strokeWidth={1.9} aria-hidden="true" />
                            : a.kind === 'text'
                              ? <Type size={13} strokeWidth={1.9} aria-hidden="true" />
                              : <ImageGlyph size={13} strokeWidth={1.9} aria-hidden="true" />}
                      </span>}
                  <span className="jim-at-name">{a.title || a.kind}</span>
                  {a.status === 'done' && <span className="jim-at-flag">已生成</span>}
                </button>
              ))}
              </div>
            </div>
          )}
          {/* 从画布选择：列出画布上可作参考的其它卡片（图片/音频），点一下即加为引用 */}
          {canvasPickOpen && (
            <div className="jim-at-pop jim-pick-pop">
              <div className="jim-at-title">
                <span>从画布选择（{pickCands.length} 张）</span>
                <span className="jim-at-kbd">Esc 关闭</span>
              </div>
              <div className="jim-at-list">
              {pickCands.length === 0 && (
                <div className="jim-pick-empty">画布上还没有其它可引用的卡片</div>
              )}
              {pickCands.map((a) => {
                const picked = refs.some((r) => r.nodeId === a.id) || voiceRefList(p).some((r) => r.nodeId === a.id);
                return (
                  <button
                    key={a.id}
                    type="button"
                    className={`jim-at-item ${picked ? 'is-picked' : ''}`}
                    disabled={picked}
                    onPointerEnter={(event) => {
                      const url = a.kind === 'audio' ? portraitForAudioNode(a) : a.kind === 'image' ? (a.mediaUrl || '') : '';
                      showAssetPreview(url, a.title || a.kind, event.currentTarget);
                    }}
                    onPointerLeave={() => setMentionPreview(null)}
                    onClick={() => { onPickRef?.(node, a.id); setCanvasPickOpen(false); }}
                  >
                    {(a.kind === 'image' && a.mediaUrl) || (a.kind === 'audio' && portraitForAudioNode(a))
                      ? <img src={a.kind === 'audio' ? portraitForAudioNode(a) : a.mediaUrl} alt="" loading="lazy" />
                      : <span className={`jim-at-ico is-${a.kind}`}>
                          {a.kind === 'audio' ? <AudioLines size={13} strokeWidth={1.9} aria-hidden="true" />
                            : a.kind === 'video' ? <Video size={13} strokeWidth={1.9} aria-hidden="true" />
                            : a.kind === 'text' ? <Type size={13} strokeWidth={1.9} aria-hidden="true" />
                            : <ImageGlyph size={13} strokeWidth={1.9} aria-hidden="true" />}
                        </span>}
                    <span className="jim-at-name">{a.title || a.kind}</span>
                    {picked && <span className="jim-at-flag is-dim">已引用</span>}
                  </button>
                );
              })}
              </div>
            </div>
          )}
          {nodeNotices.length > 0 && (
            <div className="jim-validation-notes" role="status">
              {nodeNotices.map((notice) => (
                <div key={notice} title={notice}>
                  <CircleAlert size={12} strokeWidth={1.9} aria-hidden="true" />
                  <span>{notice}</span>
                </div>
              ))}
            </div>
          )}
          {dependenciesBlocked && (
            <div className="jim-dependency-prepare">
              {!prepareOpen ? (
                <button
                  type="button"
                  className="jim-dependency-prepare-trigger"
                  disabled={prepareTargets.length === 0}
                  onClick={() => setPrepareOpen(true)}
                >
                  <Wand2 size={12} strokeWidth={1.9} aria-hidden="true" />
                  <span>{prepareTargets.length > 0 ? `准备缺失资产（${prepareTargets.length}）` : '缺失依赖需手动处理'}</span>
                </button>
              ) : (
                <div className="jim-dependency-prepare-panel">
                  <div className="jim-dependency-prepare-head">
                    <strong>选择要准备的资产</strong>
                    <span>默认不勾选，不会自动提交付费任务</span>
                  </div>
                  <div className="jim-dependency-prepare-list">
                    {prepareTargets.map((item) => {
                      const checked = prepareSelected.includes(item.source.id);
                      return (
                        <button
                          key={item.source.id}
                          type="button"
                          className={`jim-dependency-prepare-item${checked ? ' is-checked' : ''}`}
                          role="checkbox"
                          aria-checked={checked}
                          disabled={!item.spec}
                          onClick={() => {
                            setPrepareMessage('');
                            setPrepareSelected((prev) => checked
                              ? prev.filter((id) => id !== item.source.id)
                              : [...new Set([...prev, item.source.id])]);
                          }}
                        >
                          <SvgCheckMark checked={checked} />
                          <span className="jim-dependency-prepare-copy">
                            <b>{item.source.title}</b>
                            <small>{item.spec?.label || '未配置可用模型'} · {item.costLabel}</small>
                          </span>
                        </button>
                      );
                    })}
                  </div>
                  <div className="jim-dependency-prepare-foot">
                    <span>
                      已选 {selectedPrepareTargets.length} 项
                      {selectedPrepareTargets.length > 0 && (
                        <> · 预估 {selectedUnknownCost ? '按供应商账单' : `≈¥${selectedKnownCost.toFixed(selectedKnownCost < 1 ? 3 : 2)}`}</>
                      )}
                    </span>
                    <div>
                      <button type="button" onClick={() => { setPrepareOpen(false); setPrepareMessage(''); }}>取消</button>
                      <button
                        type="button"
                        className="is-primary"
                        disabled={selectedPrepareTargets.length === 0}
                        onClick={() => {
                          const started: string[] = [];
                          for (const item of selectedPrepareTargets) {
                            if (onGenerate(item.source)) started.push(item.source.id);
                          }
                          setPrepareSelected((prev) => prev.filter((id) => !started.includes(id)));
                          setPrepareMessage(started.length > 0 ? `已提交 ${started.length} 项所选资产` : '没有提交任务');
                        }}
                      >
                        只生成选中项
                      </button>
                    </div>
                  </div>
                  {prepareMessage && <p className="jim-dependency-prepare-message">{prepareMessage}</p>}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 工具栏：＋ @ 风格 ｜ 模型 比例 清晰度/分辨率/时长 ｜ ✂ 费用 ↑ */}
        <div className="jim-toolbar">
          <div className="jim-toolbar-left">
            {templates.length > 0 && (
              <div className="jim-tpl-wrap">
                <button type="button" className={`jim-tool ${openMenu === 'tpl' ? 'is-open' : ''}`} data-pop-owner="tpl" onClick={() => toggle('tpl')} title="快捷模板"><Plus size={13} strokeWidth={2.3} aria-hidden="true" /></button>
                {openMenu === 'tpl' && (
                  <div className="jim-pop jim-pop-tpl">
                    {templates.map((t) => (
                      <button key={t.label} type="button" onClick={() => insertTemplate(t.text)}>
                        <strong>{t.label}</strong>
                        <span>{t.text.replace(/^，/, '').slice(0, 26)}…</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <button ref={atButtonRef} type="button" className={`jim-tool is-at ${atOpen ? 'is-open' : ''}`} data-pop-owner="at" onClick={() => { setOpenMenu(null); setAtOpen((v) => !v); setAtIdx(0); }} title="@ 引用素材"><AtSign size={13} strokeWidth={2.1} aria-hidden="true" /></button>
          </div>
          <div className="jim-toolbar-right">
          {modelOptions.length > 0 && (
            <button type="button" className={`jim-tool is-model ${openMenu === 'model' ? 'is-open' : ''}`} data-pop-owner="model" onClick={() => toggle('model')} title={curModelSpec?.label || curModel}>
              <i className="jim-model-dot" style={{background: MODEL_META[curModel]?.tone || '#4d9fff'}} />
              {curModelSpec?.label || MODEL_META[curModel]?.label || curModel}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" />
            </button>
          )}
          {isAud ? (
            <>
              <button type="button" className={`jim-tool ${openMenu === 'ttsmode' ? 'is-open' : ''}`} data-pop-owner="ttsmode" onClick={() => toggle('ttsmode')} title="音色设计 / 指定音色合成">
                {(p.ttsMode || 'design') === 'design' ? '音色设计' : '指定音色'}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" />
              </button>
              {(p.ttsMode || 'design') === 'speech' && (
                <label className="jim-tool is-input" title="百炼音色 ID">音色<input value={p.voiceId || p.ttsVoice || ''} placeholder="longxiaochun…" onChange={(e) => patch('voiceId', e.target.value)} /></label>
              )}
              <button type="button" className={`jim-tool ${openMenu === 'ttsformat' ? 'is-open' : ''}`} data-pop-owner="ttsformat" onClick={() => toggle('ttsformat')} title="音频格式">
                {String(p.outputFormat || 'mp3').toUpperCase()}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" />
              </button>
            </>
          ) : isTxt ? (
            <button type="button" className={`jim-tool ${openMenu === 'len' ? 'is-open' : ''}`} data-pop-owner="len" onClick={() => toggle('len')} title="生成长度">{lenLabel}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" /></button>
          ) : isVid ? (
            <>
              <button type="button" className={`jim-tool is-params ${openMenu === 'params' ? 'is-open' : ''}`} data-pop-owner="params" onClick={() => toggle('params')} title="比例 / 分辨率 / 数量">
                <SlidersHorizontal size={13} strokeWidth={1.9} aria-hidden="true" />
                <span>{paramSummary}</span>
                <ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" />
              </button>
              <button type="button" className={`jim-tool is-duration ${openMenu === 'dur' ? 'is-open' : ''}`} data-pop-owner="dur" onClick={() => toggle('dur')} title="时长">{durLabel}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" /></button>
            </>
          ) : (
            <>
              <button type="button" className={`jim-tool ${openMenu === 'ratio' ? 'is-open' : ''}`} data-pop-owner="ratio" onClick={() => toggle('ratio')} title="画幅比例">{ratioLabel}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" /></button>
              <button type="button" className={`jim-tool ${openMenu === 'clarity' ? 'is-open' : ''}`} data-pop-owner="clarity" onClick={() => toggle('clarity')} title="清晰度 / 推理强度">{clarityLabel}<ChevronDown size={11} strokeWidth={2.4} className="jim-caret" aria-hidden="true" /></button>
            </>
          )}
          <span className="jim-tool-spacer" />
          <span className="jim-cost" title={modelPricingNote(curModelSpec, hasVideoInput)}>{generationCostHint(node, curModelSpec, {hasVideoInput})}</span>
          {/* ★ 文本节点也能 AI 生成（用户要求）：模型 + 生成按钮对所有类型开放。
              文本卡生成的是「正文」—— 先按提示写意图，生成结果覆盖卡片内容。 */}
          <button
            type="button"
            className="jim-gen"
            disabled={busy || !node.prompt.trim() || overPromptLimit || Boolean(referenceEvaluation.issue) || !curModelSpec || missingProviderKey || dependenciesBlocked || modelParamIssues.length > 0}
            onClick={generateAndClose}
            title={busy ? '当前节点正在生成，请到任务中心查看进度' : !curModelSpec ? '当前类型没有可用模型' : missingProviderKey ? '当前供应商尚未配置 API Key' : overPromptLimit ? `提示词超过当前模型上限（${promptLimit} 字符）` : referenceEvaluation.issue ? referenceEvaluation.message : modelParamIssues.length ? modelParamIssues[0].message : dependenciesBlocked ? `缺少：${dependencyBlockers.map((item) => `${item.title}（${item.reason}）`).join('；')}` : isTxt ? '按当前内容生成/改写文本 · Ctrl+⏎' : '按当前供应商与参数生成 · Ctrl+⏎'}
          ><ArrowUp size={16} strokeWidth={2.6} aria-hidden="true" /></button>
          </div>
        </div>

        {/* ---- 下拉面板（统一向上弹出） ---- */}
        {openMenu === 'model' && (
          <div className="jim-pop jim-pop-model">
            <div className="jim-model-search">
              <Search size={12} strokeWidth={1.9} aria-hidden="true" />
              <input
                value={modelSearch}
                onChange={(event) => setModelSearch(event.target.value)}
                placeholder={`搜索模型或供应商（${visibleModelOptions.length}/${modelOptions.length}）`}
                aria-label="搜索模型"
                autoFocus
              />
              {modelSearch && <button type="button" onClick={() => setModelSearch('')} aria-label="清空模型搜索"><X size={10} /></button>}
            </div>
            <div className="jim-model-list">
              {visibleModelOptions.map((m) => {
                const favorite = favoriteModelIds.includes(m.id);
                return (
                  <div key={m.id} className="jim-model-row">
                    <button type="button" className={`jim-model-item ${curModel === m.id ? 'is-active' : ''}`} onClick={() => selectModel(m)}>
                      <span className="jim-model-name">
                        {favorite && <Star size={10} fill="currentColor" aria-hidden="true" />}
                        {m.label || MODEL_META[m.id]?.label || m.id}
                        <i className="jim-model-badge">{m.badge || MODEL_META[m.id]?.badge || 'M'}</i>
                      </span>
                      <span className="jim-model-desc">{m.description || MODEL_META[m.id]?.desc || m.remoteModel}</span>
                    </button>
                    <button
                      type="button"
                      className={`jim-model-favorite ${favorite ? 'is-active' : ''}`}
                      title={favorite ? '取消收藏' : '收藏模型'}
                      aria-label={favorite ? '取消收藏模型' : '收藏模型'}
                      onClick={() => {
                        const next = favorite ? favoriteModelIds.filter((id) => id !== m.id) : [m.id, ...favoriteModelIds];
                        setFavoriteModelIds(next);
                        writeStoredStringArray(MODEL_FAVORITES_KEY, next);
                      }}
                    >
                      <Star size={11} fill={favorite ? 'currentColor' : 'none'} strokeWidth={1.8} aria-hidden="true" />
                    </button>
                  </div>
                );
              })}
            </div>
            {visibleModelOptions.length === 0 && <div className="jim-model-empty">没有匹配模型</div>}
          </div>
        )}
        {openMenu === 'params' && ReactDOM.createPortal(
          <div
            ref={paramsPanelRef}
            className="jim-pop jim-params-panel"
            style={{
              position: 'fixed',
              left: paramsPanelPos?.left ?? -9999,
              top: paramsPanelPos?.top ?? -9999,
              bottom: 'auto',
              visibility: paramsPanelPos ? 'visible' : 'hidden',
            }}
          >
            {!isVid && (
              <div className="jim-params-head">
                <strong>生成参数</strong>
                <span title={modelPricingNote(curModelSpec, hasVideoInput)}>{curModelSpec?.label || curModel || meta.label} · {generationCostHint(node, curModelSpec, {hasVideoInput})}</span>
              </div>
            )}

            <div className="jim-params-scroll">
            {isVid ? (
              <VideoParamsPanel
                params={p}
                ratioOptions={ratioOptions}
                resolutionOptions={resolutionOptions}
                countOptions={countOptions}
                ratioLabel={ratioLabel}
                resolutionLabel={resLabel}
                countLabel={countLabel}
                onPatch={patch}
              />
            ) : (
            <>
              <div className="jim-params-compact-grid">
            {!isAud && !isTxt && (
              <section className="jim-param-section jim-param-ratio-section">
                <div className="jim-param-section-title"><span>画幅比例</span><em>{ratioLabel}</em></div>
                <div
                  className="jim-ratio-grid jim-params-ratio-grid"
                  style={{
                    gridTemplateColumns: `repeat(${Math.max(1, ratioOptions.length)}, minmax(0, 1fr))`,
                  }}
                >
                  {ratioOptions.map((r) => (
                    <button
                      key={r}
                      type="button"
                      className={`jim-ratio-item ${ratioLabel === r ? 'is-active' : ''}`}
                      onClick={() => {
                        patch('ratio', r);
                        if (isImg) patch('size', CLARITY_SIZES[clarityLabel]?.[r] || '1536x1536');
                      }}
                    >
                      <RatioIcon r={r} />
                      <span>{r}</span>
                    </button>
                  ))}
                </div>
              </section>
            )}

            {isImg && (
              <>
                <section className="jim-param-section">
                  <div className="jim-param-section-title"><span>清晰度</span><em>{clarityLabel}</em></div>
                  <div className="jim-chip-row">
                    {clarityOptions.map((c) => (
                      <button
                        key={c}
                        type="button"
                        className={`jim-chip ${clarityLabel === c ? 'is-active' : ''}`}
                        onClick={() => {
                          patch('clarity', c);
                          patch('size', CLARITY_SIZES[c]?.[ratioLabel] || '1536x1536');
                        }}
                      >{c}</button>
                    ))}
                  </div>
                </section>
                {schema.guidance && (
                  <section className="jim-param-section">
                    <div className="jim-param-section-title"><span>推理强度</span><em>{guidanceLabel}</em></div>
                    <div className="jim-chip-row">
                      {guidanceOptions.map((g) => (
                        <button key={g} type="button" className={`jim-chip ${guidanceLabel === g ? 'is-active' : ''}`} onClick={() => patch('guidance', g)}>{g}</button>
                      ))}
                    </div>
                  </section>
                )}
                {schema.watermark && (
                  <section className="jim-param-section">
                    <div className="jim-param-section-title"><span>水印</span></div>
                    <div className="jim-chip-row">
                      <button type="button" className={`jim-chip ${p.watermark ? 'is-active' : ''}`} onClick={() => patch('watermark', true)}>开启</button>
                      <button type="button" className={`jim-chip ${!p.watermark ? 'is-active' : ''}`} onClick={() => patch('watermark', false)}>关闭</button>
                    </div>
                  </section>
                )}
              </>
            )}

            {isAud && (
              <>
                <section className="jim-param-section">
                  <div className="jim-param-section-title"><span>音色模式</span><em>{(p.ttsMode || 'design') === 'design' ? '音色设计' : '指定音色'}</em></div>
                  <div className="jim-chip-row">
                    <button type="button" className={`jim-chip ${(p.ttsMode || 'design') === 'design' ? 'is-active' : ''}`} onClick={() => patch('ttsMode', 'design')}>音色设计</button>
                    <button type="button" className={`jim-chip ${p.ttsMode === 'speech' ? 'is-active' : ''}`} onClick={() => patch('ttsMode', 'speech')}>指定音色</button>
                  </div>
                  {(p.ttsMode || 'design') === 'speech' && (
                    <label className="jim-param-control">
                      <span className="jim-param-label">音色 ID</span>
                      <input className="jim-param-input" value={p.voiceId || p.ttsVoice || ''} placeholder="longxiaochun…" onChange={(e) => patch('voiceId', e.target.value)} />
                    </label>
                  )}
                </section>
                <section className="jim-param-section">
                  <div className="jim-param-section-title"><span>输出格式</span><em>{String(p.outputFormat || 'mp3').toUpperCase()}</em></div>
                  <div className="jim-chip-row">
                    {['mp3', 'wav'].map((format) => (
                      <button key={format} type="button" className={`jim-chip ${(p.outputFormat || 'mp3') === format ? 'is-active' : ''}`} onClick={() => patch('outputFormat', format)}>{format.toUpperCase()}</button>
                    ))}
                  </div>
                </section>
                <section className="jim-param-section">
                  <div className="jim-param-section-title"><span>采样率</span><em>{audioRateLabel(String(p.sampleRate || 24000))}</em></div>
                  <div className="jim-chip-row">
                    {[16000, 22050, 24000, 44100, 48000].map((rate) => (
                      <button key={rate} type="button" className={`jim-chip ${Number(p.sampleRate || 24000) === rate ? 'is-active' : ''}`} onClick={() => patch('sampleRate', rate)}>{rate}</button>
                    ))}
                  </div>
                </section>
              </>
            )}

            {isTxt && (
              <>
                <section className="jim-param-section">
                  <div className="jim-param-section-title"><span>生成长度</span><em>{lenLabel}</em></div>
                  <div className="jim-chip-row">
                    {TEXT_LEN_OPTS.map((o) => (
                      <button key={o.label} type="button" className={`jim-chip ${curLen.tokens === o.tokens ? 'is-active' : ''}`} onClick={() => patch('maxTokens', o.tokens)}>{o.label}</button>
                    ))}
                  </div>
                  <p className="jim-pop-note">当前约 {curLen.tokens} token ≈ {Math.round(curLen.tokens * 1.5)} 汉字</p>
                </section>
                <section className="jim-param-section">
                  <div className="jim-param-section-title"><span>采样温度</span><em>{Number(p.temperature ?? 0.7).toFixed(1)}</em></div>
                  <div className="jim-dur-row">
                    <input type="range" min={0} max={2} step={0.1} value={Number(p.temperature ?? 0.7)} onChange={(event) => patch('temperature', Number(event.target.value))} />
                  </div>
                </section>
              </>
            )}
              </div>
            </>
            )}
            </div>

          </div>,
          document.body,
        )}
        {openMenu === 'ttsmode' && (
          <div className="jim-pop">
            <div className="jim-pop-title">音色生成方式</div>
            <button
              type="button"
              className={`jim-model-item ${(p.ttsMode || 'design') === 'design' ? 'is-active' : ''}`}
              onClick={() => { patch('ttsMode', 'design'); setOpenMenu(null); }}
            >
              <span className="jim-model-name">音色设计<i className="jim-model-badge">设计</i></span>
              <span className="jim-model-desc">提示词写音色特征，由百炼创建并返回试听音频</span>
            </button>
            <button
              type="button"
              className={`jim-model-item ${p.ttsMode === 'speech' ? 'is-active' : ''}`}
              onClick={() => { patch('ttsMode', 'speech'); setOpenMenu(null); }}
            >
              <span className="jim-model-name">指定音色合成<i className="jim-model-badge">语音</i></span>
              <span className="jim-model-desc">使用已创建的音色 ID 合成提示词中的台词</span>
            </button>
          </div>
        )}
        {openMenu === 'ttsformat' && (
          <div className="jim-pop">
            <div className="jim-pop-title">音频输出参数</div>
            <div className="jim-pop-title">格式</div>
            <div className="jim-chip-row">
              {['mp3', 'wav'].map((format) => (
                <button key={format} type="button" className={`jim-chip ${(p.outputFormat || 'mp3') === format ? 'is-active' : ''}`} onClick={() => patch('outputFormat', format)}>{format.toUpperCase()}</button>
              ))}
            </div>
            <div className="jim-pop-title">采样率</div>
            <div className="jim-chip-row">
              {[16000, 22050, 24000, 44100, 48000].map((rate) => (
                <button key={rate} type="button" className={`jim-chip ${Number(p.sampleRate || 24000) === rate ? 'is-active' : ''}`} onClick={() => patch('sampleRate', rate)}>{rate}</button>
              ))}
            </div>
          </div>
        )}
        {openMenu === 'advanced' && (
          <div className="jim-pop jim-pop-clarity">
            <div className="jim-pop-title">模型自定义参数</div>
            {renderExtraParamControls()}
          </div>
        )}
        {openMenu === 'ratio' && (
          <div className="jim-pop jim-pop-ratio">
            <div className="jim-pop-title">画幅比例</div>
            <div className="jim-ratio-grid">
              {ratioOptions.map((r) => (
                <button
                  key={r}
                  type="button"
                  className={`jim-ratio-item ${ratioLabel === r ? 'is-active' : ''}`}
                  onClick={() => { patch('ratio', r); if (isImg) patch('size', CLARITY_SIZES[clarityLabel]?.[r] || '1536x1536'); setOpenMenu(null); }}
                >
                  <RatioIcon r={r} />
                  <span>{r}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {openMenu === 'clarity' && (
          <div className="jim-pop jim-pop-clarity">
            <div className="jim-pop-title">清晰度</div>
            <div className="jim-chip-row">
              {clarityOptions.map((c) => (
                <button key={c} type="button" className={`jim-chip ${clarityLabel === c ? 'is-active' : ''}`} onClick={() => { patch('clarity', c); patch('size', CLARITY_SIZES[c]?.[ratioLabel] || '1536x1536'); }}>{c}</button>
              ))}
            </div>
            {schema.guidance && (
              <>
                <div className="jim-pop-title">推理强度</div>
                <div className="jim-chip-row">
                  {guidanceOptions.map((g) => (
                    <button key={g} type="button" className={`jim-chip ${guidanceLabel === g ? 'is-active' : ''}`} onClick={() => patch('guidance', g)}>{g}</button>
                  ))}
                </div>
              </>
            )}
            {schema.watermark && (
              <>
                <div className="jim-pop-title">水印</div>
                <div className="jim-chip-row">
                  <button type="button" className={`jim-chip ${p.watermark ? 'is-active' : ''}`} onClick={() => patch('watermark', true)}>开启</button>
                  <button type="button" className={`jim-chip ${!p.watermark ? 'is-active' : ''}`} onClick={() => patch('watermark', false)}>关闭</button>
                </div>
              </>
            )}
          </div>
        )}
        {openMenu === 'style' && (
          <div className="jim-pop jim-pop-style">
            <div className="jim-pop-title">推理强度</div>
            <div className="jim-chip-row">
              {guidanceOptions.map((g) => (
                <button key={g} type="button" className={`jim-chip ${guidanceLabel === g ? 'is-active' : ''}`} onClick={() => { patch('guidance', g); setOpenMenu(null); }}>{g}</button>
              ))}
            </div>
          </div>
        )}
        {openMenu === 'res' && (
          <div className="jim-pop jim-pop-res">
            <div className="jim-pop-title">分辨率</div>
            {resolutionOptions.map((r) => (
              <button key={r} type="button" className={`jim-radio-item ${resLabel === r ? 'is-active' : ''}`} onClick={() => { patch('resolution', r); setOpenMenu(null); }}><i />{r}</button>
            ))}
          </div>
        )}
        {openMenu === 'dur' && (
          <div className="jim-pop jim-pop-dur jim-dur-panel">
            <div className="jim-dur-head">
              <span>选择视频生成时长</span>
            </div>
            <div className="jim-dur-main">
              <div className="jim-dur-control">
                <div className="jim-dur-track">
                  <input
                    type="range"
                    min={0}
                    max={durationMax}
                    step={durationStep}
                    value={p.duration ?? curModelSpec?.defaultParams?.duration ?? Math.min(5, durationMax)}
                    style={{
                      '--jim-dur-progress': `${Math.max(0, Math.min(100,
                        (Number(p.duration ?? curModelSpec?.defaultParams?.duration ?? 5)
                          / Math.max(1, durationMax)) * 100))}%`,
                    } as CSSProperties}
                    onChange={(e) => patch('duration', Math.max(durationMin, Number(e.target.value)))}
                  />
                  <span className="jim-dur-track-ticks" aria-hidden="true">
                    {durationTicks.map((tick) => (
                      <i key={tick} style={{left: `${durationTickPercent(tick)}%`}} />
                    ))}
                  </span>
                </div>
                <div className="jim-dur-ticks">
                  {durationTicks.map((tick) => (
                    <span
                      key={tick}
                      style={{left: `${durationTickPercent(tick)}%`}}
                    >{tick}</span>
                  ))}
                </div>
              </div>
              <label className="jim-dur-number-box">
                <input
                  type="number"
                  min={durationMin}
                  max={durationMax}
                  step={durationStep}
                  value={p.duration ?? curModelSpec?.defaultParams?.duration ?? Math.min(5, durationMax)}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    if (Number.isFinite(value)) patch('duration', Math.max(durationMin, Math.min(durationMax, value)));
                  }}
                />
                <span>S</span>
              </label>
            </div>
            <div className="jim-dur-settings">
              {schema.generateAudio && (
                <div className="jim-dur-extra">
                  <span>生成音频</span>
                  <div className="jim-segment">
                    <button type="button" className={p.generateAudio !== false ? 'is-active' : ''} onClick={() => patch('generateAudio', true)}>开启</button>
                    <button type="button" className={p.generateAudio === false ? 'is-active' : ''} onClick={() => patch('generateAudio', false)}>关闭</button>
                  </div>
                </div>
              )}
              {schema.watermark && (
                <div className="jim-dur-extra">
                  <span>水印</span>
                  <div className="jim-segment">
                    <button type="button" className={p.watermark ? 'is-active' : ''} onClick={() => patch('watermark', true)}>开启</button>
                    <button type="button" className={!p.watermark ? 'is-active' : ''} onClick={() => patch('watermark', false)}>关闭</button>
                  </div>
                </div>
              )}
              {schema.seed && (
                <label className="jim-dur-extra is-seed">
                  <span>随机种子</span>
                  <input
                    type="number"
                    value={p.seed ?? ''}
                    placeholder="留空随机"
                    onChange={(event) => patch('seed', event.target.value === '' ? undefined : Number(event.target.value))}
                  />
                </label>
              )}
            </div>
          </div>
        )}
        {openMenu === 'len' && (
          <div className="jim-pop jim-pop-len">
            <div className="jim-pop-title">生成长度</div>
            <div className="jim-chip-row">
              {TEXT_LEN_OPTS.map((o) => (
                <button
                  key={o.label}
                  type="button"
                  className={`jim-chip ${curLen.tokens === o.tokens ? 'is-active' : ''}`}
                  onClick={() => patch('maxTokens', o.tokens)}
                >{o.label}</button>
              ))}
            </div>
            <p className="jim-pop-note">当前约 {curLen.tokens} token ≈ {Math.round(curLen.tokens * 1.5)} 汉字</p>
            <div className="jim-pop-title">采样温度</div>
            <div className="jim-dur-row">
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={Number(p.temperature ?? 0.7)}
                onChange={(event) => patch('temperature', Number(event.target.value))}
              />
              <span className="jim-dur-val">{Number(p.temperature ?? 0.7).toFixed(1)}</span>
            </div>
          </div>
        )}
      </div>
      {mentionPreview && (
        <div
          className="jim-mention-preview"
          style={{
            left: mentionPreview.left,
            ...(mentionPreview.top !== undefined ? {top: mentionPreview.top} : {bottom: mentionPreview.bottom}),
          }}
        >
          <img src={mentionPreview.url} alt={mentionPreview.label} />
          <span>{mentionPreview.label}</span>
        </div>
      )}
    </div>,
    document.body
  );
}

const nodeTypes = {
  shot: ShotNode,
  segment: SegmentNode,
  'asset-col': AssetColNode,
  'asset-group': AssetGroupNode,
  'asset-card': AssetCardNode,
  'ep-box': EpBoxNode,
  char: (props: NodeProps<FlowNode<AssetNodeData>>) => <AssetNode {...props} kind="char" />,
  scene: (props: NodeProps<FlowNode<AssetNodeData>>) => <AssetNode {...props} kind="scene" />,
  prop: (props: NodeProps<FlowNode<AssetNodeData>>) => <AssetNode {...props} kind="prop" />,
  voice: (props: NodeProps<FlowNode<AssetNodeData>>) => <AssetNode {...props} kind="voice" />,
  note: NoteNode,
  media: MediaNode,
};

type ContextMenuState = {
  x: number;
  y: number;
  nodeId: string | null;
  nodeType: string | null;
  // 多选批量菜单：右键命中「当前已选中的节点」且画布上有多选时打开，
  // 菜单内提供 整理…（弹窗）/ 删除…（弹窗）/ 复制 / 适应所选 / 取消选择。
  multi?: boolean;
  // 参与批量操作的可批处理节点 id（媒体卡 media-* + 备注 note-*）
  selIds?: string[];
};

// 画布图例（工业级）：节点/连线/状态语义说明，右下角固定、可折叠
// P0-10 工业化视频组弹窗：点击镜头/视频组 → 弹出「视频提示词 + 参考资产引用」窗口。
// 对齐 AniShort 提示词面板：左分镜脚本信息 + 参考图身份（@图片N）+ 完整提示词 + 复制/关闭。
// 段级提示词弹窗：回看本段渲染用的完整提示词（资产绑定/机位/时间线/光线/禁项/固定块）
function SegmentPromptModal({segment, onClose}: {segment: CanvasSegment; onClose: () => void}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [copied, setCopied] = useState(false);
  const fields: [string, string | null | undefined][] = [
    ['段标题', segment.title],
    ['资产绑定', segment.asset_binding],
    ['入场状态', segment.entry_state],
    ['动作时间线', segment.change_timeline],
    ['出场状态', segment.exit_state],
    ['末帧状态', segment.end_state],
    ['机位与轴线', segment.staging_and_axis],
    ['群演调度', segment.group_bridge],
    ['继承规则', segment.inheritance_rules],
    ['光线与美术', segment.style_lighting],
    ['全段固定块', segment.fixed_block],
    ['禁项', segment.forbidden],
  ];
  const plainText = fields.filter(([, v]) => v).map(([k, v]) => `【${k}】\n${v}`).join('\n\n');
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(plainText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* ignore */ }
  };
  return (
    <div className="canvas-prompt-overlay" onClick={onClose}>
      <div ref={dialogRef} tabIndex={-1} className="canvas-prompt-modal canvas-prompt-modal--seg" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="段提示词">
        <header className="canvas-prompt-header">
          <div>
            <strong>段提示词 · Scene{segment.segment_id}</strong>
            <span className="canvas-prompt-sub">{segment.duration !== null ? `${segment.duration}s` : ''}</span>
          </div>
          <div className="canvas-prompt-actions">
            <button type="button" onClick={copy}>{copied ? <><Check size={12} strokeWidth={2.5} aria-hidden="true" />已复制</> : '复制全部'}</button>
            <button type="button" className="canvas-prompt-close" onClick={onClose} aria-label="关闭"><X size={16} strokeWidth={2} aria-hidden="true" /></button>
          </div>
        </header>
        <div className="canvas-seg-prompt-body">
          {fields.map(([label, value]) => value ? (
            <section key={label}>
              <h4>{label}</h4>
              <p>{value}</p>
            </section>
          ) : null)}
        </div>
      </div>
    </div>
  );
}

function ShotPromptModal({shot, canvas, onClose, onZoom}: {shot: CanvasShot; canvas: Canvas; onClose: () => void; onZoom: (url: string) => void}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [copied, setCopied] = useState(false);
  const chars = shot.char_idxs.map((i) => canvas.characters[i]).filter(Boolean);
  const scenes = shot.scene_idxs.map((i) => canvas.scenes[i]).filter(Boolean);
  const props = shot.prop_idxs.map((i) => canvas.props[i]).filter(Boolean);
  const voices = shot.voice_idxs.map((i) => canvas.voices[i]).filter(Boolean);
  const epTag = shot.ep_id ? `${shot.ep_id} · ` : '';
  const promptText = shot.video_prompt || '（该镜头尚未写入视频提示词，可点击镜头卡编辑后重新渲染）';
  const copyPrompt = () => {
    navigator.clipboard?.writeText(promptText)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600); })
      .catch(() => { /* 剪贴板不可用 */ });
  };
  return (
    <div className="canvas-prompt-overlay" role="dialog" aria-modal="true" onClick={onClose}>
      <div ref={dialogRef} tabIndex={-1} className="canvas-prompt-modal" onClick={(event) => event.stopPropagation()}>
        <header className="canvas-prompt-header">
          <div>
            <strong>镜头 {shot.idx}</strong>
            <span className="canvas-prompt-sub">{epTag}段 {shot.segment_id ?? '—'} · {shot.duration !== null ? `${shot.duration}s` : '时长未知'} · 全能参考视频</span>
          </div>
          <button type="button" className="canvas-prompt-close" onClick={onClose} aria-label="关闭"><X size={16} strokeWidth={2} aria-hidden="true" /></button>
        </header>

        <section className="canvas-prompt-block">
          <div className="canvas-prompt-block-title">参考图身份（视频生成直传参考池）</div>
          {chars.length === 0 && scenes.length === 0 && props.length === 0 && voices.length === 0 ? (
            <p className="canvas-prompt-empty">本镜头未解析到资产引用</p>
          ) : (
            <div className="canvas-prompt-refs">
              {chars.map((c, i) => (
                <span key={`c-${c.name}`} className="canvas-prompt-ref is-char" title={c.description ?? c.name}>
                  {c.image_url ? <img src={c.image_url} alt="" loading="lazy" onClick={(event) => { event.stopPropagation(); onZoom(c.image_url as string); }} /> : <span className="canvas-prompt-ref-media">角</span>}
                  <span className="canvas-prompt-ref-meta">
                    <span className="canvas-prompt-ref-name">{c.name} 即@图片{i + 1}</span>
                    <span className="canvas-prompt-ref-kind">角色参考 · 锁脸直传</span>
                  </span>
                </span>
              ))}
              {scenes.map((s) => (
                <span key={`s-${s.name}`} className="canvas-prompt-ref is-scene" title={s.description ?? s.name}>
                  {s.image_url ? <img src={s.image_url} alt="" loading="lazy" onClick={(event) => { event.stopPropagation(); onZoom(s.image_url as string); }} /> : <span className="canvas-prompt-ref-media">场</span>}
                  <span className="canvas-prompt-ref-meta">
                    <span className="canvas-prompt-ref-name">{s.name}</span>
                    <span className="canvas-prompt-ref-kind">场景参考 · 环境/光线基准</span>
                  </span>
                </span>
              ))}
              {props.map((p) => (
                <span key={`p-${p.name}`} className="canvas-prompt-ref is-prop" title={p.description ?? p.name}>
                  {p.image_url ? <img src={p.image_url} alt="" loading="lazy" onClick={(event) => { event.stopPropagation(); onZoom(p.image_url as string); }} /> : <span className="canvas-prompt-ref-media">道</span>}
                  <span className="canvas-prompt-ref-meta">
                    <span className="canvas-prompt-ref-name">{p.name}</span>
                    <span className="canvas-prompt-ref-kind">道具参考 · 外观继承</span>
                  </span>
                </span>
              ))}
              {voices.map((v) => (
                <span key={`v-${v.name}`} className="canvas-prompt-ref is-voice" title={v.description ?? v.name}>
                  <span className="canvas-prompt-ref-media is-voice"><Music size={14} strokeWidth={1.8} aria-hidden="true" /></span>
                  <span className="canvas-prompt-ref-meta">
                    <span className="canvas-prompt-ref-name">{v.name} 音色</span>
                    <span className="canvas-prompt-ref-kind">音色参考 · 对白配音</span>
                  </span>
                </span>
              ))}
            </div>
          )}
        </section>

        <section className="canvas-prompt-block">
          <div className="canvas-prompt-block-title">视频提示词（完整原文）</div>
          <pre className="canvas-prompt-text">{promptText}</pre>
          <div className="canvas-prompt-actions">
            <span className="canvas-param-tag">Seedance 2.0</span>
            <span className="canvas-param-tag is-mode">全能参考</span>
            <span className="canvas-param-tag">{shot.clip_url ? '已出片段' : '未渲染'}</span>
            <button type="button" className={`canvas-prompt-copy ${copied ? 'is-copied' : ''}`} onClick={copyPrompt}>
              {copied ? <><Check size={12} strokeWidth={2.5} aria-hidden="true" />已复制</> : '复制提示词'}
            </button>
          </div>
        </section>

        {(shot.motion_desc || shot.audio_desc || shot.camera_lang) && (
          <section className="canvas-prompt-block">
            <div className="canvas-prompt-block-title">镜头设计（参考）</div>
            <div className="canvas-prompt-design">
              {shot.motion_desc && (
                <div className="canvas-prompt-design-row">
                  <span className="canvas-prompt-design-label">运镜/动作</span>
                  <p className="canvas-prompt-design-value">{shot.motion_desc}</p>
                </div>
              )}
              {shot.audio_desc && (
                <div className="canvas-prompt-design-row">
                  <span className="canvas-prompt-design-label">声音/对白</span>
                  <p className="canvas-prompt-design-value">{shot.audio_desc}</p>
                </div>
              )}
              {shot.camera_lang && (
                <div className="canvas-prompt-design-row">
                  <span className="canvas-prompt-design-label">机位</span>
                  <p className="canvas-prompt-design-value">{shot.camera_lang}</p>
                </div>
              )}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function CanvasLegend({isOverview}: {isOverview: boolean}) {  const [open, setOpen] = useState(false); // 默认收起，避免遮挡画布操作
  const [tab, setTab] = useState<'节点' | '连线' | '状态'>('节点');
  const rootRef = useRef<HTMLDivElement | null>(null);
  // 展开后点图例以外的任何地方（空白画布 / 节点 / 工具栏）自动收起。
  // 用捕获阶段监听：React Flow 的拖拽与各类面板会 stopPropagation，冒泡阶段收不到。
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Element)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [open]);
  return (
    <div ref={rootRef} className={`canvas-legend ${open ? 'is-open' : ''}`}>
      <button type="button" className="canvas-legend-toggle" onClick={() => setOpen((o) => !o)} title={open ? '收起图例' : '展开图例'}>
        图例<ChevronDown size={12} strokeWidth={2.2} aria-hidden="true" className={open ? 'is-open' : 'is-collapsed'} />
      </button>
      {open && (
        <div className="canvas-legend-body">
          <div className="canvas-legend-tabs">
            {(['节点', '连线', '状态'] as const).map((t) => (
              <button key={t} type="button" className={tab === t ? 'is-active' : ''} onClick={() => setTab(t)}>{t}</button>
            ))}
          </div>
          {tab === '节点' && (
            <ul className="canvas-legend-list">
              <li><span className="canvas-legend-swatch is-char" />角色立绘<span className="canvas-legend-note">三视图合图 · 视频全能参考直传</span></li>
              <li><span className="canvas-legend-swatch is-scene" />场景<span className="canvas-legend-note">本场环境/光线基准</span></li>
              <li><span className="canvas-legend-swatch is-prop" />道具<span className="canvas-legend-note">仅继承外观</span></li>
              <li><span className="canvas-legend-swatch is-voice" />音色<span className="canvas-legend-note">音频 + 音色描述双通道</span></li>
              <li><span className="canvas-legend-swatch is-seg" />Scene 段卡<span className="canvas-legend-note">左规划区 + 右剧情宫格（镜头网格）</span></li>
              <li><span className="canvas-legend-swatch is-shot" />镜头<span className="canvas-legend-note">16:9 缩略 · 时长 · 已出片段</span></li>
              <li><span className="canvas-legend-swatch is-ep" />集分组{isOverview ? <span className="canvas-legend-note">整剧总览 · 一集一画布</span> : null}</li>
              <li><span className="canvas-legend-swatch is-note" />备注<span className="canvas-legend-note">自由文本批注</span></li>
            </ul>
          )}
          {tab === '连线' && (
            <ul className="canvas-legend-list">
              <li><span className="canvas-legend-line is-seg" />段序链<span className="canvas-legend-note">段间时序（同集内连接，跨集自动断开）</span></li>
              <li><span className="canvas-legend-line is-asset" />资产绑定<span className="canvas-legend-note">段卡 → 角色/场景/道具/音色（按类型着色）</span></li>
              <li><span className="canvas-legend-line is-user" />手动连线<span className="canvas-legend-note">连续模式下用户建立的关系</span></li>
            </ul>
          )}
          {tab === '状态' && (
            <ul className="canvas-legend-list">
              <li><span className="canvas-legend-badge is-confirmed">已确认</span><span className="canvas-legend-note">资产经用户逐项确认</span></li>
              <li><span className="canvas-legend-badge is-pending">待确认</span><span className="canvas-legend-note">未确认不开放渲染入口</span></li>
              <li><span className="canvas-legend-badge is-clip">已出片段</span><span className="canvas-legend-note">该镜头视频已生成</span></li>
              <li><span className="canvas-legend-badge is-fullref">全能参考</span><span className="canvas-legend-note">视频生成：角色/场景/道具/音色参考图直传</span></li>
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

// 网格吸附步进：写成模块级常量，避免字面量数组每次渲染换新引用，反复触发 React Flow 重算。
const CANVAS_SNAP_GRID: [number, number] = [12, 12];
// 画布节点 id 前缀（与 flowNodes 命名一致）：媒体卡 media-<mnId>、备注 note-<noteId>。
// 放在模块级是为了让任何位置的回调都能引用，不受组件内声明顺序影响。
const NOTE_PREFIX = 'note-';
const MEDIA_PREFIX = 'media-';

const KIND_LABEL: Record<CanvasMediaNodeKind, string> = {text: '文本', image: '图片', audio: '音频', video: '视频'};

/** 删除确认弹窗：可勾选要删哪些（Del 键 / 卡片 ✕ / 右键菜单都走这里） */
function DeleteDialog({targets, mediaNodes, notes, onCancel, onConfirm}: {
  targets: {id: string; type: string}[];
  mediaNodes: CanvasMediaNode[];
  notes: CanvasNote[];
  onCancel: () => void;
  onConfirm: (ids: string[]) => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [checked, setChecked] = useState<Set<string>>(() => new Set(targets.map((t) => t.id)));
  const toggle = (id: string) => setChecked((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const labelOf = (t: {id: string}): string => {
    if (t.id.startsWith(NOTE_PREFIX)) {
      const note = notes.find((x) => x.id === t.id.slice(NOTE_PREFIX.length));
      const txt = (note?.text || '').trim();
      return txt ? (txt.length > 26 ? `${txt.slice(0, 26)}…` : txt) : '（空备注）';
    }
    const mn = mediaNodes.find((x) => x.id === t.id.slice(MEDIA_PREFIX.length));
    return mn?.title || '节点';
  };
  const kindOf = (t: {id: string}): string => {
    if (t.id.startsWith(NOTE_PREFIX)) return '备注';
    const mn = mediaNodes.find((x) => x.id === t.id.slice(MEDIA_PREFIX.length));
    return mn ? KIND_LABEL[mn.kind] : '媒体';
  };
  return (
    <div className="canvas-help-backdrop" onClick={onCancel}>
      <div ref={dialogRef} tabIndex={-1} className="canvas-arrange" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
        <header>
          <strong>删除确认</strong>
          <button type="button" className="canvas-inspector-close" onClick={onCancel} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
        </header>
        <p className="canvas-arrange-hint">共 {targets.length} 项，可取消勾选保留不想删的：</p>
        <div className="canvas-delete-list">
          {targets.map((t) => (
            <button
              key={t.id}
              type="button"
              className={`canvas-delete-item ${checked.has(t.id) ? 'is-on' : ''}`}
              role="checkbox"
              aria-checked={checked.has(t.id)}
              onClick={() => toggle(t.id)}
            >
              <SvgCheckMark checked={checked.has(t.id)} />
              <span className="canvas-delete-kind">{kindOf(t)}</span>
              <span className="canvas-delete-name" title={labelOf(t)}>{labelOf(t)}</span>
            </button>
          ))}
        </div>
        <footer className="canvas-arrange-foot">
          <button type="button" onClick={() => setChecked(new Set(targets.map((t) => t.id)))}>全选</button>
          <button type="button" onClick={() => setChecked(new Set())}>全不选</button>
          <span className="canvas-arrange-spacer" />
          <button type="button" onClick={onCancel}>取消</button>
          <button
            type="button"
            className="is-del"
            disabled={checked.size === 0}
            onClick={() => onConfirm([...checked])}
          >删除 {checked.size} 项</button>
        </footer>
      </div>
    </div>
  );
}

/** 整理弹窗：对齐 / 分布 / 排序整理（取代堆在工具栏上的一排按钮） */
function ArrangeDialog({selectedCount, totalCount, onClose, onAlign, onSort}: {
  selectedCount: number;
  totalCount: number;
  onClose: () => void;
  onAlign: (mode: 'left' | 'right' | 'top' | 'bottom' | 'hcenter' | 'vcenter' | 'distH' | 'distV', scopeAll: boolean) => void;
  onSort: (key: 'name' | 'kind' | 'created', cols: number, scopeAll: boolean) => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const [cols, setCols] = useState(4);
  // 未选中时默认不动手：整批重排会打乱既有版式，必须显式勾选「应用到全部」才生效。
  const [applyAll, setApplyAll] = useState(false);
  const active = selectedCount > 0 || applyAll;
  // scopeAll 只在「没有选中 + 显式勾选全部」时为真；有选中时永远只作用选中节点。
  const scopeAll = selectedCount === 0 && applyAll;
  // ★ 第 15 轮打磨：没有选中、也没勾「应用到全部」时**不能**写 "作用范围：全部 N 个" ——
  //   那时所有按钮都是 disabled，文案却在告诉用户"要对全部动手"，自相矛盾
  //   （巡检实测：提示写"全部 31 个"，13 个按钮里 11 个是灰的）。
  //   改成明确说"请先选择"，用户才知道下一步该干什么。
  const scope = selectedCount > 0
    ? `选中的 ${selectedCount} 个`
    : applyAll
      ? `全部 ${totalCount} 个（已勾选应用全部）`
      : '未选中，请先在画布上框选节点';
  return (
    <div className="canvas-help-backdrop" onClick={onClose}>
      <div ref={dialogRef} tabIndex={-1} className="canvas-arrange" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
        <header>
          <strong>整理节点</strong>
          <button type="button" className="canvas-inspector-close" onClick={onClose} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
        </header>
        <p className="canvas-arrange-hint">作用范围：{scope}</p>
        {selectedCount === 0 && (
          <button
            type="button"
            className="canvas-arrange-all"
            role="checkbox"
            aria-checked={applyAll}
            onClick={() => setApplyAll((value) => !value)}
          >
            <SvgCheckMark checked={applyAll} />
            <span className="canvas-arrange-all-copy">当前没有选中节点。勾选后应用到全部 {totalCount} 个，会重排整张画布。</span>
          </button>
        )}

        <section className="canvas-arrange-sec">
          <h5>对齐</h5>
          <div className="canvas-arrange-grid">
            <button type="button" disabled={!active} onClick={() => onAlign('left', scopeAll)} title="按最左侧对齐">
              <AlignHorizontalJustifyStart size={15} strokeWidth={1.9} aria-hidden="true" />左
            </button>
            <button type="button" disabled={!active} onClick={() => onAlign('hcenter', scopeAll)} title="水平居中对齐">
              <AlignHorizontalJustifyCenter size={15} strokeWidth={1.9} aria-hidden="true" />水平居中
            </button>
            <button type="button" disabled={!active} onClick={() => onAlign('right', scopeAll)} title="按最右侧对齐">
              <AlignHorizontalJustifyEnd size={15} strokeWidth={1.9} aria-hidden="true" />右
            </button>
            <button type="button" disabled={!active} onClick={() => onAlign('top', scopeAll)} title="按最上方对齐">
              <AlignVerticalJustifyStart size={15} strokeWidth={1.9} aria-hidden="true" />顶
            </button>
            <button type="button" disabled={!active} onClick={() => onAlign('vcenter', scopeAll)} title="垂直居中对齐">
              <AlignVerticalJustifyCenter size={15} strokeWidth={1.9} aria-hidden="true" />垂直居中
            </button>
            <button type="button" disabled={!active} onClick={() => onAlign('bottom', scopeAll)} title="按最下方对齐">
              <AlignVerticalJustifyEnd size={15} strokeWidth={1.9} aria-hidden="true" />底
            </button>
          </div>
        </section>

        <section className="canvas-arrange-sec">
          <h5>分布</h5>
          <div className="canvas-arrange-grid">
            <button type="button" disabled={!active} onClick={() => onAlign('distH', scopeAll)} title="首尾不动，中间均分水平间距">
              <AlignHorizontalDistributeCenter size={15} strokeWidth={1.9} aria-hidden="true" />水平等距
            </button>
            <button type="button" disabled={!active} onClick={() => onAlign('distV', scopeAll)} title="首尾不动，中间均分垂直间距">
              <AlignVerticalDistributeCenter size={15} strokeWidth={1.9} aria-hidden="true" />垂直等距
            </button>
          </div>
        </section>

        <section className="canvas-arrange-sec">
          <h5>排序整理</h5>
          <div className="canvas-arrange-cols">
            <span>每行</span>
            <button type="button" onClick={() => setCols((c) => Math.max(1, c - 1))} title="减少列数">
              <Minus size={13} strokeWidth={2.2} aria-hidden="true" />
            </button>
            <strong>{cols}</strong>
            <button type="button" onClick={() => setCols((c) => Math.min(10, c + 1))} title="增加列数">
              <Plus size={13} strokeWidth={2.2} aria-hidden="true" />
            </button>
            <span>个</span>
          </div>
          <div className="canvas-arrange-grid">
            <button type="button" disabled={!active} onClick={() => onSort('name', cols, scopeAll)}>按名称</button>
            <button type="button" disabled={!active} onClick={() => onSort('kind', cols, scopeAll)}>按类型</button>
            <button type="button" disabled={!active} onClick={() => onSort('created', cols, scopeAll)}>按创建顺序</button>
          </div>
        </section>
      </div>
    </div>
  );
}

/**
 * 批量调参的**记忆**：上次用过哪些字段、选了哪些值，下次打开直接带回来。
 *
 * 用户原话：「默认开关开启啊 每次都要打开开关烦死了 最好是有记忆功能」。
 * 所以这里不是"记住开关状态"这么简单，而是两层：
 *   ① **没有任何记忆时，三个开关默认全开**（用户明确要求"默认开关开启"）；
 *   ② 有过一次「确认应用」，就把当时的开关 + 取值全部记住，下次原样带回。
 *
 * ★ 为什么存 localStorage 而不是后端：
 *   这是**本机操作偏好**，不是项目数据。换项目也该沿用上次的习惯
 *   （同一套模型/清晰度偏好不会因为切项目就变），所以用全局 key、不带 session。
 * ★ 为什么要校验读回来的值：
 *   模型可能被下线、清晰度档位可能随模型变化。读到不存在的值必须丢掉，
 *   否则会带着一个后端不认的模型去批量提交（那比"没记住"严重得多）。
 */
const BATCH_PREFS_KEY = 'vimax.canvas.batchParamsPrefs';

/**
 * 批量调参要下发的改动。
 *
 * ★ 模型按卡片类型分成两个字段（第 32 轮）：
 *   图片卡只能吃 `image` 能力的模型、视频卡只能吃 `video` 能力的模型，
 *   用一个 `model` 字段表达两件事在类型上就是错的 —— 上一版因此出现过
 *   "只选图片时下拉显示 Seedance 2.0 mini"以及"混选时另一类被静默跳过"。
 */
type BatchParamsPatch = {
  /** 只写给图片卡 */
  modelImage?: {id: string; providerId: string; remoteModel?: string};
  /** 只写给视频卡 */
  modelVideo?: {id: string; providerId: string; remoteModel?: string};
  /** 只写给音频卡（第 33 轮补；音频也是"可批量调参"的媒体卡） */
  modelAudio?: {id: string; providerId: string; remoteModel?: string};
  ratio?: string;
  clarityImage?: string;
  clarityVideo?: string;
  /** 音频：输出格式 wav/mp3 */
  audioFormat?: string;
  /** 音频：采样率（字符串形式传，落地转 number） */
  audioSampleRate?: string;
  /** 音频：合成模式 design（音色设计）/ speech（读音色 ID） */
  audioMode?: string;
  /** 只写给文本节点卡（用户「文本节点也要增加批量操作」） */
  modelText?: {id: string; providerId: string; remoteModel?: string};
  /** 文本：最大输出 token（与单卡弹窗「生成长度」同源：400/1200/3000） */
  textMaxTokens?: number;
  /** 文本：采样温度 0~2（单卡默认 0.7） */
  textTemperature?: number;
};

type BatchPrefs = {
  useModel: boolean;
  useRatio: boolean;
  useClarity: boolean;
  /* ★ 第 35 轮：这里**故意没有 scope* 字段**。
     作用范围描述的是"这一次选区里有哪几类卡"，与选区强绑定、跨选区毫无意义，
     记忆它只会造成"选着 2 个文本卡、文本那格却是关的"这类错误。
     模型/清晰度/比例/温度是**用户偏好**，与选区无关，才值得记。 */
  /** 图片模型与视频模型**分开记**：两类是两套完全不同的模型，不能共用一个字段 */
  modelImage: string;
  modelVideo: string;
  modelAudio: string;
  ratio: string;
  clarityImage: string;
  clarityVideo: string;
  audioFormat: string;
  audioSampleRate: string;
  audioMode: string;
  /** 文本节点：模型 / 长度 / 温度 */
  modelText: string;
  textTokens: string;
  textTemperature: string;
};

function loadBatchPrefs(): Partial<BatchPrefs> | null {
  try {
    const raw = localStorage.getItem(BATCH_PREFS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== 'object') return null;
    const str = (v: unknown) => (typeof v === 'string' ? v : '');
    const bool = (v: unknown, fallback: boolean) => (typeof v === 'boolean' ? v : fallback);
    // 兼容旧版单一 `modelId`：按 id 前缀判断当初记的是哪一类，挪进对应字段。
    // 不迁移的话，老用户升级后模型会"记忆失效"，还得重选一次。
    const legacy = str(parsed.modelId);
    const legacyIsVideo = /seedance/i.test(legacy);
    const legacyIsAudio = /cosyvoice|tts/i.test(legacy);
    return {
      useModel: bool(parsed.useModel, true),
      useRatio: bool(parsed.useRatio, true),
      useClarity: bool(parsed.useClarity, true),
      modelImage: str(parsed.modelImage) || (legacy && !legacyIsVideo && !legacyIsAudio ? legacy : ''),
      modelVideo: str(parsed.modelVideo) || (legacy && legacyIsVideo ? legacy : ''),
      modelAudio: str(parsed.modelAudio) || (legacy && legacyIsAudio ? legacy : ''),
      ratio: str(parsed.ratio),
      clarityImage: str(parsed.clarityImage),
      clarityVideo: str(parsed.clarityVideo),
      audioFormat: str(parsed.audioFormat),
      audioSampleRate: str(parsed.audioSampleRate),
      audioMode: str(parsed.audioMode),
      modelText: str(parsed.modelText) || (legacy && /deepseek|gpt|chat/i.test(legacy) ? legacy : ''),
      textTokens: str(parsed.textTokens),
      textTemperature: str(parsed.textTemperature),
    };
  } catch {
    // localStorage 被禁用 / JSON 坏掉都不该让弹窗打不开
    return null;
  }
}

function saveBatchPrefs(prefs: BatchPrefs) {
  try { localStorage.setItem(BATCH_PREFS_KEY, JSON.stringify(prefs)); } catch { /* 静默 */ }
}

/**
 * 批量调参弹窗（图片 / 视频）：一次给多张卡设 模型 / 比例 / 清晰度。
 *
 * 设计要点：
 *  1. **每个字段都可单独启用**，但默认全开、并记住上次的选择 ——
 *     用户明确要求「默认开关开启，每次都要打开开关烦死了」。
 *     想跳过某一项，关掉那个开关即可，这个"关"同样会被记住。
 *  2. **模型按类型分组**：选中的可能是图片+视频混选，两类的模型完全不同，
 *     给个混在一起的模型列表会让人选到不适用自己卡片的那一个。
 *  3. **清晰度按类型分列**：图片是 1.5K/2K/4K、视频是 480P/720P/1080P，
 *     两个量纲不能并成一行。
 *  4. 只写 params，**不动 position / prompt / refs** —— 保存走 patchMediaNodesFields，
 *     它本身就不带 position，不会覆盖别人的拖动结果。
 */
/** 批量调参管的**四类**卡：图片 / 视频 / 音频（第 33 轮）/ 文本（第 34 轮，用户「文本节点也要增加批量操作」） */
type BatchKind = 'image' | 'video' | 'audio' | 'text';

/** 作用范围：四类各自可独立开关 */
type BatchScope = {image: boolean; video: boolean; audio: boolean; text: boolean};

function BatchParamsDialog({targets, catalog, onClose, onApply}: {
  targets: CanvasMediaNode[];
  catalog: ModelCatalog | null;
  onClose: () => void;
  onApply: (patch: BatchParamsPatch, scope: BatchScope) => void;
}) {
  const dialogRef = useDialogFocusTrap<HTMLDivElement>();
  const hasImage = targets.some((n) => n.kind === 'image');
  const hasVideo = targets.some((n) => n.kind === 'video');
  const hasAudio = targets.some((n) => n.kind === 'audio');
  const hasText = targets.some((n) => n.kind === 'text');
  /** 选区里一共几种类型：>1 才需要行标签与「作用范围」分区 */
  const kindCount = Number(hasImage) + Number(hasVideo) + Number(hasAudio) + Number(hasText);

  const catalogModels = (catalog?.models || []).filter((m) => m.enabled !== false
    && (m.capability === 'image' || m.capability === 'video' || m.capability === 'audio' || m.capability === 'text'));
  const modelsOf = (kind: BatchKind) => (catalog ? catalogModels.filter((m) => m.capability === kind) : fallbackModelSpecs(kind));
  const allSpecs = [...modelsOf('image'), ...modelsOf('video'), ...modelsOf('audio'), ...modelsOf('text')];

  // 记忆：只在首次挂载时读一次，避免每次渲染都解析 JSON
  const [prefs] = useState(() => loadBatchPrefs());
  /**
   * 作用范围：**只要该类卡片在选区里，就一律默认勾上**（不读记忆）。
   *
   * ★★ 第 35 轮二次修正。用户截图：「文本 2」那一格是未勾选的灰态，
   *   红框标出来质问「文本这里没有自动选择 还手动选择」。
   *   根因：scope 初始值读了 `prefs?.scopeText !== false` —— 上次混选时取消过
   *   「文本」，false 被记住，这次明明选着 2 个文本卡，它却是关的 →
   *   下面的「文本」参数区整块不渲染，用户既看不到现状也设不了，只能手动把勾打回来。
   *
   * ★ 为什么 scope **根本不该被记忆**（而不是"加个兜底"）：
   *   作用范围描述的是「**这一次**选区里有哪几类卡、要不要动它们」，
   *   它天然与"这次选了什么"绑定。而记忆是跨选区的 ——
   *   上次没勾文本、这次选了一堆文本，把 false 带过来就是纯粹的错。
   *   对比一下记忆真正该管的字段（模型/清晰度/比例/温度）：
   *   那些是**用户偏好**，与选区无关，跨次沿用才有价值。两者性质不同。
   *
   * ★ 第 33 轮那个"单类型强制 true"的补丁本质上是这个 bug 的另一副面孔
   *   （只在一类卡片时躲开记忆）。现在统一成"永不读记忆"，那个特例也就没有必要了。
   */
  const [scopeImage, setScopeImage] = useState(hasImage);
  const [scopeVideo, setScopeVideo] = useState(hasVideo);
  const [scopeAudio, setScopeAudio] = useState(hasAudio);
  const [scopeText, setScopeText] = useState(hasText);
  // 开关：**默认开**（用户要求）；有记忆时用记忆值
  const [useModel, setUseModel] = useState(prefs?.useModel !== false);
  const [useRatio, setUseRatio] = useState(prefs?.useRatio !== false);
  const [useClarity, setUseClarity] = useState(prefs?.useClarity !== false);
  // 取值：优先用记忆值，但必须校验它现在仍然合法
  const [clarityImage, setClarityImage] = useState('');
  const [clarityVideo, setClarityVideo] = useState('');
  const [ratio, setRatio] = useState('');
  /**
   * 模型**必须按卡片类型分开选**（第 32 轮，用户原话
   * 「不同节点卡片类型你要读取不同模型了」/「是 Seedream 5.0 Pro 不是 Seedance 2.0 mini」）。
   *
   * ★ 上一版只有一个 `modelId`，由此产生两类真实错乱：
   *   ① 只选图片时，记忆里上次用的**视频模型**被塞进下拉 → 显示 Seedance 2.0 mini；
   *   ② 混选时选一个模型只能覆盖一种类型，另一类要么被静默跳过、要么写错。
   *   两类的模型集合本来就完全不同（图片只有 Seedream、视频只有 Seedance），
   *   用一个字段表达"两类各自的模型"在结构上就是错的。
   */
  const [imageModelId, setImageModelId] = useState('');
  const [videoModelId, setVideoModelId] = useState('');
  const [audioModelId, setAudioModelId] = useState('');
  const [textModelId, setTextModelId] = useState('');
  /** 打开中的下拉（同时只会开一个） */
  const [modelOpen, setModelOpen] = useState<null | BatchKind>(null);
  /**
   * 音频的专属可批量项（第 33 轮）。音频没有「比例 / 清晰度」这两个概念，
   * 它有自己的三件套：输出格式 / 采样率 / 合成模式。
   * 硬把图片那套搬过来只会得到一堆"音频不适用"的置灰胶囊。
   */
  const [audioFormat, setAudioFormat] = useState('');
  const [audioSampleRate, setAudioSampleRate] = useState('');
  const [audioMode, setAudioMode] = useState('');
  /**
   * 文本节点的专属可批量项。
   * ★ 值必须与单卡弹窗（媒体卡的「生成长度 / 采样温度」）**同源**：
   *   长度档位就是 TEXT_LEN_OPTS（400/1200/3000 token），温度 0~2、步长 0.1、默认 0.7。
   *   各写一套的话，批量改完再打开单卡会看到"值对不上"，用户会以为批量没生效。
   */
  const [textTokens, setTextTokens] = useState('');
  const [textTemp, setTextTemp] = useState('');

  // 混选时各类型模型分开显示，且各自只能作用到同类型的卡
  const kindInScope = (k: CanvasMediaNodeKind) =>
    (k === 'image' ? scopeImage : k === 'video' ? scopeVideo : k === 'audio' ? scopeAudio : k === 'text' ? scopeText : false);
  const scopeKinds = (['image', 'video', 'audio', 'text'] as const).filter(kindInScope);

  // 选项并集：只统计"当前作用范围内"的卡片所能支持的取值
  const ratioOptions = (() => {
    const set: string[] = [];
    for (const kind of scopeKinds) {
      for (const spec of modelsOf(kind)) {
        const opts = spec.params?.ratio?.options;
        const list = Array.isArray(opts) && opts.length ? opts.map(String) : ['1:1', '16:9', '9:16', '4:3', '3:4', '21:9'];
        for (const v of list) if (!set.includes(v)) set.push(v);
      }
    }
    return set.length ? set : ['1:1', '16:9', '9:16', '4:3', '3:4', '21:9'];
  })();
  // 清晰度按类型各自算（两个量纲，绝不合并）
  const clarityOptsOf = (kind: 'image' | 'video'): string[] => {
    const key = kind === 'video' ? 'resolution' : 'clarity';
    const fallback = kind === 'video' ? RESOLUTIONS : CLARITIES;
    const set: string[] = [];
    for (const spec of modelsOf(kind)) {
      const opts = spec.params?.[key]?.options;
      const list = Array.isArray(opts) && opts.length ? opts.map(String) : fallback;
      for (const v of list) if (!set.includes(v)) set.push(v);
    }
    return set.length ? set : fallback;
  };
  const showImageClarity = scopeImage && hasImage;
  const showVideoClarity = scopeVideo && hasVideo;
  /** 清晰度 / 比例只对图片和视频有意义；只有音频或文本在选区时整块不出现 */
  const showClaritySec = showImageClarity || showVideoClarity;
  const showRatioSec = showImageClarity || showVideoClarity;
  const clarityImageOpts = clarityOptsOf('image');
  const clarityVideoOpts = clarityOptsOf('video');
  /**
   * 文本生成长度档位：**与单卡弹窗同源**。
   * 优先用 catalog 里文本模型声明的 `maxTokens.options`（可在「模型接口」里改），
   * 没声明就用内置 TEXT_LEN_OPTS（短 400 / 中 1200 / 长 3000）。
   * 「标称长度」与单卡那三个胶囊完全一致，避免两处对不上。
   */
  const textTokenOpts = (() => {
    const spec = modelsOf('text')[0];
    const opts = spec?.params?.maxTokens?.options;
    const list = Array.isArray(opts) && opts.length
      ? opts.map(String)
      : TEXT_LEN_OPTS.map((o) => String(o.tokens));
    return list;
  })();
  const textTokenLabel = (v: string) => TEXT_LEN_OPTS.find((o) => String(o.tokens) === v)?.label || v;

  /**
   * 按**卡片类型**取对应的模型 —— 这是本轮的核心修正。
   *
   * ★ 用户原话：「不同节点卡片类型你要读取不同模型了」
   *   「是 Seedream 5.0 Pro 不是 Seedance 2.0 mini」。
   *   模型目录里 `capability` 就写着 image / video（见「模型接口」列表左侧的灰标签），
   *   图片卡只可能是 `image` 能力的模型（Seedream 系列），
   *   视频卡只可能是 `video` 能力的模型（Seedance 系列）。两者不能共用一个字段。
   */
  const modelIdOf = (kind: BatchKind) => (kind === 'image' ? imageModelId
    : kind === 'video' ? videoModelId
      : kind === 'audio' ? audioModelId
        : textModelId);
  const setModelIdOf = (kind: BatchKind, id: string) => {
    if (kind === 'image') setImageModelId(id);
    else if (kind === 'video') setVideoModelId(id);
    else if (kind === 'audio') setAudioModelId(id);
    else setTextModelId(id);
  };
  /** 该类型当前选中的模型规格（未选 / 不属于该类型 → undefined） */
  const pickedSpecOf = (kind: BatchKind) => modelsOf(kind).find((m) => m.id === modelIdOf(kind));

  /**
   * 某个档位能被**多少张卡**支持 —— 这就是「不支持就变灰点不了」的判据。
   *
   * ★ 为什么要按"选了新模型就用新模型、没选就用卡片自己的模型"来算：
   *   用户的操作顺序通常是"先选模型、再选清晰度"。如果还按卡片旧的模型算，
   *   会出现"新模型明明支持 1080P 但胶囊是灰的"这种自相矛盾的状态。
   *   这里始终按**点下去之后即将生效的那个模型**判断，所见即所得。
   *
   * ★ 返回 0 才置灰：只要有一张卡支持，这个档位就是有意义的（其余卡保留原值）。
   */
  const supportCount = (kind: BatchKind, key: 'clarity' | 'resolution' | 'ratio', want: string): number => {
    if (!want) return 0;
    const picked = pickedSpecOf(kind);
    let n = 0;
    for (const t of targets) {
      if (t.kind !== kind) continue;
      const mid = useModel && picked ? picked.id : (t.params?.model || '');
      const spec = allSpecs.find((m) => m.id === mid) || catalogModels.find((m) => m.capability === kind);
      const opts = spec?.params?.[key]?.options;
      const fallback = key === 'clarity' ? CLARITIES : key === 'resolution' ? RESOLUTIONS : RATIOS;
      const list = Array.isArray(opts) && opts.length ? opts.map(String) : fallback;
      // schema 缺失时不拦（宁可让用户点，也不要因为目录没加载好就把所有档位锁死）
      if (!list.length || list.includes(want)) n += 1;
    }
    return n;
  };
  const kindTotal = (kind: BatchKind) => targets.filter((t) => t.kind === kind).length;

  /**
   * 某类型的卡片**全部用的都是同一个模型**时，把这个模型作为默认显示值。
   *
   * ★ 为什么需要：模型下拉默认显示「选择模型」时，用户看不出这批图现在用的
   *   到底是哪一个模型，得自己翻下拉去对照 —— 而实际场景里 12 张图往往
   *   全都跑 `Seedream 5.0 Pro`。把"事实上唯一的值"显示出来才符合所见即所得。
   *
   * ★ 为什么只在"全票一致"时才填：只要有一张卡用的模型不同，预选任何一个
   *   都会在点确认时**静默改写**那一张 —— 这正是本轮要杜绝的静默失败。
   *   不一致就保持空白（= 保持原值），让用户显式选。
   *
   * ★ 必须按类型分别算：图片卡之间比、视频卡之间比。混在一起比的话，
   *   只要选了图片+视频就一定"不一致"，两边的真实模型都显示不出来。
   */
  const unanimousModelOf = (kind: BatchKind): string => {
    const ofKind = targets.filter((t) => t.kind === kind);
    if (ofKind.length === 0) return '';
    const ids = new Set(ofKind.map((t) => t.params?.model || ''));
    if (ids.size !== 1) return '';
    const only = [...ids][0];
    // 只认**该类型**的模型：万一历史数据里图片卡存过一个视频模型 id，
    // 显示出来只会误导（用户截图里的 Seedance 2.0 mini 就是这么来的）
    return only && modelsOf(kind).some((m) => m.id === only) ? only : '';
  };

  /**
   * 某类型的卡片在某个参数上**全部一致**时，返回那个值；否则返回 ''。
   *
   * ★★ 第 35 轮，用户原话「文本节点没有自动选择设置」。
   *   上一版只给「模型」做了全票一致带出（`unanimousModelOf`），
   *   长度 / 温度 / 清晰度 / 比例 / 音频三件套全都不带 —— 用户打开弹窗后
   *   看到的是「这 3 个文本节点到底现在是短还是长？」，必须逐个点开单卡去看。
   *   而批量调参弹窗的整个价值就是「替我总结现状 + 让我改」，
   *   不显示现状等于只做了一半。
   *
   * ★ 为什么仍然是"只在全票一致时才带出"（不能取"多数值"或"第一个值"）：
   *   只要有一张卡不同，预选任何一个都会在点确认时**静默改写**那张卡 ——
   *   这正是本项目反复强调要杜绝的静默失败。不一致就留空 = 保持原值。
   *
   * ★ 参数缺省时按**单卡弹窗的默认值**折算（文本 maxTokens 缺省 = 1200、
   *   temperature 缺省 = 0.7），否则"全部没设过"会被判成"不一致"而永远带不出来。
   */
  const unanimousParamOf = (kind: BatchKind, read: (p: NonNullable<CanvasMediaNode['params']>) => string): string => {
    const ofKind = targets.filter((t) => t.kind === kind);
    if (ofKind.length === 0) return '';
    const vals = new Set(ofKind.map((t) => read(t.params || {})));
    if (vals.size !== 1) return '';
    return [...vals][0];
  };

  /**
   * ★★ 第 35 轮：非模型字段**不再自动预选**，改为「标出现状」。
   *
   * 用户原话「文本节点没有自动选择设置」—— 他要的是**看到现在是什么**。
   * 但直接把现状写进 state（= 预选）有两个真实危害：
   *   ① 预选就等于"待应用"：用户什么都没点，按钮就亮了，一按就把全部卡
   *      按这个值重写一遍 —— 违反本项目「绝不允许没有显式意图就批量动作」；
   *   ② 比例尤其危险：图片 16:9、视频 9:16 是常态，预选其中一个会**静默**把
   *      另一种改掉，且用户完全看不出发生了什么。
   * ⇒ 所以拆成两个概念：
   *      `ratio/clarity 等`（state） = 用户**显式点选**的、会下发的值；
   *      `curXxx`（下面几个派生值） = 现状，只在 UI 上打一个"现状"标记。
   *   两者互不干扰：没点选时只显示现状，点了才变成待应用。
   */
  // 比例：只有"图片全票一致"和"视频全票一致"两者**相等**时才算现状，
  // 否则跨类型冲突，标任何一个都是误导 → 不标。
  const uRatioImg = unanimousParamOf('image', (p) => String(p.ratio || ''));
  const uRatioVid = unanimousParamOf('video', (p) => String(p.ratio || ''));
  const curRatio = uRatioImg && uRatioVid
    ? (uRatioImg === uRatioVid ? uRatioImg : '')
    : (uRatioImg || uRatioVid);

  // 清晰度：图片 / 视频各算各的（两个量纲，绝不合并）。
  // 缺省值按模型 defaultParams 折算 —— 卡片没设过时 defaultParams 才是"实际生效"的值。
  const clarityDefaultOf = (kind: 'image' | 'video'): string => {
    const key = kind === 'video' ? 'resolution' : 'clarity';
    return String(modelsOf(kind)[0]?.defaultParams?.[key] || (kind === 'video' ? '480P' : '1.5K'));
  };
  const curClarityImage = unanimousParamOf('image', (p) => String(p.clarity || clarityDefaultOf('image')));
  const curClarityVideo = unanimousParamOf('video', (p) => String(p.resolution || clarityDefaultOf('video')));

  // 音频三件套：只认当前音频模型 schema 里真实存在的选项
  const audioSpec = modelsOf('audio')[0];
  const optsOf = (key: 'outputFormat' | 'sampleRate' | 'ttsMode', fb: string[]) => {
    const o = audioSpec?.params?.[key]?.options;
    return Array.isArray(o) && o.length ? o.map(String) : fb;
  };
  const fmtOpts = optsOf('outputFormat', AUDIO_FORMATS);
  const rateOpts = optsOf('sampleRate', AUDIO_SAMPLE_RATES);
  const modeOpts = optsOf('ttsMode', AUDIO_MODES);
  // 缺省同样取音频模型 defaultParams（wav / 24000 / design），与单卡一致
  const audioDef = (k: 'outputFormat' | 'sampleRate' | 'ttsMode') => String(audioSpec?.defaultParams?.[k] ?? '');
  const curAudioFormat = unanimousParamOf('audio', (p) => String(p.outputFormat || audioDef('outputFormat') || AUDIO_FORMATS[0]));
  const curAudioRate = unanimousParamOf('audio', (p) => String(p.sampleRate || audioDef('sampleRate') || AUDIO_SAMPLE_RATES[0]));
  const curAudioMode = unanimousParamOf('audio', (p) => String(p.ttsMode || audioDef('ttsMode') || AUDIO_MODES[0]));

  // 文本长度：卡片存的可能是 schema 允许的任意值（比如 800），
  // 直接比对 `includes` 会判"不合法"而带不出来 → 归到最近的档位。
  // 缺省 1200（= 单卡 `p.maxTokens ?? 1200`）。
  const nearestTokenOpt = (want: string): string => {
    const n = Number(want);
    if (!Number.isFinite(n) || !textTokenOpts.length) return '';
    let best = textTokenOpts[0];
    for (const o of textTokenOpts) if (Math.abs(Number(o) - n) < Math.abs(Number(best) - n)) best = o;
    return best;
  };
  const curTextTokens = nearestTokenOpt(unanimousParamOf('text', (p) => String(p.maxTokens ?? 1200)));
  // 文本温度：卡片存连续值（0~2 步长 0.1），批量只给 0.2/0.7/1.2 三档 → 归到最近档
  const curTextTemp = (() => {
    const raw = unanimousParamOf('text', (p) => String(p.temperature ?? 0.7));
    const t = Number(raw);
    if (!raw || !Number.isFinite(t) || t < 0 || t > 2) return '';
    let best = TEXT_TEMP_OPTS[0];
    for (const o of TEXT_TEMP_OPTS) if (Math.abs(Number(o) - t) < Math.abs(Number(best) - t)) best = o;
    return best;
  })();


  // ── 记忆值回填 ─────────────────────────────────────────────
  // ★ 必须在这里（选项算好之后）而不是 useState 初值里做：
  //   初始渲染时 catalog 可能还没到，选项列表是空/兜底的，
  //   把记忆值直接塞进 state 会被"不合法"判掉、记忆就白存了。
  //   所以等 catalog 到位后再校验一次：合法才填，不合法留空（让它走"保持原值"）。
  //
  // ★ 同时**不能让用户改过的值被记忆覆盖**：这个 effect 只在 state 仍为空时回填，
  //   用户点过选项之后 state 非空，就再也不会被动。
  const prefsAppliedRef = useRef(false);
  useEffect(() => {
    if (prefsAppliedRef.current) return;
    // catalog 还没到（并且确实配了记忆里的模型）→ 再等一轮
    if (!catalog && (prefs?.modelImage || prefs?.modelVideo || prefs?.modelAudio)) return;
    prefsAppliedRef.current = true;
    // 模型：**按类型各自回填**，类型不符 / 已下线的一律丢掉（不跨类污染）
    for (const kind of (['image', 'video', 'audio', 'text'] as const)) {
      const remembered = kind === 'image' ? prefs?.modelImage
        : kind === 'video' ? prefs?.modelVideo
          : kind === 'audio' ? prefs?.modelAudio
            : prefs?.modelText;
      if (remembered && modelsOf(kind).some((m) => m.id === remembered)) setModelIdOf(kind, remembered);
      else {
        // 没记忆（或记忆失效）→ 用"该类型全票一致的模型"，让用户一眼看到现状
        const u = unanimousModelOf(kind);
        if (u) setModelIdOf(kind, u);
      }
    }
    // ★ 记忆值仍然要回填成"**已选**"：那是用户上次明确点过并应用过的，
    //   属于"显式意图"，和"替用户猜现状"是两件事。仍然要校验选项存在。
    if (prefs?.ratio && ratioOptions.includes(prefs.ratio)) setRatio(prefs.ratio);
    if (prefs?.clarityImage && clarityImageOpts.includes(prefs.clarityImage)) setClarityImage(prefs.clarityImage);
    if (prefs?.clarityVideo && clarityVideoOpts.includes(prefs.clarityVideo)) setClarityVideo(prefs.clarityVideo);
    if (prefs?.audioFormat && fmtOpts.includes(prefs.audioFormat)) setAudioFormat(prefs.audioFormat);
    if (prefs?.audioSampleRate && rateOpts.includes(prefs.audioSampleRate)) setAudioSampleRate(prefs.audioSampleRate);
    if (prefs?.audioMode && modeOpts.includes(prefs.audioMode)) setAudioMode(prefs.audioMode);
    if (prefs?.textTokens && textTokenOpts.includes(prefs.textTokens)) setTextTokens(prefs.textTokens);
    if (prefs?.textTemperature) {
      const t = Number(prefs.textTemperature);
      if (Number.isFinite(t) && t >= 0 && t <= 2) setTextTemp(prefs.textTemperature);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog]);

  // ── 兜底：作用范围不可能为空 ────────────────────────────────
  // ★ 作用范围为空 = 三个字段全部置灰、模型行一行都不渲染、确认按钮永远禁用
  //   = 一个完全无法操作也无法自救的弹窗。用户截图里的"啥模型都没显示"就是它。
  //   所以这里做最后一层保险：只要空了，就按"选区里有什么就勾什么"恢复。
  useEffect(() => {
    if (scopeImage || scopeVideo || scopeAudio || scopeText) return;
    if (hasImage) setScopeImage(true);
    if (hasVideo) setScopeVideo(true);
    if (hasAudio) setScopeAudio(true);
    if (hasText) setScopeText(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeImage, scopeVideo, scopeAudio, scopeText]);

  // ── 已选档位失配时自动作废 ──────────────────────────────────
  // ★ 场景：记忆里存的是 1080P，但这次选中的卡全是 2.0-mini（只到 720P）。
  //   1080P 胶囊会被置灰 → 点不到 → 但 state 里还是 '1080P'，
  //   「确认应用」就会提交一个"胶囊灰着、却写着 1080P"的自相矛盾状态。
  //   这里在渲染前把它清掉（等于回到"保持原值"），保证 UI 与实际提交永远一致。
  useEffect(() => {
    if (clarityVideo && supportCount('video', 'resolution', clarityVideo) === 0) setClarityVideo('');
    if (clarityImage && supportCount('image', 'clarity', clarityImage) === 0) setClarityImage('');
    if (ratio && scopeKinds.reduce((s, k) => s + supportCount(k, 'ratio', ratio), 0) === 0) setRatio('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageModelId, videoModelId, audioModelId, useModel, scopeImage, scopeVideo, scopeAudio, clarityVideo, clarityImage, ratio]);

  const anyFieldOn = useModel || useRatio || useClarity;
  // 至少有一个"有实际取值"的字段才算能应用；开着但没选值等于"保持原值"。
  // 但用户可能只想改模型 —— 模型选了值就成立。全都没值时才拦。
  // ★ 模型按类型分别判：图片/视频各自的模型都在作用范围内且被选中才算"有值"。
  //   否则会出现"下拉显示着一个模型（其实是另一类的残留）、点确认却什么都没改"。
  const hasAnyValue = Boolean(
    (useModel && scopeKinds.some((k) => pickedSpecOf(k)))
    || (useRatio && ratio)
    || (useClarity && (clarityImage || clarityVideo))
    || (scopeAudio && (audioFormat || audioSampleRate || audioMode))
    // 文本两件套与音频同理：它们不受「清晰度/比例」两个开关管，
    // 只要有值就算"有实际改动"，否则只改文本长度时确认按钮会一直是灰的
    || (scopeText && (textTokens || textTemp)),
  );
  const canApply = anyFieldOn && hasAnyValue && scopeKinds.length > 0;

  const imageCount = targets.filter((n) => n.kind === 'image').length;
  const videoCount = targets.filter((n) => n.kind === 'video').length;
  const audioCount = targets.filter((n) => n.kind === 'audio').length;
  const textCount = targets.filter((n) => n.kind === 'text').length;
  /** 选区里有几类卡片：>1 时才需要行标签与「作用范围」 */
  const kindTabs = kindCount;

  /** 记住这次的选择（只记"合法的、有意义的"），下次打开原样带回 */
  const persistPrefs = (apply: boolean) => {
    // 取消时**只记开关**，不记取值 —— 取消意味着"这次的选择不算数"，
    // 把取消前随手点的值记下来会污染下次的默认值。
    // ★ 第 35 轮：`scope*` 不再写入记忆（见 scopeImage 初始化处的长注释）——
    //   作用范围描述的是"这次选区里有哪几类卡"，跨选区沿用必然是错的。
    //   这里仍然把 scope 写进去只是为了不让类型报错，读取方已经完全不看它。
    saveBatchPrefs({
      useModel, useRatio, useClarity,
      modelImage: apply && useModel ? imageModelId : (prefs?.modelImage || ''),
      modelVideo: apply && useModel ? videoModelId : (prefs?.modelVideo || ''),
      modelAudio: apply && useModel ? audioModelId : (prefs?.modelAudio || ''),
      modelText: apply && useModel ? textModelId : (prefs?.modelText || ''),
      ratio: apply && useRatio ? ratio : (prefs?.ratio || ''),
      clarityImage: apply && useClarity ? clarityImage : (prefs?.clarityImage || ''),
      clarityVideo: apply && useClarity ? clarityVideo : (prefs?.clarityVideo || ''),
      audioFormat: apply ? audioFormat : (prefs?.audioFormat || ''),
      audioSampleRate: apply ? audioSampleRate : (prefs?.audioSampleRate || ''),
      audioMode: apply ? audioMode : (prefs?.audioMode || ''),
      textTokens: apply ? textTokens : (prefs?.textTokens || ''),
      textTemperature: apply ? textTemp : (prefs?.textTemperature || ''),
    });
  };

  /**
   * 预演：选中档位里，有多少张卡**该档位不适用**。
   *
   * ★ 现在的分工变了（用户要求「不支持就变灰点不了」）：
   *   - 全都不支持 → 胶囊已置灰，用户根本点不到，**不需要**再警告；
   *   - 部分支持 → 胶囊可点，但会有一批卡被跳过，这时才需要一句说明。
   *   所以这里只统计"部分跳过"的情形，做到"能点就说明能生效一些、并告诉你多少"。
   */
  const unsupported = (() => {
    const hits: {title: string; reason: string}[] = [];
    for (const n of targets) {
      // 音频没有比例/清晰度概念，不进这套预演
      if (n.kind !== 'image' && n.kind !== 'video') continue;
      // 与置灰判定同口径：选了新模型就用**该类型**的新模型算，否则用卡片自己的
      const picked = pickedSpecOf(n.kind);
      const mid = useModel && picked ? picked.id : (n.params?.model || '');
      const spec = allSpecs.find((m) => m.id === mid)
        || catalogModels.find((m) => m.capability === n.kind);
      const check = (key: 'ratio' | 'clarity' | 'resolution', want: string) => {
        if (!want) return;
        const opts = spec?.params?.[key]?.options;
        const list = Array.isArray(opts) && opts.length ? opts.map(String) : [];
        if (list.length && !list.includes(want)) {
          hits.push({title: n.title || '未命名', reason: want});
        }
      };
      if (useRatio) check('ratio', ratio);
      if (useClarity) {
        if (n.kind === 'video') check('resolution', clarityVideo);
        else check('clarity', clarityImage);
      }
    }
    return hits;
  })();

  return (
    <div className="canvas-help-backdrop" onClick={onClose}>
      {/*
        ★★ 第 35 轮：用户「内容太满溢出」。
        根因有两条，必须一起修：
        ① `.canvas-batch-params` 为了不让模型下拉被裁，把继承来的 `overflow:auto`
           覆盖成了 `overflow:visible` —— 于是内容高 934px、外框只有 633px 时
           **既不撑开也不滚动**，直接被 backdrop 裁掉，footer 落在 y=1036（视口 790）。
        ② 所有分区平铺在一个滚动容器里，滚到底才能看到「确认应用」，
           而用户根本不知道该滚 —— 看上去就是"弹窗被切了一半"。
        ⇒ 改成三段式：header / 可滚动的 `.canvas-batch-body` / 固定的 footer。
          下拉浮层改用 position:fixed（见 BatchModelPicker），不再依赖祖先 overflow:visible。
      */}
      <div ref={dialogRef} tabIndex={-1} className="canvas-arrange canvas-batch-params" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true">
        <header>
          <strong>批量调参</strong>
          <button type="button" className="canvas-inspector-close" onClick={onClose} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
        </header>
        <div className="canvas-batch-body">
        <p className="canvas-arrange-hint">
          调整组内 <b>{targets.length}</b> 个节点的参数
          {kindCount > 1 && (
            <span className="canvas-batch-kinds">
              （{[hasImage && `${imageCount} 图片`, hasVideo && `${videoCount} 视频`, hasAudio && `${audioCount} 音频`, hasText && `${textCount} 文本`].filter(Boolean).join(' · ')}）
            </span>
          )}
          {/* 只有真的读到记忆时才提示，否则会出现"说记住了其实没有"的假承诺 */}
          {prefs && (prefs.useModel || prefs.useRatio || prefs.useClarity) && (
            <span className="canvas-batch-memo">已恢复上次设置</span>
          )}
        </p>

        {/* 作用范围：混选才需要，单选类型时不占空间。
            ★ 这里是**可独立开关的多选**（不是二选一的分段控件）：用户可能只想改视频，
              所以做成"带勾的过滤片"—— 带勾 = 会作用于这一类，无勾 = 跳过这一类。
              用分段控件的视觉会让人以为是单选，只能二者留一。 */}
        {kindCount > 1 && (
          <section className="canvas-arrange-sec canvas-batch-scope">
            <h5>作用范围</h5>
            <div className="canvas-batch-segments">
              <button type="button" role="switch" aria-checked={scopeImage} className={`canvas-batch-seg ${scopeImage ? 'is-on' : ''}`} onClick={() => setScopeImage((v) => !v)}>
                <span className="canvas-batch-segcheck" aria-hidden="true">
                  {scopeImage && <Check size={9} strokeWidth={3.2} />}
                </span>
                图片 <em>{imageCount}</em>
              </button>
              <button type="button" role="switch" aria-checked={scopeVideo} className={`canvas-batch-seg ${scopeVideo ? 'is-on' : ''}`} onClick={() => setScopeVideo((v) => !v)}>
                <span className="canvas-batch-segcheck" aria-hidden="true">
                  {scopeVideo && <Check size={9} strokeWidth={3.2} />}
                </span>
                视频 <em>{videoCount}</em>
              </button>
              {hasAudio && (
                <button type="button" role="switch" aria-checked={scopeAudio} className={`canvas-batch-seg ${scopeAudio ? 'is-on' : ''}`} onClick={() => setScopeAudio((v) => !v)}>
                  <span className="canvas-batch-segcheck" aria-hidden="true">
                    {scopeAudio && <Check size={9} strokeWidth={3.2} />}
                  </span>
                  音频 <em>{audioCount}</em>
                </button>
              )}
              {hasText && (
                <button type="button" role="switch" aria-checked={scopeText} className={`canvas-batch-seg ${scopeText ? 'is-on' : ''}`} onClick={() => setScopeText((v) => !v)}>
                  <span className="canvas-batch-segcheck" aria-hidden="true">
                    {scopeText && <Check size={9} strokeWidth={3.2} />}
                  </span>
                  文本 <em>{textCount}</em>
                </button>
              )}
            </div>
          </section>
        )}

        <section className="canvas-arrange-sec">
          <div className="canvas-batch-head">
            <h5>生成模型</h5>
            <BatchToggle on={useModel} onChange={setUseModel} label="应用模型" />
          </div>
          {/**
            * ★ 按卡片类型**各给一个下拉**（用户：「不同节点卡片类型你要读取不同模型了」
            *   「是 Seedream 5.0 Pro 不是 Seedance 2.0 mini」「模型接口那都有模型标识的」）。
            *
            * 上一版把所有模型塞进一个下拉、一个 state：混选时选一个只能覆盖一种类型，
            * 只选图片时还可能显示着上次留下的视频模型 —— 两类的模型集合本来就不相交，
            * 用一个字段表达"两类各自的模型"在结构上就是错的。
            * 现在图片走 image 能力（Seedream 系列）、视频走 video 能力（Seedance 系列），
            * 各读各的、各写各的，互不影响。
            */}
          <div className={`canvas-batch-field ${useModel ? '' : 'is-off'}`}>
            {scopeImage && hasImage && (
              <div className="canvas-batch-modelrow">
                {kindTabs > 1 && <span className="canvas-batch-rowlabel">图片</span>}
                <BatchModelPicker
                  kindLabel="图片"
                  models={modelsOf('image')}
                  value={imageModelId}
                  open={modelOpen === 'image'}
                  disabled={!useModel}
                  onToggle={() => setModelOpen((cur) => (cur === 'image' ? null : 'image'))}
                  onChange={(id) => { setImageModelId(id); setModelOpen(null); }}
                />
              </div>
            )}
            {scopeVideo && hasVideo && (
              <div className="canvas-batch-modelrow">
                {kindTabs > 1 && <span className="canvas-batch-rowlabel">视频</span>}
                <BatchModelPicker
                  kindLabel="视频"
                  models={modelsOf('video')}
                  value={videoModelId}
                  open={modelOpen === 'video'}
                  disabled={!useModel}
                  onToggle={() => setModelOpen((cur) => (cur === 'video' ? null : 'video'))}
                  onChange={(id) => { setVideoModelId(id); setModelOpen(null); }}
                />
              </div>
            )}
            {/* ★ 音频也有模型（当前是 CosyVoice TTS）。第 33 轮补：
                用户「音频没有加批量设置」—— 之前音频卡连"模型"这一项都选不了，
                而它其实同样有 TTS 模型可换。 */}
            {scopeAudio && hasAudio && (
              <div className="canvas-batch-modelrow">
                {kindTabs > 1 && <span className="canvas-batch-rowlabel">音频</span>}
                <BatchModelPicker
                  kindLabel="音频"
                  models={modelsOf('audio')}
                  value={audioModelId}
                  open={modelOpen === 'audio'}
                  disabled={!useModel}
                  onToggle={() => setModelOpen((cur) => (cur === 'audio' ? null : 'audio'))}
                  onChange={(id) => { setAudioModelId(id); setModelOpen(null); }}
                />
              </div>
            )}
            {/* 文本卡也有模型（capability=text）。用户「文本节点也要增加批量操作」，
                模型是文本最具意义的一项 —— 换模型就是换"写这句话的脑子"。 */}
            {scopeText && hasText && (
              <div className="canvas-batch-modelrow">
                {kindTabs > 1 && <span className="canvas-batch-rowlabel">文本</span>}
                <BatchModelPicker
                  kindLabel="文本"
                  models={modelsOf('text')}
                  value={textModelId}
                  open={modelOpen === 'text'}
                  disabled={!useModel}
                  onToggle={() => setModelOpen((cur) => (cur === 'text' ? null : 'text'))}
                  onChange={(id) => { setTextModelId(id); setModelOpen(null); }}
                />
              </div>
            )}
            {useModel && (
              <p className="canvas-batch-note">
                {(() => {
                  // ★ 说清两件事：① 会改写哪些类型的卡；② 哪些类型没选、为什么没选。
                  //   上一版只说"将作用于 N 个节点"，用户看不出视频那边为什么是空的。
                  const willWrite: string[] = [];
                  const blank: string[] = [];
                  if (scopeImage && hasImage) {
                    if (imageModelId) willWrite.push(`图片 ${imageCount} 个`);
                    else blank.push(`图片（${imageCount} 个）`);
                  }
                  if (scopeVideo && hasVideo) {
                    if (videoModelId) willWrite.push(`视频 ${videoCount} 个`);
                    else blank.push(`视频（${videoCount} 个）`);
                  }
                  if (scopeAudio && hasAudio) {
                    if (audioModelId) willWrite.push(`音频 ${audioCount} 个`);
                    else blank.push(`音频（${audioCount} 个）`);
                  }
                  if (scopeText && hasText) {
                    if (textModelId) willWrite.push(`文本 ${textCount} 个`);
                    else blank.push(`文本（${textCount} 个）`);
                  }
                  if (!willWrite.length) return '各类型分别选模型；未选的类型保持原值';
                  const tail = blank.length ? `；${blank.join('、')}未选 → 保持原值` : '';
                  return `将改写 ${willWrite.join('、')} 卡片的模型${tail}`;
                })()}
              </p>
            )}
          </div>
        </section>

        {showClaritySec && (
        <section className="canvas-arrange-sec">
          <div className="canvas-batch-head">
            <h5>清晰度</h5>
            <BatchToggle on={useClarity} onChange={setUseClarity} label="应用清晰度" />
          </div>
          <div className={`canvas-batch-field ${useClarity ? '' : 'is-off'}`}>
            {/* 图片与视频的清晰度是两个量纲，分开显示；只选中一类时只出现一行 */}
            {showImageClarity && (
              <div className="canvas-batch-chiprow">
                {showVideoClarity && <span className="canvas-batch-rowlabel">图片</span>}
                <div className="canvas-batch-chips">
                  {clarityImageOpts.map((opt) => {
                    // ★ 用户要求：「选择对应模型 比如不支持1080P就变灰色点不了」
                    //   选中的模型里没有任何一张支持这个档位 → 直接禁用，从源头杜绝"点了不生效"。
                    const n = supportCount('image', 'clarity', opt);
                    const off = useClarity && n === 0;
                    return (
                      <button key={opt} type="button" disabled={!useClarity || off}
                        title={off ? `当前作用范围内没有图片节点支持 ${opt}` : n > 0 && n < kindTotal('image') ? `仅 ${n}/${kindTotal('image')} 张图片支持 ${opt}` : undefined}
                        className={`canvas-batch-chip ${clarityImage === opt ? 'is-active' : ''} ${!clarityImage && curClarityImage === opt ? 'is-current' : ''} ${off ? 'is-unsupported' : ''}`}
                        onClick={() => setClarityImage(clarityImage === opt ? '' : opt)}>{opt}</button>
                    );
                  })}
                </div>
              </div>
            )}
            {showVideoClarity && (
              <div className="canvas-batch-chiprow">
                {showImageClarity && <span className="canvas-batch-rowlabel">视频</span>}
                <div className="canvas-batch-chips">
                  {clarityVideoOpts.map((opt) => {
                    const n = supportCount('video', 'resolution', opt);
                    const off = useClarity && n === 0;
                    return (
                      <button key={opt} type="button" disabled={!useClarity || off}
                        title={off ? `当前作用范围内没有视频节点支持 ${opt}` : n > 0 && n < kindTotal('video') ? `仅 ${n}/${kindTotal('video')} 个视频支持 ${opt}` : undefined}
                        className={`canvas-batch-chip ${clarityVideo === opt ? 'is-active' : ''} ${!clarityVideo && curClarityVideo === opt ? 'is-current' : ''} ${off ? 'is-unsupported' : ''}`}
                        onClick={() => setClarityVideo(clarityVideo === opt ? '' : opt)}>{opt}</button>
                    );
                  })}
                </div>
              </div>
            )}
            {useClarity && !clarityImage && !clarityVideo && (
              <p className="canvas-batch-note">点一个档位；未点则为"保持原值"</p>
            )}
          </div>
        </section>
        )}

        {showRatioSec && (
        <section className="canvas-arrange-sec">
          <div className="canvas-batch-head">
            <h5>比例</h5>
            <BatchToggle on={useRatio} onChange={setUseRatio} label="应用比例" />
          </div>
          <div className={`canvas-batch-field ${useRatio ? '' : 'is-off'}`}>
            <div className="canvas-batch-ratio-grid">
              {ratioOptions.map((opt) => {
                // 比例同理：所有在作用范围内的卡都不支持这一档 → 置灰
                const n = scopeKinds.reduce((sum, k) => sum + supportCount(k, 'ratio', opt), 0);
                const total = scopeKinds.reduce((sum, k) => sum + kindTotal(k), 0);
                const off = useRatio && n === 0;
                return (
                  <button key={opt} type="button" disabled={!useRatio || off}
                    title={off ? `当前作用范围内没有节点支持 ${opt}` : n > 0 && n < total ? `仅 ${n}/${total} 个节点支持 ${opt}` : undefined}
                    className={`canvas-batch-ratio-item ${ratio === opt ? 'is-active' : ''} ${!ratio && curRatio === opt ? 'is-current' : ''} ${off ? 'is-unsupported' : ''}`}
                    onClick={() => setRatio(ratio === opt ? '' : opt)}
                  >
                    <span className="canvas-batch-ratio-visual" aria-hidden="true">
                      {opt === 'adaptive' ? <i className="canvas-batch-ratio-auto">A</i> : <RatioIcon r={opt} />}
                    </span>
                    <b>{opt === 'adaptive' ? '自适应' : opt}</b>
                  </button>
                );
              })}
            </div>
            {/* 现状说明（第 35 轮）：比例跨类型冲突时蓝点会主动隐藏，
                不说清楚用户会以为"这里又没自动选择"。 */}
            {!ratio && !curRatio && useRatio && (
              <p className="canvas-batch-note">图片与视频的现有比例不一致，不预标；点选即统一</p>
            )}
          </div>
        </section>
        )}

        {/* ★ 音频专属参数区（第 33 轮）。用户「音频没有加批量设置」。
            音频没有"比例 / 清晰度"这两个概念，硬套图片那套只会得到一排置灰胶囊；
            它真正能批量设的是：输出格式 / 采样率 / 合成模式。
            选项一律取音频模型 schema（catalog 未到才用 AUDIO_* 兜底）。 */}
        {scopeAudio && hasAudio && (() => {
          const spec = modelsOf('audio')[0];
          const optsOf = (key: 'outputFormat' | 'sampleRate' | 'ttsMode', fb: string[]) => {
            const o = spec?.params?.[key]?.options;
            return Array.isArray(o) && o.length ? o.map(String) : fb;
          };
          const fmtOpts = optsOf('outputFormat', AUDIO_FORMATS);
          const rateOpts = optsOf('sampleRate', AUDIO_SAMPLE_RATES);
          const modeOpts = optsOf('ttsMode', AUDIO_MODES);
          return (
            <section className="canvas-arrange-sec canvas-batch-audio">
              <div className="canvas-batch-head">
                <h5>音频</h5>
              </div>
              <div className="canvas-batch-field">
                <div className="canvas-batch-chiprow">
                  <span className="canvas-batch-rowlabel">格式</span>
                  <div className="canvas-batch-chips">
                    {fmtOpts.map((opt) => (
                      <button key={opt} type="button"
                        className={`canvas-batch-chip ${audioFormat === opt ? 'is-active' : ''} ${!audioFormat && curAudioFormat === opt ? 'is-current' : ''}`}
                        onClick={() => setAudioFormat(audioFormat === opt ? '' : opt)}>{opt.toUpperCase()}</button>
                    ))}
                  </div>
                </div>
                <div className="canvas-batch-chiprow">
                  <span className="canvas-batch-rowlabel">采样</span>
                  <div className="canvas-batch-chips">
                    {rateOpts.map((opt) => (
                      <button key={opt} type="button"
                        className={`canvas-batch-chip ${audioSampleRate === opt ? 'is-active' : ''} ${!audioSampleRate && curAudioRate === opt ? 'is-current' : ''}`}
                        onClick={() => setAudioSampleRate(audioSampleRate === opt ? '' : opt)}>{audioRateLabel(opt)}</button>
                    ))}
                  </div>
                </div>
                <div className="canvas-batch-chiprow">
                  <span className="canvas-batch-rowlabel">模式</span>
                  <div className="canvas-batch-chips">
                    {modeOpts.map((opt) => (
                      <button key={opt} type="button"
                        className={`canvas-batch-chip ${audioMode === opt ? 'is-active' : ''} ${!audioMode && curAudioMode === opt ? 'is-current' : ''}`}
                        onClick={() => setAudioMode(audioMode === opt ? '' : opt)}>{AUDIO_MODE_LABELS[opt] || opt}</button>
                    ))}
                  </div>
                </div>
                <p className="canvas-batch-note">作用于 {audioCount} 个音频节点；未选的项保持原值</p>
              </div>
            </section>
          );
        })()}

        {/* ★ 文本节点专属参数区（第 34 轮）。用户「文本节点也要增加批量操作」。
            文本没有比例 / 清晰度 / 采样率，它真正能批量设的是：
              · 生成长度（max_tokens）：短 400 / 中 1200 / 长 3000，与单卡弹窗同源；
              · 采样温度：0~2，步长 0.1。
            模型选择在上面「生成模型」区（文本也走 model 字段，只是 capability=text）。 */}
        {scopeText && hasText && (
          <section className="canvas-arrange-sec canvas-batch-text">
            <div className="canvas-batch-head">
              <h5>文本</h5>
            </div>
            <div className="canvas-batch-field">
              <div className="canvas-batch-chiprow">
                <span className="canvas-batch-rowlabel">长度</span>
                <div className="canvas-batch-chips">
                  {textTokenOpts.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      className={`canvas-batch-chip ${textTokens === opt ? 'is-active' : ''} ${!textTokens && curTextTokens === opt ? 'is-current' : ''}`}
                      title={`约 ${opt} token ≈ ${Math.round(Number(opt) * 1.5)} 汉字`}
                      onClick={() => setTextTokens(textTokens === opt ? '' : opt)}
                    >{textTokenLabel(opt)}</button>
                  ))}
                </div>
              </div>
              <div className="canvas-batch-chiprow">
                <span className="canvas-batch-rowlabel">温度</span>
                <div className="canvas-batch-chips">
                  {TEXT_TEMP_OPTS.map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      className={`canvas-batch-chip ${textTemp === opt ? 'is-active' : ''} ${!textTemp && curTextTemp === opt ? 'is-current' : ''}`}
                      title={opt === '0.2' ? '0.2 · 更稳定、更贴提示词' : opt === '0.7' ? '0.7 · 平衡（默认）' : '1.2 · 更发散、更有想象力'}
                      onClick={() => setTextTemp(textTemp === opt ? '' : opt)}
                    >{opt}</button>
                  ))}
                </div>
              </div>
              <p className="canvas-batch-note">
                作用于 {textCount} 个文本节点
                {(curTextTokens || curTextTemp) && !textTokens && !textTemp ? (
                  <span className="canvas-batch-curhint">；蓝点是这批节点当前的值，点选才会改动</span>
                ) : '；未选的项保持原值'}
              </p>
            </div>
          </section>
        )}

        {/* 部分跳过提示：只在这种"能点但不会全覆盖"的情况下出现。
            全都不支持的档位已经置灰点不了，不需要再说一遍。 */}
        {unsupported.length > 0 && (
          <p className="canvas-batch-warn">
            <CircleAlert size={12} strokeWidth={2.1} aria-hidden="true" />
            <span>
              {unsupported.slice(0, 2).map((u) => u.title).join('、')}
              {unsupported.length > 2 ? ` 等 ${unsupported.length} 个节点` : ''}
              不支持该档位，将保留原值；其余节点正常应用
            </span>
          </p>
        )}

        </div>
        <footer className="canvas-arrange-foot">
          <span className="canvas-arrange-spacer" />
          <button type="button" onClick={() => { persistPrefs(false); setModelOpen(null); onClose(); }}>取消</button>
          <button
            type="button"
            className="is-primary"
            disabled={!canApply}
            onClick={() => {
              // 先记偏好再应用：即便应用过程抛错，用户的习惯也已经被记住
              persistPrefs(true);
              // ★ 模型分类型下发：图片模型只写图片卡、视频模型只写视频卡。
              //   两边都可能是 undefined（该项关掉 / 该类型没选 / 不在作用范围），
              //   applyBatchParams 会按缺失字段跳过。
              const imgSpec = useModel && scopeImage ? pickedSpecOf('image') : undefined;
              const vidSpec = useModel && scopeVideo ? pickedSpecOf('video') : undefined;
              const audSpec = useModel && scopeAudio ? pickedSpecOf('audio') : undefined;
              const txtSpec = useModel && scopeText ? pickedSpecOf('text') : undefined;
              onApply(
                {
                  modelImage: imgSpec ? {id: imgSpec.id, providerId: imgSpec.providerId, remoteModel: imgSpec.remoteModel} : undefined,
                  modelVideo: vidSpec ? {id: vidSpec.id, providerId: vidSpec.providerId, remoteModel: vidSpec.remoteModel} : undefined,
                  modelAudio: audSpec ? {id: audSpec.id, providerId: audSpec.providerId, remoteModel: audSpec.remoteModel} : undefined,
                  modelText: txtSpec ? {id: txtSpec.id, providerId: txtSpec.providerId, remoteModel: txtSpec.remoteModel} : undefined,
                  ratio: useRatio && ratio ? ratio : undefined,
                  clarityImage: useClarity && clarityImage ? clarityImage : undefined,
                  clarityVideo: useClarity && clarityVideo ? clarityVideo : undefined,
                  audioFormat: audioFormat || undefined,
                  audioSampleRate: audioSampleRate || undefined,
                  audioMode: audioMode || undefined,
                  textMaxTokens: textTokens ? Number(textTokens) : undefined,
                  textTemperature: textTemp ? Number(textTemp) : undefined,
                },
                {image: scopeImage, video: scopeVideo, audio: scopeAudio, text: scopeText},
              );
            }}
          >确认应用</button>
        </footer>
      </div>
    </div>
  );
}

/** 批量弹窗里的「是否应用这一项」拨动开关（与模型接口弹窗的开关同一套语言） */
function BatchToggle({on, onChange, label}: {on: boolean; onChange: (v: boolean) => void; label: string}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      className={`canvas-batch-toggle ${on ? 'is-on' : ''}`}
      onClick={() => onChange(!on)}
    >
      <i />
    </button>
  );
}

/**
 * 按卡片类型选择模型的单个下拉（图片一个、视频一个）。
 *
 * ★ 为什么拆成独立组件：#31 那版用一个大下拉把两类模型分组列在一起，
 *   状态却只有一个 —— 于是"选了图片模型"会把视频模型的记忆冲掉，反之亦然。
 *   现在每个 picker 只认自己这一类的模型列表（调用方已按 `capability` 过滤好），
 *   结构上就不可能串类。
 *
 * ★ `badge` 用模型自带的（Pro / mini / 2.5…），与「模型接口」列表里显示的一致，
 *   用户能直接把两边对起来看。
 */
function BatchModelPicker({kindLabel, models, value, open, disabled, onToggle, onChange}: {
  kindLabel: string;
  models: ModelSpec[];
  value: string;
  open: boolean;
  disabled: boolean;
  onToggle: () => void;
  onChange: (id: string) => void;
}) {
  const picked = models.find((m) => m.id === value);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  /**
   * ★★ 第 35 轮：弹窗改成「header / 可滚动 body / 固定 footer」三段式后，
   *   `.canvas-batch-body` 有了 `overflow-y:auto`，会**裁掉**绝对定位的下拉浮层
   *   （浮层会跟着 body 一起被卷走，甚至完全不显示）。
   *
   *   三条可选路线，只有第三条是真的稳：
   *   ① 把 body 改回 visible → 溢出的老问题立刻回来，等于没修；
   *   ② 用 CSS `position:fixed` 但仍 `top:calc(100%+6px)` → fixed 的百分比是相对
   *      视口算的，不是相对按钮，位置会完全错乱；
   *   ③ 用 **fixed + 运行时算出的像素坐标**（按按钮 rect）。固定定位脱离所有祖先的
   *      overflow 裁剪，且坐标精确。这就是现在的做法。
   *
   *   ★ 必须 useLayoutEffect：useEffect 在绘制之后才跑，首批一定画在 (0,0)，
   *     用户能看到一次跳位（本项目在 ModelSelect 处已踩过同样一帧闪动）。
   *   ★ 滚动 / 窗口尺寸变化时重算：fixed 元素不会跟着容器滚动，
   *     不重算的话用户一滚 body 下拉就与按钮脱开了。
   *   ★ 方向自适应复用 ModelSelect 的口径：下方装不下且上方更宽敞 → 向上弹。
   *     阈值用真实列表高度（items×32 + padding 12），不写死数字。
   */
  const [pos, setPos] = useState<{top: number; left: number; width: number; up: boolean} | null>(null);
  useLayoutEffect(() => {
    if (!open || disabled) { setPos(null); return; }
    const measure = () => {
      const el = wrapRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const need = Math.min(models.length * 32 + 12, 264);
      const below = window.innerHeight - r.bottom - 8;
      const above = r.top - 8;
      const up = below < need && above > below;
      setPos({
        top: up ? Math.max(8, r.top - Math.min(need, above) - 6) : r.bottom + 6,
        left: r.left,
        width: r.width,
        up,
      });
    };
    measure();
    window.addEventListener('resize', measure);
    // capture=true：要能收到 .canvas-batch-body 的滚动（scroll 不冒泡）
    window.addEventListener('scroll', measure, true);
    return () => {
      window.removeEventListener('resize', measure);
      window.removeEventListener('scroll', measure, true);
    };
  }, [open, disabled, models.length]);
  /**
   * badge 只在**确实补足了信息**时才显示。
   * ★ 踩过两次：
   *   · `Seedream 5.0 Pro` 的 badge 是 `Pro`，label 已以它结尾 → 拼出「ProPro」；
   *   · `CosyVoice v3.5 Flash` 的 badge 是 `TTS`，但它本身就以 `TTS` 结尾
   *     （"…Flash" 后面直接接 TTS）→ 拼出「FlashTTS」。
   *   规则：label 里已含 badge 就连着不重复显示。
   */
  const badge = String(picked?.badge || '');
  const showBadge = Boolean(badge) && !String(picked?.label || '').includes(badge);
  return (
    <div className="canvas-batch-selectwrap" ref={wrapRef}>
      <button
        type="button"
        className={`canvas-batch-select ${open ? 'is-open' : ''}`}
        onClick={onToggle}
        disabled={disabled}
        title={disabled ? '先打开「生成模型」开关' : `为${kindLabel}卡片选择模型`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${kindLabel}模型`}
      >
        <span className="canvas-batch-select-label">
          {picked ? picked.label : `选择${kindLabel}模型`}
          {showBadge && <em className="canvas-batch-badge">{picked!.badge}</em>}
        </span>
        <ChevronDown size={13} strokeWidth={2.2} aria-hidden="true" />
      </button>
      {open && !disabled && pos && (
        <div
          className="canvas-batch-dropdown is-fixed"
          role="listbox"
          style={{top: pos.top, left: pos.left, width: pos.width}}
        >
          {models.map((m) => (
            <button
              key={m.id}
              type="button"
              role="option"
              aria-selected={value === m.id}
              className={`canvas-batch-dropitem ${value === m.id ? 'is-active' : ''}`}
              onClick={() => onChange(m.id)}
            >
              <span>{m.label}</span>
              {value === m.id && <Check size={13} strokeWidth={2.4} aria-hidden="true" />}
            </button>
          ))}
          {models.length === 0 && <p className="canvas-batch-note">这一类没有可用模型，请到「模型接口」启用</p>}
        </div>
      )}
    </div>
  );
}

function GenerationBudgetDialog({
  onClose,
  onMessage,
}: {
  onClose: () => void;
  onMessage: (message: string) => void;
}) {
  const dialogRef = useDialogFocusTrap();
  const [config, setConfig] = useState<BudgetConfig>({
    enabled: false,
    currency: 'CNY',
    dailyLimit: 0,
    monthlyLimit: 0,
    maxRequestCost: 0,
    allowUnknownCost: false,
  });
  const [status, setStatus] = useState<BudgetStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    setLoading(true);
    void getBudgetStatus()
      .then((data) => {
        setStatus(data);
        setConfig(data.budget);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [onClose]);

  const updateNumber = (key: 'dailyLimit' | 'monthlyLimit' | 'maxRequestCost', value: string) => {
    setConfig((current) => ({
      ...current,
      [key]: Math.max(0, Number(value) || 0),
    }));
  };

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const data = await saveBudgetConfig(config);
      setStatus(data);
      setConfig(data.budget);
      onMessage(config.enabled ? '生成预算保护已启用' : '生成预算保护已关闭');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const symbol = config.currency === 'USD' ? '$' : '¥';
  const dailyPercent = config.dailyLimit > 0
    ? Math.min(100, (Number(status?.daySpend || 0) / config.dailyLimit) * 100)
    : 0;
  const monthlyPercent = config.monthlyLimit > 0
    ? Math.min(100, (Number(status?.monthSpend || 0) / config.monthlyLimit) * 100)
    : 0;

  return (
    <div className="canvas-prompt-overlay canvas-budget-overlay" onClick={onClose}>
      <section
        className="canvas-budget-dialog"
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="生成预算保护"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <span><Wallet size={16} strokeWidth={1.8} aria-hidden="true" /><strong>生成预算保护</strong></span>
          <button type="button" onClick={onClose} aria-label="关闭"><X size={15} strokeWidth={2} /></button>
        </header>
        {loading ? (
          <p className="canvas-budget-loading">正在读取预算配置…</p>
        ) : (
          <>
            <label className="canvas-budget-toggle">
              <span>
                <strong>启用预算校验</strong>
                <small>每次提交生成前由服务端校验，前端显示的金额不能绕过。</small>
              </span>
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(event) => setConfig((current) => ({...current, enabled: event.target.checked}))}
              />
            </label>
            <div className="canvas-budget-usage">
              <div>
                <span>今日已占用</span>
                <strong>{symbol}{Number(status?.daySpend || 0).toFixed(2)}</strong>
                <i><b style={{width: `${dailyPercent}%`}} /></i>
              </div>
              <div>
                <span>本月已占用</span>
                <strong>{symbol}{Number(status?.monthSpend || 0).toFixed(2)}</strong>
                <i><b style={{width: `${monthlyPercent}%`}} /></i>
              </div>
            </div>
            <div className="canvas-budget-fields">
              <label>
                <span>币种</span>
                <select
                  value={config.currency}
                  onChange={(event) => setConfig((current) => ({...current, currency: event.target.value}))}
                >
                  <option value="CNY">人民币 CNY</option>
                  <option value="USD">美元 USD</option>
                </select>
              </label>
              <label>
                <span>单次上限</span>
                <input type="number" min="0" step="0.01" value={config.maxRequestCost} onChange={(event) => updateNumber('maxRequestCost', event.target.value)} />
                <small>0 = 不限制单次</small>
              </label>
              <label>
                <span>每日上限</span>
                <input type="number" min="0" step="0.01" value={config.dailyLimit} onChange={(event) => updateNumber('dailyLimit', event.target.value)} />
                <small>0 = 不限制每日</small>
              </label>
              <label>
                <span>每月上限</span>
                <input type="number" min="0" step="0.01" value={config.monthlyLimit} onChange={(event) => updateNumber('monthlyLimit', event.target.value)} />
                <small>0 = 不限制每月</small>
              </label>
            </div>
            <label className="canvas-budget-unknown">
              <input
                type="checkbox"
                checked={config.allowUnknownCost}
                onChange={(event) => setConfig((current) => ({...current, allowUnknownCost: event.target.checked}))}
              />
              <span>模型没有计费规则时仍允许生成</span>
            </label>
            {!config.allowUnknownCost && config.enabled && (
              <p className="canvas-budget-hint">预算保护开启时，缺少 pricing 的模型会被拒绝，建议先补齐模型计费规则。</p>
            )}
            {error && <p className="canvas-budget-error">{error}</p>}
            <footer>
              <button type="button" onClick={onClose}>取消</button>
              <button type="button" className="is-primary" disabled={saving} onClick={() => void save()}>
                {saving ? '保存中…' : '保存预算设置'}
              </button>
            </footer>
          </>
        )}
      </section>
    </div>
  );
}

function ProjectVersionsDialog({
  projectId,
  busy,
  onClose,
  onMessage,
}: {
  projectId: string;
  busy: boolean;
  onClose: () => void;
  onMessage: (message: string) => void;
}) {
  const dialogRef = useDialogFocusTrap();
  const [versions, setVersions] = useState<ProjectVersion[]>([]);
  const [currentRevision, setCurrentRevision] = useState(0);
  const [selected, setSelected] = useState<ProjectVersionPreview | null>(null);
  const [title, setTitle] = useState('');
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await getProjectVersions(projectId);
      setVersions(data.versions || []);
      setCurrentRevision(Number(data.currentRevision || 0));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose();
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [onClose]);

  const selectVersion = async (version: ProjectVersion) => {
    setWorking(true);
    setError('');
    setConfirmRestore(false);
    try {
      setSelected(await previewProjectVersion(projectId, version.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(false);
    }
  };

  const saveVersion = async () => {
    if (busy || working) return;
    setWorking(true);
    setError('');
    try {
      const data = await createProjectVersion(
        projectId,
        title.trim() || '手动版本',
        currentRevision,
      );
      setTitle('');
      setCurrentRevision(Number(data.currentRevision || currentRevision));
      await refresh();
      onMessage('已保存项目版本');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorking(false);
    }
  };

  const restoreVersion = async () => {
    if (!selected || busy || working) return;
    setWorking(true);
    setError('');
    try {
      await restoreProjectVersion(projectId, selected.id, currentRevision);
      onMessage('项目已恢复，正在重新载入画布');
      window.setTimeout(() => window.location.reload(), 180);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setWorking(false);
    }
  };

  const formatTime = (value: string) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? value
      : date.toLocaleString('zh-CN', {hour12: false});
  };

  return (
    <div className="canvas-prompt-overlay canvas-version-overlay" onClick={onClose}>
      <section
        className="canvas-version-dialog"
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="项目版本历史"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="canvas-version-head">
          <span className="canvas-version-title">
            <History size={16} strokeWidth={1.8} aria-hidden="true" />
            <strong>项目版本</strong>
            <small>当前 revision {currentRevision}</small>
          </span>
          <button type="button" onClick={onClose} aria-label="关闭">
            <X size={15} strokeWidth={2.1} aria-hidden="true" />
          </button>
        </header>
        <div className="canvas-version-create">
          <input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="版本说明，例如：角色资产定稿"
            maxLength={80}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void saveVersion();
            }}
          />
          <button
            type="button"
            disabled={busy || working}
            onClick={() => void saveVersion()}
            title={busy ? '画布正在保存，请稍后再创建版本' : '保存当前画布为手动版本'}
          >
            <Plus size={13} strokeWidth={2} aria-hidden="true" />
            保存当前
          </button>
        </div>
        {busy && <p className="canvas-version-note">画布仍有未完成保存，版本操作已暂时锁定。</p>}
        {error && <p className="canvas-version-error">{error}</p>}
        <div className="canvas-version-body">
          <div className="canvas-version-list">
            {loading ? (
              <p className="canvas-version-empty">正在读取版本…</p>
            ) : versions.length === 0 ? (
              <p className="canvas-version-empty">还没有可恢复的项目版本。</p>
            ) : versions.map((version) => (
              <button
                key={version.id}
                type="button"
                className={`canvas-version-item ${selected?.id === version.id ? 'is-active' : ''}`}
                onClick={() => void selectVersion(version)}
              >
                <span className="canvas-version-item-main">
                  <strong>{version.title || '未命名版本'}</strong>
                  <small>{formatTime(version.createdAt)}</small>
                </span>
                <span className="canvas-version-item-meta">
                  <em className={version.kind === 'manual' ? 'is-manual' : ''}>
                    {version.kind === 'manual' ? '手动' : '自动'}
                  </em>
                  <span>R{version.revision}</span>
                  <span>{version.nodeCount} 节点</span>
                </span>
              </button>
            ))}
          </div>
          <aside className="canvas-version-preview">
            {!selected ? (
              <div className="canvas-version-preview-empty">
                <History size={22} strokeWidth={1.5} aria-hidden="true" />
                <p>选择一个版本查看恢复影响</p>
              </div>
            ) : (
              <>
                <div className="canvas-version-preview-head">
                  <span>版本预览</span>
                  <em>{selected.kind === 'manual' ? '手动版本' : '自动保护'}</em>
                </div>
                <strong>{selected.title || '未命名版本'}</strong>
                <small>{formatTime(selected.createdAt)}</small>
                <div className="canvas-version-stats">
                  <span><b>{selected.nodeCount}</b>节点</span>
                  <span><b>{selected.connectionCount}</b>连线</span>
                  <span><b>{selected.revision}</b>revision</span>
                </div>
                <div className="canvas-version-diff">
                  <span>恢复后新增 {selected.diff.nodesAddedByRestore} 节点</span>
                  <span>恢复后移除 {selected.diff.nodesRemovedByRestore} 节点</span>
                  <span>{selected.diff.promptsChanged} 条提示词将被替换</span>
                  <span>{selected.diff.mediaChanged} 个媒体内容将被替换</span>
                </div>
                {!confirmRestore ? (
                  <button
                    type="button"
                    className="canvas-version-restore"
                    disabled={busy || working}
                    onClick={() => setConfirmRestore(true)}
                  >
                    <RotateCw size={13} strokeWidth={1.9} aria-hidden="true" />
                    恢复到此版本
                  </button>
                ) : (
                  <div className="canvas-version-confirm">
                    <p>当前画布会被覆盖，并自动保留恢复前保护版本。</p>
                    <div>
                      <button type="button" onClick={() => setConfirmRestore(false)}>取消</button>
                      <button
                        type="button"
                        className="is-danger"
                        disabled={working}
                        onClick={() => void restoreVersion()}
                      >
                        {working ? '恢复中…' : '确认恢复'}
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </aside>
        </div>
      </section>
    </div>
  );
}

function BatchGenerateDialog({targets, pool, edges, catalog, tasks, busyNodeIds, onClose, onGenerate, onSubmitted}: {
  targets: CanvasMediaNode[];
  pool: CanvasMediaNode[];
  edges: Edge[];
  catalog: ModelCatalog | null;
  tasks: GenerationTask[];
  busyNodeIds: Set<string>;
  onClose: () => void;
  onGenerate: (node: CanvasMediaNode) => boolean;
  onSubmitted: (started: number, selected: number) => void;
}) {
  const dialogRef = useDialogFocusTrap();
  const candidates = useMemo(
    () => evaluateBatchGeneration(targets, pool, catalog, tasks, busyNodeIds, edges),
    [targets, pool, catalog, tasks, busyNodeIds, edges],
  );
  const readyCandidates = candidates.filter((item) => item.state === 'ready');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(readyCandidates.map((item) => item.node.id)),
  );
  const selected = candidates.filter((item) => item.state === 'ready' && selectedIds.has(item.node.id));
  const runningCount = candidates.filter((item) => item.state === 'running').length;
  const blockedCount = candidates.filter((item) => item.state === 'blocked').length;
  const referenceLimitCandidates = candidates.filter((item) => item.blocker === 'reference_limit');
  const allReadySelected = readyCandidates.length > 0
    && readyCandidates.every((item) => selectedIds.has(item.node.id));
  const knownCost = selected.reduce((sum, item) => sum + Number(item.cost || 0), 0);
  const unknownCostCount = selected.filter((item) => item.cost === null).length;
  const currency = pricingCurrencySymbol(selected.find((item) => item.model)?.model?.pricing?.currency);

  const toggleNode = (candidate: BatchGenerationCandidate) => {
    if (candidate.state !== 'ready') return;
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(candidate.node.id)) next.delete(candidate.node.id);
      else next.add(candidate.node.id);
      return next;
    });
  };

  return (
    <div className="canvas-batch-gen-backdrop" onClick={onClose}>
      <article
        ref={dialogRef}
        tabIndex={-1}
        className="canvas-batch-gen-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="批量生成"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <span className="canvas-batch-gen-title">
            <Wand2 size={16} strokeWidth={1.9} aria-hidden="true" />
            <span>
              <strong>批量生成</strong>
              <small>只会提交勾选且依赖就绪的节点</small>
            </span>
          </span>
          <button type="button" onClick={onClose} aria-label="关闭批量生成"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
        </header>

        <div className="canvas-batch-gen-summary">
          <span className="is-ready"><b>{readyCandidates.length}</b> 可生成</span>
          <span className="is-blocked"><b>{blockedCount}</b> 待处理</span>
          <span className="is-running"><b>{runningCount}</b> 生成中</span>
          {readyCandidates.length > 0 && (
            <button
              type="button"
              onClick={() => setSelectedIds(
                allReadySelected
                  ? new Set()
                  : new Set(readyCandidates.map((item) => item.node.id)),
              )}
            >
              {allReadySelected ? '取消全选' : '全选可生成'}
            </button>
          )}
        </div>

        {referenceLimitCandidates.length > 0 && (
          <div className="canvas-batch-gen-alert" role="alert">
            <CircleAlert size={14} strokeWidth={2} aria-hidden="true" />
            <span>
              <strong>{referenceLimitCandidates.length} 项引用超出模型限制，已阻止提交</strong>
              <small title={referenceLimitCandidates.map((item) => `${item.node.title}：${item.reason}`).join('\n')}>
                {referenceLimitCandidates.slice(0, 2).map((item) => `${item.node.title || '未命名节点'}：${item.reason}`).join('；')}
                {referenceLimitCandidates.length > 2 ? `；另有 ${referenceLimitCandidates.length - 2} 项` : ''}
              </small>
            </span>
          </div>
        )}

        <div className="canvas-batch-gen-list">
          {candidates.map((candidate) => {
            const node = candidate.node;
            const checked = selectedIds.has(node.id);
            const kindLabel = node.kind === 'image' ? '图片' : node.kind === 'audio' ? '音频' : '视频';
            const Icon = node.kind === 'image' ? ImageGlyph : node.kind === 'audio' ? Music : Video;
            const StateIcon = candidate.state === 'ready' ? Check : candidate.state === 'running' ? RotateCw : CircleAlert;
            return (
              <button
                key={node.id}
                type="button"
                className={`canvas-batch-gen-item is-${candidate.state}${checked ? ' is-checked' : ''}`}
                title={candidate.reason}
                role="checkbox"
                aria-checked={checked}
                aria-label={`${checked ? '取消选择' : '选择'}${node.title || kindLabel}`}
                disabled={candidate.state !== 'ready'}
                onClick={() => toggleNode(candidate)}
              >
                <span className="canvas-batch-gen-check" aria-hidden="true">
                  {checked && <Check size={12} strokeWidth={2.7} />}
                </span>
                <span className={`canvas-batch-gen-kind is-${node.kind}`} aria-hidden="true">
                  <Icon size={16} strokeWidth={1.8} />
                </span>
                <span className="canvas-batch-gen-copy">
                  <strong>{node.title || `${kindLabel}节点`}</strong>
                  <small>
                    {kindLabel}
                    {candidate.model ? ` · ${candidate.model.label}` : ''}
                    {candidate.state === 'ready' ? ` · ${candidate.costLabel}` : ` · ${candidate.reason}`}
                  </small>
                </span>
                <span className={`canvas-batch-gen-state is-${candidate.state}`}>
                  <StateIcon size={11} strokeWidth={2.1} aria-hidden="true" />
                  {candidate.state === 'ready' ? '就绪' : candidate.state === 'running' ? '生成中' : '阻塞'}
                </span>
              </button>
            );
          })}
          {candidates.length === 0 && <p className="canvas-batch-gen-empty">选中里没有可批量生成的图片、音频或视频节点</p>}
        </div>

        <footer>
          <span className="canvas-batch-gen-cost">
            <span className="canvas-batch-gen-selection">已选 <b>{selected.length}</b> 项</span>
            <em>
              {selected.length === 0
                ? '未选择'
                : unknownCostCount > 0
                  ? `${unknownCostCount} 项按供应商账单`
                  : `预估 ≈${currency}${knownCost.toFixed(knownCost < 1 ? 3 : 2)}`}
            </em>
          </span>
          <span className="canvas-batch-gen-actions">
            <button type="button" onClick={onClose}>取消</button>
            <button
              type="button"
              className="is-primary"
              disabled={selected.length === 0}
              onClick={() => {
                let started = 0;
                for (const candidate of selected) {
                  if (onGenerate(candidate.node)) started += 1;
                }
                onSubmitted(started, selected.length);
                onClose();
              }}
            >
              <Wand2 size={14} strokeWidth={1.9} aria-hidden="true" />
              生成选中 {selected.length} 项
            </button>
          </span>
        </footer>
      </article>
    </div>
  );
}

function CanvasViewInner({session, actions, onCreate}: Props) {
  const [canvas, setCanvas] = useState<Canvas | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [zoomTarget, setZoomTarget] = useState<string | null>(null);
  const [trayPreview, setTrayPreview] = useState<({url: string; label: string} & PreviewPlacement) | null>(null);
  const [notes, setNotes] = useState<CanvasNote[]>([]);
  // 画布内提示条：必须自动收起，否则会一直悬在画布顶部挡视线（全书 20+ 处 setToast）
  const [toast, setToast] = useState('');
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(''), toast.length > 24 ? 4200 : 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);
  const showTrayPreview = useCallback((url: string, label: string, element: HTMLElement) => {
    if (!url) return;
    setTrayPreview({
      url,
      label,
      ...previewPlacement(element.getBoundingClientRect(), 'right'),
    });
  }, []);
  const [helpOpen, setHelpOpen] = useState(false);
  const [taskCenterOpen, setTaskCenterOpen] = useState(false);
  const [generationTasks, setGenerationTasks] = useState<GenerationTask[]>([]);
  const activeGenerationTaskCount = generationTasks.filter(
    (task) => ['queued', 'submitting', 'running'].includes(task.status),
  ).length;
  const [textReader, setTextReader] = useState<CanvasMediaNode | null>(null);
  // 顶部栏下拉菜单（状态 / 工具）与全局搜索聚焦态
  const [openMenu, setOpenMenu] = useState<'status' | 'tools' | null>(null);
  const [searchFocused, setSearchFocused] = useState(false);
  // 搜索结果键盘导航：↑↓ 移动高亮、Enter 定位过去（原来只能鼠标点，长列表时很难受）
  const [searchIndex, setSearchIndex] = useState(0);
  const layoutFileRef = useRef<HTMLInputElement | null>(null);
  const materialFileInputRef = useRef<HTMLInputElement | null>(null);
  const canvasUploadOriginRef = useRef<{x: number; y: number} | null>(null);
  const nodeLocalUploadInputRef = useRef<HTMLInputElement | null>(null);
  const nodeLocalUploadTargetRef = useRef<string | null>(null);
  const batchUploadAbortRef = useRef<AbortController | null>(null);
  const [nodeLocalUploadAccept, setNodeLocalUploadAccept] = useState('image/*,audio/*,video/*');
  const [batchUploadState, setBatchUploadState] = useState<{
    total: number;
    done: number;
    percent: number;
    currentName: string;
  } | null>(null);
  const mediaOpenSuppressedUntilRef = useRef(0);
  const suppressNodeOpen = useCallback(() => {
    mediaOpenSuppressedUntilRef.current = Date.now() + 1200;
  }, []);
  const isNodeOpenSuppressed = useCallback(
    () => Date.now() < mediaOpenSuppressedUntilRef.current,
    [],
  );
  const openMaterialPicker = useCallback((origin?: {x: number; y: number}) => {
    canvasUploadOriginRef.current = origin || null;
    materialFileInputRef.current?.click();
  }, []);
  // 三个下拉的容器 ref：点外面自动收起时要判断点击是否落在自己身上
  const statusMenuRef = useRef<HTMLDivElement | null>(null);
  const toolsMenuRef = useRef<HTMLDivElement | null>(null);
  const addMenuRef = useRef<HTMLDivElement | null>(null);
  const [topbarSlot, setTopbarSlot] = useState<HTMLElement | null>(null);

  useLayoutEffect(() => {
    setTopbarSlot(document.getElementById('canvas-topbar-slot'));
  }, []);

  const [drafts, setDrafts] = useState<Record<number, ShotDraft>>({});
  const [dirtyShots, setDirtyShots] = useState<Set<number>>(new Set());
  const [savingShot, setSavingShot] = useState<number | null>(null);
  const [savedFlash, setSavedFlash] = useState<Record<number, number>>({});
  // 创作质量自评报告：用于画布镜头卡点出"哪镜该改"
  const [qualityReport, setQualityReport] = useState<QualityReport | null>(null);
  const [qualityRecomputing, setQualityRecomputing] = useState(false);
  const [pickShot, setPickShot] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // P0-10 工业化视频组：点击镜头/视频组弹出「视频提示词 + 资产引用」窗口（对齐 AniShort 提示词面板）
  const [promptShot, setPromptShot] = useState<CanvasShot | null>(null);
  const [promptSegment, setPromptSegment] = useState<CanvasSegment | null>(null);
  const [saveError, setSaveError] = useState('');
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved');
  const pendingSavesRef = useRef(0);
  const syncChannelRef = useRef<BroadcastChannel | null>(null);
  const modelFallbackNoticeRef = useRef('');
  const syncInstanceIdRef = useRef(`canvas-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  const beginSave = useCallback(() => {
    pendingSavesRef.current += 1;
    setSaveState('saving');
  }, []);
  const finishSave = useCallback((ok: boolean) => {
    pendingSavesRef.current = Math.max(0, pendingSavesRef.current - 1);
    if (!ok) {
      setSaveState('error');
      return;
    }
    if (pendingSavesRef.current === 0) setSaveState('saved');
  }, []);
  // 资产详情面板（点击资产节点弹出：类型/名称/描述词/大图/音频）
  const [assetDetail, setAssetDetail] = useState<{kind: string; item: CanvasAssetItem} | null>(null);
  // 一集一画布：多集项目按集切换（避免几十集节点同屏卡死）
  const [currentEp, setCurrentEp] = useState<string | null>(() => {
    try {
      return resolveInitialEpisode(
        window.location.search,
        window.localStorage.getItem(`vimax.canvas.episode.${session}`),
      );
    } catch {
      return null;
    }
  });
  useEffect(() => {
    if (!currentEp) return;
    try {
      window.localStorage.setItem(`vimax.canvas.episode.${session}`, currentEp);
    } catch {
      // Local memory is optional; the URL remains the shareable source.
    }
    try {
      const url = new URL(window.location.href);
      if (url.searchParams.get('ep') !== currentEp) {
        url.searchParams.set('ep', currentEp);
        window.history.replaceState(null, '', `${url.pathname}${url.search}${url.hash}`);
      }
    } catch {
      // Keep the in-memory selection if the browser URL cannot be rewritten.
    }
  }, [currentEp, session]);
  // P2 批2：资产分组折叠（80/120 集项目资产多时收拢分组，只保留分组头）
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const toggleGroup = useCallback((kind: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);
  // 左侧画布列表默认收起（与右侧镜头顺序对称，需要时点竖条展开）
  const [epListOpen, setEpListOpen] = useState(false);
  // 左侧托盘页签：剧集列表 / 资产库（角色·场景·道具·音色）
  const [railTab, setRailTab] = useState<'eps' | 'assets'>('eps');
  const [episodeCreateOpen, setEpisodeCreateOpen] = useState(false);
  const [episodeDraftId, setEpisodeDraftId] = useState('');
  const [episodeDraftTitle, setEpisodeDraftTitle] = useState('');
  const [episodeCreating, setEpisodeCreating] = useState(false);
  const [episodeCreateError, setEpisodeCreateError] = useState('');
  const [episodeDeleting, setEpisodeDeleting] = useState(false);
  const [episodeMenu, setEpisodeMenu] = useState<{
    epId: string;
    title: string;
    left: number;
    top: number;
  } | null>(null);
  useEffect(() => {
    if (!episodeMenu) return;
    const close = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest('.canvas-ep-action-menu') && !target?.closest('.canvas-ep-more')) {
        setEpisodeMenu(null);
      }
    };
    document.addEventListener('pointerdown', close, true);
    return () => document.removeEventListener('pointerdown', close, true);
  }, [episodeMenu]);
  // 画布底纹（复刻 BeefTV appearance）：dots 点阵 / lines 细线网格 / blank 空白
  const [bgMode, setBgMode] = useState<'dots' | 'lines' | 'blank'>('dots');
  // ＋添加节点菜单开关
  const [addMenuOpen, setAddMenuOpen] = useState(false);
  // 画布舞台容器 ref：右键菜单视口坐标 → 舞台相对坐标换算
  const stageRef = useRef<HTMLDivElement | null>(null);
  // 编组外框层（纯视觉，命令式更新 style，见 syncGroupFrames）
  const groupFrameRef = useRef<HTMLDivElement | null>(null);

  // 资产图比例探测：按真实比例显示卡片（9:16 竖图高 / 16:9 横图矮 / 1:1 方图），不固定小卡片。
  // 性能：每张图 onload 都全量 setState 会触发 N 次 flowNodes 重算（120 集项目 = 上百次整图重建），
  // 这里累积变更后用 rAF 合入一次；canvas 切换/卸载时丢弃在途探测，避免串数据与卸载后 setState。
  const assetRatiosRef = useRef<Record<string, number>>({});
  const [assetRatios, setAssetRatios] = useState<Record<string, number>>({});
  useEffect(() => {
    if (!canvas) return;
    let alive = true;
    const pending: Record<string, number> = {};
    let flushHandle: number | null = null;
    const flush = () => {
      flushHandle = null;
      if (!alive || Object.keys(pending).length === 0) return;
      Object.assign(assetRatiosRef.current, pending);
      setAssetRatios({...assetRatiosRef.current});
      for (const key of Object.keys(pending)) delete pending[key];
    };
    const scheduleFlush = () => {
      if (flushHandle !== null) return;
      flushHandle = requestAnimationFrame(flush);
    };
    const items = [...canvas.characters, ...canvas.scenes, ...canvas.props].filter((i) => i.image_url);
    items.forEach((it) => {
      const url = it.image_url as string;
      if (assetRatiosRef.current[url]) return;
      const img = new Image();
      img.onload = () => {
        const r = img.naturalWidth > 0 ? img.naturalHeight / img.naturalWidth : 1;
        pending[url] = r;
        scheduleFlush();
      };
      img.onerror = () => {
        pending[url] = 1;
        scheduleFlush();
      };
      img.src = url;
    });
    return () => {
      alive = false;
      if (flushHandle !== null) cancelAnimationFrame(flushHandle);
    };
  }, [canvas]);

  // 视频真实宽高比探测：<video> onloadedmetadata 时读 videoWidth/videoHeight，
  // 按真实横/竖屏决定缩略图与播放框形状（横屏给横框、竖屏给竖框）。
  // 与 assetRatios 同模式：rAF 合入一次，避免每个片段 metadata 到达都重建整图。
  const videoRatiosRef = useRef<Record<string, number>>({});
  const [videoRatios, setVideoRatios] = useState<Record<string, number>>({});
  const pendingVideoRatios = useRef<Record<string, number>>({});
  const videoRatioFlushHandle = useRef<number | null>(null);
  const flushVideoRatios = useCallback(() => {
    videoRatioFlushHandle.current = null;
    if (Object.keys(pendingVideoRatios.current).length === 0) return;
    Object.assign(videoRatiosRef.current, pendingVideoRatios.current);
    pendingVideoRatios.current = {};
    setVideoRatios({...videoRatiosRef.current});
  }, []);
  const onVideoMeta = useCallback<VideoMetaHandler>((event) => {
    const v = event.currentTarget;
    // 必须用 getAttribute('src') 拿原始属性值（相对路径），
    // 否则 currentSrc / v.src 会被浏览器解析成绝对 URL，与 data.*.url 传入的相对路径对不上，查表 miss。
    const url = v.getAttribute('src') || v.currentSrc || v.src;
    if (!url || !v.videoWidth || !v.videoHeight) return;
    const r = v.videoHeight / v.videoWidth;
    if (videoRatiosRef.current[url] === r) return;
    pendingVideoRatios.current[url] = r;
    if (videoRatioFlushHandle.current !== null) return;
    videoRatioFlushHandle.current = requestAnimationFrame(flushVideoRatios);
  }, [flushVideoRatios]);
  useEffect(() => () => {
    if (videoRatioFlushHandle.current !== null) cancelAnimationFrame(videoRatioFlushHandle.current);
  }, []);

  // ep_id → 可读集名：ep01/ep1 → 第01集
  // 必须 memo：它是 flowNodes 的依赖，每次渲染换新身份会让 flowNodes 每帧重建，
  // 进而让下面的节点同步 effect 每帧 setNodes（控制台 Max update depth exceeded）。
  const fmtEp = useCallback((epId: string): string => {
    const m = /^ep(\d+)$/i.exec(epId);
    return m ? `第${String(Number(m[1])).padStart(2, '0')}集` : epId;
  }, []);

  const beginEpisodeCreate = useCallback(() => {
    const used = (canvas?.meta.episodes || []).map((episode) => {
      const found = /^EP0*(\d+)$/i.exec(episode.ep_id);
      return found ? Number(found[1]) : 0;
    });
    const next = Math.max(0, ...used) + 1;
    setRailTab('eps');
    setEpisodeDraftId(`EP${String(next).padStart(3, '0')}`);
    setEpisodeDraftTitle('');
    setEpisodeCreateError('');
    setEpisodeCreateOpen(true);
  }, [canvas]);

  const submitEpisodeCreate = useCallback(async (event: FormEvent) => {
    event.preventDefault();
    if (episodeCreating) return;
    const episodeId = episodeDraftId.trim();
    if (!/^(?:EP)?0*[1-9]\d{0,3}$/i.test(episodeId)) {
      setEpisodeCreateError('请输入 1-9999，或 EP001 这种格式');
      return;
    }
    setEpisodeCreating(true);
    setEpisodeCreateError('');
    try {
      const result = await createCanvasEpisode(session, {
        episodeId,
        title: episodeDraftTitle.trim(),
      });
      setEpisodeCreateOpen(false);
      setEpisodeDraftTitle('');
      setToast(`${fmtEp(result.episode.id)}已创建`);
      setCurrentEp(result.episode.id);
    } catch (error) {
      setEpisodeCreateError(error instanceof Error ? error.message : '创建集数失败');
    } finally {
      setEpisodeCreating(false);
    }
  }, [episodeCreating, episodeDraftId, episodeDraftTitle, fmtEp, session, setToast]);

  const removeEpisode = useCallback(async (episodeId: string, episodeTitle: string) => {
    if (episodeDeleting) return;
    const confirmed = await dialog.confirm({
      title: `删除「${episodeTitle}」？`,
      message: '只删除画布列表中的这一集；如果本集仍有节点，系统会提示先移除或迁移，避免误删素材。',
      tone: 'danger',
      confirmText: '删除本集',
    });
    if (!confirmed) return;
    setEpisodeDeleting(true);
    try {
      await deleteCanvasEpisode(session, episodeId);
      setToast(`${fmtEp(episodeId)}已删除`);
      const remaining = canvas?.meta.episodes.filter((episode) => episode.ep_id !== episodeId) || [];
      if (currentEp === episodeId) {
        setCurrentEp(remaining[0]?.ep_id || null);
      }
      setCanvas((prev) => prev ? {
        ...prev,
        meta: {
          ...prev.meta,
          episodes: prev.meta.episodes.filter((episode) => episode.ep_id !== episodeId),
          current_ep: currentEp === episodeId ? (remaining[0]?.ep_id || '') : prev.meta.current_ep,
        },
        segments: prev.segments.filter((segment) => segment.ep_id !== episodeId),
        shots: prev.shots.filter((shot) => shot.ep_id !== episodeId),
      } : prev);
    } catch (error) {
      setToast(error instanceof Error ? error.message : '删除集数失败');
    } finally {
      setEpisodeDeleting(false);
    }
  }, [canvas, currentEp, episodeDeleting, fmtEp, session, setToast]);

  // LibTV 风格交互状态
  const [mouseMode, setMouseMode] = useState<MouseMode>('select');
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [budgetOpen, setBudgetOpen] = useState(false);
  const toolsTriggerRef = useRef<HTMLButtonElement>(null);
  const [userEdges, setUserEdges] = useState<Edge[]>([]);
  const projectConnectionsRef = useRef<Array<{
    id?: string;
    fromNodeId: string;
    toNodeId: string;
    source?: string;
  }>>([]);
  const connectionSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectionSaveInFlightRef = useRef(false);
  const pendingConnectionsRef = useRef<Array<{
    id: string;
    fromNodeId: string;
    toNodeId: string;
  }> | null>(null);
  const [deletedAutoEdgeIds, setDeletedAutoEdgeIds] = useState<Set<string>>(new Set());
  const [panning, setPanning] = useState(false);
  const noteSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 镜头编辑防抖自动保存：输入停顿 900ms 后写回（与备注同一模式），不再依赖手动点保存按钮
  const shotSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveShotRef = useRef<(shotIdx: number) => void>(() => {});
  const positionRef = useRef<Record<string, {x: number; y: number}>>({});
  const rf = useReactFlow();
  const revealRef = useRef({key: 0, at: 0});
  // storeApi：编组「按组拖动」需要绕过 React 调度，直接改内部选中位（见 pressGroup）
  const storeApi = useStoreApi();
  const nodesRef = useRef<FlowNode[]>([]);

  // ===== 画布增强：布局持久化 / 资产搜索 / 导出 PNG / 复制粘贴 / 段卡右键菜单 =====
  const [assetQuery, setAssetQuery] = useState('');
  const [clipboard, setClipboard] = useState<string[]>([]);
  // 节点剪贴板：帮助面板一直写着"复制节点 Ctrl C 粘贴"，但旧实现只把备注**文本**塞进 clipboard，
  // 媒体卡片无法被复制/粘贴。这里单独存节点快照（媒体 + 备注），Ctrl+V 优先粘贴节点。
  // edges：被复制节点**之间**的连线（源与目标都在复制集合内），粘贴时一并重建到新节点上。
  const [nodeClipboard, setNodeClipboard] = useState<{
    media: CanvasMediaNode[];
    notes: CanvasNote[];
    edges: {source: string; target: string}[];
  } | null>(null);
  // 多选数量：用于工具栏按需显示「对齐/分布」浮动组（选中 ≥2 才出现，避免常态占地方）
  const [selectedCount, setSelectedCount] = useState(0);
  // 节点层级（置顶/置底）：nodeId → zIndex。
  // 放在 node.style.zIndex 而不是 node.zIndex —— React Flow 的 NodeWrapper 会用
  // `style: {zIndex: internals.z, ...node.style}` 展开，node.style 反而能覆盖 internals.z，
  // 而 internals.z 每次 flowNodes 派生重建都会被重置（派生数据不带层级）。
  const [zOrder, setZOrder] = useState<Record<string, number>>({});
  // 提示词弹窗是否打开（供全局 Esc / 快捷键判断，避免闭包读到旧值）。
  // 真实 state 在下方 editNodeId；这里只做镜像，渲染时同步。
  const editNodeRef = useRef<string | null>(null);
  // 实时缩放百分比（onMove 回写），工具栏显示 + 点击重置 100%
  const [zoomPct, setZoomPct] = useState(100);
  const [largeCanvasOverview, setLargeCanvasOverview] = useState(false);
  const [largeCanvasVisibleBounds, setLargeCanvasVisibleBounds] = useState<CanvasVisibleBounds | null>(null);
  const overviewKeyRef = useRef('');
  const visibleBoundsTimerRef = useRef<number | null>(null);
  // 网格吸附：默认关。开启后节点按 12px 栅格对齐，手工排版不再歪斜。
  const [snapGrid, setSnapGrid] = useState(false);
  // 智能对齐参考线（对标即梦）：拖动卡片时与其它卡片边缘/中线对齐会亮起参考线并自动吸附。
  // 实现要点：参考线用「命令式 DOM」绘制（不每帧 setState），吸附在拦截 position change 时就地完成。
  const [alignSnapOn, setAlignSnapOn] = useState(true);
  // 连线显隐（用户需求：一键隐藏/显示所有连线）。
  // ★ 实现方式是给画布根加一个 class 让 CSS 把连线层 `display:none`，
  //   **绝不改动 edges 数组**：改数组会让 React Flow 重建整张边图（60+ 条边），
  //   每次切换都卡一下；而且 flowEdges 是全程派生的，改了也会被派生逻辑覆盖回去。
  //   隐藏期间连线数据、选中态、引用关系全部原样保留，再点一次立刻恢复。
  // 状态存 localStorage：用户关掉连线是为了看清排版，刷新后不该自己又冒出来。
  // 用惰性初始化而不是 mount 后 effect 读，否则首帧会先亮一下连线再消失（闪烁）。
  const [edgesHidden, setEdgesHidden] = useState(() => {
    try { return localStorage.getItem('vimax.canvas.edgesHidden') === '1'; } catch { return false; }
  });
  // ★ 这里**不能**直接 useCallback 引用 setEdges：setEdges 在第 2719 行才由 useEdgesState
  //   创建，函数组件是逐行执行的，写在它之前读它会命中 TDZ（"Cannot access 'setEdges'
  //   before initialization"直接整树崩溃）。所以这里只翻转开关，取消选线的副作用
  //   放到 setEdges 定义之后的 effect 里做（见下方 edgesHidden effect）。
  const alignGuidesRef = useRef<HTMLDivElement | null>(null);
  const guideVRef = useRef<HTMLDivElement | null>(null);
  const guideHRef = useRef<HTMLDivElement | null>(null);
  // 参考线读数标签（显示剩余偏差 px，即梦/Figma 同款）：线在 17% 缩放下只有 1~2 屏幕像素，
  // 光有线读不出"还差多少"，标签补上这个信息。
  const guideVLabelRef = useRef<HTMLDivElement | null>(null);
  const guideHLabelRef = useRef<HTMLDivElement | null>(null);
  // 吸附后的最终位置：RF 拖拽结束时给的 node.position 是「未吸附」的指针坐标，
  // 写回位置真源前要用这里的值，否则松手瞬间会弹回未对齐位置。
  const snappedPosRef = useRef<Map<string, {x: number; y: number}>>(new Map());
  // 磁吸吸附的跨帧状态（锁定在哪条参考线上）：必须跨帧保留，
  // 否则每帧独立重算会出现「越过阈值后弹跳」的抖动。
  const alignStateRef = useRef<AlignState>(createAlignState());
  // 连线进行中：给画布加类，让可落点的端口高亮（见 ui-refinements 的 .is-connecting）
  const [connecting, setConnecting] = useState(false);
  // 「整理」弹窗：对齐/分布/排序整理（不把这些按钮堆在工具栏上）
  const [arrangeOpen, setArrangeOpen] = useState(false);
  // 删除确认弹窗：存待删节点（画布节点 id），非空即弹窗
  const [deleteTargets, setDeleteTargets] = useState<{id: string; type: string}[] | null>(null);
  const [exporting, setExporting] = useState(false);
  // 多选浮动操作条（对标即梦）：选中 ≥2 个卡片时，直接在选区块上方浮出一条工具条，
  // 不再要求"先右键再找菜单"。条内只放高频动作，重型整理/删除仍走弹窗。
  // 位置由选区包围盒实时算出（屏幕坐标），随画布平移/缩放/选区块移动自动跟随。
  // barTop：操作条的最终 top（已钳进画布）。
  // menuDown：下拉菜单向下展开（向上会顶出画布被顶栏遮住时切换）。
  const [selBar, setSelBar] = useState<{
    x: number; y: number; w: number; count: number; rightX: number; midY: number;
    mediaCount: number; menuDown: boolean; barTop: number;
    /** 选中里「图片 + 视频」的个数：只有它们有 模型/比例/清晰度 可批量调 */
    adjustCount: number;
    /** 选中里「已生成好、有素材地址」的个数：批量下载只认这些 */
    readyCount: number;
    /** 选中里可进入批量生成的图片/音频/视频节点数 */
    generateCount: number;
    /** 选中卡片的媒体节点 id（media- 前缀已剥掉），批量动作直接用，省一次遍历 */
    mediaIds: string[];
  } | null>(null);
  /** 批量调参弹窗：null = 关闭；数组 = 目标节点（图片/视频） */
  const [batchParamsTargets, setBatchParamsTargets] = useState<CanvasMediaNode[] | null>(null);
  const [batchGenerateTargets, setBatchGenerateTargets] = useState<CanvasMediaNode[] | null>(null);
  // 操作条内的两个小下拉（批量连线 / 编组整理）：'link' | 'arrange' | null
  const [selBarMenu, setSelBarMenu] = useState<'link' | 'arrange' | null>(null);
  // ★ 操作条小下拉：点外面 / Esc 都要收起。
  //   旧行为只在「选区散掉」时重置 —— 用户点画布空白处（选区还在、只是没变）时菜单不关。
  useEffect(() => {
    if (!selBarMenu) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      // 下拉本体与其触发按钮都在 .canvas-selbar 内，点它们不关（按钮自己 toggle）
      if (!t.closest('.canvas-selbar')) setSelBarMenu(null);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSelBarMenu(null); };
    document.addEventListener('pointerdown', onDown, true);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [selBarMenu]);
  // 选区批量连线拖拽（对标即梦：选区右缘一个圆点，按住拖到目标卡片 = 一次连过去）
  // 坐标全部用 stage 相对 px（覆盖层是 stage 的绝对定位子元素），不参与画布变换。
  const [selLinkDrag, setSelLinkDrag] = useState<{sx: number; sy: number; cx: number; cy: number; targetId: string | null} | null>(null);
  // 段右键菜单：存节点 id 而非段号——总览模式段卡 id 形如 seg-ep01-0，
  // 直接 replace('seg-','') 解析段号会得到 NaN，菜单根本不弹。
  const [segMenu, setSegMenu] = useState<{nodeId: string; x: number; y: number} | null>(null);
  const [segDetail, setSegDetail] = useState<CanvasSegment | null>(null);
  const didInitialLayoutRef = useRef(false);
  const fittedScopeRef = useRef('');
  const autoEpAppliedRef = useRef(false);
  // 布局按集持久化：整剧总览用 'all'，切集互不污染。
  // ★ 单集项目（≤1 集）：整剧总览与「第01集」共用同一布局 key——否则整剧总览里拖好的
  //   卡片位置，点「第01集」会因 key 切换读不到而全部回落到载荷默认位置（实测"排序乱"）。
  const isSingleEpProject = !!canvas && canvas.meta.episodes.length <= 1;
  const layoutKey = `vimax.canvas.layout.${session}.${isSingleEpProject ? 'all' : (currentEp ?? 'all')}`;

  // ── 编组（用户要求：可以批量编组、可以解组、编组后按组拖动）───────────────
  // 设计取舍：**不做 React Flow 的 parentId 容器**，只维护一张「组 → 成员 flow id」表。
  //   原因：parentId 是真实父子坐标系（子卡 position 变相对坐标），把媒体卡塞进去会
  //   与既有的 ep-box / asset-group 父子链冲突，且拖动/连线端点换算全要跟着改，风险极高。
  //   这里用「选中扩散」实现同等体验：点组内任一卡 → 整组一起选中；
  //   而多选拖动本来就是整片位移（React Flow 原生行为），于是"按组拖动"自然成立。
  type CanvasGroup = {id: string; name: string; members: string[]};
  const [groups, setGroups] = useState<CanvasGroup[]>([]);
  const groupsRef = useRef<CanvasGroup[]>([]);
  groupsRef.current = groups;   // persistLayout 里要读最新值（不能挂 deps，会连锁重建回调）
  const viewportRef = useRef({x: 0, y: 0, zoom: 1});
  const overviewReturnViewportRef = useRef<CanvasViewport | null>(null);
  const focusSelectionRef = useRef<(showEmptyHint?: boolean) => void>(() => {});
  const viewportSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadPersistedLayout = useCallback(() => {
    try {
      const raw = localStorage.getItem(layoutKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as {
        positions?: Record<string, {x: number; y: number}>;
        userEdges?: Edge[];
        deletedAuto?: string[];
        groups?: {id: string; name: string; members: string[]}[];
        viewport?: {x: number; y: number; zoom: number};
        viewportPolicyVersion?: number;
        connectionPersistenceVersion?: number;
      };
      return parsed;
    } catch {
      return null;
    }
  }, [layoutKey]);

  const persistLayout = useCallback((
    positions: Record<string, {x: number; y: number}>,
    edges: Edge[],
    deleted: Set<string>,
    options?: {connectionSynced?: boolean},
  ) => {
    try {
      // 资产卡子节点位置跟随分组框整体拖动，不单独持久化（索引可能因资产增删变化）；
      // 媒体卡片位置由载荷 node.position 管理（拖动同步回 mn 并写回后端），也不再进 localStorage——
      // 否则历史残留的"单列"位置会与载荷多列布局互相覆盖。
      const stablePositions: Record<string, {x: number; y: number}> = {};
      Object.entries(positions).forEach(([id, pos]) => {
        if (!id.startsWith('asset-') && !id.startsWith('media-')) stablePositions[id] = pos;
      });
      const previousVersion = Number(loadPersistedLayout()?.connectionPersistenceVersion || 0);
      localStorage.setItem(layoutKey, JSON.stringify({
        positions: stablePositions, userEdges: edges, deletedAuto: [...deleted],
        viewport: viewportRef.current,
        viewportPolicyVersion: CANVAS_VIEWPORT_POLICY_VERSION,
        connectionPersistenceVersion: options?.connectionSynced ? 1 : previousVersion,
        // 编组也随布局一起存：换集/刷新后组关系不能丢（用户编的组是他的工作成果）
        groups: groupsRef.current,
      }));
    } catch {
      /* localStorage 不可用时静默 */
    }
  }, [layoutKey, loadPersistedLayout]);
  const rememberViewport = useCallback((viewport: {x: number; y: number; zoom: number}) => {
    viewportRef.current = viewport;
    if (viewportSaveTimer.current) clearTimeout(viewportSaveTimer.current);
    viewportSaveTimer.current = setTimeout(() => {
      try {
        const raw = localStorage.getItem(layoutKey);
        const saved = raw ? JSON.parse(raw) : {};
        localStorage.setItem(layoutKey, JSON.stringify({
          ...saved,
          viewport,
          viewportPolicyVersion: CANVAS_VIEWPORT_POLICY_VERSION,
        }));
      } catch {
        // View persistence is best-effort and must not affect canvas interaction.
      }
    }, 220);
  }, [layoutKey]);

  // ── 编组操作 ──────────────────────────────────────────────────────
  // 编组：把当前选中的卡片收进一个新组。已属于其它组的成员会自动离组（一个人只能在一个组里）。
  const groupSelected = useCallback((ids: string[]) => {
    const members = ids.filter((id) => id.startsWith(MEDIA_PREFIX) || id.startsWith(NOTE_PREFIX));
    if (members.length < 2) { setToast('至少选中 2 个卡片才能编组'); return; }
    const set = new Set(members);
    setGroups((prev) => {
      // 从已有组里剔除这些成员；组空了就删掉
      const cleaned = prev
        .map((g) => ({...g, members: g.members.filter((m) => !set.has(m))}))
        .filter((g) => g.members.length >= 2);
      const name = `组 ${cleaned.length + 1}`;
      const next = [...cleaned, {id: `grp-${Date.now().toString(36)}`, name, members}];
      groupsRef.current = next;
      return next;
    });
    setToast(`已编组 ${members.length} 个卡片（点组内任一卡即整组选中，拖动整组移动）`);
  }, [setToast]);

  // 解组：把选中卡片所在的组拆掉（整组解散）
  const ungroupSelected = useCallback((ids: string[]) => {
    const set = new Set(ids);
    setGroups((prev) => {
      const hit = prev.filter((g) => g.members.some((m) => set.has(m)));
      if (hit.length === 0) { setToast('选中的卡片不在任何组里'); return prev; }
      const next = prev.filter((g) => !hit.some((h) => h.id === g.id));
      groupsRef.current = next;
      setToast(`已解除 ${hit.length} 个编组`);
      return next;
    });
  }, [setToast]);

  // 解除指定组的编组（外框上的 ✕ 按钮用）
  const ungroupById = useCallback((groupId: string) => {
    setGroups((prev) => {
      const target = prev.find((g) => g.id === groupId);
      const next = prev.filter((g) => g.id !== groupId);
      groupsRef.current = next;
      if (target) setToast(`已解除「${target.name}」`);
      return target ? next : prev;
    });
  }, [setToast]);
  // 外框是命令式 DOM，点击事件走原生委托，用 ref 拿最新的回调（避免闭包过期）
  const ungroupByIdRef = useRef(ungroupById);
  ungroupByIdRef.current = ungroupById;

  const [nodes, setNodes, onNodesChange] = useNodesState<FlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  // edges 最新值：deleteEdgeById 需要按 edge id 找回真实端点（自动线 id 里的节点 id 含 '-'，不能靠正则反解）
  const edgesRef = useRef<Edge[]>([]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  // 连线显隐开关（实现在 setEdges 之后就位，避开 TDZ —— 见 edgesHidden 声明处注释）。
  const toggleEdgesHidden = useCallback(() => {
    setEdgesHidden((prev) => {
      const next = !prev;
      try { localStorage.setItem('vimax.canvas.edgesHidden', next ? '1' : '0'); } catch { /* 静默 */ }
      return next;
    });
  }, []);
  // 隐藏连线时顺手取消「单独选中的连线」：否则线看不见了，按 Del 却仍会把它们删掉
  // （用户完全无从判断会删什么）。开关打开时才需要处理一次，关闭时不必动选中态。
  useEffect(() => {
    if (!edgesHidden) return;
    setEdges((prev) => (prev.some((e) => e.selected)
      ? prev.map((e) => (e.selected ? {...e, selected: false} : e))
      : prev));
  }, [edgesHidden, setEdges]);
  // 拖动节点期间给画布加类名，把节点提升为独立合成层（见 styles.css .is-node-dragging）。
  // 否则每次 pointermove 都会重绘整个画布区域。
  const [draggingNode, setDraggingNode] = useState(false);

  // 撤销/重做（作用于用户连线、备注节点与自动边删除状态）
  const pastRef = useRef<HistorySnapshot[]>([]);
  const futureRef = useRef<HistorySnapshot[]>([]);
  const metadataPastRef = useRef<MetadataHistoryEntry[]>([]);
  const metadataFutureRef = useRef<MetadataHistoryEntry[]>([]);
  const suppressMetadataHistoryRef = useRef(false);
  const [undoTick, setUndoTick] = useState(0);
  const deletedAutoRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    deletedAutoRef.current = deletedAutoEdgeIds;
  }, [deletedAutoEdgeIds]);

  const pushHistory = useCallback((
    edgesSnapshot: Edge[],
    notesSnapshot: CanvasNote[],
    options?: {removeAddedMedia?: boolean; positions?: boolean},
  ) => {
    metadataPastRef.current = [];
    metadataFutureRef.current = [];
    pastRef.current = [
      ...pastRef.current.slice(-49),
      {
        edges: edgesSnapshot,
        notes: notesSnapshot,
        deletedAuto: [...deletedAutoRef.current],
        media: mediaNodesRef.current,
        flowNodes: options?.positions ? nodesRef.current : undefined,
        removeAddedMedia: options?.removeAddedMedia === true,
        positions: options?.positions === true,
      },
    ];
    // Position snapshots must copy coordinates now. Refs point to live arrays; a
    // later sort would otherwise mutate the history entry itself.
    if (options?.positions) {
      const snapshot = pastRef.current[pastRef.current.length - 1];
      snapshot.media = snapshot.media.map((node) => ({...node}));
      snapshot.notes = snapshot.notes.map((note) => ({...note}));
      snapshot.flowNodes = snapshot.flowNodes?.map((node) => ({
        ...node,
        position: {...node.position},
      }));
    }
    futureRef.current = [];
    setUndoTick((t) => t + 1);
  }, []);

  // 媒体节点落库（提前到这里：deleteEdgeById / removeRefAndEdge 都要用它清 refs，
  // 放到后面会形成 TDZ —— 曾因此整页白屏 "Cannot access 'persistMediaNodes' before initialization"）
  const [mediaNodes, setMediaNodes] = useState<CanvasMediaNode[]>([]);
  const [modelCatalog, setModelCatalog] = useState<ModelCatalog | null>(null);
  const [generationBusyIds, setGenerationBusyIds] = useState<Set<string>>(() => new Set());
  // 事件流是否在线：在线时轮询降级成兜底对账，离线时恢复 1.5s 的密集轮询。
  const [eventsConnected, setEventsConnected] = useState(false);
  const mediaNodesRef = useRef<CanvasMediaNode[]>([]);
  useEffect(() => { mediaNodesRef.current = mediaNodes; }, [mediaNodes]);
  useEffect(() => {
    if (mediaNodes.length === 0) return;
    const key = currentEp || '__project__';
    if (overviewKeyRef.current === key) return;
    overviewKeyRef.current = key;
    setLargeCanvasOverview(mediaNodes.length > 400);
  }, [currentEp, mediaNodes.length]);
  useEffect(() => {
    const latestByNode = new Map<string, GenerationTask>();
    for (const task of generationTasks) {
      if (!latestByNode.has(task.node_id)) latestByNode.set(task.node_id, task);
    }
    const activeByNode = new Map(
      [...latestByNode.values()]
        .filter((task) => ['queued', 'submitting', 'running'].includes(task.status))
        .map((task) => [task.node_id, task]),
    );
    setGenerationBusyIds((prev) => {
      const next = new Set(prev);
      for (const [nodeId, task] of latestByNode) {
        if (['queued', 'submitting', 'running'].includes(task.status)) next.add(nodeId);
        else next.delete(nodeId);
      }
      return next.size === prev.size && [...next].every((id) => prev.has(id)) ? prev : next;
    });
    setMediaNodes((prev) => {
      let changed = false;
      const next = prev.map((node) => {
        // A local rendered result is stronger evidence than a stale task
        // snapshot. Never paint a media card as "generating" when the file is
        // already on this canvas.
        if (node.status === 'done' && node.mediaUrl) return node;
        const task = activeByNode.get(node.id);
        if (task) {
          // A completion event is authoritative. An older task-list snapshot
          // may arrive after it and still say running; never demote a finished
          // node back to generating.
          // finalizePending：供应商已出结果、后端还在落盘归档。此时卡片已经能看，
          // 不能再退回「生成中」，否则用户会看到画面闪一下又变回转圈。
          if (node.status === 'generating' || node.finalizePending) return node;
          changed = true;
          return {
            ...node,
            status: 'generating' as const,
            lastError: undefined,
            generationStartedAt: task.started_at || task.created_at || node.generationStartedAt || new Date().toISOString(),
            generationFinishedAt: undefined,
            providerTaskStatus: task.status,
            providerTaskPhase: task.phase || '生成中',
            providerTaskProgress: Number(task.progress || node.providerTaskProgress || 0),
          };
        }
        // A terminal task is authoritative. This clears a stale local
        // "generating" state left by a backend restart or an old tab, which
        // otherwise blocks the generate button without issuing a new request.
        const terminal = latestByNode.get(node.id);
        if (
          node.status !== 'generating'
          || generationBusyIds.has(node.id)
          || !terminal
          || !['failed', 'interrupted', 'succeeded'].includes(terminal.status)
        ) {
          return node;
        }
        changed = true;
        return {
          ...node,
          status: (terminal.status === 'succeeded' ? 'done' : 'failed') as 'done' | 'failed',
          lastError: terminal.status === 'succeeded'
            ? undefined
            : (terminal.error || node.lastError || '生成任务已结束'),
          generationFinishedAt: terminal.finished_at || node.generationFinishedAt,
          providerTaskStatus: terminal.status,
          providerTaskPhase: terminal.phase || (terminal.status === 'succeeded' ? '已完成' : '生成失败'),
          providerTaskProgress: terminal.status === 'succeeded' ? 100 : node.providerTaskProgress,
        };
      });
      return changed ? next : prev;
    });
  }, [generationTasks, generationBusyIds]);

  const terminalRefreshRef = useRef<Map<string, string>>(new Map());
  useEffect(() => {
    const terminalSignatures = new Map<string, string>();
    for (const task of generationTasks) {
      if (
        !['succeeded', 'failed', 'interrupted'].includes(task.status)
        || !task.node_id
      ) {
        continue;
      }
      terminalSignatures.set(
        task.node_id,
        `${task.id}:${task.status}:${task.finished_at || task.updated_at || ''}`,
      );
    }
    const newlyTerminal = [...terminalSignatures].some(
      ([nodeId, signature]) => (
        terminalRefreshRef.current.get(nodeId) !== signature
      ),
    );
    terminalRefreshRef.current = terminalSignatures;
    if (!newlyTerminal) return;

    let alive = true;
    void Promise.all([
      getCanvasMediaNodes(session, currentEp),
      getGenerationTasks(session, 80, currentEp),
    ]).then(([remoteNodes, tasks]) => {
      if (!alive) return;
      setGenerationTasks(tasks);
      const remoteById = new Map(
        remoteNodes.map((node) => [node.id, node]),
      );
      setMediaNodes((prev) => prev.map((node) => {
        const remote = remoteById.get(node.id);
        if (!remote) return node;
        return remote;
      }));
      void getCanvas(session, currentEp)
        .then((refreshed) => {
          if (alive && !('empty' in refreshed)) setCanvas(refreshed);
        })
        .catch(() => {});
    }).catch(() => {
      // Keep the signatures unchanged so the next task poll retries the
      // terminal refresh instead of depending on a manual browser refresh.
      terminalRefreshRef.current.clear();
    });
    return () => {
      alive = false;
    };
  }, [generationTasks, session, currentEp]);

  /**
   * A succeeded generation task is authoritative, even when an earlier
   * provider attempt had already flipped the node to "failed". The task center
   * is the source of truth for media generation, so reconcile the final media
   * URL back into the local node instead of leaving a stale failure card.
   */
  useEffect(() => {
    const succeededTasks = generationTasks.filter(
      (task) => task.status === 'succeeded',
    );
    if (succeededTasks.length === 0) return;
    let alive = true;
    void getCanvasMediaNodes(session, currentEp)
      .then((remoteNodes) => {
        if (!alive) return;
        const remoteById = new Map(remoteNodes.map((node) => [node.id, node]));
        setMediaNodes((prev) => {
          let changed = false;
          const next = prev.map((node) => {
            const task = succeededTasks.find((item) => item.node_id === node.id);
            if (!task) return node;
            const remote = remoteById.get(node.id);
            if (!remote) return node;
            const reconciled = reconcileSucceededNode(node, task, remote);
            if (!reconciled) return node;
            changed = true;
            return reconciled as CanvasMediaNode;
          });
          return changed ? next : prev;
        });
        void getCanvas(session, currentEp)
          .then((refreshed) => { if (alive && !('empty' in refreshed)) setCanvas(refreshed); })
          .catch(() => {});
      })
      .catch(() => {
        // Keep the task unmarked so the next poll retries the reconciliation.
      });
    return () => {
      alive = false;
    };
  }, [generationTasks, session, currentEp]);
  const mediaSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mediaBatchSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mediaBatchSaveInFlightRef = useRef<Promise<void> | null>(null);
  const pendingMediaDeleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingMediaDeleteIdsRef = useRef<Set<string>>(new Set());
  const mediaPositionSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingMediaPatchesRef = useRef<Map<string, CanvasMediaNode>>(new Map());
  const pendingMediaNodeSavesRef = useRef<Map<string, CanvasMediaNode>>(new Map());
  const pendingPositionSavesRef = useRef<Map<string, {x: number; y: number; seq: number}>>(new Map());
  const positionMutationSeqRef = useRef(0);
  const broadcastMediaChange = useCallback((nodeIds: string[]) => {
    if (!syncChannelRef.current || nodeIds.length === 0) return;
    syncChannelRef.current.postMessage({
      type: 'canvas-nodes-updated',
      sessionId: session,
      sourceId: syncInstanceIdRef.current,
      nodeIds: [...new Set(nodeIds)],
      at: Date.now(),
    });
  }, [session]);
  const flushMediaNodeSaves = useCallback(() => {
    if (mediaBatchSaveInFlightRef.current) return;
    const snapshots = [...pendingMediaNodeSavesRef.current.values()];
    pendingMediaNodeSavesRef.current.clear();
    if (snapshots.length === 0) return;
    beginSave();
    let task: Promise<void>;
    task = saveCanvasMediaNodes(session, snapshots, currentEp)
      .then((result) => {
        const updates = new Map((result.updates || []).map((item) => [item.id, item]));
        if (updates.size > 0) {
          setMediaNodes((prev) => prev.map((node) => {
            const update = updates.get(node.id);
            return update
              ? {
                  ...node,
                  updatedAt: update.updatedAt || node.updatedAt,
                  positionUpdatedAt: update.positionUpdatedAt || node.positionUpdatedAt,
                }
              : node;
          }));
        }
        finishSave(true);
        broadcastMediaChange(snapshots.map((node) => node.id));
      })
      .catch((error) => {
        const message = error instanceof Error ? error.message : String(error);
        finishSave(false);
        setSaveError(message);
        if (message.includes('另一个标签页')) setToast(message);
      })
      .finally(() => {
        if (mediaBatchSaveInFlightRef.current === task) {
          mediaBatchSaveInFlightRef.current = null;
        }
        if (pendingMediaNodeSavesRef.current.size > 0) {
          if (mediaBatchSaveTimer.current) clearTimeout(mediaBatchSaveTimer.current);
          mediaBatchSaveTimer.current = setTimeout(flushMediaNodeSaves, 80);
        }
      });
    mediaBatchSaveInFlightRef.current = task;
  }, [beginSave, broadcastMediaChange, currentEp, finishSave, session, setToast]);

  const deleteMediaNodePersistently = useCallback(async (id: string) => {
    pendingMediaNodeSavesRef.current.delete(id);
    if (mediaBatchSaveTimer.current) {
      clearTimeout(mediaBatchSaveTimer.current);
      mediaBatchSaveTimer.current = null;
      if (pendingMediaNodeSavesRef.current.size > 0) {
        mediaBatchSaveTimer.current = setTimeout(flushMediaNodeSaves, 600);
      }
    }
    const inFlight = mediaBatchSaveInFlightRef.current;
    if (inFlight) await inFlight.catch(() => undefined);
    pendingMediaNodeSavesRef.current.delete(id);
    await deleteCanvasMediaNode(session, id);
  }, [flushMediaNodeSaves, session]);

  const cancelPendingMediaDeletes = useCallback(() => {
    if (pendingMediaDeleteTimerRef.current) {
      clearTimeout(pendingMediaDeleteTimerRef.current);
      pendingMediaDeleteTimerRef.current = null;
    }
    pendingMediaDeleteIdsRef.current.clear();
  }, []);

  const flushPendingMediaDeletes = useCallback(() => {
    const ids = [...pendingMediaDeleteIdsRef.current];
    pendingMediaDeleteIdsRef.current.clear();
    pendingMediaDeleteTimerRef.current = null;
    for (const id of ids) {
      void deleteMediaNodePersistently(id).catch(() => {});
    }
  }, [deleteMediaNodePersistently]);

  const queueMediaDelete = useCallback((id: string) => {
    pendingMediaDeleteIdsRef.current.add(id);
    if (pendingMediaDeleteTimerRef.current) clearTimeout(pendingMediaDeleteTimerRef.current);
    pendingMediaDeleteTimerRef.current = setTimeout(flushPendingMediaDeletes, 3000);
  }, [flushPendingMediaDeletes]);

  const persistMediaNodes = useCallback((next: CanvasMediaNode[], nodesToSave: CanvasMediaNode[] = next) => {
    for (const node of nodesToSave) pendingMediaNodeSavesRef.current.set(node.id, node);
    if (mediaBatchSaveTimer.current) clearTimeout(mediaBatchSaveTimer.current);
    mediaBatchSaveTimer.current = setTimeout(flushMediaNodeSaves, 600);
  }, [flushMediaNodeSaves]);
  const persistMediaPositions = useCallback((_next: CanvasMediaNode[]) => {
    if (mediaPositionSaveTimer.current) clearTimeout(mediaPositionSaveTimer.current);
    mediaPositionSaveTimer.current = setTimeout(() => {
      const snapshot = new Map(pendingPositionSavesRef.current);
      if (snapshot.size === 0) return;
      if (!pendingPositionSavesRef.current.size) return;
      const moves = [...snapshot].map(([id, position]) => ({
        id,
        position: {x: position.x, y: position.y},
        seq: position.seq,
      }));
      beginSave();
      void saveCanvasMediaNodePositions(session, moves, currentEp, syncInstanceIdRef.current)
        .then((result) => {
          const updates = new Map((result.updates || []).map((item) => [item.nodeId, item]));
          if (updates.size > 0) {
            setMediaNodes((prev) => prev.map((node) => {
              const update = updates.get(node.id);
              return update
                ? {
                    ...node,
                    updatedAt: update.updatedAt,
                    positionUpdatedAt: update.positionUpdatedAt,
                  }
                : node;
            }));
          }
          for (const [nodeId, sent] of snapshot) {
            const pending = pendingPositionSavesRef.current.get(nodeId);
            if (pending?.seq === sent.seq) pendingPositionSavesRef.current.delete(nodeId);
          }
          const missingIds = new Set(result.missing || []);
          const missingNodes = mediaNodesRef.current.filter((node) => missingIds.has(node.id));
          if (missingNodes.length > 0) {
            void saveCanvasMediaNodes(session, missingNodes, currentEp)
              .then(() => broadcastMediaChange(missingNodes.map((node) => node.id)))
              .catch((error) => {
                const message = error instanceof Error ? error.message : String(error);
                setSaveError(message);
              });
          }
          finishSave(true);
          broadcastMediaChange(moves.map((move) => move.id));
          if (pendingPositionSavesRef.current.size > 0) {
            persistMediaPositions(mediaNodesRef.current);
          }
        })
        .catch((error) => {
          const message = error instanceof Error ? error.message : String(error);
          finishSave(false);
          setSaveError(message);
        });
    }, 300);
  }, [beginSave, broadcastMediaChange, currentEp, finishSave, session]);

  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const channel = new BroadcastChannel(`vimax-canvas-${session}`);
    syncChannelRef.current = channel;
    channel.onmessage = (event) => {
      const message = event.data as {type?: string; sourceId?: string; nodeIds?: string[]};
      if (message.sourceId === syncInstanceIdRef.current) return;
      if (message.type === 'canvas-connections-updated') {
        if (pendingConnectionsRef.current || connectionSaveInFlightRef.current) return;
        void getCanvasWorkspace(session, currentEp)
          .then(({connections}) => {
            projectConnectionsRef.current = connections;
            const remoteEdges = connections
              .filter((connection) => connection.source === 'user')
              .map(userEdgeFromConnection);
            setUserEdges((prev) => {
              const signature = (edges: Edge[]) => edges
                .map((edge) => `${edge.id}:${edge.source}:${edge.target}`)
                .sort()
                .join('|');
              return signature(prev) === signature(remoteEdges) ? prev : remoteEdges;
            });
          })
          .catch(() => {});
        return;
      }
      if (message.type !== 'canvas-nodes-updated') return;
      const requested = new Set(message.nodeIds || []);
      void getCanvasMediaNodes(session, currentEp)
        .then((remoteNodes) => {
          let changed = 0;
          setMediaNodes((prev) => prev.map((local) => {
            if (requested.size > 0 && !requested.has(local.id)) return local;
            const remote = remoteNodes.find((item) => item.id === local.id);
            if (!remote || remote.updatedAt <= local.updatedAt) return local;
            changed += 1;
            return remote;
          }));
          if (changed > 0) {
            setToast('另一个标签页已更新此节点，当前节点已同步到最新状态');
          }
        })
        .catch(() => {});
    };
    return () => {
      channel.close();
      if (syncChannelRef.current === channel) syncChannelRef.current = null;
    };
  }, [currentEp, session, setToast]);

  useEffect(() => {
    let alive = true;
    const refreshTasks = () => {
      void getGenerationTasks(session, 80, currentEp)
        .then((tasks) => { if (alive) setGenerationTasks(tasks); })
        .catch(() => {});
    };
    setGenerationTasks([]);
    refreshTasks();
    // Only poll quickly while there is active work. Closed task center with no
    // active workments still gets a low-frequency reconciliation, not 1.2s churn.
    const interval = taskCenterOpen && activeGenerationTaskCount > 0
      ? 1200
      : activeGenerationTaskCount > 0
        ? 2500
        : 6000;
    const timer = window.setInterval(refreshTasks, interval);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [activeGenerationTaskCount, currentEp, session, taskCenterOpen]);

  useEffect(() => {
    let alive = true;
    const loadCatalog = () => {
      void getModelCatalog()
        .then((catalog) => { if (alive) setModelCatalog(catalog); })
        .catch(() => { if (alive) setModelCatalog(null); });
    };
    loadCatalog();
    window.addEventListener('canvas-model-catalog-changed', loadCatalog);
    return () => {
      alive = false;
      window.removeEventListener('canvas-model-catalog-changed', loadCatalog);
    };
  }, []);

  // Imported and legacy nodes often have no model fields (or point to a model the
  // user later disabled). Resolve them to the first available model exactly once
  // the live catalog is present so the UI and the backend do not disagree about
  // which model a node will use. This only rewrites metadata, never positions.
  useEffect(() => {
    if (!modelCatalog || mediaNodes.length === 0) return;
    const changed: CanvasMediaNode[] = [];
    const fallbackNotices: string[] = [];
    const next = mediaNodes.map((node) => {
      const candidates = modelCatalog.models.filter(
        (item) => item.enabled !== false && item.capability === node.kind,
      );
      if (!candidates.length) return node;
      const current = node.params || {};
      const stored = canonicalModelId(current.model || current.ttsModel);
      const exactStored = candidates.find((item) => item.id === stored || item.remoteModel === stored);
      const spec = exactStored
        || candidates.find((item) => item.providerId === current.provider)
        || candidates[0];
      if (stored && !exactStored) {
        fallbackNotices.push(`${node.title}：${current.model || current.ttsModel || stored} → ${spec.label || spec.id}`);
      }
      if (exactStored) return node;
      const merged = migrateUnavailableModelParams(current, spec, node.kind);
      if (JSON.stringify(merged) === JSON.stringify(current)) return node;
      const updated = {...node, params: merged};
      changed.push(updated);
      return updated;
    });
    if (!changed.length) return;
    if (fallbackNotices.length > 0) {
      const noticeKey = fallbackNotices.join('|');
      if (modelFallbackNoticeRef.current !== noticeKey) {
        modelFallbackNoticeRef.current = noticeKey;
        setToast(`原模型不可用，已自动切换 ${fallbackNotices.slice(0, 3).join('；')}${fallbackNotices.length > 3 ? '…' : ''}`);
      }
    }
    mediaNodesRef.current = next;
    setMediaNodes(next);
    void patchMediaNodesFields(session, changed).catch(() => {});
  }, [modelCatalog, mediaNodes, session, setToast]);

  // 清理「连线 → 引用参数」的联动（三个入口共用）：
  //   deleteEdgeById（点剪刀）、removeRefAndEdge（弹窗里点 ✕）、onEdgesDelete（Del/Backspace）。
  // 三者都必须走这里，否则总有一条路径删了线却留着 refs —— 表现就是"缩略图删不掉"。
  const dropRefsForPairs = useCallback((pairs: {src: string; tgt: string}[]) => {
    if (pairs.length === 0) return;
    for (const {src, tgt} of pairs) markRefRemoved(session, tgt, src);
    setMediaNodes((prev) => {
      let touched = false;
      const changedNodes: CanvasMediaNode[] = [];
      const next = prev.map((n) => {
        const hits = pairs.filter((x) => x.tgt === n.id);
        if (hits.length === 0) return n;
        const params = {...(n.params || {})};
        let prompt = n.prompt;
        let dirty = false;
        for (const {src} of hits) {
          const sourceTitle = prev.find((item) => item.id === src)?.title || '';
          const nextPrompt = removePromptMention(prompt, sourceTitle);
          if (nextPrompt !== prompt) {
            prompt = nextPrompt;
            dirty = true;
          }
          if ((params.refs || []).some((r) => r.nodeId === src)) {
            params.refs = (params.refs || []).filter((r) => r.nodeId !== src);
            dirty = true;
          }
          // 第 17 轮：音色改成多值后必须**逐项摘**，整体 delete voiceRef 会把其它音色一起清掉
          const vl = voiceRefList(params);
          if (vl.some((v) => v.nodeId === src)) {
            const kept = withVoiceRefs(params, vl.filter((v) => v.nodeId !== src));
            if (kept.voiceRefs) params.voiceRefs = kept.voiceRefs; else delete params.voiceRefs;
            if (kept.voiceRef) params.voiceRef = kept.voiceRef; else delete params.voiceRef;
            dirty = true;
          }
        }
        if (!dirty) return n;
        touched = true;
        const updated = {...n, prompt, params};
        changedNodes.push(updated);
        return updated;
      });
      if (!touched) return prev;
      persistMediaNodes(next, changedNodes);
      return next;
    });
  }, [session, persistMediaNodes]);

  // 删除任意一条连线（手动线直接移除；自动线记入 deletedAutoEdgeIds 不再重建）
  const deleteEdgeById = useCallback((id: string) => {
    pushHistory(userEdgesRef.current, notesRef.current);
    // ★ 连线与「引用参数」是同一个关系的两面，必须一起删：
    //   onConnect 会把源节点写进目标节点的 params.refs（linkRef），openMediaModal 又会按连线把缺失的 refs 补回来。
    //   早前只删线不清 refs → 提示词栏里的缩略图永远挂着，剪断连线也删不掉（用户实测反馈）。
    const targetEdge = userEdgesRef.current.find((edge) => edge.id === id);
    // 自动线的真实端点从当前画布的边里查 —— 不能从 id 反解：
    // 节点 id 形如 node-jlcvikebz45p-7e5a32f9 本身含 '-'，正则切不准（曾因此删线但 refs 没清）。
    const screenEdge = edgesRef.current.find((e) => e.id === id);
    const srcFlow = targetEdge?.source ?? screenEdge?.source ?? null;
    const tgtFlow = targetEdge?.target ?? screenEdge?.target ?? null;
    if (srcFlow?.startsWith(MEDIA_PREFIX) && tgtFlow?.startsWith(MEDIA_PREFIX)) {
      dropRefsForPairs([{src: srcFlow.slice(MEDIA_PREFIX.length), tgt: tgtFlow.slice(MEDIA_PREFIX.length)}]);
    }
    if (id.startsWith('e-user-')) {
      setUserEdges((prev) => prev.filter((edge) => edge.id !== id));
    } else {
      setDeletedAutoEdgeIds((prev) => {
        const next = new Set(prev);
        next.add(id);
        return next;
      });
    }
  }, [pushHistory, dropRefsForPairs]);

  // ★★★ 第 20 轮：把 deleteEdgeById 登记到模块级注册表。
  //   边组件的删除回调不能只靠 `edge.data.onDelete` —— 那是函数，写 localStorage 时
  //   被 JSON.stringify 整块丢掉，刷新后恢复的边 data 变成 `{}`，剪刀点击静默无效
  //   （用户原话「放大看到剪刀按钮就是点了没反应」）。
  //   注册表活在内存、每次挂载重新登记、不参与任何序列化，因此不会再丢。
  //   ⚠ 必须挂在 deleteEdgeById 声明**之后**：写在声明前会撞 TDZ
  //   （本项目已踩过两次 "used before its declaration"）。
  useEffect(() => {
    EDGE_DELETE_REG.current = deleteEdgeById;
    return () => { if (EDGE_DELETE_REG.current === deleteEdgeById) EDGE_DELETE_REG.current = null; };
  }, [deleteEdgeById]);

  // 从弹窗里移除一条引用：必须把画布上对应连线一起删掉。
  // 否则 openMediaModal 每次打开都会按残留连线把 refs 重新预填回来 → 用户感觉"删不掉"。
  const removeRefAndEdge = useCallback((targetNodeId: string, refNodeId: string) => {
    // 记入"已移除"黑名单 + 清 params.refs（两者都在 dropRefsForPairs 里）
    dropRefsForPairs([{src: refNodeId, tgt: targetNodeId}]);
    // 手动线：source=ref → target=当前节点（也可能反向，两种方向都清）
    const srcFlow = `media-${refNodeId}`;
    const tgtFlow = `media-${targetNodeId}`;
    const hits = userEdgesRef.current.filter((e) =>
      (e.source === srcFlow && e.target === tgtFlow) || (e.source === tgtFlow && e.target === srcFlow));
    for (const e of hits) {
      if (e.id.startsWith('e-user-')) setUserEdges((prev) => prev.filter((x) => x.id !== e.id));
      else setDeletedAutoEdgeIds((prev) => new Set(prev).add(e.id));
    }
    // 自动线（refIds 派生那类）也一并标记删除，避免它重建后再把引用带回来
    setDeletedAutoEdgeIds((prev) => new Set(prev).add(`e-media-${targetNodeId}-${refNodeId}`));
  }, [dropRefsForPairs]);

  const persistNotes = useCallback((next: CanvasNote[]) => {
    if (noteSaveTimer.current) clearTimeout(noteSaveTimer.current);
    noteSaveTimer.current = setTimeout(() => {
      beginSave();
      void saveCanvasNotes(session, next)
        .then(() => finishSave(true))
        .catch((error) => {
          finishSave(false);
          setSaveError(error instanceof Error ? error.message : String(error));
        });
    }, 600);
  }, [beginSave, finishSave, session]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    // 一集一画布：切集后重置镜头草稿与选中态（备注为项目级，保留）
    if (shotSaveTimer.current) { clearTimeout(shotSaveTimer.current); shotSaveTimer.current = null; }
    setDrafts({});
    setDirtyShots(new Set());
    setSavedFlash({});
    setSelectedId(null);
    setSaveError('');
    didInitialLayoutRef.current = false; // 切集后允许重新恢复该集自己的布局
    getCanvasWorkspace(session, currentEp)
      .then(({
        canvas: data,
        notes: noteList,
        mediaNodes: mediaList,
        connections: projectConnections,
      }) => {
        if (!alive) return;
        projectConnectionsRef.current = projectConnections;
        startTransition(() => {
          if ('empty' in data) {
            setCanvas(null);
          } else {
            setCanvas(data);
            const currentExists = currentEp
              ? data.meta.episodes.some((episode) => episode.ep_id === currentEp)
              : false;
            // 多集长剧：默认进入一集，避免整剧总览一次性渲染全部资产/镜头导致卡死
            if (!autoEpAppliedRef.current) {
              autoEpAppliedRef.current = true;
              if (!currentExists && data.meta.episodes.length > 1) {
                // 默认进入"最近活动且有内容"的一集（pickDefaultEpisode 纯函数，见 canvasLayout.ts）。
                // 旧逻辑只看 updated_at：120 集剧本刚拆完时最后被触碰的一集常常只有 0 镜
                // （实测 ep02），画布于是开在空集上——表现为"场景/镜头都不显示"。
                const target = pickDefaultEpisode(data.meta.episodes);
                if (target) setCurrentEp(target);
              }
            }
          }
          setNotes(noteList);
          if (mediaList.length > 400 && mediaList.some((node) => node.kind !== 'text')) {
            overviewKeyRef.current = currentEp || '__project__';
            setLargeCanvasVisibleBounds(null);
            setLargeCanvasOverview(true);
          } else {
            setLargeCanvasVisibleBounds(null);
          }
          setMediaNodes(mediaList);
        });
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [session, currentEp]);

  // 拉取创作质量自评报告（与画布数据并行），供镜头卡弱项色点使用。
  useEffect(() => {
    let alive = true;
    const timer = window.setTimeout(() => {
      getQualityReport(session, currentEp)
        .then((rep) => { if (alive) setQualityReport(rep); })
        .catch(() => { if (alive) setQualityReport(null); });
    }, 180);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [session, currentEp]);

  // 逐镜质量弱项映射：镜号 → 维度名列表
  const qualityFlags = useMemo<Record<number, string[]>>(() => {
    const map: Record<number, string[]> = {};
    const si = qualityReport?.shot_issues;
    if (si) {
      for (const [idx, dims] of Object.entries(si)) {
        map[Number(idx)] = dims;
      }
    }
    return map;
  }, [qualityReport]);

  const recomputeQuality = useCallback(async () => {
    setQualityRecomputing(true);
    try {
      const rep = await recomputeQualityReport(session, currentEp);
      setQualityReport(rep);
      const failCount = Number(rep.levels?.fail || 0);
      setToast(rep.passed ? '挂接机检已通过' : `挂接机检：${failCount} 段未通过`);
    } catch {
      setToast('质量自评重算失败');
    } finally {
      setQualityRecomputing(false);
    }
  }, [session, currentEp, setToast]);

  const onDraftChange = useCallback((shotIdx: number, field: keyof ShotDraft, value: string) => {
    setDrafts((prev) => {
      const current = prev[shotIdx];
      if (!current) return prev;
      return {...prev, [shotIdx]: {...current, [field]: value}};
    });
    setDirtyShots((prev) => {
      const next = new Set(prev);
      next.add(shotIdx);
      return next;
    });
    setSelectedId((prev) => prev ?? `shot-${shotIdx}`);
    // 防抖自动保存：连续输入只在停顿后写一次回磁盘；saveShotRef 始终指向最新闭包，无过期状态问题
    if (shotSaveTimer.current) clearTimeout(shotSaveTimer.current);
    shotSaveTimer.current = setTimeout(() => {
      shotSaveTimer.current = null;
      saveShotRef.current(shotIdx);
    }, 900);
  }, []);

  const saveShot = useCallback(async (shotIdx: number) => {
    const draft = drafts[shotIdx];
    if (!draft) return;
    setSavingShot(shotIdx);
    setSaveError('');
    const patch: Record<string, unknown> = {
      visual_desc: draft.visual_desc,
      motion_desc: draft.motion_desc,
      audio_desc: draft.audio_desc,
      transition_type: draft.transition_type,
      ff_desc: draft.ff_desc,
      lf_desc: draft.lf_desc,
      action_breakdown: draft.action_breakdown,
      video_prompt: draft.video_prompt,
      duration: draft.duration === '' ? null : Number(draft.duration),
      cam_idx: draft.cam_idx === '' ? null : Number(draft.cam_idx),
    };
    try {
      // P1：集模式（currentEp 非空）补丁必须写集级 shots，与渲染/画布读取路径一致
      await saveCanvasShot(session, shotIdx, patch, currentEp);
      setDirtyShots((prev) => {
        const next = new Set(prev);
        next.delete(shotIdx);
        return next;
      });
      setSavedFlash((prev) => ({...prev, [shotIdx]: Date.now()}));
      setCanvas((prevCanvas) => {
        if (!prevCanvas) return prevCanvas;
        return {
          ...prevCanvas,
          shots: prevCanvas.shots.map((shot) => {
            if (shot.idx !== shotIdx) return shot;
            return {
              ...shot,
              visual_desc: patch.visual_desc as string,
              motion_desc: patch.motion_desc as string,
              audio_desc: patch.audio_desc as string,
              transition_type: (patch.transition_type as string) || null,
              ff_desc: patch.ff_desc as string,
              lf_desc: patch.lf_desc as string,
              action_breakdown: patch.action_breakdown as string,
              duration: typeof patch.duration === 'number' ? patch.duration : null,
              cam_idx: typeof patch.cam_idx === 'number' ? patch.cam_idx : null,
            };
          }),
        };
      });
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSavingShot(null);
    }
  }, [drafts, session, currentEp]);
  saveShotRef.current = saveShot;

  // 段卡"生成本段"：与对话框同一渲染入口（sendMessage → Agent → vimax_render_video），
  // 以当前画布内容为准：先防抖写回的镜头草稿已落盘，渲染读的就是用户在画布上看到的状态。
  const onRenderSegment = useCallback((segmentId: number) => {
    const epLabel = currentEp ? ` ${fmtEp(currentEp)}` : '';
    const instruction = `请渲染${epLabel}段 ${segmentId}：phase=video，segment_ids=${segmentId}，自动复用已确认资产（角色/场景/道具/音色），480p 竖屏 9:16，全能参考直出；以画布当前内容为准。`;
    setToast(`已发起段 ${segmentId} 渲染，完成后视频自动挂回本段卡片`);
    void sendMessage(session, instruction)
      .catch((err) => setToast(`发起渲染失败：${err instanceof Error ? err.message : String(err)}`));
  }, [session, currentEp, setToast]);


  const notesRef = useRef<CanvasNote[]>([]);
  useEffect(() => {
    notesRef.current = notes;
  }, [notes]);

  // 通用内容节点（文本/图片/音频/视频空节点，复刻 BeefTV 画布）
  // ★ mediaNodes state 与 persistMediaNodes 已上移到 deleteEdgeById 之前（那里需要用它清 refs）
  const addMediaNode = useCallback((kind: CanvasMediaNodeKind, x: number, y: number) => {
    const titles: Record<CanvasMediaNodeKind, string> = {text: '文本节点', image: '图片节点', audio: '音频节点', video: '视频节点'};
    const defaultModel = modelCatalog?.models.find((item) => item.enabled !== false && item.capability === kind);
    const node: CanvasMediaNode = {
      id: newMediaNodeId(),
      kind,
      title: titles[kind],
      prompt: '',
      status: 'idle',
      x,
      y,
      updatedAt: new Date().toISOString(),
      params: defaultModel ? {
        ...(defaultModel.defaultParams || {}),
        provider: defaultModel.providerId,
        model: defaultModel.id,
        ...(kind === 'audio' ? {ttsModel: defaultModel.remoteModel} : {}),
      } : undefined,
    };
    pushHistory(userEdgesRef.current, notesRef.current, {removeAddedMedia: true});
    setMediaNodes((prev) => { const next = [...prev, node]; persistMediaNodes(next, [node]); return next; });
  }, [modelCatalog, persistMediaNodes, pushHistory]);
  const updateMediaNode = useCallback((
    id: string,
    patch: Partial<CanvasMediaNode>,
    options?: {persist?: boolean; mediaVersionSelection?: boolean},
  ) => {
    const previousNode = mediaNodesRef.current.find((node) => node.id === id);
    const onlyMetadata = !('x' in patch) && !('y' in patch);
    if (previousNode && !onlyMetadata) {
      const seq = ++positionMutationSeqRef.current;
      pendingPositionSavesRef.current.set(id, {
        x: typeof patch.x === 'number' ? patch.x : previousNode.x,
        y: typeof patch.y === 'number' ? patch.y : previousNode.y,
        seq,
      });
    }
    const nonEditableKeys = Object.keys(patch).filter((key) => key !== 'prompt' && key !== 'params');
    const recordable = onlyMetadata && !suppressMetadataHistoryRef.current
      && nonEditableKeys.length === 0 && ('prompt' in patch || 'params' in patch);
    if (!onlyMetadata) {
      metadataPastRef.current = [];
      metadataFutureRef.current = [];
    }
    if (previousNode && recordable) {
      const nextNode: CanvasMediaNode = {
        ...previousNode,
        ...patch,
        params: {...(previousNode.params || {}), ...(patch.params || {})},
        updatedAt: new Date().toISOString(),
      };
      const keys = Object.keys(patch).sort().join(',');
      const now = Date.now();
      const last = metadataPastRef.current[metadataPastRef.current.length - 1];
      if (last && last.nodeId === id && last.keys === keys && now - last.at < 800) {
        last.after = nextNode;
        last.at = now;
      } else {
        metadataPastRef.current.push({
          nodeId: id,
          before: {...previousNode, params: {...(previousNode.params || {})}},
          after: nextNode,
          keys,
          at: now,
        });
        if (metadataPastRef.current.length > 100) metadataPastRef.current.shift();
      }
      metadataFutureRef.current = [];
    }
    setMediaNodes((prev) => {
      let changed: CanvasMediaNode | null = null;
      const next = prev.map((n) => {
        if (n.id !== id) return n;
        // params 是嵌套对象：必须基于"最新 state"做读-改-写。
        // 旧写法 `{...p, [key]: value}` 在同一轮连发两次 patch（选比例会同时写 ratio + size）时
        // 第二次用的是旧快照，把第一次写的 ratio 覆盖掉 → 比例永远不生效、卡片尺寸不变。
        const merged: CanvasMediaNode = {...n, ...patch, updatedAt: new Date().toISOString()};
        merged.params = {...(n.params || {}), ...(patch.params || {})};
        changed = merged;
        return merged;
      });
      // ★★ 第 16 轮（关键数据安全修复）：这里**不再做全量保存**，只提交这一个节点。
      //   为什么必须改：全量 PUT 会把内存里 24 个节点的位置整批写回服务端。
      //   只要内存里的坐标比服务端旧（另一个标签页刚拖过、自动化刚改过、上一次拖拽
      //   还没同步完），一次「改提示词」就会把别人的位置改动静默覆盖掉 ——
      //   实测复现过 77px 的漂移，且因为只覆盖 position、其它字段都对，极难察觉。
      //   单节点提交不带 position 字段，后端深合并会保留库里的当前坐标。
      //   注意：patch 里若**显式带了位置**（拖动落点写回），必须走全量保存那条路，
      //   见 commitPositions / persistLayout —— 那里才需要写 position。
      if (changed) {
        if (options?.persist === false) return next;
        if (!onlyMetadata && changed && pendingMediaNodeSavesRef.current.has(id)) {
          pendingMediaNodeSavesRef.current.set(id, changed);
        }
        if (onlyMetadata) {
          const pending = pendingMediaPatchesRef.current.get(id);
          const snapshot = (
            options?.mediaVersionSelection
            || pending?._mediaVersionSelection
          )
            ? {...(changed as CanvasMediaNode), _mediaVersionSelection: true}
            : changed as CanvasMediaNode;
          pendingMediaPatchesRef.current.set(id, snapshot);
          if (mediaSaveTimer.current) clearTimeout(mediaSaveTimer.current);
          mediaSaveTimer.current = setTimeout(() => {
            const snapshots = [...pendingMediaPatchesRef.current.values()];
            pendingMediaPatchesRef.current.clear();
            if (snapshots.length === 0) return;
            beginSave();
            void patchMediaNodesFields(session, snapshots)
              .then(() => {
                finishSave(true);
                broadcastMediaChange(snapshots.map((snapshot) => snapshot.id));
              })
              .catch((error) => {
                const message = error instanceof Error ? error.message : String(error);
                const conflict = message.includes('另一个标签页');
                if (!conflict) {
                  for (const snapshot of snapshots) {
                    if (!pendingMediaPatchesRef.current.has(snapshot.id)) {
                      pendingMediaPatchesRef.current.set(snapshot.id, snapshot);
                    }
                  }
                } else {
                  const changedIds = new Set(snapshots.map((snapshot) => snapshot.id));
                  void getCanvasMediaNodes(session, currentEp)
                    .then((remoteNodes) => {
                      const byId = new Map(remoteNodes.map((item) => [item.id, item]));
                      setMediaNodes((prev) => prev.map((item) => (
                        changedIds.has(item.id) ? (byId.get(item.id) || item) : item
                      )));
                    })
                    .catch(() => {});
                  setToast(message);
                }
                finishSave(false);
                setSaveError(message);
              });
          }, 600);
        } else {
          persistMediaPositions(next);
        }
      }
      return next;
    });
  }, [beginSave, broadcastMediaChange, currentEp, finishSave, persistMediaNodes, persistMediaPositions, session, setToast]);

  const importLocalFileToNode = useCallback(async (
    target: CanvasMediaNode,
    file: File,
    options?: {
      signal?: AbortSignal;
      onProgress?: (percent: number) => void;
    },
  ) => {
    const fileKind = localFileKind(file);
    if (!fileKind || fileKind !== target.kind) {
      throw new Error(`请选择${target.kind === 'image' ? '图片' : target.kind === 'audio' ? '音频' : '视频'}文件`);
    }
    let uploaded: Awaited<ReturnType<typeof uploadWorkspaceFileWithProgress>>;
    try {
      uploaded = await uploadWorkspaceFileWithProgress(session, file, {
        signal: options?.signal,
        onProgress: (_loaded, _total, percent) => options?.onProgress?.(percent),
      });
    } catch (error) {
      setToast(error instanceof Error ? error.message : '素材上传失败');
      throw error;
    }
    const inspected = await inspectLocalMedia(file);
    // The initial node save may finish while the upload is in flight. Re-read the
    // latest version before writing media fields so the patch does not use a stale
    // baseUpdatedAt and conflict with its own autosave.
    let latestTarget = mediaNodesRef.current.find((item) => item.id === target.id) || target;
    for (let attempt = 0; attempt < 20 && target.updatedAt && latestTarget.updatedAt === target.updatedAt; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 100));
      latestTarget = mediaNodesRef.current.find((item) => item.id === target.id) || latestTarget;
    }
    const existingVersions = mediaVersionList(latestTarget);
    const currentVersionRecorded = existingVersions.some((version) => version.url === latestTarget.mediaUrl);
    if (latestTarget.mediaUrl && !currentVersionRecorded) {
      existingVersions.push({
        url: latestTarget.mediaUrl,
        sourceType: latestTarget.source?.type || 'generated',
        actualDuration: latestTarget.actualDuration,
      });
    }
    const uploadedAt = new Date().toISOString();
    const localVersion = {
      url: uploaded.url,
      sourceType: 'local',
      fileName: file.name,
      mime: uploaded.mime || file.type,
      size: uploaded.size || file.size,
      sha256: uploaded.sha256,
      storage: uploaded.storage,
      width: inspected.width,
      height: inspected.height,
      actualDuration: inspected.duration,
      generatedAt: uploadedAt,
    };
    const allVersions = [...existingVersions, localVersion].slice(-20);
    const selectedVersionIndex = allVersions.length - 1;
    const params: CanvasMediaParams = {
      ...(latestTarget.params || {}),
      mediaVersions: allVersions,
      mediaVersionIndex: selectedVersionIndex,
      ...(latestTarget.kind === 'video'
        ? {
            videoVersions: allVersions,
            videoVersionIndex: selectedVersionIndex,
          }
        : {}),
      ...((latestTarget.kind === 'image' || latestTarget.kind === 'video') && inspected.ratio
        ? {ratio: inspected.ratio}
        : {}),
    };
    const genericTitle = !latestTarget.title.trim()
      || ['图片节点', '视频节点', '音频节点', '素材'].includes(latestTarget.title.trim());
    const title = genericTitle
      ? file.name.replace(/\.[^.]+$/, '')
      : latestTarget.title;
    const nextPatch: Partial<CanvasMediaNode> = {
      title,
      mediaUrl: uploaded.url,
      status: 'done',
      lastError: undefined,
      actualDuration: inspected.duration,
      promptStale: false,
      promptStaleReason: '',
      sourcePromptUpdatedAt: undefined,
      source: {
        type: 'local',
        fileName: file.name,
        mime: uploaded.mime || file.type,
        size: uploaded.size || file.size,
        uploadedAt,
        sha256: uploaded.sha256,
        width: inspected.width,
        height: inspected.height,
      },
      storage: uploaded.storage,
      storageError: '',
      params,
    };
    const persistedAt = new Date().toISOString();
    await patchMediaNodeFields(session, {
      ...latestTarget,
      ...nextPatch,
      params: {...(latestTarget.params || {}), ...(nextPatch.params || {})},
      updatedAt: latestTarget.updatedAt,
      _mediaImport: true,
    });
    updateMediaNode(target.id, {...nextPatch, updatedAt: persistedAt}, {persist: false});
    setToast(`${latestTarget.mediaUrl ? '已添加本地版本' : '已导入本地素材'}：${file.name}`);
  }, [session, setToast, updateMediaNode]);

  const openNodeLocalUpload = useCallback((node: CanvasMediaNode) => {
    if (node.kind === 'text') return;
    suppressNodeOpen();
    nodeLocalUploadTargetRef.current = node.id;
    const accept = node.kind === 'image'
        ? 'image/*,.png,.jpg,.jpeg,.webp,.gif,.bmp,.avif'
        : node.kind === 'audio'
          ? 'audio/*,.mp3,.wav,.ogg,.m4a,.flac,.aac'
          : 'video/*,.mp4,.webm,.mov';
    setNodeLocalUploadAccept(accept);
    if (nodeLocalUploadInputRef.current) nodeLocalUploadInputRef.current.accept = accept;
    nodeLocalUploadInputRef.current?.click();
  }, [suppressNodeOpen]);

  const uploadNodeLocalFromMenu = useCallback(async (file: File) => {
    const targetId = nodeLocalUploadTargetRef.current;
    const target = mediaNodesRef.current.find((node) => node.id === targetId);
    nodeLocalUploadTargetRef.current = null;
    if (!target) return;
    const controller = new AbortController();
    batchUploadAbortRef.current = controller;
    setBatchUploadState({total: 1, done: 0, percent: 0, currentName: file.name});
    try {
      await importLocalFileToNode(target, file, {
        signal: controller.signal,
        onProgress: (percent) => {
          setBatchUploadState({total: 1, done: 0, percent, currentName: file.name});
        },
      });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === 'AbortError')) {
        setToast(error instanceof Error ? error.message : '上传失败');
      }
    } finally {
      batchUploadAbortRef.current = null;
      setBatchUploadState(null);
    }
  }, [importLocalFileToNode, setToast]);

  useEffect(() => {
    const flushPendingMediaPatches = () => {
      if (mediaSaveTimer.current) {
        clearTimeout(mediaSaveTimer.current);
        mediaSaveTimer.current = null;
      }
      const snapshots = [...pendingMediaPatchesRef.current.values()];
      pendingMediaPatchesRef.current.clear();
      if (snapshots.length === 0) return;
      void patchMediaNodesFields(session, snapshots, {keepalive: true}).catch(() => {
        // Navigation is already underway; recovery on the next edit is preferable
        // to blocking the page unload with an alert.
      });
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === 'hidden') flushPendingMediaPatches();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    window.addEventListener('pagehide', flushPendingMediaPatches);
    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      window.removeEventListener('pagehide', flushPendingMediaPatches);
    };
  }, [session]);

  // 级联避让：同一坐标反复插入会把新节点完全叠在上一个下面（表现为"点了没反应"）。
  // 已占用则沿对角线让位，直到 50 次兜底。
  // ★ 定义必须早于 uploadRefFile（下方 ~2606 行）—— 放到后面会 TDZ：
  //   "Cannot access 'freeSpot' before initialization"，整页白屏（本轮已踩一次）。
  const freeSpot = useCallback((x: number, y: number) => {
    const occupied = (px: number, py: number) =>
      notesRef.current.some((n) => Math.abs(n.x - px) < 44 && Math.abs(n.y - py) < 44) ||
      mediaNodesRef.current.some((mn) => Math.abs(mn.x - px) < 44 && Math.abs(mn.y - py) < 44);
    let cx = Math.round(x);
    let cy = Math.round(y);
    let guard = 0;
    while (occupied(cx, cy) && guard < 50) { cx += 30; cy += 30; guard += 1; }
    return {x: cx, y: cy};
  }, []);

  // 把画布上已有节点加为该卡片的引用（「从画布选择」入口）
  const addRefById = useCallback((target: CanvasMediaNode, refNodeId: string) => {
    const src = mediaNodesRef.current.find((n) => n.id === refNodeId);
    if (!src) return;
    const refs = [...(target.params?.refs || [])];
    const voiceRefs = voiceRefList(target.params || {});
    const srcFlow = `media-${refNodeId}`;
    const tgtFlow = `media-${target.id}`;
    const ensureEdge = () => {
      const exists = userEdgesRef.current.some((e) =>
        (e.source === srcFlow && e.target === tgtFlow) || (e.source === tgtFlow && e.target === srcFlow));
      if (exists) return;
      setUserEdges((prev) => [...prev, {
        id: nextUserEdgeId(),
        source: srcFlow,
        target: tgtFlow,
        type: 'deletable',
        className: 'canvas-edge-user',
        data: {onDelete: deleteEdgeById},
      } as Edge]);
    };
    if (refs.some((r) => r.nodeId === refNodeId) || voiceRefs.some((r) => r.nodeId === refNodeId)) {
      ensureEdge();
      clearRefRemoved(session, target.id, refNodeId);
      return;
    }
    const model = modelCatalog?.models.find((item) =>
      item.id === target.params?.model
      || item.remoteModel === target.params?.model
      || item.id === target.params?.ttsModel
    );
    const maxRefs = modelMaxRefs(model);
    if (refs.length + voiceRefs.length >= maxRefs) {
      setToast(`当前模型最多引用 ${maxRefs} 个素材`);
      return;
    }
    const maxAudiosLimit = model?.limits?.maxAudios;
    const maxAudios = Number(
      maxAudiosLimit === undefined || maxAudiosLimit === null
        ? 3
        : maxAudiosLimit,
    );
    if (target.kind === 'video' && src.kind === 'audio' && voiceRefs.length >= maxAudios) {
      setToast(`当前模型最多引用 ${maxAudios} 个音色`);
      return;
    }
    if (!(target.kind === 'video' && src.kind === 'audio')) {
      refs.push({nodeId: refNodeId, label: src.title || src.kind, url: src.mediaUrl});
    }
    const params: CanvasMediaParams = {...(target.params || {}), refs};
    // 视频卡的音频引用同样累加进多值音色列表（第 17 轮：不能覆盖）
    const nextParams = (target.kind === 'video' && src.kind === 'audio')
      ? withVoiceRefs(params, [...voiceRefList(params), {nodeId: refNodeId, label: src.title || src.kind, url: src.mediaUrl}])
      : params;
    // 引用与连线必须同源；@、上传、从画布选择三个入口统一在这里补线。
    ensureEdge();
    // 清掉"已移除"黑名单：用户显式加回来，就不该再被过滤掉
    clearRefRemoved(session, target.id, refNodeId);
    updateMediaNode(target.id, {params: nextParams});
  }, [modelCatalog, session, setToast, updateMediaNode, deleteEdgeById]);

  // 上传文件并作为引用（「上传图片/音频」入口）：真上传 → 建节点 → 加引用
  const uploadRefFile = useCallback(async (target: CanvasMediaNode, file: File) => {
    try {
      const uploaded = await uploadWorkspaceFile(session, file);
      const url = uploaded.url;
      if (!url) throw new Error('上传未返回地址');
      const kind = localFileKind(file) || 'image';
      const inspected = await inspectLocalMedia(file);
      const uploadedAt = new Date().toISOString();
      const localVersion = {
        url,
        sourceType: 'local',
        fileName: file.name,
        mime: uploaded.mime || file.type,
        size: uploaded.size || file.size,
        sha256: uploaded.sha256,
        storage: uploaded.storage,
        width: inspected.width,
        height: inspected.height,
        actualDuration: inspected.duration,
        generatedAt: uploadedAt,
      };
      // 新节点落在目标卡左侧，避免和已有卡重叠
      const pos = freeSpot(target.x - 320, target.y);
      const newNode: CanvasMediaNode = {
        id: newMediaNodeId(),
        kind,
        title: file.name,
        prompt: '',
        mediaUrl: url,
        status: 'done',
        actualDuration: inspected.duration,
        source: {
          type: 'local',
          fileName: file.name,
          mime: uploaded.mime || file.type,
          size: uploaded.size || file.size,
          uploadedAt,
          sha256: uploaded.sha256,
          width: inspected.width,
          height: inspected.height,
        },
        storage: uploaded.storage,
        storageError: '',
        x: pos.x,
        y: pos.y,
        updatedAt: new Date().toISOString(),
        params: {
          mediaVersions: [localVersion],
          mediaVersionIndex: 0,
          ...(kind === 'video' ? {videoVersions: [localVersion], videoVersionIndex: 0} : {}),
          ...((kind === 'image' || kind === 'video') && inspected.ratio ? {ratio: inspected.ratio} : {}),
        },
      };
      const previous = mediaNodesRef.current;
      const next = [...previous, newNode];
      mediaNodesRef.current = next;
      setMediaNodes(next);
      try {
        // 先把上传节点真正落库，再绑定引用。否则引用更新会与节点保存竞态，
        // 刷新后只有引用、没有被引用的上传节点。
        await saveCanvasMediaNodes(session, [newNode], currentEp);
        addRefById(target, newNode.id);
      } catch (error) {
        mediaNodesRef.current = previous;
        setMediaNodes(previous);
        throw error;
      }
      setToast(`已添加参考${kind === 'audio' ? '音频' : kind === 'video' ? '视频' : '图片'}：${file.name}`);
    } catch (err) {
      setToast(err instanceof Error ? err.message : '上传失败');
    }
  }, [session, currentEp, freeSpot, addRefById, setToast]);

  // 顶部批量上传和拖入画布共用这一条链路：真实上传 → 读取媒体元数据 → 创建节点 → 落库。
  const [dropArmed, setDropArmed] = useState(false);
  const uploadFilesToCanvas = useCallback(async (
    files: File[],
    origin?: {x: number; y: number},
  ) => {
    const supported = files.filter((file) => !!localFileKind(file));
    if (supported.length === 0) {
      setToast('没有可导入的图片、音频或视频文件');
      return;
    }
    const base = origin || rf.screenToFlowPosition({
      x: window.innerWidth * 0.48,
      y: window.innerHeight * 0.45,
    });
    const controller = new AbortController();
    batchUploadAbortRef.current = controller;
    setBatchUploadState({total: supported.length, done: 0, percent: 0, currentName: supported[0].name});
    let completed = 0;
    const failed: string[] = [];
    for (let index = 0; index < supported.length; index += 1) {
      const file = supported[index];
      const kind = localFileKind(file);
      if (!kind) continue;
      setBatchUploadState({
        total: supported.length,
        done: completed,
        percent: 0,
        currentName: file.name,
      });
      try {
        const uploaded = await uploadWorkspaceFileWithProgress(session, file, {
          signal: controller.signal,
          onProgress: (_loaded, _total, percent) => {
            setBatchUploadState({
              total: supported.length,
              done: completed,
              percent,
              currentName: file.name,
            });
          },
        });
        const inspected = await inspectLocalMedia(file);
        const uploadedAt = new Date().toISOString();
        const localVersion = {
          url: uploaded.url,
          sourceType: 'local',
          fileName: file.name,
          mime: uploaded.mime || file.type,
          size: uploaded.size || file.size,
          sha256: uploaded.sha256,
          storage: uploaded.storage,
          width: inspected.width,
          height: inspected.height,
          actualDuration: inspected.duration,
          generatedAt: uploadedAt,
        };
        const node: CanvasMediaNode = {
          id: newMediaNodeId(),
          kind,
          title: file.name.replace(/\.[^.]+$/, ''),
          prompt: '',
          mediaUrl: uploaded.url,
          status: 'done',
          actualDuration: inspected.duration,
          source: {
            type: 'local',
            fileName: file.name,
            mime: uploaded.mime || file.type,
            size: uploaded.size || file.size,
            uploadedAt,
            sha256: uploaded.sha256,
            width: inspected.width,
            height: inspected.height,
          },
          storage: uploaded.storage,
          storageError: '',
          x: base.x + (index % 3) * 300,
          y: base.y + Math.floor(index / 3) * 260,
          updatedAt: uploadedAt,
          params: {
            mediaVersions: [localVersion],
            mediaVersionIndex: 0,
            ...(kind === 'video' ? {videoVersions: [localVersion], videoVersionIndex: 0} : {}),
            ...((kind === 'image' || kind === 'video') && inspected.ratio
              ? {ratio: inspected.ratio}
              : {}),
          },
        };
        setMediaNodes((prev) => [...prev, node]);
        try {
          const saved = await saveCanvasMediaNodes(session, [node], currentEp);
          const savedNode = saved.updates?.find((item) => item.id === node.id);
          if (savedNode) {
            setMediaNodes((prev) => prev.map((item) => item.id === node.id
              ? {
                  ...item,
                  updatedAt: savedNode.updatedAt || item.updatedAt,
                  positionUpdatedAt: savedNode.positionUpdatedAt || item.positionUpdatedAt,
                }
              : item));
          }
          broadcastMediaChange([node.id]);
        } catch (error) {
          setMediaNodes((prev) => prev.filter((item) => item.id !== node.id));
          throw error;
        }
        completed += 1;
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') break;
        failed.push(file.name);
      }
    }
    batchUploadAbortRef.current = null;
    setBatchUploadState(null);
    if (completed > 0) {
      setToast(`已导入 ${completed} 个本地素材${failed.length ? `，${failed.length} 个失败` : ''}`);
    } else if (failed.length > 0) {
      setToast(`上传失败：${failed.slice(0, 3).join('、')}${failed.length > 3 ? '…' : ''}`);
    }
  }, [broadcastMediaChange, currentEp, rf, session, setToast]);

  const handleCanvasDrop = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    setDropArmed(false);
    const files = Array.from(event.dataTransfer.files || []);
    if (files.length === 0) return;
    const origin = rf.screenToFlowPosition({x: event.clientX, y: event.clientY});
    void uploadFilesToCanvas(files, origin);
  }, [rf, uploadFilesToCanvas]);
  const deleteMediaNode = useCallback((id: string) => {
    pushHistory(userEdgesRef.current, notesRef.current);
    // A node can be referenced through params even when there is no visible edge
    // (@ insertion and uploaded references both support that path). Clean every
    // dependant before removing the source so generation never sees a dangling ref.
    const pairs = new Map<string, {src: string; tgt: string}>();
    const addPair = (src: string, tgt: string) => {
      if (src && tgt && src !== tgt) pairs.set(`${src}::${tgt}`, {src, tgt});
    };
    for (const other of mediaNodesRef.current) {
      if (other.id === id) continue;
      if ((other.params?.refs || []).some((ref) => ref.nodeId === id)
        || voiceRefList(other.params || {}).some((ref) => ref.nodeId === id)) {
        addPair(id, other.id);
      }
    }
    const mediaFlowId = `${MEDIA_PREFIX}${id}`;
    for (const edge of userEdgesRef.current) {
      if (edge.source !== mediaFlowId || !edge.target?.startsWith(MEDIA_PREFIX)) continue;
      addPair(id, edge.target.slice(MEDIA_PREFIX.length));
    }
    dropRefsForPairs([...pairs.values()]);
    setMediaNodes((prev) => {
      return prev.filter((n) => n.id !== id);
    });
    // 落库删除：不调用后端 DELETE 的话，节点只是从本地 state 里摘掉，
    // 后端 payload.nodes 仍保留该节点 → 刷新后"复活"。
    void deleteMediaNodePersistently(id).catch((err: unknown) => {
      setToast(err instanceof Error ? err.message : '节点删除失败');
    });
    // 顺带清掉指向它的手动连线，避免残留悬空线（线还在但端点节点已不存在）
    setUserEdges((prev) => prev.filter((edge) => edge.source !== mediaFlowId && edge.target !== mediaFlowId));
    // 删除是可撤销操作，明确告诉用户，避免误删后以为找不回来
    setToast('已删除节点，Ctrl+Z 可撤销');
  }, [deleteMediaNodePersistently, dropRefsForPairs, pushHistory, setToast]);
  // 卡片上的删除入口（✕ / ⋯菜单）同样先弹确认框，与 Del 键行为一致
  const requestDeleteMediaNode = useCallback((id: string) => {
    setDeleteTargets([{id: `${MEDIA_PREFIX}${id}`, type: 'media'}]);
  }, []);
  // 节点上生成：状态置生成中 → 走与对话框同一入口 sendMessage，agent 生成对应媒体
  // 活动任务面板：agent 回合结束（done/error）→ 生成中的节点标记完成并刷新画布资产
  useEffect(() => {
    const unsub = subscribeToEvents((event) => {
      if (event.type !== 'done' && event.type !== 'error') return;
      if (event.sessionId && event.sessionId !== session) return;
      const nodeId = event.nodeId as string | undefined;
      if (nodeId) {
        setGenerationBusyIds((prev) => {
          if (!prev.has(nodeId)) return prev;
          const next = new Set(prev);
          next.delete(nodeId);
          return next;
        });
      }
      // 只翻转事件指名的节点：多节点同时生成时互不串号（旧逻辑会把所有 generating 一起翻）
      setMediaNodes((prev) => {
        const idx = prev.findIndex((n) => n.id === nodeId);
        if (idx < 0) return prev;
        const n = prev[idx];
        const next = [...prev];
        // 文本节点：生成结果是正文，要覆盖 prompt；媒体节点才写 mediaUrl。
        // 以前只写 mediaUrl，文本卡生成完正文不变 —— 看起来"白跑一趟"。
        const genText = event.text as string | undefined;
        const generatedParams = event.params && typeof event.params === 'object'
          ? event.params as Partial<CanvasMediaParams>
          : null;
        const finishedAt = new Date().toISOString();
        next[idx] = {
          ...n,
          status: (event.type === 'error' ? 'failed' : 'done') as CanvasMediaNode['status'],
          prompt: event.type === 'done' && n.kind === 'text' && genText ? genText : n.prompt,
          mediaUrl: event.type === 'done' ? ((event.url as string | undefined) || n.mediaUrl) : n.mediaUrl,
          params: event.type === 'done' && generatedParams
            ? {...n.params, ...generatedParams}
            : n.params,
          lastError: event.type === 'error'
            ? String(event.message || '生成失败')
            : undefined,
          generationFinishedAt: finishedAt,
          providerTaskStatus: event.type === 'error' ? 'failed' : 'completed',
          providerTaskPhase: event.type === 'error' ? '生成失败' : '已完成',
          providerTaskProgress: event.type === 'error' ? n.providerTaskProgress : 100,
          storage: event.type === 'done' && event.storage && typeof event.storage === 'object'
            ? event.storage as CanvasMediaNode['storage']
            : n.storage,
          storageError: event.type === 'done' && typeof event.storageError === 'string'
            ? event.storageError
            : n.storageError,
          promptStale: event.type === 'done' && (n.kind === 'image' || n.kind === 'audio') ? false : n.promptStale,
          promptStaleReason: event.type === 'done' && (n.kind === 'image' || n.kind === 'audio') ? '' : n.promptStaleReason,
          updatedAt: new Date().toISOString(),
        };
        return next;
      });
      if (event.type === 'error') {
        setToast(`生成失败：${event.message || '未知错误'}`);
      }
      // 刷新画布：agent 新生成的资产/片段自动出现在托盘与画布上
      void getCanvas(session, currentEp)
        .then((d) => { if (!('empty' in d)) setCanvas(d); })
        .catch(() => {});
    }, () => {});
    return unsub;
  }, [session, currentEp, setToast]);

  // A video POST may stay open for several minutes while the provider queues and
  // renders it. If this tab reloads, another tab owns the request, or the done
  // event is lost, the backend can be complete while this tab still shows
  // "generating" forever. Reconcile only terminal states; prompt, refs and
  // position remain owned by this tab.
  const activeGenerationNodeCount = mediaNodes.filter(
    (node) => node.status === 'generating' || generationBusyIds.has(node.id),
  ).length;
  const hasGeneratingMedia = activeGenerationNodeCount > 0;
  // 轮询就是「卡片多久变」的下限，所以有任务时按 1.5s 拉，闲时降到 15s 兜底。
  // 早先没有任务就整个不轮询，一旦本地状态丢了（刷新、换标签页、任务过期），
  // 后端明明完成也永远不会再回来。
  // SSE is an optimization, not a guarantee: proxies can keep the connection
  // open while buffering events. While a paid task is in flight, always poll
  // terminal state quickly so a lost `done` event cannot leave a completed
  // image/video invisible for 12 seconds.
  const generationSyncMs = hasGeneratingMedia || activeGenerationTaskCount > 0
    ? 1_500
    : eventsConnected ? 7_000 : 15_000;
  useEffect(() => {
    let alive = true;
    let syncing = false;
    const syncTerminalState = async () => {
      if (syncing) return;
      syncing = true;
      try {
        const remoteNodes = await getCanvasMediaNodes(session, currentEp);
        if (!alive) return;
        const remoteById = new Map(remoteNodes.map((node) => [node.id, node]));
        const generatingIds = new Set(
          mediaNodesRef.current
            .filter((node) => node.status === 'generating' || generationBusyIds.has(node.id))
            .map((node) => node.id),
        );
        const remoteCompleted = remoteNodes.some(
          (node) => generatingIds.has(node.id) && node.status === 'done',
        );
        setMediaNodes((prev) => {
          let changed = false;
          const next = prev.map((node) => {
            // 还有一种漏网状态：本地已经标成 done，但没有地址（任务先落终态、
            // 内容稍后才写回，或刷新打断了提交）。只要远端有内容就补上。
            const needsContent = !node.mediaUrl
              && remoteById.get(node.id)?.status === 'done'
              && !!remoteById.get(node.id)?.mediaUrl;
            const remoteVersionCount = remoteById.get(node.id)?.params?.mediaVersions?.length || 0;
            const localVersionCount = node.params?.mediaVersions?.length || 0;
            const hasNewRemoteVersion = remoteVersionCount > localVersionCount
              && remoteById.get(node.id)?.status === 'done';
            if (
              node.status !== 'generating'
              && !generationBusyIds.has(node.id)
              && !needsContent
              && !hasNewRemoteVersion
            ) return node;
            const remote = remoteById.get(node.id);
            if (!remote) return node;
            if (remote.status === 'generating') {
              const changedProgress = node.status !== 'generating'
                || node.providerTaskStatus !== remote.providerTaskStatus
                || node.providerTaskPhase !== remote.providerTaskPhase
                || node.providerTaskProgress !== remote.providerTaskProgress
                || node.generationStartedAt !== remote.generationStartedAt;
              if (!changedProgress) return node;
              changed = true;
              return {
                ...node,
                status: 'generating' as CanvasMediaNode['status'],
                generationStartedAt: remote.generationStartedAt || node.generationStartedAt,
                providerTaskStatus: remote.providerTaskStatus,
                providerTaskPhase: remote.providerTaskPhase,
                providerTaskProgress: remote.providerTaskProgress,
              };
            }
            // A newly submitted attempt owns the status until its task reaches a
            // terminal state. The first remote read can still contain the previous
            // completed attempt, and applying that `done` here makes the task chip
            // and edge animation alternate between running and idle.
            // 用「上一次的完成时间是否早于本次开始时间」判断新旧，而不是一味跳过：
            // 后端现在拿到供应商结果就写 done，任务行还停在 running，条件太粗会
            // 把这次真正的完成挡在门外。
            if (generationBusyIds.has(node.id)) {
              const remoteFinished = remote.generationFinishedAt || '';
              const localStarted = node.generationStartedAt || '';
              if (!remoteFinished || (localStarted && remoteFinished < localStarted)) return node;
            }
            if (remote.status !== 'done' && remote.status !== 'failed') return node;
            changed = true;
            return {
              ...node,
              status: remote.status,
              mediaUrl: remote.status === 'done' ? (remote.mediaUrl || node.mediaUrl) : node.mediaUrl,
              params: {
                ...(node.params || {}),
                mediaVersions: remote.params?.mediaVersions || node.params?.mediaVersions,
                mediaVersionIndex: remote.params?.mediaVersionIndex ?? node.params?.mediaVersionIndex,
                ...(node.kind === 'video'
                  ? {
                      videoVersions: remote.params?.videoVersions || node.params?.videoVersions,
                      videoVersionIndex: remote.params?.videoVersionIndex ?? node.params?.videoVersionIndex,
                    }
                  : {}),
              },
              lastError: remote.status === 'failed' ? (remote.lastError || '生成失败') : undefined,
              generationFinishedAt: remote.generationFinishedAt,
              providerTaskStatus: remote.providerTaskStatus,
              providerTaskPhase: remote.providerTaskPhase,
              providerTaskProgress: remote.providerTaskProgress,
              finalizePending: remote.finalizePending,
              promptStale: remote.promptStale,
              promptStaleReason: remote.promptStaleReason,
              sourcePromptUpdatedAt: remote.sourcePromptUpdatedAt,
              updatedAt: new Date().toISOString(),
            };
          });
          return changed ? next : prev;
        });
        if (remoteCompleted) {
          const refreshed = await getCanvas(session, currentEp);
          if (alive && !('empty' in refreshed)) setCanvas(refreshed);
        }
      } catch {
        // A transient local read failure must not turn a live provider task into
        // a failed node. The next interval retries from backend truth.
      } finally {
        syncing = false;
      }
    };
    void syncTerminalState();
    const timer = window.setInterval(() => void syncTerminalState(), generationSyncMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [session, currentEp, generationSyncMs, generationBusyIds]);

  // 事件流：后端拿到供应商结果、落盘完成、失败时都会推一条，卡片直接改，
  // 不再等下一次轮询。EventSource 自带重连，服务端按 Last-Event-ID 补发。
  useEffect(() => {
    if (typeof EventSource === 'undefined' || !session) return;
    let source: EventSource | null = null;
    let disposed = false;
    let terminalRefreshTimer: number | null = null;

    const refreshAfterTerminalEvent = () => {
      if (terminalRefreshTimer !== null) {
        window.clearTimeout(terminalRefreshTimer);
      }
      terminalRefreshTimer = window.setTimeout(() => {
        terminalRefreshTimer = null;
        if (disposed) return;
        void getCanvasMediaNodes(session, currentEp)
          .then((remoteNodes) => {
            if (disposed) return;
            const remoteById = new Map(
              remoteNodes.map((item) => [item.id, item]),
            );
            setMediaNodes((prev) => prev.map((node) => (
              remoteById.get(node.id) || node
            )));
          })
          .catch(() => {});
        void getGenerationTasks(session, 80, currentEp)
          .then((tasks) => {
            if (!disposed) setGenerationTasks(tasks);
          })
          .catch(() => {});
        void getCanvas(session, currentEp)
          .then((refreshed) => {
            if (!disposed && !('empty' in refreshed)) setCanvas(refreshed);
          })
          .catch(() => {});
      }, 120);
    };

    const applyEvent = (raw: string) => {
      let data: Record<string, unknown>;
      try {
        data = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        return;
      }
      const nodeId = typeof data.nodeId === 'string' ? data.nodeId : '';
      if (!nodeId) return;
      const rawStatus = data.status;
      const nextStatus: CanvasMediaNode['status'] = rawStatus === 'failed'
        ? 'failed'
        : rawStatus === 'done' ? 'done' : undefined;
      const url = typeof data.url === 'string' && data.url ? mediaSourceUrl(data.url) : '';
      setMediaNodes((prev) => {
        const index = prev.findIndex((node) => node.id === nodeId);
        if (index < 0) return prev;
        const node = prev[index];
        const phase = typeof data.providerTaskPhase === 'string' ? data.providerTaskPhase : node.providerTaskPhase;
        const progress = typeof data.providerTaskProgress === 'number'
          ? data.providerTaskProgress
          : node.providerTaskProgress;
        if (!nextStatus && phase === node.providerTaskPhase && progress === node.providerTaskProgress) {
          return prev;
        }
        const updated: CanvasMediaNode = {
          ...node,
          ...(nextStatus ? { status: nextStatus } : {}),
          ...(url ? { mediaUrl: url } : {}),
          ...(typeof data.finalizePending === 'boolean' ? { finalizePending: data.finalizePending } : {}),
          ...(typeof data.providerTaskStatus === 'string' ? { providerTaskStatus: data.providerTaskStatus } : {}),
          providerTaskPhase: phase,
          providerTaskProgress: progress,
          ...(typeof data.generationFinishedAt === 'string' && data.generationFinishedAt
            ? { generationFinishedAt: data.generationFinishedAt }
            : {}),
          ...(node.kind === 'text' && typeof data.text === 'string' ? { prompt: data.text } : {}),
          lastError: nextStatus === 'failed'
            ? String(data.error || node.lastError || '生成失败')
            : (nextStatus === 'done' ? undefined : node.lastError),
          updatedAt: new Date().toISOString(),
        };
        const copy = prev.slice();
        copy[index] = updated;
        return copy;
      });
      if (nodeId && typeof data.providerTaskStatus === 'string' && data.providerTaskStatus === 'retrying') {
        setGenerationTasks((prev) => prev.map((task) => (
          task.node_id === nodeId && ['queued', 'submitting', 'running'].includes(task.status)
            ? {
                ...task,
                phase: '查询重试中',
                error: typeof data.error === 'string' ? data.error : task.error,
              }
            : task
        )));
      }
      if (nextStatus === 'failed' || nextStatus === 'done') {
        setGenerationTasks((prev) => prev.map((task) => (
          task.node_id === nodeId
          && ['queued', 'submitting', 'running'].includes(task.status)
            ? {
                ...task,
                status: nextStatus === 'done' ? 'succeeded' : 'failed',
                phase: typeof data.providerTaskPhase === 'string'
                  ? data.providerTaskPhase
                  : task.phase,
                error: nextStatus === 'failed'
                  ? String(data.error || task.error || '生成失败')
                  : '',
                finished_at: typeof data.generationFinishedAt === 'string'
                  ? data.generationFinishedAt
                  : task.finished_at,
              }
            : task
        )));
        refreshAfterTerminalEvent();
      }
    };

    const open = () => {
      if (disposed) return;
      source = new EventSource(projectEventsUrl(session));
      source.onopen = () => { if (!disposed) setEventsConnected(true); };
      source.onerror = () => { if (!disposed) setEventsConnected(false); };
      source.addEventListener('media', (event) => applyEvent((event as MessageEvent<string>).data));
      source.addEventListener('media.progress', (event) => applyEvent((event as MessageEvent<string>).data));
    };

    open();
    return () => {
      disposed = true;
      if (terminalRefreshTimer !== null) {
        window.clearTimeout(terminalRefreshTimer);
        terminalRefreshTimer = null;
      }
      setEventsConnected(false);
      source?.close();
      source = null;
    };
  }, [session, currentEp]);

  // 用户手动连线：把来源媒体节点写进目标节点的 params.refs / voiceRef，
  // 让连线真正参与生成（生成时还会再次扫描边，但此处让弹窗即时反映连接）
  const linkRef = useCallback((targetId: string, src: CanvasMediaNode) => {
    setMediaNodes((prev) => {
      let linkedNode: CanvasMediaNode | null = null;
      const next = prev.map((n) => {
        if (n.id !== targetId) return n;
        // ★ label 必须写**真实节点名**（src.title），不能是 src.kind 泛称。
        //   后端 _generate 会把它拼进喂给模型的 prompt，泛称 = 模型分不清谁是谁。
        const ref = {nodeId: src.id, label: (src.title || '').trim() || src.kind, url: src.mediaUrl};
        let params = {...(n.params || {})};
        if (n.kind === 'video') {
          if (src.kind === 'image') {
            params.refs = [...(params.refs || []).filter((r) => r.nodeId !== src.id), ref];
          } else if (src.kind === 'audio') {
            // ★★ 第 17 轮修（用户原话：「明明连线的是 4 个节点，为什么引用资产只显示一个」）：
            //   旧写法 `params.voiceRef = ref` 是**整体覆盖** —— 连第 2、3、4 个音频节点时，
            //   前一个被直接顶掉，最终只剩最后一个。界面上就只剩 1 个音色方块。
            //   而一段戏里本来就有多个角色音色（钱万金、陈大强各说各的），单值从设计上就不够。
            //   现在改为**追加去重**（同一节点连两次不会重复），顺序 = 连线先后。
            params = withVoiceRefs(params, [...voiceRefList(params), ref]);
          } else if (src.kind === 'text' || src.kind === 'video') {
            // ★★ 第 17 轮补：文本与视频引用此前**完全被丢弃**。
            //   用户原话「图片和音色引用显示了，那文本和视频没有引用显示」。
            //   根因就是这里的分支只写了 image / audio 两种，text / video 落到 else 里什么都不做，
            //   所以连了也看不见。文本（剧本设定）和前段视频（续接动作）同样是喂给模型的素材，
            //   必须一并进 refs。
            params.refs = [...(params.refs || []).filter((r) => r.nodeId !== src.id), ref];
          }
        } else if (n.kind === 'audio' && src.kind === 'image') {
          // 音频卡被连角色图 = 该音色的参考图，同样走多值，不覆盖
          params = withVoiceRefs(params, [...voiceRefList(params), ref]);
        } else if (n.kind === 'image' && (src.kind === 'text' || src.kind === 'video' || src.kind === 'image')) {
          // 图片卡被连文本/视频/图片：都是有效的参考来源（图生图、参考构图/风格）
          params.refs = [...(params.refs || []).filter((r) => r.nodeId !== src.id), ref];
        } else if (n.kind === 'text' && src.kind !== 'audio') {
          // 文本卡被连其它任何非音频节点：作为上下文参考
          params.refs = [...(params.refs || []).filter((r) => r.nodeId !== src.id), ref];
        }
        linkedNode = {...n, params};
        return linkedNode;
      });
      if (linkedNode) persistMediaNodes(next, [linkedNode]);
      return next;
    });
  }, [persistMediaNodes]);

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target) return;
    // 自环没有意义，连到自己会被立即拒绝（isValidConnection 已视觉阻止，这里兜底）
    if (connection.source === connection.target) { setToast('不能连到自己'); return; }
    // 去重：同一对节点（含反向）已有手动线就不再叠第二条——叠线看不出来却会重复写 refs
    const dup = userEdgesRef.current.some((e) =>
      (e.source === connection.source && e.target === connection.target) ||
      (e.source === connection.target && e.target === connection.source));
    if (dup) { setToast('这两个节点之间已有连线'); return; }
    pushHistory(userEdgesRef.current, notesRef.current);
    // 自增序列 id：Date.now()+length 在同毫秒同长度下会碰撞（删一条再加一条即复现）
    const id = nextUserEdgeId();
    setUserEdges((prev) => [...prev, {...connection, id, className: 'canvas-edge-user', type: 'deletable', data: {onDelete: deleteEdgeById}}]);
    // 媒体→媒体 连线：同步写入目标节点的引用参数（让连线不只是装饰）
    const sId = (connection.source || '').replace(/^media-/, '');
    const tId = (connection.target || '').replace(/^media-/, '');
    const src = mediaNodesRef.current.find((n) => n.id === sId);
    const tgt = mediaNodesRef.current.find((n) => n.id === tId);
    if (src && tgt && src.kind !== 'text' && tgt.kind !== 'text') {
      clearRefRemoved(session, tgt.id, src.id);
      linkRef(tgt.id, src);
    }
  }, [pushHistory, deleteEdgeById, linkRef, session, setToast]);

  // 改写连线：把已有连线的端点拖到另一个节点上（不必删掉重连）。
  // 只处理手动线（e-user-）：自动线是由素材引用关系派生出来的，改接它没意义且下次重建会被覆盖。
  const onReconnect = useCallback((oldEdge: Edge, newConnection: Connection) => {
    if (!newConnection.source || !newConnection.target) return;
    if (newConnection.source === newConnection.target) { setToast('不能连到自己'); return; }
    const id = oldEdge.id;
    if (!id.startsWith('e-user-')) { setToast('自动连线由素材引用关系生成，不能直接改接'); return; }
    // 去重判断放在 updater 之外读 ref：setToast 是副作用，写在 updater 里会被 React 重复执行两次
    const dup = userEdgesRef.current.some((e) => e.id !== id && (
      (e.source === newConnection.source && e.target === newConnection.target) ||
      (e.source === newConnection.target && e.target === newConnection.source)));
    if (dup) { setToast('这两个节点之间已有连线'); return; }
    pushHistory(userEdgesRef.current, notesRef.current);
    const oldSourceId = oldEdge.source.startsWith(MEDIA_PREFIX) ? oldEdge.source.slice(MEDIA_PREFIX.length) : '';
    const oldTargetId = oldEdge.target.startsWith(MEDIA_PREFIX) ? oldEdge.target.slice(MEDIA_PREFIX.length) : '';
    if (oldSourceId && oldTargetId) {
      dropRefsForPairs([{src: oldSourceId, tgt: oldTargetId}]);
    }
    setUserEdges((prev) => prev.map((e) => (e.id === id
      ? {
        ...e,
        source: newConnection.source as string,
        target: newConnection.target as string,
        sourceHandle: newConnection.sourceHandle ?? e.sourceHandle,
        targetHandle: newConnection.targetHandle ?? e.targetHandle,
      }
      : e)));
    const newSourceId = newConnection.source.slice(MEDIA_PREFIX.length);
    const newTargetId = newConnection.target.slice(MEDIA_PREFIX.length);
    const newSource = mediaNodesRef.current.find((node) => node.id === newSourceId);
    const newTarget = mediaNodesRef.current.find((node) => node.id === newTargetId);
    if (newSource && newTarget && newSource.kind !== 'text' && newTarget.kind !== 'text') {
      clearRefRemoved(session, newTarget.id, newSource.id);
      linkRef(newTarget.id, newSource);
    }
  }, [dropRefsForPairs, linkRef, pushHistory, session, setToast]);

  // 批量删除选中：一次入栈，媒体节点逐个走落库删除（避免刷新后复活）
  const deleteSelectedNodes = useCallback(() => {
    const sel = rf.getNodes().filter((n) => n.selected);
    const noteIds = sel.filter((n) => n.id.startsWith(NOTE_PREFIX)).map((n) => n.id.slice(NOTE_PREFIX.length));
    const mediaIds = sel.filter((n) => n.id.startsWith(MEDIA_PREFIX)).map((n) => n.id.slice(MEDIA_PREFIX.length));
    const total = noteIds.length + mediaIds.length;
    if (total === 0) return 0;
    pushHistory(userEdgesRef.current, notesRef.current);
    if (noteIds.length > 0) {
      const drop = new Set(noteIds);
      setNotes((prev) => {
        const next = prev.filter((n) => !drop.has(n.id));
        persistNotes(next);
        return next;
      });
    }
    if (mediaIds.length > 0) {
      const drop = new Set(mediaIds);
      setMediaNodes((prev) => {
        return prev.filter((n) => !drop.has(n.id));
      });
      mediaIds.forEach((id) => {
        void deleteMediaNodePersistently(id).catch(() => {});
        const mediaId = MEDIA_PREFIX + id;
        setUserEdges((prev) => prev.filter((edge) => edge.source !== mediaId && edge.target !== mediaId));
      });
    }
    const deletedFlowIds = new Set(sel.map((node) => node.id));
    setUserEdges((prev) => prev.filter(
      (edge) => !deletedFlowIds.has(edge.source) && !deletedFlowIds.has(edge.target),
    ));
    return total;
  }, [deleteMediaNodePersistently, rf, pushHistory, persistNotes, persistMediaNodes]);

  const addNote = useCallback((x: number, y: number, text = '') => {
    const note: CanvasNote = {id: `n-${Date.now()}`, text, x, y, updatedAt: new Date().toISOString()};
    pushHistory(userEdgesRef.current, notesRef.current);
    setNotes((prev) => {
      const next = [...prev, note];
      persistNotes(next);
      return next;
    });
    setContextMenu(null);
  }, [persistNotes, pushHistory]);

  const userEdgesRef = useRef<Edge[]>([]);
  useEffect(() => {
    userEdgesRef.current = userEdges;
  }, [userEdges]);
  useEffect(() => () => {
    flushPendingMediaDeletes();
  }, [flushPendingMediaDeletes]);

  // 用户连线 / 删除的自动连线变化 → 写入持久化布局（首次布局恢复完成前禁止写回，避免空状态覆盖历史布局）
  useEffect(() => {
    if (!didInitialLayoutRef.current) return;
    persistLayout(positionRef.current, userEdges, deletedAutoEdgeIds);
  }, [userEdges, deletedAutoEdgeIds, persistLayout]);

  const flushConnectionSave = useCallback(() => {
    if (connectionSaveInFlightRef.current) return;
    const connections = pendingConnectionsRef.current;
    if (!connections) return;
    pendingConnectionsRef.current = null;
    connectionSaveInFlightRef.current = true;
    beginSave();
    void saveCanvasConnections(session, connections, currentEp)
      .then(() => {
        finishSave(true);
        persistLayout(
          positionRef.current,
          userEdgesRef.current,
          deletedAutoRef.current,
          {connectionSynced: true},
        );
        syncChannelRef.current?.postMessage({
          type: 'canvas-connections-updated',
          sessionId: session,
          sourceId: syncInstanceIdRef.current,
          at: Date.now(),
        });
      })
      .catch((error) => {
        finishSave(false);
        setSaveError(error instanceof Error ? error.message : String(error));
      })
      .finally(() => {
        connectionSaveInFlightRef.current = false;
        if (pendingConnectionsRef.current) {
          connectionSaveTimerRef.current = setTimeout(flushConnectionSave, 80);
        }
      });
  }, [beginSave, currentEp, finishSave, persistLayout, session]);

  useEffect(() => {
    if (!didInitialLayoutRef.current) return;
    if (connectionSaveTimerRef.current) clearTimeout(connectionSaveTimerRef.current);
    pendingConnectionsRef.current = userEdges.flatMap((edge) => {
      if (!edge.source.startsWith(MEDIA_PREFIX) || !edge.target.startsWith(MEDIA_PREFIX)) return [];
      return [{
        id: edge.id,
        fromNodeId: edge.source.slice(MEDIA_PREFIX.length),
        toNodeId: edge.target.slice(MEDIA_PREFIX.length),
      }];
    });
    connectionSaveTimerRef.current = setTimeout(() => {
      connectionSaveTimerRef.current = null;
      flushConnectionSave();
    }, 260);
    return () => {
      if (connectionSaveTimerRef.current) clearTimeout(connectionSaveTimerRef.current);
    };
  }, [flushConnectionSave, userEdges]);

  const updateNoteText = useCallback((id: string, text: string) => {
    setNotes((prev) => prev.map((note) => (note.id === id ? {...note, text, updatedAt: new Date().toISOString()} : note)));
    persistNotes(notesRef.current.map((note) => (note.id === id ? {...note, text} : note)));
  }, [persistNotes]);

  const duplicateNote = useCallback((note: CanvasNote) => {
    addNote(note.x + 36, note.y + 36, note.text);
  }, [addNote]);

  // ===== 节点层级：置顶 / 置底 =====
  // zIndex 存在 zOrder map 里（nodeId → 数值），通过 flowNodes 写到 node.style.zIndex。
  // 置顶 = 分配一个比当前所有层级都大的值；置底 = 比所有层级都小的值。
  const setNodeZOrder = useCallback((nodeIds: string[], mode: 'front' | 'back') => {
    if (nodeIds.length === 0) return;
    setZOrder((prev) => {
      const values = Object.values(prev);
      const next = {...prev};
      if (mode === 'front') {
        // 基准取当前最大值（没有层级数据时从 1000 起，避免与 RF 的 internals.z 冲突）
        let base = values.length ? Math.max(...values) : 1000;
        for (const id of nodeIds) {
          base += 1;
          next[id] = base;
        }
      } else {
        let base = values.length ? Math.min(...values) : 1000;
        for (const id of nodeIds) {
          base -= 1;
          next[id] = base;
        }
      }
      return next;
    });
  }, []);

  // ===== 多选编辑增益：复制/粘贴/对齐/分布/微调 =====
  // 节点 id 前缀：媒体卡 media-<mnId>、备注 note-<noteId>（与 flowNodes 里的命名一致）
  // 前缀常量已提升到模块级（NODE_ID_PREFIX），这里保留引用名以兼容既有代码
  const COPY_STEP = 36; // 复制/粘贴的阶梯偏移，避免新节点与原节点完全重叠

  // 画布真实选中节点（含框选产生的多选），只读 views；位置/尺寸取自 RF 内部 nodes，
  // 比 nodesRef（只存 {id,type}）信息更全，几何运算（对齐/分布）需要真实宽高。
  const selectedFlowNodes = useCallback(
    () => rf.getNodes().filter((n) => n.selected && (n.id.startsWith(NOTE_PREFIX) || n.id.startsWith(MEDIA_PREFIX))),
    [rf],
  );

  const selectedMediaList = useCallback(() => {
    const ids = selectedFlowNodes().filter((n) => n.id.startsWith(MEDIA_PREFIX)).map((n) => n.id.slice(MEDIA_PREFIX.length));
    return mediaNodesRef.current.filter((mn) => ids.includes(mn.id));
  }, [selectedFlowNodes]);

  const selectedNoteList = useCallback(() => {
    const ids = selectedFlowNodes().filter((n) => n.id.startsWith(NOTE_PREFIX)).map((n) => n.id.slice(NOTE_PREFIX.length));
    return notesRef.current.filter((nn) => ids.includes(nn.id));
  }, [selectedFlowNodes]);

  // freeSpot 已上移到 uploadRefFile 之前（避免 TDZ），此处不再重复定义。

  // 新增节点落点：视口中心 + 上述避让。
  // 旧实现写的是 rf.getViewport().x - 60：viewport.x 是**平移量**不是画布世界坐标，
  // 缩放不等于 1 时算出来的点在视口之外（新节点常落在画布外看不见）；且连点两次完全重叠。
  const newNodeSpot = useCallback(() => {
    const el = document.querySelector('.react-flow');
    if (!el) return {x: 0, y: 0};
    const rect = el.getBoundingClientRect();
    const center = rf.screenToFlowPosition({x: rect.left + rect.width / 2, y: rect.top + rect.height / 2});
    return freeSpot(center.x - 40, center.y - 20);
  }, [rf, freeSpot]);

  // 把一组节点快照按偏移量追加进画布（复制粘贴与 Ctrl+D 共用）
  // edgePairs：被复制节点之间的连线（source/target 为原始媒体/备注 id，不是 RF 节点 id）。
  // 粘贴时按 id 映射表重建到新节点上，做到「复制节点连同连线一起复制」。
  const appendNodeCopies = useCallback((
    media: CanvasMediaNode[],
    noteList: CanvasNote[],
    dx: number,
    dy: number,
    edgePairs: {source: string; target: string}[] = [],
  ) => {
    const count = media.length + noteList.length;
    if (count === 0) return 0;
    pushHistory(userEdgesRef.current, notesRef.current, {removeAddedMedia: true});
    const stamp = Date.now();
    // 原始 id → 新 id 的映射（媒体 id 与备注 id 用 kind 区分，避免同 id 冲突）
    const idMap = new Map<string, string>();
    const kindMap = new Map<string, 'media' | 'note'>();
    const newMedia = media.map((mn, i) => {
      const newId = `m-copy-${stamp}-${i}`;
      idMap.set(mn.id, newId);
      kindMap.set(mn.id, 'media');
      return {...mn, id: newId, x: mn.x + dx, y: mn.y + dy, updatedAt: new Date().toISOString()};
    });
    const newNotes = noteList.map((nn, i) => {
      const newId = `n-copy-${stamp}-${i}`;
      idMap.set(nn.id, newId);
      kindMap.set(nn.id, 'note');
      return {...nn, id: newId, x: nn.x + dx, y: nn.y + dy, updatedAt: new Date().toISOString()};
    });
    const remappedMedia = newMedia.map((mn) => {
      const params = mn.params || {};
      let changed = false;
      const nextParams = {...params};
      if (Array.isArray(params.refs)) {
        const mapped = params.refs.map((ref) => {
          const nid = idMap.get(ref.nodeId);
          if (!nid) return ref;
          changed = true;
          return {...ref, nodeId: nid};
        });
        if (changed) nextParams.refs = mapped;
      }
      const voices = voiceRefList(params);
      if (voices.length) {
        const remapped = voices.map((voice) => {
          const nid = idMap.get(voice.nodeId);
          if (!nid || nid === voice.nodeId) return voice;
          changed = true;
          return {...voice, nodeId: nid};
        });
        if (changed) {
          const kept = withVoiceRefs(nextParams, remapped);
          if (kept.voiceRefs) nextParams.voiceRefs = kept.voiceRefs; else delete nextParams.voiceRefs;
          if (kept.voiceRef) nextParams.voiceRef = kept.voiceRef; else delete nextParams.voiceRef;
        }
      }
      return changed ? {...mn, params: nextParams} : mn;
    });
    if (media.length > 0) {
      setMediaNodes((prev) => {
        const next = [...prev, ...remappedMedia];
        // Collection changes must persist only the new cards. Re-submitting every
        // existing card makes an unrelated stale position reject the whole copy.
        persistMediaNodes(next, remappedMedia);
        return next;
      });
    }
    if (noteList.length > 0) {
      setNotes((prev) => {
        const next = [...prev, ...newNotes];
        persistNotes(next);
        return next;
      });
    }
    // 重建节点之间的连线：把 source/target 换成新节点的 RF 节点 id
    if (edgePairs.length > 0) {
      const toFlowId = (encoded: string) => {
        const sep = encoded.indexOf(':');
        if (sep < 0) return null;
        const kind = encoded.slice(0, sep) as 'media' | 'note';
        const rawId = encoded.slice(sep + 1);
        if (kindMap.get(rawId) !== kind) return null;
        const newId = idMap.get(rawId);
        if (!newId) return null;
        return kind === 'media' ? `media-${newId}` : `note-${newId}`;
      };
      const newEdges: Edge[] = [];
      for (const pair of edgePairs) {
        const s = toFlowId(pair.source);
        const t = toFlowId(pair.target);
        if (!s || !t || s === t) continue;
        newEdges.push({
          id: nextUserEdgeId(),
          source: s,
          target: t,
          className: 'canvas-edge-user',
          type: 'deletable',
          data: {onDelete: deleteEdgeById},
        });
      }
      if (newEdges.length > 0) {
        setUserEdges((prev) => [...prev, ...newEdges]);
      }
    }
    const copiedFlowIds = [
      ...remappedMedia.map((node) => `${MEDIA_PREFIX}${node.id}`),
      ...newNotes.map((note) => `${NOTE_PREFIX}${note.id}`),
    ];
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
      const wanted = new Set(copiedFlowIds);
      setNodes((prev) => prev.map((node) => (
        wanted.has(node.id) ? {...node, selected: true} : {...node, selected: false}
      )));
      rf.fitView({
        nodes: copiedFlowIds.map((id) => ({id})),
        padding: 0.35,
        duration: 260,
      });
    }));
    return count;
  }, [pushHistory, persistMediaNodes, persistNotes, rf, setNodes]);

  // Ctrl+D：就地复制选中（不污染剪贴板）
  const duplicateSelectedNodes = useCallback(
    () => appendNodeCopies(selectedMediaList(), selectedNoteList(), COPY_STEP, COPY_STEP),
    [appendNodeCopies, selectedMediaList, selectedNoteList],
  );

  // ── 批量调参：把弹窗里勾选的字段写到一批图片/视频节点上 ──────────────
  //
  // 三条硬约束：
  //  ① **只写 params 与派生的 size**，不动 prompt / refs / position；
  //  ② 每个字段都要按**目标节点自身的模型 schema** 校验 ——
  //     不支持的取值**不写**（保留用户原值），并把跳过的卡片回传给 UI 提示。
  //     ★ 绝不能"退回默认值"：实测会让 Seedance 2.0 mini 的卡从 480P 被静默改成 480P
  //       或无提示地忽略 1080P 请求，用户以为生效了、实际没有；
  //  ③ 图片节点的 size 必须**跟着 clarity × ratio 重算**，否则卡片比例变了、
  //     后端实际出图尺寸还是旧值（库里 size 才是真正的出图尺寸）。
  const applyBatchParams = useCallback((
    patch: BatchParamsPatch,
    scope: BatchScope,
    targets: CanvasMediaNode[],
  ) => {
    const pickedIds = new Set(
      targets
        .filter((n) => (n.kind === 'image' ? scope.image
          : n.kind === 'video' ? scope.video
            : n.kind === 'audio' ? scope.audio
              : n.kind === 'text' ? scope.text
                : false))
        .map((n) => n.id),
    );
    if (pickedIds.size === 0) return {changed: 0, skipped: [] as {title: string; reason: string}[]};
    // 模型按类型分别下发：图片卡只吃 modelImage、视频卡只吃 modelVideo
    let changed = 0;
    const changedNodes: CanvasMediaNode[] = [];
    // 收集"因当前模型不支持而跳过"的卡片，交给 UI 明确告知（不能静默失败）
    const skipped: {title: string; reason: string}[] = [];
    setMediaNodes((prev) => {
      const next = prev.map((mn) => {
        if (!pickedIds.has(mn.id)) return mn;
        const cur = mn.params || {};
        const merged: CanvasMediaParams = {...cur};
        let touched = false;

        // ① 模型：**按卡片类型取对应的那个字段**，并补齐 provider。
        //    ★ 上一版只有一个 patch.model，靠"类型不匹配就跳过"兜底 ——
        //      结果是混选时永远有一类改不动，且界面上看不出为什么。
        //      现在图片走 modelImage、视频走 modelVideo，各写各的，不互相牵连。
        const modelPatch = mn.kind === 'image' ? patch.modelImage
          : mn.kind === 'video' ? patch.modelVideo
            : mn.kind === 'audio' ? patch.modelAudio
              : mn.kind === 'text' ? patch.modelText
                : undefined;
        if (modelPatch) {
          const spec = (modelCatalog?.models || []).find((m) => m.id === modelPatch.id);
          // 双保险：即便外部传错，也绝不把视频模型写到图片卡上（反之亦然）
          if (!spec || spec.capability === mn.kind) {
            merged.model = modelPatch.id;
            merged.provider = modelPatch.providerId;
            // 音频还有一个 ttsModel 字段（后端读它去调 dashscope）
            if (mn.kind === 'audio') merged.ttsModel = spec?.remoteModel || modelPatch.remoteModel || merged.ttsModel;
            touched = true;
          } else {
            const kindName = mn.kind === 'image' ? '图片' : mn.kind === 'video' ? '视频' : mn.kind === 'audio' ? '音频' : '文本';
            skipped.push({title: mn.title, reason: `${spec.label} 不是${kindName}模型`});
          }
        }

        // ② 比例 / 清晰度：先按该节点当前模型（或新模型）的 schema 收敛
        const specFor = (() => {
          const mid = merged.model || cur.model;
          return (modelCatalog?.models || []).find((m) => m.id === mid)
            || (modelCatalog?.models || []).find((m) => m.enabled !== false && m.capability === mn.kind);
        })();
        /**
         * 校验取值是否被该节点的模型支持。
         *
         * ★★ 这里曾经写成"不合法就退回默认值"，实测出真问题：
         *    9 个视频里 3 个用的是 Seedance 2.0 mini（只支持 480P/720P），
         *    批量选 1080P 时它们被**静默改成 480P** —— 用户明明选了 1080P，
         *    结果有些卡变成 480P 还毫无提示，是比"没生效"更糟的体验。
         *    现在改为：**不支持就不写这个字段**（保留用户原来的值），
         *    并把"哪些卡不支持"统计出来回传给调用方，由 UI 明确告知。
         */
        const supports = (key: 'ratio' | 'clarity' | 'resolution', want: string, fallback: string[]): boolean => {
          const opts = specFor?.params?.[key]?.options;
          const list = Array.isArray(opts) && opts.length ? opts.map(String) : fallback;
          return list.includes(want);
        };

        if (patch.ratio) {
          if (supports('ratio', patch.ratio, RATIOS)) {
            merged.ratio = patch.ratio;
            if (mn.kind === 'video') merged.size = patch.ratio; // 视频的 size 字段历史上存的是比例
            touched = true;
          } else {
            skipped.push({title: mn.title, reason: `不支持比例 ${patch.ratio}`});
          }
        }
        if (patch.clarityImage || patch.clarityVideo) {
          if (mn.kind === 'video' && patch.clarityVideo) {
            if (supports('resolution', patch.clarityVideo, RESOLUTIONS)) {
              merged.resolution = patch.clarityVideo;
              touched = true;
            } else {
              skipped.push({title: mn.title, reason: `不支持 ${patch.clarityVideo}`});
            }
          } else if (mn.kind === 'image' && patch.clarityImage) {
            if (supports('clarity', patch.clarityImage, CLARITIES)) {
              merged.clarity = patch.clarityImage;
              touched = true;
            } else {
              skipped.push({title: mn.title, reason: `不支持 ${patch.clarityImage}`});
            }
          }
        }
        // 图片：clarity × ratio → size（出图尺寸真源）
        if (mn.kind === 'image') {
          const cl = String(merged.clarity || '1.5K');
          const ra = String(merged.ratio || '1:1');
          merged.size = CLARITY_SIZES[cl]?.[ra] || merged.size;
        }
        /**
         * ④ 音频三件套（第 33 轮）。
         * ★ 音频**没有** ratio / clarity / resolution，那三个字段对它是无意义的；
         *   它真正能让用户批量设的是：输出格式、采样率、合成模式。
         * ★ 采样率在 schema 里是字符串选项（"24000"），落地必须转成 number，
         *   否则后端 pydantic 校验会因为类型不符直接 400。
         */
        if (mn.kind === 'audio') {
          if (patch.audioFormat) { merged.outputFormat = patch.audioFormat; touched = true; }
          if (patch.audioSampleRate) {
            const n = Number(patch.audioSampleRate);
            if (Number.isFinite(n) && n > 0) { merged.sampleRate = n; touched = true; }
          }
          if (patch.audioMode) { merged.ttsMode = patch.audioMode === 'speech' ? 'speech' : 'design'; touched = true; }
        }
        /**
         * ⑤ 文本两件套。
         * ★ 文本**没有** ratio / clarity / resolution / 采样率，那几项对它是无意义的；
         *   它真正能批量设的是「生成长度」和「采样温度」。
         * ★ 两个值都要做范围校验：maxTokens 必须是正整数（后端 pydantic 会拒 0 与负数），
         *   temperature 限制在 0~2（超出会被供应商 400）。
         *   schema 里若声明了 max/min，以 schema 为准 —— 不同文本模型的上下文窗口不同。
         */
        if (mn.kind === 'text') {
          if (patch.textMaxTokens != null) {
            const raw = Number(patch.textMaxTokens);
            const lo = Number(specFor?.params?.maxTokens?.min ?? 1);
            const hi = Number(specFor?.params?.maxTokens?.max ?? Number.MAX_SAFE_INTEGER);
            if (Number.isFinite(raw) && raw >= lo && raw <= hi) {
              merged.maxTokens = Math.round(raw);
              touched = true;
            } else {
              skipped.push({title: mn.title, reason: `长度 ${raw} 超出该模型范围`});
            }
          }
          if (patch.textTemperature != null) {
            const raw = Number(patch.textTemperature);
            if (Number.isFinite(raw) && raw >= 0 && raw <= 2) {
              merged.temperature = raw;
              touched = true;
            } else {
              skipped.push({title: mn.title, reason: `温度 ${raw} 超出 0~2`});
            }
          }
        }
        /**
         * ③ 换模型后，原有的比例 / 清晰度可能不再被新模型支持。
         *
         * ★ 这是"按类型分别选模型"带来的新风险：把 12 张图统一换成另一个图片模型后，
         *   若新模型不支持某张卡原来的档位，卡片上就会同时挂着"新模型 + 它不认的档位"，
         *   下次生成直接 400。只在**本次确实写了新模型**时才收敛，且只收敛到
         *   新模型自己的默认值 —— 没换模型的卡一律不动（不能随手改用户已有设置）。
         */
        if (modelPatch && specFor) {
          const ratioOpts = specFor.params?.ratio?.options;
          const ratioList = Array.isArray(ratioOpts) && ratioOpts.length ? ratioOpts.map(String) : null;
          const defRatio = String(specFor.defaultParams?.ratio || '');
          if (ratioList && merged.ratio && !ratioList.includes(String(merged.ratio)) && ratioList.includes(defRatio)) {
            merged.ratio = defRatio;
            if (mn.kind === 'video') merged.size = defRatio;
            if (mn.kind === 'image' && !patch.clarityImage) merged.size = CLARITY_SIZES[String(merged.clarity || '')]?.[defRatio] || merged.size;
          }
          if (mn.kind === 'video') {
            const resOpts = specFor.params?.resolution?.options;
            const resList = Array.isArray(resOpts) && resOpts.length ? resOpts.map(String) : null;
            const defRes = String(specFor.defaultParams?.resolution || '');
            if (!patch.clarityVideo && resList && merged.resolution
              && !resList.includes(String(merged.resolution)) && resList.includes(defRes)) {
              merged.resolution = defRes;
            }
          } else if (mn.kind === 'image') {
            const clOpts = specFor.params?.clarity?.options;
            const clList = Array.isArray(clOpts) && clOpts.length ? clOpts.map(String) : null;
            const defCl = String(specFor.defaultParams?.clarity || '');
            if (!patch.clarityImage && clList && merged.clarity
              && !clList.includes(String(merged.clarity)) && clList.includes(defCl)) {
              merged.clarity = defCl;
              merged.size = CLARITY_SIZES[defCl]?.[String(merged.ratio || '1:1')] || merged.size;
            }
          }
        }
        if (!touched) return mn;
        changed += 1;
        const updated = {...mn, params: merged, updatedAt: new Date().toISOString()};
        changedNodes.push(updated);
        return updated;
      });
      if (changed > 0) {
        mediaNodesRef.current = next;
        persistMediaNodes(next, changedNodes);
      }
      return next;
    });
    return {changed, skipped};
  }, [modelCatalog, persistMediaNodes]);

  // ── 批量下载已生成的素材 ─────────────────────────────────────────
  //
  // 为什么不逐个 createElement('a').click()：
  //   浏览器对"同一 tick 内多次触发下载"会做节流，实测 9 个只下到 3~4 个。
  //   必须串行（await 之间留出间隔），并在每张之间 await 一次真正的 fetch，
  //   让浏览器有机会处理上一轮的下载许可。
  //
  // 素材来源两处：/uploads/xxx（本项目上传/生成的本地文件，直接 a[download] 即可）
  // 与 http(s)（供应商返回的外链，可能已过期或跨域）。跨域链接 a[download] 会被忽略、
  // 变成"打开新标签"，所以统一先 fetch 成 blob 再下载；fetch 失败就退回打开原地址，
  // 至少不让用户空手而归。
  const downloadMediaBatch = useCallback(async (
    targets: CanvasMediaNode[],
    options?: {allVersions?: boolean},
  ) => {
    const items = targets.flatMap((node) => {
      if (node.kind === 'text') return [];
      const versions = mediaVersionList(node);
      if (options?.allVersions && versions.length > 0) {
        return versions.map((version, versionIndex) => ({
          node,
          url: String(version.url || ''),
          versionIndex,
          versionTotal: versions.length,
        }));
      }
      const currentIndex = versions.findIndex((version) => version.url === node.mediaUrl);
      return node.mediaUrl ? [{
        node,
        url: node.mediaUrl,
        versionIndex: currentIndex >= 0 ? currentIndex : Math.max(0, Number(node.params?.mediaVersionIndex ?? node.params?.videoVersionIndex ?? versions.length - 1)),
        versionTotal: versions.length,
      }] : [];
    }).filter((item) => !!item.url);
    if (items.length === 0) { setToast('选中的卡片里没有已生成的素材'); return; }
    setToast(`正在下载 ${items.length} 个素材…`);
    const safeName = (s: string) => (s || '素材')
      // ★ 去掉已存在的扩展名再统一补：素材标题常常本身就是 "1551f07a.png"，
      //   直接拼 extOf 会得到 "1551f07a.png.png"（实测下载出来的就是这种双后缀）。
      .replace(/\.(png|jpe?g|webp|gif|mp4|webm|mov|m4v|mp3|wav|m4a)$/i, '')
      .replace(/[\\/:*?"<>|\r\n]+/g, '_')
      .slice(0, 60);
    const extOf = (url: string, kind: CanvasMediaNodeKind): string => {
      const m = /\.(png|jpe?g|webp|gif|mp4|webm|mov|m4v|mp3|wav|m4a)(?:[?#]|$)/i.exec(url);
      if (m) return `.${m[1].toLowerCase().replace('jpeg', 'jpg')}`;
      return kind === 'video' ? '.mp4' : kind === 'audio' ? '.mp3' : '.png';
    };
    let ok = 0;
    for (let i = 0; i < items.length; i += 1) {
      const item = items[i];
      const n = item.node;
      const url = item.url;
      const projectName = canvas?.meta.episodes?.[0]?.title || '项目';
      const episodeName = currentEp ? fmtEp(currentEp) : '整剧';
      const versionName = item.versionTotal > 1
        ? `_版本${item.versionIndex + 1}_共${item.versionTotal}版`
        : '';
      const filename = `${safeName(projectName)}_${safeName(episodeName)}_${safeName(n.title)}${versionName}${extOf(url, n.kind)}`;
      let done = false;
      try {
        if (url.startsWith('data:')) {
          // data URL 直接走 a[download]
          const a = document.createElement('a');
          a.href = url; a.download = filename; a.click();
          done = true;
        } else {
          // 同源（/uploads/…）与跨域统一走 blob，行为一致且能真正拿到文件名
          const res = await fetch(url, {credentials: 'include'});
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const blob = await res.blob();
          const objUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = objUrl; a.download = filename; a.click();
          // 立刻 revoke 会让部分浏览器中断下载，延后一拍
          window.setTimeout(() => URL.revokeObjectURL(objUrl), 4000);
          done = true;
        }
      } catch {
        // 兜底：至少把原地址打开，用户可自行另存
        window.open(url, '_blank', 'noopener');
      }
      if (done) ok += 1;
      // 串行间隔：给浏览器处理上一轮下载的时间（20 个以上时肉眼可见地必要）
      if (i < items.length - 1) await new Promise((r) => window.setTimeout(r, 260));
    }
    setToast(ok === items.length ? `已下载 ${ok} 个素材` : `已下载 ${ok} / ${items.length} 个（其余已打开原地址）`);
  }, [canvas, currentEp, fmtEp, setToast]);

  // 累计粘贴步数：重复 Ctrl+V 逐级偏移，而不是叠在同一点上
  const pasteStepRef = useRef(0);
  // Ctrl+C：捕获选中节点快照 + 选中节点之间的连线
  const copySelectedNodes = useCallback(() => {
    const media = selectedMediaList();
    const noteList = selectedNoteList();
    if (media.length === 0 && noteList.length === 0) return {media: 0, notes: 0};
    // 连线也要一起复制：只取「源与目标都在选中集合内」的边，
    // 否则粘贴后会凭空多出指向未复制节点的悬空线。
    const picked = new Set<string>();
    media.forEach((m) => picked.add(`media-${m.id}`));
    noteList.forEach((n) => picked.add(`note-${n.id}`));
    const edges = userEdgesRef.current
      .filter((e) => picked.has(e.source) && picked.has(e.target))
      .map((e) => ({
        // 用 "kind:id" 编码，避免媒体 id 与备注 id 同名时无法区分前缀
        source: e.source.startsWith('note-') ? `note:${e.source.slice(5)}` : `media:${e.source.slice(6)}`,
        target: e.target.startsWith('note-') ? `note:${e.target.slice(5)}` : `media:${e.target.slice(6)}`,
      }));
    setNodeClipboard({media: media.map((m) => ({...m})), notes: noteList.map((n) => ({...n})), edges});
    pasteStepRef.current = 0;
    return {media: media.length, notes: noteList.length};
  }, [selectedMediaList, selectedNoteList]);
  // Ctrl+V：粘贴节点
  const pasteNodeClipboard = useCallback(() => {
    const clip = nodeClipboard;
    if (!clip || (clip.media.length === 0 && clip.notes.length === 0)) return 0;
    pasteStepRef.current += 1;
    const step = COPY_STEP * pasteStepRef.current;
    return appendNodeCopies(clip.media, clip.notes, step, step, clip.edges);
  }, [nodeClipboard, appendNodeCopies]);

  // 统一写回位置：位置真源是 mediaNodes.x/y 与 notes.x/y（flowNodes 只是派生），
  // 直接改 RF 的 nodes 会被派生重算覆盖回去。
  const applyPositionPatch = useCallback((patchMap: Map<string, {x: number; y: number}>) => {
    if (patchMap.size === 0) return;
    const hasMedia = [...patchMap.keys()].some((id) => id.startsWith(MEDIA_PREFIX));
    const hasNote = [...patchMap.keys()].some((id) => id.startsWith(NOTE_PREFIX));
    if (hasMedia) {
      setMediaNodes((prev) => {
        const next = prev.map((mn) => {
          const pos = patchMap.get(MEDIA_PREFIX + mn.id);
          if (!pos) return mn;
          const seq = ++positionMutationSeqRef.current;
          pendingPositionSavesRef.current.set(mn.id, {x: pos.x, y: pos.y, seq});
          return {...mn, x: pos.x, y: pos.y, updatedAt: new Date().toISOString()};
        });
        persistMediaPositions(next);
        return next;
      });
    }
    if (hasNote) {
      setNotes((prev) => {
        const next = prev.map((nn) => {
          const pos = patchMap.get(NOTE_PREFIX + nn.id);
          return pos ? {...nn, x: pos.x, y: pos.y, updatedAt: new Date().toISOString()} : nn;
        });
        persistNotes(next);
        return next;
      });
    }
  }, [persistMediaNodes, persistMediaPositions, persistNotes]);

  // 对齐 / 分布：基于 RF 实测宽高（measured），缺测量值时退化为左上角对齐
  // scopeAll=true 时才允许作用于全量节点；否则**只**作用于选中节点。
  // 安全阀：绝不因为「没选中」就静默回退成全量操作（早期 sortIntoGrid 就是这么把整张画布重排的）。
  const applyGeometry = useCallback((mode: 'left' | 'right' | 'top' | 'bottom' | 'hcenter' | 'vcenter' | 'distH' | 'distV', scopeAll = false) => {
    const sel = scopeAll
      ? rf.getNodes().filter((n) => n.id.startsWith(NOTE_PREFIX) || n.id.startsWith(MEDIA_PREFIX))
      : selectedFlowNodes();
    if (sel.length < 2) return 0;
    const boxes = sel.map((n) => ({
      id: n.id,
      x: n.position.x,
      y: n.position.y,
      w: n.measured?.width ?? 0,
      h: n.measured?.height ?? 0,
    }));
    const minX = Math.min(...boxes.map((b) => b.x));
    const minY = Math.min(...boxes.map((b) => b.y));
    const maxRight = Math.max(...boxes.map((b) => b.x + b.w));
    const maxBottom = Math.max(...boxes.map((b) => b.y + b.h));
    const centerLineX = (minX + maxRight) / 2;
    const centerLineY = (minY + maxBottom) / 2;
    const patch = new Map<string, {x: number; y: number}>();

    if (mode === 'left' || mode === 'right' || mode === 'top' || mode === 'bottom' || mode === 'hcenter' || mode === 'vcenter') {
      boxes.forEach((b) => {
        let x = b.x;
        let y = b.y;
        if (mode === 'left') x = minX;
        else if (mode === 'right') x = maxRight - b.w;
        else if (mode === 'hcenter') x = centerLineX - b.w / 2;
        else if (mode === 'top') y = minY;
        else if (mode === 'bottom') y = maxBottom - b.h;
        else if (mode === 'vcenter') y = centerLineY - b.h / 2;
        patch.set(b.id, {x: Math.round(x), y: Math.round(y)});
      });
    } else if (mode === 'distH' || mode === 'distV') {
      const horizontal = mode === 'distH';
      const order = [...boxes].sort((a, b) => (horizontal ? a.x - b.x : a.y - b.y));
      const start = horizontal ? order[0].x : order[0].y;
      const last = order[order.length - 1];
      const end = horizontal ? last.x + last.w : last.y + last.h;
      const sumSize = order.reduce((s, b) => s + (horizontal ? b.w : b.h), 0);
      // ★ 这里原来直接用 (end - start - sumSize) / (n-1) 当间距。
      //   当选中卡片的总宽**超过**首尾跨度时（多选 20+ 张卡的常态），分子为负 →
      //   gap 是负数 → 卡片互相压在一起，看起来"等距完全没生效、还叠成一坨"。
      //   实测本项目 26 个节点算出 gap = -89.2（卡片宽 190~293，全挤在一起）。
      //   现在：算出来放不下就放弃"首尾固定"，改为从起点按固定间距顺排，保证永不重叠。
      const raw = (end - start - sumSize) / (order.length - 1);
      const gap = raw >= 0 ? raw : DIST_GAP;
      let cursor = start;
      order.forEach((b) => {
        if (horizontal) patch.set(b.id, {x: Math.round(cursor), y: Math.round(b.y)});
        else patch.set(b.id, {x: Math.round(b.x), y: Math.round(cursor)});
        cursor += (horizontal ? b.w : b.h) + gap;
      });
    }
    if (patch.size === 0) return 0;
    pushHistory(userEdgesRef.current, notesRef.current, {positions: true});
    applyPositionPatch(patch);
    return patch.size;
  }, [rf, selectedFlowNodes, pushHistory, applyPositionPatch]);

  // 按画布节点 id 批量删除（一次入栈历史；媒体节点逐个落库删除，避免刷新后复活）
  const deleteNodesByIds = useCallback((ids: string[]) => {
    const noteIds = ids.filter((id) => id.startsWith(NOTE_PREFIX)).map((id) => id.slice(NOTE_PREFIX.length));
    const mediaIds = ids.filter((id) => id.startsWith(MEDIA_PREFIX)).map((id) => id.slice(MEDIA_PREFIX.length));
    const total = noteIds.length + mediaIds.length;
    if (total === 0) return 0;
    pushHistory(userEdgesRef.current, notesRef.current);
    // Deleted media must be removable from the server but restorable locally by
    // Ctrl+Z. Defer the backend deletion until the next gesture commits; the
    // snapshot itself retains the complete node card.
    cancelPendingMediaDeletes();
    if (noteIds.length > 0) {
      const drop = new Set(noteIds);
      setNotes((prev) => {
        const next = prev.filter((n) => !drop.has(n.id));
        persistNotes(next);
        return next;
      });
    }
    if (mediaIds.length > 0) {
      const drop = new Set(mediaIds);
      const droppedPairs: {src: string; tgt: string}[] = [];
      for (const other of mediaNodesRef.current) {
        if (drop.has(other.id)) continue;
        const refs = [...(other.params?.refs || []), ...voiceRefList(other.params || {})];
        for (const ref of refs) {
          const sourceId = String(ref.nodeId || '');
          if (drop.has(sourceId)) droppedPairs.push({src: sourceId, tgt: other.id});
        }
      }
      if (droppedPairs.length > 0) dropRefsForPairs(droppedPairs);
      setMediaNodes((prev) => {
        return prev.filter((n) => !drop.has(n.id));
      });
      mediaIds.forEach((id) => {
        queueMediaDelete(id);
        const mediaId = MEDIA_PREFIX + id;
        setUserEdges((prev) => prev.filter((edge) => edge.source !== mediaId && edge.target !== mediaId));
      });
    }
    return total;
  }, [deleteMediaNodePersistently, pushHistory, persistNotes, dropRefsForPairs]);

  const cutSelectedNodes = useCallback(() => {
    const copied = copySelectedNodes();
    if (copied.media === 0 && copied.notes === 0) return 0;
    const ids = [
      ...selectedMediaList().map((node) => `${MEDIA_PREFIX}${node.id}`),
      ...selectedNoteList().map((note) => `${NOTE_PREFIX}${note.id}`),
    ];
    return deleteNodesByIds(ids);
  }, [copySelectedNodes, deleteNodesByIds, selectedMediaList, selectedNoteList]);

  // 排序整理：把节点按指定规则排序后重排成网格（用于「整理」弹窗）
  const sortIntoGrid = useCallback((sortKey: 'name' | 'kind' | 'created', cols: number, scopeAll = false) => {
    const all = rf.getNodes().filter((n) => n.id.startsWith(NOTE_PREFIX) || n.id.startsWith(MEDIA_PREFIX));
    // 安全阀：只有显式传了 scopeAll（用户在弹窗里勾了「应用到全部」）才动全量；
    // 否则只排选中节点。早期写法 `sel.length > 0 ? sel : all` 会在没选中时把整张画布重排。
    const list = scopeAll ? all : all.filter((n) => n.selected);
    if (list.length === 0) return 0;
    const labelOf = (n: FlowNode): string => {
      if (n.id.startsWith(NOTE_PREFIX)) {
        const note = notesRef.current.find((x) => x.id === n.id.slice(NOTE_PREFIX.length));
        return (note?.text || '备注').trim() || '备注';
      }
      const mn = mediaNodesRef.current.find((x) => x.id === n.id.slice(MEDIA_PREFIX.length));
      return mn?.title || '节点';
    };
    const kindOf = (n: FlowNode): string => {
      if (n.id.startsWith(NOTE_PREFIX)) return 'note';
      const mn = mediaNodesRef.current.find((x) => x.id === n.id.slice(MEDIA_PREFIX.length));
      return mn?.kind ?? 'media';
    };
    const sorted = [...list].sort((a, b) => {
      if (sortKey === 'name') return labelOf(a).localeCompare(labelOf(b), 'zh-Hans-CN');
      if (sortKey === 'kind') {
        const k = kindOf(a).localeCompare(kindOf(b));
        return k !== 0 ? k : labelOf(a).localeCompare(labelOf(b), 'zh-Hans-CN');
      }
      // created：媒体节点按 id 里的时间戳，备注按 updatedAt；兜底用现坐标
      const ta = a.id.startsWith(MEDIA_PREFIX) ? Number(/m-(\d+)/.exec(a.id.slice(MEDIA_PREFIX.length))?.[1] ?? 0) : 0;
      const tb = b.id.startsWith(MEDIA_PREFIX) ? Number(/m-(\d+)/.exec(b.id.slice(MEDIA_PREFIX.length))?.[1] ?? 0) : 0;
      if (ta !== tb) return ta - tb;
      return a.position.x - b.position.x || a.position.y - b.position.y;
    });
    // 网格原点取当前这批节点的左上角，避免整批跳到画布别处
    const originX = Math.min(...list.map((n) => n.position.x));
    const originY = Math.min(...list.map((n) => n.position.y));
    const CELL_W = 260;
    const CELL_H = 220;
    const patch = new Map<string, {x: number; y: number}>();
    sorted.forEach((n, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      patch.set(n.id, {x: Math.round(originX + col * CELL_W), y: Math.round(originY + row * CELL_H)});
    });
    if (patch.size === 0) return 0;
    pushHistory(userEdgesRef.current, notesRef.current, {positions: true});
    applyPositionPatch(patch);
    return patch.size;
  }, [rf, pushHistory, applyPositionPatch]);

  // 方向键微调：Shift 加速到 10 倍
  const nudgeSelected = useCallback((dx: number, dy: number) => {
    const sel = selectedFlowNodes();
    if (sel.length === 0) return 0;
    const patch = new Map<string, {x: number; y: number}>();
    sel.forEach((n) => patch.set(n.id, {x: Math.round(n.position.x + dx), y: Math.round(n.position.y + dy)}));
    pushHistory(userEdgesRef.current, notesRef.current, {positions: true});
    applyPositionPatch(patch);
    return patch.size;
  }, [selectedFlowNodes, pushHistory, applyPositionPatch]);

  const commitNotePositions = useCallback((dirty: {id: string; position: {x: number; y: number}}[]) => {
    setNotes((prev) => {
      const map = new Map(dirty.map((item) => [item.id, item.position]));
      return prev.map((note) => (map.has(note.id) ? {...note, x: map.get(note.id)!.x, y: map.get(note.id)!.y} : note));
    });
    persistNotes(notesRef.current.map((note) => {
      const pos = dirty.find((item) => item.id === note.id);
      return pos ? {...note, x: pos.position.x, y: pos.position.y} : note;
    }));
  }, [persistNotes]);

  // 子卡片（资产卡 / 分段卡）的位置是按索引派生的（见 flowNodes 末尾 placed 分支），
  // 不支持单独摆放；直接拖子卡会每帧被派生位置拽回，表现为"一闪一闪、跳过来跳过去"。
  // 因此：拖子卡 = 拖它所在的整框（分组框 / 整集框），子卡保持在槽位内随框移动。
  const childDragRef = useRef<{childId: string; parentId: string; childStart: {x: number; y: number}; parentStart: {x: number; y: number}; pointerStart: {x: number; y: number}} | null>(null);
  const dragNodesRef = useRef<FlowNode[]>([]);
  const childIdsRef = useRef<Set<string>>(new Set());
  // 拖动期间冻结构建：flowNodes 会因为资产图比例回填 / SSE 数据刷新而重算，
  // 若此时整批替换节点对象，React Flow 会重新采纳被拖节点（positionAbsolute 重算），
  // 出现"拖到一半跳一下"。拖动结束后再补一次同步。
  const draggingRef = useRef(false);
  const dragPointerIdRef = useRef<number | null>(null);
  const pointerSurfaceRef = useRef<HTMLDivElement | null>(null);
  const pendingFlowSyncRef = useRef(false);
  const [flowSyncTick, setFlowSyncTick] = useState(0);
  useEffect(() => {
    dragNodesRef.current = nodes;
    childIdsRef.current = new Set(nodes.filter((item) => item.parentId).map((item) => item.id));
  }, [nodes]);

  // 参考线绘制：命令式 DOM（每帧 setState 会让整棵树重渲染，拖动必然掉帧）。
  // 参考线挂在 ViewportPortal 里 —— 它渲染在 .react-flow__viewport 内部，
  // 因此可以直接用「画布坐标」定位，不必手算 zoom/pan 换算。
  const updateAlignGuides = useCallback((guides: AlignGuide[], zoom = 1) => {
    const vEl = guideVRef.current;
    const hEl = guideHRef.current;
    if (!vEl || !hEl) return;
    // 参考线父级带 zoom 变换，这里反向补偿，保证屏幕上恒为 1px（缩到 20% 也不会消失）
    const z = Math.max(zoom, 0.05);
    // ★ 线身 = 「屏幕上 1px」（第 17 轮修）：之前是 2px，用户反馈"太粗"。
    //   1px 之所以之前被判定"看不清"，真正原因是那时颜色用了高饱和洋红 + 1px 外描边，
    //   在深色画布上发闷；现在换成统一蓝 + 柔光晕，1px 实线反而更准更利落
    //   （Figma / 即梦的参考线同样是 1px）。保持 1/zoom 反向补偿，任何缩放下都是 1 屏幕 px。
    const thickness = 1 / z;
    const v = guides.find((g) => g.axis === 'v');
    const h = guides.find((g) => g.axis === 'h');
    // ★ 区间兜底：求解器给的 from/to 只覆盖「被拖盒子 + 对齐目标」那一段，
    //   卡片小时只有几十像素长，缩到 17% 后屏幕上只剩 5px —— 用户根本看不到，
    //   反馈就是"没有辅助线"。这里把区间向两端各外扩 24 屏幕像素（换算成画布坐标），
    //   保证任何缩放下都有一段清晰可见的线；外扩量不改变吸附语义，只是视觉延长。
    const pad = 24 / z;
    const span = (a: number, b: number) => {
      const lo = Math.min(a, b);
      const hi = Math.max(a, b);
      // NaN / Infinity 守卫：以前 movingBox 用 Infinity 起步会算成 NaN，
      // 写进 style 就是 "NaNpx" 被浏览器丢弃 → 零长度线（见 handleNodesChange 注释）。
      const safeLo = Number.isFinite(lo) ? lo : 0;
      const safeHi = Number.isFinite(hi) ? hi : safeLo + 120 / z;
      return {lo: safeLo - pad, len: Math.max(60 / z, safeHi - safeLo + pad * 2)};
    };
    // ★★ 第 16 轮修（用户反馈「拖动辅助线功能细节还需打磨优化」）：
    //   ① 用 class 切换代替 style.opacity —— 之前每帧写 opacity='1' 但 CSS 上有
    //      `transition: opacity 90ms`，拖动中反复"消失→出现"会采到 0.71 这种半透明中间帧，
    //      肉眼就是"辅助线一闪一闪"。现在加 `.is-on`：transition 只在显示时给短淡入，
    //      隐藏时立即生效（0ms），彻底消除中间帧。
    //   ② 显示位置数值标签：即梦/Figma 都会在参考线上标出距目标边的距离，
    //      用户才能判断"还差多少对不齐"。之前只有一条线，看不出偏差量。
    const setOn = (el: HTMLElement, on: boolean) => {
      el.classList.toggle('is-on', on);
    };
    if (v) {
      const {lo, len} = span(v.from, v.to);
      setOn(vEl, true);
      vEl.style.left = `${v.pos}px`;
      vEl.style.top = `${lo}px`;
      vEl.style.width = `${thickness}px`;
      vEl.style.height = `${len}px`;
      // 垂直线的读数放在线的顶部（跟随线走，不遮挡被拖的卡片）
      const lblV = guideVLabelRef.current;
      if (lblV) {
        const val = v.delta ?? 0;
        lblV.textContent = val === 0 ? '0' : `${val > 0 ? '+' : ''}${val}`;
        lblV.classList.toggle('is-zero', val === 0);
        lblV.style.left = `${v.pos}px`;
        lblV.style.top = `${lo}px`;
        lblV.classList.add('is-on');
      }
    } else {
      setOn(vEl, false);
      guideVLabelRef.current?.classList.remove('is-on');
    }
    if (h) {
      const {lo, len} = span(h.from, h.to);
      setOn(hEl, true);
      hEl.style.left = `${lo}px`;
      hEl.style.top = `${h.pos}px`;
      hEl.style.width = `${len}px`;
      hEl.style.height = `${thickness}px`;
      const lblH = guideHLabelRef.current;
      if (lblH) {
        const val = h.delta ?? 0;
        lblH.textContent = val === 0 ? '0' : `${val > 0 ? '+' : ''}${val}`;
        lblH.classList.toggle('is-zero', val === 0);
        lblH.style.left = `${lo}px`;
        lblH.style.top = `${h.pos}px`;
        lblH.classList.add('is-on');
      }
    } else {
      setOn(hEl, false);
      guideHLabelRef.current?.classList.remove('is-on');
    }
  }, []);

  // ── 多选浮动操作条（对标即梦）─────────────────────────────────
  // 选区变化 / 画布平移缩放 / 拖拽中，都把「选中卡片的屏幕包围盒」算出来，
  // 操作条浮在包围盒上方居中。用 rAF 节流：拖拽每帧都会触发，直接 setState 会掉帧。
  // 必须定义在 onNodeDragStop / onSelectionEnd 之前：它们在 deps 里引用本函数，
  // 位置放后面会命中 TDZ（"Cannot access before initialization" 整树白屏）。
  const selBarRafRef = useRef<number | null>(null);
  const syncSelBar = useCallback(() => {
    if (selBarRafRef.current !== null) return;
    selBarRafRef.current = requestAnimationFrame(() => {
      selBarRafRef.current = null;
      const rect = stageRef.current?.getBoundingClientRect();
      if (!rect) { setSelBar(null); return; }
      // 只统计「卡片」节点（媒体卡 / 备注）：段卡、资产卡、整集框不参与批量摆放
      const sel = rf.getNodes().filter((n) => n.selected && (n.id.startsWith(MEDIA_PREFIX) || n.id.startsWith(NOTE_PREFIX)));
      if (sel.length < 2) { setSelBar(null); return; }
      let minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity;
      for (const n of sel) {
        const el = document.querySelector(`.react-flow__node[data-id="${CSS.escape(n.id)}"]`) as HTMLElement | null;
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r.width === 0 && r.height === 0) continue;
        minL = Math.min(minL, r.left);
        minT = Math.min(minT, r.top);
        maxR = Math.max(maxR, r.right);
        maxB = Math.max(maxB, r.bottom);
      }
      if (!Number.isFinite(minL) || !Number.isFinite(minT)) { setSelBar(null); return; }
      // 坐标转成 stage 相对坐标（操作条是 stage 的绝对定位子元素）
      const centerX = (minL + maxR) / 2 - rect.left;
      const topY = minT - rect.top;
      // 批量连线抓手：贴在选区右缘垂直居中（对标即梦的选区连接点）——
      // 按住它拖到目标卡片，即可把选中的所有卡片一次性连过去。
      // 只有 ≥2 张媒体卡时才有意义（备注没有生成语义），所以额外带 mediaCount。
      const mediaCount = sel.filter((n) => n.id.startsWith(MEDIA_PREFIX)).length;
      // 批量调参 / 批量下载的可用范围（只统计媒体卡；备注没有参数也没有素材）
      const mediaIds = sel.filter((n) => n.id.startsWith(MEDIA_PREFIX)).map((n) => n.id.slice(MEDIA_PREFIX.length));
      const picked = mediaNodesRef.current.filter((mn) => mediaIds.includes(mn.id));
      // ★ 四类都算"可批量调参"：图片(模型/比例/清晰度) · 视频(模型/比例/清晰度)
      //   · 音频(模型/格式/采样率/模式，第 33 轮) · 文本(模型/长度/温度，第 34 轮)
      const adjustCount = picked.filter((mn) => mn.kind === 'image' || mn.kind === 'video' || mn.kind === 'audio' || mn.kind === 'text').length;
      const generateCount = picked.filter((mn) => mn.kind === 'image' || mn.kind === 'video' || mn.kind === 'audio').length;
      // 「已生成好」的判定：status === done 且真的有 url。只看 status 会把
      // 重新生成中（status 仍是 done 但 content 已被清空）的节点算进来。
      const readyCount = picked.filter((mn) => mn.kind !== 'text' && !!mn.mediaUrl).length;
      // 贴边不悬空：留 12px（正好等于抓手半径 + 边距），视觉上像从卡片边缘长出来的手柄
      const rightX = maxR - rect.left + 12;
      const midY = (minT + maxB) / 2 - rect.top;
      // 操作条位置：贴在选区上方，最多钳到 8px。
      // ★ 下拉方向必须**独立**判断（用户截图反馈的遮挡就是这里错了）：
      //   旧逻辑用「选区顶部到画布顶部」的空间判断能否向上展开，但操作条本身被钳在
      //   8px 时，它上方根本没有空间放菜单 —— 于是菜单顶到 top=-53、被顶部工具条吃掉。
      //   现在改成先算出操作条真实 top，再看「操作条上方还剩多少」，不够就向下展开。
      const BAR_H = 38;
      const MENU_H = 320;      // 编组整理：对齐6+分布2+更多整理+编组2，实测 ~275，留余量
      // ★ 选区上方若套着编组外框，操作条要再往上让一截（框顶部预留了组名标签带）。
      //   否则操作条正好压在框上沿、把组名盖住 —— 用户选了整组却看不出这是哪个组。
      //
      //   数值 24 → 34 的由来（第 15 轮实测）：
      //     框顶部带现在屏幕恒为 22px（.canvas-group-band.is-top），再加 10px 的框外留白，
      //     操作条要完全避开这 32px 才不压住它。原来的 24 只够盖到一半，
      //     实测操作条底边落在 y=132~154 正好压在框标题栏（框 top=132），
      //     于是"抓框顶部拖动"命中的是操作条按钮，而不是框 → 顶部整条手感死区。
      //
      // ★★ 改用**实测**框的顶边，而不是猜一个常量（上一版 FRAME_HEAD=24 → 34 都不够：
      //     框顶边距选区顶部 = 屏幕 10px(PAD) + 22px(标题带) ≈ 32px，
      //     而操作条本来只比选区高 BAR_H+14=52px，34 的预留仍会压到框上）。
      //     做法：直接读框 DOM 的 getBoundingClientRect().top，
      //     barTop 必须在它上面再留 8px 呼吸 —— 无论缩放到多少都自动正确。
      const frameBox = (() => {
        const fr = groupFrameRef.current;
        if (!fr) return null;
        let minTop = Infinity, maxBottom = -Infinity;
        for (const f of fr.querySelectorAll<HTMLElement>('[data-group-id]')) {
          if (f.style.display === 'none') continue;
          const gid = f.dataset.groupId;
          const g = groupsRef.current.find((x) => x.id === gid);
          if (!g) continue;
          if (!g.members.some((m) => sel.some((n) => n.id === m))) continue;
          const r = f.getBoundingClientRect();
          if (r.height > 0) {
            minTop = Math.min(minTop, r.top);
            maxBottom = Math.max(maxBottom, r.bottom);
          }
        }
        return Number.isFinite(minTop) ? {top: minTop, bottom: maxBottom} : null;
      })();
      const frameTop = frameBox ? frameBox.top : null;
      const frameBottom = frameBox ? frameBox.bottom : maxB;
      let barTop: number;
      if (frameTop !== null) {
        // 有框：优先放到框顶边上方 8px。
        // ★ 但框贴近画布顶部时上方没有空间 —— 实测框顶边 131（stage 相对 123），
        //   减掉 BAR_H(38)+8 得 77，再减 rect.top 后仍是负值 → 被钳到 8px，
        //   结果操作条**还是压在框上**（styleTop 恒为 "8px"，重叠 26px）。
        //   钳位后依然会重叠时，改为把操作条放到**选区下方**：
        //   下方就是卡片，但卡片在操作条之下（操作条是 stage 浮层，z-index 更高），
        //   视觉上像"贴着选区下沿的悬浮工具条"，比压住框标题栏（那里是整条拖动热区）好得多。
        const wantTop = Math.round(frameTop - rect.top) - BAR_H - 8;
        if (wantTop >= 8) {
          barTop = wantTop;
        } else {
          // 上方塞不下 → 选区下方。
          // ★ 必须放到**框的底边之下**，不能只按选区底边（maxB）：
          //   框底部还有 10px 屏幕留白 + 22px 底部抓取带，按选区底边算会正好压住底部热区
          //   （实测"抓框底部拖动"失败：命中的是操作条按钮）。
          const belowTop = Math.round(frameBottom - rect.top) + 10;
          barTop = Math.min(rect.height - BAR_H - 8, belowTop);
        }
      } else {
        barTop = Math.max(8, topY - BAR_H - 14);
      }
      const roomAbove = barTop - 8;
      const roomBelow = rect.height - (barTop + BAR_H) - 8;
      // 上方放不下菜单，且下方比上方宽裕 → 向下展开；两边都紧就选更宽裕的一侧。
      const menuDown = roomAbove < MENU_H && roomBelow > roomAbove;
      setSelBar({x: centerX, y: topY, w: Math.max(0, maxR - minL), count: sel.length, rightX, midY, mediaCount, menuDown, barTop, adjustCount, generateCount, readyCount, mediaIds});
    });
  }, [rf]);

  useEffect(() => () => {
    if (selBarRafRef.current !== null) cancelAnimationFrame(selBarRafRef.current);
  }, []);

  // ── 编组外框（用户："组起来后加个框，不然第一眼谁知道哪些是组起来的"）────────
  //
  // 设计取舍：
  //  · **不用 React Flow 的 parentId 容器**：那会把节点变成子节点、位置真源改写，
  //    拖父级才动、拖子级要额外开拖拽手柄，还会和画布既有的段卡父子结构打架。
  //    这里只做「纯视觉框」：按组成员实时算包围盒，画一个框套在外面，成员关系仍是
  //    groups 表 + 选中扩散（按组拖动已在 pressGroup 里解决）。
  //  · **命令式写 DOM，不走 setState**：拖动时每帧都要更新框，走 React 必然掉帧
  //    （和 updateAlignGuides / syncSelBar 同一套理由）。
  //  · 框本身 `pointer-events:none`，只有组名标签和解组按钮可点，绝不挡卡片。
  // 拖动过程中每帧的**权威位置**（屏幕无关，画布坐标）。
  // ★ 为什么不能只读 store：React Flow 的 store 由 React 渲染提交后才更新，
  //   在 onNodeDrag/handleNodesChange 里读到的还是上一帧 —— 框必然落后卡片（实测落后 20~40px）。
  //   handleNodesChange 收到的 position change 就是这一帧的最终值，在这里记下来，
  //   框用它来算，和卡片同帧、零延迟。拖动结束（onNodeDragStop）清空。
  const livePosRef = useRef<Map<string, {x: number; y: number}>>(new Map());
  const syncGroupFrames = useCallback(() => {
    // ★★ 定位方式：框挂在 **ViewportPortal** 里（.react-flow__viewport 内部），
    //   用**画布坐标**定位 —— 框的位置和缩放由画布的 transform 自动带着走，
    //   不需要手算 zoom/pan，也不需要每帧量 DOM。
    //
    //   上一版是「stage 绝对定位 + 每帧 getBoundingClientRect 测卡片屏幕位置」，两个致命问题：
    //     ① 拖动的同步时机永远对不上：onNodeDrag/handleNodesChange 里量 DOM 时，
    //        React 还没把新位置刷进 style，量到的是上一帧 → 框永远比卡片落后 20~30px
    //        （实测 dFrame=15 / dCard=53），用户看到的就是"拖卡片，框跟不上"。
    //     ② 画布缩放/平移时要额外监听 onMove 重算，否则框会漂。
    //   现在改成读 React Flow store 里的节点坐标（position + measured），
    //   这些值在同一帧内就是最新的，不存在时序问题；缩放平移由 viewport 自动处理。
    const layer = groupFrameRef.current;
    if (!layer) return;
    const list = groupsRef.current.filter((g) => g.members.length >= 2);
    // 复用已有 DOM（按 data-group-id），数量不变时只改 style
    const existing = new Map<string, HTMLElement>();
    layer.querySelectorAll<HTMLElement>('[data-group-id]').forEach((el) => {
      existing.set(el.dataset.groupId || '', el);
    });
    const st = storeApi.getState();
    const lookup = st.nodeLookup;
    const live = livePosRef.current;
    const keep = new Set<string>();
    for (const g of list) {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      let anySelected = false;
      let allSelected = true;
      let found = 0;
      for (const m of g.members) {
        const internal = lookup.get(m);
        if (!internal) { allSelected = false; continue; }
        // 位置优先级：拖动中的实时值 > internalNode 绝对坐标 > position
        // （拖动中读 store 会拿到上一帧，框就落后 —— 见 livePosRef 注释）
        const livePos = live.get(m);
        const abs = livePos
          ?? (internal as unknown as {internals?: {positionAbsolute?: {x: number; y: number}}}).internals?.positionAbsolute
          ?? internal.position;
        const w = internal.measured?.width ?? internal.width ?? 0;
        const h = internal.measured?.height ?? internal.height ?? 0;
        if (!abs || (w === 0 && h === 0)) { allSelected = false; continue; }
        found++;
        if (internal.selected) anySelected = true; else allSelected = false;
        minX = Math.min(minX, abs.x);
        minY = Math.min(minY, abs.y);
        maxX = Math.max(maxX, abs.x + w);
        maxY = Math.max(maxY, abs.y + h);
      }
      if (found === 0 || !Number.isFinite(minX)) {
        const old = existing.get(g.id);
        if (old) old.style.display = 'none';
        continue;
      }
      keep.add(g.id);
      let el = existing.get(g.id);
      if (!el) {
        el = document.createElement('div');
        el.className = 'canvas-group-frame';
        el.dataset.groupId = g.id;
        const tag = document.createElement('div');
        tag.className = 'canvas-group-tag';
        tag.dataset.groupGrab = '1';   // 抓标签 = 拖整组
        const nameEl = document.createElement('span');
        nameEl.className = 'canvas-group-tag-name';
        tag.appendChild(nameEl);
        const cnt = document.createElement('i');
        cnt.className = 'canvas-group-tag-count';
        tag.appendChild(cnt);
        const x = document.createElement('button');
        x.type = 'button';
        x.className = 'canvas-group-tag-x';
        x.title = '解除编组';
        const closeIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        closeIcon.setAttribute('viewBox', '0 0 16 16');
        closeIcon.setAttribute('aria-hidden', 'true');
        const closePath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        closePath.setAttribute('d', 'M4 4l8 8M12 4l-8 8');
        closePath.setAttribute('fill', 'none');
        closePath.setAttribute('stroke', 'currentColor');
        closePath.setAttribute('stroke-width', '1.8');
        closePath.setAttribute('stroke-linecap', 'round');
        closeIcon.appendChild(closePath);
        x.appendChild(closeIcon);
        x.dataset.ungroup = '1';
        tag.appendChild(x);
        el.appendChild(tag);
        // ★ 四条**真实 DOM** 抓取边带（不是伪元素）：即梦/Figma 抓框边就能拖整组。
        //   为什么不用一个铺满 + mask 挖空的伪元素：CSS mask 只影响绘制、不影响命中测试，
        //   挖空后 pointer-events 仍覆盖整块 → 卡片中心被盖住、点卡片全被框吃掉（实测选中数=0）。
        //   四条边带各自只有一条几十 px 的窄带，卡片区域物理上没有元素覆盖，不可能抢点击。
        //
        //   ★ 第 15 轮补：顶部这条带要**比其它三边更高**（覆盖组名标签整行 + 右侧留白）。
        //     用户实测点"框顶部"（标签右边的空位）拖不动 —— 那里既不是标签本体、也不在 16px 边带里，
        //     elementFromPoint 直接落回画布。顶部本来就是"标题栏"语义，整条都可抓最符合直觉。
        for (const side of ['top', 'bottom', 'left', 'right']) {
          const band = document.createElement('div');
          band.className = `canvas-group-band is-${side}`;
          band.dataset.groupBand = side;
          el.appendChild(band);
        }
        layer.appendChild(el);
      }
      el.style.display = '';
      // 画布坐标 → 直接写 left/top/width/height（viewport 已负责缩放平移）。
      //
      // ★★ PAD/HEAD 必须**按 zoom 反向放大**（除以 scale），否则在缩小视图下框内根本没有空白：
      //    框在 viewport 里用画布坐标定位，10px 画布单位 × 0.168（17% 缩放）= **1.68 屏幕像素**。
      //    用户说"组框每个地方都可以拖动"，可实测只有 1.68px 的缝，
      //    人根本点不进去，等于框内空白区不存在（这就是 G5 里 5 个候选点全被判成"压在卡片上"的原因）。
      //    除以 scale 后，屏幕上恒为 PAD_SCREEN 像素的留白，任何缩放下都抓得住。
      const scale = Math.max(0.05, rf.getZoom() || 1);
      const PAD_SCREEN = 10;   // 期望的屏幕像素留白
      const HEAD_SCREEN = 20;  // 顶部组名标签带（屏幕像素）
      const PAD = PAD_SCREEN / scale;
      const HEAD = HEAD_SCREEN / scale;
      el.style.left = `${minX - PAD}px`;
      el.style.top = `${minY - PAD - HEAD}px`;
      el.style.width = `${maxX - minX + PAD * 2}px`;
      el.style.height = `${maxY - minY + PAD * 2 + HEAD}px`;
      el.classList.toggle('is-active', anySelected);
      el.classList.toggle('is-full', allSelected);
      const nameEl = el.querySelector<HTMLElement>('.canvas-group-tag-name');
      if (nameEl && nameEl.textContent !== g.name) nameEl.textContent = g.name;
      const cntEl = el.querySelector<HTMLElement>('.canvas-group-tag-count');
      const cntText = `${g.members.length}`;
      if (cntEl && cntEl.textContent !== cntText) cntEl.textContent = cntText;
    }
    // 清掉已经不存在的组
    for (const [id, el] of existing) {
      if (!keep.has(id)) el.remove();
    }
  }, [storeApi]);

  // 编组变化 / 组内卡片移动 / 画布缩放平移 → 都要重画框
  useEffect(() => { syncGroupFrames(); }, [groups, syncGroupFrames]);

  // ★ 编组状态一变，浮动操作条要**重新定位**。
  //   操作条的位置是在"选区选中"时算好的（barTop 减 FRAME_HEAD 只对当时已有框的组生效），
  //   而用户的操作顺序通常是「先框选 → 操作条已就位 → 右键编组」，此时框才出现，
  //   操作条不会自己重算 —— 于是它压在框的标题栏上。实测：操作条底边 157 / 框顶边 130，
  //   重叠 27px，框顶部整条被按钮吃掉（elementFromPoint 全返回 BUTTON / .canvas-selbar）。
  //   编组后主动 syncSelBar() 一次，它会按最新的 groups（hasFrame=true）把操作条抬上去。
  useEffect(() => {
    syncSelBar();
    // 下一帧再补一次：框的 DOM 是命令式创建的，首帧时 hasFrame 已为真但选区块尺寸可能还没量准
    const t = window.setTimeout(() => syncSelBar(), 60);
    return () => window.clearTimeout(t);
  }, [groups, syncSelBar]);

  // ref 桥接：框拖动要用到一批「定义在本文件更后面」的回调（updateMediaNode / commitNotePositions /
  // persistLayout / syncGroupFrames 自身）。直接写进依赖会命中 TDZ（本项目踩过两次，
  // 白屏 + 所有选择器失效），所以统一过 ref，effect 依赖只留 [rf]。
  const updateMediaNodeRef = useRef(updateMediaNode);
  updateMediaNodeRef.current = updateMediaNode;
  const commitNotePositionsRef = useRef(commitNotePositions);
  commitNotePositionsRef.current = commitNotePositions;
  const persistLayoutRef = useRef(persistLayout);
  persistLayoutRef.current = persistLayout;
  const syncGroupFramesRef = useRef(syncGroupFrames);
  syncGroupFramesRef.current = syncGroupFrames;
  // pressGroup 定义在第 5269 行（比这段 effect 靠后）—— 直接引用会命中 TDZ，
  // 本项目已踩过两次（白屏 + 所有选择器失效）。这里只声明 ref，
  // **赋值放在 pressGroup 定义之后**（render 体内 `const` 是块级作用域，
  // 在声明之前读取变量本身就会抛 ReferenceError，光"写在 ref 里"救不了）。
  const pressGroupRef = useRef<(members: string[]) => void>(() => {});

  // 外框上的「✕ 解除编组」：DOM 是命令式建的，用原生事件委托（别用 React 合成事件）。
  // ★ 委托必须挂在 **document** 上，不能挂 groupFrameRef.current：
  //   effect 在首次渲染后执行，那时 ref 可能还是 null（弹窗分支渲染时这一层根本没挂载），
  //   一挂 null 就再也不会绑定，点击永远没反应（本轮实测 H6 就挂在这里）。
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const t = e.target as HTMLElement | null;
      const btn = t?.closest?.('[data-ungroup]') as HTMLElement | null;
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();
      const frame = btn.closest('[data-group-id]') as HTMLElement | null;
      const gid = frame?.dataset.groupId;
      if (gid) ungroupByIdRef.current(gid);
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  // ★ 抓「组框」拖整组（用户："成熟系统会这么做？"——Figma/即梦 抓组框边缘即可移动整组）。
  //   为什么不用 React Flow 的 parentId 容器：那会把卡变成子节点、位置真源改写，
  //   还会和既有段卡父子结构打架（见 syncGroupFrames 注释）。这里用原生拖拽实现：
  //     ① pointerdown 落在组名标签上（data-group-grab）→ 记住起始指针 + 所有成员起始位置
  //     ② pointermove → 按画布缩放换算成世界坐标位移，直接写 mediaNodes/notes 的位置真源
  //     ③ pointerup → 落盘一次（不逐帧写库）
  //   **只在标签/框边缘（.canvas-group-tag）触发**，框的中间区域 pointer-events:none，
  //   所以永远不会抢卡片的点击和拖拽。
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      // ✕ 解组按钮不当作拖动
      if (t.closest?.('[data-ungroup]')) return;
      // ★ 抓取点（按优先级）：组名标签 → 四条边带 → **框内整块空白**。
      //   用户第 4 次反馈原话：「即梦的组框每个地方都可以拖动，相当于就是批量选择节点一样的道理，
      //   单击节点就还是选中单个不影响单击弹窗」。
      //   —— 上一版只有「标签 + 四条 16px 边带」，用户在框内缝隙/卡片之间按下时什么都不会发生，
      //      他还得专门去找那条窄边，这就是他说的"必须要拖动节点才能拖动"。
      //
      //   ★ 为什么能做到「框里随便拖」同时「卡片行为完全不变」：
      //     框本体 pointer-events:none，所以 **document.elementFromPoint 会自动跳过框**、
      //     直接返回那个位置真正该接收点击的元素：
      //       · 返回 .react-flow__node（卡片本体、卡片上的按钮、连接点）→ 我们**让位**，
      //         React Flow 的选中/拖单卡/打开弹窗全部照旧，一行都不用改
      //       · 返回 .react-flow__pane / viewport（卡片之间的缝隙、框与卡片之间的留白）
      //         → 说明这里是"组框的空白区域"，由我们接管，拖动整组
      //     这比铺一层覆盖全框的热区 + mask/clip 挖洞靠谱得多：
      //     覆盖层一旦铺上，卡片中心就被物理盖住（CSS mask 只影响绘制、不影响命中测试，
      //     实测点卡片选中数 = 0）。
      let frame: HTMLElement | null = null;
      const tagHit = t.closest('[data-group-grab]');
      if (tagHit) {
        frame = tagHit.closest('[data-group-id]') as HTMLElement | null;
      } else if (t.closest('[data-group-band]')) {
        // 命中四条抓取边带之一（.canvas-group-band）—— 即梦/Figma 抓框边拖整组
        frame = t.closest('[data-group-id]') as HTMLElement | null;
      } else {
        // 框内空白区：几何判断是否落在某个框的矩形里，且**该点下方没有卡片**
        const layer = groupFrameRef.current;
        if (layer) {
          const hitEl = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
          const onCard = !!(hitEl && hitEl.closest?.('.react-flow__node'));
          // 压在卡片上 → 一律让位；也不接管画布上的其它浮层（操作条、弹窗等）
          const onOverlay = !!(hitEl && hitEl.closest?.('.canvas-selbar, .jim-bar, .canvas-context-menu, .canvas-seg-menu, .canvas-inspector, .canvas-topbar, .canvas-toolbar'));
          if (!onCard && !onOverlay) {
            for (const f of layer.querySelectorAll<HTMLElement>('[data-group-id]')) {
              const r = f.getBoundingClientRect();
              if (r.width === 0 || r.height === 0) continue;
              if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
                frame = f;
                break;
              }
            }
          }
        }
      }
      if (!frame) return;
      const gid = frame.dataset.groupId;
      if (!gid) return;
      const grp = groupsRef.current.find((g) => g.id === gid);
      if (!grp) return;
      // 记下所有成员的起始位置（媒体卡与备注两套真源分别处理）
      const startM = new Map<string, {x: number; y: number}>();
      const startN = new Map<string, {x: number; y: number}>();
      for (const m of grp.members) {
        if (m.startsWith(MEDIA_PREFIX)) {
          const id = m.slice(MEDIA_PREFIX.length);
          const mn = mediaNodesRef.current.find((x) => x.id === id);
          if (mn) startM.set(id, {x: mn.x, y: mn.y});
        } else if (m.startsWith(NOTE_PREFIX)) {
          const id = m.slice(NOTE_PREFIX.length);
          const nt = notesRef.current.find((x) => x.id === id);
          if (nt) startN.set(id, {x: nt.x, y: nt.y});
        }
      }
      if (startM.size + startN.size === 0) return;
      // ★ 按下框内空白 = 选中整组（即梦/Figma 语义：组框就是"批量选择"的具象化）。
      //   用户原话：「相当于就是批量选择节点一样的道理」。选中后操作条才会出现，
      //   用户才能接着做对齐/删除/编组等批量动作 —— 否则抓框拖完之后组还是"没选中"状态，
      //   操作条不出来，等于这个组只能拖不能管。
      pressGroupRef.current(grp.members);
      e.preventDefault();
      e.stopPropagation();
      // 抓框拖动时光标给 grabbing，和抓卡片一致
      const prevCursor = document.body.style.cursor;
      document.body.style.cursor = 'grabbing';
      const sx = e.clientX, sy = e.clientY;
      const zoom = rf.getZoom() || 1;
      let moved = false;
      let raf = 0;
      const apply = (dx: number, dy: number) => {
        if (raf) return;
        raf = requestAnimationFrame(() => {
          raf = 0;
          // ★★ 第 18 轮修（用户原话：「组的连线节点拖动不跟着走」）：
          //   根因不是连线本身，而是**拖动期间节点的位置更新走了两条不同步的路**：
          //     旧写法只调 updateMediaNode → 改 mn.x/mn.y → flowNodes useMemo 重算
          //     → 生成**全新的 node 对象** → React Flow 拿新对象要重新 measure 一次
          //     才能算出连线锚点（sourceX/sourceY）。这一帧里卡片已经被 CSS transform
          //     移到新位置，连线却还锚在旧坐标上 —— 视觉上就是"线不跟着卡片走"，
          //     松手后才"啪"地追上。
          //   （拖单卡没这个问题：React Flow 自己拖的时候直接改 internalNode.position，
          //     卡片和锚点在同一帧、同一条路径上更新。）
          //   修法：拖动期间**同时**用 setNodes 把位置写进 React Flow 的节点状态，
          //     让锚点和卡片在同一帧内一起动；updateMediaNode 仍负责写数据源（落盘用）。
          setNodes((prev) => prev.map((item) => {
            if (item.type !== 'media') return item;
            const raw = item.id.startsWith('media-') ? item.id.slice('media-'.length) : '';
            const p = startM.get(raw);
            return p ? {...item, position: {x: p.x + dx, y: p.y + dy}} : item;
          }));
          for (const [id, p] of startM) {
            updateMediaNodeRef.current(id, {x: p.x + dx, y: p.y + dy});
          }
          if (startN.size > 0) {
            commitNotePositionsRef.current([...startN].map(([id, p]) => ({id, position: {x: p.x + dx, y: p.y + dy}})));
            // 备注节点同样要同步进 React Flow 状态，否则连到备注的线也会滞后
            setNodes((prev) => prev.map((item) => {
              const raw = item.id.startsWith('note-') ? item.id.slice('note-'.length) : '';
              const p = startN.get(raw);
              return p ? {...item, position: {x: p.x + dx, y: p.y + dy}} : item;
            }));
          }
          syncGroupFramesRef.current();
        });
      };
      const onMove = (ev: PointerEvent) => {
        const dx = (ev.clientX - sx) / zoom;
        const dy = (ev.clientY - sy) / zoom;
        if (!moved && Math.abs(ev.clientX - sx) + Math.abs(ev.clientY - sy) < 3) return;
        if (!moved) {
          // 真正开始拖动才算：先入历史，让这次拖动可撤销（与拖单卡的行为一致）
          pushHistory(userEdgesRef.current, notesRef.current);
        }
        moved = true;
        apply(dx, dy);
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        document.body.style.cursor = prevCursor;
        if (raf) { cancelAnimationFrame(raf); raf = 0; }
        if (moved) {
          // 落盘前先按最终位置重画一次框：
          // 拖动中框走的是 rAF 合批，最后一帧可能还没跑就被 pointerup 取消了 →
          // 框停在倒数第二帧的位置，和卡片差出整个末帧位移（实测差 54px）。
          syncGroupFramesRef.current();
          // ★ persistLayout 的签名是 (positions, edges, deleted)，**不能空调用** ——
          //   空调用等于什么都没存，松手后刷新页面整组位置全回退。
          persistLayoutRef.current(positionRef.current, userEdgesRef.current, deletedAutoRef.current);
        }
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [rf, pushHistory, setNodes]);

  // ★ 框内空白区的**光标提示**：框本体 pointer-events:none（不能吃点击），
  //   CSS 的 cursor 就失效了 —— 用户在框里移动鼠标看到的是默认箭头，
  //   根本不知道"这块能拖"。这里用 pointermove 主动兜：鼠标落在框矩形内、
  //   且该点下方不是卡片/浮层时，把 body 光标换成 move（即梦/Figma 同款手感）。
  //   命中卡片时恢复默认，卡片自己的 cursor 才能正常生效。
  useEffect(() => {
    let last = '';
    const onMove = (e: PointerEvent) => {
      const layer = groupFrameRef.current;
      if (!layer) return;
      const hitEl = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null;
      let want = '';
      if (hitEl && !hitEl.closest?.('.react-flow__node') && !hitEl.closest?.('.canvas-selbar, .jim-bar, .canvas-context-menu, .canvas-seg-menu, .canvas-inspector, .canvas-topbar, .canvas-toolbar')) {
        for (const f of layer.querySelectorAll<HTMLElement>('[data-group-id]')) {
          const r = f.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) continue;
          if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
            want = 'move';
            break;
          }
        }
      }
      if (want !== last) {
        last = want;
        document.body.style.cursor = want;
      }
    };
    document.addEventListener('pointermove', onMove, true);
    return () => {
      document.removeEventListener('pointermove', onMove, true);
      document.body.style.cursor = '';
    };
  }, []);

  // （groupFrameRafRef 已删除：框同步改同步执行后不再用 rAF 合批，无待清理的帧）

  // 子卡位置策略：
  // - 分段卡（type=segment）：允许自由拖拽 + Shift 多选批量拖动，位置持久化到 positionRef。
  // - 资产卡（资产分组框的子节点）：位置完全由槽位派生，不接受任何位置变更，避免闪帧。
  const handleNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    const segmentIds = new Set(nodesRef.current.filter((n) => n.type === 'segment').map((n) => n.id));
    const allowed = changes.filter((change) => {
      if (change.type !== 'position') return true;
      // 分段卡允许自由移动
      if (segmentIds.has(change.id)) return true;
      // 其他子节点（资产卡）锁定槽位
      if (childIdsRef.current.has(change.id)) return false;
      return true;
    });

    // ★ 记录这一帧的权威位置（框用它定位，避免慢一帧 —— 见 livePosRef 注释）。
    //   放在吸附之前：吸附只改 dx/dy 偏移量，最终位置后面会再覆盖一次（下面 patched 分支）。
    for (const c of allowed) {
      if (c.type === 'position' && c.position) {
        livePosRef.current.set(c.id, {x: c.position.x, y: c.position.y});
      }
    }

    // ── 智能对齐吸附（对标即梦 / Figma）────────────────────────────
    // React Flow 每帧用「指针绝对坐标」重算节点位置（dragItem.distance 固定，
    // nextPosition = 指针 - distance），所以位置天然跟随指针。
    // 我们在 onNodesChange 里把「吸附修正量」叠加到 position change 上：
    //   · 指针进入 enter 阈值 → 吸到参考线上
    //   · 继续在 glue 范围内 → 被"磁"住，牢牢贴在线上（不会弹回）
    //   · 超出 glue 但在 release 内 → 修正量线性衰减，节点平滑滑出线
    //   · 超出 release → 完全跟随指针
    // 因为修正是「叠加」而非「替换」，整个过程连续无跳变。
    if (!alignSnapOn || !draggingRef.current) {
      if (draggingRef.current) updateAlignGuides([]);
      onNodesChange(allowed);
      // 拖动中也要让编组外框每帧跟上（onNodeDrag 里也调了，这里再兜一次：
      // 有些位置的移动走的是 onNodesChange 而不是 onNodeDrag，比如键盘微移、程序化对齐）
      syncGroupFrames();
      return;
    }
    const posChanges = allowed.filter((c): c is NodeChange<FlowNode> & {type: 'position'; position: {x: number; y: number}} =>
      c.type === 'position' && !!c.position,
    );
    if (posChanges.length === 0) {
      onNodesChange(allowed);
      return;
    }
    const dragIds = new Set(posChanges.map((c) => c.id));
    const storeNodes = rf.getNodes();
    const storeById = new Map(storeNodes.map((n) => [n.id, n]));
    const sizeOf = (id: string) => {
      const sn = storeById.get(id);
      const mw = (sn?.measured?.width ?? sn?.width ?? 0) as number;
      const mh = (sn?.measured?.height ?? sn?.height ?? 0) as number;
      if (mw > 0 && mh > 0) return {w: mw, h: mh};
      return {
        w: (sn?.initialWidth ?? 0) as number,
        h: (sn?.initialHeight ?? 0) as number,
      };
    };
    // 被拖动组的整体包围盒（多选拖动时按组对齐，和 Figma 一致）
    // 注意：这里用 RF 给的**原始指针位置**（未加修正），求解器内部会自行处理磁吸状态。
    //
    // ★ 不能用 {x: Infinity, y: Infinity, w: 0, h: 0} 起步再边算边累加：
    //   第一轮 nx = min(Infinity, x) = x，但 movingBox.x + movingBox.w = Infinity + 0 = Infinity，
    //   于是 movingBox.w 被算成 Infinity - x = Infinity，后续所有 Math.max 都维持 Infinity，
    //   最终 movingBox.w/h 恒为 Infinity → 参考线 from/to 算成 ±Infinity →
    //   updateAlignGuides 里 height = max(1, to - from) 得到 NaN → 样式写入 0px。
    //   表现就是"拖动时辅助线根本不出现"（实测 100% 缩放下 .is-h 的 computed width 是 0px）。
    //   改成先收集所有盒子的四边，再用 min/max 求并集，结果必然有限。
    const boxes = posChanges.map((c) => {
      const {w, h} = sizeOf(c.id);
      return {x: c.position.x, y: c.position.y, w, h};
    });
    const bx0 = Math.min(...boxes.map((b) => b.x));
    const by0 = Math.min(...boxes.map((b) => b.y));
    const bx1 = Math.max(...boxes.map((b) => b.x + b.w));
    const by1 = Math.max(...boxes.map((b) => b.y + b.h));
    const movingBox = {id: '__moving__', x: bx0, y: by0, w: bx1 - bx0, h: by1 - by0};
    // 对齐候选：画布上其它「可自由摆放」的卡片（媒体卡 / 备注）。段卡与资产卡是派生布局，
    // 参与对齐反而会给出无意义的参考线，故排除。
    const others: AlignBox[] = [];
    for (const sn of storeNodes) {
      if (dragIds.has(sn.id)) continue;
      if (sn.parentId) continue;
      if (sn.type !== 'media' && sn.type !== 'note') continue;
      const {w, h} = sizeOf(sn.id);
      if (w <= 0 || h <= 0) continue;
      others.push({id: sn.id, x: sn.position.x, y: sn.position.y, w, h});
    }
    // 三个距离档都按「屏幕像素」手感换算成画布坐标（缩到 20% 时也能正常吸附）
    const zoom = rf.getZoom() || 1;
    const enter = Math.max(3, Math.min(30, ALIGN_ENTER_PX / zoom));
    const glue = Math.max(enter + 2, Math.min(90, ALIGN_GLUE_PX / zoom));
    const release = Math.max(glue + 4, Math.min(160, ALIGN_RELEASE_PX / zoom));
    const visible = Math.max(enter, ALIGN_VISIBLE_PX / zoom);
    const solved = solveAlignSnap(movingBox, others, alignStateRef.current, enter, glue, release);
    const liveGuides = computeAlignSnap(movingBox, others, visible).guides;
    const displayGuides = [
      ...solved.guides,
      ...liveGuides.filter((guide) => !solved.guides.some((active) => active.axis === guide.axis)),
    ];
    alignStateRef.current = solved.state;
    updateAlignGuides(displayGuides, zoom);
    // 修正量叠加到 position change 上（同一批统一叠加，保持多选组相对位置）
    const patched = allowed.map((c) => {
      if (c.type !== 'position' || !c.position) return c;
      if (!dragIds.has(c.id)) return c;
      return {...c, position: {x: c.position.x + solved.dx, y: c.position.y + solved.dy}};
    });
    for (const c of posChanges) {
      snappedPosRef.current.set(c.id, {x: c.position.x + solved.dx, y: c.position.y + solved.dy});
      // 吸附后的位置才是这一帧真正落地的坐标，覆盖掉上面记录的原始值
      livePosRef.current.set(c.id, {x: c.position.x + solved.dx, y: c.position.y + solved.dy});
    }
    onNodesChange(patched);
    syncGroupFrames();
  }, [alignSnapOn, onNodesChange, rf, updateAlignGuides, syncGroupFrames]);


  const onNodeDragStart = useCallback((event: MouseEvent | TouchEvent, node: FlowNode) => {
    setDraggingNode(true);
    draggingRef.current = true;
    // Only capture after the browser has committed to a real drag. Capturing on
    // pointerdown swallowed normal clicks and prevented the prompt editor from
    // opening.
    if (dragPointerIdRef.current !== null && pointerSurfaceRef.current) {
      try {
        pointerSurfaceRef.current.setPointerCapture(dragPointerIdRef.current);
      } catch {
        // Pointer capture is a release-safety enhancement, not a drag prerequisite.
      }
    }
    // 新一轮拖动：清掉上一轮残留的实时位置
    livePosRef.current.clear();
    // 新一轮拖拽：重置磁吸状态与吸附坐标，避免上一轮的锁定/偏移残留
    alignStateRef.current = createAlignState();
    snappedPosRef.current.clear();
    // Every draggable node type (media, segment, note, group frame, child card)
    // needs a position snapshot before the first movement. Media-only snapshots
    // left segments/groups/notes impossible to undo and caused the card to snap
    // back when the derived layout read the stale positionRef.
    pushHistory(userEdgesRef.current, notesRef.current, {positions: true});
    // 分段卡：允许自由拖拽，不走"拖子卡=移父框"逻辑
    if (node.type === 'segment') {
      childDragRef.current = null;
      return;
    }
    if (!node.parentId) {
      childDragRef.current = null;
      return;
    }
    const parent = dragNodesRef.current.find((item) => item.id === node.parentId);
    const pointer = dragPointer(event);
    childDragRef.current = parent
      ? {
          childId: node.id,
          parentId: node.parentId,
          childStart: {x: node.position.x, y: node.position.y},
          parentStart: {x: parent.position.x, y: parent.position.y},
          pointerStart: pointer,
        }
      : null;
  }, [pushHistory]);

  const onNodeDrag = useCallback((event: MouseEvent | TouchEvent, node: FlowNode) => {
    // ★ 编组外框必须**跟着节点一起动**（用户："编组框起来的节点拖动的时候框框不连节点一起动"）。
    //   旧实现只在 onNodeDragStop 里同步框 —— 拖动全程框钉在原地，松手才"啪"地跳过去，
    //   视觉上框和卡片彻底脱节，等于框没用。这里每帧都同步（内部 rAF 节流，不会掉帧）。
    syncGroupFrames();
    const drag = childDragRef.current;
    if (!drag || drag.childId !== node.id) {
      // 顶层节点（分组框 / 整集框 / 备注 / 分段卡）：持续同步 positionRef，
      // 避免拖动过程中 flowNodes 重算把节点拽回旧位置。
      // 媒体卡片不写 positionRef（位置以 mn.x/mn.y 为准，dragStop 时同步回 mn）。
      if (!node.id.startsWith('media-')) {
        positionRef.current[node.id] = {x: node.position.x, y: node.position.y};
      }
      return;
    }
    const zoom = rf.getZoom() || 1;
    const pointer = dragPointer(event);
    const next = {
      x: drag.parentStart.x + (pointer.x - drag.pointerStart.x) / zoom,
      y: drag.parentStart.y + (pointer.y - drag.pointerStart.y) / zoom,
    };
    positionRef.current[drag.parentId] = next;
    setNodes((prev) => prev.map((item) => {
      if (item.id === drag.childId) return {...item, position: drag.childStart};
      if (item.id === drag.parentId) return {...item, position: next};
      return item;
    }));
  }, [rf, setNodes, syncGroupFrames]);

  const onNodeDragStop = useCallback((_: unknown, node: FlowNode) => {
    setDraggingNode(false);
    draggingRef.current = false;
    // 拖动结束：实时位置表作废，之后一律读 store（store 此时已经是最新值）
    livePosRef.current.clear();
    // 拖动结束：清掉参考线
    updateAlignGuides([]);
    // 浮动操作条跟随落点（拖完不会停在旧位置）
    syncSelBar();
    // ★ 编组外框的落点同步**不能**放在这里（原来就在这儿）。
    //   此刻 livePosRef 刚被清空、位置真源还没写回（下面 4635+ 才开始 updateMediaNode），
    //   syncGroupFrames 读到的 store 是"拖动前的旧值" → 框停在距卡片 11px 处
    //   （实测 H5b：dFrame.y=59 / dA.y=70，差一整帧吸附量）。
    //   正确做法：等位置写回 + React 提交完再同步，见本函数末尾的两次同步。
    // RF 在拖拽结束时给的 node.position 是「未吸附」的指针坐标（dragItem.position 是指针推导值）。
    // 若直接写回位置真源，节点会在松手瞬间弹回未对齐位置 —— 必须用吸附后的坐标。
    const snapped = snappedPosRef.current.get(node.id);
    const finalPos = snapped ?? node.position;
    const drag = childDragRef.current;
    if (drag && drag.childId === node.id) {
      const parent = dragNodesRef.current.find((item) => item.id === drag.parentId);
      if (parent) positionRef.current[drag.parentId] = {x: parent.position.x, y: parent.position.y};
      childDragRef.current = null;
    } else {
      // 媒体卡片：位置同步回 mn（updateMediaNode → setMediaNodes → flowNodes 重建），
      // 并持久化到载荷 node.position（刷新/切集后保持拖过的位置）；不写 positionRef。
      if (node.id.startsWith('media-')) {
        const mediaId = node.id.slice('media-'.length);
        updateMediaNode(mediaId, {x: finalPos.x, y: finalPos.y});
      } else {
        positionRef.current[node.id] = {x: finalPos.x, y: finalPos.y};
      }
      // 批量拖动：保存所有同时选中节点的位置（多段一起拖时其他段不丢位置）
      const selected = dragNodesRef.current.filter((n) => n.selected && n.id !== node.id);
      for (const sel of selected) {
        const selSnapped = snappedPosRef.current.get(sel.id);
        const selPos = selSnapped ?? sel.position;
        if (sel.id.startsWith('media-')) {
          updateMediaNode(sel.id.slice('media-'.length), {x: selPos.x, y: selPos.y});
        } else {
          positionRef.current[sel.id] = {x: selPos.x, y: selPos.y};
        }
      }
      // 段卡拖出父框范围时，动态撑开 ep-box 高度
      if (node.type === 'segment' && node.parentId) {
        const parent = dragNodesRef.current.find((n) => n.id === node.parentId);
        if (parent) {
          const siblings = dragNodesRef.current.filter((n) => n.parentId === node.parentId);
          const maxBottom = siblings.reduce((max, s) => {
            const h = (s.initialHeight ?? s.height ?? 360) as number;
            return Math.max(max, s.position.y + h);
          }, 0);
          const curH = (parent.style?.height ?? parent.height ?? 0) as number;
          const needed = maxBottom + 20;
          if (needed > curH) {
            setNodes((prev) => prev.map((n) => {
              if (n.id !== node.parentId) return n;
              return {...n, style: {...n.style, height: needed}, height: needed};
            }));
          }
        }
      }
      if (node.type === 'note') {
        const noteId = node.id.startsWith('note-') ? node.id.slice('note-'.length) : node.id;
        commitNotePositions([{id: noteId, position: finalPos}]);
      }
    }
    snappedPosRef.current.clear();
    persistLayout(positionRef.current, userEdgesRef.current, deletedAutoRef.current);
    if (pendingFlowSyncRef.current) setFlowSyncTick((tick) => tick + 1);
    // ★ 编组外框落点同步（放在位置写回之后，两拍：
    //   ① 同步一次：此时 updateMediaNode/commitNotePositions 已调（React 还没提交 DOM，
    //      但 syncGroupFrames 读的是 store，store 由 setState 驱动，这一拍能拿到最终值）
    //   ② rAF 再补一次：React 提交 + React Flow 重算 measured 之后的兜底，
    //      保证框和卡片像素级贴合（实测只有①时仍差 11px）。
    syncGroupFrames();
    requestAnimationFrame(() => syncGroupFrames());
  }, [commitNotePositions, persistLayout, setNodes, updateMediaNode, updateAlignGuides, syncSelBar, syncGroupFrames]);

  const clearStuckDrag = useCallback(() => {
    // Run after React Flow's own pointer handlers. A normal drag has already
    // called onNodeDragStop; this only clears state if pointerup was lost.
    requestAnimationFrame(() => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      dragPointerIdRef.current = null;
      setDraggingNode(false);
      livePosRef.current.clear();
      snappedPosRef.current.clear();
      updateAlignGuides([]);
      syncGroupFrames();
    });
  }, [syncGroupFrames, updateAlignGuides]);

  const onCanvasPointerDownCapture = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    const target = event.target as HTMLElement | null;
    if (!target?.closest('.react-flow__node')) return;
    pointerSurfaceRef.current = event.currentTarget;
    dragPointerIdRef.current = event.pointerId;
  }, []);

  const onCanvasPointerRelease = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    const surface = event.currentTarget;
    if (dragPointerIdRef.current === event.pointerId && surface.hasPointerCapture(event.pointerId)) {
      try {
        surface.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may already have been released by the browser.
      }
    }
    dragPointerIdRef.current = null;
    clearStuckDrag();
  }, [clearStuckDrag]);

  // 框选结束后把整集框从选中里摘掉：它包住整列段卡，而框选是"碰到即选"，
  // 被捎上之后一拖就把整集内容全带走了——"框选几段批量摆放"因此失效。
  // 只影响框选：点选/拖动整集框（它的空白处或标题栏）照旧。
  const onSelectionEnd = useCallback(() => {
    setNodes((prev) => prev.some((node) => node.type === 'ep-box' && node.selected)
      ? prev.map((node) => (node.type === 'ep-box' && node.selected ? {...node, selected: false} : node))
      : prev);
    // ★ 框选会把「与选中节点相连的所有边」一并置 selected（React Flow 内置行为），
    //   结果一整屏连线变蓝、还要冒一堆剪刀。框选是"选卡片"，不是"选连线" ——
    //   批量选完后统一把边的选中态清掉，只保留节点选中。
    setEdges((prev) => (prev.some((e) => e.selected) ? prev.map((e) => (e.selected ? {...e, selected: false} : e)) : prev));
    // 框选结束后再同步一次浮动条（框选过程中节点还在变，结束这一帧才是稳定位置）
    syncSelBar();
    syncGroupFrames();
  }, [setNodes, setEdges, syncSelBar, syncGroupFrames]);

  const onNodeClick = useCallback((_: unknown, node: FlowNode) => {
    setSelectedId(node.id);
    setOpenMenu(null);
    // 点击资产节点 → 弹出资产详情面板（即梦式：类型/名称/描述词/大图/音频）
    if (node.type && ['char', 'scene', 'prop', 'voice'].includes(node.type)) {
      const data = node.data as {item?: CanvasAssetItem; kind?: string};
      const kind = node.type === 'char' ? '角色' : node.type === 'scene' ? '场景' : node.type === 'prop' ? '道具' : '音色';
      if (data.item) setAssetDetail({kind, item: data.item});
    }
  }, []);

  // ── 多选浮动操作条（对标即梦）─────────────────────────────────
  const onSelectionChangeHandler = useCallback(({nodes: sel}: {nodes: FlowNode[]}) => {
    setSelectedCount(sel.length);
    // 选区散掉时收起操作条内的小下拉，避免"菜单还在但对象没了"
    if (sel.length < 2) setSelBarMenu(null);
    // ★★ 第 18 轮定稿：**绝不自动展开「编组整理」面板**。
    //   经过一轮来回，真实需求已经清楚：
    //     · 用户第一句「多选后怎么不自动弹出整理工具」指的是**工具条本身**要出现；
    //     · 我误解成"面板要自动展开"，加上自动展开后用户立刻纠正：
    //       「编组整理不要自动展开面板，因为开始是整个工具条都不显示」。
    //   为什么自动展开会让"整个工具条都不显示"：
    //     面板高约 320px，展开时托在工具条上方；若选区贴近画布顶部，
    //     面板会被顶出视口上沿，视觉上工具条像"整条消失了"（只剩一条被裁切的边）。
    //   所以：工具条的显隐只由 syncSelBar 负责，面板展开一律交给用户点击。
    syncSelBar();
    // 编组外框的高亮态跟选中态走（选中整组时框整体提亮）
    syncGroupFrames();
  }, [syncSelBar, syncGroupFrames]);

  // ── 批量连线（对标即梦：多选后一键串起来）────────────────────
  // 两种层叠关系：链式（1→2→3…，做"参考图 → 视频"的流水线）与全互连（每两个之间都连）。
  // 只连媒体卡（备注没有生成语义），自动过滤已存在的连线（含反向），避免重复写 refs。
  const connectSelectedBatch = useCallback((mode: 'chain' | 'mesh') => {
    const sel = rf.getNodes()
      .filter((n) => n.selected && n.id.startsWith(MEDIA_PREFIX))
      .map((n) => n.id);
    if (sel.length < 2) { setToast('至少选中 2 个卡片才能批量连线'); return 0; }
    // 按当前视觉位置排序（从上到下、从左到右），链式连线才符合"看图顺序"
    const ordered = sel
      .map((id) => ({id, node: rf.getNode(id)}))
      .filter((x): x is {id: string; node: FlowNode} => !!x.node)
      .sort((a, b) => (a.node.position.y - b.node.position.y) || (a.node.position.x - b.node.position.x))
      .map((x) => x.id);
    const pairs: [string, string][] = [];
    if (mode === 'chain') {
      for (let i = 0; i < ordered.length - 1; i += 1) pairs.push([ordered[i], ordered[i + 1]]);
    } else {
      for (let i = 0; i < ordered.length; i += 1) {
        for (let j = i + 1; j < ordered.length; j += 1) pairs.push([ordered[i], ordered[j]]);
      }
    }
    // 去重：与已有手动线（含反向）冲突的跳过
    const exists = new Set<string>();
    userEdgesRef.current.forEach((e) => {
      exists.add(`${e.source}->${e.target}`);
      exists.add(`${e.target}->${e.source}`);
    });
    const fresh = pairs.filter(([s, t]) => s !== t && !exists.has(`${s}->${t}`));
    if (fresh.length === 0) { setToast('选中的卡片之间已经有连线了'); return 0; }
    pushHistory(userEdgesRef.current, notesRef.current);
    const created: Edge[] = fresh.map(([s, t]) => ({
      id: nextUserEdgeId(),
      source: s,
      target: t,
      className: 'canvas-edge-user',
      type: 'deletable',
      data: {onDelete: deleteEdgeById},
    }));
    setUserEdges((prev) => [...prev, ...created]);
    // 连线同时写进目标节点的引用参数（连线不只是装饰：视频会真的拿这张图当参考）
    fresh.forEach(([s, t]) => {
      const src = mediaNodesRef.current.find((n) => `media-${n.id}` === s);
      const tgtId = t.replace(/^media-/, '');
      const tgt = mediaNodesRef.current.find((n) => n.id === tgtId);
      if (src && tgt && src.kind !== 'text' && tgt.kind !== 'text') linkRef(tgt.id, src);
    });
    setToast(`已连线 ${created.length} 条`);
    return created.length;
  }, [rf, pushHistory, deleteEdgeById, linkRef, setToast]);

  // ── 拖拽批量连线（对标即梦：选区右缘的圆点按住拖到目标卡片）────────────────
  // 与上面的「串成链 / 两两互连」互补：那两个是把选区块自己连成一团，
  // 这个是"把选中的卡片全部喂给某一张卡"——做「多张分镜参考图 → 一个视频」这类场景时，
  // 不用一张一张拖端口，按住选区那一个点拖过去即可。
  const connectSelectedToTarget = useCallback((targetFlowId: string) => {
    if (!targetFlowId.startsWith(MEDIA_PREFIX)) return 0;
    const sel = rf.getNodes()
      .filter((n) => n.selected && n.id.startsWith(MEDIA_PREFIX) && n.id !== targetFlowId)
      .map((n) => n.id);
    if (sel.length === 0) { setToast('选区里没有可连线的卡片'); return 0; }
    const exists = new Set<string>();
    userEdgesRef.current.forEach((e) => {
      exists.add(`${e.source}->${e.target}`);
      exists.add(`${e.target}->${e.source}`);
    });
    const fresh = sel.filter((s) => !exists.has(`${s}->${targetFlowId}`));
    if (fresh.length === 0) { setToast('选中的卡片已经连到这张卡了'); return 0; }
    pushHistory(userEdgesRef.current, notesRef.current);
    const created: Edge[] = fresh.map((s) => ({
      id: nextUserEdgeId(),
      source: s,
      target: targetFlowId,
      className: 'canvas-edge-user',
      type: 'deletable',
      data: {onDelete: deleteEdgeById},
    }));
    setUserEdges((prev) => [...prev, ...created]);
    fresh.forEach((s) => {
      const src = mediaNodesRef.current.find((n) => `media-${n.id}` === s);
      const tgt = mediaNodesRef.current.find((n) => `media-${n.id}` === targetFlowId);
      if (src && tgt && src.kind !== 'text' && tgt.kind !== 'text') linkRef(tgt.id, src);
    });
    setToast(`已把 ${created.length} 张卡片连到目标`);
    return created.length;
  }, [rf, pushHistory, deleteEdgeById, linkRef, setToast]);

  // 拖拽批量连线的指针生命周期：窗口级监听（指针很快会移出抓手本身，
  // 只挂元素级 onPointerMove 会在鼠标离开圆点的瞬间断掉）。
  // 命中目标用局部变量记，不读 state：setState 是异步的，pointerup 时读到的可能还是上一帧。
  const beginSelLinkDrag = useCallback((event: React.PointerEvent) => {
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    event.stopPropagation();
    const sx = event.clientX - rect.left;
    const sy = event.clientY - rect.top;
    setSelLinkDrag({sx, sy, cx: sx, cy: sy, targetId: null});
    let targetId: string | null = null;
    const onMove = (ev: PointerEvent) => {
      const r = stageRef.current?.getBoundingClientRect();
      if (!r) return;
      const cx = ev.clientX - r.left;
      const cy = ev.clientY - r.top;
      // 命中检测：拖拽线本身 pointer-events:none，elementFromPoint 能穿过它拿到卡片。
      // 不用 rf.getIntersectingNodes：那是世界坐标，还得反算缩放，误差大且没考虑卡片遮挡。
      const el = document.elementFromPoint(ev.clientX, ev.clientY) as Element | null;
      const nodeEl = el?.closest('.react-flow__node[data-id]') as HTMLElement | null;
      const rawId = nodeEl?.getAttribute('data-id') ?? null;
      // 目标必须是媒体卡。允许目标本身也在选区里（选 A/B/C 拖到 C = A→C、B→C，
      // 这是最常见用法）；连到自己由 connectSelectedToTarget 内部排除。
      targetId = rawId && rawId.startsWith(MEDIA_PREFIX) ? rawId : null;
      setSelLinkDrag({sx, sy, cx, cy, targetId});
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
      setSelLinkDrag(null);
      if (targetId) connectSelectedToTarget(targetId);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  }, [connectSelectedToTarget]);

  // 顶部「状态 / 工具」与底部「＋添加」下拉：点外面任何地方自动收起（与图例同一套交互）。
  // 捕获阶段监听：React Flow 的拖拽与各类面板会 stopPropagation，冒泡阶段收不到。
  useEffect(() => {
    if (!openMenu && !addMenuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element;
      if (openMenu === 'status' && !statusMenuRef.current?.contains(target)) setOpenMenu(null);
      else if (openMenu === 'tools' && !toolsMenuRef.current?.contains(target)) setOpenMenu(null);
      if (addMenuOpen && !addMenuRef.current?.contains(target)) setAddMenuOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [openMenu, addMenuOpen]);

  // ★ 右键菜单 / 段菜单：点「菜单以外」的任何地方都关掉。
  //   旧代码只靠 `onPaneClick`（画布空白）关，于是：
  //     · 点顶部工具栏、侧边栏、底部工具条 → 菜单赖在原地
  //     · 点弹窗内部 → 菜单也不走（backdrop 挡着，paneClick 永远收不到）
  //   两条菜单都用"目标是否在菜单内"判定，与浮层同一套语义。
  useEffect(() => {
    if (!contextMenu && !segMenu) return;
    const onDown = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (!t) return;
      if (!t.closest('.canvas-context-menu, .canvas-seg-menu')) {
        setContextMenu(null);
        setSegMenu(null);
      }
    };
    document.addEventListener('pointerdown', onDown, true);
    return () => document.removeEventListener('pointerdown', onDown, true);
  }, [contextMenu, segMenu]);

  const onPaneClick = useCallback(() => {
    if (locateSelectTimerRef.current) {
      window.clearTimeout(locateSelectTimerRef.current);
      locateSelectTimerRef.current = null;
    }
    setSelectedId(null);
    setContextMenu(null);
    setSegMenu(null);
    setOpenMenu(null);
    setAddMenuOpen(false);
    // 点击画布空白处自动收起侧边面板（画布列表 / 镜头顺序），无需点关闭按钮
    setEpListOpen(false);
    setNodes((prev) => prev.some((node) => node.selected)
      ? prev.map((node) => node.selected ? {...node, selected: false} : node)
      : prev);
    setEdges((prev) => prev.some((edge) => edge.selected)
      ? prev.map((edge) => edge.selected ? {...edge, selected: false} : edge)
      : prev);
  }, [setEdges, setNodes]);

  // ★★★ 2026-10-25：`双击空白 = 新建备注` 的两个 dblclick 处理器已全部移除。
  //   用户原话：「文本备注不能双击添加，经常很容易双击空白就添加了越加越多」。
  //
  //   历史上这里其实有**两处**监听同时挂着（一次双击建两条备注）：
  //     ① 挂在 `.react-flow__pane` 上的原生 dblclick → addNote + setToast；
  //     ② document **捕获阶段**的 dblclick（原在下方 6263 行附近），
  //        过滤节点/小地图后也 addNote。
  //   ②是为"帮助面板写了但没实现"补的，补的时候没发现①已经存在 ——
  //   两个 handler 都跑 = "双击一次建两个"，级联让位再错开 24px，
  //   视觉上正是用户说的"越加越多"。
  //
  //   ★ 选择「全部移除」而不是「只留一个」，理由：
  //     双击在画布上本来就是 React Flow 的**缩放手势**，拿它顺带建节点
  //     属于占用既有手势，误触率天然高（拖画布、双击看图都会掉出空备注）。
  //     新建备注仍有明确入口：工具条「添加 → 文本备注」、
  //     空白处右键「添加节点 → 文本备注」、以及选中后 Ctrl+D 复制。


  const onPaneContextMenu = useCallback((event: MouseEvent | React.MouseEvent) => {    event.preventDefault();
    const pos = rf.screenToFlowPosition({x: (event as MouseEvent).clientX, y: (event as MouseEvent).clientY});
    void pos;
    // ★ 有多个节点被选中时，在空白处右键也要弹**批量菜单**。
    //   用户反馈"批量选择节点后为什么不能右键单击弹" —— 根因就是这里：
    //   旧代码只在「右键正好点在某个已选中的节点上」才走批量菜单，
    //   而框选完之后手指通常会落在空白处右键，于是只弹出「添加节点」那套，
    //   批量操作（编组/整理/删除/层级）全都看不见。
    const sel = rf.getNodes().filter((n) => n.selected);
    const batchIds = sel
      .map((n) => n.id)
      .filter((id) => id.startsWith(NOTE_PREFIX) || id.startsWith(MEDIA_PREFIX));
    if (batchIds.length >= 2) {
      setSegMenu(null);
      setContextMenu({x: (event as MouseEvent).clientX, y: (event as MouseEvent).clientY, nodeId: null, nodeType: null, multi: true, selIds: batchIds});
      return;
    }
    setContextMenu({x: (event as MouseEvent).clientX, y: (event as MouseEvent).clientY, nodeId: null, nodeType: null});
  }, [rf]);

  const onNodeContextMenu = useCallback((event: React.MouseEvent, node: FlowNode) => {
    event.preventDefault();
    event.stopPropagation();
    // 段卡右键：只走 segMenu（段专用菜单），不再同时弹通用 contextMenu
    if (node.type === 'segment') {
      setSegMenu({nodeId: node.id, x: event.clientX, y: event.clientY});
      setContextMenu(null);
      return;
    }
    setSegMenu(null);
    // 多选批量菜单：右键命中「当前已选中的节点」且画布上有多选时，
    // 打开批量菜单（整理…/ 删除…/ 复制 / 适应所选 / 取消选择），
    // 而不是把单节点菜单顶掉——这是桌面软件的通行行为。
    const sel = rf.getNodes().filter((n) => n.selected);
    const batchIds = sel
      .map((n) => n.id)
      .filter((id) => id.startsWith(NOTE_PREFIX) || id.startsWith(MEDIA_PREFIX));
    if (sel.length >= 2 && batchIds.length >= 2 && batchIds.includes(node.id)) {
      setContextMenu({x: event.clientX, y: event.clientY, nodeId: node.id, nodeType: node.type ?? null, multi: true, selIds: batchIds});
      return;
    }
    // 右键点在未选中的节点上：
    //   画布上已有多选 → 保留批量菜单（用户多选后随手右键别的卡也想批量操作）
    //   否则收敛选中到该节点（标准桌面行为）
    if (batchIds.length >= 2) {
      setContextMenu({x: event.clientX, y: event.clientY, nodeId: node.id, nodeType: node.type ?? null, multi: true, selIds: batchIds});
      return;
    }
    if (!node.selected) {
      setNodes((prev) => prev.map((n) => (n.id === node.id ? {...n, selected: true} : (n.selected ? {...n, selected: false} : n))));
    }
    setContextMenu({x: event.clientX, y: event.clientY, nodeId: node.id, nodeType: node.type ?? null});
  }, [rf, setNodes]);

  // ★★★ 2026-10-25：**移除**「双击空白新建备注」功能（用户明确要求）。
  //   用户原话：「文本备注不能双击添加，经常很容易双击空白就添加了越加越多」。
  //
  //   这里原来有**两个** dblclick 处理器同时挂着，一次双击会建出**两条**备注：
  //     ① 本段（下方已删除）：挂在 `.react-flow__pane` 上的原生 dblclick，
  //        调 addNote + setToast('已新建备注，双击可编辑')；
  //     ② 紧跟着的那段：在 document **捕获阶段**再监听一次 dblclick，
  //        过滤掉节点/小地图后也调 addNote。
  //   ②是后来为了"帮助面板承诺了但没实现"补的，却忘了①已经存在 ——
  //   两个 handler 都跑，于是"双击一次 = 建两个"，级联让位后又错开 24px，
  //   视觉上就是用户说的"越加越多"。
  //
  //   ★ 为什么不是"只留一个"而是"全部移除"：
  //     双击空白在画布上本来是 React Flow 的**缩放手势**（双击 zoom in），
  //     拿它顺带建节点属于占用既有手势，误触率天然高 ——
  //     用户拖动画布/双击查看时稍慢一点就会掉出一堆空备注。
  //     新建备注本来就有两个明确入口（工具条「添加 → 文本备注」、空白处右键
  //     →「添加节点 → 文本备注」），不缺这一个手势。
  //   ★ 保守起见同时保留 React Flow 的原生双击缩放，什么都不拦。

  const onBeforeDelete = useCallback(async ({nodes: delNodes, edges: delEdges}: {nodes: FlowNode[]; edges: Edge[]}) => {
    const keepNodes = delNodes.filter((node) => node.type === 'note' || node.type === 'media');
    if (delEdges.length > 0) {
      pushHistory(userEdgesRef.current, notesRef.current);
      const userIds = new Set(delEdges.filter((edge) => edge.className === 'canvas-edge-user' || edge.id.startsWith('e-user-')).map((edge) => edge.id));
      if (userIds.size > 0) {
        setUserEdges((prev) => prev.filter((edge) => !userIds.has(edge.id)));
      }
      const autoIds = new Set(delEdges.map((edge) => edge.id).filter((id) => !id.startsWith('e-user-')));
      if (autoIds.size > 0) {
        setDeletedAutoEdgeIds((prev) => {
          const next = new Set(prev);
          autoIds.forEach((id) => next.add(id));
          return next;
        });
      }
      // ★ 删线必须同步清引用参数：Del/Backspace 删的是边，而提示词栏里的缩略图来自 params.refs，
      //   两边不清就会"线没了、图还在"（用户实测反馈的核心问题）。
      const pairs = delEdges.map((e) => ({
        src: e.source?.startsWith(MEDIA_PREFIX) ? e.source.slice(MEDIA_PREFIX.length) : null,
        tgt: e.target?.startsWith(MEDIA_PREFIX) ? e.target.slice(MEDIA_PREFIX.length) : null,
      })).filter((x): x is {src: string; tgt: string} => !!x.src && !!x.tgt && x.src !== x.tgt);
      dropRefsForPairs(pairs);
    }
    // 节点删除统一走「删除确认弹窗」（可勾选要删哪些），不在 Del 键上直接删：
    // 多选时手滑一下就会整批丢失。这里取消掉 React Flow 的默认删除，由弹窗确认后再执行。
    if (keepNodes.length > 0) {
      // React Flow's built-in delete path runs outside our DOM keyboard listener;
      // record here so deleting nodes still has a real Ctrl+Z snapshot.
      if (delEdges.length === 0) pushHistory(userEdgesRef.current, notesRef.current);
      setDeleteTargets(keepNodes.map((node) => ({id: node.id, type: node.type ?? 'media'})));
    }
    return {nodes: [], edges: []};
  }, [pushHistory, dropRefsForPairs]);

  // 撤销 / 重做只恢复「连线 + 备注 + 被删的自动线」，**不回滚媒体节点位置**。
  //
  // 原实现把 mediaNodesRef.current 整份快照也存进历史、撤销时整体 setMediaNodes(snapshot.media)，
  // 于是「连一条线再 Ctrl+Z」会把全画布 22 张卡的位置一起弹回上一次历史点：
  // 表现是点几次撤销，整张画布被平移/缩放到别处（实测坐标整体偏移 0.6~238px 不等），
  // 而且 persistMediaNodes 会把这个错误位置写进数据库，刷新后依然是错的。
  // 位置回滚应由拖动自己的历史项负责，不该被连线操作的历史带偏。
  const restoreSnapshot = useCallback((snapshot: HistorySnapshot, prevMedia: CanvasMediaNode[]) => {
    setUserEdges(snapshot.edges);
    setNotes(snapshot.notes);
    setDeletedAutoEdgeIds(new Set(snapshot.deletedAuto));
    // 连线/内容撤销保留当前坐标；位置型操作（排序、对齐、方向键）必须恢复旧坐标。
    const curById = new Map(prevMedia.map((m) => [m.id, m]));
    const merged = snapshot.media.map((m) => {
      const cur = curById.get(m.id);
      const mergedNode = cur
        ? {
            ...m,
            x: snapshot.positions ? m.x : cur.x,
            y: snapshot.positions ? m.y : cur.y,
            positionUpdatedAt: snapshot.positions ? m.positionUpdatedAt : cur.positionUpdatedAt,
            updatedAt: cur.updatedAt,
          }
        : m;
      // Tag restored positions with the current sequence so a queued position save
      // from the action being undone cannot land after the restore.
      if (snapshot.positions && cur) {
        const seq = ++positionMutationSeqRef.current;
        pendingPositionSavesRef.current.set(cur.id, {x: mergedNode.x, y: mergedNode.y, seq});
      }
      return mergedNode;
    });
    const snapIds = new Set(snapshot.media.map((m) => m.id));
    const removedMedia = snapshot.removeAddedMedia
      ? prevMedia.filter((m) => !snapIds.has(m.id))
      : [];
    // Edge/metadata undo keeps unrelated newer nodes. Node-creation undo removes
    // exactly the cards that did not exist before the action.
    if (!snapshot.removeAddedMedia) {
      prevMedia.forEach((m) => { if (!snapIds.has(m.id)) merged.push(m); });
    }
    const prevById = new Map(prevMedia.map((item) => [item.id, item]));
    const changedNodes = merged.filter((item) => {
      const current = prevById.get(item.id);
      return !current
        || JSON.stringify({...current, x: 0, y: 0, positionUpdatedAt: '', updatedAt: ''})
          !== JSON.stringify({...item, x: 0, y: 0, positionUpdatedAt: '', updatedAt: ''});
    });
    // A position-only undo must update the media source of truth as well.
    // `changedNodes` deliberately ignores x/y when deciding whether to send a
    // metadata save, so using it alone here left mediaNodes at the dragged
    // coordinates: setNodes painted the old position for one frame, then the
    // flowNodes rebuild read mediaNodes and snapped the card back.
    setMediaNodes(
      changedNodes.length > 0 || removedMedia.length > 0 || snapshot.positions
        ? merged
        : prevMedia,
    );
    if (snapshot.positions && snapshot.flowNodes) {
      const beforeById = new Map(prevMedia.map((node) => [`${MEDIA_PREFIX}${node.id}`, node]));
      const restoredPositionRef: Record<string, {x: number; y: number}> = {...positionRef.current};
      for (const node of snapshot.flowNodes) {
        if (!node.id.startsWith(MEDIA_PREFIX) && !node.id.startsWith('asset-')) {
          restoredPositionRef[node.id] = {...node.position};
        }
      }
      positionRef.current = restoredPositionRef;
      setNodes((prev) => prev.map((node) => {
        const target = snapshot.flowNodes?.find((item) => item.id === node.id);
        const restoredMedia = beforeById.get(node.id);
        if (target && !target.id.startsWith(MEDIA_PREFIX)) {
          return {...node, position: target.position};
        }
        if (restoredMedia) {
          const restored = merged.find((item) => item.id === restoredMedia.id);
          if (restored) return {...node, position: {x: restored.x, y: restored.y}};
        }
        return node;
      }));
      requestAnimationFrame(() => syncGroupFrames());
    }
    persistNotes(snapshot.notes);
    if (changedNodes.length > 0) persistMediaNodes(merged, changedNodes);
    if (snapshot.positions) {
      // A drag stop already queued its final coordinates. Cancel that pending
      // write before restoring, otherwise it lands after the undo and the card
      // snaps back to the dragged position.
      if (mediaPositionSaveTimer.current) {
        clearTimeout(mediaPositionSaveTimer.current);
        mediaPositionSaveTimer.current = null;
      }
      pendingPositionSavesRef.current.clear();
      for (const item of merged) {
        const seq = ++positionMutationSeqRef.current;
        pendingPositionSavesRef.current.set(item.id, {x: item.x, y: item.y, seq});
      }
      persistMediaPositions(merged);
    }
    for (const removed of removedMedia) {
      void deleteMediaNodePersistently(removed.id).catch((error: unknown) => {
        setToast(error instanceof Error ? error.message : '撤销新增节点失败');
      });
    }
  }, [deleteMediaNodePersistently, persistNotes, persistMediaNodes, persistMediaPositions, setNodes, setToast, syncGroupFrames]);

  const applyMetadataSnapshot = useCallback((node: CanvasMediaNode) => {
    const next = mediaNodesRef.current.map((item) => item.id === node.id ? node : item);
    mediaNodesRef.current = next;
    setMediaNodes(next);
    beginSave();
    void patchMediaNodeFields(session, node)
      .then(() => {
        finishSave(true);
        broadcastMediaChange([node.id]);
      })
      .catch((error) => {
        finishSave(false);
        setSaveError(error instanceof Error ? error.message : String(error));
      });
  }, [beginSave, broadcastMediaChange, finishSave, session]);

  const undo = useCallback(() => {
    cancelPendingMediaDeletes();
    const metadataEntry = metadataPastRef.current.pop();
    if (metadataEntry) {
      metadataFutureRef.current.push(metadataEntry);
      applyMetadataSnapshot(metadataEntry.before);
      return;
    }
    const past = pastRef.current;
    if (past.length === 0) return;
    const snapshot = past[past.length - 1];
    pastRef.current = past.slice(0, -1);
    futureRef.current = [...futureRef.current, {
      edges: userEdgesRef.current,
      notes: notesRef.current,
      deletedAuto: [...deletedAutoRef.current],
      media: mediaNodesRef.current,
      flowNodes: snapshot.positions ? nodesRef.current : undefined,
      positions: snapshot.positions,
    }];
    restoreSnapshot(snapshot, mediaNodesRef.current);
  }, [applyMetadataSnapshot, cancelPendingMediaDeletes, restoreSnapshot]);

  const redo = useCallback(() => {
    flushPendingMediaDeletes();
    const metadataEntry = metadataFutureRef.current.pop();
    if (metadataEntry) {
      metadataPastRef.current.push(metadataEntry);
      applyMetadataSnapshot(metadataEntry.after);
      return;
    }
    const future = futureRef.current;
    if (future.length === 0) return;
    const snapshot = future[future.length - 1];
    futureRef.current = future.slice(0, -1);
    pastRef.current = [...pastRef.current, {
      edges: userEdgesRef.current,
      notes: notesRef.current,
      deletedAuto: [...deletedAutoRef.current],
      media: mediaNodesRef.current,
      flowNodes: snapshot.positions ? nodesRef.current : undefined,
      positions: snapshot.positions,
    }];
    restoreSnapshot(snapshot, mediaNodesRef.current);
  }, [applyMetadataSnapshot, flushPendingMediaDeletes, restoreSnapshot]);

  // 快捷键：组合键必须**先于裸键**判断——旧实现把 `key === 'c'` 放在最前，
  // 导致 Ctrl+C / Ctrl+V 被"切换鼠标模式"截走，复制粘贴从未真正生效。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (episodeMenu && event.key === 'Escape') {
        setEpisodeMenu(null);
        event.preventDefault();
        return;
      }
      const key = event.key.toLowerCase();
      const mod = event.ctrlKey || event.metaKey;
      // Ctrl+D（复制选中节点）在文本编辑态也保留：点中备注往往会自动聚焦其 textarea，
      // 若一律让位给输入控件，用户会以为"复制节点没反应"。Ctrl+D 在文本框里没有原生语义，不冲突。
      if (mod && key === 'd') {
        const n = duplicateSelectedNodes();
        if (n > 0) { setToast(`已复制 ${n} 个节点`); event.preventDefault(); }
        return;
      }
      const target = event.target as HTMLElement | null;
      // 其余快捷键在输入态让位给浏览器（Ctrl+A 选中文本、Ctrl+C/V 复制粘贴文本等）
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        // The prompt editor is contenteditable and can keep focus after its
        // modal closes. Let the browser handle undo only when the editable
        // element actually has a text edit to restore; otherwise Ctrl+Z must
        // still reach the canvas history.
        if (mod && key === 'z' && target.isContentEditable) {
          const editable = target.closest<HTMLElement>('[contenteditable="true"]') || target;
          const text = (editable.textContent || '').trim();
          const hasNativeUndo = text.length > 0;
          if (!hasNativeUndo) {
            event.preventDefault();
            if (event.shiftKey) redo();
            else undo();
          }
        }
        return;
      }
      if (mod) {
        if (key === 'z' && !event.shiftKey) { event.preventDefault(); undo(); return; }
        if (key === 'z' && event.shiftKey) { event.preventDefault(); redo(); return; }
        if (key === 'x') {
          const cut = cutSelectedNodes();
          if (cut > 0) setToast(`已剪切 ${cut} 个节点，Ctrl+V 粘贴`);
          event.preventDefault();
          return;
        }
        if (key === 'c') {
          // 优先按"节点"复制（帮助面板承诺的能力）；无选中则回退到旧的备注文本复制
          const picked = copySelectedNodes();
          if (picked && (picked.media > 0 || picked.notes > 0)) {
            const parts: string[] = [];
            if (picked.media > 0) parts.push(`${picked.media} 个媒体卡`);
            if (picked.notes > 0) parts.push(`${picked.notes} 条备注`);
            setToast(`已复制 ${parts.join(' · ')}，Ctrl+V 粘贴`);
          } else {
            const copied = nodesRef.current.filter((n) => n.type === 'note').map((n) => n.id.slice('note-'.length));
            const texts = copied.map((id) => notes.find((n) => n.id === id)?.text).filter((t): t is string => Boolean(t));
            if (texts.length > 0) setClipboard(texts);
          }
          event.preventDefault();
          return;
        }
        if (key === 'v') {
          const pasted = pasteNodeClipboard();
          if (pasted > 0) setToast(`已粘贴 ${pasted} 个节点`);
          else if (clipboard.length > 0) {
            // 回退：旧备注文本粘贴
            clipboard.forEach((text, index) => {
              const base = selectedId?.startsWith('note-')
                ? notes.find((n) => n.id === selectedId.slice('note-'.length))
                : undefined;
              addNote((base?.x ?? 0) + 40 + index * 12, (base?.y ?? 0) + 40 + index * 12, text);
            });
          }
          event.preventDefault();
          return;
        }
        // Ctrl+D 已在函数开头统一处理（含文本编辑态），此处不再重复
        if (key === 'a') {
          setNodes((prev) => prev.map((n) => ({...n, selected: true})));
          event.preventDefault();
          return;
        }
        if (key === 'f') {
          event.preventDefault();
          focusSelectionRef.current();
          return;
        }
        if (key === '0') { event.preventDefault(); rf.zoomTo(1, {duration: 200}); return; }
        if (key === '1') { event.preventDefault(); rf.fitView({padding: 0.2, duration: 300}); return; }
        if (key === 's') { event.preventDefault(); setToast('画布自动保存已开启，无需手动保存'); return; }
        return;
      }
      if (key === 'delete' || key === 'backspace') {
        const sel = rf.getNodes().filter((node) => node.selected && (node.type === 'media' || node.type === 'note'));
        if (sel.length > 0) {
          setDeleteTargets(sel.map((node) => ({id: node.id, type: node.type ?? 'media'})));
          event.preventDefault();
        }
        return;
      }
      // —— 以下为裸键 ——
      if (key === 'v') setMouseMode('select');
      else if (key === 'h') setMouseMode('hand');
      else if (key === 'c') setMouseMode('connect');
      // L：一键隐藏/显示所有连线（Logic=连线，和 V/H/C 同一组单键视图快捷键）
      else if (key === 'l') { event.preventDefault(); toggleEdgesHidden(); }
      else if (key === 'f' && event.shiftKey) {
        // Shift+F：聚焦已选卡片（自适应比例；F 是适应全部）
        event.preventDefault();
        focusSelectionRef.current();
      }
      else if (key === 'f') { event.preventDefault(); rf.fitView({padding: 0.2}); }
      else if (key === '?') { event.preventDefault(); setHelpOpen(true); }
      else if (event.code === 'Space') { event.preventDefault(); setPanning(true); }
      else if (key === 'arrowup' || key === 'arrowdown' || key === 'arrowleft' || key === 'arrowright') {
        const step = event.shiftKey ? 10 : 1;
        const dx = key === 'arrowleft' ? -step : key === 'arrowright' ? step : 0;
        const dy = key === 'arrowup' ? -step : key === 'arrowdown' ? step : 0;
        if (nudgeSelected(dx, dy) > 0) event.preventDefault();
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code === 'Space') setPanning(false);
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('pointerdown', flushPendingMediaDeletes, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('pointerdown', flushPendingMediaDeletes, true);
    };
  }, [undo, redo, cutSelectedNodes, copySelectedNodes, pasteNodeClipboard, clipboard, addNote, notes, selectedId, episodeMenu,
      duplicateSelectedNodes, nudgeSelected, setNodes, rf, setToast, toggleEdgesHidden, flushPendingMediaDeletes]);

  // 有效段列表：有 segments.json 用真实分段；无分段时兜底分组。
  // 编号体系必须与镜头自带 segment_id 对齐（时间线/搜索/定位都按 segment_id 反查段卡节点 id）：
  // 旧兜底用 1 基连续编号，而分镜产物是 0 基，导致定位 id 对不上（点了没反应）。
  // 现在优先按镜头自带 segment_id 聚合；只有全部镜头同一段号（真未分段）才按每 4 镜切分。
  const effectiveSegments = useMemo(() => {
    if (!canvas) return [];
    if (canvas.segments.length > 0) {
      return currentEp ? canvas.segments.filter((segment) => segment.ep_id === currentEp) : canvas.segments;
    }
    // 兜底分组（纯函数 fallbackSegmentsOf）：带 ep_id、0 基段号、跨集同段号不合并
    return fallbackSegmentsOf(canvas.shots)
      .filter((group) => !currentEp || group.ep_id === currentEp)
      .map((group): CanvasSegment => {
      const members = canvas.shots.filter((s) => group.shot_idxs.includes(s.idx));
      const aligned = members.length > 0 && members.every((s) => (typeof s.segment_id === 'number' ? s.segment_id : 0) === group.segment_id);
      return {
        segment_id: group.segment_id,
        ep_id: group.ep_id,
        title: aligned
          ? `第${group.segment_id + 1}段 · 自动聚合（待 segments.json）`
          : '自动分组 · 该集尚未生成分段',
        duration: null,
        shot_idxs: group.shot_idxs,
        scene_id: null,
        asset_binding: null,
        end_state: null,
      };
      });
  }, [canvas, currentEp]);

  // 段卡节点 id：整剧总览时带集前缀防跨集冲突；单集模式保持兼容（持久化布局/搜索/定位引用同一 id）
  const segNodeId = useCallback((seg: CanvasSegment): string => {
    return currentEp ? `seg-${seg.segment_id}` : `seg-${seg.ep_id || 'ep00'}-${seg.segment_id}`;
  }, [currentEp]);

  const bindReference = useCallback((assetKey: string) => {
    if (pickShot === null) return;
    // 画布中没有独立 shot 节点（镜头嵌在段卡内），边必须锚定到所属段卡，
    // 否则 source/target 悬空，React Flow 不渲染该边（旧实现：点了没反应）。
    const seg = canvas
      ? effectiveSegments.find((s) => segShotIdxsOf(s, canvas.shots, {overview: currentEp === null}).includes(pickShot))
      : undefined;
    const targetId = seg ? segNodeId(seg) : `shot-${pickShot}`;
    const id = `e-user-${pickShot}-${assetKey}`;
    // pushHistory 放在 updater 外：React StrictMode 会双调用 updater，
    // 在 updater 内 push 历史会重复入栈（旧实现的撤销行为异常）。
    pushHistory(userEdgesRef.current, notesRef.current);
    setUserEdges((prev) => (
      prev.some((edge) => edge.id === id)
        ? prev
        // 与手动连线对齐：deletable 类型 + onDelete，参考选择器建立的边同样可经 ✕ 删除；
        // 方向与自动边一致（资产 → 段卡）
        : [...prev, {id, source: assetKey, target: targetId, type: 'deletable', className: 'canvas-edge-user', data: {onDelete: deleteEdgeById}}]
    ));
    setPickShot(null);
  }, [pickShot, canvas, effectiveSegments, currentEp, segNodeId, pushHistory, deleteEdgeById]);

  // 自动边（段间连续链 + 段→资产绑定）——按分段显示：段卡为主节点，镜头嵌在段内
  const flowEdges = useMemo((): Edge[] => {
    if (!canvas) return [];
    const edgeOut: Edge[] = [];
    const sortedSegs = [...effectiveSegments].sort((a, b) => {
      const epA = a.ep_id || '', epB = b.ep_id || '';
      if (currentEp === null && epA !== epB) return epA.localeCompare(epB, undefined, {numeric: true});
      return a.segment_id - b.segment_id;
    });
    // 段内镜头解析：优先 seg.shot_idxs，回退 shot.segment_id 分组（总览模式追加 ep_id 守卫，防跨集同段号串镜）
    const segShotIdxs = (seg: CanvasSegment): number[] => segShotIdxsOf(seg, canvas.shots, {overview: currentEp === null});
    // 段间连续链：段 1 → 段 2 → …（整剧总览按集内段序连线，跨集不连——idx 冲突靠集前缀 id 隔离）
    for (let i = 0; i < sortedSegs.length - 1; i += 1) {
      if (true) continue; // ← 段间连续链停用（段卡已停用）
      const a = sortedSegs[i], b = sortedSegs[i + 1];
      if (currentEp === null && (a.ep_id || 'ep00') !== (b.ep_id || 'ep00')) continue;
      edgeOut.push({
        id: `e-chain-${segNodeId(a)}-${segNodeId(b)}`,
        source: segNodeId(a),
        target: segNodeId(b),
        className: 'canvas-edge-chain',
        type: 'deletable',
        data: {onDelete: deleteEdgeById},
      });
    }
    // 段 → 资产绑定（逐资产单独连线：每个绑定资产一条实线，指向该资产在生成组内对应的行把手）
    // 自动连线是核心可见性：资产节点保留在画布左侧四列，连线必须画出来——否则用户看不到资产绑到了哪一段。
    const assetNameByIdx: Record<string, Map<number, string>> = {
      char: new Map(canvas.characters.map((item, i) => [i, item.name])),
      scene: new Map(canvas.scenes.map((item, i) => [i, item.name])),
      prop: new Map(canvas.props.map((item, i) => [i, item.name])),
      voice: new Map(canvas.voices.map((item, i) => [i, item.name])),
    };
    const shotByIdx = new Map(canvas.shots.map((s) => [s.idx, s]));
    sortedSegs.forEach((seg) => {
      const shotIdxs = segShotIdxs(seg);
      const boundKeys = new Set<string>();
      shotIdxs.forEach((shotIdx) => {
        const shot = shotByIdx.get(shotIdx);
        if (!shot) return;
        (['char', 'scene', 'prop', 'voice'] as const).forEach((kind) => {
          const idxList = shot[`${kind}_idxs`] as number[] | undefined;
          (idxList ?? []).forEach((ci) => {
            if (assetNameByIdx[kind].has(ci)) boundKeys.add(`${kind}-${ci}`);
          });
        });
      });
      boundKeys.forEach((key) => {
        if (true) return; // ← 段→原生资产绑定边停用（资产分组已停用，媒体引用连线承担资产关联）
        const [kind, idx] = key.split('-');
        edgeOut.push({
          id: `e-seg-${segNodeId(seg)}-${kind}-${idx}`,
          source: `asset-${kind}-${idx}`,
          target: segNodeId(seg),
          hidden: collapsedGroups.has(kind),
          className: 'canvas-edge-auto',
          type: 'deletable',
          data: {onDelete: deleteEdgeById},
        });
      });
    });
    // 媒体节点引用连线：视频/音频节点按 refIds 连到对应源节点（source=源 → target=消费节点）
    // 方向与段→资产自动边一致：资产图/音色 → 消费方；refIds 里存的是源节点的 mn.id。
    // ★ 之前这里只放行 target.kind === 'image'，音频（音色）引用被整类跳过 →
    //   视频卡明明引用了音色，画布上却一根线都没有（用户反馈"音色节点没有连线上"）。
    const mediaById = new Map(mediaNodes.map((mn) => [mn.id, mn]));
    mediaNodes.forEach((mn) => {
      if (mn.kind !== 'video' && mn.kind !== 'audio') return;
      const seen = new Set<string>();
      (mn.refIds ?? []).forEach((refId) => {
        if (seen.has(refId)) return;
        seen.add(refId);
        const target = mediaById.get(refId);
        if (!target) return;
        // 音频卡只吃图片（角色图）；视频卡吃图片 + 音色
        // ★ 第 20 轮结论（不要再"顺手放开 video→video 自动线"）：
        //   数据层往上加自动线会凭空多出用户从未画过的线（实测 +9 条），
        //   且一旦与用户手动画的同端点线重叠，剪断其中一条另一条还在 —— 症状还是"剪不断"。
        //   用户报的那条线经确认是**手动边**（自动边每次渲染都带新的 onDelete，不可能失效），
        //   所以这里维持原判定，问题在手动边的 onDelete 丢失（见 CANVAS_EDGE_TYPES 上方注释）。
        const ok = target.kind === 'image' || (mn.kind === 'video' && target.kind === 'audio');
        if (!ok) return;
        edgeOut.push({
          id: `e-media-${mn.id}-${refId}`,
          source: `media-${refId}`,
          target: `media-${mn.id}`,
          // 音色线单独着色：否则和资产图线同色，看图上分不出哪根是音色
          className: target.kind === 'audio' ? 'canvas-edge-auto canvas-edge-voice' : 'canvas-edge-auto',
          type: 'deletable',
          data: {onDelete: deleteEdgeById},
        });
      });
    });
    return edgeOut;
  }, [canvas, effectiveSegments, deleteEdgeById, collapsedGroups, currentEp, segNodeId, mediaNodes]);

  const allEdges = useMemo(() => {
    // ★ 第 20 轮：userEdges 里可能混着「被 JSON 洗过」的旧边（data 只剩 {}），
    //   渲染前统一 hydrate 一遍，保证每一条都带 type/className 和可用的 onDelete 转发。
    return [...flowEdges.filter((edge) => !deletedAutoEdgeIds.has(edge.id)), ...userEdges.map(hydrateUserEdge)];
  }, [flowEdges, userEdges, deletedAutoEdgeIds]);

  useEffect(() => {
    const edgePairs = new Set(allEdges.map((edge) => `${edge.source}\x1f${edge.target}`));
    const missing: Edge[] = [];
    for (const node of mediaNodes) {
      if (node.kind !== 'video' && node.kind !== 'audio') continue;
      const refs = [...(node.params?.refs || []), ...voiceRefList(node.params || {})];
      for (const ref of refs) {
        const sourceId = String(ref.nodeId || '');
        if (!sourceId) continue;
        const source = mediaNodes.find((item) => item.id === sourceId);
        if (!source) continue;
        const ok = source.kind === 'image' || (node.kind === 'video' && source.kind === 'audio');
        if (!ok) continue;
        const sourceFlow = `media-${sourceId}`;
        const targetFlow = `media-${node.id}`;
        if (edgePairs.has(`${sourceFlow}\x1f${targetFlow}`) || edgePairs.has(`${targetFlow}\x1f${sourceFlow}`)) continue;
        edgePairs.add(`${sourceFlow}\x1f${targetFlow}`);
        missing.push({
          id: nextUserEdgeId(),
          source: sourceFlow,
          target: targetFlow,
          type: 'deletable',
          className: 'canvas-edge-user',
          data: {onDelete: deleteEdgeById},
        } as Edge);
      }
    }
    if (missing.length > 0) setUserEdges((prev) => [...prev, ...missing]);
  }, [allEdges, mediaNodes, deleteEdgeById]);

  // ── 选中节点的连线高亮（对标即梦）────────────────────────────────────
  // 即梦的行为：选中一张卡片，**所有与它相连的线**都点亮流光，一眼看清"这张卡吃谁、喂谁"。
  // 这里的做法不是在 edge 数据上打标记（那会让 allEdges 依赖 selectedCount，每次框选
  // 都重算 60+ 条边对象，React Flow 要重建整张边图，拖动掉帧），而是：
  //   1) 把「被选中节点 id 集合」写进 React Flow 容器的 CSS 变量 / class；
  //   2) 边组件自己读 useStore 里 endpoints 的 selected 状态，命中就挂 is-linked。
  // 这样选择变化只触发边组件内部重渲染，不重建边数组。
  // 见 CanvasEdgeWithDelete 里的 `linkedSelected`。

  // 点击卡片节点 → 弹窗编辑（提示词 + 设置参数 + 生成）
  // 只存 id，弹窗里的 node 从 mediaNodes 派生，保证永远是"最新数据"。
  // 旧写法把整份节点快照存进 state：改参数后栏里的 pill / 提示词停在旧值
  // （选了比例 pill 不变、打字丢字符），只有重开弹窗才刷新。
  const [editNodeId, setEditNodeId] = useState<string | null>(null);
  const editNode = useMemo(
    () => mediaNodes.find((n) => n.id === editNodeId) ?? null,
    [mediaNodes, editNodeId],
  );
  // 镜像给 hook / 事件处理读最新值（它们不能依赖 state 闭包）
  editNodeRef.current = editNodeId;  // 弹窗锚点：点击卡片时记录卡片视口位置，弹窗直接贴在卡片正下方（不做全屏浮层）
  const [modalAnchor, setModalAnchor] = useState<{x: number; y: number; w: number; h: number} | null>(null);
  // 编组查表：flow id → 该组全部成员（只收 ≥2 人的组，单人组没意义）
  const groupMap = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const g of groups) {
      if (g.members.length < 2) continue;
      for (const m of g.members) map.set(m, g.members);
    }
    return map;
  }, [groups]);
  // 给命令式回调读最新编组（不能直接依赖 groupMap，否则 openMediaModal 每编组一次就换身份，
  // 下游 flowNodes 依赖 → 整张画布重建）
  const groupMapRef = useRef(groupMap);
  groupMapRef.current = groupMap;
  const groupMembersOf = useCallback((flowId: string) => groupMapRef.current.get(flowId) ?? null, []);
  // 编组变化即落盘（每次编/解组都持久化，不用等拖完）
  const didLoadGroupsRef = useRef(false);
  useEffect(() => {
    if (!didLoadGroupsRef.current) { didLoadGroupsRef.current = true; return; }
    try {
      const raw = localStorage.getItem(layoutKey);
      const prev = raw ? JSON.parse(raw) : {};
      localStorage.setItem(layoutKey, JSON.stringify({...prev, groups}));
    } catch { /* 静默 */ }
  }, [groups, layoutKey]);
  // 把一批 flow id 置为唯一选中集（编组后"按组拖动"靠它：选中扩散 → 原生多选拖动）
  const selectFlowIds = useCallback((ids: string[]) => {
    const set = new Set(ids);
    setNodes((prev) => {
      let changed = false;
      const next = prev.map((n) => {
        const want = set.has(n.id);
        if (n.selected === want) return n;
        changed = true;
        return {...n, selected: want};
      });
      return changed ? next : prev;
    });
  }, [setNodes]);

  // ★ 按压即「预备整组拖动」：在 pointerdown 阶段把整组写进 React Flow 的 **store**。
  //
  //   为什么必须写 store 而不是只 setNodes：
  //   d3-drag 的 startDrag() 先调 handleNodeClick(id)，若该节点在 store 里未选中，
  //   会 addSelectedNodes([id]) 走非多选分支 → 选中集合被收敛成只剩这一张；
  //   紧接着 getDragItems() 只遍历 node.selected 的节点（读的是 nodeLookup）。
  //   所以 setState 那条路（React 调度，晚一拍）根本赶不上同一帧的 dragstart。
  //   这里直接改 nodeLookup 的 selected 位，store 内的视角当场就是"整组都选中"，
  //   getDragItems 于是收集到全组成员 → 一次拖动整组位移。
  //   同时 setNodes 同步 React 侧（渲染 class、操作条计数都要看它）。
  const pressGroup = useCallback((members: string[]) => {
    if (members.length < 2) return;
    const want = new Set(members);
    // ① 先改 store 内部选中位（关键，赶在 d3 dragstart 之前）
    try {
      const st = storeApi.getState();
      const lookup = st.nodeLookup;
      // 组外节点：若被选中则取消（避免"拖整组却把别处的也带走"）
      for (const [id, internal] of lookup) {
        const shouldSelect = want.has(id);
        if (!!internal.selected !== shouldSelect) {
          (internal as {selected?: boolean}).selected = shouldSelect;
        }
      }
    } catch { /* store 未就绪时静默，setNodes 仍会生效 */ }
    // ② 再同步 React 侧（渲染与计数）
    setNodes((prev) => {
      let changed = false;
      const next = prev.map((n) => {
        const shouldSelect = want.has(n.id);
        if (!!n.selected === shouldSelect) return n;
        changed = true;
        return {...n, selected: shouldSelect};
      });
      return changed ? next : prev;
    });
  }, [setNodes, storeApi]);
  // ★ 此刻 pressGroup 已定义完成，可以安全赋给上面那个 ref（避开 TDZ）。
  pressGroupRef.current = pressGroup;

  // 打开弹窗时：把画布上连到它的资产图/音色预填进 params.refs / voiceRef
  const openMediaModal = useCallback((node: CanvasMediaNode) => {
    // ★ 打开弹窗时必须把该节点置为选中（用户反馈"单击节点弹出输入框了却没有流光"）。
    //   根因：卡片的 onClick 里做了 e.stopPropagation()，React Flow 收不到这次点击，
    //   node.selected 永远是 false；而连线流光（is-linked）是靠端点 selected 驱动的
    //   （见 CanvasEdgeWithDelete 的 useStore），于是"点开了弹窗 = 选中了卡片"这条直觉不成立。
    //
    // ★★ 但**不能无条件收敛成单选**：编组后点组内任一卡，onClick 会先把整组选上
    //    （groupMembers → onSelectGroup），紧接着这里若把别人都取消选中，
    //    整组选中瞬间被打回单选 → "按组拖动"彻底失效（第 12 轮 D3 就是栽在这）。
    //    规则：本卡已在某个组里且该组成员的选中状态已就位 → 原样保留；否则才收敛为单选。
    setNodes((prev) => {
      const flowId = `media-${node.id}`;
      const members = groupMembersOf(flowId);
      const groupReady = !!members && members.filter((m) => prev.find((n) => n.id === m)?.selected).length >= 2;
      let changed = false;
      const next = prev.map((n) => {
        if (n.id === flowId) {
          if (n.selected) return n;
          changed = true;
          return {...n, selected: true};
        }
        if (!n.selected) return n;
        // 组已就位时，组内其它成员保持选中；组外的才取消
        if (groupReady && members!.includes(n.id)) return n;
        changed = true;
        return {...n, selected: false};
      });
      return changed ? next : prev;
    });
    // 文本节点也接入：AI 生成的上下文可以来自相连的其它文本/资产节点
    if (node.kind === 'video' || node.kind === 'audio' || node.kind === 'text') {
      const refs = [...(node.params?.refs || [])];
      const nodeFlowId = `media-${node.id}`;
      // ★ 手动连线可能是「源→目标」也可能是「目标→源」（用户从任一端的把手拖），
      //   只查 e.target === 自己会漏掉一半，表现为"明明连了线，弹窗里没有引用"。
      const linked = allEdges
        .filter((e) => (e.target === nodeFlowId && e.source.startsWith(MEDIA_PREFIX))
          || (e.source === nodeFlowId && e.target.startsWith(MEDIA_PREFIX)))
        .map((e) => ({
          incoming: e.target === nodeFlowId,
          src: mediaNodes.find((n) => `media-${n.id}` === (e.target === nodeFlowId ? e.source : e.target)),
        }))
        // 反向边只用于兼容“从视频把手拖向图片/音频”的画法。
        // 视频→下一段视频是时间顺序，不是下一段反向喂给上一段。
        .filter(({incoming, src}) => !!src && (incoming || src.kind !== 'video'))
        .map(({src}) => src as CanvasMediaNode);
      let voiceList = voiceRefList(node.params || {});
      const activeModel = modelCatalog?.models.find((item) =>
        item.id === node.params?.model
        || item.remoteModel === node.params?.model
        || item.id === node.params?.ttsModel
      );
      const linkedLimit = modelMaxRefs(activeModel);
      const audioLimitValue = activeModel?.limits?.maxAudios;
      const audioLimit = Number(
        audioLimitValue === undefined || audioLimitValue === null
          ? 3
          : audioLimitValue,
      );
      let dirty = false;
      for (const src of linked) {
        if (src.id === node.id) continue;
        // ★★ 第 17 轮修（用户原话「图片和音色引用显示了，那文本和视频没有引用显示」）：
        //   旧逻辑这里是 `src.kind === 'image' || takesText`，其中 takesText 只在
        //   目标是文本节点时才可能为真 —— 于是视频卡连文本框 / 连前段视频**全被丢弃**，
        //   弹窗素材行永远只有图片。现在视频卡接受 image / text / video 三类，
        //   音频仍走 voiceList（不进 refs，避免与音色语义混在一起）。
        //   注意这段在 `node.kind === 'video'` 分支内，node.kind 已被收窄，
        //   不要再写 node.kind === 'image' 之类的比较（tsc 会报 no overlap）。
        if (src.kind !== 'audio'
          && !refs.some((r) => r.nodeId === src.id)
          && refs.length + voiceList.length < linkedLimit) {
          refs.push({nodeId: src.id, label: src.title || src.kind, url: src.mediaUrl});
          dirty = true;
        }
        // 音色：视频卡吃 audio；音频卡自身也可能被连了角色图（音色参考图）
        // ★★ 第 17 轮修：旧代码 `voiceRef = {...}` 是覆盖写 —— 连了 4 个音频节点，
        //   这里循环 4 次只留下最后一个，与用户反馈「连 4 个只显示 1 个引用」完全对应。
        //   改为逐条追加去重，一条都不能少。
        if (src.kind === 'audio'
          && node.kind === 'video'
          && !voiceList.some((v) => v.nodeId === src.id)
          && refs.length + voiceList.length < linkedLimit
          && voiceList.length < audioLimit) {
          voiceList = [...voiceList, {nodeId: src.id, label: src.title || src.kind, url: src.mediaUrl}];
          dirty = true;
        }
      }
      if (dirty) {
        const merged = withVoiceRefs({...node.params, refs}, voiceList);
        updateMediaNode(node.id, {params: merged});
      }
    }
    // 弹窗贴卡片下方：读卡片实际渲染位置（滚动后 getBoundingClientRect 是最新的）
    const cardEl = document.querySelector(`[data-id="${CSS.escape(`media-${node.id}`)}"] .canvas-media-card`) as HTMLElement | null;
    setModalAnchor(cardEl ? (() => {
      const r = cardEl.getBoundingClientRect();
      return {x: r.left, y: r.top, w: r.width, h: r.height};
    })() : null);
    setEditNodeId(node.id);
  }, [allEdges, mediaNodes, modelCatalog, updateMediaNode, setNodes, groupMembersOf]);

  const onGenerateMediaNode = useCallback((node: CanvasMediaNode) => {
    if (node.status === 'generating') {
      setToast('该节点正在生成，请到任务中心查看进度');
      return false;
    }
    if (!node.prompt.trim()) {
      setToast('请先填写提示词');
      return false;
    }
    const kindLabel: Record<CanvasMediaNodeKind, string> = {text: '一段文本', image: '一张图片', audio: '一段音频', video: '一段视频'};
    const candidates = (modelCatalog?.models || []).filter(
      (item) => item.enabled !== false && item.capability === node.kind,
    );
    if (modelCatalog && candidates.length === 0) {
      setToast('当前类型没有可用模型，请在「模型接口」中启用或添加');
      return false;
    }
    const storedModel = canonicalModelId(node.params?.model || node.params?.ttsModel);
    const activeModel = candidates.find((item) => item.id === storedModel || item.remoteModel === storedModel)
      || candidates.find((item) => item.providerId === node.params?.provider)
      || candidates[0];
    const provider = modelCatalog?.providers.find((item) => item.id === activeModel?.providerId);
    if (provider && !provider.has_api_key) {
      setToast(`供应商「${provider.name}」尚未配置 API Key`);
      return false;
    }
    const promptLimit = Number(activeModel?.limits?.maxPromptChars || 0);
    if (promptLimit > 0 && node.prompt.length > promptLimit) {
      setToast(`提示词超过当前模型上限（${promptLimit} 字符）`);
      return false;
    }
    let params: CanvasMediaParams = activeModel
      ? inheritedGenerationParams(node.params || {}, activeModel)
      : {...(node.params || {})};
    if (activeModel) {
      if (node.kind === 'image' && !params.size) {
        params.size = CLARITY_SIZES[String(params.clarity || '1.5K')]?.[String(params.ratio || '1:1')]
          || activeModel.defaultParams?.size;
      }
    }
    const references = evaluateReferenceLimits(
      {...node, params},
      mediaNodes,
      allEdges,
      activeModel || null,
    );
    params = references.node.params || params;
    if (references.issue) {
      setToast(references.message);
      return false;
    }
    const dependencyBlockers = mediaDependencyBlockers(references.node, mediaNodes);
    if (dependencyBlockers.length > 0) {
      const names = dependencyBlockers
        .slice(0, 4)
        .map((item) => `${item.title}（${item.reason}）`)
        .join('、');
      setToast(`还有 ${dependencyBlockers.length} 项引用未就绪：${names}${dependencyBlockers.length > 4 ? '…' : ''}`);
      return false;
    }
    const generationStartedAt = new Date().toISOString();
    const clientOperationId = (
      globalThis.crypto?.randomUUID?.()
      || `canvas-operation-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
    );
    // This action sends the latest prompt/params atomically with /generate.
    // Drop any older debounced patch for the same node so it cannot race that
    // request with a stale baseUpdatedAt.
    pendingMediaPatchesRef.current.delete(node.id);
    if (pendingMediaPatchesRef.current.size === 0 && mediaSaveTimer.current) {
      clearTimeout(mediaSaveTimer.current);
      mediaSaveTimer.current = null;
    }
    setGenerationBusyIds((prev) => {
      if (prev.has(node.id)) return prev;
      const next = new Set(prev);
      next.add(node.id);
      return next;
    });
    updateMediaNode(node.id, {
      status: 'generating',
      lastError: undefined,
      generationStartedAt,
      generationFinishedAt: undefined,
      providerTaskStatus: 'starting',
      providerTaskPhase: '提交中',
      providerTaskProgress: 0,
      params,
    }, {persist: false});
    const startedLabel = node.status === 'done' ? '重新生成已连接供应商' : '正在连接供应商';
    setToast(`${startedLabel}：${kindLabel[node.kind]}，结果会自动回写`);
    void (async () => {
      try {
        // The generation endpoint applies this explicit prompt/params snapshot
        // against the latest server node. A separate optimistic patch used a
        // stale updatedAt and falsely reported a multi-tab conflict.
        const generationPromise = generateCanvasNodeNow(session, node.id, {
          prompt: node.prompt,
          params,
          clientOperationId,
        });
        const result = await generationPromise;
        void getGenerationTasks(session, 80, currentEp)
          .then((tasks) => setGenerationTasks(tasks))
          .catch(() => {});
        void getCanvasMediaNodes(session, currentEp)
          .then((remoteNodes) => {
            const remote = remoteNodes.find((item) => item.id === node.id);
            if (remote) updateMediaNode(node.id, remote, {persist: false});
          })
          .catch(() => {});
        if (result.deferred) {
          setToast(result.accepted
            ? `${kindLabel[node.kind]}已提交，后台生成中，任务中心会实时更新`
            : `已有生成任务运行中：${result.reason || '正在等待供应商返回'}`);
        } else {
          if (node.kind === 'image' || node.kind === 'audio') {
            updateMediaNode(node.id, {
              promptStale: false,
              promptStaleReason: '',
              sourcePromptUpdatedAt: undefined,
            });
          }
          setToast(`${kindLabel[node.kind]}生成完成，版本已更新`);
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setGenerationBusyIds((prev) => {
          if (!prev.has(node.id)) return prev;
          const next = new Set(prev);
          next.delete(node.id);
          return next;
        });
        updateMediaNode(node.id, {
          status: 'failed',
          lastError: message,
          generationFinishedAt: new Date().toISOString(),
          providerTaskStatus: 'failed',
          providerTaskPhase: '生成失败',
        }, {persist: false});
        setToast(`生成请求发送失败：${message}`);
      }
    })();
    return true;
  }, [session, currentEp, updateMediaNode, allEdges, mediaNodes, modelCatalog, setToast]);

  // 段级绑定资产（段内镜头绑定的并集）——直接由 shots 索引构建，供段卡 chips
  const segBoundAssets = useMemo(() => {
    const map = new Map<number, {key: string; name: string; kind: string; image_url?: string; audio_url?: string}[]>();
    if (!canvas) return map;
    const lookup: {kind: string; name: string; image_url?: string; audio_url?: string}[] = [
      ...canvas.characters.map((item, i) => ({kind: '角色', name: item.name, image_url: item.image_url})),
      ...canvas.scenes.map((item, i) => ({kind: '场景', name: item.name, image_url: item.image_url})),
      ...canvas.props.map((item, i) => ({kind: '道具', name: item.name, image_url: item.image_url})),
      ...canvas.voices.map((item, i) => ({kind: '音色', name: item.name, audio_url: item.audio_url})),
    ];
    const kindPrefix = ['char', 'scene', 'prop', 'voice'];
    const offsetOf = (kindIdx: number) => [0, canvas.characters.length, canvas.characters.length + canvas.scenes.length, canvas.characters.length + canvas.scenes.length + canvas.props.length][kindIdx];
    effectiveSegments.forEach((seg) => {
      const shotIdxs = segShotIdxsOf(seg, canvas!.shots, {overview: currentEp === null});
      const byKey = new Map<string, {key: string; name: string; kind: string; image_url?: string; audio_url?: string}>();
      shotIdxs.forEach((shotIdx) => {
        const shot = canvas.shots.find((s) => s.idx === shotIdx);
        if (!shot) return;
        const pushIdx = (kindIdx: number, idx: number) => {
          const flat = offsetOf(kindIdx) + idx;
          const info = lookup[flat];
          if (!info) return;
          const key = `${kindPrefix[kindIdx]}-${idx}`;
          if (!byKey.has(key)) byKey.set(key, {key, ...info});
        };
        (shot.char_idxs ?? []).forEach((ci) => pushIdx(0, ci));
        (shot.scene_idxs ?? []).forEach((si) => pushIdx(1, si));
        (shot.prop_idxs ?? []).forEach((pi) => pushIdx(2, pi));
        (shot.voice_idxs ?? []).forEach((vi) => pushIdx(3, vi));
      });
      map.set(seg.segment_id, [...byKey.values()]);
    });
    return map;
  }, [canvas, effectiveSegments, currentEp]);

  const visibleMediaNodes = useMemo(() => {
    if (
      mediaNodes.length <= 400
      || largeCanvasOverview
      || !largeCanvasVisibleBounds
    ) {
      return mediaNodes;
    }
    return filterMediaNodesInBounds(
      mediaNodes,
      largeCanvasVisibleBounds,
      (node) => ({
        w: MEDIA_KIND_META[node.kind].w,
        h: MEDIA_KIND_META[node.kind].h + 44,
      }),
    );
  }, [largeCanvasOverview, largeCanvasVisibleBounds, mediaNodes]);
  const mediaRenderMode: 'overview' | 'compact' | 'full' = zoomPct < 35
    ? 'overview'
    : zoomPct < 65
      ? 'compact'
      : 'full';
  const flowNodes = useMemo((): FlowNode[] => {
    if (!canvas || largeCanvasOverview) return [];
    const out: FlowNode[] = [];
    const onZoom = (url: string) => setZoomTarget(url);
    const boundMap = segBoundAssets;

    // 按分段显示：资产生成组在前（角色/场景/道具/音色 4 组），段卡列排在最右侧
    const groupX: Record<string, number> = {char: 0, scene: COL_X, prop: COL_X * 2, voice: COL_X * 3};
    const segX = COL_X * 4;

    const groupDefs: {kind: 'char' | 'scene' | 'prop' | 'voice'; label: string; items: CanvasAssetItem[]}[] = [
      {kind: 'char', label: '角色', items: canvas.characters},
      {kind: 'scene', label: '场景', items: canvas.scenes},
      {kind: 'prop', label: '道具', items: canvas.props},
      {kind: 'voice', label: '音色', items: canvas.voices},
    ];
    // 资产搜索过滤：按名称/描述即时过滤；空组不建节点。过滤只影响显示，节点身份用原始索引保持稳定（连线/徽章不错位）
    const q = assetQuery.trim().toLowerCase();
    const filteredGroups = groupDefs.map((g) => {
      const items: {item: CanvasAssetItem; origIdx: number}[] = [];
      g.items.forEach((item, idx) => {
        if (!q || item.name.toLowerCase().includes(q) || (item.description ?? '').toLowerCase().includes(q)) items.push({item, origIdx: idx});
      });
      return {...g, items};
    });
    // 资产引用统计（卡片徽章）：每类资产被多少镜头引用、参与镜头是否已出片
    const assetUse: Record<string, {refs: number; clips: number}> = {};
    const kindPrefix = ['char', 'scene', 'prop', 'voice'];
    canvas.shots.forEach((s) => {
      const bump = (kindIdx: number, idx: number) => {
        const key = `${kindPrefix[kindIdx]}-${idx}`;
        const entry = (assetUse[key] ??= {refs: 0, clips: 0});
        entry.refs += 1;
        if (s.clip_url) entry.clips += 1;
      };
      (s.char_idxs ?? []).forEach((ci) => bump(0, ci));
      (s.scene_idxs ?? []).forEach((si) => bump(1, si));
      (s.prop_idxs ?? []).forEach((pi) => bump(2, pi));
      (s.voice_idxs ?? []).forEach((vi) => bump(3, vi));
    });
    // 资产卡参数：一排一个（单列列表式，即梦风格）；卡宽按类型（音色卡含音频控件更宽）；图高按真实比例自动（9:16 竖 / 16:9 横 / 1:1 方）
    const ASSET_CARD_W: Record<string, number> = {char: 200, scene: 200, prop: 200, voice: 280};
    const GROUP_PAD_X = 12;
    const GROUP_HEADER_H = 46;
    // 原生资产分组/资产卡不再上画布：媒体卡片节点（media-*）已自带分区与预览，
    // 双套资产表示会造成重复（用户 2026-10-23 裁定）。侧栏资产托盘仍读 canvas.* 数据，不受影响。
    filteredGroups.forEach((g) => {
      if (true) return; // ← 原生资产分组整体停用（保留代码便于日后切换）
      if (g.items.length === 0) return; // 空组不建节点
      const cardW = ASSET_CARD_W[g.kind] ?? 168;
      const gw = cardW + GROUP_PAD_X * 2;
      // 逐卡累计高度：图高=卡宽×真实比例（限制 0.55~1.6 防极端），卡高=图高+名称行/内边距
      let cursorY = GROUP_HEADER_H;
      const cardPos: {x: number; y: number}[] = [];
      const cardHList: number[] = [];
      g.items.forEach(({item}) => {
        const isImage = !!item.image_url;
        const ratio = isImage ? (assetRatios[item.image_url as string] ?? 1) : 0.4;
        const imgH = isImage ? Math.round(cardW * Math.min(Math.max(ratio, 0.55), 1.6)) : 64;
        const cardH = imgH + 34;
        cardPos.push({x: GROUP_PAD_X, y: cursorY});
        cardHList.push(cardH);
        cursorY += cardH + 8;
      });
      const gh = cursorY + 10;
      const isCollapsed = collapsedGroups.has(g.kind);
      // 父节点：分组框（背景 + 标题），整组拖动；折叠时只保留头部，子节点全部隐藏
      out.push({
        id: `asset-group-${g.kind}`,
        type: 'asset-group',
        position: {x: groupX[g.kind], y: 0},
        // initialWidth/initialHeight：React Flow 对未测量节点保持 visibility:hidden，
        // 显式初始尺寸让 hasDimensions 立即为 true（测量链路不可靠时节点不会永久隐藏）
        initialWidth: gw,
        initialHeight: isCollapsed ? GROUP_HEADER_H + 30 : gh,
        style: {width: gw, height: isCollapsed ? GROUP_HEADER_H + 30 : gh},
        data: {kind: g.kind, label: g.label, count: g.items.length, collapsed: isCollapsed, onToggle: () => toggleGroup(g.kind)} satisfies AssetGroupNodeData,
      });
      // 子节点：每张资产卡独立节点（端口锚定在卡片右缘，连线从资产图旁精确发出）。id 用原始索引，搜索过滤不改变节点身份
      g.items.forEach(({item, origIdx}, i) => {
        out.push({
          id: `asset-${g.kind}-${origIdx}`,
          type: 'asset-card',
          parentId: `asset-group-${g.kind}`,
          position: cardPos[i],
          hidden: isCollapsed,
          initialWidth: cardW,
          initialHeight: cardHList[i],
          style: {width: cardW, height: cardHList[i]},
          data: {
            kindLabel: g.label,
            item,
            refs: assetUse[`${g.kind}-${origIdx}`]?.refs ?? 0,
            clips: assetUse[`${g.kind}-${origIdx}`]?.clips ?? 0,
            onDetail: (kind: string, item: CanvasAssetItem) => setAssetDetail({kind, item}),
            onZoom,
          } satisfies AssetCardNodeData,
        });
      });
    });

    // 资产列头（不再使用——组节点自带标题）

    // 段卡节点：每段一个，段内包含全部镜头提示词/媒体。
    // 整剧总览：按集分组（ep-box 父节点，AniShort"一集一画布"容器），段卡 id 带集前缀防跨集冲突。
    const sortedSegs = [...effectiveSegments].sort((a, b) => {
      const epA = a.ep_id || '', epB = b.ep_id || '';
      if (currentEp === null && epA !== epB) return epA.localeCompare(epB, undefined, {numeric: true});
      return a.segment_id - b.segment_id;
    });
    const shotByIdx = new Map(canvas.shots.map((s) => [s.idx, s]));
    const isOverview = currentEp === null;
    const epGroups = new Map<string, CanvasSegment[]>();
    if (isOverview) {
      for (const seg of sortedSegs) {
        const ep = seg.ep_id || 'ep00';
        if (!epGroups.has(ep)) epGroups.set(ep, []);
        epGroups.get(ep)!.push(seg);
      }
    }
    // ep-box 盒头高度预算（CSS 对齐：header 40 + 资产泳道 34 + 视频泳道(空) 28 + 泳道内边距/间距/盒内边距）。
    // 段卡子节点从该 y 起排——预算必须 ≥ 盒头实际渲染高度，否则段卡压在泳道上（旧版 76 导致重叠）。
    const EP_HEADER_H = 120;
    const EP_VIDEO_LANE_EXTRA = 40; // 视频组泳道增量
    const EP_CHILD_X = 12;          // 段卡与盒左边框留白，不贴虚线
    const EP_BOTTOM_PAD = 14;       // 末张段卡与盒底留白
    const EP_GAP = 48;
    const SEG_GAP = 32;
    let epCursorY = 0;
    sortedSegs.forEach((seg, i) => {
      if (true) return; // ← 原生段卡/分集盒停用：画布只留媒体节点卡片（角色/场景/道具/音色/视频），分段信息在对应视频节点内
      const shotIdxs = segShotIdxsOf(seg, canvas!.shots, {overview: currentEp === null});
      const segShots = shotIdxs.map((idx) => shotByIdx.get(idx)).filter((s): s is CanvasShot => Boolean(s));
      const baseData = {
        segment: seg,
        shots: segShots,
        boundAssets: boundMap.get(seg.segment_id) ?? [],
        onSelectShot: (idx: number) => setSelectedId(`shot-${idx}`),
        onOpenPrompt: (shot: CanvasShot) => setPromptShot(shot),
        onZoom,
        onRenderSegment,
        onOpenSegmentPrompt: (s: CanvasSegment) => setPromptSegment(s),
        qualityFlags,
        videoRatios,
        onVideoMeta,
      } satisfies SegmentNodeData;
      if (isOverview) {
        const ep = seg.ep_id || 'ep00';
        const group = epGroups.get(ep)!;
        const gIdx = group.indexOf(seg);
        // 视频组：该集已出片段镜头（按集内镜头序），点击弹出提示词/资产引用窗口
        const epVideoShots = group
          .flatMap((gseg) => (Array.isArray(gseg.shot_idxs) ? gseg.shot_idxs : []))
          .map((idx) => shotByIdx.get(idx))
          .filter((s): s is CanvasShot => Boolean(s?.clip_url))
          .sort((a, b) => a.idx - b.idx);
        const epFinalUrl = canvas!.meta.episodes?.find((e) => e.ep_id === (seg.ep_id || 'ep00'))?.final_video_url ?? null;
        // 左右两栏：左栏成片视频（竖版 170 高 + 标签/padding）撑高；右栏资产多行 + 视频组
        const epHeadH = epFinalUrl
          ? 280
          : EP_HEADER_H + (epVideoShots.length > 0 ? EP_VIDEO_LANE_EXTRA : 0);
        if (gIdx === 0) {
          const epShotCount = group.reduce((acc, s) => acc + (Array.isArray(s.shot_idxs) ? s.shot_idxs.length : 0), 0);
          const epAssets: SegmentNodeData['boundAssets'] = [];
          const seenAssets = new Set<string>();
          for (const gseg of group) {
            for (const a of (boundMap.get(gseg.segment_id) ?? [])) {
              const ak = `${a.kind}:${a.key}`;
              if (!seenAssets.has(ak)) { seenAssets.add(ak); epAssets.push(a); }
            }
          }
          const EP_W = 800; // 与段卡展开态 min-width:760 + 子节点偏移/padding 对齐
          out.push({
            id: `ep-box-${ep}`,
            type: 'ep-box',
            position: {x: segX, y: epCursorY},
            initialWidth: EP_W,
            initialHeight: epHeadH + SEG_GAP + group.length * SEG_ROW + (group.length - 1) * SEG_GAP + EP_BOTTOM_PAD,
            style: {width: EP_W, height: epHeadH + SEG_GAP + group.length * SEG_ROW + (group.length - 1) * SEG_GAP + EP_BOTTOM_PAD},
            data: {label: fmtEp(ep), count: group.length, shots: epShotCount, assets: epAssets, videoShots: epVideoShots, onOpenPrompt: (shot: CanvasShot) => setPromptShot(shot), qualityFlags, final_video_url: canvas!.meta.episodes?.find((e) => e.ep_id === ep)?.final_video_url ?? null, videoRatios, onVideoMeta} satisfies EpBoxNodeData,
          });
          epCursorY += epHeadH + SEG_GAP + group.length * SEG_ROW + (group.length - 1) * SEG_GAP + EP_BOTTOM_PAD + EP_GAP;
        }
        out.push({
          id: segNodeId(seg),
          type: 'segment',
          parentId: `ep-box-${ep}`,
          position: {x: EP_CHILD_X, y: epHeadH + SEG_GAP + gIdx * (SEG_ROW + SEG_GAP)},
          initialWidth: 400,
          initialHeight: SEG_ROW,
          data: baseData,
        });
      } else {
        out.push({
          id: segNodeId(seg),
          type: 'segment',
          position: {x: segX, y: i * (SEG_ROW + 32)},
          initialWidth: 400,
          initialHeight: SEG_ROW,
          data: baseData,
        });
      }
    });

    notes.forEach((note) => {
      out.push({
        id: `note-${note.id}`,
        type: 'note',
        position: {x: note.x, y: note.y},
        initialWidth: 220,
        initialHeight: 90,
        style: zOrder[`note-${note.id}`] ? {zIndex: zOrder[`note-${note.id}`]} : undefined,
        data: {note, onChange: updateNoteText, groupMembers: groupMap.get(`note-${note.id}`) ?? null, onPressGroup: pressGroup},
      });
    });

    visibleMediaNodes.forEach((mn) => {
      // 初始尺寸必须显式给足（header ~32px + 预览区按比例换算 + 卡片 padding）：
      // React Flow 对无 initialWidth/Height 的节点走 ResizeObserver 测量，若测量链路
      // 不触发（节点 hidden）则边 path 永远算不出 → 连线不渲染（实测 0 条）。
      const _meta = MEDIA_KIND_META[mn.kind];
      const _ratio = (mn.kind === 'image' || mn.kind === 'video')
        ? (mn.params?.ratio || (mn.kind === 'image' ? '1:1' : '9:16'))
        : undefined;
      const _rm = /(\d+)\s*:\s*(\d+)/.exec(_ratio || '');
      // 文本节点支持右下角拖拽自定义尺寸：initialWidth/Height 必须跟着走，
      // 否则连线端点仍按默认 200×120 算，卡片拉大后线会插在卡片中间。
      const _tw = mn.kind === 'text' ? (mn.params?.textW || _meta.w) : _meta.w;
      const _th = mn.kind === 'text'
        ? (mn.params?.textH || Math.round(_meta.h * 0.9))
        : ((_rm && Number(_rm[2]) > 0)
          ? Math.round((_meta.w * Number(_rm[2])) / Number(_rm[1]))
          : (mn.kind === 'audio' ? 100 : _meta.h));
      const _overviewHeight = mediaRenderMode === 'overview' && editNode?.id !== mn.id
        ? 68
        : 32 + _th + 12;
      out.push({
        id: `media-${mn.id}`,
        type: 'media',
        position: {x: mn.x, y: mn.y},
        initialWidth: _tw,
        initialHeight: _overviewHeight,
        style: zOrder[`media-${mn.id}`] ? {zIndex: zOrder[`media-${mn.id}`]} : undefined,
        data: {
          node: mn,
          renderMode: mediaRenderMode,
          onChange: updateMediaNode,
          onGenerate: onGenerateMediaNode,
          onDelete: requestDeleteMediaNode,
          onEdit: openMediaModal,
          onZoom: (url: string) => setZoomTarget(url),
          onReadText: (item: CanvasMediaNode) => setTextReader(item),
          onCopyText: (item: CanvasMediaNode) => {
            navigator.clipboard?.writeText(item.prompt || '')
              .then(() => setToast('已复制全文'))
              .catch(() => setToast('复制失败，请展开后手动复制'));
          },
          assets: mediaNodes.filter((a: CanvasMediaNode) => a.id !== mn.id),
          active: editNode?.id === mn.id,
          generationBusy: generationBusyIds.has(mn.id),
          groupMembers: groupMap.get(`media-${mn.id}`) ?? null,
          onSelectGroup: selectFlowIds,
          onPressGroup: pressGroup,
          onUploadLocal: importLocalFileToNode,
          suppressNodeOpen,
          isNodeOpenSuppressed,
        },
      });
    });

    const placed = out.map((node) => {
      // 资产卡子节点始终用组内自动排布位置（跟随分组框拖动），不恢复单独位置
      if (node.parentId && node.type !== 'segment') return node;
      // 媒体卡片位置以载荷 mn.x/mn.y 为准（拖动时同步回 mn，见 onNodeDragStop），
      // 不走 positionRef——否则 localStorage 历史残留的"单列位置"会覆盖载荷多列布局
      // （切集排序乱的根因：剧集页签点「第01集」后 21 张卡堆成 x≈0 一列）。
      if (node.type === 'media') return node;
      // 分段卡：恢复用户手动拖拽保存的位置
      const saved = positionRef.current[node.id];
      return saved ? {...node, position: saved} : node;
    });
    return placed;
  }, [canvas, notes, updateNoteText, segBoundAssets, effectiveSegments, assetRatios, videoRatios, onVideoMeta, assetQuery, collapsedGroups, toggleGroup, currentEp, segNodeId, fmtEp, visibleMediaNodes, generationBusyIds, zOrder, groupMap, selectFlowIds, pressGroup, importLocalFileToNode, suppressNodeOpen, isNodeOpenSuppressed, mediaRenderMode, largeCanvasOverview, editNode?.id]);
  const renderedEdges = useMemo(() => {
    if (largeCanvasOverview) return [];
    const ids = new Set(flowNodes.map((node) => node.id));
    return edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
  }, [edges, flowNodes, largeCanvasOverview]);

  useEffect(() => {
    // 关键：程序化重建节点时必须继承上一轮已测量的尺寸。
    // React Flow v12 的 adoptUserNodes/parseHandles 以「userNode.measured 是否存在」判断
    // 节点是否被重新初始化：新对象若不带 measured，就会把该节点的 internals.handleBounds
    // 置空以便重新测量；handleBounds 一旦为空，EdgeWrapper 解析端点得到 sourceX=null，
    // 连线不渲染——表现就是"进入画布连线闪几下就消失"。
    // 这里把已测量节点的 measured 原样带过去，handleBounds 在节点重建中得以保留。
    if (draggingRef.current) {
      pendingFlowSyncRef.current = true;
      return;
    }
    pendingFlowSyncRef.current = false;
    setNodes((prevNodes) => {
      const prevById = new Map(prevNodes.map((node) => [node.id, node]));
      return flowNodes.map((node) => {
        const prev = prevById.get(node.id);
        if (!prev) return node;
        // selected 必须一并继承：flowNodes 是纯派生数据，不带选中态；
        // 不继承的话 React Flow 刚点出的选中会被这次重建抹掉——表现就是
        // 「点一下出现选中样式随即消失，Shift 多选永远选不中第二个节点」。
        return {
          ...node,
          ...(prev.measured ? {measured: prev.measured} : null),
          ...(prev.selected ? {selected: true} : null),
        };
      });
    });
  }, [flowNodes, setNodes, flowSyncTick]);

  // 早前版本在这里用 store.updateNodeInternals 手动重建 handleBounds，用来绕开
  // "连线闪几下即消失"。根因其实是上面 setNodes 丢失 measured（见该处注释）；
  // 修好后测量链路恢复正常，这里不再需要任何手动补丁：
  // 手动 updateNodeInternals 会额外触发节点重渲染，本身就是抖动来源之一。
  useEffect(() => {
    const deleted = deletedAutoEdgeIds;
    // selected 必须一并继承：这里是整批重建 edges，不带选中态的话，
    // 刚点选中的连线会在下一次重建时被抹掉选中 → 表现就是"点一下连线，✕ 随即消失、剪不断"。
    setEdges((prev) => {
      const prevById = new Map(prev.map((edge) => [edge.id, edge]));
      return [...flowEdges.filter((edge) => !deleted.has(edge.id)), ...userEdges].map((edge) => {
        const before = prevById.get(edge.id);
        return before?.selected ? {...edge, selected: true} : edge;
      });
    });
  }, [flowEdges, userEdges, deletedAutoEdgeIds, setEdges]);

  const selectedShot = useMemo(() => {
    if (!selectedId || !selectedId.startsWith('shot-') || !canvas) return null;
    const idx = Number(selectedId.slice('shot-'.length));
    const shot = canvas.shots.find((item) => item.idx === idx);
    if (!shot) return null;
    return {idx, shot, draft: drafts[idx] ?? emptyDraft(shot), dirty: dirtyShots.has(idx)};
  }, [selectedId, canvas, drafts, dirtyShots]);

  const pickerAssets = useMemo(() => {
    if (!canvas) return [] as {key: string; kind: string; name: string; image_url?: string; audio_url?: string}[];
    const all: {key: string; kind: string; name: string; image_url?: string; audio_url?: string}[] = [
      ...canvas.characters.map((item, i) => ({key: `char-${i}`, kind: '角色', name: item.name, image_url: item.image_url})),
      ...canvas.scenes.map((item, i) => ({key: `scene-${i}`, kind: '场景', name: item.name, image_url: item.image_url})),
      ...canvas.props.map((item, i) => ({key: `prop-${i}`, kind: '道具', name: item.name, image_url: item.image_url})),
      ...canvas.voices.map((item, i) => ({key: `voice-${i}`, kind: '音色', name: item.name, audio_url: item.audio_url})),
    ];
    return all;
  }, [canvas]);

  // 确定性全景适配：基于流程节点的预设尺寸手动计算 bounds（不依赖 React Flow 节点测量时序，
  // 避免首次 fitView 漏掉未测量节点导致整体放大、后几组资产被推出视口）
  const fitCanvas = useCallback((options?: {animate?: boolean; focus?: boolean}) => {
    if (!canvas) return;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    // 与 flowNodes 布局常量保持一致（单列卡宽 + 组内边距）；卡高按真实比例逐卡累计
    const GROUP_PAD_X = 12;
    const GROUP_HEADER_H = 46;
    const ASSET_CARD_W: Record<string, number> = {char: 200, scene: 200, prop: 200, voice: 280};
    const grpW: Record<string, number> = {
      char: ASSET_CARD_W.char + GROUP_PAD_X * 2,
      scene: ASSET_CARD_W.scene + GROUP_PAD_X * 2,
      prop: ASSET_CARD_W.prop + GROUP_PAD_X * 2,
      voice: ASSET_CARD_W.voice + GROUP_PAD_X * 2,
    };
    const groupHeightOf = (kind: 'char' | 'scene' | 'prop' | 'voice', items: CanvasAssetItem[]): number => {
      const cardW = ASSET_CARD_W[kind];
      let cursorY = GROUP_HEADER_H;
      items.forEach((item) => {
        const isImage = !!item.image_url;
        const ratio = isImage ? (assetRatios[item.image_url as string] ?? 1) : 0.4;
        const imgH = isImage ? Math.round(cardW * Math.min(Math.max(ratio, 0.55), 1.6)) : 64;
        cursorY += imgH + 34 + 8;
      });
      return cursorY + 10;
    };
    const grpH: Record<string, number> = {
      char: groupHeightOf('char', canvas.characters),
      scene: groupHeightOf('scene', canvas.scenes),
      prop: groupHeightOf('prop', canvas.props),
      voice: groupHeightOf('voice', canvas.voices),
    };
    const gx: Record<string, number> = {char: 0, scene: COL_X, prop: COL_X * 2, voice: COL_X * 3};
    const focusMediaOnly = !!options?.focus && mediaNodes.length > 0;
    if (!focusMediaOnly) {
      (['char', 'scene', 'prop', 'voice'] as const).forEach((k) => {
        const cnt = k === 'char' ? canvas.characters.length : k === 'scene' ? canvas.scenes.length : k === 'prop' ? canvas.props.length : canvas.voices.length;
        if (cnt === 0) return;
        minX = Math.min(minX, gx[k]);
        maxX = Math.max(maxX, gx[k] + grpW[k]);
        minY = Math.min(minY, 0);
        maxY = Math.max(maxY, grpH[k]);
      });
      // 段卡列在右侧：只计入首段区域（y0~SEG_ROW），避免多段竖排把内容撑大导致整体过度缩小
      if (effectiveSegments.length > 0) {
        minX = Math.min(minX, COL_X * 4);
        maxX = Math.max(maxX, COL_X * 4 + 400);
        maxY = Math.max(maxY, SEG_ROW);
      }
    }
    notes.forEach((n) => {
      minX = Math.min(minX, n.x);
      maxX = Math.max(maxX, n.x + 220);
      minY = Math.min(minY, n.y);
      maxY = Math.max(maxY, n.y + 90);
    });
    // 媒体节点（角色/场景/道具/音色/视频卡片）：按载荷坐标并入 bounds——原生资产列停用后
    // 媒体卡片是画布唯一主体，若 bounds 不含它们，适配后视口会落在空区。
    mediaNodes.forEach((mn) => {
      // Initial entry focuses the readable working band instead of shrinking the
      // whole vertical episode to 15%. "适应视图" still fits the complete graph.
      // Segment cards use a compact three-column grid; keep its first four rows
      // visible instead of hiding every row below the first one.
      const focusYLimit = mn.kind === 'video' ? 2400 : 900;
      if (options?.focus && mn.y > focusYLimit) return;
      const _m = MEDIA_KIND_META[mn.kind];
      minX = Math.min(minX, mn.x);
      maxX = Math.max(maxX, mn.x + _m.w);
      minY = Math.min(minY, mn.y);
      maxY = Math.max(maxY, mn.y + _m.h + 44);
    });
    if (!isFinite(minX)) return;
    const bounds = {x: minX, y: minY, width: Math.max(100, maxX - minX), height: Math.max(100, maxY - minY)};
    // First-entry fitting must land at its final viewport immediately. The old
    // implementation fitted once, then animated a second fit 350ms later, while
    // two surrounding effects retried at 400/1200/2500ms. That looked like the
    // canvas was zooming itself after the user had already entered the project.
    // Explicit bounds make later node measurement unnecessary, and ResizeObserver
    // updates on the edges/nodes do not require another viewport fit.
    const host = document.querySelector('.react-flow') as HTMLElement | null;
    const viewport = viewportForCanvasBounds(
      bounds,
      host?.clientWidth || window.innerWidth,
      host?.clientHeight || window.innerHeight,
      {
        focus: options?.focus,
        padding: 0.15,
        maxZoom: 1,
      },
    );
    rf.setViewport(viewport, {duration: options?.animate ? 220 : 0});
  }, [canvas, effectiveSegments, notes, rf, assetRatios, mediaNodes]);

  const fitView = useCallback(() => {
    if (shouldUseLargeCanvasOverview(mediaNodes)) {
      overviewReturnViewportRef.current = viewportRef.current;
      setLargeCanvasOverview(true);
      return;
    }
    fitCanvas({animate: true});
  }, [fitCanvas, mediaNodes]);
  const focusSelection = useCallback((showEmptyHint = true) => {
    setLargeCanvasOverview(false);
    window.requestAnimationFrame(() => {
      const selected = rf.getNodes().filter((node) => node.selected);
      if (selected.length === 0) {
        if (showEmptyHint) setToast('先选择节点，再点「聚焦」');
        return;
      }
      rf.fitView({
        nodes: selected.map((node) => ({id: node.id})),
        padding: 0.3,
        duration: 280,
      });
    });
  }, [rf, setToast]);
  const focusWorkspace = useCallback(() => {
    focusSelection(true);
  }, [focusSelection]);
  focusSelectionRef.current = focusSelection;
  const returnFromOverview = useCallback(() => {
    const viewport = overviewReturnViewportRef.current;
    setLargeCanvasOverview(false);
    window.requestAnimationFrame(() => {
      if (viewport) rf.setViewport(viewport, {duration: 180});
      else fitCanvas({animate: true, focus: true});
    });
  }, [fitCanvas, rf]);
  const focusOverviewNode = useCallback((node: CanvasMediaNode) => {
    const zoom = 0.8;
    setLargeCanvasOverview(false);
    setLargeCanvasVisibleBounds(focusedNodeVisibleBounds(
      {
        x: node.x,
        y: node.y,
        w: MEDIA_KIND_META[node.kind].w,
        h: MEDIA_KIND_META[node.kind].h,
      },
      zoom,
      window.innerWidth,
      window.innerHeight,
    ));
    window.setTimeout(() => {
      rf.setCenter(
        node.x + MEDIA_KIND_META[node.kind].w / 2,
        node.y + MEDIA_KIND_META[node.kind].h / 2,
        {zoom, duration: 260},
      );
    }, 0);
  }, [rf]);
  const updateLargeCanvasViewport = useCallback((viewport: CanvasViewport) => {
    if (mediaNodes.length <= 400 || largeCanvasOverview) return;
    if (visibleBoundsTimerRef.current !== null) {
      window.clearTimeout(visibleBoundsTimerRef.current);
    }
    visibleBoundsTimerRef.current = window.setTimeout(() => {
      visibleBoundsTimerRef.current = null;
      setLargeCanvasVisibleBounds(visibleBoundsForViewport(
        viewport,
        window.innerWidth,
        window.innerHeight,
      ));
    }, 90);
  }, [largeCanvasOverview, mediaNodes.length]);
  useEffect(() => () => {
    if (visibleBoundsTimerRef.current !== null) {
      window.clearTimeout(visibleBoundsTimerRef.current);
    }
  }, []);

  // LibTV 式自动布局：资产生成组在前（角色/场景/道具/音色），段卡列在最右（备注/列头保持原位）
  const autoLayout = useCallback(() => {
    setNodes((nds) => {
      const rows: Record<string, number> = {segment: SEG_ROW, shot: SHOT_ROW};
      const cols: Record<string, number> = {
        segment: COL_X * 4,
        shot: COL_X * 4,
        note: COL_X * 4,
        'asset-group-char': 0,
        'asset-group-scene': COL_X,
        'asset-group-prop': COL_X * 2,
        'asset-group-voice': COL_X * 3,
      };
      // ep-box 不在 cols 里，若不排除会被摆到 x=0，正好压在"角色"分组框上：
      // 角色组整块被整集框盖住，拖分组时 pointerdown 落到整集框上（拖不动 / 拖错对象）。
      // 整集框位置由 flowNodes 统一排布（段卡列 COL_X*4、按集纵向堆叠），这里保持不动。
      const keyOf = (node: FlowNode): string => {
        const type = node.type ?? '';
        if (type === 'asset-group') {
          const d = node.data as {kind?: string};
          return `asset-group-${d.kind ?? ''}`;
        }
        return type;
      };
      const groups: Record<string, FlowNode[]> = {};
      nds.forEach((node) => {
        const key = keyOf(node);
        if (key === 'note' || key === 'ep-box') return;
        (groups[key] ??= []).push(node);
      });
      // 整集框不受自动布局管理，但「自动布局 / 恢复默认布局」需要把它清回默认槽位，
      // 否则旧的持久化位置（曾把整集框摆在 x=0 压住"角色"分组）会一直生效。
      nds.forEach((node) => {
        if (keyOf(node) === 'ep-box') delete positionRef.current[node.id];
      });
      const sortKey = (node: FlowNode): string => {
        const data = node.data as {shot?: CanvasShot; segment?: CanvasSegment; item?: CanvasAssetItem};
        if (data.shot) return String(data.shot.idx);
        if (data.segment) return String(data.segment.segment_id);
        return data.item?.name ?? node.id;
      };
      Object.values(groups).forEach((list) => list.sort((a, b) => sortKey(a).localeCompare(sortKey(b), undefined, {numeric: true})));
      const next = nds.map((node) => {
        // 资产卡子节点：位置相对父节点（分组框），跟随父整体拖动，不参与重排
        if (node.parentId) return node;
        const key = keyOf(node);
        // 媒体卡片节点保持导入载荷的原始五区位置，不参与自动重排
        if (key === 'media') return node;
        if (key === 'note' || key === 'ep-box' || key === 'asset-col' || key === '') return node;
        const list = groups[key] ?? [];
        const index = list.findIndex((n) => n.id === node.id);
        const position = {x: cols[key] ?? 0, y: (rows[key] ?? ASSET_ROW) * Math.max(0, index)};
        positionRef.current[node.id] = position;
        return {...node, position};
      });
      return next;
    });
    requestAnimationFrame(() => fitCanvas({animate: false}));
  }, [rf, setNodes, fitCanvas]);

  // 首次加载：恢复持久化布局；无持久化时才自动布局（依赖 autoLayout/fitCanvas，须声明在其后，避免 TDZ）
  // fitCanvas 的依赖里有 mediaNodes，改任意参数（比例/清晰度…）都会换一个新身份。
  // 直接把它放进下面的 effect 依赖 → 每改一次参数画布就被"重新适配"一次（视口突然缩回全局）。
  // 用 ref 取最新实现，effect 只跟 loading/canvas 走。
  const fitCanvasRef = useRef(fitCanvas);
  fitCanvasRef.current = fitCanvas;
  // autoLayout / loadPersistedLayout 的身份也会随 fitCanvas（依赖 mediaNodes）变化，
  // 同样用 ref 取值；否则改一次参数就把整个画布重新 fit 一次（视口突然缩回全局）。
  const autoLayoutRef = useRef(autoLayout);
  autoLayoutRef.current = autoLayout;
  const loadPersistedLayoutRef = useRef(loadPersistedLayout);
  loadPersistedLayoutRef.current = loadPersistedLayout;
  useEffect(() => {
    if (loading || !canvas) return;
    // Fit once per project/episode scope. Canvas objects are replaced after
    // generation/status refreshes too; refitting on those updates caused the
    // viewport to jump back to overview while the user was working.
    const scope = `${session}:${currentEp ?? 'all'}`;
    const shouldFit = fittedScopeRef.current !== scope;
    if (shouldFit) fittedScopeRef.current = scope;
    let savedViewport: {x: number; y: number; zoom: number} | null = null;
    if (!didInitialLayoutRef.current) {
      didInitialLayoutRef.current = true;
      const saved = loadPersistedLayoutRef.current();
      const remoteUserEdges = projectConnectionsRef.current
        .filter((connection) => connection.source === 'user')
        .map(userEdgeFromConnection);
      const legacyUserEdges = saved && Array.isArray(saved.userEdges)
        ? saved.userEdges.map(hydrateUserEdge)
        : [];
      const useRemoteConnections = Number(saved?.connectionPersistenceVersion || 0) >= 1;
      const mergedUserEdges = useRemoteConnections
        ? remoteUserEdges
        : [...legacyUserEdges, ...remoteUserEdges];
      const uniqueUserEdges = [...new Map(
        mergedUserEdges.map((edge) => [`${edge.source}->${edge.target}`, edge]),
      ).values()];
      if (saved) {
        savedViewport = saved.viewport || null;
        if (
          savedViewport
          && Number(saved.viewportPolicyVersion || 1) < CANVAS_VIEWPORT_POLICY_VERSION
          && savedViewport.zoom < 0.5
        ) {
          savedViewport = null;
        }
        if (saved.positions) positionRef.current = {...positionRef.current, ...saved.positions};
        if (Array.isArray(saved.deletedAuto) && saved.deletedAuto.length > 0) setDeletedAutoEdgeIds(new Set(saved.deletedAuto));
        // 编组关系随布局恢复（换集/刷新后组不能丢）
        if (Array.isArray(saved.groups) && saved.groups.length > 0) {
          setGroups(saved.groups);
          groupsRef.current = saved.groups;
        }
      } else {
        autoLayoutRef.current();
      }
      setUserEdges(uniqueUserEdges);
    }
    // 画布适配必须等节点 DOM 挂载后再执行：React Flow 对未测量节点保持
    // visibility:hidden（hasDimensions 为 false），首次 fitBounds 若在节点挂载前
    // 调用不会触发测量，节点将永久隐藏——"画布内容进入后就消失"的根因。
    // 延迟一帧再 fitCanvas（fitBounds→setViewport 会触发节点测量并可见）。
    // ★ 每次切集（loading/canvas 变化）都必须重新 fit，否则视口停在上一集位置，
    //   媒体卡片会被挤到视口外（实测：剧集页签点「第01集」后画布只剩镜头条块）。
    if (shouldFit) {
      requestAnimationFrame(() => {
        if (savedViewport) {
          viewportRef.current = savedViewport;
          rf.setViewport(savedViewport, {duration: 0});
        } else {
          fitCanvasRef.current({animate: false, focus: true});
        }
      });
    }
  }, [loading, canvas, session, currentEp]);

  // 通用文本下载（导出分镜 Markdown / 镜头 CSV / 布局 JSON）
  const downloadText = useCallback((filename: string, text: string, mime = 'text/plain') => {
    const blob = new Blob([text], {type: `${mime};charset=utf-8`});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, []);

  // 导出当前集分镜 Markdown（按段组织镜头提示词）
  const exportStoryboardMd = useCallback(() => {
    if (!canvas) return;
    const lines: string[] = [`# ${fmtEp(currentEp ?? 'all')} 分镜`];
    // 用 effectiveSegments：缺 segments.json 时仍有兜底分组，导出结构与画布一致
    effectiveSegments.forEach((seg) => {
      const segShots = segShotIdxsOf(seg, canvas.shots, {overview: currentEp === null});
      lines.push('', `## 段 ${seg.segment_id}${seg.title ? `：${seg.title}` : ''}（${seg.duration ?? '-'}s）`);
      segShots.forEach((idx) => {
        const shot = canvas.shots.find((s) => s.idx === idx);
        if (!shot) return;
        lines.push(`- 镜头 ${idx}${shot.duration !== null ? `（${shot.duration}s）` : ''}${shot.clip_url ? ' ✅已出片' : shot.keyframe_url ? ' ◐关键帧' : ' ○未渲染'}`);
        const prompt = shot.video_prompt || shot.visual_desc || '';
        if (prompt) lines.push(`  ${prompt}`);
      });
    });
    if (effectiveSegments.length === 0) {
      canvas.shots.forEach((shot) => {
        lines.push(`- 镜头 ${shot.idx}${shot.duration !== null ? `（${shot.duration}s）` : ''}${shot.clip_url ? ' ✅已出片' : ''}`);
        const prompt = shot.video_prompt || shot.visual_desc || '';
        if (prompt) lines.push(`  ${prompt}`);
      });
    }
    downloadText(`storyboard-${session}-${currentEp ?? 'all'}.md`, lines.join('\n'), 'text/markdown');
  }, [canvas, effectiveSegments, currentEp, session, downloadText]);

  // 导出镜头清单 CSV
  const exportShotsCsv = useCallback(() => {
    if (!canvas) return;
    const esc = (v: unknown) => `"${String(v ?? '').replaceAll('"', '""')}"`;
    const rows = ['镜头,段,时长,关键帧,片段,机位,转场,提示词'];
    [...canvas.shots].sort((a, b) => a.idx - b.idx).forEach((s) => {
      rows.push([s.idx, s.segment_id ?? '', s.duration ?? '', s.keyframe_url ? '是' : '', s.clip_url ? '是' : '', s.cam_idx ?? '', s.transition_type ?? '', s.video_prompt || s.visual_desc || ''].map(esc).join(','));
    });
    downloadText(`shots-${session}-${currentEp ?? 'all'}.csv`, rows.join('\n'), 'text/csv');
  }, [canvas, currentEp, session, downloadText]);

  // 导出布局 JSON（当前集）
  const exportLayoutJson = useCallback(() => {
    downloadText(`layout-${session}-${currentEp ?? 'all'}.json`, JSON.stringify({
      kind: 'drama-canvas-layout',
      version: 1,
      positions: positionRef.current,
      userEdges,
      deletedAuto: [...deletedAutoEdgeIds],
      savedAt: new Date().toISOString(),
    }, null, 2), 'application/json');
  }, [session, currentEp, userEdges, deletedAutoEdgeIds, downloadText]);

  // 恢复默认布局：清除该集缓存并重新自动排布
  const resetLayout = useCallback(() => {
    try {
      localStorage.removeItem(layoutKey);
    } catch { /* 忽略 */ }
    // 清空所有手动保存的节点位置（含段卡拖拽位置），让 autoLayout 重新计算
    positionRef.current = {};
    setUserEdges([]);
    setDeletedAutoEdgeIds(new Set());
    requestAnimationFrame(() => autoLayout());
    setToast('已恢复默认布局');
  }, [layoutKey, autoLayout, setUserEdges, setToast]);

  // 导入布局 JSON（文件）
  const importLayoutJson = useCallback((text: string) => {
    try {
      const parsed = JSON.parse(text) as {
        kind?: string;
        positions?: Record<string, {x: number; y: number}>;
        userEdges?: Edge[];
        deletedAuto?: string[];
        asset_prompts?: unknown[];
        segments?: unknown[];
      };
      if (Array.isArray(parsed.asset_prompts) || Array.isArray(parsed.segments)) {
        throw new Error('这是投喂载荷，不是布局文件。请用顶部「项目设置 → 导入投喂载荷」导入');
      }
      if (!parsed.positions || typeof parsed.positions !== 'object' || Array.isArray(parsed.positions)) {
        throw new Error('不是有效画布布局：缺少 positions 数据');
      }
      positionRef.current = {...positionRef.current, ...parsed.positions};
      setNodes((nds) => nds.map((node) => {
        const position = parsed.positions?.[node.id];
        return position ? {...node, position} : node;
      }));
      if (Array.isArray(parsed.userEdges)) setUserEdges(parsed.userEdges.map(hydrateUserEdge));
      if (Array.isArray(parsed.deletedAuto)) setDeletedAutoEdgeIds(new Set(parsed.deletedAuto));
      persistLayout(positionRef.current, parsed.userEdges ?? [], new Set(parsed.deletedAuto ?? []));
      setToast('布局已导入');
    } catch (error) {
      setToast(error instanceof Error ? error.message : '布局文件解析失败');
    }
  }, [persistLayout, setNodes, setUserEdges, setToast]);

  // 全局搜索结果：段/镜头/备注/资产（资产命中同时过滤画布卡片；点击定位画布）
  const searchResults = useMemo(() => {
    if (!canvas) return [] as SearchHit[];
    const q = assetQuery.trim().toLowerCase();
    if (!q) return [] as SearchHit[];
    const hits: SearchHit[] = [];
    const match = (text?: string | null) => (text ?? '').toLowerCase().includes(q);
    const trunc = (text: string, n: number) => (text.length > n ? text.slice(0, n) + '…' : text);
    canvas.characters.forEach((item, i) => { if (match(item.name) || match(item.description)) hits.push({kind: '角色', label: item.name, sub: item.description ? trunc(item.description, 60) : undefined, nodeId: `asset-char-${i}`}); });
    canvas.scenes.forEach((item, i) => { if (match(item.name) || match(item.description)) hits.push({kind: '场景', label: item.name, sub: item.description ? trunc(item.description, 60) : undefined, nodeId: `asset-scene-${i}`}); });
    canvas.props.forEach((item, i) => { if (match(item.name) || match(item.description)) hits.push({kind: '道具', label: item.name, sub: item.description ? trunc(item.description, 60) : undefined, nodeId: `asset-prop-${i}`}); });
    canvas.voices.forEach((item, i) => { if (match(item.name) || match(item.description)) hits.push({kind: '音色', label: item.name, sub: item.description ? trunc(item.description, 60) : undefined, nodeId: `asset-voice-${i}`}); });
    effectiveSegments.forEach((seg) => {
      const segText = [seg.title, seg.asset_binding, seg.end_state, seg.scene_id].filter(Boolean).join(' ');
      if (match(segText)) hits.push({kind: '段', label: `段 ${seg.segment_id}：${seg.title || '（无标题）'}`, sub: trunc(segText.replace(/\s+/g, ' ').trim(), 60), nodeId: segNodeId(seg)});
    });
    // 镜头命中：画布中没有独立 shot 节点（镜头嵌在段卡内），命中后定位到所属段卡，
    // 否则点击搜索结果是死路（旧实现跳回左上角）。
    const shotToSeg = new Map<number, CanvasSegment>();
    effectiveSegments.forEach((seg) => {
      segShotIdxsOf(seg, canvas.shots, {overview: currentEp === null}).forEach((idx) => {
        if (!shotToSeg.has(idx)) shotToSeg.set(idx, seg);
      });
    });
    canvas.shots.forEach((shot) => {
      const text = [shot.visual_desc, shot.motion_desc, shot.audio_desc, shot.video_prompt].filter(Boolean).join(' ');
      if (match(text)) {
        const seg = shotToSeg.get(shot.idx);
        hits.push({kind: '镜头', label: `镜头 ${shot.idx}${shot.duration !== null ? `（${shot.duration}s）` : ''}`, sub: seg ? `段 ${seg.segment_id} · ${trunc(text.replace(/\s+/g, ' ').trim(), 48)}` : trunc(text.replace(/\s+/g, ' ').trim(), 60), nodeId: seg ? segNodeId(seg) : `shot-${shot.idx}`});
      }
    });
    notes.forEach((note) => { if (match(note.text)) hits.push({kind: '备注', label: trunc(note.text.replace(/\s+/g, ' ').trim(), 40), nodeId: `note-${note.id}`}); });
    // 媒体节点（自由画布上手工新增的文本/图片/音频/视频卡）也纳入搜索：
    // 之前这里只搜资产/段/镜头/备注，导致自己新建的卡片永远找不到 —— 节点一多就没法定位。
    mediaNodes.forEach((mn) => {
      const body = (mn.prompt || '').trim();
      if (!match(mn.title) && !match(body)) return;
      const kindLabel = mn.kind === 'text' ? '文本' : mn.kind === 'image' ? '图片' : mn.kind === 'audio' ? '音频' : '视频';
      hits.push({
        kind: kindLabel,
        label: (mn.title || '').trim() || `${kindLabel}节点`,
        sub: body ? trunc(body.replace(/\s+/g, ' ').trim(), 60) : undefined,
        nodeId: `${MEDIA_PREFIX}${mn.id}`,
      });
    });
    return hits.slice(0, 40);
  }, [canvas, assetQuery, notes, effectiveSegments, currentEp, segNodeId, mediaNodes]);

  // 点击搜索结果 → 定位并高亮目标节点。
  // 绝对坐标必须沿 parentId 链累加：整剧总览模式下段卡是 ep-box 的子节点，
  // position 是相对坐标，直接当绝对用会跳到错误位置（旧实现跳左上角）。
  const locateSelectTimerRef = useRef<number | null>(null);
  const locateHit = useCallback((hit: SearchHit) => {
    let target = {x: 0, y: 0};
    if (hit.nodeId.startsWith('asset-')) {
      // 原生资产节点已停用：按名称匹配媒体卡片节点定位（fallback）
      const media = mediaNodes.find((mn) => (mn.title || '').includes(hit.label));
      target = media ? {x: media.x, y: media.y} : {x: 0, y: 0};
    } else if (hit.nodeId.startsWith('note-')) {
      const note = notes.find((n) => n.id === hit.nodeId.slice('note-'.length));
      if (note) target = {x: note.x, y: note.y};
    } else if (hit.nodeId.startsWith(MEDIA_PREFIX)) {
      // 媒体节点：定位到卡片位置，并**同时选中**它（只跳不选的话用户还得自己再点一下找过去）
      const mn = mediaNodes.find((n) => n.id === hit.nodeId.slice(MEDIA_PREFIX.length));
      if (mn) target = {x: mn.x, y: mn.y};
    } else {
      const abs = absolutePositionOf(hit.nodeId, dragNodesRef.current);
      const fallback = positionRef.current[hit.nodeId];
      target = abs ?? fallback ?? {x: 0, y: 0};
    }
    rf.setCenter(target.x + 180, target.y + 140, {zoom: Math.max(rf.getZoom(), 0.9), duration: 360});
    setSelectedId(hit.nodeId);
    setSearchFocused(false);
    // ★ 选中态必须落到 React Flow 的节点上（只 setSelectedId 是内部 state，画布上没有任何视觉反馈，
    //   用户看到的就是"跳过去了但没选中"）。这里把目标节点置为唯一选中项。
    //   延迟到定位动画之后再做：RF 的 fitView/setCenter 期间会重算节点，先设容易被覆盖。
    const applySelect = () => setNodes((prev) => {
      const has = prev.some((n) => n.id === hit.nodeId);
      if (!has) return prev;
      return prev.map((n) => (n.id === hit.nodeId
        ? (n.selected ? n : {...n, selected: true})
        : (n.selected ? {...n, selected: false} : n)));
    });
    applySelect();
    if (locateSelectTimerRef.current) window.clearTimeout(locateSelectTimerRef.current);
    locateSelectTimerRef.current = window.setTimeout(() => {
      locateSelectTimerRef.current = null;
      applySelect();
    }, 420);
    // 目标节点短暂高亮（DOM 层视觉反馈，不与 React Flow 选中态冲突）
    const flash = () => {
      const el = document.querySelector(`[data-id="${CSS.escape(hit.nodeId)}"]`);
      if (!el) return false;
      el.classList.add('is-located');
      window.setTimeout(() => el.classList.remove('is-located'), 1400);
      return true;
    };
    if (!flash()) window.setTimeout(flash, 200);
  }, [rf, notes, mediaNodes, setNodes]);

  // Escape 关闭各类弹层（菜单/段契约/资产详情/帮助/侧边面板）
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpenMenu(null);
      setSegMenu(null);
      setContextMenu(null);
      setSegDetail(null);
      setAssetDetail(null);
      setHelpOpen(false);
      setPickShot(null);
      setEpListOpen(false);
      setSearchFocused(false);
      // ★ 第 15 轮打磨：这两个也是模态弹窗，之前 ESC 关不掉。
      //   用户按 Esc 是"退出当前这层"的通用手势，模态层不响应会让人以为卡死
      //   （实测点 Esc 后 .canvas-arrange 仍在 DOM 里，只能去点遮罩或右上角 ✕）。
      setArrangeOpen(false);
      setDeleteTargets(null);
      // 镜头检视弹层也是模态：不关它就一直盖着画布，后续点击全被遮罩吃掉
      setSelectedId(null);
      // Esc 退出文本编辑态：文本节点的 textarea 聚焦时，画布快捷键（Delete / Ctrl+A / 方向键微调）
      // 会被"输入态让位"规则全部吞掉，用户必须再点一下空白才能回到画布操作。
      // 这里主动失焦，Esc 一次就把控制权交回画布 —— 符合成熟画布的通用手感。
      const ae = document.activeElement as HTMLElement | null;
      if (ae && (ae.tagName === 'INPUT' || ae.tagName === 'TEXTAREA' || ae.isContentEditable)) {
        ae.blur();
      }
      // Esc 同时取消画布多选；无选中时原样返回 prev，避免每次 Esc 都整批重建节点对象触发重渲染
      // ★ 例外：提示词弹窗打开时，Esc 只关弹窗，**保留画布选中** ——
      //   用户是"选中几张卡 → 点开其中一张改提示词 → Esc 退出"的工作流，
      //   一按 Esc 就把多选清掉、浮动操作条也跟着消失，等于每开一次弹窗就要重新框选。
      if (!editNodeRef.current) {
        setNodes((prev) => (prev.some((n) => n.selected) ? prev.map((n) => (n.selected ? {...n, selected: false} : n)) : prev));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setNodes]);

  const zoomIn = useCallback(() => rf.zoomIn({duration: 200}), [rf]);
  const zoomOut = useCallback(() => rf.zoomOut({duration: 200}), [rf]);

  // 一键导出画布 PNG（html-to-image 对当前视口截图，2x 像素；背景色随主题）
  const exportCanvasPng = useCallback(async () => {
    const el = document.querySelector('.react-flow__viewport') as HTMLElement | null;
    if (!el) return;
    setExporting(true);
    try {
      const root = document.documentElement;
      const isLight = root.getAttribute('data-theme') === 'light';
      const surfaceVar = getComputedStyle(root).getPropertyValue('--surface').trim();
      const backgroundColor = isLight ? '#ffffff' : (surfaceVar || '#17191d');
      const dataUrl = await toPng(el, {pixelRatio: 2, cacheBust: true, backgroundColor});
      const a = document.createElement('a');
      a.href = dataUrl;
      a.download = `canvas-${session}-${currentEp ?? 'all'}.png`;
      a.click();
    } catch (error) {
      console.error('导出画布失败', error);
    } finally {
      setExporting(false);
    }
  }, [session, currentEp]);

  // 镜头 → 所属段：优先按段的 shot_idxs 命中，回退按 segment_id（+集）匹配。
  // 统一从这里解析，避免各处"按镜头 ep_id 现拼段卡 id"与段对象自身 id 不一致。
  const segOfShot = useCallback((idx: number, segmentId?: number | null, epId?: string | null): CanvasSegment | null => {
    if (!canvas) return null;
    const overview = currentEp === null;
    const byIdxs = effectiveSegments.find((seg) => segShotIdxsOf(seg, canvas.shots, {overview}).includes(idx));
    if (byIdxs) return byIdxs;
    if (segmentId === null || segmentId === undefined) return null;
    return effectiveSegments.find((seg) => String(seg.segment_id) === String(segmentId)
      && (epId === null || epId === undefined || seg.ep_id == null || (seg.ep_id ?? null) === epId)) ?? null;
  }, [canvas, effectiveSegments, currentEp]);

  // 段卡节点 id → 段对象（右键菜单/定位都按节点 id 反查，兼容总览的 seg-<ep>-<n> 形式）
  const segByNodeId = useCallback((nodeId: string): CanvasSegment | null => {
    if (!canvas) return null;
    return effectiveSegments.find((seg) => segNodeId(seg) === nodeId)
      ?? effectiveSegments.find((seg) => nodeId === `seg-${seg.segment_id}`)
      ?? null;
  }, [canvas, effectiveSegments, segNodeId]);

  // 镜头顺序 → 画布联动定位：平移/缩放到该段所在段卡。
  // 绝对坐标沿 parentId 链累加：整剧总览模式下属节点 position 是相对坐标，
  // 直接用会跳到错误位置（旧实现在总览模式定位失效）。
  const focusSegmentOf = useCallback((seg: CanvasSegment) => {
    const targetId = segNodeId(seg);
    const abs = absolutePositionOf(targetId, dragNodesRef.current);
    if (!abs) return;
    rf.setCenter(abs.x + 160, abs.y + 140, {zoom: Math.max(rf.getZoom(), 0.9), duration: 420});
  }, [rf, segNodeId]);

  const contextNode = useMemo(() => {
    if (!contextMenu?.nodeId) return null;
    return nodesRef.current.find((node) => node.id === contextMenu.nodeId) ?? null;
  }, [contextMenu, undoTick]);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  const contextMenuNote = useMemo(() => {
    if (!contextMenu?.nodeId?.startsWith('note-')) return null;
    const noteId = contextMenu.nodeId.slice('note-'.length);
    return notes.find((note) => note.id === noteId) ?? null;
  }, [contextMenu, notes]);

  const lightboxImages = useMemo(
    () => mediaNodes.filter((node) => node.kind === 'image' && node.mediaUrl),
    [mediaNodes],
  );
  const lightboxIndex = zoomTarget
    ? lightboxImages.findIndex((node) => node.mediaUrl === zoomTarget)
    : -1;
  const navigateLightbox = useCallback((direction: number) => {
    if (lightboxIndex < 0 || lightboxImages.length < 2) return;
    const next = (lightboxIndex + direction + lightboxImages.length) % lightboxImages.length;
    setZoomTarget(lightboxImages[next].mediaUrl || null);
  }, [lightboxImages, lightboxIndex]);

  useEffect(() => {
    if (!zoomTarget) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') navigateLightbox(-1);
      if (event.key === 'ArrowRight') navigateLightbox(1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigateLightbox, zoomTarget]);

  const syncLabel = saveState === 'error' || saveError
    ? '保存失败'
    : saveState === 'saving' || savingShot !== null
      ? '保存中…'
      : dirtyShots.size > 0
        ? `${dirtyShots.size} 处未保存`
        : '已同步';
  const completionPercent = canvas && canvas.shots.length > 0
    ? Math.round((canvas.shots.filter((shot) => shot.clip_url).length / canvas.shots.length) * 100)
    : 0;
  const qualityLabel = qualityReport?.available
    ? qualityReport.passed
      ? '挂接通过'
      : `挂接 ${Number(qualityReport.levels?.fail || 0)} 段未通过`
    : '未检查挂接';

  const canvasHeaderBody = (
    <div className="workspace-title-row">
          <div className="workspace-title-copy">
            <strong>画布</strong>
            <span>
              {currentEp ? fmtEp(currentEp) : '项目画布'} · 资产 {canvas ? canvas.characters.length + canvas.scenes.length + canvas.props.length + canvas.voices.length : 0}
            </span>
          </div>
          {actions}
          <div ref={statusMenuRef} className="canvas-menu-wrap">
            <button
              type="button"
              className={`canvas-status-trigger ${openMenu === 'status' ? 'is-open' : ''} ${saveState === 'error' || saveError ? 'is-error' : dirtyShots.size > 0 ? 'is-dirty' : ''}`}
              onClick={() => setOpenMenu(openMenu === 'status' ? null : 'status')}
              title="保存、生产进度与挂接检查"
            >
              <span className="canvas-status-dot" aria-hidden="true" />
              {syncLabel}
              {canvas && canvas.shots.length > 0 && <span className="canvas-status-progress">完成率 {completionPercent}%</span>}
              <span className={`canvas-status-quality ${qualityReport?.available ? (qualityReport.passed ? 'is-pass' : 'is-fail') : ''}`}>{qualityLabel}</span>
              <ChevronDown size={12} strokeWidth={2.2} aria-hidden="true" />
            </button>
            {openMenu === 'status' && (
              <div className="canvas-header-menu canvas-status-menu">
                <div className="canvas-status-menu-head">画布状态</div>
                <div className="canvas-status-row"><span>自动保存</span><strong>{syncLabel}</strong></div>
                <div className="canvas-status-row"><span>生产统计</span><strong>{canvas && canvas.shots.length > 0 ? `${effectiveSegments.length} 段 · ${canvas.shots.length} 镜头 · ${canvas.shots.filter((s) => s.clip_url).length} 已出片` : '暂无分镜'}</strong></div>
                <div className="canvas-status-row"><span>完成率</span><strong>{completionPercent}%</strong></div>
                <div className="canvas-status-row"><span>关键帧</span><strong>{canvas ? canvas.shots.filter((s) => s.keyframe_url).length : 0}</strong></div>
                <button
                  type="button"
                  className="canvas-status-check"
                  disabled={qualityRecomputing}
                  onClick={() => {
                    setOpenMenu(null);
                    recomputeQuality();
                  }}
                >
                  <Link2 size={12} strokeWidth={1.9} aria-hidden="true" />
                  {qualityRecomputing ? '检查中…' : qualityReport?.stale ? '刷新过期挂接检查' : '重新检查挂接'}
                </button>
              </div>
            )}
          </div>
          <div className="canvas-header-tools">
            <div className="canvas-search-wrap">
              <input
                className="canvas-asset-search"
                type="search"
                placeholder="搜索资产 / 段 / 镜头 / 备注 / 节点…"
                value={assetQuery}
                onFocus={() => setSearchFocused(true)}
                onBlur={() => setTimeout(() => setSearchFocused(false), 160)}
                onChange={(event) => { setAssetQuery(event.target.value); setSearchIndex(0); }}
                onKeyDown={(event) => {
                  const total = searchResults.length;
                  if (total === 0) return;
                  if (event.key === 'ArrowDown') {
                    event.preventDefault();
                    setSearchIndex((i) => (i + 1) % total);
                  } else if (event.key === 'ArrowUp') {
                    event.preventDefault();
                    setSearchIndex((i) => (i - 1 + total) % total);
                  } else if (event.key === 'Enter') {
                    event.preventDefault();
                    const hit = searchResults[Math.min(searchIndex, total - 1)];
                    if (hit) locateHit(hit);
                  }
                }}
                title="全局搜索：资产/段/镜头/备注/画布节点；↑↓ 选择，Enter 定位过去"
              />
              {assetQuery && (
                <button type="button" onClick={() => setAssetQuery('')} title="清空搜索"><X size={12} strokeWidth={2.2} aria-hidden="true" /></button>
              )}
              {searchFocused && assetQuery.trim().length > 0 && (
                <div className="canvas-search-results">
                  {searchResults.length === 0 ? (
                    <p className="canvas-search-empty">未找到匹配项</p>
                  ) : (
                    searchResults.map((hit, i) => (
                      <button
                        key={i}
                        type="button"
                        className={`canvas-search-hit ${i === searchIndex ? 'is-active' : ''}`}
                        onMouseEnter={() => setSearchIndex(i)}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => locateHit(hit)}
                      >
                        <span className={`canvas-search-kind is-${hit.kind}`}>{hit.kind}</span>
                        <span className="canvas-search-label">{hit.label}</span>
                        {hit.sub && <span className="canvas-search-sub">{hit.sub}</span>}
                      </button>
                    ))
                  )}
                </div>
              )}
            </div>
            <div ref={toolsMenuRef} className="canvas-menu-wrap">
              <button ref={toolsTriggerRef} type="button" className={openMenu === 'tools' ? 'is-open' : ''} onClick={() => setOpenMenu(openMenu === 'tools' ? null : 'tools')} title="导出、布局与帮助">
                <SlidersHorizontal size={13} strokeWidth={1.9} aria-hidden="true" />
                工具<ChevronDown size={13} strokeWidth={2} aria-hidden="true" />
              </button>
              {openMenu === 'tools' && (
                <div className="canvas-header-menu canvas-tools-menu">
                  <div className="canvas-tools-section">素材</div>
                  <button type="button" onClick={() => { setOpenMenu(null); openMaterialPicker(); }}>
                    <Upload size={13} strokeWidth={1.9} aria-hidden="true" />上传素材
                  </button>
                  <div className="canvas-tools-section">导出</div>
                  <button type="button" disabled={exporting} onClick={() => { setOpenMenu(null); void exportCanvasPng(); }}>{exporting ? '导出中…' : '导出画布 PNG'}</button>
                  <button type="button" onClick={() => { setOpenMenu(null); exportStoryboardMd(); }}>导出分镜 Markdown</button>
                  <button type="button" onClick={() => { setOpenMenu(null); exportShotsCsv(); }}>导出镜头清单 CSV</button>
                  <div className="canvas-tools-section">布局</div>
                  <button type="button" onClick={() => { setOpenMenu(null); persistLayout(positionRef.current, userEdges, deletedAutoEdgeIds); setToast('布局已保存'); }}>保存当前布局</button>
                  <button type="button" onClick={() => { setOpenMenu(null); resetLayout(); }}>恢复默认布局</button>
                  <button type="button" title="仅恢复节点位置和连线；技能生成的投喂载荷请用顶部「项目设置 → 导入投喂载荷」" onClick={() => { setOpenMenu(null); layoutFileRef.current?.click(); }}>导入布局 JSON</button>
                  <button type="button" onClick={() => { setOpenMenu(null); exportLayoutJson(); }}>导出布局 JSON</button>
                  <div className="canvas-tools-section">版本</div>
                  <button type="button" onClick={() => { toolsTriggerRef.current?.focus(); setOpenMenu(null); setVersionsOpen(true); }}>
                    <History size={13} strokeWidth={1.9} aria-hidden="true" />
                    项目版本历史
                  </button>
                  <button type="button" onClick={() => { toolsTriggerRef.current?.focus(); setOpenMenu(null); setBudgetOpen(true); }}>
                    <Wallet size={13} strokeWidth={1.9} aria-hidden="true" />
                    生成预算保护
                  </button>
                  <div className="canvas-tools-section">帮助</div>
                  <button type="button" onClick={() => { setOpenMenu(null); setHelpOpen(true); }}>快捷键与操作说明</button>
                </div>
              )}
            </div>
            <input
              ref={layoutFileRef}
              type="file"
              accept="application/json,.json"
              style={{display: 'none'}}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (!file) return;
                const reader = new FileReader();
                reader.onload = () => importLayoutJson(String(reader.result ?? ''));
                reader.readAsText(file);
                event.target.value = '';
              }}
            />
            <input
              ref={materialFileInputRef}
              type="file"
              accept="image/*,audio/*,video/*,.png,.jpg,.jpeg,.webp,.gif,.bmp,.avif,.mp3,.wav,.ogg,.m4a,.flac,.aac,.mp4,.webm,.mov"
              multiple
              style={{display: 'none'}}
              onChange={(event) => {
                const files = Array.from(event.target.files || []);
                event.target.value = '';
                const origin = canvasUploadOriginRef.current || undefined;
                canvasUploadOriginRef.current = null;
                if (files.length > 0) void uploadFilesToCanvas(files, origin);
              }}
            />
            <input
              ref={nodeLocalUploadInputRef}
              type="file"
              accept={nodeLocalUploadAccept}
              style={{display: 'none'}}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void uploadNodeLocalFromMenu(file);
              }}
            />
            <button
              type="button"
              className={`canvas-task-center-trigger ${taskCenterOpen ? 'is-open' : ''}`}
              onClick={() => setTaskCenterOpen((value) => !value)}
              title="生成任务中心"
            >
              <ListChecks size={13} strokeWidth={1.9} aria-hidden="true" />
              任务
              {activeGenerationTaskCount > 0 && (
                <i>{activeGenerationTaskCount}</i>
              )}
            </button>
          </div>
    </div>
  );

  return (
    <div className="canvas-view">
      {topbarSlot
        ? ReactDOM.createPortal(<div className="canvas-inline-topbar">{canvasHeaderBody}</div>, topbarSlot)
        : <header className="workspace-header">{canvasHeaderBody}</header>}

      {error && <div className="inline-error canvas-error" role="alert"><span>{error}</span></div>}
      {saveError && <div className="inline-error canvas-error" role="alert"><span>保存失败：{saveError}</span><button onClick={() => setSaveError('')} aria-label="关闭"><X size={12} strokeWidth={2.2} aria-hidden="true" /></button></div>}
      {toast && <div className="canvas-toast" onClick={() => setToast('')}>{toast}</div>}

      {loading && !canvas ? (
        <div className="ledger-loading"><div className="app-initializing-spinner" /><span>正在整理画布素材…</span></div>
      ) : !canvas ? (
        <div className="ledger-empty-state">
          <strong>还没有可展示的画布</strong>
          <p>当前项目尚未生成镜头或资产；选中已开工的项目后再看。</p>
          {onCreate && <button type="button" className="primary-btn" onClick={onCreate}>回到工作区</button>}
        </div>
      ) : (
        <div
          ref={stageRef}
          className={`canvas-stage ${selectedShot ? 'has-inspector' : ''}`}
          data-bg={bgMode}
          onDragOver={(e) => { e.preventDefault(); setDropArmed(true); }}
          onDragLeave={(e) => { if (e.currentTarget === e.target) setDropArmed(false); }}
          onDrop={handleCanvasDrop}
        >
          {dropArmed && <div className="canvas-drop-overlay">松开鼠标，素材将变成节点</div>}
          {mediaNodes.length === 0 && notes.length === 0 && (
            <div className="canvas-empty-workspace">
              <div className="canvas-empty-workspace-copy">
                <strong>开始创建你的画布</strong>
                <span>上传本地素材，或先创建一个空白节点</span>
              </div>
              <div className="canvas-empty-workspace-actions">
                <button type="button" className="is-primary" onClick={() => openMaterialPicker()}>
                  <Upload size={15} strokeWidth={1.9} aria-hidden="true" />
                  上传素材
                </button>
                <button type="button" onClick={() => {
                  const point = rf.screenToFlowPosition({x: window.innerWidth / 2, y: window.innerHeight / 2});
                  addMediaNode('image', point.x - 110, point.y - 120);
                }}>
                  <ImageGlyph size={15} strokeWidth={1.8} aria-hidden="true" />
                  图片节点
                </button>
                <button type="button" onClick={() => {
                  const point = rf.screenToFlowPosition({x: window.innerWidth / 2, y: window.innerHeight / 2});
                  addMediaNode('video', point.x - 110, point.y - 120);
                }}>
                  <Video size={15} strokeWidth={1.8} aria-hidden="true" />
                  视频节点
                </button>
                <button type="button" onClick={() => {
                  const point = rf.screenToFlowPosition({x: window.innerWidth / 2, y: window.innerHeight / 2});
                  addMediaNode('audio', point.x - 110, point.y - 70);
                }}>
                  <Music size={15} strokeWidth={1.8} aria-hidden="true" />
                  音频节点
                </button>
              </div>
            </div>
          )}
          {largeCanvasOverview && mediaNodes.length > 0 && (
            <LargeCanvasOverview
              nodes={mediaNodes}
              edges={edges}
              onFocus={focusOverviewNode}
              onClose={returnFromOverview}
            />
          )}
          <div className="canvas-bg-switch" title="画布底纹">
            {([
              ['dots', '点阵'],
              ['lines', '网格'],
              ['blank', '空白'],
            ] as const).map(([k, label]) => (
              <button
                key={k}
                type="button"
                className={bgMode === k ? 'is-active' : ''}
                onClick={() => setBgMode(k)}
              >{label}</button>
            ))}
          </div>
          {activeGenerationNodeCount > 0 && (
            <div className="canvas-task-chip" title="Agent 正在生成，完成后自动刷新画布">
              <span className="canvas-task-spin" aria-hidden="true" />
              <span className="canvas-task-label">生成中 {activeGenerationNodeCount} 项…</span>
            </div>
          )}
          {loading && (
            <div className="canvas-reload-bar" role="status">
              <span className="canvas-reload-spin" />
              <span>正在加载{currentEp ? ` ${fmtEp(currentEp)}` : '项目画布'}…（画布内容保留）</span>
            </div>
          )}
          <aside className={`canvas-ep-list ${epListOpen ? 'is-open' : ''}`} aria-hidden={!epListOpen}>
            <header className="canvas-ep-list-header">
              <strong>{railTab === 'eps' ? '画布列表' : '资产托盘'}</strong>
              <span className="canvas-ep-list-count">{railTab === 'eps' ? (canvas.meta.episodes.length || 1) : (canvas.characters.length + canvas.scenes.length + canvas.props.length + canvas.voices.length)}</span>
              {railTab === 'eps' && (
                <button
                  type="button"
                  className="canvas-ep-list-add"
                  onClick={beginEpisodeCreate}
                  aria-label="新建集数"
                  title="新建集数"
                ><Plus size={12} strokeWidth={2.2} aria-hidden="true" /></button>
              )}
              <button type="button" className="canvas-ep-list-close" onClick={() => setEpListOpen(false)} aria-label="收起" title="收起"><X size={13} strokeWidth={2.2} aria-hidden="true" /></button>
            </header>
            {/* 剧集 / 资产切换
                ★ 用户原话：「剧集和资产切换按钮也太丑了」。
                  旧版是两个"下边框高亮"的 Tab：20 度的斜切感靠 inset 阴影撑，
                  在深色面板上像两条残线，而且两个按钮之间还有 4px 裂缝。
                  新版=标准分段控件：一条下沉槽底 + 一个滑动的选中块，
                  带图标与数量，一眼看出"我在哪一格"。 */}
            <div className="canvas-rail-tabs" role="tablist" aria-label="画布列表切换">
              <button
                type="button"
                role="tab"
                aria-selected={railTab === 'eps'}
                className={`canvas-rail-tab ${railTab === 'eps' ? 'is-active' : ''}`}
                onClick={() => setRailTab('eps')}
              >
                <Film size={12} strokeWidth={1.9} aria-hidden="true" />
                <span>剧集</span>
                <em>{canvas.meta.episodes.length || 1}</em>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={railTab === 'assets'}
                className={`canvas-rail-tab ${railTab === 'assets' ? 'is-active' : ''}`}
                onClick={() => setRailTab('assets')}
              >
                <Package size={12} strokeWidth={1.9} aria-hidden="true" />
                <span>资产</span>
                <em>{canvas.characters.length + canvas.scenes.length + canvas.props.length + canvas.voices.length}</em>
              </button>
            </div>
            {railTab === 'eps' && episodeCreateOpen && (
              <form className="canvas-ep-create" onSubmit={submitEpisodeCreate}>
                <div className="canvas-ep-create-fields">
                  <label>
                    <span>集数</span>
                    <input
                      value={episodeDraftId}
                      onChange={(event) => {
                        setEpisodeDraftId(event.target.value.toUpperCase());
                        setEpisodeCreateError('');
                      }}
                      placeholder="EP002"
                      maxLength={6}
                      aria-label="集编号"
                    />
                  </label>
                  <label>
                    <span>标题 <i>可选</i></span>
                    <input
                      value={episodeDraftTitle}
                      onChange={(event) => setEpisodeDraftTitle(event.target.value)}
                      placeholder="留空自动命名"
                      maxLength={40}
                      aria-label="集标题"
                    />
                  </label>
                </div>
                {episodeCreateError && <p role="alert">{episodeCreateError}</p>}
                <div className="canvas-ep-create-actions">
                  <button type="button" onClick={() => setEpisodeCreateOpen(false)} disabled={episodeCreating}>取消</button>
                  <button type="submit" className="primary" disabled={episodeCreating || !episodeDraftId.trim()}>
                    {episodeCreating ? '创建中…' : '创建'}
                  </button>
                </div>
              </form>
            )}
            {railTab === 'eps' ? (
              <div className="canvas-ep-list-body">
                {canvas.meta.episodes.map((ep) => {
                  const baseEpisodeLabel = fmtEp(ep.ep_id);
                  const episodeLabel = ep.title
                    && ep.title !== ep.ep_id
                    && ep.title !== baseEpisodeLabel
                    ? `${baseEpisodeLabel} · ${ep.title}`
                    : baseEpisodeLabel;
                  // 计划镜数（storyboard.json）与落地镜数（shot_description.json）不一致时
                  // 显示"5/32"：只报计划数会让人以为分镜做完了，进渲染才发现门禁拒。
                  const planned = ep.shot_count ?? null;
                  const described = ep.described_count ?? null;
                  const partial = described !== null && planned !== null && described !== planned;
                  const badge = ep.final_ready
                    ? '成片'
                    : ep.partial_render
                      // 残缺成片：final_video.mp4 在盘但缺段/缺镜，显示"段 7/14"而不是"成片✓"
                      ? `残缺段 ${ep.segments_rendered ?? 0}/${ep.segments_total ?? 0}`
                      : partial
                        ? `${described}/${planned}镜`
                        : planned !== null
                          ? `${planned}镜`
                          : '待处理';
                  return (
                    <div key={ep.ep_id} className={`canvas-ep-row ${currentEp === ep.ep_id ? 'is-active' : ''}`}>
                      <button
                        type="button"
                        className="canvas-ep-select"
                        onClick={() => setCurrentEp(ep.ep_id)}
                        title={`${episodeLabel} · ${badge}${partial ? '（分镜未完成，续跑后自动补齐）' : ''}${ep.partial_render ? '（成片残缺，续跑补全缺失段后再拼成片）' : ''}${ep.final_ready ? ' · 已成片' : ''}`}
                      >
                        <span className="ep-name">{episodeLabel}</span>
                        <span className={`ep-badge ${ep.final_ready ? 'is-done' : ''} ${ep.partial_render ? 'is-partial' : partial ? 'is-partial' : ''}`}>
                          {ep.final_ready && <Check size={10} strokeWidth={2.6} aria-hidden="true" />}
                          {badge}
                        </span>
                      </button>
                      <button
                        type="button"
                        className="canvas-ep-more"
                        onClick={(event) => {
                          event.stopPropagation();
                          if (currentEp !== ep.ep_id) setCurrentEp(ep.ep_id);
                          const rect = event.currentTarget.getBoundingClientRect();
                          setEpisodeMenu({
                            epId: ep.ep_id,
                            title: episodeLabel,
                            left: rect.right - 8,
                            top: rect.bottom + 4,
                          });
                        }}
                        disabled={episodeDeleting}
                        aria-label={`${episodeLabel}操作`}
                        title="集数操作"
                      ><MoreHorizontal size={13} strokeWidth={2} aria-hidden="true" /></button>
                    </div>
                  );
                })}
                {canvas.meta.episodes.length === 0 && (
                  <p className="canvas-ep-list-hint">单集项目 · 直接编辑本集画布</p>
                )}
              </div>
            ) : (
              <div className="canvas-asset-tray-body">
                {/* 资产托盘：只列画布媒体节点（卡片），不再显示原生资产分组/分段树。
                    分组按媒体节点标题前缀（角色｜/场景｜/道具｜）+ kind（音色/视频）区分，
                    与画布上的独立媒体卡片一一对应，点击定位。 */}
                {/* ★ 分组要互斥：原来「资产图」用 kind==='image' 兜底，
                    又把角色/场景/道具（它们也是 image）按标题前缀先捞了一遍 →
                    9 张图在列表里出现两遍（角色5 + 场景2 + 道具2 + 资产图9 = 18 条）。
                    现在按顺序消费：先被前缀组认领的图，不再进「资产图」，
                    「资产图」只剩前缀不匹配的散图。 */}
                {(() => {
                  const claimed = new Set(
                    mediaNodes
                      .filter((mn) => mn.kind === 'image' && /^(角色|场景|道具)[｜|]/.test(mn.title))
                      .map((mn) => mn.id),
                  );
                  const groups = ([
                    {label: '角色', key: 'role', Icon: UserRound, test: (mn: CanvasMediaNode) => /^角色[｜|]/.test(mn.title)},
                    {label: '场景', key: 'scene', Icon: Mountain, test: (mn: CanvasMediaNode) => /^场景[｜|]/.test(mn.title)},
                    {label: '道具', key: 'prop', Icon: Box, test: (mn: CanvasMediaNode) => /^道具[｜|]/.test(mn.title)},
                    // 音色：单独组，名字加「音色」后缀区分同名的角色图
                    {label: '音色', key: 'voice', Icon: AudioLines, test: (mn: CanvasMediaNode) => mn.kind === 'audio'},
                    {label: '视频', key: 'video', Icon: Clapperboard, test: (mn: CanvasMediaNode) => mn.kind === 'video'},
                    // 资产图：排除已被前缀组认领的（否则重复）
                    {label: '资产图', key: 'image', Icon: ImageGlyph, test: (mn: CanvasMediaNode) => mn.kind === 'image' && !claimed.has(mn.id)},
                    {label: '文本', key: 'text', Icon: Type, test: (mn: CanvasMediaNode) => mn.kind === 'text'},
                  ] as const).map((g) => ({...g, items: mediaNodes.filter(g.test)}));
                  return groups.map((g) => {
                  const items = g.items;
                  if (items.length === 0) return null;
                  return (
                    <div key={g.label} className={`canvas-tray-group is-${g.key}`}>
                      <div className="canvas-tray-group-title">
                        <span className="canvas-tray-group-ico"><g.Icon size={13} strokeWidth={1.8} aria-hidden="true" /></span>
                        <span>{g.label}</span>
                        <em>{items.length}</em>
                      </div>
                      {items.map((mn) => (
                        <button
                          key={mn.id}
                          type="button"
                          data-node-id={mn.id}
                          className={`canvas-tray-item${selectedId === `media-${mn.id}` ? ' is-active' : ''}`}
                          title={`${mn.title}${mn.kind === 'audio' ? '（音色）' : ''} · 点击在画布中定位`}
                          onClick={() => locateHit({kind: g.label as SearchHit['kind'], label: mn.title, nodeId: `media-${mn.id}`})}
                          onPointerEnter={(event) => {
                            if (mn.kind === 'image' && mn.mediaUrl) {
                              showTrayPreview(mn.mediaUrl, mn.title, event.currentTarget);
                            }
                          }}
                          onPointerLeave={() => setTrayPreview(null)}
                          onFocus={() => setTrayPreview(null)}
                        >
                          <span className={`canvas-tray-thumb-wrap is-${g.key}`}>
                            {mn.mediaUrl && mn.kind !== 'audio'
                              ? mn.kind === 'video'
                                ? <video className="canvas-tray-thumb" src={mn.mediaUrl} muted playsInline preload="metadata" aria-hidden="true" />
                                : <img className="canvas-tray-thumb" src={mn.mediaUrl} alt="" loading="lazy" />
                              : <span className={`canvas-tray-thumb is-placeholder is-${g.key}`}>
                                  <g.Icon size={17} strokeWidth={1.65} aria-hidden="true" />
                                </span>}
                          </span>
                          <span className="canvas-tray-name">
                            <span>{mn.title.replace(/^[^｜|]*[｜|]\s*/, '')}</span>
                            {/* ★ 音色组与角色图组同名（都叫「赵铁柱（图1）」），列表里根本分不清 */}
                            {mn.kind === 'audio' && <i className="canvas-tray-kind">音色</i>}
                          </span>
                        </button>
                      ))}
                    </div>
                  );
                  });
                })()}
                <p className="canvas-ep-list-hint">画布媒体卡片（角色/场景/道具/音色/视频）· 点击在画布中定位；改绑在画布上拖线或删线</p>
              </div>
            )}
          </aside>
          {episodeMenu && createPortal(
            <div
              className="canvas-ep-action-menu"
              role="menu"
              style={{left: episodeMenu.left, top: episodeMenu.top}}
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                className="is-danger"
                disabled={episodeDeleting}
                onClick={() => {
                  const target = episodeMenu;
                  setEpisodeMenu(null);
                  void removeEpisode(target.epId, target.title);
                }}
              >
                <Trash2 size={13} strokeWidth={1.9} aria-hidden="true" />
                删除集数
              </button>
            </div>,
            document.body,
          )}

          {!epListOpen && (
            <button type="button" className="canvas-ep-list-rail" onClick={() => setEpListOpen(true)} title="展开画布列表">
              <span className="canvas-ep-list-rail-ico"><ChevronRight size={12} strokeWidth={2.2} aria-hidden="true" /></span>
              <span>画布列表</span>
            </button>
          )}
          {/* 空集引导：0 镜时不显示"段 0 · 镜头 0"式的假空态，直接说明下一步该做什么 */}
          {canvas && canvas.shots.length === 0 && (
            <div className="canvas-empty-guide" role="status">
              <strong>{currentEp ? `${fmtEp(currentEp)}` : '本项目'}暂无分镜镜头</strong>
              <span>
                {currentEp
                  ? '先在「分镜」阶段为本集生成镜头（确认剧本后运行分镜生成），画布会自动汇入段卡、关键帧与片段。'
                  : '各集都还没有镜头：先在第 1 集生成分镜，再回画布查看。'}
              </span>
            </div>
          )}
          <ReactFlow
            className={`canvas-flow ${draggingNode ? 'is-node-dragging' : ''} ${connecting ? 'is-connecting' : ''} ${selectedCount >= 2 ? 'has-multi-selection' : ''} ${edgesHidden ? 'is-edges-hidden' : ''}`}
            onConnectStart={() => setConnecting(true)}
            onConnectEnd={() => setConnecting(false)}
            data-mouse-mode={mouseMode}
            nodes={largeCanvasOverview ? [] : nodes}
            edges={largeCanvasOverview ? [] : renderedEdges}
            edgeTypes={CANVAS_EDGE_TYPES}
            onNodesChange={handleNodesChange}
            onEdgesChange={onEdgesChange}
            onPointerDownCapture={onCanvasPointerDownCapture}
            onPointerUpCapture={onCanvasPointerRelease}
            onPointerCancelCapture={onCanvasPointerRelease}
            onLostPointerCapture={() => {
              dragPointerIdRef.current = null;
              clearStuckDrag();
            }}
            /* 注意：不要在这里加 onEdgesDelete —— 本项目的 edges 是由 flowEdges 全程派生的
               （见下方 setEdges 的 useEffect），React Flow 内部删除不会走 onEdgesDelete。
               Del/Backspace 删线的实际入口是 onBeforeDelete，那里已同步清理 refs。 */
            onConnect={onConnect}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onNodeContextMenu={onNodeContextMenu}
            onPaneContextMenu={onPaneContextMenu}
            onBeforeDelete={onBeforeDelete}
            onNodeDragStart={onNodeDragStart}
            onNodeDrag={onNodeDrag}
            onNodeDragStop={onNodeDragStop}
            nodeTypes={nodeTypes}
            minZoom={0.12}
            // ★ 第 18 轮：2.5 → 8。用户原话「即梦的节点线放大到 800% 都很清晰，
            //   我们的放大了就有噪点不清晰」—— 根因之一就是上限只有 250%，
            //   用户以为自己在"放大"，实际已经是上限（再滚就停住，视觉上像被"卡糊"）。
            maxZoom={8}
            deleteKeyCode={['Backspace', 'Delete']}
            snapToGrid={snapGrid}
            snapGrid={CANVAS_SNAP_GRID}
            onMove={(_evt, viewport) => {
              setZoomPct(Math.round(viewport.zoom * 100));
              updateLargeCanvasViewport(viewport);
              rememberViewport(viewport);
              syncSelBar();
              syncGroupFrames();
            }}
            // 连线中点的剪断按钮挂在 EdgeLabelRenderer 里，会被画布的 zoom 一起缩放：
            // 缩到 38% 时 20px 的按钮只剩 7.6px，剪刀图标等于看不见。
            // 这里注入 1/zoom 让按钮恒为 20px 屏幕像素（钳到 6x 防极端缩放时爆掉）。
            // ★ 流光参数也随 zoom 注入：non-scaling-stroke 让 dash 按屏幕像素算，放大后
            //   亮段 5px 相对整条路径的密度被稀释，视觉上流光"消失"（用户实测反馈）。
            //   放大时把亮段拉长、空隙压短，保持每屏可见的流光段数基本恒定。
            style={{
              '--edge-ui-scale': String(Math.min(6, Math.max(0.5, 100 / Math.max(12, zoomPct)))),
              // ★ 端口收缩系数（第 18 轮）：让端口随卡片一起缩。
              //   用户诉求：「画布缩小后节点不要太大」。全览时 zoom≈19%，
              //   若端口恒 22px 屏幕像素，相对 55px 宽的卡片就是两个大球。
              //   ★ 曲线必须是「低区陡、高区平」：
              //     直接用 sqrt(zoom) 时 61% 只给 0.78 → 端口缩到 17px，还没到全览就小了，
              //     用户会觉得"稍一缩小端口就变mini"。
              //     改用 (zoom/1.0)^0.35：19%→0.56、35%→0.69、61%→0.85、100%→1.0。
              //     配合 CSS 的 clamp 12~22px，结果：
              //       19%  → 22*0.56=12.3 ≈ 12px（跟卡片 55px 成比例，不臃肿）
              //       61%  → 22*0.85=18.7px（够点）
              //       100%+→ 22px 封顶
              '--port-shrink': String(Math.min(1, Math.max(0.5, Math.pow(Math.max(12, zoomPct) / 100, 0.35)))),
              // At overview zoom, icon buttons shrink below usable hit size.
              // Hide detail-only controls instead of showing 2-4px specks; the
              // card itself remains clickable to open the full prompt panel.
              '--card-control-visible': zoomPct < 30 ? 'hidden' : 'visible',
              ...edgeFlowVars(zoomPct / 100),
            } as CSSProperties}
            onSelectionChange={onSelectionChangeHandler}
            panOnDrag={mouseMode === 'hand' || mouseMode === 'connect' || panning}
            selectionOnDrag={mouseMode === 'select'}
            selectionMode={SelectionMode.Partial}
            onSelectionEnd={onSelectionEnd}
            selectionKeyCode={['Shift']}
            multiSelectionKeyCode={['Shift', 'Meta', 'Control']}
            nodesDraggable={mouseMode === 'select'}
            // 端口连线的可用性：此前只有「连续模式」才能拖端口，默认「选择模式」下拖端口毫无反应，
            // 是"手动连线连不上"的根因。成熟画布应当在常规选择态就能从端口拉线，只有抓手模式才禁用。
            nodesConnectable={mouseMode !== 'hand'}
            // 改接：把已有连线的端点拖到别的节点即可重新接线，不必删掉重连
            edgesReconnectable={mouseMode !== 'hand'}
            onReconnect={onReconnect}
            onReconnectStart={() => setConnecting(true)}
            onReconnectEnd={() => setConnecting(false)}
            isValidConnection={(c) => !!c.source && !!c.target && c.source !== c.target}
            elementsSelectable={mouseMode !== 'hand'}
            proOptions={{hideAttribution: true}}
          >
            <Background gap={24} size={1} />
            <MiniMap
              pannable
              zoomable
              className="canvas-minimap"
              nodeClassName={(node) => {
                // 按节点类型着色（即梦同款）：媒体卡 / 备注 / 分段卡 / 资产卡 一眼可辨
                if (node.type === 'media') {
                  const kind = (node.data as MediaNodeData | undefined)?.node?.kind;
                  return kind ? `is-media is-media-${kind}` : 'is-media';
                }
                if (node.type === 'note') return 'is-note';
                if (node.type === 'segment') return 'is-segment';
                if (node.type === 'asset-card' || node.type === 'asset-group') return 'is-asset';
                return '';
              }}
            />

            {/* 智能对齐参考线（对标即梦）：挂在 ViewportPortal 里，随画布缩放平移。
                坐标由 updateAlignGuides 命令式写入（每帧 setState 会让整棵树重渲染，拖动必然掉帧）。 */}
            <ViewportPortal>
              <div className="canvas-align-guides" ref={alignGuidesRef} aria-hidden="true">
                <div ref={guideVRef} className="canvas-align-guide is-v" />
                <div ref={guideHRef} className="canvas-align-guide is-h" />
                {/* 读数标签：显示距参考线的剩余偏差（颜色与线同属画布统一蓝 #4d9fff） */}
                <div ref={guideVLabelRef} className="canvas-align-guide-label is-v" />
                <div ref={guideHLabelRef} className="canvas-align-guide-label is-h" />
              </div>
              {/* 编组外框：同样挂在 viewport 内、用**画布坐标**定位 ——
                  位置和缩放由画布 transform 自动带着走，拖卡片时框必然同步（不存在量 DOM 的时序问题）。
                  DOM 由 syncGroupFrames 命令式维护；框本体 pointer-events:none，不吃卡片点击，
                  只有组名标签（可抓取拖整组）和解组按钮可点。 */}
              <div className="canvas-group-frames" ref={groupFrameRef} aria-hidden="true" />
            </ViewportPortal>

            {/* 图例面板（工业级）：节点/连线/状态语义；右下角固定可折叠 */}
            <CanvasLegend isOverview={currentEp === null} />

            {/* 多选浮动操作条（对标即梦）：选中 ≥2 张卡片时自动浮在选区上方，
                不必再去右键或到底部工具栏找。位置随选区实时跟随。
                ★ 提示词弹窗打开时必须隐藏：两者都贴在卡片附近，操作栏（z 22）会被
                  弹窗遮住一半、操作栏自己的下拉（z 30）又会盖到弹窗内容上 ——
                  用户截图反馈"弹窗被挡住了，操作体验一点都不好"。 */}
            {selBar && !editNode && (
              <div
                className={`canvas-selbar${selBar.menuDown ? ' menu-down' : ''}`}
                style={{left: selBar.x, top: selBar.barTop}}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => event.stopPropagation()}
              >
                <span className="canvas-selbar-count" title={`已选中 ${selBar.count} 个节点`}>{selBar.count} 节点</span>
                <span className="canvas-selbar-sep" />
                <button type="button" onClick={() => setNodeZOrder(rf.getNodes().filter((n) => n.selected && (n.id.startsWith(MEDIA_PREFIX) || n.id.startsWith(NOTE_PREFIX))).map((n) => n.id), 'front')} title="把选中的卡片提到最上层" aria-label="置顶">
                  <span className="canvas-selbar-ico" aria-hidden="true">
                    <BringToFront size={14} strokeWidth={1.9} />
                  </span><span className="canvas-selbar-label">置顶</span>
                </button>
                <div className="canvas-selbar-dropwrap">
                  <button type="button" className={selBarMenu === 'arrange' ? 'is-active' : ''} onClick={() => setSelBarMenu(selBarMenu === 'arrange' ? null : 'arrange')} title="对齐 / 分布 / 排序整理（只作用于选中卡片）" aria-label="编组整理">
                    <span className="canvas-selbar-ico" aria-hidden="true">
                      <LayoutGrid size={14} strokeWidth={1.9} />
                    </span><span className="canvas-selbar-label">编组整理</span>
                    <span className="canvas-selbar-caret" aria-hidden="true"><ChevronDown size={11} strokeWidth={2.4} /></span>
                  </button>
                  {selBarMenu === 'arrange' && (
                    <div className="canvas-selbar-dropdown">
                      <div className="canvas-selbar-dropdown-title">对齐</div>
                      <div className="canvas-selbar-grid">
                        <button type="button" onClick={() => { const n = applyGeometry('left'); setToast(n ? `左对齐 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="左边缘对齐"><AlignHorizontalJustifyStart size={14} strokeWidth={1.9} aria-hidden="true" />左</button>
                        <button type="button" onClick={() => { const n = applyGeometry('hcenter'); setToast(n ? `水平居中 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="水平居中对齐"><AlignHorizontalJustifyCenter size={14} strokeWidth={1.9} aria-hidden="true" />中</button>
                        <button type="button" onClick={() => { const n = applyGeometry('right'); setToast(n ? `右对齐 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="右边缘对齐"><AlignHorizontalJustifyEnd size={14} strokeWidth={1.9} aria-hidden="true" />右</button>
                        <button type="button" onClick={() => { const n = applyGeometry('top'); setToast(n ? `顶对齐 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="顶边缘对齐"><AlignVerticalJustifyStart size={14} strokeWidth={1.9} aria-hidden="true" />上</button>
                        <button type="button" onClick={() => { const n = applyGeometry('vcenter'); setToast(n ? `垂直居中 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="垂直居中对齐"><AlignVerticalJustifyCenter size={14} strokeWidth={1.9} aria-hidden="true" />中</button>
                        <button type="button" onClick={() => { const n = applyGeometry('bottom'); setToast(n ? `底对齐 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="底边缘对齐"><AlignVerticalJustifyEnd size={14} strokeWidth={1.9} aria-hidden="true" />下</button>
                      </div>
                      <div className="canvas-selbar-dropdown-title">分布</div>
                      <div className="canvas-selbar-grid">
                        <button type="button" onClick={() => { const n = applyGeometry('distH'); setToast(n ? `水平均分 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="水平等间距分布"><AlignHorizontalDistributeCenter size={14} strokeWidth={1.9} aria-hidden="true" />横向等距</button>
                        <button type="button" onClick={() => { const n = applyGeometry('distV'); setToast(n ? `垂直均分 ${n} 个` : '需要至少 2 个选中卡片'); setSelBarMenu(null); }} title="垂直等间距分布"><AlignVerticalDistributeCenter size={14} strokeWidth={1.9} aria-hidden="true" />纵向等距</button>
                      </div>
                      <button type="button" className="is-wide" onClick={() => { setSelBarMenu(null); setArrangeOpen(true); }}>更多整理（排序 / 应用到全部）…</button>
                      {/* 编组：编完之后点组内任一卡即整组选中，拖动就是整组移动 */}
                      <div className="canvas-selbar-dropdown-title">编组</div>
                      <button
                        type="button"
                        className="is-wide"
                        onClick={() => {
                          const ids = rf.getNodes().filter((n) => n.selected && (n.id.startsWith(MEDIA_PREFIX) || n.id.startsWith(NOTE_PREFIX))).map((n) => n.id);
                          groupSelected(ids);
                          setSelBarMenu(null);
                        }}
                      >编组（{selBar?.count ?? 0} 项）</button>
                      {(() => {
                        const ids = rf.getNodes().filter((n) => n.selected && (n.id.startsWith(MEDIA_PREFIX) || n.id.startsWith(NOTE_PREFIX))).map((n) => n.id);
                        const inGroup = ids.some((id) => groupMap.has(id));
                        return inGroup ? (
                          <button type="button" className="is-wide" onClick={() => { ungroupSelected(ids); setSelBarMenu(null); }}>解除编组</button>
                        ) : null;
                      })()}
                    </div>
                  )}
                </div>
                <div className="canvas-selbar-dropwrap">
                  <button type="button" className={selBarMenu === 'link' ? 'is-active' : ''} onClick={() => setSelBarMenu(selBarMenu === 'link' ? null : 'link')} title="把选中的卡片批量连起来" aria-label="批量连线">
                    <span className="canvas-selbar-ico" aria-hidden="true">
                      <Spline size={14} strokeWidth={1.9} />
                    </span><span className="canvas-selbar-label">批量连线</span>
                    <span className="canvas-selbar-caret" aria-hidden="true"><ChevronDown size={11} strokeWidth={2.4} /></span>
                  </button>
                  {selBarMenu === 'link' && (
                    <div className="canvas-selbar-dropdown">
                      <div className="canvas-selbar-dropdown-title">连线方式</div>
                      <button type="button" className="is-wide" onClick={() => { connectSelectedBatch('chain'); setSelBarMenu(null); }}>串成一条链（1→2→3…）</button>
                      <button type="button" className="is-wide" onClick={() => { connectSelectedBatch('mesh'); setSelBarMenu(null); }}>两两互连（全连接）</button>
                      <p className="canvas-selbar-hint">顺序按画布位置从上到下、从左到右</p>
                    </div>
                  )}
                </div>
                <span className="canvas-selbar-sep" />
                <button
                  type="button"
                  className="is-generate"
                  disabled={selBar.generateCount === 0}
                  onClick={() => {
                    const picked = mediaNodesRef.current.filter((mn) => selBar.mediaIds.includes(mn.id)
                      && (mn.kind === 'image' || mn.kind === 'video' || mn.kind === 'audio'));
                    if (picked.length === 0) { setToast('选中里没有可批量生成的媒体节点'); return; }
                    setBatchGenerateTargets(picked);
                    setSelBarMenu(null);
                  }}
                  title={selBar.generateCount === 0
                    ? '选中里没有图片、音频或视频节点'
                    : `检查并批量生成 ${selBar.generateCount} 个媒体节点`}
                  aria-label="批量生成"
                >
                  <span className="canvas-selbar-ico" aria-hidden="true"><Wand2 size={15} strokeWidth={1.9} /></span>
                  <span className="canvas-selbar-label">批量生成{selBar.generateCount > 0 ? ` ${selBar.generateCount}` : ''}</span>
                </button>
                {/* 批量调参：只对图片/视频有意义（文本卡没有模型/比例/清晰度，音频是另一套语音参数） */}
                <button
                  type="button"
                  disabled={selBar.adjustCount === 0}
                  onClick={() => {
                    const picked = mediaNodesRef.current.filter((mn) => selBar.mediaIds.includes(mn.id)
                      && (mn.kind === 'image' || mn.kind === 'video' || mn.kind === 'audio' || mn.kind === 'text'));
                    if (picked.length === 0) { setToast('选中里没有可批量调参的节点'); return; }
                    setBatchParamsTargets(picked);
                    setSelBarMenu(null);
                  }}
                  title={selBar.adjustCount === 0 ? '选中里没有可批量调参的节点' : `批量设置 ${selBar.adjustCount} 个节点的模型 / 比例 / 清晰度 / 音频 / 文本参数`}
                  aria-label="批量调参"
                >
                  <span className="canvas-selbar-ico" aria-hidden="true"><SlidersHorizontal size={14} strokeWidth={1.9} /></span><span className="canvas-selbar-label">批量调参</span>
                </button>
                {/* 批量下载：只认「已生成好、有素材地址」的卡片；一张都没有时禁用并给出原因，
                    避免点了没反应（本项目历史上"点了没反应"是被反复投诉的体验问题）。 */}
                <button
                  type="button"
                  disabled={selBar.readyCount === 0}
                  onClick={() => {
                    const picked = mediaNodesRef.current.filter((mn) => selBar.mediaIds.includes(mn.id));
                    void downloadMediaBatch(picked);
                    setSelBarMenu(null);
                  }}
                  title={selBar.readyCount === 0 ? '选中里没有已生成的素材' : `下载 ${selBar.readyCount} 个已生成的素材`}
                >
                  <span className="canvas-selbar-ico" aria-hidden="true"><Download size={14} strokeWidth={1.9} /></span><span className="canvas-selbar-label">下载{selBar.readyCount > 0 ? ` ${selBar.readyCount}` : ''}</span>
                </button>
                <span className="canvas-selbar-sep" />
                <button type="button" onClick={() => { const n = duplicateSelectedNodes(); if (n > 0) setToast(`已复制 ${n} 个节点`); }} title="就地复制（Ctrl+D）" aria-label="复制">
                  <span className="canvas-selbar-ico" aria-hidden="true"><Copy size={14} strokeWidth={1.9} /></span><span className="canvas-selbar-label">复制</span>
                </button>
                <button
                  type="button"
                  className="is-danger"
                  onClick={() => setDeleteTargets(rf.getNodes().filter((n) => n.selected && (n.id.startsWith(MEDIA_PREFIX) || n.id.startsWith(NOTE_PREFIX))).map((n) => ({id: n.id, type: n.id.startsWith(NOTE_PREFIX) ? 'note' : 'media'})))}
                  title="删除选中的卡片（弹窗确认）"
                  aria-label="删除"
                >
                  <span className="canvas-selbar-ico" aria-hidden="true"><Trash2 size={14} strokeWidth={1.9} /></span><span className="canvas-selbar-label">删除</span>
                </button>
              </div>
            )}

            {/* 选区批量连线抓手（对标即梦）：贴在选区右缘垂直居中，按住拖到目标卡片，
                选中的所有卡片一次性连过去。≥2 张媒体卡才出现——单张直接拖端口就行。 */}
            {selBar && selBar.mediaCount >= 2 && !editNode && (
              <div
                className={`canvas-selbar-linkwrap ${selLinkDrag ? 'is-dragging' : ''}`}
                style={{left: selBar.rightX, top: selBar.midY}}
              >
                {/* 引线：从卡片边缘连到圆点，让抓手看起来是"长在选区上"的，而不是凭空漂在右边 */}
                <span className="canvas-selbar-linkstem" aria-hidden="true" />
                <button
                  type="button"
                  className="canvas-selbar-linkdot"
                  onPointerDown={beginSelLinkDrag}
                  title="拖到另一张卡片：把选中的卡片全部连过去"
                  aria-label="拖拽批量连线"
                >
                  <Plus size={11} strokeWidth={2.6} aria-hidden="true" />
                </button>
              </div>
            )}
            {/* 拖拽批量连线的拉线：从抓手到鼠标的贝塞尔曲线，命中目标时变蓝并加粗。
                覆盖层 pointer-events:none，不挡命中检测（elementFromPoint 靠这个穿透）。 */}
            {selLinkDrag && (
              <svg className="canvas-selbar-linkdrag" aria-hidden="true">
                <path
                  className={selLinkDrag.targetId ? 'is-hot' : ''}
                  d={`M ${selLinkDrag.sx} ${selLinkDrag.sy} C ${selLinkDrag.sx + 60} ${selLinkDrag.sy} ${selLinkDrag.cx - 60} ${selLinkDrag.cy} ${selLinkDrag.cx} ${selLinkDrag.cy}`}
                />
                <circle className={selLinkDrag.targetId ? 'is-hot' : ''} cx={selLinkDrag.cx} cy={selLinkDrag.cy} r={4} />
              </svg>
            )}

            {/* 底部工具栏：模式 + 编辑 + 布局（撤销/重做移除，仅保留快捷键 Ctrl+Z / Ctrl+Shift+Z）

                ★ 2026-10-25 用户要求（附即梦截图）「这些工具条 只显示图标 不显示文字，跟即梦一样
                  这样不占太长空间」：
                  每个按钮的文字收进 `<span className="canvas-tool-label">`，由 CSS 统一隐藏，
                  图标本身保留 `title`（悬停出原生提示）+ `aria-label`（读屏），信息一点不丢。
                  ★ 为什么不直接删掉文字节点：① 读屏/无 tooltip 场景还需要它（sr-only 可读）；
                    ② 以后想恢复带文字（宽屏）只改一条 CSS，不用动 JSX。
                  ★ zoom 百分比 **不算文字标签**，必须保留 —— 它是实时数值（即梦也显示），
                    删掉用户就不知道当前缩放到多少了。 */}
            <div className="canvas-toolbar">
              <div className="canvas-toolbar-group">
                <button type="button" className={mouseMode === 'select' ? 'is-active' : ''} onClick={() => setMouseMode('select')} title="选择模式（V）：点选/拖动画布元素" aria-label="选择">
                  <span className="canvas-tool-ico"><MousePointer2 size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">选择</span>
                </button>
                <button type="button" className={mouseMode === 'hand' ? 'is-active' : ''} onClick={() => setMouseMode('hand')} title="抓手模式（H）：拖动平移画布" aria-label="抓手">
                  <span className="canvas-tool-ico"><Hand size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">抓手</span>
                </button>
                <button type="button" className={mouseMode === 'connect' ? 'is-active' : ''} onClick={() => setMouseMode('connect')} title="连续模式（C）：拖端口建立镜头/资产连续关系" aria-label="连续">
                  <span className="canvas-tool-ico"><Link2 size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">连续</span>
                </button>
                <span className="canvas-toolbar-sep" />
                <div ref={addMenuRef} className="canvas-add-menu">
                  <button type="button" onClick={() => setAddMenuOpen((v) => !v)} title="添加节点（文本/图片/音频/视频）" aria-label="添加">
                    <span className="canvas-tool-ico"><Plus size={14} strokeWidth={2.2} aria-hidden="true" /></span><span className="canvas-tool-label">添加</span>
                  </button>
                  {addMenuOpen && (
                    <div className="canvas-add-dropdown">
                      <button type="button" onClick={() => { setAddMenuOpen(false); openMaterialPicker(); }}>
                        <Upload size={13} strokeWidth={1.8} aria-hidden="true" />上传素材
                      </button>
                      <span className="canvas-add-menu-divider" aria-hidden="true" />
                      {/* ★ 顺序与右键菜单保持一致：文本备注在前、文本节点在后
                          （用户明确要求"文本备注节点在前"；两个入口顺序不一致会让人以为功能不同） */}
                      <button type="button" onClick={() => { const s = newNodeSpot(); addNote(s.x, s.y); setAddMenuOpen(false); }}><StickyNote size={13} strokeWidth={1.8} aria-hidden="true" />文本备注</button>
                      <button type="button" onClick={() => { const s = newNodeSpot(); addMediaNode('text', s.x, s.y); setAddMenuOpen(false); }}><Type size={13} strokeWidth={1.8} aria-hidden="true" />文本节点</button>
                      <button type="button" onClick={() => { const s = newNodeSpot(); addMediaNode('image', s.x, s.y); setAddMenuOpen(false); }}><ImageGlyph size={13} strokeWidth={1.8} aria-hidden="true" />图片节点</button>
                      <button type="button" onClick={() => { const s = newNodeSpot(); addMediaNode('audio', s.x, s.y); setAddMenuOpen(false); }}><Music size={13} strokeWidth={1.8} aria-hidden="true" />音频节点</button>
                      <button type="button" onClick={() => { const s = newNodeSpot(); addMediaNode('video', s.x, s.y); setAddMenuOpen(false); }}><Video size={13} strokeWidth={1.8} aria-hidden="true" />视频节点</button>
                    </div>
                  )}
                </div>
                <span className="canvas-toolbar-sep" />
                <button type="button" onClick={autoLayout} title="按段/镜头/资产自动排布" aria-label="自动布局">
                  <span className="canvas-tool-ico"><LayoutGrid size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">自动布局</span>
                </button>
                <button type="button" onClick={fitView} title="适配视图（F / Ctrl+1）" aria-label="适应视图">
                  <span className="canvas-tool-ico"><Maximize2 size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">适应</span>
                </button>
                <button
                  type="button"
                  onClick={focusWorkspace}
                  disabled={selectedCount === 0}
                  title={selectedCount === 0 ? '先选择节点，再聚焦到已选卡片' : '聚焦到已选卡片'}
                  aria-label="聚焦已选节点"
                >
                  <span className="canvas-tool-ico"><ScanSearch size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">聚焦</span>
                </button>
                <button type="button" onClick={zoomOut} title="缩小" aria-label="缩小"><Minus size={14} strokeWidth={2.2} aria-hidden="true" /></button>
                <button type="button" className="canvas-toolbar-zoom" onClick={() => rf.zoomTo(1, {duration: 200})} title="重置为 100%（Ctrl+0）">{zoomPct}%</button>
                <button type="button" onClick={zoomIn} title="放大" aria-label="放大"><Plus size={14} strokeWidth={2.2} aria-hidden="true" /></button>
                <span className="canvas-toolbar-sep" />
                <button type="button" className={snapGrid ? 'is-active' : ''} onClick={() => setSnapGrid((v) => !v)} title="网格吸附：拖动时按 12px 栅格对齐" aria-label="网格吸附">
                  <span className="canvas-tool-ico"><Magnet size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">吸附</span>
                </button>
                <button
                  type="button"
                  className={alignSnapOn ? 'is-active' : ''}
                  onClick={() => setAlignSnapOn((v) => !v)}
                  title="智能对齐参考线：拖动卡片与其它卡片边缘/中线对齐时自动吸附并显示参考线（即梦同款）"
                  aria-label="智能对齐参考线"
                >
                  <span className="canvas-tool-ico"><Ruler size={14} strokeWidth={1.9} aria-hidden="true" /></span><span className="canvas-tool-label">对齐线</span>
                </button>
                {/* 一键隐藏/显示连线：卡片多、连线交织成网时，开关一下就能看清卡片排版。
                    隐藏时连线数据与引用关系完全不动，只是不画，再点一次立刻回来。 */}
                <button
                  type="button"
                  className={edgesHidden ? 'is-active' : ''}
                  onClick={toggleEdgesHidden}
                  title={edgesHidden ? '显示所有连线（L）' : '隐藏所有连线（L）'}
                  aria-label={edgesHidden ? '显示所有连线' : '隐藏所有连线'}
                  aria-pressed={edgesHidden}
                >
                  <span className="canvas-tool-ico">
                    {edgesHidden
                      ? <EyeOff size={14} strokeWidth={1.9} aria-hidden="true" />
                      : <Eye size={14} strokeWidth={1.9} aria-hidden="true" />}
                  </span>
                  <span className="canvas-tool-label">{edgesHidden ? '显示连线' : '隐藏连线'}</span>
                </button>
                {/* 无选中时保留一个整理入口：在没有选区的情况下对齐/分布/排序整张画布 */}
                {selectedCount < 2 && (
                  <button
                    type="button"
                    onClick={() => setArrangeOpen(true)}
                    title="对齐 / 分布 / 排序整理（整张画布）"
                    aria-label="整理"
                  >
                    <span className="canvas-tool-ico"><Rows3 size={14} strokeWidth={1.9} aria-hidden="true" /></span>
                    <span className="canvas-tool-label">整理…</span>
                  </button>
                )}
              </div>
            </div>
          </ReactFlow>

          {batchUploadState && (
            <div className="canvas-upload-queue" role="status" aria-live="polite">
              <div className="canvas-upload-queue-head">
                <span>
                  <b>正在导入 {batchUploadState.done + 1}/{batchUploadState.total}</b>
                  <small title={batchUploadState.currentName}>{batchUploadState.currentName}</small>
                </span>
                <button
                  type="button"
                  onClick={() => batchUploadAbortRef.current?.abort()}
                >
                  取消
                </button>
              </div>
              <span className="canvas-upload-queue-track">
                <i style={{width: `${batchUploadState.percent}%`}} />
              </span>
            </div>
          )}

          {segMenu && (
            <div
              className="canvas-seg-menu"
              style={{left: segMenu.x, top: segMenu.y}}
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                onClick={() => {
                  const seg = segByNodeId(segMenu.nodeId);
                  if (seg) setSegDetail(seg);
                  else setToast(`${segMenu.nodeId} 尚未生成真实分段，暂无段级契约`);
                  setSegMenu(null);
                }}
              >
                查看段级契约
              </button>
              <button
                type="button"
                onClick={() => {
                  const seg = segByNodeId(segMenu.nodeId);
                  const shots = seg && canvas
                    ? segShotIdxsOf(seg, canvas.shots, {overview: currentEp === null})
                    : [];
                  const texts = canvas
                    ? canvas.shots.filter((s) => shots.includes(s.idx)).map((s) => s.video_prompt).filter(Boolean)
                    : [];
                  const label = seg ? `段 ${seg.segment_id}` : segMenu.nodeId;
                  const body = texts.length > 0
                    ? `【${label} 提示词契约】\n` + texts.join('\n\n')
                    : `${label} 暂无视频提示词`;
                  navigator.clipboard?.writeText(body).catch(() => {});
                  setClipboard([body]);
                  setSegMenu(null);
                }}
              >
                复制段提示词
              </button>
              <button
                type="button"
                onClick={() => {
                  const seg = segByNodeId(segMenu.nodeId);
                  if (seg) focusSegmentOf(seg);
                  else setToast(`${segMenu.nodeId} 暂未生成段卡，无法定位`);
                  setSegMenu(null);
                }}
              >
                定位到画布
              </button>
              <button
                type="button"
                onClick={() => {
                  // 移到该集最前：y 设为最小值
                  const node = dragNodesRef.current.find((n) => n.id === segMenu.nodeId);
                  if (node?.parentId) {
                    const siblings = dragNodesRef.current.filter((n) => n.parentId === node.parentId && n.type === 'segment');
                    const minY = Math.min(...siblings.map((s) => s.position.y));
                    positionRef.current[segMenu.nodeId] = {x: node.position.x, y: minY - (SEG_ROW + 32)};
                    setNodes((prev) => prev.map((n) => n.id === segMenu.nodeId ? {...n, position: {...n.position, y: minY - (SEG_ROW + 32)}} : n));
                    setToast('已移到最前');
                  }
                  setSegMenu(null);
                }}
              >
                 <ArrowUpToLine size={13} strokeWidth={1.9} aria-hidden="true" />移到最前
              </button>
              <button
                type="button"
                onClick={() => {
                  const node = dragNodesRef.current.find((n) => n.id === segMenu.nodeId);
                  if (node?.parentId) {
                    const siblings = dragNodesRef.current.filter((n) => n.parentId === node.parentId && n.type === 'segment');
                    const maxY = Math.max(...siblings.map((s) => s.position.y));
                    positionRef.current[segMenu.nodeId] = {x: node.position.x, y: maxY + SEG_ROW + 32};
                    setNodes((prev) => prev.map((n) => n.id === segMenu.nodeId ? {...n, position: {...n.position, y: maxY + SEG_ROW + 32}} : n));
                    setToast('已移到最后');
                  }
                  setSegMenu(null);
                }}
              >
                 <ArrowDownToLine size={13} strokeWidth={1.9} aria-hidden="true" />移到最后
              </button>
              <button type="button" className="is-cancel" onClick={() => setSegMenu(null)}>取消</button>
            </div>
          )}

          {selectedShot && (
            <div className="canvas-inspector-backdrop" onClick={() => setSelectedId(null)}>
              <aside className="canvas-inspector" onClick={(event) => event.stopPropagation()}>
                <header>
                  <strong>镜头 {selectedShot.idx} · 提示词与参数</strong>
                  <button type="button" className="canvas-inspector-close" onClick={() => setSelectedId(null)} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
                </header>
                <label>
                  <span>画面</span>
                  <textarea rows={3} value={selectedShot.draft.visual_desc} onChange={(event) => onDraftChange(selectedShot.idx, 'visual_desc', event.target.value)} />
                </label>
                <label>
                  <span>动作</span>
                  <textarea rows={2} value={selectedShot.draft.motion_desc} onChange={(event) => onDraftChange(selectedShot.idx, 'motion_desc', event.target.value)} />
                </label>
                <label>
                  <span>音频</span>
                  <textarea rows={2} value={selectedShot.draft.audio_desc} onChange={(event) => onDraftChange(selectedShot.idx, 'audio_desc', event.target.value)} />
                </label>
                <label>
                  <span>时长（秒）</span>
                  <input
                    type="number" min={0} step={0.1}
                    value={selectedShot.draft.duration}
                    onChange={(event) => onDraftChange(selectedShot.idx, 'duration', event.target.value)}
                  />
                </label>
                <label>
                  <span>机位 cam_idx</span>
                  <input
                    type="number" min={0} step={1}
                    value={selectedShot.draft.cam_idx}
                    onChange={(event) => onDraftChange(selectedShot.idx, 'cam_idx', event.target.value)}
                  />
                </label>
                <label>
                  <span>转场类型</span>
                  <input
                    value={selectedShot.draft.transition_type}
                    onChange={(event) => onDraftChange(selectedShot.idx, 'transition_type', event.target.value)}
                    placeholder="硬切 / 叠化 / 推近…"
                  />
                </label>
                <label>
                  <span>首帧描述</span>
                  <textarea rows={2} value={selectedShot.draft.ff_desc} onChange={(event) => onDraftChange(selectedShot.idx, 'ff_desc', event.target.value)} />
                </label>
                <label>
                  <span>尾帧描述</span>
                  <textarea rows={2} value={selectedShot.draft.lf_desc} onChange={(event) => onDraftChange(selectedShot.idx, 'lf_desc', event.target.value)} />
                </label>
                <label>
                  <span>动作分解</span>
                  <textarea rows={3} value={selectedShot.draft.action_breakdown} onChange={(event) => onDraftChange(selectedShot.idx, 'action_breakdown', event.target.value)} />
                </label>
                <label className="canvas-inspector-final-field">
                  <span title="分镜生成的连贯动作叙事：以「从…开始。首先…接着…最终…」写全本镜表演，渲染时作为主叙事注入（存在时跳过「动作」的重复注入）">
                    连贯表演提示词 · video_prompt（喂给模型）
                  </span>
                  <textarea
                    rows={5}
                    className="canvas-inspector-prompt"
                    placeholder="留空则由系统用「画面 + 动作 + 音频」编译；填写后作为本镜主叙事，按此顺序演出。"
                    value={selectedShot.draft.video_prompt}
                    onChange={(event) => onDraftChange(selectedShot.idx, 'video_prompt', event.target.value)}
                  />
                </label>
                <button type="button" className={`canvas-save-btn is-dirty ${selectedShot.dirty ? '' : 'is-synced'}`} onClick={() => void saveShot(selectedShot.idx)} disabled={!selectedShot.dirty || savingShot !== null} title="输入停顿 0.9 秒后自动写回磁盘；也可点此立即保存">
                  {savingShot === selectedShot.idx ? '保存中…' : selectedShot.dirty ? '改动将自动保存（点此立即存）' : <><Check size={12} strokeWidth={2.5} aria-hidden="true" />已自动保存</>}
                </button>
              </aside>
            </div>
          )}

          {pickShot !== null && (
            <div className="canvas-picker-backdrop" onClick={() => setPickShot(null)}>
              <div className="canvas-picker" onClick={(event) => event.stopPropagation()}>
                <header>
                  <strong>为镜头 {pickShot} 绑定参考</strong>
                  <button type="button" className="canvas-inspector-close" onClick={() => setPickShot(null)} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
                </header>
                <div className="canvas-picker-grid">
                  {pickerAssets.map((asset) => (
                    <button key={asset.key} type="button" className="canvas-picker-item" onClick={() => bindReference(asset.key)}>
                      {asset.image_url ? <img src={asset.image_url} alt={asset.name} loading="lazy" /> : <span className="canvas-picker-audio"><Music size={15} strokeWidth={1.9} aria-hidden="true" /></span>}
                      <span className="canvas-picker-kind">{asset.kind}</span>
                      <span className="canvas-picker-name">{asset.name}</span>
                    </button>
                  ))}
                  {pickerAssets.length === 0 && <p className="canvas-picker-empty">暂无资产，先生成资产后再绑定参考</p>}
                </div>
                <em>点击资产即生成「资产 → 段卡」绑定连线（镜头嵌在段卡内）；也可直接从节点把手拖线</em>
              </div>
            </div>
          )}

          {contextMenu && (() => {
            const rect = stageRef.current?.getBoundingClientRect();
            const relX = contextMenu.x - (rect?.left ?? 0);
            const relY = contextMenu.y - (rect?.top ?? 0);
            const MENU_W = 220;
            // 批量菜单实测：标题 + 编组(1~2) + 整理 + 批量调参 + 批量下载 + 删除/复制/适应/置顶/置底 + 取消
            // ★ 500 是加入「批量调参 / 批量下载」两行后的值（各 ~34px），
            //   否则底部「取消」会被切掉（第 12 轮踩过一次）。
            // ★ 本轮菜单项全部加了 13px 图标：不改行高（padding 7px + 12px 字 → 图标不撑行），
            //   但为了防"字体放大 / 系统缩放"下估算偏小，统一放到 520，样式里的
            //   max-height + overflow-y 仍是第二层保险。
            const MENU_H = contextMenu.multi
              ? 520
              : !contextMenu.nodeId
                ? 320
                : contextMenu.nodeId.startsWith(MEDIA_PREFIX) ? 360 : 260;
            return (
            <div
              className="canvas-context-menu"
              style={{left: Math.max(4, Math.min(relX, (rect?.width ?? window.innerWidth) - MENU_W)), top: Math.max(4, Math.min(relY, (rect?.height ?? window.innerHeight) - MENU_H))}}
              onClick={(event) => event.stopPropagation()}
            >
              {/* ★ 批量菜单必须与「添加节点」菜单互斥。
                  条件只写 `!nodeId` 时，在**空白处**右键且存在多选的情况下两块会同时渲染：
                  菜单被撑到 665px 高、直接溢出视口底部，用户看到的就是
                  「上面一堆添加节点、下面才是编组/删除」，等于批量菜单被埋没（第 12 轮实测）。 */}
              {!contextMenu.multi && !contextMenu.nodeId && (
                <>
                  <button
                    type="button"
                    onClick={() => {
                      const pos = rf.screenToFlowPosition({x: contextMenu.x, y: contextMenu.y});
                      setContextMenu(null);
                      openMaterialPicker(pos);
                    }}
                  >
                    <Upload size={13} strokeWidth={1.8} aria-hidden="true" />上传素材
                  </button>
                  <span className="canvas-context-sep" />
                  {/* 顺序按用户的习惯调换：文本备注在前、文本节点在后（之前两个反了，
                      而且图标也对调 —— 备注是便签纸、文本节点是 T 字形）。 */}
                  <button type="button" onClick={() => { const pos = rf.screenToFlowPosition({x: contextMenu.x, y: contextMenu.y}); addNote(pos.x - 110, pos.y - 60); setContextMenu(null); }}><StickyNote size={13} strokeWidth={1.8} aria-hidden="true" />添加文本备注</button>
                  <button type="button" onClick={() => { const pos = rf.screenToFlowPosition({x: contextMenu.x, y: contextMenu.y}); addMediaNode('text', pos.x - 100, pos.y - 60); setContextMenu(null); }}><Type size={13} strokeWidth={1.8} aria-hidden="true" />添加文本节点</button>
                  <button type="button" onClick={() => { const pos = rf.screenToFlowPosition({x: contextMenu.x, y: contextMenu.y}); addMediaNode('image', pos.x - 90, pos.y - 90); setContextMenu(null); }}><ImageGlyph size={13} strokeWidth={1.8} aria-hidden="true" />添加图片节点</button>
                  <button type="button" onClick={() => { const pos = rf.screenToFlowPosition({x: contextMenu.x, y: contextMenu.y}); addMediaNode('audio', pos.x - 90, pos.y - 65); setContextMenu(null); }}><Music size={13} strokeWidth={1.8} aria-hidden="true" />添加音频节点</button>
                  <button type="button" onClick={() => { const pos = rf.screenToFlowPosition({x: contextMenu.x, y: contextMenu.y}); addMediaNode('video', pos.x - 90, pos.y - 140); setContextMenu(null); }}><Video size={13} strokeWidth={1.8} aria-hidden="true" />添加视频节点</button>
                  <span className="canvas-context-sep" />
                  <button type="button" onClick={() => { setNodes((prev) => prev.map((n) => ({...n, selected: true}))); setContextMenu(null); }}>全选（Ctrl+A）</button>
                  {nodeClipboard && (nodeClipboard.media.length > 0 || nodeClipboard.notes.length > 0) && (
                    <button type="button" onClick={() => { const n = pasteNodeClipboard(); if (n > 0) setToast(`已粘贴 ${n} 个节点`); setContextMenu(null); }}>粘贴（Ctrl+V）</button>
                  )}
                  <button type="button" onClick={() => { rf.zoomTo(1, {duration: 200}); setContextMenu(null); }}>重置为 100%（Ctrl+0）</button>
                  <button type="button" onClick={() => { fitView(); setContextMenu(null); }}>适应视图</button>
                  <button type="button" onClick={() => { setHelpOpen(true); setContextMenu(null); }}>帮助</button>
                </>
              )}
              {contextMenu.multi && contextMenu.selIds && contextMenu.selIds.length > 0 && (
                <>
                  <div className="canvas-context-title">已选 {contextMenu.selIds.length} 个节点</div>
                  <button type="button" onClick={() => { groupSelected(contextMenu.selIds!); setContextMenu(null); }}><Group size={13} strokeWidth={1.9} aria-hidden="true" />编组（{contextMenu.selIds.length} 项）</button>
                  {(() => {
                    const inGroup = contextMenu.selIds!.some((id) => groupMap.has(id));
                    return inGroup ? (
                      <button type="button" onClick={() => { ungroupSelected(contextMenu.selIds!); setContextMenu(null); }}><Ungroup size={13} strokeWidth={1.9} aria-hidden="true" />解除编组</button>
                    ) : null;
                  })()}
                  <span className="canvas-context-sep" />
                  <button type="button" onClick={() => { setContextMenu(null); setArrangeOpen(true); }}><LayoutGrid size={13} strokeWidth={1.9} aria-hidden="true" />整理…（对齐 / 分布 / 排序）</button>
                  {(() => {
                    const picked = mediaNodesRef.current.filter((mn) => contextMenu.selIds!.some((id) => id === `${MEDIA_PREFIX}${mn.id}`));
                    const adjustable = picked.filter((mn) => mn.kind === 'image' || mn.kind === 'video' || mn.kind === 'audio' || mn.kind === 'text');
                    const ready = picked.filter((mn) => mn.kind !== 'text' && !!mn.mediaUrl);
                    return (
                      <>
                        <button
                          type="button"
                          disabled={adjustable.length === 0}
                          title={adjustable.length === 0 ? '选中里没有可批量调参的节点' : `批量设置 ${adjustable.length} 个节点的模型 / 比例 / 清晰度 / 音频 / 文本参数`}
                          onClick={() => { setContextMenu(null); setBatchParamsTargets(adjustable); }}
                        ><SlidersHorizontal size={13} strokeWidth={1.9} aria-hidden="true" />批量调参…（{adjustable.length} 个）</button>
                        <button
                          type="button"
                          disabled={ready.length === 0}
                          title={ready.length === 0 ? '选中里没有已生成的素材' : `下载 ${ready.length} 个已生成的素材`}
                          onClick={() => { setContextMenu(null); void downloadMediaBatch(picked); }}
                        ><Download size={13} strokeWidth={1.9} aria-hidden="true" />批量下载素材（{ready.length} 个）</button>
                      </>
                    );
                  })()}
                  <button type="button" onClick={() => { setDeleteTargets(contextMenu.selIds!.map((id) => ({id, type: id.startsWith(NOTE_PREFIX) ? 'note' : 'media'}))); setContextMenu(null); }}><Trash2 size={13} strokeWidth={1.9} aria-hidden="true" />删除…（{contextMenu.selIds.length} 项）</button>
                  <button type="button" onClick={() => { const n = cutSelectedNodes(); if (n > 0) setToast(`已剪切 ${n} 个节点，Ctrl+V 粘贴`); setContextMenu(null); }}><Scissors size={13} strokeWidth={1.9} aria-hidden="true" />剪切（Ctrl+X）</button>
                  <button type="button" onClick={() => { const n = duplicateSelectedNodes(); if (n > 0) setToast(`已复制 ${n} 个节点`); setContextMenu(null); }}><Copy size={13} strokeWidth={1.9} aria-hidden="true" />复制（Ctrl+D）</button>
                  <button type="button" onClick={() => { focusSelection(true); setContextMenu(null); }}><ScanSearch size={13} strokeWidth={1.9} aria-hidden="true" />聚焦所选（Shift+F）</button>
                  <button type="button" onClick={() => { setNodeZOrder(contextMenu.selIds!, 'front'); setContextMenu(null); }}><ArrowUpToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于顶层（{contextMenu.selIds!.length} 项）</button>
                  <button type="button" onClick={() => { setNodeZOrder(contextMenu.selIds!, 'back'); setContextMenu(null); }}><ArrowDownToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于底层（{contextMenu.selIds!.length} 项）</button>
                  <span className="canvas-context-sep" />
                  <button type="button" onClick={() => { setNodes((prev) => prev.map((n) => (n.selected ? {...n, selected: false} : n))); setContextMenu(null); }}><X size={13} strokeWidth={2} aria-hidden="true" />取消选择（Esc）</button>
                </>
              )}
              {!contextMenu.multi && contextMenu.nodeId && contextNode?.type === 'shot' && (
                <>
                  <button type="button" onClick={() => { setToast(`镜头 ${contextMenu.nodeId!.slice(5)} 的渲染由工作区 Agent 驱动：回到对话发送「1 继续渲染」`); setContextMenu(null); }}><Play size={13} strokeWidth={1.9} aria-hidden="true" />运行此镜头</button>
                  <button type="button" onClick={() => { setSelectedId(contextMenu.nodeId); setContextMenu(null); }}><Pencil size={13} strokeWidth={1.9} aria-hidden="true" />检查 / 编辑参数</button>
                  <button type="button" onClick={() => { setUserEdges((prev) => { const id = contextMenu.nodeId!; const next = prev.filter((edge) => edge.source !== id && edge.target !== id); pushHistory(prev, notesRef.current); return next; }); setContextMenu(null); }}><Scissors size={13} strokeWidth={1.9} aria-hidden="true" />断开全部连线</button>
                </>
              )}
              {!contextMenu.multi && contextMenu.nodeId && contextNode?.type === 'segment' && (
                <>
                  <button type="button" onClick={() => {
                    const seg = segByNodeId(contextMenu.nodeId!);
                    if (seg) setSegDetail(seg);
                    setContextMenu(null); setSegMenu(null);
                  }}><Ruler size={13} strokeWidth={1.9} aria-hidden="true" />查看段契约</button>
                  <button type="button" onClick={() => {
                    const seg = segByNodeId(contextMenu.nodeId!);
                    const segShots = seg && canvas
                      ? canvas.shots.filter((s) => segShotIdxsOf(seg, canvas.shots, {overview: currentEp === null}).includes(s.idx))
                      : [];
                    const label = seg ? `段 ${seg.segment_id}` : contextMenu.nodeId!;
                    const text = segShots.map((s) => `【镜头 ${s.idx}】${s.video_prompt ?? s.visual_desc ?? ''}`).filter((t) => t.length > 4).join('\n\n');
                    if (text) {
                      navigator.clipboard?.writeText(text).then(() => setToast(`已复制 ${label} 的 ${segShots.length} 个镜头提示词`)).catch(() => setToast('复制失败，请手动选择复制'));
                    } else {
                      setToast('该段暂无提示词');
                    }
                    setContextMenu(null); setSegMenu(null);
                  }}><Copy size={13} strokeWidth={1.9} aria-hidden="true" />复制段提示词</button>
                </>
              )}
              {!contextMenu.multi && contextMenu.nodeId && contextNode?.type === 'note' && (
                <>
                  {contextMenuNote && <button type="button" onClick={() => { duplicateNote(contextMenuNote); setContextMenu(null); }}><Copy size={13} strokeWidth={1.9} aria-hidden="true" />复制</button>}
                  <button type="button" onClick={() => { setNodeZOrder([contextMenu!.nodeId!], 'front'); setContextMenu(null); }}><ArrowUpToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于顶层</button>
                  <button type="button" onClick={() => { setNodeZOrder([contextMenu!.nodeId!], 'back'); setContextMenu(null); }}><ArrowDownToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于底层</button>
                  <button type="button" onClick={() => { const nodeId = contextMenu!.nodeId!; setDeleteTargets([{id: nodeId, type: 'note'}]); setContextMenu(null); }}><Trash2 size={13} strokeWidth={1.9} aria-hidden="true" />删除…</button>
                </>
              )}
              {!contextMenu.multi && contextMenu.nodeId && contextMenu.nodeId.startsWith(MEDIA_PREFIX)
                && mediaNodes.find((n) => n.id === contextMenu.nodeId!.slice(MEDIA_PREFIX.length))?.kind === 'text' && (() => {
                const mid = contextMenu.nodeId!.slice(MEDIA_PREFIX.length);
                const mn = mediaNodes.find((n) => n.id === mid);
                return (
                  <>
                    {mn && <button type="button" onClick={() => { openMediaModal(mn); setContextMenu(null); }}><Pencil size={13} strokeWidth={1.9} aria-hidden="true" />编辑文本</button>}
                    <button type="button" onClick={() => { const n = duplicateSelectedNodes(); if (n > 0) setToast(`已复制 ${n} 个节点`); setContextMenu(null); }}><Copy size={13} strokeWidth={1.9} aria-hidden="true" />复制（Ctrl+D）</button>
                    <button type="button" onClick={() => { setNodeZOrder([contextMenu!.nodeId!], 'front'); setContextMenu(null); }}><ArrowUpToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于顶层</button>
                    <button type="button" onClick={() => { setNodeZOrder([contextMenu!.nodeId!], 'back'); setContextMenu(null); }}><ArrowDownToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于底层</button>
                    <button type="button" onClick={() => { setDeleteTargets([{id: contextMenu!.nodeId!, type: 'media'}]); setContextMenu(null); }}><Trash2 size={13} strokeWidth={1.9} aria-hidden="true" />删除…</button>
                  </>
                );
              })()}
              {!contextMenu.multi && contextMenu.nodeId && contextNode?.type === 'media'
                && mediaNodes.find((n) => n.id === contextMenu.nodeId!.slice(MEDIA_PREFIX.length))?.kind !== 'text' && (() => {
                const mid = contextMenu.nodeId!.slice('media-'.length);
                const mn = mediaNodes.find((n) => n.id === mid);
                const versions = mn ? mediaVersionList(mn) : [];
                return (
                  <>
                    {mn && (
                      <button type="button" onClick={() => { openNodeLocalUpload(mn); setContextMenu(null); }}>
                        <Upload size={13} strokeWidth={1.9} aria-hidden="true" />
                        {mn.mediaUrl ? '从本地添加版本' : '导入本地文件'}
                      </button>
                    )}
                    {mn && <button type="button" onClick={() => { onGenerateMediaNode(mn); setContextMenu(null); }}><Sparkles size={13} strokeWidth={1.9} aria-hidden="true" />重新生成</button>}
                    {mn?.mediaUrl && (
                      <button type="button" onClick={() => { void downloadMediaBatch([mn]); setContextMenu(null); }}>
                        <Download size={13} strokeWidth={1.9} aria-hidden="true" />下载当前版本
                      </button>
                    )}
                    {mn && versions.length > 1 && (
                      <button type="button" onClick={() => { void downloadMediaBatch([mn], {allVersions: true}); setContextMenu(null); }}>
                        <Download size={13} strokeWidth={1.9} aria-hidden="true" />下载全部版本（{versions.length}）
                      </button>
                    )}
                    <button type="button" onClick={() => { const n = duplicateSelectedNodes(); if (n > 0) setToast(`已复制 ${n} 个节点`); setContextMenu(null); }}><Copy size={13} strokeWidth={1.9} aria-hidden="true" />复制（Ctrl+D）</button>
                    <button type="button" onClick={() => { setNodeZOrder([contextMenu!.nodeId!], 'front'); setContextMenu(null); }}><ArrowUpToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于顶层</button>
                    <button type="button" onClick={() => { setNodeZOrder([contextMenu!.nodeId!], 'back'); setContextMenu(null); }}><ArrowDownToLine size={13} strokeWidth={1.9} aria-hidden="true" />置于底层</button>
                    <button type="button" onClick={() => { requestDeleteMediaNode(mid); setContextMenu(null); }}><Trash2 size={13} strokeWidth={1.9} aria-hidden="true" />删除…</button>
                  </>
                );
              })()}
              {contextMenu.nodeId && (contextNode?.type === 'char' || contextNode?.type === 'scene' || contextNode?.type === 'prop' || contextNode?.type === 'voice') && (
                <button type="button" onClick={() => { setSelectedId(contextMenu.nodeId); setContextMenu(null); }}><Pencil size={13} strokeWidth={1.9} aria-hidden="true" />检查</button>
              )}
              <button type="button" className="is-cancel" onClick={() => setContextMenu(null)}><X size={13} strokeWidth={2} aria-hidden="true" />取消</button>
            </div>
            );
          })()}

          {arrangeOpen && (
            <ArrangeDialog
              selectedCount={selectedCount}
              totalCount={mediaNodes.length + notes.length}
              onClose={() => setArrangeOpen(false)}
              onAlign={(mode, scopeAll) => {
                const n = applyGeometry(mode, scopeAll);
                if (n > 0) setToast(`已整理 ${n} 个节点`);
                else setToast(scopeAll ? '至少需要 2 个节点才能对齐/分布' : '需要至少 2 个选中节点才能对齐/分布');
                setArrangeOpen(false);
              }}
              onSort={(key, cols, scopeAll) => {
                const n = sortIntoGrid(key, cols, scopeAll);
                // ★ 静默失败也是体验问题：以前 n===0 时什么都不提示，弹窗关掉了、
                //   画布没变化，用户会以为"点了没反应 / 这功能坏了"。
                if (n > 0) setToast(`已重排 ${n} 个节点`);
                else setToast(scopeAll ? '至少需要 2 个节点才能重排' : '需要至少 2 个选中节点才能重排');
                setArrangeOpen(false);
              }}
            />
          )}
          {batchParamsTargets && (
            <BatchParamsDialog
              targets={batchParamsTargets}
              catalog={modelCatalog}
              onClose={() => setBatchParamsTargets(null)}
              onApply={(patch, scope) => {
                const res = applyBatchParams(patch, scope, batchParamsTargets);
                setBatchParamsTargets(null);
                if (res.changed === 0 && res.skipped.length === 0) {
                  setToast('参数没有变化');
                } else if (res.skipped.length > 0) {
                  // ★ 必须明确告诉用户"哪些卡因为模型不支持被跳过了"。
                  //   静默失败（选了 1080P 却有几张卡没变）是最容易被当成 bug 的体验。
                  const names = res.skipped.slice(0, 3).map((s) => s.title || '未命名').join('、');
                  const more = res.skipped.length > 3 ? ` 等 ${res.skipped.length} 个` : '';
                  setToast(`已更新 ${res.changed} 个；${names}${more} 当前模型不支持所选档位，已保留原值`);
                } else {
                  setToast(`已更新 ${res.changed} 个节点的参数`);
                }
              }}
            />
          )}
          {batchGenerateTargets && (
            <BatchGenerateDialog
              targets={batchGenerateTargets}
              pool={mediaNodes}
              edges={allEdges}
              catalog={modelCatalog}
              tasks={generationTasks}
              busyNodeIds={generationBusyIds}
              onClose={() => setBatchGenerateTargets(null)}
              onGenerate={onGenerateMediaNode}
              onSubmitted={(started, selected) => {
                if (started === 0) {
                  // onGenerateMediaNode has already surfaced the exact blocking
                  // reason. Do not overwrite it with a generic toast.
                  return;
                } else if (started < selected) {
                  setToast(`已提交 ${started} / ${selected} 项，其余项目在提交前状态已变化`);
                } else {
                  setToast(`已提交 ${started} 项批量生成，任务中心会统一更新`);
                }
              }}
            />
          )}
          {deleteTargets && (
            <DeleteDialog
              targets={deleteTargets}
              mediaNodes={mediaNodes}
              notes={notes}
              onCancel={() => setDeleteTargets(null)}
              onConfirm={(ids) => {
                const n = deleteNodesByIds(ids);
                setDeleteTargets(null);
                if (n > 0) setToast(`已删除 ${n} 个节点，Ctrl+Z 可撤销`);
                else setToast('没有删除任何节点');
              }}
            />
          )}
          {versionsOpen && (
            <ProjectVersionsDialog
              projectId={session}
              busy={saveState === 'saving' || dirtyShots.size > 0}
              onClose={() => setVersionsOpen(false)}
              onMessage={setToast}
            />
          )}
          {budgetOpen && (
            <GenerationBudgetDialog
              onClose={() => setBudgetOpen(false)}
              onMessage={setToast}
            />
          )}
          {taskCenterOpen && (
            <div className="canvas-task-center-backdrop" onClick={() => setTaskCenterOpen(false)}>
              <aside className="canvas-task-center" role="dialog" aria-label="生成任务中心" onClick={(event) => event.stopPropagation()}>
                <header>
                  <span className="canvas-task-center-title">
                    <ListChecks size={15} strokeWidth={1.9} aria-hidden="true" />
                    <strong>生成任务</strong>
                    <em>{activeGenerationTaskCount} 运行中</em>
                  </span>
                  <button type="button" onClick={() => setTaskCenterOpen(false)} aria-label="关闭任务中心"><X size={14} strokeWidth={2} aria-hidden="true" /></button>
                </header>
                <div className="canvas-task-center-list">
                  {generationTasks.length === 0 && (
                    <div className="canvas-task-center-empty">还没有生成任务</div>
                  )}
                  {generationTasks.map((task) => {
                    const node = mediaNodes.find((item) => item.id === task.node_id);
                    const taskError = ['queued', 'submitting', 'running'].includes(task.status)
                      ? ''
                      : friendlyTaskError(task.error);
                    const {
                      localBlocked,
                      outcomeUncertain,
                      outcomePending,
                      statusLabel,
                      canRegenerate,
                    } = taskOutcomePresentation(task);
                    return (
                      <article key={task.id} className={`canvas-task-item is-${task.status}`}>
                        <div className="canvas-task-item-head">
                          <strong title={task.title}>{task.title || node?.title || '生成任务'}</strong>
                          <span>{statusLabel || task.status}</span>
                        </div>
                        <div className="canvas-task-item-meta">
                          <span>{task.kind === 'video' ? '视频' : task.kind === 'image' ? '图片' : task.kind === 'audio' ? '音频' : '文本'}</span>
                          <span>{task.provider || '未提交'}</span>
                          <span>{task.model || '-'}</span>
                          <span>第 {Number(task.attempt || 1)} 次</span>
                          {Number(task.estimated_cost || 0) > 0 && (
                            <span>
                              预计 {task.cost_currency === 'USD' ? '$' : '¥'}
                              {Number(task.estimated_cost).toFixed(2)}
                            </span>
                          )}
                          {task.queue_position && ['queued', 'submitting', 'running'].includes(task.status) && (
                            <span>队列第 {task.queue_position} 位</span>
                          )}
                          <span>{generationDurationLabel(task)}</span>
                        </div>
                        <div className="canvas-task-item-phase">
                          <i style={{width: `${task.status === 'succeeded' ? 100 : Math.max(2, Math.min(100, Number(task.progress || 6)))}%`}} />
                          <span>
                            {task.status === 'succeeded' && <Check size={11} strokeWidth={2.5} aria-hidden="true" />}
                            {task.status === 'succeeded'
                              ? '已完成'
                              : generationPhaseLabel(task.phase || taskError || statusLabel)}
                          </span>
                        </div>
                        {taskError && <p title={taskError}>{taskError}</p>}
                        <div className="canvas-task-item-actions">
                          {node && (
                            <button type="button" onClick={() => {
                              const kind = task.kind === 'image' ? '图片' : task.kind === 'audio' ? '音频' : task.kind === 'text' ? '文本' : '视频';
                              locateHit({kind, label: node.title, nodeId: `media-${node.id}`});
                              setTaskCenterOpen(false);
                            }}>定位节点</button>
                          )}
                          {task.status === 'queued' && (
                            <button type="button" onClick={() => {
                              void cancelGenerationTask(task.id)
                                .then(() => {
                                  const finishedAt = new Date().toISOString();
                                  setGenerationTasks((prev) => prev.map((item) => item.id === task.id
                                    ? {
                                        ...item,
                                        status: 'interrupted' as GenerationTask['status'],
                                        phase: '已取消',
                                        error: '用户取消了排队任务',
                                        finished_at: finishedAt,
                                        updated_at: finishedAt,
                                      }
                                    : item));
                                  if (node) {
                                    updateMediaNode(node.id, {
                                      status: 'failed',
                                      lastError: '已取消生成排队',
                                      providerTaskStatus: 'interrupted',
                                      providerTaskPhase: '已取消',
                                      generationFinishedAt: finishedAt,
                                    });
                                  }
                                  setToast('已取消排队任务');
                                })
                                .catch((error) => setToast(error instanceof Error ? error.message : '取消任务失败'));
                            }}>取消排队</button>
                          )}
                          {node && canRegenerate && (
                            <button type="button" className="is-retry" onClick={() => {
                              setTaskCenterOpen(false);
                              onGenerateMediaNode(node);
                            }}>重新生成</button>
                          )}
                        </div>
                      </article>
                    );
                  })}
                </div>
              </aside>
            </div>
          )}
          {textReader && (
            <div className="canvas-text-reader-backdrop" onClick={() => setTextReader(null)}>
              <article className="canvas-text-reader" role="dialog" aria-label="文本全文" onClick={(event) => event.stopPropagation()}>
                <header>
                  <strong>{textReader.title || '文本全文'}</strong>
                  <span>{(textReader.prompt || '').length} 字</span>
                  <button
                    type="button"
                    title="复制全文"
                    onClick={() => {
                      navigator.clipboard?.writeText(textReader.prompt || '')
                        .then(() => setToast('已复制全文'))
                        .catch(() => setToast('复制失败'));
                    }}
                  >
                    <Copy size={13} strokeWidth={1.9} aria-hidden="true" />复制
                  </button>
                  <button type="button" onClick={() => setTextReader(null)} aria-label="关闭全文"><X size={14} strokeWidth={2} aria-hidden="true" /></button>
                </header>
                <pre>{textReader.prompt || '暂无内容'}</pre>
              </article>
            </div>
          )}
          {helpOpen && (
            <div className="canvas-help-backdrop" onClick={() => setHelpOpen(false)}>
              <div className="canvas-help" onClick={(event) => event.stopPropagation()}>
                <header>
                  <strong>画布操作帮助</strong>
                  <button type="button" className="canvas-inspector-close" onClick={() => setHelpOpen(false)} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
                </header>
                <div className="canvas-help-grid">
                  <section>
                    <h4>创作</h4>
                    {/* ★ 2026-10-25：删掉「新建备注 · 双击空白」这条说明。
                        该手势已按用户要求移除（双击空白容易误建、越加越多），
                        帮助面板不能再承诺一个不存在的快捷键。 */}
                    <div className="canvas-help-row"><span>新建备注</span><span>工具栏「添加」/ 右键</span></div>
                    <div className="canvas-help-row"><span>复制节点</span><kbd>Ctrl</kbd><kbd>C</kbd></div>
                    <div className="canvas-help-row"><span>粘贴节点</span><kbd>Ctrl</kbd><kbd>V</kbd></div>
                    <div className="canvas-help-row"><span>就地复制选中</span><kbd>Ctrl</kbd><kbd>D</kbd></div>
                    <div className="canvas-help-row"><span>删除节点 / 连线</span><kbd>Del</kbd></div>
                    <div className="canvas-help-row"><span>右键菜单</span><kbd>右键</kbd></div>
                  </section>
                  <section>
                    <h4>视图</h4>
                    <div className="canvas-help-row"><span>选择模式</span><kbd>V</kbd></div>
                    <div className="canvas-help-row"><span>抓手模式</span><kbd>H</kbd></div>
                    <div className="canvas-help-row"><span>连续模式</span><kbd>C</kbd></div>
                    <div className="canvas-help-row"><span>删除选中</span><kbd>Del</kbd><kbd>Backspace</kbd></div>
                    <div className="canvas-help-row"><span>临时平移</span><kbd>Space</kbd></div>
                    <div className="canvas-help-row"><span>隐藏 / 显示连线</span><kbd>L</kbd></div>
                    <div className="canvas-help-row"><span>适应画布</span><kbd>F</kbd></div>
                    <div className="canvas-help-row"><span>聚焦选中</span><kbd>Shift+F</kbd><kbd>Ctrl+F</kbd></div>
                    <div className="canvas-help-row"><span>打开快捷键</span><kbd>?</kbd></div>
                    <div className="canvas-help-row"><span>重置 100%</span><kbd>Ctrl</kbd><kbd>0</kbd></div>
                  </section>
                  <section>
                    <h4>画布操作</h4>
                    <div className="canvas-help-row"><span>全选节点</span><kbd>Ctrl</kbd><kbd>A</kbd></div>
                    <div className="canvas-help-row"><span>取消选择</span><kbd>Esc</kbd></div>
                    <div className="canvas-help-row"><span>微调位置</span><kbd>↑↓←→</kbd><kbd>Shift</kbd><kbd>×10</kbd></div>
                    <div className="canvas-help-row"><span>对齐 / 分布 / 排序</span><span>工具栏「整理…」</span></div>
                    <div className="canvas-help-row"><span>批量整理 / 删除</span><span>多选后右键</span></div>
                    <div className="canvas-help-row"><span>删除确认</span><span>弹窗勾选</span></div>
                    <div className="canvas-help-row"><span>网格吸附</span><span>工具栏「吸附」</span></div>
                    <div className="canvas-help-row"><span>缩放画布</span><kbd>＋</kbd><kbd>－</kbd></div>
                  </section>
                  <section>
                    <h4>其他</h4>
                    <div className="canvas-help-row"><span>撤销</span><kbd>Ctrl+Z</kbd></div>
                    <div className="canvas-help-row"><span>重做</span><kbd>Ctrl+Shift+Z</kbd></div>
                    <div className="canvas-help-row"><span>剪切节点</span><kbd>Ctrl+X</kbd></div>
                    <div className="canvas-help-row"><span>段卡右键</span><span>段契约 / 复制提示词</span></div>
                    <div className="canvas-help-row"><span>布局自动保存</span><span>刷新后还原</span></div>
                  </section>
                </div>
              </div>
            </div>
          )}

        </div>
      )}

      {assetDetail && (
        <div className="canvas-inspector-backdrop" onClick={() => setAssetDetail(null)}>
          <div className="canvas-asset-detail" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <header>
              <span className={`canvas-kind ${assetDetail.kind === '角色' ? 'is-char' : assetDetail.kind === '场景' ? 'is-scene' : assetDetail.kind === '道具' ? 'is-prop' : 'is-voice'}`}>{assetDetail.kind}</span>
              <strong>{assetDetail.item.name}</strong>
              <button type="button" className="canvas-inspector-close" onClick={() => setAssetDetail(null)} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
            </header>
            {assetDetail.item.image_url && (
              <div className="canvas-asset-detail-media">
                <img src={assetDetail.item.image_url} alt={assetDetail.item.name} onDoubleClick={() => setZoomTarget(assetDetail.item.image_url as string)} />
                <span>双击图片放大</span>
              </div>
            )}
            {assetDetail.item.audio_url && (
              <audio className="canvas-asset-detail-audio" src={assetDetail.item.audio_url} controls aria-label={`${assetDetail.item.name} 音频`} />
            )}
            {assetDetail.item.description ? (
              <div className="canvas-asset-detail-desc">
                <h4>设定描述词</h4>
                <p>{assetDetail.item.description}</p>
              </div>
            ) : (
              <p className="canvas-asset-detail-empty">该资产暂无设定描述词</p>
            )}
          </div>
        </div>
      )}

      {segDetail && (
        <div className="canvas-inspector-backdrop" onClick={() => setSegDetail(null)}>
          <div className="canvas-seg-detail" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <header>
              <strong>段 {segDetail.segment_id} · 段级契约</strong>
              <button type="button" className="canvas-inspector-close" onClick={() => setSegDetail(null)} aria-label="关闭"><X size={15} strokeWidth={2} aria-hidden="true" /></button>
            </header>
            <div className="canvas-seg-detail-meta">
              <p><b>标题</b>：{segDetail.title || '—'}</p>
              <p><b>时长</b>：{segDetail.duration !== null ? `${segDetail.duration}s` : '—'}　<b>镜头数</b>：{(segDetail.shot_idxs ?? []).length}</p>
              {segDetail.scene_id && <p><b>场景</b>：{segDetail.scene_id}</p>}
              {segDetail.asset_binding && <p><b>资产绑定</b>：{segDetail.asset_binding}</p>}
              {segDetail.end_state && <p className="is-endstate"><b>末帧交接</b>：{segDetail.end_state}</p>}
            </div>
            <div className="canvas-seg-detail-shots">
              {((segDetail.shot_idxs ?? []).length > 0 ? segDetail.shot_idxs : (canvas?.shots ?? []).filter((s) => String(s.segment_id) === String(segDetail.segment_id)).map((s) => s.idx)).map((idx) => {
                const shot = canvas?.shots.find((s) => s.idx === idx);
                if (!shot) return null;
                return (
                  <div key={idx} className="canvas-seg-detail-shot">
                    <strong>镜头 {idx} {shot.duration !== null ? `· ${shot.duration}s` : ''}</strong>
                    {shot.video_prompt ? <p>{shot.video_prompt}</p> : (
                      <p className="is-empty">暂无 video_prompt（可用画面/动作/音频描述）</p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {trayPreview && (
        <div
          className="jim-mention-preview"
          style={{
            left: trayPreview.left,
            ...(trayPreview.top !== undefined ? {top: trayPreview.top} : {bottom: trayPreview.bottom}),
          }}
          role="tooltip"
        >
          <img src={trayPreview.url} alt={trayPreview.label} />
          <span>{trayPreview.label}</span>
        </div>
      )}

      {zoomTarget && (
        <div className="canvas-lightbox" role="dialog" onClick={() => setZoomTarget(null)}>
          {lightboxIndex >= 0 && lightboxImages.length > 1 && (
            <button type="button" className="canvas-lightbox-nav is-prev" onClick={(event) => { event.stopPropagation(); navigateLightbox(-1); }} title="上一张" aria-label="上一张">
              <ChevronLeft size={20} strokeWidth={2} aria-hidden="true" />
            </button>
          )}
          <img src={zoomTarget} alt="放大查看" />
          {lightboxIndex >= 0 && lightboxImages.length > 1 && (
            <button type="button" className="canvas-lightbox-nav is-next" onClick={(event) => { event.stopPropagation(); navigateLightbox(1); }} title="下一张" aria-label="下一张">
              <ChevronRight size={20} strokeWidth={2} aria-hidden="true" />
            </button>
          )}
          <span>{lightboxIndex >= 0 && lightboxImages.length > 1 ? `${lightboxIndex + 1} / ${lightboxImages.length} · 点击任意处关闭` : '点击任意处关闭'}</span>
        </div>
      )}

      {canvas && promptShot && (
        <ShotPromptModal shot={promptShot} canvas={canvas} onClose={() => setPromptShot(null)} onZoom={(url) => setZoomTarget(url)} />
      )}

      {promptSegment && (
        <SegmentPromptModal segment={promptSegment} onClose={() => setPromptSegment(null)} />
      )}

      {/* 媒体节点编辑弹窗（Portal 到 body，浮层于画布之上） */}
      <MediaNodeModal
        node={editNode}
        assets={mediaNodes.filter(a => a.id !== editNode?.id)}
        edges={allEdges}
        catalog={modelCatalog}
        onChange={updateMediaNode}
        onGenerate={onGenerateMediaNode}
        onDelete={deleteMediaNode}
        onRemoveRef={(refNodeId) => { if (editNode) removeRefAndEdge(editNode.id, refNodeId); }}
        onUploadRef={uploadRefFile}
        onPickRef={(node, refNodeId) => addRefById(node, refNodeId)}
        onBackdropPick={(flowId) => {
          // 只处理媒体卡（media-xxx）；备注等其它类型照旧走"点外部即关闭"。
          if (!flowId.startsWith('media-')) return false;
          const mnId = flowId.slice('media-'.length);
          const nextNode = mediaNodes.find((m) => m.id === mnId);
          if (!nextNode) return false;
          if (editNode?.id === mnId) return true;   // 点的就是当前这张：什么都不做
          // 不能只替换 editNodeId：那样只换标题/内容，modalAnchor 仍停在旧卡片，
          // 弹窗看起来不会跟着新卡片走，选中态和引用补全也会漏掉。
          openMediaModal(nextNode);
          return true;
        }}
        onRevealAt={(requiredBottom) => {
          // 正数 = 弹层底部越界，抬高画布内容；负数 = 顶部越界，压低内容。
          if (!requiredBottom) return;
          const now = performance.now();
          if (now - revealRef.current.at < 120) return;
          revealRef.current = {at: now, key: 1};
          const current = rf.getViewport();
          const limit = Math.max(0, window.innerHeight - 120);
          const shift = Math.max(-limit, Math.min(limit, requiredBottom));
          rf.setViewport(
            {...current, y: current.y + shift},
            {duration: 180},
          );
        }}
        onClose={(reason) => {
          revealRef.current = {key: 0, at: 0};
          setEditNodeId(null);
          setModalAnchor(null);
          if (reason !== 'clear') return;
          if (locateSelectTimerRef.current) {
            window.clearTimeout(locateSelectTimerRef.current);
            locateSelectTimerRef.current = null;
          }
          setSelectedId(null);
          setNodes((prev) => prev.some((node) => node.selected)
            ? prev.map((node) => node.selected ? {...node, selected: false} : node)
            : prev);
          setEdges((prev) => prev.some((edge) => edge.selected)
            ? prev.map((edge) => edge.selected ? {...edge, selected: false} : edge)
            : prev);
        }}
        anchor={modalAnchor}
      />
    </div>
  );
}

// useReactFlow() 必须在 ReactFlowProvider 内调用；此前缺失 Provider 导致挂载即崩溃、整页白屏
export function CanvasView(props: Props) {
  return (
    <ReactFlowProvider>
      <CanvasViewInner {...props} />
    </ReactFlowProvider>
  );
}
