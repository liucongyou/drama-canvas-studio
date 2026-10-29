import {describe, expect, it} from 'vitest';
import {
  popupRevealShift,
  filterMediaNodesInBounds,
  focusedNodeVisibleBounds,
  shouldUseLargeCanvasOverview,
  viewportForCanvasBounds,
  visibleBoundsForViewport,
} from './canvasViewport';

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
