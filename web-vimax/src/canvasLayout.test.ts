import {describe, expect, it} from 'vitest';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {absolutePositionOf, alignGuideDashDevicePx, alignGuideDeviceQuantum, alignGuideSnapQuantum, easeAlignSnap, mergeAdjacentAlignGuides, alignGuideStrokeDevicePx, alignGuideStrokePx, alignGuideSegments, ALIGN_GUIDE_THICK_ZOOM, applyLayoutPositions, canvasIdlePresentation, canvasViewportIndexKey, computeAlignSnap, createAlignState, createUserEdgeIdFactory, fallbackSegmentsOf, initialViewportForScope, layoutPositionsForExport, pickDefaultEpisode, resolveInitialEpisode, resolveLoadedEpisodeId, segShotIdxsOf, snapToPixel, solveAlignSnap, validUserConnections, viewportFromPayload, viewportIndexForSave, workspaceScopeMatches} from './canvasLayout';

describe('canvas idle presentation', () => {
  it('shows the empty workspace only after the target episode is fully loaded', () => {
    expect(canvasIdlePresentation({
      dataReady: false,
      mediaCount: 0,
      noteCount: 0,
    }).showEmptyWorkspace).toBe(false);
    expect(canvasIdlePresentation({
      dataReady: true,
      mediaCount: 0,
      noteCount: 0,
    }).showEmptyWorkspace).toBe(true);
    expect(canvasIdlePresentation({
      dataReady: true,
      mediaCount: 0,
      noteCount: 1,
    }).showEmptyWorkspace).toBe(false);
  });
});

describe('canvas viewport scope', () => {
  it('restores the active episode before the project fallback', () => {
    const scopes = [
      {scope: '__project__', zoom: 0.4},
      {scope: 'EP010', zoom: 0.72},
    ];
    expect(initialViewportForScope(scopes, 'EP010')?.zoom).toBe(0.72);
    expect(initialViewportForScope(scopes, 'EP011')?.zoom).toBe(0.4);
    expect(initialViewportForScope([], 'EP011')).toBeNull();
  });

  it('replaces only the active episode viewport entry', () => {
    const next = viewportIndexForSave(
      [
        {scope: '__project__', zoom: 0.4},
        {scope: 'EP010', zoom: 0.6},
      ],
      'EP010',
      {scope: 'EP010', zoom: 0.75},
    );
    expect(next).toEqual([
      {scope: '__project__', zoom: 0.4},
      {scope: 'EP010', zoom: 0.75},
    ]);
  });

  it('uses one stable viewport index per project', () => {
    expect(canvasViewportIndexKey('project-1'))
      .toBe('vimax.canvas.viewports.project-1');
  });

  it('prefers the server viewport for the active episode', () => {
    const payload = {
      episodeViewports: {
        EP004: {x: 10, y: 20, zoom: 0.75},
        EP010: {x: 30, y: 40, zoom: 0.6},
      },
    };
    expect(viewportFromPayload(payload, 'project-1', 'EP004'))
      .toEqual({x: 10, y: 20, zoom: 0.75});
    expect(viewportFromPayload(payload, 'project-1', null)).toBeNull();
  });

  it('falls back to the legacy project viewport for single-episode projects', () => {
    const payload = {
      episodeViewports: {
        'project-1': {x: 5, y: 6, zoom: 0.8},
      },
    };
    expect(viewportFromPayload(payload, 'project-1', null))
      .toEqual({x: 5, y: 6, zoom: 0.8});
  });
});

describe('canvas layout import', () => {
  it('projects media and note coordinates without losing unchanged nodes', () => {
    const media = [
      {id: 'image-1', x: 1, y: 2, title: 'image'},
      {id: 'video-1', x: 3, y: 4, title: 'video'},
    ];
    const notes = [{id: 'note-1', x: 5, y: 6, text: 'note'}];
    const result = applyLayoutPositions({
      positions: {
        'media-image-1': {x: 20, y: 30},
        'media-video-1': {x: 3, y: 4},
        'note-note-1': {x: 50, y: 60},
      },
      mediaNodes: media,
      notes,
    });

    expect(result.mediaNodes).toHaveLength(2);
    expect(result.mediaNodes[0]).toMatchObject({
      id: 'image-1',
      x: 20,
      y: 30,
      title: 'image',
    });
    expect(result.mediaNodes[1]).toBe(media[1]);
    expect(result.notes[0]).toMatchObject({
      id: 'note-1',
      x: 50,
      y: 60,
      text: 'note',
    });
    expect(result.changedMedia.map((node) => node.id)).toEqual(['image-1']);
    expect(result.changedNotes.map((note) => note.id)).toEqual(['note-1']);
  });
});

describe('resolveInitialEpisode', () => {
  it('URL 中的集号优先，支持刷新和分享链接', () => {
    expect(resolveInitialEpisode('?ep=EP001', 'EP003')).toBe('EP001');
  });

  it('没有 URL 时恢复该项目上次选中的集', () => {
    expect(resolveInitialEpisode('', 'ep01')).toBe('EP01');
  });

  it('没有 URL 和本地记忆时返回 null，交给默认集策略', () => {
    expect(resolveInitialEpisode('', null)).toBeNull();
    expect(resolveInitialEpisode('', '')).toBeNull();
  });
});

describe('resolveLoadedEpisodeId', () => {
  const episodes = [
    {ep_id: 'EP001'},
    {ep_id: 'EP002'},
    {ep_id: 'EP010'},
  ];

  it('uses the server active episode after the workspace has loaded', () => {
    expect(resolveLoadedEpisodeId({
      activeEpisodeId: 'EP002',
      episodes,
      preferredEpisodeId: 'EP010',
    })).toBe('EP002');
  });

  it('keeps a valid URL or remembered episode when the server omits an active id', () => {
    expect(resolveLoadedEpisodeId({
      activeEpisodeId: null,
      episodes,
      preferredEpisodeId: 'ep010',
    })).toBe('EP010');
  });

  it('rejects a stale cross-project episode and falls back to the first valid episode', () => {
    expect(resolveLoadedEpisodeId({
      activeEpisodeId: null,
      episodes,
      preferredEpisodeId: 'EP099',
    })).toBe('EP001');
  });

  it('binds an empty project to EP001 instead of leaving a writable null scope', () => {
    expect(resolveLoadedEpisodeId({
      activeEpisodeId: null,
      episodes: [],
    })).toBe('EP001');
  });
});

const shots = [
  {idx: 0, segment_id: 1, ep_id: 'ep01'},
  {idx: 1, segment_id: 1, ep_id: 'ep01'},
  {idx: 2, segment_id: 1, ep_id: 'ep02'}, // 跨集同段号：总览模式不得串入 ep01 段1
  {idx: 3, segment_id: 2, ep_id: 'ep02'},
];

