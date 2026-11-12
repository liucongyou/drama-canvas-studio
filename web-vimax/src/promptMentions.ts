import type {CanvasMediaNodeKind} from './api';

export type PromptMentionToken = {
  label: string;
  url?: string;
  kind: CanvasMediaNodeKind;
  role?: string;
};

export type PromptMentionMatch = PromptMentionToken & {
  start: number;
  end: number;
  exact: boolean;
};

export function promptMentionMatches(
  value: string,
  mentions: PromptMentionToken[],
): PromptMentionMatch[] {
  const matches: PromptMentionMatch[] = [];
  for (const mention of mentions) {
    if (!mention.label) continue;
    const token = `@${mention.label}`;
    let from = 0;
    while (from < value.length) {
      const start = value.indexOf(token, from);
      if (start < 0) break;
      if (start > 0 && value[start - 1] === '<') {
        from = start + token.length;
        continue;
      }
      matches.push({...mention, start, end: start + token.length, exact: true});
      from = start + token.length;
    }
  }

  matches.sort((left, right) => (
    left.start - right.start
    || Number(right.exact) - Number(left.exact)
    || right.end - left.end
  ));
  const accepted: PromptMentionMatch[] = [];
  let consumed = -1;
  for (const match of matches) {
    if (match.start < consumed) continue;
    accepted.push(match);
    consumed = match.end;
  }
  return accepted;
}
