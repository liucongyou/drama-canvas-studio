import type {CanvasMediaNodeKind} from './api';

export type PromptMentionToken = {
  label: string;
  url?: string;
  kind: CanvasMediaNodeKind;
};

export type PromptMentionMatch = PromptMentionToken & {
  start: number;
  end: number;
  exact: boolean;
};

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isDialogueLine(value: string, offset: number): boolean {
  const lineStart = Math.max(
    value.lastIndexOf('\n', offset - 1) + 1,
    value.lastIndexOf('\r', offset - 1) + 1,
  );
  const prefix = value.slice(lineStart, offset).trimStart();
  return /^(?:【\s*)?台词\s*[】:：]/.test(prefix);
}

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
      matches.push({...mention, start, end: start + token.length, exact: true});
      from = start + token.length;
    }
  }

  // Legacy prompts may contain "name（图N）". That is only a media reference
  // outside dialogue lines. Dialogue text such as
  // "台词：【钱万金（图2）：..." must remain literal script text.
  for (const mention of mentions) {
    const figure = /[（(]\s*(?:参考)?图\s*(\d+)\s*[）)]/.exec(mention.label);
    if (!figure) continue;
    const withoutPrefix = mention.label.replace(/^[^｜|]*[｜|]\s*/, '');
    const base = withoutPrefix
      .replace(/[（(]\s*(?:参考)?图\s*\d+\s*[）)]\s*$/, '')
      .trim();
    const candidates = [
      ...new Set([
        base,
        base.split(/[·・]/)[0]?.trim() || '',
      ].filter(Boolean)),
    ];
    for (const candidate of candidates) {
      const pattern = new RegExp(
        `(?<![A-Za-z0-9_\\u3400-\\u9fff@｜|])${escapeRegExp(candidate)}`
        + `\\s*[（(]\\s*(?:参考)?图\\s*${figure[1]}\\s*[）)]`,
        'gu',
      );
      for (const match of value.matchAll(pattern)) {
        const start = match.index ?? 0;
        if (isDialogueLine(value, start)) continue;
        matches.push({
          ...mention,
          start,
          end: start + match[0].length,
          exact: false,
        });
      }
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
