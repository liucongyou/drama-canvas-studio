import {mkdtemp, mkdir, readFile, readdir, stat, utimes, writeFile} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {afterEach, describe, expect, it} from 'vitest';
import {aggregateSessionUsage, aggregateUsageLog, compactSessionStateForEvents, coverageGapMessage, deleteSession, deleteEpisodeRenderOutputs, episodeRenderCoverage, extractWorkspaceUploadPaths, listSessionArtifacts, missingWorkspaceUploads, parseByteRange, pickNewSessionId, readCanvas, readEpisodeProgress, readLedger, readSessionHistory, readSessionHistoryPage, readSessionState, recomputeEpisodeProgress, resolveArtifactPath, resolveProductionEntryAsset, resolveProductionRequest, saveCanvasShot, storeWorkspaceUpload} from './server-lib.mjs';

const roots = [];

// 慢文件系统（Windows 沙箱 rm 一个临时目录可达十几秒）下默认 10s 的 hook 预算不够，
// 会以 "Hook timed out" 假失败，与产品逻辑无关。
afterEach(async () => {
  const {rm} = await import('node:fs/promises');
  await Promise.all(roots.splice(0).map((root) => rm(root, {recursive: true, force: true})));
}, 90_000);

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), 'vimax-web-'));
  roots.push(root);
  await mkdir(path.join(root, '.vimax', 'logs'), {recursive: true});
  await mkdir(path.join(root, '.working_dir', 'session-1', 'script2video', 'shots', '0'), {recursive: true});
  await writeFile(path.join(root, '.vimax', 'sessions.json'), JSON.stringify({
    active_session_id: 'session-1',
    sessions: {
      'session-1': {
        session_id: 'session-1',
        project_name: 'Ocean campaign',
        working_dir: '.working_dir/session-1',
        stage: 'rendering',
        updated_at: '2026-07-17T10:00:00',
        workflow_plan: {title: '生成分镜', stage: 'storyboard', status: 'ready', steps: []},
      },
    },
  }));
  return root;
}