describe('segShotIdxsOf', () => {
  it('优先使用 shot_idxs', () => {
    expect(segShotIdxsOf({segment_id: 1, shot_idxs: [7, 8], ep_id: 'ep01'}, shots)).toEqual([7, 8]);
  });

  it('单集模式回退按 segment_id 匹配', () => {
    expect(segShotIdxsOf({segment_id: 1}, shots, {overview: false})).toEqual([0, 1, 2]);
  });

  it('总览模式回退按集过滤（防跨集串镜）', () => {
    expect(segShotIdxsOf({segment_id: 1, ep_id: 'ep01'}, shots, {overview: true})).toEqual([0, 1]);
    expect(segShotIdxsOf({segment_id: 1, ep_id: 'ep02'}, shots, {overview: true})).toEqual([2]);
  });

  it('总览模式 seg 无 ep_id 时不过滤（兼容旧数据）', () => {
    expect(segShotIdxsOf({segment_id: 1}, shots, {overview: true})).toEqual([0, 1, 2]);
  });
});

describe('fallbackSegmentsOf', () => {
  it('按 segment_id 聚合，且带上 ep_id（总览定位靠它拼段卡 id）', () => {
    const segs = fallbackSegmentsOf([
      {idx: 0, segment_id: 0, ep_id: 'ep01'},
      {idx: 1, segment_id: 0, ep_id: 'ep01'},
      {idx: 2, segment_id: 1, ep_id: 'ep01'},
    ]);
    expect(segs).toEqual([
      {segment_id: 0, ep_id: 'ep01', shot_idxs: [0, 1]},
      {segment_id: 1, ep_id: 'ep01', shot_idxs: [2]},
    ]);
  });

  it('跨集同段号不得合并', () => {
    const segs = fallbackSegmentsOf([
      {idx: 0, segment_id: 0, ep_id: 'ep01'},
      {idx: 1, segment_id: 0, ep_id: 'ep02'},
    ]);
    expect(segs).toHaveLength(2);
    expect(segs.map((s) => s.ep_id)).toEqual(['ep01', 'ep02']);
    expect(segs.every((s) => s.segment_id === 0)).toBe(true);
  });

  it('真未分段时每 4 镜一段，段号 0 基', () => {
    const all = Array.from({length: 5}, (_, i) => ({idx: i, segment_id: 0, ep_id: 'ep01'}));
    const segs = fallbackSegmentsOf(all);
    expect(segs.map((s) => s.segment_id)).toEqual([0, 1]);
    expect(segs[0].shot_idxs).toEqual([0, 1, 2, 3]);
    expect(segs[1].shot_idxs).toEqual([4]);
  });

  it('镜头无 ep_id 时 ep_id 缺省，不写死 ep00', () => {
    const segs = fallbackSegmentsOf([
      {idx: 0, segment_id: 0},
      {idx: 1, segment_id: 1},
    ]);
    expect(segs.every((s) => s.ep_id === undefined)).toBe(true);
  });

  it('无镜头时返回空', () => {
    expect(fallbackSegmentsOf([])).toEqual([]);
  });
});

describe('absolutePositionOf', () => {
  const nodes = [
    {id: 'top', position: {x: 100, y: 50}},
    {id: 'box', position: {x: 20, y: 10}, parentId: 'top'},
    {id: 'child', position: {x: 5, y: 7}, parentId: 'box'},
  ];

  it('顶层节点直接返回', () => {
    expect(absolutePositionOf('top', nodes)).toEqual({x: 100, y: 50});
  });

  it('子节点沿父链累加', () => {
    expect(absolutePositionOf('child', nodes)).toEqual({x: 125, y: 67});
    expect(absolutePositionOf('box', nodes)).toEqual({x: 120, y: 60});
  });

  it('缺失节点返回 null', () => {
    expect(absolutePositionOf('missing', nodes)).toBeNull();
  });

  it('父链断裂时用已有累加值', () => {
    const orphan = [{id: 'o', position: {x: 3, y: 4}, parentId: 'gone'}];
    expect(absolutePositionOf('o', orphan)).toEqual({x: 3, y: 4});
  });
});

describe('createUserEdgeIdFactory', () => {
  it('同毫秒内不碰撞', () => {
    const next = createUserEdgeIdFactory();
    const ids = new Set(Array.from({length: 200}, next));
    expect(ids.size).toBe(200);
  });

  it('前缀可定制', () => {
    const next = createUserEdgeIdFactory('e-ref');
    expect(next().startsWith('e-ref-')).toBe(true);
  });
});

describe('validUserConnections', () => {
  it('drops stale endpoints, self loops, duplicates, and reverse duplicates', () => {
    const connections = [
      {id: 'keep', fromNodeId: 'a', toNodeId: 'b'},
      {id: 'stale-target', fromNodeId: 'a', toNodeId: 'deleted'},
      {id: 'self', fromNodeId: 'a', toNodeId: 'a'},
      {id: 'duplicate', fromNodeId: 'a', toNodeId: 'b'},
      {id: 'reverse', fromNodeId: 'b', toNodeId: 'a'},
    ];

    expect(validUserConnections(connections, ['a', 'b'])).toEqual([
      {id: 'keep', fromNodeId: 'a', toNodeId: 'b'},
    ]);
  });

  it('trims endpoint ids without mutating the source edge', () => {
    const connection = {id: 'trim', fromNodeId: ' a ', toNodeId: ' b '};

    expect(validUserConnections([connection], ['a', 'b'])).toEqual([
      {id: 'trim', fromNodeId: 'a', toNodeId: 'b'},
    ]);
    expect(connection).toEqual({id: 'trim', fromNodeId: ' a ', toNodeId: ' b '});
  });
});

describe('pickDefaultEpisode', () => {
  it('空列表返回 null', () => {
    expect(pickDefaultEpisode([])).toBeNull();
    expect(pickDefaultEpisode(null)).toBeNull();
  });

  it('有内容的集优先：即使空集 updated_at 更新也进有内容的集', () => {
    // 实测场景：ep01 有 32 镜，ep02 有 0 镜但最后被触碰 → 旧逻辑会开在 ep02（空画布）
    const eps = [
      {ep_id: 'ep01', shot_count: 32, keyframes: 32, clips: 0, updated_at: '2026-10-12T10:00:00'},
      {ep_id: 'ep02', shot_count: 0, keyframes: 0, clips: 0, updated_at: '2026-10-12T23:00:00'},
    ];
    expect(pickDefaultEpisode(eps)).toBe('ep01');
  });

  it('多集都有内容时取最近活动的一集', () => {
    const eps = [
      {ep_id: 'ep01', shot_count: 32, clips: 32, updated_at: '2026-10-11T10:00:00'},
      {ep_id: 'ep02', shot_count: 5, clips: 0, updated_at: '2026-10-12T10:00:00'},
    ];
    expect(pickDefaultEpisode(eps)).toBe('ep02');
  });

  it('全部为空时回退集号最小的一集（不是列表最后一项）', () => {
    const eps = Array.from({length: 120}, (_, i) => ({
      ep_id: `ep${String(i + 1).padStart(3, '0')}`,
      shot_count: 0,
      keyframes: 0,
      clips: 0,
      updated_at: null,
    }));
    expect(pickDefaultEpisode(eps)).toBe('ep001');
  });

  it('集号自然序：ep2 小于 ep10', () => {
    const eps = [
      {ep_id: 'ep10', shot_count: 0, clips: 0, updated_at: null},
      {ep_id: 'ep2', shot_count: 0, clips: 0, updated_at: null},
    ];
    expect(pickDefaultEpisode(eps)).toBe('ep2');
  });

  it('storyboard_ready 也算有内容（分镜已就绪但 shot_count 为 null）', () => {
    const eps = [
      {ep_id: 'ep01', shot_count: null, keyframes: 0, clips: 0, storyboard_ready: false, updated_at: null},
      {ep_id: 'ep02', shot_count: null, keyframes: 0, clips: 0, storyboard_ready: true, updated_at: null},
    ];
    expect(pickDefaultEpisode(eps)).toBe('ep02');
  });

  it('有内容但无时间戳时按产出量（片段 > 关键帧 > 镜头）排序', () => {
    const eps = [
      {ep_id: 'ep01', shot_count: 32, keyframes: 32, clips: 0, updated_at: null},
      {ep_id: 'ep02', shot_count: 4, keyframes: 4, clips: 4, updated_at: null},
    ];
    expect(pickDefaultEpisode(eps)).toBe('ep02');
  });
});

