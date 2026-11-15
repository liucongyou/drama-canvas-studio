# Frontend Runtime and UX Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove generation and event-state coupling from the large canvas component, make task recovery actions consistent, improve prompt editing performance and add traceable runtime errors.

**Architecture:** Extract focused controllers/hooks from `CanvasView.tsx`, keep React Flow and existing visual components intact, and drive every card/task action from one backend-derived state model.

**Tech Stack:** React 18, TypeScript, React Flow, Vitest, Puppeteer/Playwright-compatible browser scripts

**Spec:** `docs/superpowers/specs/2026-11-15-industrial-root-refactor-design.md`

## Global Constraints

- Do not change card click, double-click, hover, drag or readonly-share semantics.
- The frontend does not invent task terminal states.
- Prompt text stays stable during Chinese IME composition.
- All polling uses compact episode projections.
- No paid generation in tests.

---

### Task 1: Generation Task Projection Hook

**Files:**
- Create: `web-vimax/src/canvas/useGenerationTaskProjection.ts`
- Create: `web-vimax/src/canvas/useGenerationTaskProjection.test.tsx`
- Modify: `web-vimax/src/CanvasView.tsx`
- Modify: `web-vimax/src/generationPresentation.ts`

**Interfaces:**
- Consumes: `GenerationTask[]`, optimistic submissions
- Produces:

```ts
useGenerationTaskProjection(tasks, optimistic): {
  busyNodeIds: Set<string>;
  applySnapshot(tasks: GenerationTask[]): Set<string>;
  releaseSynchronous(operationId: string, nodeId: string): void;
}
```

- [ ] **Step 1: Write a failing concurrent projection test**

```tsx
it('releases the completed node while a sibling keeps running', () => {
  const {result} = renderHook(() => useGenerationTaskProjection());
  act(() => result.current.applySnapshot([
    task('done', 'node-done', 'succeeded'),
    task('running', 'node-running', 'running'),
  ]));
  expect([...result.current.busyNodeIds]).toEqual(['node-running']);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run src/canvas/useGenerationTaskProjection.test.tsx`
Expected: hook missing.

- [ ] **Step 3: Implement the hook**

```ts
export function useGenerationTaskProjection() {
  const [tasks, setTasks] = useState<GenerationTask[]>([]);
  const optimisticRef = useRef<Map<string, OptimisticGenerationSubmission>>(new Map());
  const [busyNodeIds, setBusyNodeIds] = useState<Set<string>>(new Set());

  const applySnapshot = useCallback((nextTasks: GenerationTask[]) => {
    const projection = projectGenerationState(nextTasks, optimisticRef.current);
    optimisticRef.current = projection.retainedOptimistic;
    setTasks(nextTasks);
    setBusyNodeIds(projection.activeNodeIds);
    return projection.activeNodeIds;
  }, []);

  const releaseSynchronous = useCallback((operationId: string, nodeId: string) => {
    if (!releaseOptimisticGenerationSubmission(optimisticRef.current, nodeId, operationId)) return;
    const next = projectGenerationState(tasks, optimisticRef.current).activeNodeIds;
    setBusyNodeIds(next);
  }, [tasks]);

  return {busyNodeIds, applySnapshot, releaseSynchronous};
}
```

- [ ] **Step 4: Replace duplicated task state in CanvasView**

Replace direct `generationTasks`/`generationBusyIds` projection calls with the hook.
Keep the current API calls and task center rendering.

- [ ] **Step 5: Run tests**

Run:

```powershell
cd web-vimax
npx vitest run src/canvas/useGenerationTaskProjection.test.tsx src/generationPresentation.test.ts src/generationBusy.test.ts
npm run typecheck:canvas
```

Expected: all passed.

- [ ] **Step 6: Commit**

```powershell
git add web-vimax/src/canvas/useGenerationTaskProjection.ts web-vimax/src/canvas/useGenerationTaskProjection.test.tsx web-vimax/src/CanvasView.tsx web-vimax/src/generationPresentation.ts
git commit -m "refactor: isolate generation task projection"
```

### Task 2: Canvas Workspace Sync Hook

**Files:**
- Create: `web-vimax/src/canvas/useCanvasWorkspaceSync.ts`
- Create: `web-vimax/src/canvas/useCanvasWorkspaceSync.test.ts`
- Modify: `web-vimax/src/CanvasView.tsx`

**Interfaces:**
- Consumes: `getWorkspaceFacts`, `getWorkspaceMediaNodeSnapshot`, `getGenerationTasks`
- Produces: `refreshWorkspaceFacts()`, `refreshCurrentEpisodeFacts()`

- [ ] **Step 1: Write a failing fallback test**

```ts
it('reads the task ledger when busy ids exist even if task center is closed', async () => {
  const api = fakeWorkspaceApi();
  const sync = createWorkspaceSync({taskCenterOpen: false, busyNodeIds: new Set(['n1']), api});
  await sync.refreshCurrentEpisodeFacts();
  expect(api.getGenerationTasks).toHaveBeenCalledOnce();
});
```