describe('web bridge state', {timeout: 15_000}, () => {
  it('returns sanitized session records', async () => {
    const root = await fixture();
    const state = await readSessionState(root);
    expect(state.activeSessionId).toBe('session-1');
    expect(state.sessions[0]).toMatchObject({
      sessionId: 'session-1',
      projectName: 'Ocean campaign',
      stage: 'rendering',
      workflowPlan: {title: '生成分镜', stage: 'storyboard', status: 'ready'},
    });
  });

  it('overrides stale JSON stage with authoritative SQLite stage', async () => {
    // 渲染中断场景：sessions.json 残留 stage=rendering（旧值），
    // 状态库已按磁盘扫描校正为 assets_pending —— 前端必须读到权威值，
    // 流程条才会停留在真实步骤（资产待生成）而非"生成关键帧中"。
    const root = await fixture();
    const {DatabaseSync} = await import('node:sqlite');
    const db = new DatabaseSync(path.join(root, '.vimax', 'project_state.sqlite3'));
    try {
      db.exec('CREATE TABLE IF NOT EXISTS projects (session_id TEXT PRIMARY KEY, mode TEXT DEFAULT \'\', stage TEXT DEFAULT \'\', first_artifact_at TEXT DEFAULT \'\', created_at TEXT DEFAULT \'\', updated_at TEXT DEFAULT \'\')');
      db.prepare("INSERT INTO projects(session_id, mode, stage, first_artifact_at, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?)").run(
        'session-1', 'script2video', 'assets_pending', '2026-07-17T10:00:00', '2026-07-17T10:00:00', '2026-07-17T10:00:00');
    } finally {
      db.close();
    }
    const state = await readSessionState(root);
    expect(state.sessions[0].stage).toBe('assets_pending');
    // render_status.json 残留 rendering 且未完成时，summary 给出中断提示
    await writeFile(path.join(root, '.working_dir', 'session-1', 'render_status.json'), JSON.stringify({
      timestamp: '2026-07-17T10:00:00', status: 'rendering', session_id: 'session-1', render_started: true, render_completed: false,
    }));
    const stateAfter = await readSessionState(root);
    expect(stateAfter.sessions[0].stage).toBe('assets_pending');
    expect(stateAfter.sessions[0].summary).toContain('渲染被中断');
  });

  it('compacts long session text for SSE without dropping sidebar metadata', () => {
    const compact = compactSessionStateForEvents({
      activeSessionId: 'session-1',
      sessions: [{
        sessionId: 'session-1',
        projectName: 'Ocean campaign',
        stage: 'rendering',
        idea: 'A'.repeat(2_000),
        summary: 'B'.repeat(1_500),
      }],
    });

    expect(compact.activeSessionId).toBe('session-1');
    expect(compact.sessions[0]).toMatchObject({sessionId: 'session-1', projectName: 'Ocean campaign', stage: 'rendering'});
    expect(compact.sessions[0].idea).toHaveLength(160);
    expect(compact.sessions[0].summary).toHaveLength(160);
  });

  it('restores persisted turn history', async () => {
    const root = await fixture();
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1', turn_id: 'turn-1', raw_user_input: 'Make a film', final_assistant_text: 'Planning is ready', status: 'completed', tool_rounds: [],
    })}\n`);
    const history = await readSessionHistory(root, 'session-1');
    expect(history.map((message) => message.role)).toEqual(['user', 'assistant']);
  });

  it('merges running checkpoints into one restored turn', async () => {
    const root = await fixture();
    const checkpoint = {
      session_id: 'session-1',
      turn_id: 'turn-checkpoint',
      raw_user_input: 'Plan a video',
      status: 'running',
      tool_rounds: [{tool_results: [{name: 'vimax_narrative_planning', ok: true, content: '{}'}]}],
      final_assistant_text: '',
    };
    const completed = {...checkpoint, status: 'completed', final_assistant_text: 'Finished'};
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify(checkpoint)}\n${JSON.stringify(completed)}\n`);
    const history = await readSessionHistory(root, 'session-1');
    expect(history.map((message) => message.role)).toEqual(['user', 'activity', 'assistant']);
    expect(history[2].text).toBe('Finished');
  });

  it('restores chat history across rotated log files', async () => {
    const root = await fixture();
    const logsRoot = path.join(root, '.vimax', 'logs');
    await writeFile(path.join(logsRoot, 'loop_history.jsonl.2'), `${JSON.stringify({
      session_id: 'session-1', turn_id: 'turn-old', raw_user_input: 'Old request', final_assistant_text: 'Old answer', status: 'completed',
    })}\n`);
    await writeFile(path.join(logsRoot, 'loop_history.jsonl.1'), `${JSON.stringify({
      session_id: 'session-1', turn_id: 'turn-middle', raw_user_input: 'Middle request', final_assistant_text: 'Middle answer', status: 'completed',
    })}\n`);
    await writeFile(path.join(logsRoot, 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1', turn_id: 'turn-new', raw_user_input: 'New request', final_assistant_text: 'New answer', status: 'completed',
    })}\n`);

    const history = await readSessionHistory(root, 'session-1');
    expect(history.map((message) => message.text)).toEqual([
      'Old request', 'Old answer', 'Middle request', 'Middle answer', 'New request', 'New answer',
    ]);
  });

  it('paginates chat history by complete turns', async () => {
    const root = await fixture();
    const logPath = path.join(root, '.vimax', 'logs', 'loop_history.jsonl');
    await writeFile(logPath, Array.from({length: 5}, (_, index) => JSON.stringify({
      session_id: 'session-1',
      turn_id: `turn-${index + 1}`,
      raw_user_input: `Request ${index + 1}`,
      final_assistant_text: `Answer ${index + 1}`,
      status: 'completed',
    })).join('\n') + '\n');

    const latest = await readSessionHistoryPage(root, 'session-1', {limit: 2});
    expect(latest.messages.map((message) => message.text)).toEqual([
      'Request 4', 'Answer 4', 'Request 5', 'Answer 5',
    ]);
    expect(latest).toMatchObject({hasMore: true, nextCursor: 'turn-4'});

    const older = await readSessionHistoryPage(root, 'session-1', {limit: 2, before: latest.nextCursor});
    expect(older.messages.map((message) => message.text)).toEqual([
      'Request 2', 'Answer 2', 'Request 3', 'Answer 3',
    ]);
    expect(older).toMatchObject({hasMore: true, nextCursor: 'turn-2'});

    const oldest = await readSessionHistoryPage(root, 'session-1', {limit: 2, before: older.nextCursor});
    expect(oldest.messages.map((message) => message.text)).toEqual(['Request 1', 'Answer 1']);
    expect(oldest).toMatchObject({hasMore: false, nextCursor: 'turn-1'});
  });

  it('filters canvas assets, voices and segments by episode', async () => {
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    const ep02 = path.join(scriptRoot, 'episodes', 'ep02');
    for (const dir of [
      path.join(scriptRoot, 'character_portraits', '0_周稳'),
      path.join(scriptRoot, 'character_portraits', '1_老贩'),
      path.join(scriptRoot, 'character_portraits', '2_月神'),
      path.join(scriptRoot, 'character_portraits', '3_天王'),
      path.join(scriptRoot, 'scenes'),
      path.join(scriptRoot, 'props'),
      path.join(scriptRoot, 'voices'),
      ep01,
      ep02,
    ]) {
      await mkdir(dir, {recursive: true});
    }
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '周稳', static_features: '城管'},
      {idx: 1, identifier_in_scene: '老贩', static_features: '摊贩'},
      {idx: 2, identifier_in_scene: '月神', static_features: '神官'},
      {idx: 3, identifier_in_scene: '天王', static_features: '守门'},
    ]));
    for (const [dirName, fileName] of [['0_周稳', 'sheet.png'], ['1_老贩', 'sheet.png'], ['2_月神', 'sheet.png'], ['3_天王', 'sheet.png']]) {
      await writeFile(path.join(scriptRoot, 'character_portraits', dirName, fileName), 'png');
    }
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_hall.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_gate.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_unregistered.png'), 'png');
    await writeFile(path.join(scriptRoot, 'props', 'old_cup.png'), 'png');
    await writeFile(path.join(scriptRoot, 'props', 'red_line.png'), 'png');
    await writeFile(path.join(scriptRoot, 'voices', '周稳.mp3'), 'audio');
    await writeFile(path.join(scriptRoot, 'voices', '月神.mp3'), 'audio');
    await writeFile(path.join(scriptRoot, 'voices', '天王.mp3'), 'audio');
    await writeFile(path.join(scriptRoot, 'scene_props.json'), JSON.stringify({
      scenes: [
        {name: '场次1：办事大厅', image_path: path.join(scriptRoot, 'scenes', 'scene_hall.png')},
        {name: '场次1：南天门', image_path: path.join(scriptRoot, 'scenes', 'scene_gate.png')},
      ],
      props: [
        {name: '旧保温杯', image_path: path.join(scriptRoot, 'props', 'old_cup.png')},
        {name: '红线糖人', image_path: path.join(scriptRoot, 'props', 'red_line.png')},
      ],
    }));
    await writeFile(path.join(ep01, 'script.txt'), [
      '第1集 办事大厅', '场次1：办事大厅', '地点：周稳工位', '人物：周稳、老贩', '周稳拿起保温杯。',
    ].join('\n'));
    await writeFile(path.join(ep02, 'script.txt'), [
      '第2集 南天门', '场次1：南天门', '地点：摊位前', '人物：周稳、月神、天王', '红线糖人散落在地。',
      '场次2：后巷', '地点：摊位后巷', '人物：周稳', '周稳走进后巷。',
    ].join('\n'));
    await writeFile(path.join(ep01, 'segments.json'), JSON.stringify([{segment_id: 'ep01-seg-1'}]));
    await writeFile(path.join(ep02, 'segments.json'), JSON.stringify([{segment_id: 'ep02-seg-1'}]));

    const first = await readCanvas(root, 'session-1', 'ep01');
    const second = await readCanvas(root, 'session-1', 'ep02');

    expect(first.characters.map((item) => item.name)).toEqual(['周稳', '老贩']);
    expect(first.scenes.map((item) => item.name)).toEqual(['场次1：办事大厅']);
    expect(first.props.map((item) => item.name)).toEqual(['旧保温杯']);
    expect(first.voices.map((item) => item.name)).toEqual(['周稳']);
    expect(first.segments.map((item) => item.segment_id)).toEqual(['ep01-seg-1']);
    expect(first.meta.asset_scope).toMatchObject({ep_id: 'ep01', filtered: true, segments: 1});

    expect(second.characters.map((item) => item.name)).toEqual(['周稳', '月神', '天王']);
    expect(second.scenes.map((item) => item.name)).toHaveLength(2);
    expect(second.scenes.map((item) => item.name)).toEqual(expect.arrayContaining(['场次1：南天门', '地点：摊位后巷']));
    expect(second.props.map((item) => item.name)).toEqual(['红线糖人']);
    expect(second.voices.map((item) => item.name)).toEqual(['周稳', '月神', '天王']);
    expect(second.segments.map((item) => item.segment_id)).toEqual(['ep02-seg-1']);
    expect(second.meta.asset_scope).toMatchObject({ep_id: 'ep02', filtered: true, segments: 1});
  });

  it('keeps episode scenes when the script uses the 场1-1 内·地点·日 heading format', async () => {
    // 回归：剧本实际写作「场1-1 内·南天门城管大队·办事大厅·日」，场次解析只认「场次N：」
    // 时解析出 0 场 → 本集场景被整组过滤 → 画布场景组空、场景连线全丢。
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    await mkdir(ep01, {recursive: true});
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '周稳', static_features: '城管'},
    ]));
    for (const dir of [path.join(scriptRoot, 'scenes'), path.join(scriptRoot, 'props'), path.join(scriptRoot, 'voices')]) {
      await mkdir(dir, {recursive: true});
    }
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_hall.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_gate.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_other_ep.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scene_props.json'), JSON.stringify({
      scenes: [
        {name: '场1-1 内·南天门城管大队·办事大厅·日', image_path: path.join(scriptRoot, 'scenes', 'scene_hall.png')},
        {name: '场1-2 外·南天门·日', image_path: path.join(scriptRoot, 'scenes', 'scene_gate.png')},
        {name: '场2-1 外·东海·夜', image_path: path.join(scriptRoot, 'scenes', 'scene_other_ep.png')},
      ],
    }));
    await writeFile(path.join(ep01, 'script.txt'), [
      '第01集 临时工',
      '场1-1 内·南天门城管大队·办事大厅·日',
      '〖人物〗周稳',
      '周稳擦桌子。',
      '场1-2 外·南天门·日',
      '周稳走出大门。',
    ].join('\n'));

    const canvas = await readCanvas(root, 'session-1', 'ep01');
    expect(canvas.meta.asset_scope).toMatchObject({ep_id: 'ep01', filtered: true, scenes: 2});
    expect(canvas.scenes.map((item) => item.name).sort()).toEqual([
      '场1-1 内·南天门城管大队·办事大厅·日',
      '场1-2 外·南天门·日',
    ]);
  });

  it('does not blank out episode scenes when no scene heading can be parsed', async () => {
    // 防御：剧本没有可识别的场次标题时宁可不做场景过滤，也不能让本集场景整组消失。
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    await mkdir(ep01, {recursive: true});
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '周稳', static_features: '城管'},
    ]));
    for (const dir of [path.join(scriptRoot, 'scenes'), path.join(scriptRoot, 'props'), path.join(scriptRoot, 'voices')]) {
      await mkdir(dir, {recursive: true});
    }
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_hall.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scene_props.json'), JSON.stringify({
      scenes: [{name: '天庭办事大厅', image_path: path.join(scriptRoot, 'scenes', 'scene_hall.png')}],
    }));
    await writeFile(path.join(ep01, 'script.txt'), ['第01集 临时工', '〖人物〗周稳', '周稳擦桌子。'].join('\n'));

    const canvas = await readCanvas(root, 'session-1', 'ep01');
    expect(canvas.scenes).toHaveLength(1);
  });

  it('binds shots to scenes from shot_description.json scene_id', async () => {
    // 回归：shot_description.json 自带 scene_id，但绑定只读 storyboard 回退分支注入的
    // _sceneId，导致落盘镜头永远绑不上场景（画布无场景连线）。
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    for (const dir of [
      path.join(scriptRoot, 'scenes'),
      path.join(scriptRoot, 'props'),
      path.join(scriptRoot, 'voices'),
      ep01,
      path.join(ep01, 'shots', '0'),
    ]) {
      await mkdir(dir, {recursive: true});
    }
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '周稳', static_features: '城管'},
    ]));
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_hall.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_gate.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scene_props.json'), JSON.stringify({
      scenes: [
        {name: '场1-1 内·南天门城管大队·办事大厅·日', image_path: path.join(scriptRoot, 'scenes', 'scene_hall.png')},
        {name: '场1-2 外·南天门·日', image_path: path.join(scriptRoot, 'scenes', 'scene_gate.png')},
      ],
    }));
    await writeFile(path.join(ep01, 'script.txt'), [
      '第01集 临时工',
      '场1-1 内·南天门城管大队·办事大厅·日',
      '〖人物〗周稳',
      '周稳擦桌子。',
      '场1-2 外·南天门·日',
      '周稳走出大门。',
    ].join('\n'));
    await writeFile(path.join(ep01, 'storyboard.json'), JSON.stringify({shots: [
      {idx: 0, segment_id: 0, scene_id: '南天门城管大队·办事大厅', visual_desc: '周稳擦桌子', char_ids: [0]},
    ]}));
    await writeFile(path.join(ep01, 'shots', '0', 'shot_description.json'), JSON.stringify({
      idx: 0,
      segment_id: 0,
      scene_id: '南天门城管大队·办事大厅',
      visual_desc: '周稳擦桌子',
      ff_vis_char_idxs: [0],
      reference_assets: '角色：周稳；场景：办事大厅',
    }));

    const canvas = await readCanvas(root, 'session-1', 'ep01');
    expect(canvas.scenes).toHaveLength(2);
    expect(canvas.shots).toHaveLength(1);
    // 场景顺序取决于文件名排序，按名字取索引再比对
    const hallIdx = canvas.scenes.findIndex((item) => item.name.startsWith('场1-1'));
    expect(hallIdx).toBeGreaterThanOrEqual(0);
    expect(canvas.shots[0].scene_idxs).toEqual([hallIdx]);
  });

  it('aggregates all episodes with ep_id tags for the whole-season canvas overview', async () => {
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    const ep02 = path.join(scriptRoot, 'episodes', 'ep02');
    for (const dir of [
      path.join(scriptRoot, 'character_portraits', '0_周稳'),
      path.join(scriptRoot, 'character_portraits', '1_老贩'),
      path.join(scriptRoot, 'scenes'),
      path.join(scriptRoot, 'props'),
      path.join(scriptRoot, 'voices'),
      ep01,
      ep02,
    ]) {
      await mkdir(dir, {recursive: true});
    }
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '周稳', static_features: '城管'},
      {idx: 1, identifier_in_scene: '老贩', static_features: '摊贩'},
    ]));
    await writeFile(path.join(scriptRoot, 'character_portraits', '0_周稳', 'sheet.png'), 'png');
    await writeFile(path.join(scriptRoot, 'character_portraits', '1_老贩', 'sheet.png'), 'png');
    await writeFile(path.join(scriptRoot, 'scenes', 'scene_hall.png'), 'png');
    await writeFile(path.join(scriptRoot, 'props', 'old_cup.png'), 'png');
    await writeFile(path.join(scriptRoot, 'voices', '周稳.mp3'), 'audio');
    await writeFile(path.join(scriptRoot, 'scene_props.json'), JSON.stringify({
      scenes: [{name: '办事大厅', image_path: path.join(scriptRoot, 'scenes', 'scene_hall.png')}],
      props: [{name: '旧保温杯', image_path: path.join(scriptRoot, 'props', 'old_cup.png')}],
    }));
    // ep01：2 段 2 镜；ep02：1 段 1 镜（idx 与 ep01 冲突，靠 ep_id 区分）
    await writeFile(path.join(ep01, 'script.txt'), '第1集 办事大厅\n人物：周稳\n周稳拿起旧保温杯。');
    await writeFile(path.join(ep01, 'segments.json'), JSON.stringify([
      {segment_id: 0, title: '开场', shot_idxs: [0]},
      {segment_id: 1, title: '转折', shot_idxs: [1]},
    ]));
    await mkdir(path.join(ep01, 'shots', '0'), {recursive: true});
    await mkdir(path.join(ep01, 'shots', '1'), {recursive: true});
    await writeFile(path.join(ep01, 'shots', '0', 'shot_description.json'), JSON.stringify({
      ff_vis_char_idxs: [0], segment_id: 0, visual_desc: '周稳在大厅。',
    }));
    await writeFile(path.join(ep01, 'shots', '1', 'shot_description.json'), JSON.stringify({
      ff_vis_char_idxs: [0], segment_id: 1, visual_desc: '周稳拿起旧保温杯。',
    }));
    await writeFile(path.join(ep02, 'script.txt'), '第2集 转角\n人物：老贩\n老贩蹲在角落。');
    await writeFile(path.join(ep02, 'segments.json'), JSON.stringify([
      {segment_id: 0, title: '转角', shot_idxs: [0]},
    ]));
    await mkdir(path.join(ep02, 'shots', '0'), {recursive: true});
    await writeFile(path.join(ep02, 'shots', '0', 'shot_description.json'), JSON.stringify({
      ff_vis_char_idxs: [1], segment_id: 0, visual_desc: '老贩蹲在角落。',
    }));

    const overview = await readCanvas(root, 'session-1');

    expect(overview.meta.overview).toBe(true);
    expect(overview.meta.current_ep).toBeNull();
    // 段契约跨集聚合：两集各带 ep_id，segment_id 冲突靠 ep_id 区分
    expect(overview.segments.map((s) => `${s.ep_id}:${s.segment_id}`)).toEqual(['ep01:0', 'ep01:1', 'ep02:0']);
    // 镜头跨集聚合：idx 冲突不丢数据
    expect(overview.shots.map((s) => `${s.ep_id}:${s.idx}`)).toEqual(['ep01:0', 'ep01:1', 'ep02:0']);
    // 资产绑定不串集：ep02 镜头只绑老贩（项目级角色索引 1）
    const ep02Shot = overview.shots.find((s) => s.ep_id === 'ep02');
    expect(ep02Shot.char_idxs).toEqual([1]);
    const ep01Shot0 = overview.shots.find((s) => s.ep_id === 'ep01' && s.idx === 0);
    expect(ep01Shot0.char_idxs).toEqual([0]);
    expect(ep01Shot0.segment_id).toBe(0);
  });

  it('resolves relative scene_props paths against the script2video root', async () => {
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    await mkdir(path.join(scriptRoot, 'scenes'), {recursive: true});
    await mkdir(path.join(scriptRoot, 'props'), {recursive: true});
    await mkdir(ep01, {recursive: true});
    await writeFile(path.join(scriptRoot, 'scenes', 'hall.png'), 'png');
    await writeFile(path.join(scriptRoot, 'props', 'cup.png'), 'png');
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '周稳', static_features: '城管'},
    ]));
    await writeFile(path.join(scriptRoot, 'scene_props.json'), JSON.stringify({
      scenes: [{name: '场次1：办事大厅', image_path: 'scenes/hall.png'}],
      props: [{name: '旧保温杯', image_path: 'props/cup.png'}],
    }));
    await writeFile(path.join(ep01, 'script.txt'), [
      '第1集 办事大厅', '场次1：办事大厅', '人物：周稳', '周稳拿起旧保温杯。',
    ].join('\n'));

    const canvas = await readCanvas(root, 'session-1', 'ep01');

    expect(canvas.scenes.map((item) => item.name)).toEqual(['场次1：办事大厅']);
    expect(canvas.props.map((item) => item.name)).toEqual(['旧保温杯']);
  });

  it('maps episode shot character indexes through the episode-scoped character order', async () => {
    const root = await fixture();
    const scriptRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const ep01 = path.join(scriptRoot, 'episodes', 'ep01');
    await mkdir(path.join(scriptRoot, 'character_portraits', '1_甲'), {recursive: true});
    await mkdir(path.join(ep01, 'shots', '0'), {recursive: true});
    await writeFile(path.join(scriptRoot, 'character_portraits', '1_甲', 'sheet.png'), 'png');
    await writeFile(path.join(scriptRoot, 'characters.json'), JSON.stringify([
      {idx: 0, identifier_in_scene: '乙', static_features: '黑衣'},
      {idx: 1, identifier_in_scene: '甲', static_features: '白发'},
    ]));
    await writeFile(path.join(ep01, 'script.txt'), '第1集：甲登场。\n甲：我来了。');
    await writeFile(path.join(ep01, 'shots', '0', 'shot_description.json'), JSON.stringify({
      ff_vis_char_idxs: [0],
      lf_vis_char_idxs: [],
      visual_desc: '甲站在门前。',
    }));

    const canvas = await readCanvas(root, 'session-1', 'ep01');

    expect(canvas.characters.map((item) => item.name)).toEqual(['甲']);
    expect(canvas.shots[0].char_idxs).toEqual([0]);
  });

  it('rejects unsafe ep_id when saving canvas shots', async () => {
    const root = await fixture();
    await expect(saveCanvasShot(root, 'session-1', 0, {visual_desc: 'x'}, '../evil')).rejects.toThrow();
    await expect(saveCanvasShot(root, 'session-1', 0, {visual_desc: 'x'}, 'ep01/../..')).rejects.toThrow();
    // 合法集号写入集级 shots
    const out = await saveCanvasShot(root, 'session-1', 0, {visual_desc: '合法写入'}, 'ep01');
    expect(out).toMatchObject({ok: true, changed: true, idx: 0});
    const saved = JSON.parse(await readFile(path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01', 'shots', '0', 'shot_description.json'), 'utf8'));
    expect(saved.visual_desc).toBe('合法写入');
  });

  it('reads paginated history from the compact SQLite index without scanning JSONL', async () => {
    const root = await fixture();
    const logsRoot = path.join(root, '.vimax', 'logs');
    const {DatabaseSync} = await import('node:sqlite');
    const database = new DatabaseSync(path.join(logsRoot, 'log_index.sqlite3'));
    database.exec(`
      CREATE TABLE log_offsets (
        log_name TEXT PRIMARY KEY,
        offset INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE loop_turns (
        log_name TEXT NOT NULL,
        session_id TEXT NOT NULL,
        turn_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT '',
        raw_user_input TEXT NOT NULL DEFAULT '',
        final_assistant_text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT '',
        data TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (log_name, session_id, turn_id)
      );
    `);
    const insert = database.prepare(`
      INSERT INTO loop_turns (
        log_name, session_id, turn_id, status, raw_user_input,
        final_assistant_text, created_at, data, updated_at
      ) VALUES ('loop_history', 'session-1', ?, 'completed', ?, ?, '', ?, '')
    `);
    for (let index = 1; index <= 5; index += 1) {
      const turnId = `turn-index-${index}`;
      const rawUserInput = `Indexed request ${index}`;
      const finalAssistantText = `Indexed answer ${index}`;
      insert.run(turnId, rawUserInput, finalAssistantText, JSON.stringify({
        session_id: 'session-1',
        turn_id: turnId,
        raw_user_input: rawUserInput,
        final_assistant_text: finalAssistantText,
        status: 'completed',
      }));
    }
    database.prepare("INSERT INTO log_offsets (log_name, offset, updated_at) VALUES ('loop_history', 0, '')").run();
    database.close();

    const latest = await readSessionHistoryPage(root, 'session-1', {limit: 2});
    expect(latest.messages.map((message) => message.text)).toEqual([
      'Indexed request 4', 'Indexed answer 4', 'Indexed request 5', 'Indexed answer 5',
    ]);
    expect(latest).toMatchObject({hasMore: true, nextCursor: 'turn-index-4'});
  });

  it('assigns a turn moved to a new session only to its final session', async () => {
    const root = await fixture();
    const logPath = path.join(root, '.vimax', 'logs', 'loop_history.jsonl');
    await writeFile(logPath, [
      JSON.stringify({session_id: 'session-1', turn_id: 'turn-moved', raw_user_input: 'Continue planning', status: 'running'}),
      JSON.stringify({session_id: 'session-2', turn_id: 'turn-moved', raw_user_input: 'Continue planning', final_assistant_text: 'Finished', status: 'completed'}),
      '',
    ].join('\n'));

    expect(await readSessionHistory(root, 'session-1')).toEqual([]);
    expect((await readSessionHistory(root, 'session-2')).map((message) => message.text)).toEqual(['Continue planning', 'Finished']);
  });

  it('hides workspace upload metadata from restored user messages', async () => {
    const root = await fixture();
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1',
      turn_id: 'turn-upload',
      raw_user_input: 'Use this script <workspace_uploads>["uploads/script.txt"]</workspace_uploads>',
    })}\n`);
    const history = await readSessionHistory(root, 'session-1');
    expect(history[0].text).toBe('Use this script');
  });

  it('drops expired temporary render snapshot images from restored assistant messages', async () => {
    const root = await fixture();
    const snapshot = '/api/artifact?session=session-1&path=script2video/.partial_render_20261006_000256/character_portraits/3_laofan/front.png';
    const permanent = '/api/artifact?session=session-1&path=script2video/character_portraits/0_zhouwen/front.png';
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1',
      turn_id: 'turn-snapshot',
      raw_user_input: 'Check the rendered image',
      final_assistant_text: `Before .partial_render_20261006_000256/character_portraits/3_laofan\n\n![snapshot](${snapshot})\n\nAfter\n\n![permanent](${permanent})`,
      status: 'completed',
    })}\n`);

    const history = await readSessionHistory(root, 'session-1');
    const assistant = history.find((message) => message.role === 'assistant');
    expect(assistant?.text).toContain('Before');
    expect(assistant?.text).toContain('After');
    expect(assistant?.text).toContain(permanent);
    expect(assistant?.text).toContain('临时渲染快照');
    expect(assistant?.text).not.toContain('.partial_render_');
    expect(assistant?.text).not.toContain(snapshot);
  });

  it('rebinds legacy artifact links and removes missing history images', async () => {
    const root = await fixture();
    const currentPath = 'script2video/character_portraits/0_zhouwen/sheet.png';
    const currentFile = path.join(root, '.working_dir', 'session-1', currentPath);
    await mkdir(path.dirname(currentFile), {recursive: true});
    await writeFile(currentFile, 'image');

    const oldSession = encodeURIComponent('20261008-210553-映界');
    const missingPath = encodeURIComponent('script2video/props/missing.png');
    const legacyLink = `![周稳](/api/artifact?session=${oldSession}&path=${encodeURIComponent(currentPath)})`;
    const missingLink = `![失效资产](/api/artifact?session=${oldSession}&path=${missingPath})`;
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1',
      turn_id: 'turn-legacy-links',
      raw_user_input: '查看资产',
      final_assistant_text: `${legacyLink}\n\n${missingLink}`,
      status: 'completed',
    })}\n`);

    const history = await readSessionHistory(root, 'session-1');
    const assistant = history.find((message) => message.role === 'assistant');
    expect(assistant?.text).toContain('/api/artifact?session=session-1&path=');
    expect(assistant?.text).not.toContain(oldSession);
    expect(assistant?.text).not.toContain(missingPath);
  });

  it('does not expose successful tool result payloads in restored history', async () => {
    const root = await fixture();
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1',
      turn_id: 'turn-tool',
      raw_user_input: 'Plan a video',
      tool_rounds: [{tool_results: [{
        name: 'vimax_narrative_planning',
        ok: true,
        content: JSON.stringify({session_id: 'session-1', working_dir: '.working_dir/session-1', generated: ['script.json']}),
        metadata: {call_id: 'tool-abc123'},
      }]}],
    })}\n`);
    const history = await readSessionHistory(root, 'session-1');
    const activity = history.find((message) => message.role === 'activity');
    expect(activity).toMatchObject({id: 'turn-tool-tool-tool-abc123', text: '已完成', status: 'done', stage: 'completed'});
    expect(JSON.stringify(activity)).not.toContain('working_dir');
  });

  it('attaches segment-level direct-output videos to restored history in segment order', async () => {
    const root = await fixture();
    // 段级直出是当前渲染主流程：产物落在 segments/seg-N/video.mp4，不在 shots/ 下。
    // 漏掉这个目录会让"分段视频已生成"在对话历史里完全看不到（产物面板/画布却能显示）。
    const segmentsRoot = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01', 'segments');
    for (const id of [2, 0, 1]) {
      const dir = path.join(segmentsRoot, `seg-${id}`);
      await mkdir(dir, {recursive: true});
      const file = path.join(dir, 'video.mp4');
      await writeFile(file, `video-${id}`);
      // 并发渲染的完成时间 ≠ 段号顺序：seg-2 最早完成，按 mtime 排会得到 2,1,0 的乱序
      const finishedAt = new Date(2026, 0, 1, 0, 0, 10 - id);
      await utimes(file, finishedAt, finishedAt);
    }
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1',
      turn_id: 'turn-render',
      raw_user_input: '开始出片',
      tool_rounds: [{tool_results: [{name: 'vimax_render_video', ok: true, content: 'done', metadata: {call_id: 'render-1'}}]}],
      status: 'completed',
    })}\n`);

    const history = await readSessionHistory(root, 'session-1');
    const activity = history.find((message) => message.role === 'activity');
    expect(activity?.artifacts?.map((artifact) => artifact.path)).toEqual([
      'script2video/episodes/ep01/segments/seg-0/video.mp4',
      'script2video/episodes/ep01/segments/seg-1/video.mp4',
      'script2video/episodes/ep01/segments/seg-2/video.mp4',
    ]);
    expect(activity?.artifacts?.[0]).toMatchObject({kind: 'video', name: 'video.mp4'});
  });

  it('attaches single regenerated segment videos produced by non-render tools', async () => {
    const root = await fixture();
    // 真实故障：单个分段视频重新生成走 vimax_regenerate（工具名不含 "render"），旧逻辑
    // 只认名字含 render 的工具，产物永远挂不上；且产物 updatedAt 是 UTC ISO、回合时间是
    // 本地字符串，直接比字符串（东八区）恒成立，产物会成堆挂在第一个渲染回合上。
    const episodeRoot = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01', 'segments');
    const firstSegment = path.join(episodeRoot, 'seg-0', 'video.mp4');
    const secondSegment = path.join(episodeRoot, 'seg-1', 'video.mp4');
    await mkdir(path.dirname(firstSegment), {recursive: true});
    await mkdir(path.dirname(secondSegment), {recursive: true});
    await writeFile(firstSegment, 'first');
    await writeFile(secondSegment, 'second');
    // 本地时间口径：10:30 落在第 1 轮，11:30 落在第 2 轮
    const firstAt = new Date(2026, 6, 17, 10, 30, 0);
    const secondAt = new Date(2026, 6, 17, 11, 30, 0);
    await utimes(firstSegment, firstAt, firstAt);
    await utimes(secondSegment, secondAt, secondAt);

    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), [
      JSON.stringify({
        session_id: 'session-1',
        turn_id: 'turn-render',
        raw_user_input: '开始出片',
        tool_rounds: [{tool_results: [{name: 'vimax_render_video', ok: true, content: 'done', metadata: {call_id: 'render-1'}}]}],
        status: 'completed',
        created_at: '2026-07-17T10:00:00',
      }),
      JSON.stringify({
        session_id: 'session-1',
        turn_id: 'turn-regenerate',
        raw_user_input: '第 2 段重出一次',
        tool_rounds: [{tool_results: [{name: 'vimax_regenerate', ok: true, content: 'done', metadata: {call_id: 'regen-1'}}]}],
        status: 'completed',
        created_at: '2026-07-17T11:00:00',
      }),
    ].join('\n') + '\n');

    const history = await readSessionHistory(root, 'session-1');
    const byId = new Map(history.map((message) => [message.id, message]));
    expect(byId.get('turn-render-tool-render-1')?.artifacts?.map((artifact) => artifact.path)).toEqual([
      'script2video/episodes/ep01/segments/seg-0/video.mp4',
    ]);
    // 关键断言：重新生成的那一段必须挂到重新生成的那一轮，而不是继续堆在出片回合
    expect(byId.get('turn-regenerate-tool-regen-1')?.artifacts?.map((artifact) => artifact.path)).toEqual([
      'script2video/episodes/ep01/segments/seg-1/video.mp4',
    ]);
  });

  it('keeps a segment poster next to its own video when they are written in different turns', async () => {
    const root = await fixture();
    // 真实数据：分段视频写于 14:58~15:04（渲染回合），缩略图写于 16:04（更晚的回合）。
    // 若缩略图按自己的写入时间落位，就和视频分属两条活动行，对话里配不出封面图。
    const segmentDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01', 'segments', 'seg-0');
    const video = path.join(segmentDir, 'video.mp4');
    const poster = path.join(segmentDir, 'video.mp4.poster.jpg');
    await mkdir(segmentDir, {recursive: true});
    await writeFile(video, 'video');
    await writeFile(poster, 'poster');
    const videoAt = new Date(2026, 6, 17, 10, 30, 0);
    const posterAt = new Date(2026, 6, 17, 12, 30, 0);
    await utimes(video, videoAt, videoAt);
    await utimes(poster, posterAt, posterAt);
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), [
      JSON.stringify({session_id: 'session-1', turn_id: 'turn-render', raw_user_input: '出片', status: 'completed', created_at: '2026-07-17T10:00:00', tool_rounds: [{tool_results: [{name: 'vimax_render_video', ok: true, content: 'done', metadata: {call_id: 'r1'}}]}]}),
      JSON.stringify({session_id: 'session-1', turn_id: 'turn-later', raw_user_input: '看进度', status: 'completed', created_at: '2026-07-17T12:00:00', tool_rounds: [{tool_results: [{name: 'vimax_episode_status', ok: true, content: 'done', metadata: {call_id: 's1'}}]}]}),
    ].join('\n') + '\n');

    const history = await readSessionHistory(root, 'session-1');
    const byId = new Map(history.map((message) => [message.id, message]));
    expect(byId.get('turn-render-tool-r1')?.artifacts?.map((artifact) => artifact.path)).toEqual([
      'script2video/episodes/ep01/segments/seg-0/video.mp4',
      'script2video/episodes/ep01/segments/seg-0/video.mp4.poster.jpg',
    ]);
    expect(byId.get('turn-later-tool-s1')?.artifacts).toBeUndefined();
  });

  it('attaches generated portraits, scene and prop images to history', async () => {
    const root = await fixture();
    // 真实故障：白名单只放行 shots/segments/clips/final_video，vimax_generate_assets 产出的
    // 角色定妆图/场景图/道具图生成了却完全不在对话历史里出现。
    const assetRoot = path.join(root, '.working_dir', 'session-1', 'script2video');
    const targets = [
      path.join(assetRoot, 'character_portraits', '0_沈雨', 'sheet.png'),
      path.join(assetRoot, 'scenes', 'scene_abc.png'),
      path.join(assetRoot, 'props', '协议.png'),
    ];
    for (const target of targets) {
      await mkdir(path.dirname(target), {recursive: true});
      await writeFile(target, 'image');
      const at = new Date(2026, 6, 17, 10, 10, 0);
      await utimes(target, at, at);
    }
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), `${JSON.stringify({
      session_id: 'session-1',
      turn_id: 'turn-assets',
      raw_user_input: '生成资产',
      tool_rounds: [{tool_results: [{name: 'vimax_generate_assets', ok: true, content: 'done', metadata: {call_id: 'assets-1'}}]}],
      status: 'completed',
      created_at: '2026-07-17T10:00:00',
    })}\n`);

    const history = await readSessionHistory(root, 'session-1');
    const activity = history.find((message) => message.role === 'activity');
    expect(activity?.artifacts?.map((artifact) => artifact.path)).toEqual([
      'script2video/character_portraits/0_沈雨/sheet.png',
      'script2video/props/协议.png',
      'script2video/scenes/scene_abc.png',
    ]);
  });

  it('keeps artifacts on paginated history pages without leaking across pages', async () => {
    const root = await fixture();
    const episodeRoot = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01', 'segments');
    const older = path.join(episodeRoot, 'seg-0', 'video.mp4');
    const newer = path.join(episodeRoot, 'seg-1', 'video.mp4');
    for (const [target, at] of [[older, new Date(2026, 6, 17, 10, 30, 0)], [newer, new Date(2026, 6, 17, 11, 30, 0)]]) {
      await mkdir(path.dirname(target), {recursive: true});
      await writeFile(target, 'video');
      await utimes(target, at, at);
    }
    await writeFile(path.join(root, '.vimax', 'logs', 'loop_history.jsonl'), [
      JSON.stringify({session_id: 'session-1', turn_id: 'turn-1', raw_user_input: '出第 1 段', status: 'completed', created_at: '2026-07-17T10:00:00', tool_rounds: [{tool_results: [{name: 'vimax_render_video', ok: true, content: 'done', metadata: {call_id: 'c1'}}]}]}),
      JSON.stringify({session_id: 'session-1', turn_id: 'turn-2', raw_user_input: '出第 2 段', status: 'completed', created_at: '2026-07-17T11:00:00', tool_rounds: [{tool_results: [{name: 'vimax_render_video', ok: true, content: 'done', metadata: {call_id: 'c2'}}]}]}),
      JSON.stringify({session_id: 'session-1', turn_id: 'turn-3', raw_user_input: '收尾', status: 'completed', created_at: '2026-07-17T12:00:00', tool_rounds: [{tool_results: [{name: 'vimax_episode_status', ok: true, content: 'done', metadata: {call_id: 'c3'}}]}]}),
    ].join('\n') + '\n');

    // 只翻到第 2 轮这一页：旧实现 before 非空时直接不收集产物，这一页的产物会全部消失
    const page = await readSessionHistoryPage(root, 'session-1', {limit: 1, before: 'turn-3'});
    const activity = page.messages.find((message) => message.role === 'activity');
    expect(activity?.id).toBe('turn-2-tool-c2');
    expect(activity?.artifacts?.map((artifact) => artifact.path)).toEqual([
      'script2video/episodes/ep01/segments/seg-1/video.mp4',
    ]);
  });

  it('lists media artifacts and blocks path traversal', async () => {
    const root = await fixture();
    await writeFile(path.join(root, '.working_dir', 'session-1', 'script2video', 'shots', '0', 'first_frame.png'), 'image');
    const artifacts = await listSessionArtifacts(root, 'session-1');
    expect(artifacts[0]).toMatchObject({kind: 'image', name: 'first_frame.png'});
    expect(() => resolveArtifactPath(root, 'session-1', '../../secrets')).toThrow(/超出/);
  });

  it('P1-6 paginates artifacts and reports total/truncated explicitly', async () => {
    const root = await fixture();
    const base = path.join(root, '.working_dir', 'session-1', 'script2video', 'shots');
    for (let i = 0; i < 7; i += 1) {
      await mkdir(path.join(base, String(i)), {recursive: true});
      await writeFile(path.join(base, String(i), `frame_${i}.png`), `img${i}`);
    }
    const page2 = await listSessionArtifacts(root, 'session-1', {page: 2, pageSize: 3});
    expect(page2.length).toBe(3);
    expect(page2.total).toBe(7);
    expect(page2.truncated).toBe(false);
    // 分页后不重复、不越界
    const all = await listSessionArtifacts(root, 'session-1');
    const paths2 = page2.map((a) => a.path);
    expect(new Set(paths2).size).toBe(3);
    expect(all.total).toBe(7);
    expect(all.truncated).toBe(false);
  });

  it('P1-7 recomputes episode progress after cleanup (no stale "成片已完成")', async () => {
    const root = await fixture();
    const epDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01');
    await mkdir(path.join(epDir, 'shots', '0'), {recursive: true});
    await writeFile(path.join(epDir, 'storyboard.json'), JSON.stringify({shots: [{idx: 0}, {idx: 1}]}));
    await writeFile(path.join(epDir, 'segments.json'), JSON.stringify([{idx: 0}]));
    await writeFile(path.join(epDir, 'shots', '0', 'first_frame.png'), 'img');
    await writeFile(path.join(epDir, 'shots', '0', 'video.mp4'), 'vid');
    await writeFile(path.join(epDir, 'final_video.mp4'), 'final');
    // 残留旧状态（渲染已完成）
    await writeFile(path.join(epDir, '.progress.json'), JSON.stringify({status: 'rendered', final_video: {ready: true}}));
    const before = await recomputeEpisodeProgress(epDir);
    expect(before.final_video.ready).toBe(true);
    expect(before.keyframes.count).toBe(1);
    expect(before.clips.count).toBe(1);
    const freed = await deleteEpisodeRenderOutputs(root, 'session-1', 'ep01');
    expect(freed.freed_bytes).toBeGreaterThan(0);
    const after = JSON.parse(await readFile(path.join(epDir, '.progress.json'), 'utf8'));
    expect(after.final_video.ready).toBe(false);
    expect(after.keyframes.count).toBe(0);
    expect(after.clips.count).toBe(0);
    expect(after.status).toBe('storyboard_ready');
    expect(after.storyboard.shot_count).toBe(2);
  });

  it('distinguishes planned shot count from actually written shot descriptions', async () => {
    // 分镜生成中断时 storyboard.json 的计划镜数会远大于落地镜数（实测 32 / 5）。
    // 只报计划数会让台账显示 32 镜、画布却只有 5 张镜头卡，用户以为数据丢了。
    const root = await fixture();
    const epDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01');
    await mkdir(path.join(epDir, 'shots', '0'), {recursive: true});
    await mkdir(path.join(epDir, 'shots', '1'), {recursive: true});
    await writeFile(path.join(epDir, 'storyboard.json'), JSON.stringify({shots: [{idx: 0}, {idx: 1}, {idx: 2}, {idx: 3}]}));
    // 只有 2 个镜头真正写出了描述
    await writeFile(path.join(epDir, 'shots', '0', 'shot_description.json'), JSON.stringify({idx: 0}));
    await writeFile(path.join(epDir, 'shots', '1', 'shot_description.json'), JSON.stringify({idx: 1}));
    const progress = await recomputeEpisodeProgress(epDir);
    expect(progress.storyboard.shot_count).toBe(4);
    expect(progress.storyboard.described_count).toBe(2);
  });

  it('exposes described_count through the episode list used by canvas/ledger', async () => {
    const root = await fixture();
    const epDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01');
    await mkdir(path.join(epDir, 'shots', '0'), {recursive: true});
    await writeFile(path.join(epDir, 'storyboard.json'), JSON.stringify({shots: [{idx: 0}, {idx: 1}, {idx: 2}]}));
    await writeFile(path.join(epDir, 'shots', '0', 'shot_description.json'), JSON.stringify({idx: 0}));
    await writeFile(path.join(epDir, '.progress.json'), JSON.stringify({status: 'rendered', storyboard: {ready: true, shot_count: 99}}));
    const ledger = await readLedger(root, 'session-1');
    expect(ledger.episodes[0]).toMatchObject({ep_id: 'ep01', described_count: 1, shot_count: 3});
    expect(ledger.storyboard.by_episode.ep01).toMatchObject({described_count: 1, shot_count: 3});
  });

  it('surfaces the script gate report (and flags legacy reports without fails)', async () => {
    const root = await fixture();
    const epDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01');
    await mkdir(epDir, {recursive: true});
    // 旧版报告：缺 fails 键（utils/script_gate 曾漏回传该字段）
    await writeFile(path.join(epDir, 'script_gate_report.json'), JSON.stringify({
      passed: false,
      warns: ['G2 首镜无冲突信号'],
      stats: {shots: 32},
    }));
    // 集级报告（多集项目写在这里）：缺 fails 键 → 标 legacy，界面提示"重跑后可得完整结果"
    const legacy = await readLedger(root, 'session-1');
    expect(legacy.script_gate).toBeNull();
    expect(legacy.storyboard.by_episode.ep01.gate).toMatchObject({
      passed: false,
      legacy: true,
      fails: [],
      warns: ['G2 首镜无冲突信号'],
    });

    // 新版报告：fails 明细可读，且不再标 legacy
    await writeFile(path.join(epDir, 'script_gate_report.json'), JSON.stringify({
      passed: false,
      fails: ['G8 分镜改写/丢失剧本台词'],
      warns: ['G2 首镜无冲突信号'],
      stats: {shots: 32},
    }));
    const fresh = await readLedger(root, 'session-1');
    expect(fresh.storyboard.by_episode.ep01.gate).toMatchObject({
      passed: false,
      legacy: false,
      fails: ['G8 分镜改写/丢失剧本台词'],
    });

    // 根级报告（单集/整剧项目）：直接挂到 ledger.script_gate
    await writeFile(path.join(root, '.working_dir', 'session-1', 'script2video', 'script_gate_report.json'), JSON.stringify({
      passed: true,
      fails: [],
      warns: ['G4 最后一镜缺强钩子'],
      stats: {shots: 8},
    }));
    const rootGate = await readLedger(root, 'session-1');
    expect(rootGate.script_gate).toMatchObject({passed: true, legacy: false, warns: ['G4 最后一镜缺强钩子']});
  });

  it('marks a clip stale when the shot description was edited after rendering', async () => {
    const root = await fixture();
    // 镜头按集读取：必须落在 episodes/<ep>/shots/<idx> 下才会被画布聚合
    const shotDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01', 'shots', '0');
    await mkdir(shotDir, {recursive: true});
    await writeFile(path.join(shotDir, 'shot_description.json'), JSON.stringify({idx: 0, visual_desc: '原画面'}));
    await writeFile(path.join(shotDir, 'video.mp4'), 'clip');
    // 还原真实时序：先有描述（-120s），再渲染出片段（-60s）→ 片段更新，不算过期
    const past = Date.now() / 1000 - 120;
    await utimes(path.join(shotDir, 'shot_description.json'), past, past);
    await utimes(path.join(shotDir, 'video.mp4'), past + 60, past + 60);
    const fresh = await readCanvas(root, 'session-1', 'ep01');
    expect(fresh.shots[0].clip_stale).toBe(false);

    const now = Date.now() / 1000;
    await utimes(path.join(shotDir, 'shot_description.json'), now, now);
    const edited = await readCanvas(root, 'session-1', 'ep01');
    expect(edited.shots[0].clip_stale).toBe(true);
  });

  // 上传件必须有**唯一身份**：同名不同内容不能互相顶替，同一份文件重复上传也不该堆副本。
  // 旧实现同名就生成 `script (2).txt` —— 实测多份同名副本会让 agent 与用户都分不清该读哪份。
  it('gives each distinct content its own stored identity', async () => {
    const root = await fixture();
    const first = await storeWorkspaceUpload(root, 'session-1', 'script.txt', Buffer.from('first'));
    const second = await storeWorkspaceUpload(root, 'session-1', 'script.txt', Buffer.from('second'));
    // 展示名给用户看（原名），路径是唯一身份（内容指纹前缀）
    expect(first.name).toBe('script.txt');
    expect(second.name).toBe('script.txt');
    expect(first.path).toMatch(/^uploads\/[0-9a-f]{12}-script\.txt$/);
    expect(second.path).toMatch(/^uploads\/[0-9a-f]{12}-script\.txt$/);
    expect(first.path).not.toBe(second.path);
    expect(await readFile(path.join(root, '.working_dir', 'session-1', first.path), 'utf8')).toBe('first');
    expect(await readFile(path.join(root, '.working_dir', 'session-1', second.path), 'utf8')).toBe('second');
    expect(first.reused).toBe(false);
    expect(second.reused).toBe(false);
  });

  it('reuses the same identity when the same content is uploaded again', async () => {
    const root = await fixture();
    const first = await storeWorkspaceUpload(root, 'session-1', 'script.txt', Buffer.from('same bytes'));
    const again = await storeWorkspaceUpload(root, 'session-1', 'script.txt', Buffer.from('same bytes'));
    expect(again.path).toBe(first.path);
    expect(again.reused).toBe(true);
    const uploads = await readdir(path.join(root, '.working_dir', 'session-1', 'uploads'));
    expect(uploads).toHaveLength(1);
  });

  it('rejects unsafe upload names', async () => {
    const root = await fixture();
    await expect(storeWorkspaceUpload(root, 'session-1', '../escape.txt', Buffer.from('no'))).rejects.toThrow(/不支持的字符/);
  });

  it('deletes project state, artifacts, and matching log records', async () => {
    const root = await fixture();
    const logPath = path.join(root, '.vimax', 'logs', 'loop_history.jsonl');
    await writeFile(logPath, [
      JSON.stringify({session_id: 'session-1', raw_user_input: 'Delete me'}),
      JSON.stringify({session_id: 'session-2', raw_user_input: 'Keep me'}),
      '',
    ].join('\n'));

    const state = await deleteSession(root, 'session-1');
    expect(state).toEqual({activeSessionId: '', sessions: []});
    await expect(stat(path.join(root, '.working_dir', 'session-1'))).rejects.toThrow();
    expect(await readFile(logPath, 'utf8')).toContain('session-2');
    expect(await readFile(logPath, 'utf8')).not.toContain('session-1');
  });

  it('deletes matching records from rotated log archives', async () => {
    const root = await fixture();
    const logsRoot = path.join(root, '.vimax', 'logs');
    const basePath = path.join(logsRoot, 'loop_history.jsonl');
    const rotatedPath = path.join(logsRoot, 'loop_history.jsonl.1');
    await writeFile(basePath, `${JSON.stringify({session_id: 'session-2', raw_user_input: 'Keep me'})}\n`);
    await writeFile(rotatedPath, `${JSON.stringify({session_id: 'session-1', raw_user_input: 'Delete me'})}\n`);

    await deleteSession(root, 'session-1');

    expect(await readFile(basePath, 'utf8')).toContain('session-2');
    expect(await readFile(rotatedPath, 'utf8')).not.toContain('session-1');
  });

  it('keeps older audio entries available through the per-kind recent list', async () => {
    const root = await fixture();
    const usageDir = path.join(root, '.working_dir', 'session-1', 'usage');
    await mkdir(usageDir, {recursive: true});
    const records = [{
      ts: 1,
      kind: 'audio',
      model: 'voice-model',
      quantity: 1,
      duration: 0,
      resolution: '',
      meta: {purpose: 'character_voice', billing_mode: 'speech_synthesizer', tts_chars: 1250},
    }];
    for (let index = 0; index < 35; index += 1) {
      records.push({
        ts: index + 2,
        kind: 'image',
        model: 'image-model',
        quantity: 1,
        duration: 0,
        resolution: '',
        meta: {purpose: 'keyframe_candidate'},
      });
    }
    await writeFile(path.join(usageDir, 'usage_log.jsonl'), `${records.map((record) => JSON.stringify(record)).join('\n')}\n`);

    const usage = await aggregateSessionUsage(root, 'session-1');
    expect(usage.recent.some((entry) => entry.kind === 'audio')).toBe(false);
    expect(usage.recent_by_kind.audio).toHaveLength(1);
    expect(usage.audio).toMatchObject({count: 1, tts_chars: 1250, billable_count: 1});
    expect(usage.recent_by_kind.audio[0]).toMatchObject({kind: 'audio', model: 'voice-model', tts_chars: 1250});
  });

  it('uses the configured video model for legacy records with an empty model', async () => {
    const root = await fixture();
    const configDir = path.join(root, 'configs');
    const usageDir = path.join(root, '.working_dir', 'session-1', 'usage');
    await mkdir(configDir, {recursive: true});
    await mkdir(usageDir, {recursive: true});
    await writeFile(path.join(configDir, 'agent.local.yaml'), 'video:\n  model: configured-video-model\n');
    await writeFile(path.join(usageDir, 'usage_log.jsonl'), `${JSON.stringify({
      ts: 10,
      kind: 'video',
      model: '',
      quantity: 1,
      duration: 5,
      resolution: '480p',
      meta: {purpose: 'shot_video'},
    })}\n`);

    const usage = await aggregateSessionUsage(root, 'session-1');

    expect(usage.video.count).toBe(1);
    expect(usage.recent[0]).toMatchObject({kind: 'video', model: 'configured-video-model'});
    expect(usage.breakdown.by_model).toMatchObject({'configured-video-model': 1});
  });

  it('maps deployment endpoints to readable model names', async () => {
    const root = await fixture();
    const usageDir = path.join(root, '.working_dir', 'session-1', 'usage');
    await mkdir(usageDir, {recursive: true});
    const records = [
      {ts: 20, kind: 'image', model: 'ep-m-20260911200253-v5snx', quantity: 1, duration: 0, resolution: '', meta: {purpose: 'keyframe_candidate'}},
      {ts: 21, kind: 'video', model: 'ep-m-20260917021019-cfgc7', quantity: 1, duration: 5, resolution: '480p', meta: {purpose: 'shot_video'}},
    ];
    await writeFile(path.join(usageDir, 'usage_log.jsonl'), `${records.map((record) => JSON.stringify(record)).join('\n')}\n`);

    const usage = await aggregateSessionUsage(root, 'session-1');

    expect(usage.recent_by_kind.image[0]).toMatchObject({
      model_name: 'Doubao-Seedream-5.0-pro',
      model_ref: 'ep-m-20260911200253-v5snx',
    });
    expect(usage.recent_by_kind.video[0]).toMatchObject({
      model_name: 'Doubao-Seedance-2.0-mini',
      model_ref: 'ep-m-20260917021019-cfgc7',
    });
    expect(usage.breakdown.by_model).toMatchObject({
      'Doubao-Seedream-5.0-pro': 1,
      'Doubao-Seedance-2.0-mini': 1,
    });
  });
});