describe('workspaceScopeMatches', () => {
  it('drops a refresh response after a project switch', () => {
    expect(workspaceScopeMatches(
      {session: 'project-a', episode: 'EP001'},
      {session: 'project-b', episode: 'EP001'},
    )).toBe(false);
  });

  it('drops a refresh response after an episode switch', () => {
    expect(workspaceScopeMatches(
      {session: 'project-a', episode: 'EP001'},
      {session: 'project-a', episode: 'EP002'},
    )).toBe(false);
  });

  it('accepts the active project and episode', () => {
    expect(workspaceScopeMatches(
      {session: 'project-a', episode: 'EP001'},
      {session: 'project-a', episode: 'EP001'},
    )).toBe(true);
  });
});

describe('layoutPositionsForExport', () => {
  it('merges persisted nodes with current media and note coordinates', () => {
    expect(layoutPositionsForExport({
      persisted: {
        'note-persisted': {x: 30, y: 40},
        'segment-1': {x: 50, y: 60},
      },
      mediaNodes: [{id: 'image-1', x: 100, y: 200}],
      notes: [{id: 'note-1', x: 300, y: 400}],
    })).toEqual({
      'note-persisted': {x: 30, y: 40},
      'segment-1': {x: 50, y: 60},
      'media-image-1': {x: 100, y: 200},
      'note-note-1': {x: 300, y: 400},
    });
  });
});

describe('computeAlignSnap', () => {
  const moving = {id: 'move', x: 103, y: 200, w: 100, h: 60};
  const other = {id: 'a', x: 300, y: 200, w: 100, h: 60};

  it('无其它节点时不吸附', () => {
    expect(computeAlignSnap(moving, [])).toEqual({dx: 0, dy: 0, guides: []});
  });

  it('只在真的对齐之后才画线（不再提前预告）', () => {
    // 第 26 轮：跟随即梦 —— 没对齐就没有线，避免"线已经画了但卡片不吸"的错觉
    const r = solveAlignSnap(
      {id: 'moving', x: 111, y: 260, w: 100, h: 60},
      [{id: 'a', x: 100, y: 80, w: 100, h: 60}],
      createAlignState(),
      6,
      14,
      22,
    );
    expect(r.dx).toBe(0);
    expect(r.guides.length).toBe(0);
  });

  it('左边缘相差在阈值内 → 吸附并画竖线', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 100, y: 500, w: 100, h: 60}]);
    expect(r.dx).toBe(-3);
    expect(r.dy).toBe(0);
    // 同宽卡片对齐时左/中/右三条边同时命中（即梦同款），不再只画一条
    expect(r.guides.length).toBe(3);
    expect(r.guides.every((g) => g.axis === 'v')).toBe(true);
    expect(r.guides[0].pos).toBe(100);
  });

  it('超出阈值不吸附', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 140, y: 500, w: 100, h: 60}]);
    expect(r.dx).toBe(0);
    expect(r.guides.length).toBe(0);
  });

  it('中心线对齐也算（锚点含中点）', () => {
    // moving 中心 x = 153；other 左边缘 150 → 差 -3，命中
    const r = computeAlignSnap(moving, [{id: 'a', x: 150, y: 900, w: 100, h: 60}]);
    expect(r.dx).toBe(-3);
    expect(r.guides[0].pos).toBe(150);
  });

  it('水平方向独立吸附', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 900, y: 198, w: 100, h: 60}]);
    expect(r.dx).toBe(0);
    expect(r.dy).toBe(-2);
    expect(r.guides[0].axis).toBe('h');
    expect(r.guides[0].pos).toBe(198);
  });

  it('同时命中横竖 → 两轴都有参考线', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 100, y: 198, w: 100, h: 60}]);
    expect([...new Set(r.guides.map((g) => g.axis))].sort()).toEqual(['h', 'v']);
  });

  it('多个候选时取偏差最小的一条（不糊满参考线）', () => {
    const r = computeAlignSnap(moving, [
      {id: 'far', x: 106, y: 900, w: 100, h: 60},  // 偏差 +3
      {id: 'nearer', x: 104, y: 900, w: 100, h: 60}, // 偏差 +1
    ]);
    expect(r.dx).toBe(1);
    expect(r.guides[0].pos).toBe(104);
    // 只跟最近的那个目标画线（104），另一个候选 106 不在其中
    expect(r.guides.every((g) => g.pos !== 106)).toBe(true);
  });

  it('候选偏差接近时优先选择空间上最近的节点', () => {
    const r = computeAlignSnap(
      {id: 'moving', x: 103, y: 200, w: 100, h: 60},
      [
        {id: 'far', x: 101, y: 900, w: 100, h: 60},
        {id: 'near', x: 104, y: 205, w: 100, h: 60},
      ],
      20,
    );
    expect(r.dx).toBe(1);
    expect(r.guides.find((guide) => guide.axis === 'v')?.pos).toBe(104);
  });

  it('参考线只画两张卡之间那一段（不再贯穿画布）', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 100, y: 500, w: 100, h: 60}]);
    // moving 下沿 260 → 目标上沿 500，只有这段空隙属于"这两张卡之间"
    expect(r.guides[0].from).toBe(260);
    expect(r.guides[0].to).toBe(500);
  });
});

