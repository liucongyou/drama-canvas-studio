import type {GenerationTask} from './api';

/**
 * The backend owns task resolution eligibility. The UI only renders the
 * explicit confirmation capability returned in the task projection.
 */
export function manualTaskResolutionAvailable(task: GenerationTask): boolean {
  return task.outcome?.canResolve === true
    && (task.status === 'failed' || task.status === 'interrupted');
}
