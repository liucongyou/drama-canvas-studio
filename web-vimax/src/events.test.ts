import {describe, expect, it} from 'vitest';
import {applyAgentEvent, appendLocalUser, attachMediaByCreatedTime, composeAgentPrompt, createChatState, humanize, isRetryableToolFailure, localizeAgentText, mergeHistoryMessages, parseReplyOptions, partitionUploadsForSession, replyOptionsForMessage, resolveWorkflowStepIndex, WORKFLOW_STEPS, workflowStepIndexForStage} from './events';
import type {Artifact, Message} from './types';

describe('agent event mapping', () => {
  it('streams assistant text into one message', () => {
    let state = appendLocalUser(createChatState(), 'Make a short film');
    state = applyAgentEvent(state, {type: 'turn', turn_id: 'turn-1'});
    expect(state.messages[0]).toMatchObject({id: 'turn-1-user', role: 'user', text: 'Make a short film'});
    state = applyAgentEvent(state, {type: 'token', turn_id: 'turn-1', delta: 'Planning'});
    expect(state.messages.at(-1)).toMatchObject({id: 'streaming-turn-1', streaming: true});
    state = applyAgentEvent(state, {type: 'token', turn_id: 'turn-1', delta: ' ready'});
    state = applyAgentEvent(state, {type: 'done', turn_id: 'turn-1'});
    expect(state.messages.at(-1)).toMatchObject({id: 'turn-1-assistant', role: 'assistant', text: 'Planning ready', streaming: false});
    expect(state.busy).toBe(false);
    const merged = mergeHistoryMessages(state.messages, [
      {id: 'turn-1-user', role: 'user', text: 'Make a short film'},
      {id: 'turn-1-assistant', role: 'assistant', text: 'Planning ready'},
    ]);
    expect(merged.filter((message) => message.role === 'user')).toHaveLength(1);
  });

  it('keeps tool-round commentary separate from the final streamed answer', () => {
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-2'});
    state = applyAgentEvent(state, {type: 'token', turn_id: 'turn-2', round: 0, delta: '先检查项目状态'});
    state = applyAgentEvent(state, {type: 'tool_start', turn_id: 'turn-2', tool: {id: 'tool-1', name: 'read_file'}});
    state = applyAgentEvent(state, {type: 'tool_result', turn_id: 'turn-2', tool_result: {name: 'read_file', ok: true}});
    state = applyAgentEvent(state, {type: 'token', turn_id: 'turn-2', round: 1, delta: '最终答案'});
    state = applyAgentEvent(state, {type: 'done', turn_id: 'turn-2', assistant: '最终答案'});

    expect(state.messages).toHaveLength(3);
    expect(state.messages[0]).toMatchObject({id: 'turn-2-commentary-0', kind: 'commentary', text: '先检查项目状态'});
    expect(state.messages[1]).toMatchObject({role: 'activity', tool: 'read_file', status: 'done', text: '已完成'});
    expect(state.messages[2]).toMatchObject({id: 'turn-2-assistant', kind: 'answer', text: '最终答案'});
  });

  it('turns backend phase events into readable live status text', () => {
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-3'});
    state = applyAgentEvent(state, {type: 'status', turn_id: 'turn-3', phase: 'sampling_assistant', message: 'Sampling assistant'});
    expect(state.statusText).toBe('正在生成回复…');
    state = applyAgentEvent(state, {type: 'status', turn_id: 'turn-3', phase: 'executing_tools', message: 'Running tools (round 1)'});
    expect(state.statusText).toBe('正在执行工具并读取结果…');
  });

  it('preserves partial output when a live answer is cancelled', () => {
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-4'});
    state = applyAgentEvent(state, {type: 'token', turn_id: 'turn-4', delta: '已经开始输出的内容'});
    state = applyAgentEvent(state, {type: 'status', turn_id: 'turn-4', phase: 'cancelled', message: '任务已停止'});
    state = applyAgentEvent(state, {type: 'token', turn_id: 'turn-4', delta: '任务已由用户取消。'});
    state = applyAgentEvent(state, {type: 'done', turn_id: 'turn-4', assistant: '任务已由用户取消。'});

    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({kind: 'commentary', text: '已经开始输出的内容', streaming: false});
    expect(state.messages[1]).toMatchObject({id: 'turn-4-assistant', kind: 'answer', text: '任务已由用户取消。'});
  });

  it('does not duplicate the same failure as both error and assistant output', () => {
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-5'});
    state = applyAgentEvent(state, {type: 'error', turn_id: 'turn-5', message: '回合执行失败：网络超时'});
    state = applyAgentEvent(state, {type: 'done', turn_id: 'turn-5', assistant: '回合执行失败：网络超时'});

    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({role: 'error', text: '回合执行失败：网络超时'});
  });

  it('closes a running tool when a cancelled turn finishes', () => {
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-6'});
    state = applyAgentEvent(state, {type: 'tool_start', turn_id: 'turn-6', tool: {id: 'tool-running', name: 'vimax_render_video'}});
    state = applyAgentEvent(state, {type: 'done', turn_id: 'turn-6', status: 'cancelled', assistant: '任务已由用户取消。'});

    const tool = state.messages.find((message) => message.role === 'activity');
    expect(tool).toMatchObject({status: 'error', stage: 'interrupted', text: '任务已停止，此步骤未完成'});
    expect(state.messages.at(-1)).toMatchObject({role: 'assistant', text: '任务已由用户取消。'});
  });

  it('updates a running tool instead of appending every progress event', () => {
    let state = createChatState();
    state = applyAgentEvent(state, {type: 'tool_start', turn_id: 'turn-1', tool: {id: 'tool-1', name: 'vimax_render_video'}});
    state = applyAgentEvent(state, {type: 'tool_start', turn_id: 'turn-1', tool: {id: 'tool-1', name: 'vimax_render_video'}});
    state = applyAgentEvent(state, {type: 'tool_progress', turn_id: 'turn-1', tool: {id: 'tool-1', name: 'vimax_render_video'}, progress: {stage: 'generate_frames', message: 'Generating frames'}});
    state = applyAgentEvent(state, {type: 'tool_result', turn_id: 'turn-1', tool_result: {name: 'vimax_render_video', ok: true, metadata: {call_id: 'tool-1'}}});
    state = applyAgentEvent(state, {type: 'tool_result', turn_id: 'turn-1', tool_result: {name: 'vimax_render_video', ok: true, metadata: {call_id: 'tool-1'}}});
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({id: 'turn-1-tool-tool-1', tool: 'vimax_render_video', status: 'done', text: '已完成'});
  });

  it('merges an older history page without duplicating overlapping messages', () => {
    const existing = [
      {id: 'turn-2-user', role: 'user' as const, text: 'second'},
      {id: 'turn-2-assistant', role: 'assistant' as const, text: 'answer'},
    ];
    const older = [
      {id: 'turn-1-user', role: 'user' as const, text: 'first'},
      {id: 'turn-1-assistant', role: 'assistant' as const, text: 'first answer'},
      {id: 'turn-2-user', role: 'user' as const, text: 'second'},
    ];
    expect(mergeHistoryMessages(older, existing).map((message) => message.id)).toEqual([
      'turn-1-user', 'turn-1-assistant', 'turn-2-user', 'turn-2-assistant',
    ]);
  });

  it('keeps each composer submission on one stdin line', () => {
    expect(composeAgentPrompt('first line\nsecond line')).toBe('first line second line');
    expect(composeAgentPrompt('Use these references', ['uploads/script.txt', 'uploads/look.png']))
      .toBe('Use these references <workspace_uploads>["uploads/script.txt","uploads/look.png"]</workspace_uploads>');
    expect(humanize('vimax_narrative_planning')).toBe('映界 Narrative Planning');
  });

  // 实测事故：新建项目后立刻上传剧本，文件落进了上一个项目的 uploads/，消息却发到新项目，
  // `<workspace_uploads>` 里的相对路径在新项目读不到 → agent 只能报 File not found，白烧一轮。
  it('上传件按目标项目分流：属于别的项目的不进本次发送', () => {
    const uploads = [
      {name: '本项目的.md', path: 'uploads/a.md', size: 1, sessionId: 's-new'},
      {name: '上个项目的.md', path: 'uploads/b.md', size: 2, sessionId: 's-old'},
    ];
    const {forSession, foreign} = partitionUploadsForSession(uploads, 's-new');
    expect(forSession.map((item) => item.path)).toEqual(['uploads/a.md']);
    expect(foreign.map((item) => item.name)).toEqual(['上个项目的.md']);
  });

  it('没有 sessionId 的历史上传件按当前项目处理，不误伤', () => {
    const uploads = [{name: 'legacy.md', path: 'uploads/legacy.md', size: 1}];
    const {forSession, foreign} = partitionUploadsForSession(uploads, 's-new');
    expect(forSession).toHaveLength(1);
    expect(foreign).toEqual([]);
  });

  it('同项目上传件全部保留，顺序不变', () => {
    const uploads = [
      {name: 'a.md', path: 'uploads/a.md', size: 1, sessionId: 's1'},
      {name: 'b.md', path: 'uploads/b.md', size: 2, sessionId: 's1'},
    ];
    expect(partitionUploadsForSession(uploads, 's1')).toEqual({forSession: uploads, foreign: []});
  });

  // 实测：agent 去读仓库根的 configs/ 被判 Path not found，用户连点两轮「重试步骤」都在撞同一面墙。
  it('确定性失败（路径不存在/越界）不再提供原样重试', () => {
    expect(isRetryableToolFailure('Path not found: D:\\x\\.working_dir\\s\\configs')).toBe(false);
    expect(isRetryableToolFailure('File not found: D:\\x\\.working_dir\\s\\uploads\\a.md')).toBe(false);
    expect(isRetryableToolFailure('Path escapes the active project workspace: ../other')).toBe(false);
    expect(isRetryableToolFailure('这个文件不在当前项目里（可能上传到了其它项目）')).toBe(false);
  });

  it('普通失败照旧可重试', () => {
    expect(isRetryableToolFailure('HTTP 502: upstream error')).toBe(true);
    expect(isRetryableToolFailure('429 too many requests')).toBe(true);
    expect(isRetryableToolFailure('')).toBe(true);
  });

  // 实测（2026-10-16 用户截图）：资产清单只抽到 3/80 集，状态库却把项目当成进了资产阶段、
  // progressStage 给出 chars → 步骤条显示"生成角色中"，而此刻正在跑的是"按集提取场景与道具"。
  it('正在提取清单时，步骤条不许显示"生成角色"', () => {
    const extractStep = WORKFLOW_STEPS.findIndex((step) => step.id === 'extract');
    const messages = [
      {id: 'a1', role: 'activity' as const, text: '正在按集提取场景与道具视觉设定（指纹增量）', stage: 'extract_scene_props', status: 'running' as const},
    ];
    expect(resolveWorkflowStepIndex(messages, 'assets_pending', 'chars')).toBe(extractStep);
  });

  it('提取跑完后仍按资产进度显示（chars/scenes/…）', () => {
    const charsStep = WORKFLOW_STEPS.findIndex((step) => step.id === 'chars');
    const messages = [
      {id: 'a1', role: 'activity' as const, text: '正在按集提取场景与道具视觉设定', stage: 'extract_scene_props', status: 'done' as const},
      {id: 'a2', role: 'activity' as const, text: '正在生成角色立绘', stage: 'assets_start', status: 'running' as const},
    ];
    expect(resolveWorkflowStepIndex(messages, 'assets_pending', 'chars')).toBe(charsStep);
  });

  it('没有进行中的工具时以资产进度/会话 stage 为准', () => {
    const scenesStep = WORKFLOW_STEPS.findIndex((step) => step.id === 'scenes');
    expect(resolveWorkflowStepIndex([], 'assets_pending', 'scenes')).toBe(scenesStep);
    const storyboardStep = WORKFLOW_STEPS.findIndex((step) => step.id === 'storyboard');
    expect(resolveWorkflowStepIndex([], 'storyboard_ready')).toBe(storyboardStep);
  });

  it('localizes provider rate-limit errors for display', () => {
    const localized = localizeAgentText('Agent LLM request failed: Error code: 429 - {"error": {"message": "Too many requests. Your current concurrency is 6, which exceeds your concurrency limit of 5 based on your remaining balance. Please top up your balance to restore your concurrency. (request_id: req-123)", "type": "rate_limit_error"}}');
    expect(localized).toContain('请求过于频繁');
    expect(localized).toContain('当前并发数为 6');
    expect(localized).toContain('并发上限 5');
    expect(localized).toContain('req-123');
    expect(localized).not.toContain('Too many requests');
    expect(localizeAgentText('精确重试完成：memory_read')).toBe('精确重试完成：记忆');
    expect(localizeAgentText('你好，我是 映界 Agent')).toBe('你好，我是 映界 智能体');
    const wrapped = localizeAgentText('叙事规划失败：failed: RetryError[<Future state=finished raised RateLimitError>]');
    expect(wrapped).toContain('请求过于频繁');
    expect(wrapped).not.toContain('RateLimitError');
  });

  it('keeps vimax unchanged inside artifact URLs and workspace paths', () => {
    const raw = [
      '使用 vimax 生成资产',
      '![周稳](/api/artifact?session=20261008-210553-vimax&path=script2video/character_portraits/0_周稳/sheet.png)',
      'C:\\项目\\ViMax-main\\web',
    ].join('\n');
    const localized = localizeAgentText(raw);
    expect(localized).toContain('使用 映界 生成资产');
    expect(localized).toContain('session=20261008-210553-vimax');
    expect(localized).toContain('ViMax-main');
  });

  it('localizes provider account and generation errors for display', () => {
    const raw = '渲染失败：All 2 image candidates failed: Volcano Ark image generation failed with HTTP 403: {error: {code: \'AccountOverdueError\', message: \'The request failed because your account has an overdue balance. Request id: 021791255932161f1aca4493aa6c7087a6ccfbfdb4be6\', param: \'\', type: \'\'}}';
    const localized = localizeAgentText(raw);
    expect(localized).toContain('2 张候选画面全部生成失败');
    expect(localized).toContain('火山方舟返回账户欠费状态');
    expect(localized).toContain('HTTP 403');
    expect(localized).toContain('错误类型：账户欠费');
    expect(localized).toContain('请求编号：021791255932161f1aca4493aa6c7087a6ccfbfdb4be6');
    expect(localized).not.toContain('image candidates failed');
    expect(localized).not.toContain('overdue balance');
    expect(localized).not.toContain('AccountOverdueError');
  });

  it('ends running tools when the agent process stops', () => {
    let state = applyAgentEvent(createChatState(), {
      type: 'tool_start',
      turn_id: 'turn-1',
      tool: {id: 'tool-1', name: 'vimax_render_video'},
    });
    state = applyAgentEvent(state, {
      type: 'bridge_status',
      status: 'stopped',
      message: 'Configuration updated',
    });
    expect(state.busy).toBe(false);
    expect(state.messages[0]).toMatchObject({
      tool: 'vimax_render_video',
      status: 'error',
      stage: 'interrupted',
      text: 'Configuration updated',
    });
  });

  it('keeps the workflow plan across the full turn and restores it from a session event', () => {
    const plan = {
      title: '生成分镜与镜头方案',
      stage: 'storyboard',
      current_step: 'storyboard',
      next_step: '生成当前集分镜',
      status: 'ready',
      steps: [{id: 'storyboard', label: '生成分镜', status: 'active'}],
    };
    let state = applyAgentEvent(createChatState(), {type: 'workflow_plan', turn_id: 'turn-1', workflow_plan: plan});
    state = applyAgentEvent(state, {type: 'done', turn_id: 'turn-1', assistant: '等待确认'});
    expect(state.busy).toBe(false);
    expect(state.workflowPlan).toEqual(plan);

    state = applyAgentEvent(state, {
      type: 'session',
      session: {active_session_id: 's1', session: {session_id: 's1', workflow_plan: {...plan, status: 'cancelled'}}},
    });
    expect(state.workflowPlan?.status).toBe('cancelled');
  });

  it('surfaces end-to-end autonomy advance / pause events as activity lines', () => {
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-auto'});
    state = applyAgentEvent(state, {
      type: 'autonomy', turn_id: 'turn-auto',
      autonomy: {action: 'advance', from: 'extract_assets', to: 'generate_assets', to_label: '生成资产', message: '自主推进：提取资产 → 生成资产'},
    });
    expect(state.statusText).toBe('自主推进：提取资产 → 生成资产');
    expect(state.messages.at(-1)).toMatchObject({role: 'activity', tool: 'autonomy', status: 'done'});
    expect(state.messages.at(-1)?.text).toContain('🤖 自主推进：提取资产 → 生成资产');

    state = applyAgentEvent(state, {
      type: 'autonomy', turn_id: 'turn-auto',
      autonomy: {action: 'pause', at: 'confirm_assets', message: '资产已生成，已到达人工闸门：请确认资产质量后继续。'},
    });
    expect(state.messages.at(-1)?.text).toMatch(/^⏸ .*已到达人工闸门/);
    expect(state.messages.at(-1)?.tool).toBe('autonomy');
  });

  it('ignores autonomy events with empty message', () => {
    const state = applyAgentEvent(createChatState(), {type: 'autonomy', turn_id: 't', autonomy: {action: 'advance', message: ''}});
    expect(state.messages).toHaveLength(0);
  });

  it('maps every four-stage asset emit to the right workflow step', () => {
    // 四段式资产生成（角色→场景→道具→音色）的后端 stage 必须能点亮步骤条对应步骤，
    // 否则生成资产时步骤条停在“提取资产清单中”不推进。
    expect(workflowStepIndexForStage('asset_characters_start')).toBe(3); // chars
    expect(workflowStepIndexForStage('asset_characters_done')).toBe(3);
    expect(workflowStepIndexForStage('asset_scenes_start')).toBe(4);     // scenes
    expect(workflowStepIndexForStage('asset_scenes_done')).toBe(4);
    expect(workflowStepIndexForStage('asset_props_start')).toBe(5);      // props
    expect(workflowStepIndexForStage('asset_props_done')).toBe(5);
    expect(workflowStepIndexForStage('asset_voices_start')).toBe(6);     // voices
    expect(workflowStepIndexForStage('asset_voices_done')).toBe(6);
  });

  it('maps fine-grained progressStage to the right workflow step', () => {
    // 服务端按资产表实际状态推导的 progressStage（chars/scenes/props/voices/confirm）
    // 必须能点亮步骤条对应步骤——这是"角色/场景已完成、道具待生成"时
    // 流程条停在"生成道具中"而非"提取资产清单中"的保证。
    expect(workflowStepIndexForStage('chars')).toBe(3);
    expect(workflowStepIndexForStage('scenes')).toBe(4);
    expect(workflowStepIndexForStage('props')).toBe(5);
    expect(workflowStepIndexForStage('voices')).toBe(6);
    expect(workflowStepIndexForStage('confirm')).toBe(7);
  });

  it('keeps stage labels consistent between workflow steps and humanize', () => {
    // 步骤条顺序敏感：analyze → split → extract → chars → scenes → props → voices → confirm → storyboard → shots → camera → frames → clips → concat
    const ids = WORKFLOW_STEPS.map((step) => step.id);
    expect(ids).toEqual([
      'analyze', 'split', 'extract', 'chars', 'scenes', 'props', 'voices',
      'confirm', 'storyboard', 'shots', 'camera', 'frames', 'clips', 'concat',
    ]);
    // 每个步骤至少有一个可推进的 stage，避免出现“永远点不亮”的死步骤
    for (const step of WORKFLOW_STEPS) {
      expect(step.stages.length).toBeGreaterThan(0);
      for (const stage of step.stages) {
        expect(workflowStepIndexForStage(stage)).toBe(WORKFLOW_STEPS.indexOf(step));
      }
    }
  });

  it('accumulates multiple progress images on one tool activity instead of overwriting', () => {
    // 资产生成会逐张发 tool_progress（每张图一个 metadata.path）。
    // 同一工具活动行必须累积所有图片，而不是用最新一张覆盖已贴出的图。
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-img'});
    const progress = (path: string) => ({
      type: 'tool_progress' as const,
      turn_id: 'turn-img',
      tool: {id: 'asset-gen', name: 'vimax_generate_assets'},
      progress: {
        stage: 'asset_characters_start',
        message: `已生成 ${path}`,
        metadata: {session_id: 's1', path, media_url: `/assets/${path}`},
      },
    });
    state = applyAgentEvent(state, progress('char-1.png'));
    state = applyAgentEvent(state, progress('char-2.png'));
    state = applyAgentEvent(state, progress('char-3.png'));
    const activity = state.messages.find((m) => m.role === 'activity' && m.tool === 'vimax_generate_assets');
    expect(activity?.artifacts?.map((a) => a.path)).toEqual(['char-1.png', 'char-2.png', 'char-3.png']);
  });

  it('does not let tool completion move the workflow step to the concat step', () => {
    // 修复前：tool_result 把活动行 stage 写成 'completed'，而 'completed' 映射到最后一个
    // 步骤（concat），导致资产没生成时步骤条却显示“合成中”。tool_result 不应改动业务 stage。
    let state = applyAgentEvent(createChatState(), {type: 'turn', turn_id: 'turn-cmp'});
    state = applyAgentEvent(state, {
      type: 'tool_start', turn_id: 'turn-cmp',
      tool: {id: 't1', name: 'read_json'},
    });
    state = applyAgentEvent(state, {
      type: 'tool_result', turn_id: 'turn-cmp',
      tool_result: {name: 'read_json', ok: true},
    });
    const activity = state.messages.find((m) => m.role === 'activity' && m.tool === 'read_json');
    expect(activity?.status).toBe('done');
    expect(activity?.stage).not.toBe('completed');
    expect(activity?.stage).not.toBe('failed');
  });
});