- [ ] **Step 2: Implement a pure factory**

```ts
export function shouldReadTaskLedger(input: {
  taskCenterOpen: boolean;
  busyNodeIds: ReadonlySet<string>;
  optimisticCount: number;
}) {
  return input.taskCenterOpen || input.busyNodeIds.size > 0 || input.optimisticCount > 0;
}
```

Wire the factory into the hook so SSE, polling and initial load share the same
decision.

- [ ] **Step 3: Run tests**

Run: `npx vitest run src/canvas/useCanvasWorkspaceSync.test.ts src/canvasEventLifecycle.test.ts`
Expected: all passed.

- [ ] **Step 4: Commit**

```powershell
git add web-vimax/src/canvas/useCanvasWorkspaceSync.ts web-vimax/src/canvas/useCanvasWorkspaceSync.test.ts web-vimax/src/CanvasView.tsx
git commit -m "refactor: centralize canvas workspace synchronization"
```

### Task 3: Prompt Editor Local Draft and IME Contract

**Files:**
- Create: `web-vimax/src/canvas/PromptEditor.tsx`
- Create: `web-vimax/src/canvas/PromptEditor.test.tsx`
- Modify: `web-vimax/src/CanvasView.tsx`
- Modify: `web-vimax/src/ui-refinements.css`

**Interfaces:**
- Consumes: `value`, `onCommit`, `onDraftChange`, `readOnly`
- Produces: an editor that updates only its local draft during typing and commits after blur or 600 ms idle

- [ ] **Step 1: Write a failing IME test**

```tsx
it('does not commit incomplete Chinese composition text', () => {
  const onCommit = vi.fn();
  render(<PromptEditor value="" onCommit={onCommit} />);
  const editor = screen.getByRole('textbox');
  fireEvent.compositionStart(editor);
  fireEvent.change(editor, {target: {value: 'jia'}});
  expect(onCommit).not.toHaveBeenCalled();
  fireEvent.compositionEnd(editor, {data: '贾'});
  vi.runAllTimers();
  expect(onCommit).toHaveBeenCalledWith('贾');
});
```

- [ ] **Step 2: Implement composition-safe state**

```tsx
const composingRef = useRef(false);
const [draft, setDraft] = useState(value);

onCompositionStart={() => { composingRef.current = true; }}
onCompositionEnd={(event) => {
  composingRef.current = false;
  setDraft(event.currentTarget.value);
  scheduleCommit(event.currentTarget.value);
}}
onChange={(event) => {
  setDraft(event.currentTarget.value);
  if (!composingRef.current) scheduleCommit(event.currentTarget.value);
}}
```

Do not update the global node array on every keystroke.

- [ ] **Step 3: Replace modal textarea**

Use `PromptEditor` in the node modal and asset annotation editor.
Preserve scroll position when content is deleted near the bottom.

- [ ] **Step 4: Run tests and typecheck**

Run:

```powershell
cd web-vimax
npx vitest run src/canvas/PromptEditor.test.tsx src/promptScrollAnchor.test.ts src/promptScrollbarCss.test.ts
npm run typecheck:canvas
```

Expected: all passed.

- [ ] **Step 5: Commit**

```powershell
git add web-vimax/src/canvas/PromptEditor.tsx web-vimax/src/canvas/PromptEditor.test.tsx web-vimax/src/CanvasView.tsx web-vimax/src/ui-refinements.css
git commit -m "refactor: isolate and stabilize prompt editor"
```

### Task 4: Unified Recovery Actions

**Files:**
- Create: `web-vimax/src/canvas/TaskRecoveryActions.tsx`
- Create: `web-vimax/src/canvas/TaskRecoveryActions.test.tsx`
- Modify: `web-vimax/src/CanvasView.tsx`
- Modify: `web-vimax/src/taskOutcomePresentation.ts`

**Interfaces:**
- Consumes: normalized task outcome
- Produces: one action set for card and task center:

```ts
type RecoveryAction =
  | 'retry'
  | 'switch-model'
  | 'check-provider'
  | 'recover-local-result'
  | 'mark-failed';
```

- [ ] **Step 1: Write failing action tests**

```tsx
it.each([
  ['provider_account_overdue', 'check-provider'],
  ['output_sensitive', 'retry'],
  ['media_source_missing', 'recover-local-result'],
  ['uncertain', 'mark-failed'],
])('maps %s to %s', (reason, action) => {
  expect(primaryRecoveryAction(taskWithReason(reason))).toBe(action);
});
```

- [ ] **Step 2: Implement the mapping**

```ts
export function primaryRecoveryAction(task: GenerationTask): RecoveryAction {
  if (task.error_kind === 'local_validation') return 'retry';
  if (/欠费|余额不足/i.test(task.error || '')) return 'check-provider';
  if (task.status === 'interrupted') return 'mark-failed';
  if (/媒体源缺失|local file/i.test(task.error || '')) return 'recover-local-result';
  return 'retry';
}
```