describe('production request routing', {timeout: 15_000}, () => {
  const distRoot = path.join(os.tmpdir(), 'vimax-dist-routing');
  const files = new Set([
    path.join(distRoot, 'index.html'),
    path.join(distRoot, 'assets', 'index-abc123.js'),
  ]);
  const fileExists = (filePath) => files.has(filePath);

  it('serves the app shell without caching and hashed assets immutably', () => {
    expect(resolveProductionRequest(distRoot, '/', fileExists)).toMatchObject({
      kind: 'file',
      filePath: path.join(distRoot, 'index.html'),
      cacheControl: 'no-cache, no-store, must-revalidate',
    });
    expect(resolveProductionRequest(distRoot, '/assets/index-abc123.js', fileExists)).toMatchObject({
      kind: 'file',
      cacheControl: 'public, max-age=31536000, immutable',
    });
  });

  it('falls back to the app shell for real SPA routes without caching', () => {
    expect(resolveProductionRequest(distRoot, '/projects/session-1', fileExists)).toMatchObject({
      kind: 'spa',
      filePath: path.join(distRoot, 'index.html'),
      cacheControl: 'no-cache, no-store, must-revalidate',
    });
  });

  it('rejects stale development and missing asset paths instead of returning HTML', () => {
    for (const pathname of ['/src/main.tsx', '/@vite/client', '/@react-refresh', '/missing.js', '/assets/missing.css']) {
      expect(resolveProductionRequest(distRoot, pathname, fileExists)).toMatchObject({
        kind: 'missing',
        filePath: '',
        cacheControl: 'no-store',
      });
    }
  });

  it('maps a removed old entry bundle to the current build asset', () => {
    const html = '<script type="module" crossorigin src="/assets/index-new123.js"></script><link rel="stylesheet" href="/assets/index-new456.css">';
    expect(resolveProductionEntryAsset(html, '/assets/index-old999.js')).toBe('/assets/index-new123.js');
    expect(resolveProductionEntryAsset(html, '/assets/index-old999.css')).toBe('/assets/index-new456.css');
    expect(resolveProductionEntryAsset(html, '/assets/vendor-old999.js')).toBe('');
    expect(resolveProductionEntryAsset(html, '/assets/index-old999.png')).toBe('');
  });
});

