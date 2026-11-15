import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import type {Dispatch, MutableRefObject, SetStateAction} from 'react';

import type {GenerationTask} from '../api';
import {
  projectGenerationState,
  releaseOptimisticGenerationSubmission,
} from '../generationPresentation';
import type {OptimisticGenerationSubmission} from '../generationPresentation';

type GenerationTaskProjection = {
  tasks: GenerationTask[];
  setTasks: Dispatch<SetStateAction<GenerationTask[]>>;
  busyNodeIds: Set<string>;
  busyNodeIdsRef: MutableRefObject<Set<string>>;
  optimisticSubmissionsRef: MutableRefObject<
    Map<string, OptimisticGenerationSubmission>
  >;
  applySnapshot: (tasks: GenerationTask[]) => Set<string>;
  refreshProjection: (tasks?: GenerationTask[]) => Set<string>;
  beginOptimistic: (
    nodeId: string,
    submission: OptimisticGenerationSubmission,
  ) => Set<string>;
  releaseSynchronous: (operationId: string, nodeId: string) => boolean;
};

function sameNodeIds(left: ReadonlySet<string>, right: ReadonlySet<string>) {
  return (
    left.size === right.size
    && [...left].every((nodeId) => right.has(nodeId))
  );
}

export function useGenerationTaskProjection(): GenerationTaskProjection {
  const optimisticSubmissionsRef = useRef<
    Map<string, OptimisticGenerationSubmission>
  >(new Map());
  const [tasks, setTasksState] = useState<GenerationTask[]>([]);
  const tasksRef = useRef(tasks);
  const [busyNodeIds, setBusyNodeIds] = useState<Set<string>>(
    () => new Set(),
  );
  const busyNodeIdsRef = useRef(busyNodeIds);
  busyNodeIdsRef.current = busyNodeIds;

  const commitBusyNodeIds = useCallback((next: Set<string>) => {
    if (sameNodeIds(next, busyNodeIdsRef.current)) return next;
    busyNodeIdsRef.current = next;
    setBusyNodeIds(next);
    return next;
  }, []);

  const refreshProjection = useCallback((sourceTasks?: GenerationTask[]) => {
    const source = sourceTasks ?? tasksRef.current;
    const projection = projectGenerationState(
      source,
      optimisticSubmissionsRef.current,
    );
    optimisticSubmissionsRef.current = projection.retainedOptimistic;
    return commitBusyNodeIds(projection.activeNodeIds);
  }, [commitBusyNodeIds]);

  const applySnapshot = useCallback((snapshot: GenerationTask[]) => {
    const next = Array.isArray(snapshot) ? snapshot : [];
    tasksRef.current = next;
    setTasksState(next);
    const projection = projectGenerationState(
      next,
      optimisticSubmissionsRef.current,
    );
    optimisticSubmissionsRef.current = projection.retainedOptimistic;
    return commitBusyNodeIds(projection.activeNodeIds);
  }, [commitBusyNodeIds]);

  const setTasks = useCallback<Dispatch<SetStateAction<GenerationTask[]>>>(
    (action) => {
      setTasksState((current) => {
        const next = (
          typeof action === 'function'
            ? action(current)
            : action
        ) || [];
        tasksRef.current = next;
        return next;
      });
    },
    [],
  );

  const beginOptimistic = useCallback((
    nodeId: string,
    submission: OptimisticGenerationSubmission,
  ) => {
    optimisticSubmissionsRef.current.set(nodeId, submission);
    const projection = projectGenerationState(
      tasksRef.current,
      optimisticSubmissionsRef.current,
    );
    optimisticSubmissionsRef.current = projection.retainedOptimistic;
    return commitBusyNodeIds(projection.activeNodeIds);
  }, [commitBusyNodeIds]);

  const releaseSynchronous = useCallback((
    operationId: string,
    nodeId: string,
  ) => {
    if (!releaseOptimisticGenerationSubmission(
      optimisticSubmissionsRef.current,
      nodeId,
      operationId,
    )) {
      return false;
    }
    refreshProjection();
    return true;
  }, [refreshProjection]);

  useEffect(() => {
    tasksRef.current = tasks;
    refreshProjection(tasks);
  }, [refreshProjection, tasks]);

  return {
    tasks,
    setTasks,
    busyNodeIds,
    busyNodeIdsRef,
    optimisticSubmissionsRef,
    applySnapshot,
    refreshProjection,
    beginOptimistic,
    releaseSynchronous,
  };
}
