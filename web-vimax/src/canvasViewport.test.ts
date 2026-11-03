import {describe, expect, it} from 'vitest';
import {
  popupRevealShift,
  filterMediaNodesInBounds,
  focusedNodeVisibleBounds,
  shouldUseLargeCanvasOverview,
  shouldLoadMediaForViewport,
  mergeEpisodeViewport,
  selectEpisodeViewport,
  selectCanvasViewport,
  terminalTaskSignature,
  episodeIdFromWorkspace,
  shouldPersistViewport,
  shouldSaveViewport,
  validRestoredViewport,
  viewportForCanvasBounds,
  viewportLoadBounds,
  visibleBoundsForViewport,
} from './canvasViewport';

import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

describe('restored canvas viewport validation', () => {
  it('accepts a finite workspace viewport inside the interactive zoom range', () => {
    expect(validRestoredViewport({x: -120, y: 48, zoom: 0.72}))
      .toEqual({x: -120, y: 48, zoom: 0.72});
    expect(validRestoredViewport({x: 40, y: 18, zoom: 3.2}))
      .toEqual({x: 40, y: 18, zoom: 3.2});
  });

  it('rejects malformed or implausible saved values instead of restoring a bad zoom', () => {
    expect(validRestoredViewport(null)).toBeNull();
    expect(validRestoredViewport({x: 0, y: 0, zoom: 0})).toBeNull();
    expect(validRestoredViewport({x: 0, y: 0, zoom: 20})).toBeNull();
    expect(validRestoredViewport({x: 'bad', y: 0, zoom: 1})).toBeNull();
  });
});