describe('reply option parsing', () => {
  it('parses numbered options from a stage-complete reply', () => {
    const text = [
      '第 1 集资产已全部生成。',
      '',
      '回复【1 继续分镜】',
      '回复【2 查看资产台账】',
      '回复【3 重做指定资产】',
    ].join('\n');
    expect(parseReplyOptions(text)).toEqual([
      {label: '1 继续分镜', reply: '1', index: 1},
      {label: '2 查看资产台账', reply: '2', index: 2},
      {label: '3 重做指定资产', reply: '3', index: 3},
    ]);
  });

  it('tolerates numbering separators and skips duplicate numbers', () => {
    const text = [
      '回复【1. 继续分镜】',
      '回复【1）继续分镜】',
      '回复【2：重做指定资产】',
      '回复【2 重做指定资产】',
    ].join('\n');
    expect(parseReplyOptions(text)).toEqual([
      {label: '1 继续分镜', reply: '1', index: 1},
      {label: '2 重做指定资产', reply: '2', index: 2},
    ]);
  });

  it('parses bracket-less and bare-bracket option lines', () => {
    const text = [
      '资产已生成，逐项贴图见上。',
      '回复 1 全部确认，确认资产，开始分镜',
      '回复 2 只重做角色，告诉我哪个角色不行',
      '【3 只看台账】',
    ].join('\n');
    expect(parseReplyOptions(text)).toEqual([
      {label: '1 全部确认', reply: '1', index: 1},
      {label: '2 只重做角色', reply: '2', index: 2},
      {label: '3 只看台账', reply: '3', index: 3},
    ]);
  });

  it('keeps text-only commands like 全量渲染', () => {
    const text = '回复【全量渲染】';
    expect(parseReplyOptions(text)).toEqual([{label: '全量渲染', reply: '全量渲染', index: Number.MAX_SAFE_INTEGER}]);
  });

  it('does not treat options mentioned in prose as clickable', () => {
    const text = '你可以回复【1 继续分镜】来推进流程。';
    expect(parseReplyOptions(text)).toEqual([]);
  });

  it('only surfaces options on assistant answers', () => {
    const activity: Message = {id: 'a1', role: 'activity', text: '回复【1 继续分镜】'};
    const assistant: Message = {id: 'a2', role: 'assistant', text: '回复【1 继续分镜】'};
    expect(replyOptionsForMessage(activity)).toEqual([]);
    expect(replyOptionsForMessage(assistant).length).toBe(1);
  });
});