describe('budget ledger aggregation', {timeout: 15_000}, () => {
  async function budgetFixture() {
    const root = await fixture();
    const sessionRoot = path.join(root, '.working_dir', 'session-1');
    const usageDir = path.join(sessionRoot, 'script2video', 'usage', '2026-10');
    await mkdir(usageDir, {recursive: true});
    await writeFile(path.join(usageDir, 'usage_log.jsonl'), [
      JSON.stringify({ts: '2026-10-09T20:00:00', kind: 'image', model: 'seedream', quantity: 4, meta: {}}),
      JSON.stringify({ts: '2026-10-09T20:01:00', kind: 'image', model: 'seedream', quantity: 32, meta: {}}),
      JSON.stringify({ts: '2026-10-09T20:02:00', kind: 'video', model: 'seedance', quantity: 1, meta: {duration_sec: 5}}),
      JSON.stringify({ts: '2026-10-09T20:03:00', kind: 'audio', model: 'voice', quantity: 4, meta: {tts_chars: 120}}),
    ].join('\n'));
    await writeFile(path.join(root, '.vimax', 'logs', 'metrics.jsonl'), [
      JSON.stringify({session_id: 'session-1', total_tokens: 1234}),
      JSON.stringify({session_id: 'other', total_tokens: 99999}),
    ].join('\n'));
    return {root, sessionRoot};
  }

  it('aggregates usage log by kind, quantity, duration and tts chars', async () => {
    const {sessionRoot} = await budgetFixture();
    const agg = await aggregateUsageLog(sessionRoot);
    expect(agg).toMatchObject({
      image_count: 36,
      video_count: 1,
      video_seconds: 5,
      audio_count: 4,
      audio_chars: 120,
    });
  });

  it('readLedger reports live budget usage even when ledger file is all zeros', async () => {
    const {root, sessionRoot} = await budgetFixture();
    // 未配置预算策略时：持久化 used 恒 0，权威口径是实时聚合流水
    await writeFile(path.join(sessionRoot, 'budget_ledger.json'), JSON.stringify({
      version: 1,
      used: {image_calls: 0, video_calls: 0, video_seconds: 0, audio_calls: 0, audio_chars: 0, llm_tokens: 0},
      pending: {},
      updated_at: '2026-10-09T20:42:36',
    }));
    const ledger = await readLedger(root, 'session-1');
    expect(ledger.budget).toMatchObject({
      used: {image_calls: 36, video_calls: 1, video_seconds: 5, audio_calls: 4, audio_chars: 120, llm_tokens: 1234},
      configured: false,
    });
  });

  it('readLedger merges persisted used when no usage log exists', async () => {
    const root = await fixture();
    const sessionRoot = path.join(root, '.working_dir', 'session-1');
    await writeFile(path.join(sessionRoot, 'budget_ledger.json'), JSON.stringify({
      version: 1,
      used: {image_calls: 7, video_calls: 2, video_seconds: 10, audio_calls: 3, audio_chars: 60, llm_tokens: 500},
      pending: {},
      updated_at: '2026-10-09T20:42:36',
      strategy: {image_budget: 100},
    }));
    const ledger = await readLedger(root, 'session-1');
    expect(ledger.budget).toMatchObject({
      used: {image_calls: 7, video_calls: 2, video_seconds: 10, audio_calls: 3, audio_chars: 60, llm_tokens: 0},
      configured: true,
    });
  });
});