describe('canvas viewport persistence threshold', () => {
  it('coalesces duplicate terminal snapshots into one reconciliation signature', () => {
    const tasks = [
      {
        id: 'task-image',
        node_id: 'node-image',
        status: 'succeeded',
        finished_at: '2026-11-03T06:00:00Z',
      },
      {
        id: 'task-video-old',
        node_id: 'node-video',
        status: 'failed',
        finished_at: '2026-11-03T05:00:00Z',
      },
      {
        id: 'task-running',
        node_id: 'node-running',
        status: 'running',
        updated_at: '2026-11-03T06:01:00Z',
      },
    ];
    const first = terminalTaskSignature(tasks);
    expect(terminalTaskSignature([...tasks].reverse())).toBe(first);
    expect(terminalTaskSignature(tasks)).toBe(first);
    expect(first).toContain('task-image:node-image:succeeded');
    expect(first).toContain('task-video-old:node-video:failed');
    expect(first).not.toContain('task-running');
  });

  it('invalidates the signature for a genuinely newer retry', () => {
    const before = terminalTaskSignature([{
      id: 'task-video-1',
      node_id: 'node-video',
      status: 'failed',
      finished_at: '2026-11-03T05:00:00Z',
    }]);
    const after = terminalTaskSignature([{
      id: 'task-video-2',
      node_id: 'node-video',
      status: 'succeeded',
      finished_at: '2026-11-03T06:00:00Z',
    }]);
    expect(after).not.toBe(before);
  });

  it('does not send a network write for identical movement callbacks', () => {
    expect(shouldPersistViewport(
      {x: 10, y: 20, zoom: 0.5},
      {x: 10.2, y: 19.8, zoom: 0.50001},
    )).toBe(false);
    expect(shouldPersistViewport(
      {x: 10, y: 20, zoom: 0.5},
      {x: 11, y: 20, zoom: 0.5},
    )).toBe(true);
  });

  it('never writes while a switch restore is settling or into a different episode scope', () => {
    expect(shouldSaveViewport(false, 'project:EP002', 'project:EP002')).toBe(true);
    expect(shouldSaveViewport(true, 'project:EP002', 'project:EP002')).toBe(false);
    expect(shouldSaveViewport(false, 'project:EP001', 'project:EP002')).toBe(false);
  });

  it('keeps the previous episode view when the target episode has no saved view', () => {
    const merged = mergeEpisodeViewport(
      {},
      'EP003',
      {x: -804.5, y: 92, zoom: 0.38},
    );
    expect(selectEpisodeViewport(merged, 'EP003')).toEqual({
      x: -804.5,
      y: 92,
      zoom: 0.38,
    });
    expect(selectEpisodeViewport(merged, 'EP004')).toBeNull();
  });

  it('coalesces viewport persistence to gesture end instead of every move frame', () => {
    const source = readFileSync(
      resolve(__dirname, 'CanvasView.tsx'),
      'utf8',
    );
    const moveBlock = source.slice(
      source.indexOf('onViewportChange={(next) => {'),
      source.indexOf('onConnectStart=', source.indexOf('onViewportChange={(next) => {')),
    );
    expect(moveBlock).not.toContain('rememberViewport(next)');
    const moveEndBlock = source.slice(
      source.indexOf('onMoveEnd={(_event, viewport) => {'),
      source.indexOf('// 连线中点的剪断按钮', source.indexOf('onMoveEnd={(_event, viewport) => {')),
    );
    expect(moveEndBlock).toContain('flushViewportNow()');
  });

  it('keeps the interactive graph read ahead of auxiliary task polling', () => {
    const source = readFileSync(resolve(__dirname, 'api.ts'), 'utf8');
    const start = source.indexOf(
      'async function getProjectionCanvasWorkspace(',
    );
    const end = source.indexOf(
      'export async function getCanvasWorkspace(',
      start,
    );
    const block = source.slice(start, end);
    expect(block.indexOf('await getWorkspaceNodes('))
      .toBeLessThan(block.indexOf('await getGenerationTasks('));
  });

  it('does not guess an episode for payload-node prefetch without ?ep=', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    expect(source).toMatch(
      /const indexedNodesPromise = episodeKnownFromUrl && requestedEpisodeForNodes/,
    );
  });

  it('does not poll project JSON when no generation is active', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    expect(source).toMatch(
      /if \(!hasGeneratingMedia && activeGenerationTaskCount === 0\) return;/,
    );
  });

  it('does not force a full workspace read when SSE reconnects', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const openBlock = source.slice(
      source.indexOf('source.onopen = () => {'),
      source.indexOf('source.onerror = () => {'),
    );
    expect(openBlock).not.toContain('refreshWorkspaceFacts()');
  });

  it('applies terminal media events without re-reading the whole workspace', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const terminalTimerBlock = source.slice(
      source.indexOf('const refreshAfterTerminalEvent = () => {'),
      source.indexOf('const applyEvent = (raw: string) => {'),
    );
    expect(terminalTimerBlock).not.toContain('refreshWorkspaceFacts()');
  });

  it('keeps cross-tab connection updates on the node projection only', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const broadcastBlock = source.slice(
      source.indexOf("if (message.type === 'canvas-connections-updated')"),
      source.indexOf("if (message.type !== 'canvas-nodes-updated')"),
    );
    expect(broadcastBlock).toContain('getWorkspaceNodes(session, currentEp)');
    expect(broadcastBlock).not.toContain('getCanvasWorkspace(');
  });

  it('reconciles visibility and network recovery, not the initial focus', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const start = source.indexOf('const visibilityRef = {hidden:');
    const recoveryBlock = source.slice(
      start,
      source.indexOf(
        "document.addEventListener('visibilitychange', onVisibilityChange)",
        start,
      ),
    );
    expect(recoveryBlock).toContain('const resumed = visibilityRef.hidden && !hidden;');
    expect(recoveryBlock).toContain('if (networkRef.offline) refreshAfterResume();');
    expect(recoveryBlock).not.toContain("window.addEventListener('focus'");
  });

  it('treats historical terminal tasks as the initial reconciliation baseline', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const terminalBlock = source.slice(
      source.indexOf('const terminalSnapshotPrimedRef'),
      source.indexOf('const mediaSaveTimer'),
    );
    expect(terminalBlock).toContain('terminalSnapshotPrimedRef.current = true;');
    expect(terminalBlock).toContain('terminalRefreshRef.current = signature;');
    expect(terminalBlock).toContain('return;');
  });

  it('suppresses implicit duplicate workspace refresh immediately after first paint', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    expect(source).toMatch(
      /workspaceReadyAtRef\.current > 0\s*&&\s*performance\.now\(\) - workspaceReadyAtRef\.current < 1_500/s,
    );
    expect(source).toMatch(
      /setLoading\(false\);\s*(?:workspaceReadyAtRef\.current = performance\.now\(\);)/s,
    );
  });

  it('does not let the initial episode correction re-run the workspace loader', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const loaderEnd = source.slice(
      source.indexOf('void completedWorkspace;'),
      source.indexOf('// 拉取创作质量自评报告', source.indexOf('void completedWorkspace;')),
    );
    expect(loaderEnd).toContain('}, [session, pidRetry]);');
    expect(loaderEnd).not.toContain('}, [session, currentEp, pidRetry]);');
  });

  it('loads only genuinely different target episodes after first paint', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const block = source.slice(
      source.indexOf('// Explicit episode switches after the initial workspace paint.'),
      source.indexOf('// 拉取创作质量自评报告'),
    );
    expect(block).toContain(
      'if (!target || !active || target === active) return;',
    );
    expect(block).toContain(
      'getCanvasEpisodeWorkspace(session, target, abort.signal)',
    );
    expect(block).not.toContain('getCanvasWorkspace(session, target');
    expect(block.slice(
      0,
      block.indexOf('getCanvasEpisodeWorkspace(session, target, abort.signal)'),
    )).not.toContain('setCanvas(null);');
    expect(block).toContain('}, [session, currentEp]);');
  });

  it('keeps the complete project episode list when loading one target episode', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    const block = source.slice(
      source.indexOf("if ('empty' in workspace.canvas)"),
      source.indexOf('startTransition(', source.indexOf("if ('empty' in workspace.canvas)")),
    );
    expect(block).toContain('episodes: previous.meta.episodes');
    expect(block).toContain('current_ep: loadedEpisode');
  });

  it('preserves every project episode across a single-episode switch', () => {
    const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
    expect(source).toMatch(
      /const loadedCanvas = workspace\.canvas;[\s\S]*setCanvas\(\(previous\) => previous[\s\S]*episodes: previous\.meta\.episodes/s,
    );
  });
});

