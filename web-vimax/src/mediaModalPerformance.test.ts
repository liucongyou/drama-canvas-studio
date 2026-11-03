import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const source = readFileSync(resolve(__dirname, 'CanvasView.tsx'), 'utf8');
const css = readFileSync(resolve(__dirname, 'ui-refinements.css'), 'utf8');

describe('media node modal interaction performance', () => {
  it('opens image and video cards without a click delay', () => {
    expect(source).toMatch(
      /if \(node\.kind === 'image' \|\| node\.kind === 'video'\)[\s\S]*?onEdit\(node\);[\s\S]*?return;/,
    );
    expect(source).not.toMatch(
      /node\.kind === 'image' \|\| node\.kind === 'video'\) \{\s*if \(!editingText\) scheduleOpen\(\);/s,
    );
  });

  it('coordinates popup direction once per meaningful size change', () => {
    expect(source).toMatch(/const popupModeKeyRef = useRef\(''\)/);
    expect(source).toMatch(/if \(popupModeKeyRef\.current === modeKey\) return;/);
    expect(source).toMatch(/requestAnimationFrame\(\(\) => \{[\s\S]*?scrollHeight/s);
  });

  it('does not force blur filters into the modal open/close animations', () => {
    const popupAnimationCss = css.slice(
      css.indexOf('/* ---------- 即梦风提示词栏'),
      css.indexOf('/* 顶部细行 */'),
    );
    expect(popupAnimationCss).not.toMatch(/filter:\s*blur/);
    expect(popupAnimationCss).toMatch(/animation:jim-pop \.18s/);
  });

  it('opens visibly from the card anchor without animating layout', () => {
    expect(css).toMatch(
      /\.jim-bar\[data-anchored='1'\]\{[\s\S]*?transform-origin:bottom center;[\s\S]*?animation:jim-pop-anchored \.18s/,
    );
    expect(css).toMatch(
      /\.jim-bar\[data-anchored='1'\]\[data-flip='1'\]\{[\s\S]*?transform-origin:bottom center;[\s\S]*?animation:jim-pop-anchored-up \.18s/,
    );
    expect(css).toMatch(
      /@keyframes jim-pop-anchored\{[\s\S]*?translateY\(18px\) scale\(\.94\)/,
    );
    expect(css).toMatch(
      /@keyframes jim-pop-anchored-up\{[\s\S]*?translateY\(-18px\) scale\(\.94\)/,
    );
  });

  it('keeps close animation as a visual phase with a bounded fallback', () => {
    expect(source).toMatch(
      /addEventListener\('animationend', finish, \{once: true\}\)/,
    );
    expect(source).toMatch(/setTimeout\(finish, 120\)/);
  });

  it('lets the same card reopen while its previous exit animation is closing', () => {
    expect(source).toMatch(/const \[editModalOpenToken, setEditModalOpenToken\] = useState\(0\)/);
    expect(source).toMatch(/setEditModalOpenToken\(\(token\) => token \+ 1\)/);
    expect(source).toMatch(/if \(closeAttemptRef\.current !== attempt\) return;/);
    expect(source).toMatch(
      /useLayoutEffect\(\(\) => \{[\s\S]*?closeAttemptRef\.current \+= 1;[\s\S]*?\}, \[node\?\.id, openToken\]\)/,
    );
    expect(source).toMatch(
      /if \(closing\) openMediaModal\(nextNode\);/,
    );
  });
});
