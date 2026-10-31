import {useEffect, useRef, type RefObject} from 'react';

const FOCUSABLE = [
  'button:not(:disabled)',
  'a[href]',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  '[contenteditable="true"]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function useDialogFocusTrap<T extends HTMLElement>(
  externalRef?: RefObject<T>,
): RefObject<T> {
  const fallbackRef = useRef<T>(null);
  const rootRef = externalRef || fallbackRef;

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const root = rootRef.current;
    // Focus the dialog container, not a text input. This keeps keyboard users
    // inside the modal without violating the canvas rule that inputs never
    // steal focus when a node editor opens.
    root?.focus({preventScroll: true});

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab' || !rootRef.current) return;
      const active = document.activeElement;
      const portalRoot = active instanceof HTMLElement
        ? active.closest<HTMLElement>('[data-dialog-portal="true"]')
        : null;
      if (portalRoot && !event.shiftKey) {
        const portalFocusable = [...portalRoot.querySelectorAll<HTMLElement>(FOCUSABLE)]
          .filter((item) => item.offsetParent !== null || item === document.activeElement);
        const portalLast = portalFocusable[portalFocusable.length - 1];
        if (active === portalLast) {
          event.preventDefault();
          rootRef.current.focus({preventScroll: true});
        }
        return;
      }
      if (portalRoot) {
        return;
      }
      const focusable = [...rootRef.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
        .filter((item) => item.offsetParent !== null || item === document.activeElement);
      if (focusable.length === 0) {
        event.preventDefault();
        rootRef.current.focus({preventScroll: true});
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (active === first || active === rootRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (!rootRef.current.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      if (previous?.isConnected) previous.focus({preventScroll: true});
    };
  }, []);

  return rootRef;
}