// 回归：候选排序曾经按 hypot(|delta|, 垂直空隙) 打分，"离得近"压过"对得齐"，
// 导致想把一张卡对到正上方那张卡的左边缘时，永远被同排邻居抢走 —— 用户原话
// "类似这样的很难跟上面对齐"。下面用的都是真实画布尺寸（canvas.db）：
// 图片卡 720×405、视频卡 420×746.7。
describe('候选排序：先「齐」再「近」', () => {
  const IMG = {w: 720, h: 405};
  const VID = {w: 420, h: 746.7};

  // 被拖的视频卡左边缘比正上方的图片卡差 3px；同排右邻居差 20px。
  const moving = {id: 'moving', x: 3, y: 500, w: VID.w, h: VID.h};
  const above = {id: 'above', x: 0, y: 0, w: IMG.w, h: IMG.h};
  const sameRow = {id: 'same-row', x: 443, y: 500, w: VID.w, h: VID.h};

  it('正上方差 3px 的卡，胜过同排差 20px 的邻居', () => {
    const r = computeAlignSnap(moving, [sameRow, above], 50);
    const v = r.guides.find((guide) => guide.axis === 'v');
    expect(v?.pos).toBe(0);
    // 区间落在「上方那张卡下沿 405 → 被拖卡上沿 500」之间，证明线连的是正上方那张
    expect(v?.from).toBe(405);
    expect(v?.to).toBe(500);
  });

  it('画出来的线与实际吸住的目标必须是同一个节点', () => {
    const drawn = computeAlignSnap(moving, [sameRow, above], 50)
      .guides.find((guide) => guide.axis === 'v');
    const solved = solveAlignSnap(moving, [sameRow, above], createAlignState(), 13, 39, 96);
    const locked = solved.guides.find((guide) => guide.axis === 'v');
    expect(solved.dx).toBe(-3);
    expect(locked?.pos).toBe(0);
    expect(locked?.pos).toBe(drawn?.pos);
  });

  it('同一条边命中多张卡时只画一根，区间覆盖全部命中目标', () => {
    const m = {id: 'm', x: 100, y: 500, w: 100, h: 60};
    const near = {id: 'near', x: 100, y: 400, w: 100, h: 60};
    const far = {id: 'far', x: 100, y: 1200, w: 100, h: 60};
    const vs = computeAlignSnap(m, [far, near], 50).guides.filter((guide) => guide.axis === 'v');
    // 三张同宽卡左边缘都在 x=100，左/中/右三条边都命中 → 3 根（不是 6 根）。
    // 以前每命中一次就 push 一根，同位置的线完全重合、互相盖住，还把这轴 4 个名额
    // 吃光，导致别处的边一根都画不出来（"少一根"）。现在同位置只留一根、区间取并集。
    expect(vs.length).toBe(3);
    expect(vs.map((g) => g.pos).sort((a, b) => a - b)).toEqual([100, 150, 200]);
    const left = vs.find((g) => g.pos === 100);
    expect(left?.from).toBe(460);
    expect(left?.to).toBe(1200);
  });

  it('同列里一张卡也不漏：偏差更小的远端目标仍然胜出', () => {
    const m = {id: 'm', x: 640, y: 900, w: VID.w, h: VID.h};
    const columnHead = {id: 'head', x: 640, y: 0, w: IMG.w, h: IMG.h}; // 偏差 0
    const sameRow = {id: 'same-row', x: 646, y: 900, w: VID.w, h: VID.h}; // 偏差 +6
    const v = computeAlignSnap(m, [sameRow, columnHead], 50)
      .guides.find((guide) => guide.axis === 'v');
    expect(v?.pos).toBe(640);
  });
});

// 第 26 轮（对标即梦）：一次拖动要把**所有**对上的边都画出来。
// 即梦在两张等高的卡片并排时会同时画上/中/下三条线，我们以前一根轴只画一条。
describe('alignGuideSegments（即梦式多段参考线）', () => {
  it('等高卡片并排 → 上/中/下三条水平线同时画出', () => {
    const a = {id: 'a', x: 0, y: 100, w: 200, h: 100};
    const b = {id: 'b', x: 300, y: 100, w: 200, h: 100};
    const hs = alignGuideSegments(b, [a]).filter((g) => g.axis === 'h');
    expect(hs.map((g) => g.pos).sort((x, y) => x - y)).toEqual([100, 150, 200]);
    // 每条都只跨两张卡之间的空隙（200 → 300），不贯穿画布
    for (const g of hs) {
      expect(g.from).toBe(200);
      expect(g.to).toBe(300);
    }
  });

  it('没有真正对齐就不产生线', () => {
    const a = {id: 'a', x: 0, y: 100, w: 200, h: 100};
    const b = {id: 'b', x: 301, y: 101, w: 200, h: 100};
    expect(alignGuideSegments(b, [a])).toEqual([]);
  });

  it('尺寸未知（NaN）的候选被忽略，不会污染参考线', () => {
    const a = {id: 'a', x: 0, y: 100, w: 200, h: 100};
    const broken = {id: 'broken', x: Number.NaN, y: Number.NaN, w: Number.NaN, h: Number.NaN};
    const segs = alignGuideSegments({id: 'b', x: 0, y: 100, w: 200, h: 100}, [broken, a]);
    expect(segs.every((s) => Number.isFinite(s.pos) && Number.isFinite(s.from) && Number.isFinite(s.to))).toBe(true);
    expect(segs.length).toBeGreaterThan(0);
  });

  it('NaN 尺寸不会成为吸附目标（否则卡片会僵住不动）', () => {
    const broken = {id: 'broken', x: Number.NaN, y: Number.NaN, w: Number.NaN, h: Number.NaN};
    const r = computeAlignSnap({id: 'm', x: 0, y: 0, w: 100, h: 60}, [broken], 20);
    expect(r.dx).toBe(0);
    expect(r.dy).toBe(0);
    expect(r.guides).toEqual([]);
  });
});

