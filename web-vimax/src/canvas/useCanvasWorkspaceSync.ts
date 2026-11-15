import {useEffect} from 'react';
import type {MutableRefObject} from 'react';

import {getGenerationTasks} from '../api';
import type {GenerationTask} from '../api';

type TaskLedgerDecision = {
  taskCenterOpen: boolean;
  busyNodeIds: ReadonlySet<string>;
  optimisticCount: number;
};

export function shouldReadTaskLedger(input: TaskLedgerDecision): boolean {
  return (
    input.taskCenterOpen
    || input.busyNodeIds.size > 0
    || input.optimisticCount > 0
  );
}

type CanvasWorkspaceSyncOptions = {
  session: string;
  currentEpisode: string | null;
  taskCenterOpen: boolean;
  busyNodeIds: ReadonlySet<string>;
  optimisticCount: number;
  activeTaskCount: number;
  taskLoadGenerationRef: MutableRefObject<number>;
  applyTaskSnapshot: (tasks: GenerationTask[]) => Set<string>;
};

export function useCanvasWorkspaceSync({
  session,
  currentEpisode,
  taskCenterOpen,
  busyNodeIds,
  optimisticCount,
  activeTaskCount,
  taskLoadGenerationRef,
  applyTaskSnapshot,
}: CanvasWorkspaceSyncOptions): void {
  const busyKey = [...busyNodeIds].sort().join('|');
  useEffect(() => {
    if (
      !currentEpisode
      || session.startsWith('share:')
      || !shouldReadTaskLedger({
        taskCenterOpen,
        busyNodeIds,
        optimisticCount,
      })
    ) {
      return;
    }
    let alive = true;
    const loadGeneration = taskLoadGenerationRef.current;
    const refreshTasks = () => {
      void getGenerationTasks(session, 80, currentEpisode)
        .then((tasks) => {
          if (
            alive
            && loadGeneration === taskLoadGenerationRef.current
          ) {
            applyTaskSnapshot(tasks);
          }
        })
        .catch(() => {});
    };
    refreshTasks();
    const interval = (
      busyNodeIds.size > 0
      || optimisticCount > 0
      || activeTaskCount > 0
    )
      ? 1200
      : 6000;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') refreshTasks();
    }, interval);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [
    activeTaskCount,
    applyTaskSnapshot,
    busyKey,
    busyNodeIds,
    currentEpisode,
    optimisticCount,
    session,
    taskCenterOpen,
    taskLoadGenerationRef,
  ]);
}