describe('attachMediaByCreatedTime', () => {
  const artifact = (path: string, updatedAt: string): Artifact => ({
    path,
    name: path.split('/').pop() || path,
    kind: 'video',
    size: 1,
    updatedAt,
    url: `/api/artifact?path=${encodeURIComponent(path)}`,
  });

  it('挂到写入时间对应的活动行，而不是名字含 render 的那一条', () => {
    // 单个分段视频重新生成走 vimax_regenerate（名字不含 render），旧逻辑整批漏掉
    const messages: Message[] = [
      {id: 'a1', role: 'activity', tool: 'vimax_render_video', text: '已完成', createdAt: '2026-07-17T10:00:00.000Z'},
      {id: 'a2', role: 'activity', tool: 'vimax_regenerate', text: '已完成', createdAt: '2026-07-17T11:00:00.000Z'},
    ];
    const next = attachMediaByCreatedTime(messages, [artifact('segments/seg-1/video.mp4', '2026-07-17T11:30:00.000Z')]);
    expect(next[0].artifacts).toEqual([]);
    expect(next[1].artifacts?.map((item) => item.path)).toEqual(['segments/seg-1/video.mp4']);
  });

  it('已经挂在别处（后端 history 恢复）的产物不重复挂', () => {
    const video = artifact('segments/seg-1/video.mp4', '2026-07-17T11:30:00.000Z');
    const messages: Message[] = [
      {id: 'a1', role: 'activity', tool: 'vimax_render_video', text: '已完成', createdAt: '2026-07-17T10:00:00.000Z', artifacts: [video]},
      {id: 'a2', role: 'activity', tool: 'vimax_regenerate', text: '已完成', createdAt: '2026-07-17T11:00:00.000Z'},
    ];
    const next = attachMediaByCreatedTime(messages, [video]);
    expect(next[1].artifacts).toEqual([]);
  });

  it('没有活动行时原样返回，不制造空壳消息', () => {
    const messages: Message[] = [{id: 'u1', role: 'user', text: '开始'}];
    expect(attachMediaByCreatedTime(messages, [artifact('a.mp4', '2026-07-17T11:30:00.000Z')])).toBe(messages);
  });
});