describe('artifact byte ranges', {timeout: 15_000}, () => {
  it('parses bounded, open-ended and suffix ranges', () => {
    expect(parseByteRange('bytes=10-19', 100)).toEqual({start: 10, end: 19, length: 10});
    expect(parseByteRange('bytes=90-', 100)).toEqual({start: 90, end: 99, length: 10});
    expect(parseByteRange('bytes=-10', 100)).toEqual({start: 90, end: 99, length: 10});
  });

  it('rejects malformed or out-of-bounds ranges', () => {
    expect(parseByteRange('bytes=100-120', 100)).toBeNull();
    expect(parseByteRange('bytes=20-10', 100)).toBeNull();
    expect(parseByteRange('items=0-10', 100)).toBeNull();
  });
});

describe('runtime config persistence', {timeout: 15_000}, () => {
  it('preserves unmanaged runtime keys when toggling autonomous mode', async () => {
    // 回归：前端拨「创作模式」开关曾整文件重写，会把 feed_prompt_char_budget 等
    // 其他活配静默清空（活配形同虚设）。
    const {readRuntimeConfig, writeRuntimeConfig} = await import('./server-lib.mjs');
    const root = await fixture();
    await mkdir(path.join(root, 'configs'), {recursive: true});
    const cfgPath = path.join(root, 'configs', 'runtime.local.json');
    await writeFile(cfgPath, JSON.stringify({
      autonomous_mode: false,
      feed_prompt_char_budget: {'seedance2.5': [2600, 3200]},
    }));

    const saved = await writeRuntimeConfig(root, {autonomous_mode: true});

    expect(saved.autonomous_mode).toBe(true);
    expect(saved.feed_prompt_char_budget).toEqual({'seedance2.5': [2600, 3200]});
    const onDisk = JSON.parse(await readFile(cfgPath, 'utf8'));
    expect(onDisk.feed_prompt_char_budget).toEqual({'seedance2.5': [2600, 3200]});
    expect((await readRuntimeConfig(root)).autonomous_mode).toBe(true);
  });

  it('rebuilds from scratch when the file is missing or corrupt', async () => {
    const {writeRuntimeConfig} = await import('./server-lib.mjs');
    const root = await fixture();
    await mkdir(path.join(root, 'configs'), {recursive: true});
    const cfgPath = path.join(root, 'configs', 'runtime.local.json');
    await writeFile(cfgPath, '{ not json');

    const saved = await writeRuntimeConfig(root, {autonomous_mode: true});

    expect(saved).toEqual({autonomous_mode: true});
    expect(JSON.parse(await readFile(cfgPath, 'utf8'))).toEqual({autonomous_mode: true});
  });
});