describe('solveAlignSnap（磁吸模型）', () => {
  const other = {id: 'a', x: 100, y: 500, w: 100, h: 60};
  // 阈值档：enter=6 / glue=26 / release=44（画布 px）
  const ENTER = 6;
  const GLUE = 26;
  const RELEASE = 44;

  it('无候选时不修正', () => {
    const raw = {id: 'move', x: 400, y: 400, w: 100, h: 60};
    const r = solveAlignSnap(raw, [other], createAlignState(), ENTER, GLUE, RELEASE);
    expect(r.dx).toBe(0);
    expect(r.dy).toBe(0);
    expect(r.guides.length).toBe(0);
  });

  it('进入吸附阈值 → 吸附到目标并建立锁定', () => {
    const raw = {id: 'move', x: 103, y: 400, w: 100, h: 60};
    const r = solveAlignSnap(raw, [other], createAlignState(), ENTER, GLUE, RELEASE);
    expect(r.dx).toBe(-3);
    expect(r.state.lockV).not.toBeNull();
    expect(r.state.lockV!.target).toBe(100);
    // 等宽卡片对齐后左/中/右三条边同时命中 → 三条竖线（即梦同款）
    expect(r.guides.length).toBeGreaterThan(0);
    expect(r.guides.every((g) => g.axis === 'v')).toBe(true);
    expect(r.guides[0].pos).toBe(100);
  });

  it('锁定后指针继续移动 → 节点被磁住不弹回（修正量吸收位移）', () => {
    let st = createAlignState();
    // 第一帧：进入阈值
    st = solveAlignSnap({id: 'move', x: 103, y: 400, w: 100, h: 60}, [other], st, ENTER, GLUE, RELEASE).state;
    // 第二帧：指针又右移 20px（原始位置 123），仍在 glue 范围内
    const raw2 = {id: 'move', x: 123, y: 400, w: 100, h: 60};
    const r2 = solveAlignSnap(raw2, [other], st, ENTER, GLUE, RELEASE);
    // 修正量应把节点拉回 x=100（-23），而不是只做 -3 的弱吸附
    expect(r2.dx).toBe(-23);
    expect(r2.state.lockV).not.toBeNull();
  });

  it('超出 glue 但在 release 内 → 修正量线性衰减（平滑滑出线，无跳变）', () => {
    let st = createAlignState();
    st = solveAlignSnap({id: 'move', x: 100, y: 400, w: 100, h: 60}, [other], st, ENTER, GLUE, RELEASE).state;
    // 指针右移 35px：deviation=35，glue=26，release=44
    // w = (44-35)/(44-26) = 9/18 = 0.5 → correction = -35 * 0.5 = -17.5
    const r = solveAlignSnap({id: 'move', x: 135, y: 400, w: 100, h: 60}, [other], st, ENTER, GLUE, RELEASE);
    expect(r.dx).toBeCloseTo(-17.5, 5);
    expect(r.state.lockV).not.toBeNull();
  });

  it('超出 release → 解除锁定，完全跟随指针', () => {
    let st = createAlignState();
    st = solveAlignSnap({id: 'move', x: 100, y: 400, w: 100, h: 60}, [other], st, ENTER, GLUE, RELEASE).state;
    const r = solveAlignSnap({id: 'move', x: 160, y: 400, w: 100, h: 60}, [other], st, ENTER, GLUE, RELEASE);
    expect(r.dx).toBe(0);
    expect(r.state.lockV).toBeNull();
    expect(r.guides.length).toBe(0);
  });

  it('横竖两轴独立锁定', () => {
    const raw = {id: 'move', x: 103, y: 503, w: 100, h: 60};
    const r = solveAlignSnap(raw, [other], createAlignState(), ENTER, GLUE, RELEASE);
    expect(r.dx).toBe(-3);
    expect(r.dy).toBe(-3);
    expect([...new Set(r.guides.map((g) => g.axis))].sort()).toEqual(['h', 'v']);
  });

  it('多个候选取偏差最小的一条', () => {
    const raw = {id: 'move', x: 103, y: 400, w: 100, h: 60};
    const r = solveAlignSnap(raw, [
      {id: 'far', x: 106, y: 900, w: 100, h: 60},
      {id: 'nearer', x: 104, y: 900, w: 100, h: 60},
    ], createAlignState(), ENTER, GLUE, RELEASE);
    expect(r.dx).toBe(1);
    expect(r.guides.filter((g) => g.axis === 'v')[0].pos).toBe(104);
  });
});

// 第 28 轮：参考线要能回答"跟谁对上了"。同一列可能压着好几张卡，
// 只描最近的那张 —— 全描会闪成一片，等于没描。
describe('alignGuideSegments 的配对卡片 peerId', () => {
  it('同一条边上压着多张卡时，peerId 取最近的那张', () => {
    const moving = {id: 'm', x: 300, y: 100, w: 200, h: 100};
    const near = {id: 'near', x: 300, y: 220, w: 200, h: 100};
    const far = {id: 'far', x: 300, y: 600, w: 200, h: 100};
    const vs = alignGuideSegments(moving, [far, near]).filter((g) => g.axis === 'v');
    expect(vs.length).toBeGreaterThan(0);
    expect(vs.every((g) => g.peerId === 'near')).toBe(true);
  });

  it('水平线也带 peerId（上下对齐时描的是上方那张卡）', () => {
    const moving = {id: 'm', x: 0, y: 100, w: 200, h: 100};
    const above = {id: 'above', x: 0, y: 0, w: 200, h: 100};
    const hs = alignGuideSegments(moving, [above]).filter((g) => g.axis === 'h');
    expect(hs.length).toBeGreaterThan(0);
    expect(hs.every((g) => g.peerId === 'above')).toBe(true);
  });

  it('没有对齐对象时既没线也没 peerId', () => {
    const moving = {id: 'm', x: 0, y: 0, w: 100, h: 60};
    expect(alignGuideSegments(moving, [])).toEqual([]);
  });
});