- [ ] **Step 3: Render one action component**

Use `TaskRecoveryActions` in:

- the media card failed state;
- the task center row;
- the missing-media card.

All actions call existing backend endpoints; no new provider request is hidden
behind a UI-only action.

- [ ] **Step 4: Run tests**

Run: `npx vitest run src/canvas/TaskRecoveryActions.test.tsx src/taskOutcomePresentation.test.ts src/manualTaskResolution.test.ts`
Expected: all passed.

- [ ] **Step 5: Commit**

```powershell
git add web-vimax/src/canvas/TaskRecoveryActions.tsx web-vimax/src/canvas/TaskRecoveryActions.test.tsx web-vimax/src/CanvasView.tsx web-vimax/src/taskOutcomePresentation.ts
git commit -m "feat: unify generation recovery actions"
```

### Task 5: Runtime Error Telemetry

**Files:**
- Create: `web-vimax/src/telemetry.ts`
- Create: `web-vimax/src/telemetry.test.ts`
- Modify: `web-vimax/src/ErrorBoundary.tsx`
- Modify: `web-vimax/src/api.ts`
- Modify: `server.py`
- Create: `tests/test_frontend_telemetry.py`

**Interfaces:**
- Consumes: sanitized browser errors with tenant/project/node/task context
- Produces: `POST /api/telemetry/frontend-errors`

- [ ] **Step 1: Write failing sanitizer test**

```ts
it('removes cookies, tokens and prompt bodies', () => {
  const clean = sanitizeTelemetry({
    message: 'boom',
    cookie: 'session=secret',
    token: 'secret',
    prompt: '完整提示词',
    projectId: 'p1',
    nodeId: 'n1',
  });
  expect(clean).toEqual({
    message: 'boom',
    projectId: 'p1',
    nodeId: 'n1',
  });
});
```

- [ ] **Step 2: Implement bounded telemetry**

Payload limit: 16KB.
Allowed fields:

```text
message, stack, code, projectId, episodeId, nodeId, taskId,
route, userAgent, releaseVersion, createdAt
```

Do not persist prompt, provider request bodies, cookies, authorization headers
or media URLs with signatures.

- [ ] **Step 3: Persist and rotate**

Store under tenant audit storage with a 30-day TTL.
Expose counts in `/api/metrics`.

- [ ] **Step 4: Run tests**

Run:

```powershell
cd web-vimax
npx vitest run src/telemetry.test.ts
cd ..
python -m pytest tests/test_frontend_telemetry.py tests/test_audit_metrics.py -q
```

Expected: all passed.

- [ ] **Step 5: Commit**

```powershell
git add web-vimax/src/telemetry.ts web-vimax/src/telemetry.test.ts web-vimax/src/ErrorBoundary.tsx web-vimax/src/api.ts server.py tests/test_frontend_telemetry.py
git commit -m "feat: add tenant-safe frontend error telemetry"
```

### Task 6: Frontend Regression and Performance Gate

**Files:**
- Create: `web-vimax/scripts/verify-industrial-canvas.mjs`
- Modify: `web-vimax/package.json`
- Modify: `web-vimax/src/changelog.ts`
- Modify: `web-vimax/src/changelogEntries.ts`

**Interfaces:**
- Consumes: local server and mocked generation APIs
- Produces: one browser gate that exits nonzero on page errors, console errors,
  overlapping controls or failed media

- [ ] **Step 1: Add the browser script**

The script must:

```text
1. Load desktop 1440x900 and mobile 390x844.
2. Open one episode with at least 50 media nodes.
3. Type Chinese text through composition events.
4. Pan and zoom for 10 seconds.
5. Open image and video history.
6. Exercise failed, uncertain, expiring and missing-media cards with mock tasks.
7. Verify no overlap and no horizontal overflow.
8. Assert pageErrors=[] and consoleErrors=[].
```

- [ ] **Step 2: Add package gates**

```json
{
  "scripts": {
    "check:industrial": "npm run typecheck:canvas && npm test && npm run build && node scripts/verify-industrial-canvas.mjs"
  }
}
```

- [ ] **Step 3: Run the gate**

Run: `npm run check:industrial`
Expected: pass.

- [ ] **Step 4: Commit**

```powershell
git add web-vimax/scripts/verify-industrial-canvas.mjs web-vimax/package.json web-vimax/src/changelog.ts web-vimax/src/changelogEntries.ts
git commit -m "test: add industrial frontend interaction gate"
```

## Plan Self-Review

- Spec coverage: task projection, workspace sync, editor, recovery actions,
  telemetry and browser performance each map to a task.
- Placeholder scan: no TBD/TODO; commands and concrete contracts are present.
- Interface consistency: the projection hook, workspace decision factory,
  recovery action type and telemetry sanitizer are defined before use.