describe('episode render completeness', {timeout: 15_000}, () => {
  async function partialEpisodeFixture({declared = 14, rendered = 7} = {}) {
    const root = await fixture();
    const epDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01');
    await mkdir(path.join(epDir, 'segments'), {recursive: true});
    await mkdir(path.join(epDir, 'shots'), {recursive: true});
    await writeFile(path.join(epDir, 'script.txt'), '第1集 测试');
    await writeFile(path.join(epDir, 'storyboard.json'), JSON.stringify({
      shots: Array.from({length: declared * 2}, (_, i) => ({idx: i, segment_id: Math.floor(i / 2)})),
    }));
    await writeFile(path.join(epDir, 'segments.json'), JSON.stringify(
      Array.from({length: declared}, (_, i) => ({segment_id: i, shot_idxs: [i * 2, i * 2 + 1]})),
    ));
    for (let i = 0; i < rendered; i += 1) {
      await mkdir(path.join(epDir, 'segments', `seg-${i}`), {recursive: true});
      await writeFile(path.join(epDir, 'segments', `seg-${i}`, 'video.mp4'), 'vid');
    }
    await writeFile(path.join(epDir, 'final_video.mp4'), 'final');
    return {root, epDir};
  }

  it('flags a truncated film (7/14 segments) as incomplete', async () => {
    // 实测事故：segments.json 14 段、盘上只有 seg-0..seg-6，final_video 却在盘，
    // 只判"成片文件在不在"就会把整集报成已完成。
    const {epDir} = await partialEpisodeFixture();
    const coverage = await episodeRenderCoverage(epDir);

    expect(coverage.mode).toBe('segment');
    expect(coverage.expected).toBe(14);
    expect(coverage.rendered).toBe(7);
    expect(coverage.missing_segment_ids).toEqual([7, 8, 9, 10, 11, 12, 13]);
    expect(coverage.final_video).toBe(true);
    expect(coverage.complete).toBe(false);
    expect(coverageGapMessage(coverage, 'ep01')).toContain('seg-7');
  });

  it('treats zero-byte clips as missing', async () => {
    const {epDir} = await partialEpisodeFixture({declared: 3, rendered: 3});
    await writeFile(path.join(epDir, 'segments', 'seg-1', 'video.mp4'), '');
    const coverage = await episodeRenderCoverage(epDir);
    expect(coverage.missing_segment_ids).toEqual([1]);
    expect(coverage.complete).toBe(false);
  });

  it('passes only when every declared segment is rendered', async () => {
    const {epDir} = await partialEpisodeFixture({declared: 3, rendered: 3});
    const coverage = await episodeRenderCoverage(epDir);
    expect(coverage.complete).toBe(true);
    expect(coverageGapMessage(coverage, 'ep01')).not.toContain('残缺成片');
  });

  it('writes partial_rendered instead of complete for a truncated film', async () => {
    const {epDir} = await partialEpisodeFixture();
    const progress = await recomputeEpisodeProgress(epDir);

    expect(progress.final_video.ready).toBe(true);
    expect(progress.final_video.complete).toBe(false);
    expect(progress.final_video.partial).toBe(true);
    expect(progress.status).toBe('partial_rendered');
    expect(progress.render_coverage.rendered).toBe(7);
    expect(progress.render_gap).toContain('seg-7');
  });

  it('self-heals a stale "complete" record on read', async () => {
    const {root, epDir} = await partialEpisodeFixture();
    await writeFile(path.join(epDir, '.progress.json'), JSON.stringify({
      status: 'complete',
      storyboard: {ready: true, shot_count: 28, described_count: 28},
      final_video: {ready: true},
    }));
    const episodes = await readEpisodeProgress(root, 'session-1');
    const ep = episodes.find((item) => item.ep_id === 'ep01');
    expect(ep.status).toBe('partial_rendered');
    expect(ep.final_ready).toBe(false);
    expect(ep.partial_render).toBe(true);
    expect(ep.segments_rendered).toBe(7);
    expect(ep.segments_total).toBe(14);
  });
});

