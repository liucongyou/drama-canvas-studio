import {describe, expect, it} from 'vitest';
import {
  activeGenerationNodeIds,
  generationDurationLabel,
  generationPhaseLabel,
  generationProgressLabel,
  hasTrustedProviderProgress,
  isGenerationTaskActive,
  isSettledMediaCard,
  latestTaskByNode,
  liveGenerationCount,
  mediaGenerationVisualState,
  preserveActiveGenerationStatus,
  projectGenerationState,
  retainOptimisticGenerationSubmissions,
} from './generationPresentation';
import type {GenerationTask} from './api';

describe('active task status projection', () => {
  it('keeps an active task node generating when a stale workspace row says done', () => {
    expect(preserveActiveGenerationStatus(
      {id: 'node-a', status: 'done'},
      new Set(['node-a']),
    )).toMatchObject({
      id: 'node-a',
      status: 'generating',
      providerTaskStatus: 'queued',
    });
  });

  it('does not alter a terminal node after the active task is released', () => {
    const remote = {id: 'node-a', status: 'done'};
    expect(preserveActiveGenerationStatus(remote, new Set())).toBe(remote);
  });
});

describe('generation presentation', () => {
  it('treats stage-only values as non-progress', () => {
    const now = Date.parse('2026-10-30T00:00:20Z');
    const changedAt = '2026-10-30T00:00:10Z';
    expect(hasTrustedProviderProgress('submitting', 10, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('queued', 58, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('running', 0, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('running', 100, changedAt, now)).toBe(false);
    expect(hasTrustedProviderProgress('running', 58, changedAt, now)).toBe(true);
  });

  it('removes stale percent suffixes from stage text', () => {
    expect(generationPhaseLabel('生成中 10%')).toBe('生成中');
    expect(generationPhaseLabel('生成中 58%')).toBe('生成中');
    expect(generationPhaseLabel('正在连接供应商')).toBe('正在连接供应商');
  });

  it('renders exactly one percentage in progress labels', () => {
    const now = Date.parse('2026-10-30T00:00:20Z');
    const changedAt = '2026-10-30T00:00:10Z';
    expect(generationProgressLabel('生成中 10%', 'running', 10, changedAt, now)).toBe('生成中 10%');
    expect(generationProgressLabel('生成中', 'running', 58, changedAt, now)).toBe('生成中 58%');
    expect(generationProgressLabel('排队中 10%', 'queued', 10, changedAt, now)).toBe('生成中');
    expect(generationPhaseLabel('排队中')).toBe('生成中');
  });

  it('hides a provider percentage that has stopped moving', () => {
    const now = Date.parse('2026-10-30T01:00:00Z');
    const stale = '2026-10-30T00:00:00Z';
    expect(generationProgressLabel('生成中 10%', 'running', 10, stale, now)).toBe('生成中');
    expect(generationProgressLabel('生成中 10%', 'running', 10, undefined, now)).toBe('生成中');
  });

  it('uses a terminal label for completed task elapsed time', () => {
    const task: GenerationTask = {
      id: 'task',
      project_id: 'project',
      node_id: 'node',
      title: '段1',
      kind: 'video',
      status: 'succeeded',
      created_at: '2026-10-30T00:00:00.000Z',
      started_at: '2026-10-30T00:00:10.000Z',
      finished_at: '2026-10-30T00:01:10.000Z',
    };
    expect(generationDurationLabel(task)).toBe('完成于 1:00');
  });

  it('selects the newest task for a node regardless of API array order', () => {
    const tasks: GenerationTask[] = [
      {
        id: 'new',
        project_id: 'project',
        node_id: 'node',
        title: '段1',
        kind: 'video',
        status: 'succeeded',
        created_at: '2026-10-30T15:24:05.714Z',
        updated_at: '2026-10-30T16:01:26.574Z',
      },
      {
        id: 'old',
        project_id: 'project',
        node_id: 'node',
        title: '段1',
        kind: 'video',
        status: 'failed',
        created_at: '2026-10-30T15:00:00.000Z',
        updated_at: '2026-10-30T15:01:00.000Z',
      },
    ];

    expect(latestTaskByNode(tasks).get('node')?.id).toBe('new');
    expect(latestTaskByNode([...tasks].reverse()).get('node')?.id).toBe('new');
  });

  it('drops stale active node ids when the task is terminal or absent', () => {
    const running: GenerationTask = {
      id: 'running',
      project_id: 'project',
      node_id: 'node-a',
      title: '段A',
      status: 'running',
      created_at: '2026-10-30T15:00:00.000Z',
      updated_at: '2026-10-30T15:01:00.000Z',
    };
    const failed: GenerationTask = {
      id: 'failed',
      project_id: 'project',
      node_id: 'node-b',
      title: '段B',
      status: 'failed',
      created_at: '2026-10-30T15:02:00.000Z',
      updated_at: '2026-10-30T15:03:00.000Z',
    };

    expect([...activeGenerationNodeIds([running, failed])]).toEqual(['node-a']);
    expect([...activeGenerationNodeIds([failed])]).toEqual([]);
  });

  it('keeps a live provider task active while local recovery is interrupted', () => {
    const recovering: GenerationTask = {
      id: 'recovering',
      project_id: 'project',
      node_id: 'node-recovering',
      title: '段A',
      status: 'interrupted',
      phase: '结果待确认',
      provider_task_id: 'provider-live-1',
      delivery_status: 'pending',
      outcome: {
        phase: 'running',
        regenerate: 'blocked',
        reason: 'provider_task_running',
        providerAccepted: true,
      },
      created_at: '2026-10-30T15:00:00.000Z',
      updated_at: '2026-10-30T15:01:00.000Z',
    };

    expect([...activeGenerationNodeIds([recovering])]).toEqual([
      'node-recovering',
    ]);
    expect(isGenerationTaskActive(recovering)).toBe(true);
    expect(liveGenerationCount([recovering])).toBe(1);
  });

  it('keeps an accepted interrupted task active while its result is fetched', () => {
    const fetching: GenerationTask = {
      id: 'fetching',
      project_id: 'project',
      node_id: 'node-fetching',
      title: '场景｜2K',
      status: 'interrupted',
      phase: '供应商任务已提交',
      provider_task_id: 'provider-2k-1',
      delivery_status: 'pending',
      outcome: {
        phase: 'fetching',
        regenerate: 'blocked',
        reason: 'result_fetching',
        providerAccepted: true,
      },
      created_at: '2026-11-01T03:40:20.000Z',
      updated_at: '2026-11-01T03:40:23.000Z',
    };

    expect(isGenerationTaskActive(fetching)).toBe(true);
    expect([...activeGenerationNodeIds([fetching])]).toEqual(['node-fetching']);
  });

  it('keeps a submitted node active until its exact persisted task appears', () => {
    const startedAt = '2026-10-31T00:00:00.000Z';
    const retained = retainOptimisticGenerationSubmissions(
      [],
      new Map([['node-a', {startedAt, operationId: 'operation-new'}]]),
      Date.parse('2026-10-31T00:00:05.000Z'),
    );
    expect([...retained.keys()]).toEqual(['node-a']);
  });

  it('treats a persisted media node as the terminal card authority', () => {
    const staleUncertainTask: GenerationTask = {
      id: 'stale-uncertain',
      project_id: 'project',
      node_id: 'node-a',
      title: '角色图',
      status: 'interrupted',
      phase: '结果待确认',
      error: '供应商请求可能已经发出，但本地未取得确认回执',
      created_at: '2026-11-03T15:00:00.000Z',
      updated_at: '2026-11-03T15:05:00.000Z',
    };
    const settledNode = {
      status: 'done',
      mediaUrl: '/uploads/generated-final.jpg',
      finalizePending: false,
      providerLive: false,
      generationBusy: false,
    };

    expect(isGenerationTaskActive(staleUncertainTask)).toBe(false);
    expect(mediaGenerationVisualState(settledNode)).toMatchObject({
      hasRenderableMedia: true,
      busy: false,
      failed: false,
    });
    expect(isSettledMediaCard(settledNode)).toBe(true);
  });

  it('does not let an older terminal task hide a new submission', () => {
    const startedAt = '2026-10-31T00:00:00.000Z';
    const oldSucceeded: GenerationTask = {
      id: 'old-succeeded',
      project_id: 'project',
      node_id: 'node-a',
      client_operation_id: 'operation-old',
      title: '段A',
      status: 'succeeded',
      created_at: '2026-10-31T00:00:01.000Z',
      updated_at: '2026-10-31T00:00:04.000Z',
    };
    const retained = retainOptimisticGenerationSubmissions(
      [oldSucceeded],
      new Map([['node-a', {startedAt, operationId: 'operation-new'}]]),
      Date.parse('2026-10-31T00:00:05.000Z'),
    );
    expect(retained.get('node-a')?.operationId).toBe('operation-new');
  });

  it('releases the optimistic lock once the exact persisted task appears', () => {
    const startedAt = '2026-10-31T00:00:00.000Z';
    const task: GenerationTask = {
      id: 'task-a',
      project_id: 'project',
      node_id: 'node-a',
      client_operation_id: 'operation-new',
      title: '段A',
      status: 'running',
      created_at: '2026-10-31T00:00:01.000Z',
      updated_at: '2026-10-31T00:00:02.000Z',
    };
    const retained = retainOptimisticGenerationSubmissions(
      [task],
      new Map([['node-a', {startedAt, operationId: 'operation-new'}]]),
      Date.parse('2026-10-31T00:00:05.000Z'),
    );
    expect(retained.size).toBe(0);
  });

  it('lets the exact failed persisted task end the local submission', () => {
    const startedAt = '2026-10-31T00:00:00.000Z';
    const failed: GenerationTask = {
      id: 'failed-a',
      project_id: 'project',
      node_id: 'node-a',
      client_operation_id: 'operation-new',
      title: '段A',
      status: 'failed',
      created_at: '2026-10-31T00:00:01.000Z',
      updated_at: '2026-10-31T00:00:02.000Z',
    };
    const retained = retainOptimisticGenerationSubmissions(
      [failed],
      new Map([['node-a', {startedAt, operationId: 'operation-new'}]]),
      Date.parse('2026-10-31T00:00:05.000Z'),
    );
    expect(retained.size).toBe(0);
  });

  it('projects submitted nodes as active until their task arrives', () => {
    const startedAt = '2026-10-31T00:00:00.000Z';
    const projection = projectGenerationState(
      [],
      new Map([['node-a', {startedAt, operationId: 'operation-new'}]]),
      Date.parse('2026-10-31T00:00:05.000Z'),
    );
    expect([...projection.activeNodeIds]).toEqual(['node-a']);
  });

  it.each(['image', 'audio', 'video'])(
    'shows the same full busy mask for %s regeneration',
    () => {
      const state = mediaGenerationVisualState({
        status: 'generating',
        mediaUrl: '/uploads/previous-result',
        generationBusy: true,
      });
      expect(state.hasRenderableMedia).toBe(true);
      expect(state.busy).toBe(true);
      expect(state.resumableInBackground).toBe(false);
      expect(state.submitLocked).toBe(true);
    },
  );

  it('shows the full mask for a first generation without previous media', () => {
    const state = mediaGenerationVisualState({
      status: 'generating',
      generationBusy: true,
    });
    expect(state.busy).toBe(true);
    expect(state.resumableInBackground).toBe(false);
    expect(state.submitLocked).toBe(true);
  });

  it('unlocks the card after a terminal task clears the busy state', () => {
    const state = mediaGenerationVisualState({
      status: 'failed',
      mediaUrl: '/uploads/previous-result',
      generationBusy: false,
    });
    expect(state.busy).toBe(false);
    expect(state.resumableInBackground).toBe(false);
    expect(state.submitLocked).toBe(false);
  });

  it('does not turn a backend failure into generating from a provider string', () => {
    const state = mediaGenerationVisualState({
      status: 'failed',
      mediaUrl: '/uploads/previous-result',
      providerLive: true,
    });
    expect(state.busy).toBe(false);
    expect(state.failed).toBe(true);
    expect(state.submitLocked).toBe(false);
  });

  it('does not turn a backend success into generating from a stale task', () => {
    const state = mediaGenerationVisualState({
      status: 'done',
      mediaUrl: '/uploads/result',
      providerLive: true,
    });
    expect(state.busy).toBe(false);
    expect(state.failed).toBe(false);
    expect(state.submitLocked).toBe(false);
  });

  it('keeps the card submit button locked while a background version is running', () => {
    const button = {
      disabled: mediaGenerationVisualState({
        status: 'generating',
        mediaUrl: '/uploads/previous-result',
        generationBusy: true,
      }).submitLocked,
      title: '生成中，不能重复提交',
    };
    expect(button).toEqual({
      disabled: true,
      title: '生成中，不能重复提交',
    });
  });
});
