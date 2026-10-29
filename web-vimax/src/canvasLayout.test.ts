import {describe, expect, it} from 'vitest';
import {absolutePositionOf, ALIGN_VISIBLE_PX, computeAlignSnap, createAlignState, createUserEdgeIdFactory, fallbackSegmentsOf, pickDefaultEpisode, segShotIdxsOf, solveAlignSnap} from './canvasLayout';

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

describe('computeAlignSnap', () => {
  const moving = {id: 'move', x: 103, y: 200, w: 100, h: 60};
  const other = {id: 'a', x: 300, y: 200, w: 100, h: 60};

  it('无其它节点时不吸附', () => {
    expect(computeAlignSnap(moving, [])).toEqual({dx: 0, dy: 0, guides: []});
  });

  it('提前显示即将对齐的竖排参考线', () => {
    const r = computeAlignSnap(
      {id: 'moving', x: 111, y: 260, w: 100, h: 60},
      [{id: 'a', x: 100, y: 80, w: 100, h: 60}],
      ALIGN_VISIBLE_PX,
    );
    expect(r.dx).toBe(-11);
    expect(r.guides.find((guide) => guide.axis === 'v')?.delta).toBe(-11);
  });

  it('左边缘相差在阈值内 → 吸附并画竖线', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 100, y: 500, w: 100, h: 60}]);
    expect(r.dx).toBe(-3);
    expect(r.dy).toBe(0);
    expect(r.guides.length).toBe(1);
    expect(r.guides[0].axis).toBe('v');
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

  it('同时命中横竖 → 两条参考线', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 100, y: 198, w: 100, h: 60}]);
    expect(r.guides.length).toBe(2);
    expect(r.guides.map((g) => g.axis).sort()).toEqual(['h', 'v']);
  });

  it('多个候选时取偏差最小的一条（不糊满参考线）', () => {
    const r = computeAlignSnap(moving, [
      {id: 'far', x: 106, y: 900, w: 100, h: 60},  // 偏差 +3
      {id: 'nearer', x: 104, y: 900, w: 100, h: 60}, // 偏差 +1
    ]);
    expect(r.dx).toBe(1);
    expect(r.guides[0].pos).toBe(104);
    expect(r.guides.length).toBe(1);
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

  it('参考线覆盖区间取两节点并集', () => {
    const r = computeAlignSnap(moving, [{id: 'a', x: 100, y: 500, w: 100, h: 60}]);
    expect(r.guides[0].from).toBe(200);
    expect(r.guides[0].to).toBe(560);
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
    expect(r.guides.length).toBe(1);
    expect(r.guides[0].axis).toBe('v');
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
    expect(r.guides.map((g) => g.axis).sort()).toEqual(['h', 'v']);
  });

  it('多个候选取偏差最小的一条', () => {
    const raw = {id: 'move', x: 103, y: 400, w: 100, h: 60};
    const r = solveAlignSnap(raw, [
      {id: 'far', x: 106, y: 900, w: 100, h: 60},
      {id: 'nearer', x: 104, y: 900, w: 100, h: 60},
    ], createAlignState(), ENTER, GLUE, RELEASE);
    expect(r.dx).toBe(1);
    expect(r.guides.length).toBe(1);
    expect(r.guides[0].pos).toBe(104);
  });
});
