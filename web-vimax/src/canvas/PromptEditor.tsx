export const PROMPT_COMMIT_DELAY_MS = 600;

export type PromptCommitController = {
  schedule: () => void;
  flush: () => void;
  cancel: () => void;
};

export function createPromptCommitController(
  commit: () => void,
  delayMs = PROMPT_COMMIT_DELAY_MS,
): PromptCommitController {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const cancel = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };
  const flush = () => {
    cancel();
    commit();
  };
  const schedule = () => {
    cancel();
    timer = setTimeout(() => {
      timer = null;
      commit();
    }, Math.max(0, delayMs));
  };
  return {schedule, flush, cancel};
}