describe('per-episode asset status readback', {timeout: 15_000}, () => {
  async function episodeWithAssetStatus(assetStatus) {
    const root = await fixture();
    const epDir = path.join(root, '.working_dir', 'session-1', 'script2video', 'episodes', 'ep01');
    await mkdir(epDir, {recursive: true});
    await writeFile(path.join(epDir, 'script.txt'), '第1集 测试');
    if (assetStatus !== undefined) {
      await writeFile(path.join(epDir, 'asset_status.json'), JSON.stringify(assetStatus));
    }
    return {root, epDir};
  }

  it('exposes the Python-written asset_status.json to the web layer', async () => {
    // 判定唯一在 Python（_write_episode_asset_status）；JS 只读不算，避免第二套口径
    const {root} = await episodeWithAssetStatus({
      ep_id: 'ep01',
      ready: false,
      blocking: ['赵铁柱', '铁西厂大门口'],
      missing: {characters: ['赵铁柱'], variants: [], scenes: ['铁西厂大门口'], props: [], voices: []},
      missing_count: 2,
      updated_at: '2026-10-16T10:00:00',
    });
    const episodes = await readEpisodeProgress(root, 'session-1');
    const ep = episodes.find((item) => item.ep_id === 'ep01');
    expect(ep.assets_ready).toBe(false);
    expect(ep.assets_blocking).toEqual(['赵铁柱', '铁西厂大门口']);
    expect(ep.assets_missing_count).toBe(2);
    expect(ep.assets_missing.characters).toEqual(['赵铁柱']);
    expect(ep.assets_updated_at).toBe('2026-10-16T10:00:00');
  });

  it('reports unknown (null) rather than guessing when the file is absent', async () => {
    const {root} = await episodeWithAssetStatus(undefined);
    const episodes = await readEpisodeProgress(root, 'session-1');
    const ep = episodes.find((item) => item.ep_id === 'ep01');
    expect(ep.assets_ready).toBeNull();
    expect(ep.assets_missing).toBeNull();
    expect(ep.assets_missing_count).toBeNull();
    expect(ep.assets_blocking).toEqual([]);
  });

  it('marks a ready episode as assets_ready true', async () => {
    const {root} = await episodeWithAssetStatus({
      ep_id: 'ep01',
      ready: true,
      blocking: [],
      missing: {characters: [], variants: [], scenes: [], props: ['旧怀表'], voices: ['赵铁柱']},
      missing_count: 2,
      updated_at: '2026-10-16T10:05:00',
    });
    const episodes = await readEpisodeProgress(root, 'session-1');
    const ep = episodes.find((item) => item.ep_id === 'ep01');
    expect(ep.assets_ready).toBe(true);
    expect(ep.assets_missing.props).toEqual(['旧怀表']);
  });
});