describe('episode viewport map', () => {
  it('keeps independent saved viewports and resolves only the active episode', () => {
    const merged = mergeEpisodeViewport(
      {EP002: {x: 20, y: 30, zoom: 0.5}},
      'EP001',
      {x: 10, y: 12, zoom: 0.66},
    );
    expect(selectEpisodeViewport(merged, 'EP001')).toEqual({
      x: 10,
      y: 12,
      zoom: 0.66,
    });
    expect(selectEpisodeViewport(merged, 'EP002')).toEqual({
      x: 20,
      y: 30,
      zoom: 0.5,
    });
    expect(selectEpisodeViewport(merged, 'EP003')).toBeNull();
  });

  it('resolves the effective workspace episode even before currentEp commits', () => {
    expect(episodeIdFromWorkspace(null, [{episodeId: 'EP002'}])).toBe('EP002');
    expect(episodeIdFromWorkspace('ep001', [{episodeId: 'EP002'}])).toBe('EP001');
  });

  it('uses the project-scoped viewport for a single-episode project', () => {
    const viewports = {
      'project-id': {x: -804.5, y: 92, zoom: 0.38},
    };
    expect(selectCanvasViewport(
      viewports,
      'EP003',
      'project-id',
      true,
    )).toEqual({x: -804.5, y: 92, zoom: 0.38});
    expect(selectCanvasViewport(
      viewports,
      'EP003',
      'unrelated-project',
      false,
    )).toBeNull();
  });
});