// 第 28 轮：低缩放档线宽 1px → 1.5px，栅格同步降到 0.5px（否则又会出现深浅不一）。
// 第 29 轮补丁：两条目标边只差 1 个像素时，吸附后会落到相邻两列 → 视觉上是一条 2px 粗线，
// 而且两套虚线相位不同互相穿插，用户看到的就是「一根深、一根浅」。
// 第 30 轮：吸附修正量改成逐帧缓动，把「被拽过去」改成「被吸过去」。
// 第 30 轮定稿：用户明确选择「固定大小」。
// 第 31 轮修正（本轮最贵的一课）：只固定长度是不够的 —— 线宽那一维仍在跟着 zoom 变粗。
//   真因：`vector-effect="non-scaling-stroke"` 只抵消 **SVG 内部**的变换，
//   挡不住 React Flow 加在 `.react-flow__viewport`（一个 div）上的 **CSS transform: scale(z)**。
//   探针实测（父 div scale + 对照 path，量真实像素）：
//     z=1 → 线宽 2px / 亮段 36px；z=2 → 4px / 36px；z=3 → 6px / 36px
//   ⇒ 长度不动、宽度在动 = 用户说的「变形」（放大后是又短又粗的胖墩）。
//   正确做法：**长度和宽度用同一套补偿**，都写成 `屏幕像素 ÷ zoom` 的画布单位。
describe('连线流光（屏幕像素恒定：长度 + 宽度同口径）', () => {
  const canvasSource = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
  const stylesSource = readFileSync(resolve(__dirname, 'styles.css'), 'utf8');
  const refineSource = readFileSync(resolve(__dirname, 'ui-refinements.css'), 'utf8');
  const themeSource = readFileSync(resolve(__dirname, 'theme-tokens.css'), 'utf8');
  const immersiveSource = readFileSync(resolve(__dirname, 'immersive-theme.css'), 'utf8');

  it('周期 / 彗尾 / 彗身 / 彗头都是常量', () => {
    expect(canvasSource).toMatch(/const cycleScreen = 132;/);
    expect(canvasSource).toMatch(/const tailScreen = 36;/);
    expect(canvasSource).toMatch(/const bodyScreen = 14;/);
    expect(canvasSource).toMatch(/const headScreen = 4\.5;/);
  });

  it('★ 线宽也要除以 zoom（只固定长度会"变形"）', () => {
    // 旧写法是写死 1.7 —— 那是画布单位，会被 CSS transform 一起放大
    expect(canvasSource).not.toMatch(/const width = 1\.7;/);
    expect(canvasSource).toMatch(/const width = Math\.round\(\(EDGE_LINE_WIDTH_PX \/ z\)/);
    expect(canvasSource).toMatch(/const EDGE_LINE_WIDTH_PX = 1\.7;/);
    expect(canvasSource).toMatch(/const op = 0\.43;/);
  });

  it('长度与宽度共用同一套 ÷zoom 补偿（toCanvas 与 width 同源）', () => {
    expect(canvasSource).toMatch(/const toCanvas = \(screenPx: number\) => Math\.round\(\(screenPx \/ z\)/);
    // 基线连线与彗星三层必须吃**同一个**变量，否则又是一种变形
    expect(canvasSource).toMatch(/'--edge-flow-width': String\(width\)/);
    expect(canvasSource).toMatch(/'--edge-line-w': String\(width\)/);
  });

  // ★ 这一条是防止回归的关键：ui-refinements.css 排在 styles.css 之后，
  //   它里面写死的 1.7 / 2 / 2.4 / 1.9 会把变量口径整块顶掉
  //   （实测 zoom=3 时基线仍是 1.7 画布单位 → 屏幕 5.4px）。
  it('★ 连线基线宽度不许再写死（三个 css 文件都必须是变量口径）', () => {
    for (const src of [stylesSource, refineSource]) {
      expect(src).not.toMatch(/\.canvas-edge-(auto|chain|user) \.react-flow__edge-path[^{]*\{[^}]*stroke-width:\s*1\.7[;}]/);
      expect(src).not.toMatch(/\.react-flow__edge:hover \.react-flow__edge-path\{[^}]*stroke-width:2\}/);
    }
    expect(refineSource).not.toMatch(/stroke-width:1\.7 !important/);
    expect(refineSource).not.toMatch(/stroke-width:1\.9 !important/);
  });

  // 用户原话：「不能比连线大很多，这样就不是彗星了」。
  // 彗尾是三层里最粗的一层。倍数上限锁在 2.6（选中态带 blur 才允许最宽）——
  // 这是"明显能看出是颗彗星"和"又变成粗光带"之间的分界：
  // 收到 1.4 时用户反馈"效果不明显"，放到 2.6 以上就会重演"比连线大很多"。
  it('★ 彗星最粗处不超过连线的 2.8 倍（是彗星，不是粗光带）', () => {
    const all = stylesSource + refineSource;
    const factors = [...all.matchAll(/calc\(var\(--edge-flow-width[^)]*\) \* ([\d.]+)\)/g)].map((m) => +m[1]);
    expect(factors.length).toBeGreaterThan(6);
    expect(Math.max(...factors)).toBeLessThanOrEqual(2.8);
  });

  // ★ 第 31 轮定稿：连线**只有一种颜色**（用户原话「你连线都用单个卡片选中连线颜色不就行了」），
  //   状态差异只靠透明度 + 线宽。所以彗星也只需要一套固定色 ——
  //   不许再出现"每个状态各写一遍 stroke"的那种四份维护。
  it('★ 彗星只有一套颜色，状态不再重复声明 stroke', () => {
    const strokeOf = (sel: RegExp) => {
      const m = stylesSource.match(sel);
      if (!m) return '';
      const s = m[0].match(/stroke:\s*([^;]+);/);
      return s ? s[1].trim() : '';
    };
    // 基础三层：尾 < 身 < 头（明度梯度），且每一层都明显亮于线色
    expect(strokeOf(/\.canvas-edge-flow-tail \{[^}]*\}/)).toBe('#b8e0ff');
    expect(strokeOf(/^\.canvas-edge-flow \{[^}]*\}/m)).toBe('#e6f5ff');
    expect(strokeOf(/\.canvas-edge-flow-head \{[^}]*\}/)).toBe('#ffffff');
    // 悬停 / 选中 / 生成中三块都不许再改色（否则又是四份色值各自漂移）
    const blocks = [
      /\.react-flow__edge:hover \.canvas-edge-flow \{[^}]*\}/,
      /\.react-flow__edge:hover \.canvas-edge-flow-tail \{[^}]*\}/,
      /\.react-flow__edge\.selected \.canvas-edge-flow \{[^}]*\}/,
      /\.react-flow__edge\.selected \.canvas-edge-flow-tail \{[^}]*\}/,
      /\.canvas-flow \.react-flow__edge \.canvas-edge-flow\.is-busy:not\(\.is-linked\) \{[^}]*\}/,
    ];
    for (const re of blocks) {
      const m = stylesSource.match(re);
      expect(m, String(re)).toBeTruthy();
      expect(m![0]).not.toMatch(/stroke:\s*#/);
    }
    // 选中卡片的相连线同样不许改色（只提不透明度）
    const linked = refineSource.match(/\.canvas-edge-flow\.is-linked\{[^}]*\}/);
    expect(linked && linked[0]).not.toMatch(/stroke:/);
  });

  it('★ 三层流光使用 React Flow 原生矢量路径', () => {
    expect(canvasSource).toContain('className={`canvas-edge-flow-tail');
    expect(canvasSource).toContain('className={`canvas-edge-flow ');
    expect(canvasSource).toContain('className={`canvas-edge-flow-head');
    expect(canvasSource).not.toContain('CanvasEdgeFlowOverlay');
    expect(stylesSource).not.toContain('.canvas-edge-flow-canvas');
  });

  it('★ 连线层禁止 paint containment：该 SVG 实测为 0×0，会被整体裁掉', () => {
    const edgeLayer = stylesSource.match(
      /\.canvas-flow \.react-flow__edges \{[^}]*\}/,
    );
    expect(edgeLayer).toBeTruthy();
    expect(edgeLayer![0]).not.toMatch(/contain:\s*paint;/);
  });

  // ★ 连线状态色：常态灰；悬停、选中、执行中必须是三支不同令牌。
  it('★ 悬停、选中、执行中使用独立状态色，常态仍为灰色', () => {
    const hoverBlock = themeSource.match(
      /\.react-flow__edge:hover \.react-flow__edge-path \{\s*stroke: ([^;]+);/,
    );
    const selectedBlock = themeSource.match(
      /\.react-flow__edge\.selected \.react-flow__edge-path \{\s*stroke: ([^;]+);/,
    );
    expect(hoverBlock, '悬停状态规则缺失').toBeTruthy();
    expect(selectedBlock, '选中状态规则缺失').toBeTruthy();
    expect(hoverBlock![1].trim()).toBe('var(--v-edge-hover) !important');
    expect(selectedBlock![1].trim()).toBe('var(--v-edge-selected) !important');

    const baseBlock = themeSource.match(
      /\.canvas-edge-auto \.react-flow__edge-path,[\s\S]*?\{\s*stroke: ([^;]+);/,
    );
    expect(baseBlock![1].trim()).toBe('var(--v-edge-tone) !important');
    expect(stylesSource).toMatch(/--edge-tone: var\(--v-edge-tone, #64798b\);/);

    for (const token of ['--v-edge-hover', '--v-edge-selected', '--v-edge-running']) {
      expect(themeSource).toMatch(new RegExp(`${token}: #[0-9a-f]{6};`, 'i'));
    }
    expect(themeSource).toMatch(
      /\.canvas-flow \.react-flow__edge:has\(\.canvas-edge-flow\.is-busy:not\(\.is-linked\)\) \.react-flow__edge-path \{\s*stroke: var\(--v-edge-running\) !important;/,
    );
    expect(refineSource).not.toMatch(/#7fc4ff/);
  });

  it('★ 石墨黑默认主题的连线使用统一冷天蓝层级', () => {
    expect(themeSource).toMatch(/--v-edge-hover: #009efa;/);
    expect(themeSource).toMatch(/--v-edge-selected: #0099f2;/);
    expect(themeSource).toMatch(/--v-edge-running: #0091e5;/);
    // immersive-theme 在 theme-tokens 之前加载；这里锁住它不再把连线拉回
    // gray/cyan 的另一套色相，最终值由 theme-tokens 的 --v-* 真源决定。
    expect(immersiveSource).not.toMatch(/--v-edge-(hover|selected|running)/);
  });

  // ★ 用户原话：「线也是这个颜色，彗星也是这个颜色，彗星效果不明显」。
  // 旧的彗尾就是线色本身 （#4d9fff）⇒ 尾整段隐形，只剩几个白点在跑。
  // 三层必须**明显亮于线色（悬停/选中那个蓝 #4d9fff）**，且保持"尾 < 身 < 头"的梯度。
  it('★ 彗星三层都比线亮（不会和线糊在一起）', () => {
    const luma = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const LINE = luma('#009efa');    // 悬停线色
    const tail = luma('#b8e0ff');
    const body = luma('#e6f5ff');
    const head = luma('#ffffff');
    expect(tail).toBeGreaterThan(LINE + 50);
    expect(body).toBeGreaterThan(tail);
    expect(head).toBeGreaterThan(body);
  });
});

describe('easeAlignSnap（吸附缓动）', () => {
  it('朝目标推进但不一次到位（第一帧只走一半左右）', () => {
    const first = easeAlignSnap(0, 8, 0.5);
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(8);
    expect(first).toBeCloseTo(8 * 0.52, 5);
  });

  it('4 帧内精确落到目标上（不会无限逼近留下浮点尾巴）', () => {
    let v = 0;
    let frames = 0;
    while (v !== 8 && frames < 20) {
      v = easeAlignSnap(v, 8, 0.5);
      frames += 1;
    }
    expect(v).toBe(8);
    expect(frames).toBeLessThanOrEqual(5);
  });

  it('目标回到 0 时同样平滑退出（滑出吸附也是滑出去的）', () => {
    let v = 8;
    const seq: number[] = [];
    for (let i = 0; i < 6; i += 1) {
      v = easeAlignSnap(v, 0, 0.5);
      seq.push(v);
    }
    // 单调递减，且不会越过 0 变号
    for (let i = 1; i < seq.length; i += 1) expect(seq[i]).toBeLessThanOrEqual(seq[i - 1]);
    expect(seq[seq.length - 1]).toBe(0);
  });

  it('差值小于 eps 时直接取齐（避免最后 0.1px 拖好几帧）', () => {
    expect(easeAlignSnap(7.8, 8, 0.5)).toBe(8);
    expect(easeAlignSnap(-0.2, 0, 0.5)).toBe(0);
  });

  it('k=1 等价于不缓动（保留退回旧行为的口子）', () => {
    expect(easeAlignSnap(0, 8, 0.5, 1)).toBe(8);
  });

  it('非法输入不产出 NaN（NaN 进 style 会让线停在上一帧）', () => {
    expect(easeAlignSnap(0, Number.NaN, 0.5)).toBe(0);
    expect(easeAlignSnap(Number.NaN, 8, 0.5)).toBe(8 * 0.52);
    expect(easeAlignSnap(Number.POSITIVE_INFINITY, 8, 0.5)).toBe(8 * 0.52);
  });
});

describe('mergeAdjacentAlignGuides（相邻参考线合并）', () => {
  it('同一轴上相距 ≤1 个物理像素的线合并成一条，区间取并集', () => {
    const merged = mergeAdjacentAlignGuides([
      {axis: 'v', pos: 800, from: 100, to: 400},
      {axis: 'v', pos: 801, from: 250, to: 600},
    ], 1);
    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({axis: 'v', pos: 800, from: 100, to: 600});
  });

  it('相距 2 个像素以上不合并（那是两条真的不同的边）', () => {
    const merged = mergeAdjacentAlignGuides([
      {axis: 'v', pos: 800, from: 100, to: 400},
      {axis: 'v', pos: 802, from: 100, to: 400},
    ], 1);
    expect(merged).toHaveLength(2);
  });

  it('不同轴的线哪怕坐标相同也不合并', () => {
    const merged = mergeAdjacentAlignGuides([
      {axis: 'v', pos: 300, from: 0, to: 100},
      {axis: 'h', pos: 300, from: 0, to: 100},
    ], 1);
    expect(merged).toHaveLength(2);
  });

  it('高 DPR 下栅格更细：0.5 间距仍然合并（DPR2 的一个物理像素）', () => {
    const merged = mergeAdjacentAlignGuides([
      {axis: 'v', pos: 800, from: 0, to: 100},
      {axis: 'v', pos: 800.5, from: 0, to: 100},
    ], 0.5);
    expect(merged).toHaveLength(1);
  });

  it('保留 peerId、不改动入参、空数组安全', () => {
    const input = [{axis: 'v' as const, pos: 10, from: 0, to: 10, peerId: 'media-a'}];
    const merged = mergeAdjacentAlignGuides(input, 1);
    expect(merged[0].peerId).toBe('media-a');
    expect(input[0].pos).toBe(10);
    expect(mergeAdjacentAlignGuides([], 1)).toEqual([]);
  });
});

describe('alignGuideStrokePx / alignGuideSnapQuantum（缩放分档线宽）', () => {
  it('阈值以上（含阈值本身）保持 1px，正常编辑观感一点不变', () => {
    expect(alignGuideStrokePx(1)).toBe(1);
    expect(alignGuideStrokePx(0.8)).toBe(1);
    expect(alignGuideStrokePx(ALIGN_GUIDE_THICK_ZOOM)).toBe(1);
  });

  it('低于阈值提到 1.5px（小地图视角也看得见）', () => {
    expect(alignGuideStrokePx(0.34)).toBe(1.5);
    expect(alignGuideStrokePx(0.2)).toBe(1.5);
    expect(alignGuideStrokePx(0.05)).toBe(1.5);
  });

  it('非法 zoom 退回 1px，绝不产出 NaN', () => {
    expect(alignGuideStrokePx(0)).toBe(1);
    expect(alignGuideStrokePx(Number.NaN)).toBe(1);
    expect(alignGuideStrokePx(Number.POSITIVE_INFINITY)).toBe(1);
  });

  it('1.5px 档的吸附栅格是 0.5px，1px 档仍是整像素', () => {
    expect(alignGuideSnapQuantum(1)).toBe(1);
    expect(alignGuideSnapQuantum(ALIGN_GUIDE_THICK_ZOOM)).toBe(1);
    expect(alignGuideSnapQuantum(0.34)).toBe(0.5);
  });
});

// 第 29 轮：DPR 是小数（Windows 125% / 150%）时，"整数 CSS 像素"仍然不是设备像素边界。
// 用户原话「虚线有时候深 有时候浅，就不能跟即梦一样一直保持一样的深色吗」。
describe('alignGuideDeviceQuantum / alignGuideStrokeDevicePx（设备像素对齐）', () => {
  it('栅格 = 1/DPR，DPR=1 时与原来的整像素一致', () => {
    expect(alignGuideDeviceQuantum(1)).toBe(1);
    expect(alignGuideDeviceQuantum(2)).toBe(0.5);
    expect(alignGuideDeviceQuantum(1.25)).toBe(0.8);
    expect(alignGuideDeviceQuantum(1.5)).toBeCloseTo(0.6667, 3);
  });

  it('非法 DPR 退回 1，不产出 0/Infinity', () => {
    expect(alignGuideDeviceQuantum(0)).toBe(1);
    expect(alignGuideDeviceQuantum(-2)).toBe(1);
    expect(alignGuideDeviceQuantum(Number.NaN)).toBe(1);
    expect(alignGuideDeviceQuantum(Number.POSITIVE_INFINITY)).toBe(1);
  });

  it('线宽取整到整数个物理像素：DPR2 的 1px 是 2 个物理像素', () => {
    expect(alignGuideStrokeDevicePx(1, 2)).toBe(1);      // 1 * 2 = 2 物理像素
    expect(alignGuideStrokeDevicePx(1, 1)).toBe(1);
    // DPR1.25 的 1px 只能取 1 个物理像素（0.8 CSS px），否则会溢到邻行被冲淡
    expect(alignGuideStrokeDevicePx(1, 1.25)).toBe(0.8);
    // 低缩放 1.5px 档：DPR2 → 3 个物理像素，DPR1 → 2 个（1x 屏上 1.5 必然抗锯齿，取整换纯色）
    expect(alignGuideStrokeDevicePx(0.2, 2)).toBe(1.5);
    expect(alignGuideStrokeDevicePx(0.2, 1)).toBe(2);
    // 1x 屏 100% 缩放：不能被取整成 0，至少 1 个物理像素
    expect(alignGuideStrokeDevicePx(1, 1)).toBeGreaterThanOrEqual(1 / 1);
  });

  it('虚线节奏也落到整数物理像素（4px @ DPR1.25 = 5 个物理像素）', () => {
    expect(alignGuideDashDevicePx(1)).toBe(4);
    expect(alignGuideDashDevicePx(1.25)).toBe(4);
    expect(alignGuideDashDevicePx(2)).toBe(4);
    // DPR1.1：4px 只有 4.4 个物理像素 → 取整到 4 个，段边缘不再做覆盖率混合
    expect(alignGuideDashDevicePx(1.1)).toBeCloseTo(4 / 1.1, 3);
    expect(alignGuideDashDevicePx(Number.NaN)).toBe(4);
  });

  it('按设备栅格吸附后，屏幕坐标 × DPR 一定是整数（纯色的前提）', () => {
    for (const dpr of [1, 1.25, 1.5, 2, 2.5]) {
      const q = alignGuideDeviceQuantum(dpr);
      for (const world of [0, 13.37, 512.81, 1234.567]) {
        const snapped = snapToPixel(world, 0.57, 41.3, q);
        const device = (snapped * 0.57 + 41.3) * dpr;
        expect(Math.abs(device - Math.round(device))).toBeLessThan(1e-6);
      }
    }
  });
});

describe('snapToPixel（参考线像素对齐）', () => {
  // 屏幕坐标 = world * zoom + pan；吸附后必须落在整数上，1px 线才不会被抗锯齿摊成两列。
  const screenOf = (world: number, zoom: number, pan: number) => world * zoom + pan;

  it('吸附后的屏幕坐标是整数（59% 缩放 + 半像素平移）', () => {
    const zoom = 0.59;
    const pan = -320.5;
    for (const world of [670.14, 1537.3, -812.77, 0.42]) {
      const out = snapToPixel(world, zoom, pan);
      expect(Number.isInteger(Math.round(screenOf(out, zoom, pan) * 1e9) / 1e9)).toBe(true);
      expect(Math.abs(screenOf(out, zoom, pan) - Math.round(screenOf(out, zoom, pan)))).toBeLessThan(1e-6);
    }
  });

  it('偏差不超过半个屏幕像素（不改变视觉位置）', () => {
    const zoom = 0.59;
    const pan = -123.4;
    for (const world of [10, 670.14, 999.99, -5.3]) {
      const out = snapToPixel(world, zoom, pan);
      expect(Math.abs(screenOf(out, zoom, pan) - screenOf(world, zoom, pan))).toBeLessThanOrEqual(0.5 + 1e-9);
    }
  });

  it('100% 缩放下就是四舍五入', () => {
    expect(snapToPixel(100.4, 1, 0)).toBe(100);
    expect(snapToPixel(100.6, 1, 0)).toBe(101);
    expect(snapToPixel(100.5, 1, 0)).toBe(101);
  });

  it('缩到 20% 时画布坐标的偏差变大，但屏幕偏差仍 ≤0.5px', () => {
    const zoom = 0.2;
    const out = snapToPixel(1234.567, zoom, 88.8);
    expect(Math.abs(screenOf(out, zoom, 88.8) - screenOf(1234.567, zoom, 88.8))).toBeLessThanOrEqual(0.5);
  });

  it('非法 zoom / 坐标不产生 NaN（否则样式写 NaNpx 会被丢弃，线卡在上一帧）', () => {
    expect(snapToPixel(100, Number.NaN, 0)).toBe(100);
    expect(snapToPixel(100, 0, 0)).toBe(100);
    expect(snapToPixel(Number.NaN, 1, 0)).toBeNaN();
    expect(Number.isFinite(snapToPixel(100, 1, Number.NaN))).toBe(true);
  });

  // 第 28 轮：线宽 1.5px 时，整像素栅格上放不下 1.5px，仍会溢 0.5px 到隔壁列被冲淡。
  // 栅格降到 0.5px 后，DPR=2 的屏幕上 1.5 CSS px = 整整 3 个物理像素，颜色回到纯色。
  it('quantum=0.5 时落在半个屏幕像素上，且偏差 ≤0.25px', () => {
    const zoom = 0.25;
    const pan = 17.3;
    for (const world of [88.1, 1234.567, -40.02, 0.03]) {
      const out = snapToPixel(world, zoom, pan, 0.5);
      const screen = screenOf(out, zoom, pan);
      expect(Math.abs(screen * 2 - Math.round(screen * 2))).toBeLessThan(1e-6);
      expect(Math.abs(screen - screenOf(world, zoom, pan))).toBeLessThanOrEqual(0.25 + 1e-9);
    }
  });

  it('非法 quantum 退回整像素栅格，不产生 NaN', () => {
    expect(snapToPixel(100.4, 1, 0, Number.NaN)).toBe(100);
    expect(snapToPixel(100.6, 1, 0, 0)).toBe(101);
  });
});