describe('workspace upload reference guard', {timeout: 15_000}, () => {
  async function uploadFixture() {
    const root = await fixture();
    const uploadRoot = path.join(root, '.working_dir', 'session-1', 'uploads');
    await mkdir(uploadRoot, {recursive: true});
    await writeFile(path.join(uploadRoot, '剧本.md'), '# 第1集\n');
    return {root, uploadRoot};
  }

  it('parses the trailing workspace_uploads block', () => {
    expect(extractWorkspaceUploadPaths('开始吧 <workspace_uploads>["uploads/a.md","uploads/b.md"]</workspace_uploads>'))
      .toEqual(['uploads/a.md', 'uploads/b.md']);
    expect(extractWorkspaceUploadPaths('没有附件')).toEqual([]);
    expect(extractWorkspaceUploadPaths('坏的 <workspace_uploads>not-json</workspace_uploads>')).toEqual([]);
  });

  it('accepts an upload that exists in this session', async () => {
    const {root} = await uploadFixture();
    expect(await missingWorkspaceUploads(root, 'session-1', ['uploads/剧本.md'])).toEqual([]);
  });

  // 实测事故：新建项目后上传，文件进了上一个项目 → 消息里的相对路径在本项目读不到，
  // agent 烧一轮对话才报 File not found。这里必须在派发前就判出来。
  it('flags an upload that lives in another project', async () => {
    const {root} = await uploadFixture();
    expect(await missingWorkspaceUploads(root, 'session-1', ['uploads/别的项目.md'])).toEqual(['uploads/别的项目.md']);
  });

  it('rejects non-upload references and path escapes', async () => {
    const {root} = await uploadFixture();
    const missing = await missingWorkspaceUploads(root, 'session-1', [
      'script2video/script.txt',
      'uploads/../../.vimax/sessions.json',
    ]);
    expect(missing).toEqual(['script2video/script.txt', 'uploads/../../.vimax/sessions.json']);
  });

  it('treats a zero-byte upload as unusable', async () => {
    const {root, uploadRoot} = await uploadFixture();
    await writeFile(path.join(uploadRoot, '空.md'), '');
    expect(await missingWorkspaceUploads(root, 'session-1', ['uploads/空.md'])).toEqual(['uploads/空.md']);
  });
});

describe('new-session claim rule', () => {
  // 实测（2026-10-16）：清单只抽了 3/80，状态库 stage 却是 assets_manifest_ready →
  // 步骤条显示"生成角色中"，而 agent 实际在做"按集提取"。现在以 Python 写的
  // production_state.json 为准（JS 只读，不再自己判"完成没完成"）。
  it('session stage follows the production-state snapshot over a lying db stage', async () => {
    const root = await fixture();
    const s2v = path.join(root, '.working_dir', 'session-1', 'script2video');
    await mkdir(s2v, {recursive: true});
    await writeFile(path.join(s2v, 'production_state.json'), JSON.stringify({
      next_step: 'extract',
      stage: 'assets_manifest_partial',
      blockers: ['资产清单还差 7 集（ep04）'],
      manifest_pending: ['ep04', 'ep05'],
    }));
    const state = await readSessionState(root);
    const session = state.sessions.find((item) => item.sessionId === 'session-1');
    expect(session.stage).toBe('assets_manifest_partial');
  });

  it('falls back to the db-derived stage when there is no snapshot', async () => {
    const root = await fixture();
    const state = await readSessionState(root);
    const session = state.sessions.find((item) => item.sessionId === 'session-1');
    expect(session.stage).toBe('rendering');   // fixture 里的库内 stage
  });

  // 台账"项目"卡必须显示项目名；根剧本首行只做兜底（单集脚本被误写进根文件时，
  // 首行会变成"第 26 集：立案"，整个项目看起来变成了第 26 集）。
  it('ledger exposes the project name for the project card', async () => {
    const root = await fixture();
    const ledger = await readLedger(root, 'session-1');
    expect(ledger.meta.project_name).toBe('Ocean campaign');
  });

  // 实测（2026-10-17）：ep01 的剧本以剧名开头 → 首行会让集名显示成《铁西硬汉》剧本全集。
  // 集标题的唯一口径是 Python `_parse_episode_title` 写进清单的 title。
  it('episode title comes from the manifest, not the script first line', async () => {
    const root = await fixture();
    const s2v = path.join(root, '.working_dir', 'session-1', 'script2video');
    const epDir = path.join(s2v, 'episodes', 'ep01');
    await mkdir(epDir, {recursive: true});
    await writeFile(path.join(epDir, 'script.txt'), '《铁西硬汉》剧本全集\n\n第 1 集： 三千块，买断一条命\n');
    await writeFile(path.join(s2v, 'assets_manifest.json'), JSON.stringify({
      episodes: [{ep_id: 'ep01', title: '三千块，买断一条命'}],
    }));
    const rows = await readEpisodeProgress(root, 'session-1');
    expect(rows.find((row) => row.ep_id === 'ep01').title).toBe('三千块，买断一条命');
  });

  // 注意：sessions.json 的 created_at / 会话 id 都是**本地时间**字符串（无时区后缀），
  // server.mjs 传进来的 record.startedAtMs 是 Date.now()（同一基准），所以这里也必须用本地时间，
  // 写 'Z' 会差一个时区（实测踩过：用例里混用 UTC 导致会话被判为"创建于 spawn 之前"）。
  const spawnedAt = Date.parse('2026-10-16T20:47:44');
  const sessions = [
    {sessionId: '20261015-133416-vimax', createdAt: '2026-10-15T13:34:16'},
    {sessionId: '20261016-204746-vimax', createdAt: '2026-10-16T20:47:46'},
  ];

  it('claims the session created by this spawn', () => {
    expect(pickNewSessionId(sessions, {spawnedAtMs: spawnedAt})).toBe('20261016-204746-vimax');
  });

  // 实测事故：settleAgentSession 直接采用 sessions.json 的全局 active_session_id，
  // 而那个值在别的项目 agent 刚启动时指向的是**别人的**项目 —— 采用就会把本进程注册到
  // 别人的 key 上（消息串台），也会把"新建项目"算成"旧项目在跑"。
  it('never adopts a session created before this spawn', () => {
    const older = [sessions[0]];
    expect(pickNewSessionId(older, {spawnedAtMs: spawnedAt})).toBe('');
  });

  it('never adopts a session already occupied by another agent', () => {
    expect(pickNewSessionId(sessions, {spawnedAtMs: spawnedAt, occupied: new Set(['20261016-204746-vimax'])})).toBe('');
  });

  it('ignores entries without a parseable createdAt', () => {
    expect(pickNewSessionId([{sessionId: 'weird'}], {spawnedAtMs: spawnedAt})).toBe('');
    expect(pickNewSessionId(null, {spawnedAtMs: spawnedAt})).toBe('');
  });

  it('prefers the earliest eligible candidate when several are eligible', () => {
    const many = [...sessions, {sessionId: '20261016-204800-vimax', createdAt: '2026-10-16T20:48:00'}];
    expect(pickNewSessionId(many, {spawnedAtMs: spawnedAt})).toBe('20261016-204746-vimax');
  });
});