describe('popupRevealShift', () => {
  it('moves canvas up only by the amount clipped at the bottom', () => {
    expect(popupRevealShift(
      {top: 120, bottom: 540},
      500,
      {topInset: 14, bottomInset: 14},
    )).toBe(54);
  });

  it('moves canvas down when a popup is clipped at the top', () => {
    expect(popupRevealShift(
      {top: 4, bottom: 260},
      500,
      {topInset: 14, bottomInset: 14},
    )).toBe(-10);
  });

  it('does not move when the popup is already visible', () => {
    expect(popupRevealShift(
      {top: 100, bottom: 400},
      500,
      {topInset: 14, bottomInset: 14},
    )).toBe(0);
  });
});

describe('large canvas viewport', () => {
  it('only enables the overview for a large non-text workspace', () => {
    const textNodes = Array.from({length: 500}, (_, index) => ({
      kind: 'text',
      x: index,
      y: index,
    }));
    expect(shouldUseLargeCanvasOverview(textNodes)).toBe(false);
    expect(shouldUseLargeCanvasOverview([
      ...textNodes.slice(0, 400),
      {kind: 'image', x: 0, y: 0},
    ])).toBe(true);
    expect(shouldUseLargeCanvasOverview(textNodes.slice(0, 400))).toBe(false);
  });

  it('converts React Flow translation into world-space bounds', () => {
    expect(visibleBoundsForViewport(
      {x: -1000, y: -500, zoom: 0.5},
      1200,
      800,
      100,
    )).toEqual({
      x1: 1800,
      y1: 800,
      x2: 4600,
      y2: 2800,
    });
  });

  it('keeps nodes that intersect the viewport and excludes distant nodes', () => {
    const nodes = [
      {id: 'inside', kind: 'image', x: 10, y: 10},
      {id: 'edge', kind: 'video', x: 90, y: 90},
      {id: 'outside', kind: 'audio', x: 300, y: 300},
    ];
    const visible = filterMediaNodesInBounds(
      nodes,
      {x1: 0, y1: 0, x2: 100, y2: 100},
      () => ({w: 20, h: 20}),
    );
    expect(visible.map((node) => node.id)).toEqual(['inside', 'edge']);
  });

  it('preloads a screen-space overscan band without loading the whole graph', () => {
    expect(viewportLoadBounds(
      {x1: 100, y1: 200, x2: 500, y2: 600},
      0.5,
      640,
    )).toEqual({
      x1: -1180,
      y1: -1080,
      x2: 1780,
      y2: 1880,
    });
  });

  it('limits deferred media loading to a bounded first-screen budget', () => {
    expect(shouldLoadMediaForViewport({
      contentVisible: true,
      index: 50,
      total: 200,
    })).toBe(true);
    expect(shouldLoadMediaForViewport({
      contentVisible: false,
      index: 11,
      total: 200,
    })).toBe(true);
    expect(shouldLoadMediaForViewport({
      contentVisible: false,
      index: 12,
      total: 200,
    })).toBe(false);
  });

  it('centers a focused node inside a tight preload window', () => {
    expect(focusedNodeVisibleBounds(
      {x: 1000, y: 500, w: 280, h: 180},
      0.8,
      1440,
      1000,
      240,
    )).toEqual({
      x1: -60,
      y1: -335,
      x2: 2340,
      y2: 1515,
    });
  });

  it('keeps an initial focus view at a readable zoom and aligns it to the top', () => {
    const viewport = viewportForCanvasBounds(
      {x: 0, y: 100, width: 2400, height: 2400},
      969,
      807,
      {focus: true},
    );
    expect(viewport.zoom).toBe(0.5);
    expect(viewport.y).toBe(80 - 100 * 0.5);
    expect(viewport.x).toBe(969 / 2 - 1200 * 0.5);
  });

  it('still fits the complete graph when invoked explicitly', () => {
    const viewport = viewportForCanvasBounds(
      {x: 0, y: 0, width: 2400, height: 2400},
      969,
      807,
      {focus: false},
    );
    expect(viewport.zoom).toBeLessThan(0.5);
    expect(viewport.zoom).toBeCloseTo(Math.min(969 * 0.7 / 2400, 807 * 0.7 / 2400), 4);
  });
});
