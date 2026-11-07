import {describe, expect, it} from 'vitest';
import {
  canvasUploadKind,
  dragFromCanvasNode,
  dragHasFiles,
  prepareCanvasUploadFiles,
  shouldArmCanvasFileDrop,
} from './uploadDragState';


function file(name: string, type = ''): File {
  return new File([name], name, {type});
}

describe('canvas upload drag state', () => {
  it('arms only for external files', () => {
    expect(dragHasFiles({types: ['Files']} as DataTransfer)).toBe(true);
    expect(shouldArmCanvasFileDrop({types: ['Files']} as DataTransfer)).toBe(true);
    expect(shouldArmCanvasFileDrop({
      types: ['Files', 'application/x-vimax-canvas'],
    } as DataTransfer)).toBe(false);
    expect(shouldArmCanvasFileDrop({types: ['text/plain']} as DataTransfer)).toBe(false);
  });

  it('recognizes explicit internal canvas drag types', () => {
    expect(dragFromCanvasNode({
      types: ['application/x-vimax-canvas'],
    } as DataTransfer)).toBe(true);
  });

  it('classifies supported media by MIME and extension', () => {
    expect(canvasUploadKind(file('a.bin', 'image/png'))).toBe('image');
    expect(canvasUploadKind(file('a.mp4'))).toBe('video');
    expect(canvasUploadKind(file('a.flac', 'application/octet-stream'))).toBe('audio');
    expect(canvasUploadKind(file('a.pdf', 'application/pdf'))).toBe(null);
  });

  it('filters unsupported files and caps a batch before enqueueing', () => {
    const prepared = prepareCanvasUploadFiles([
      file('a.png', 'image/png'),
      file('b.pdf', 'application/pdf'),
      file('c.mp4', 'video/mp4'),
    ], 1);
    expect(prepared.accepted.map((item) => item.name)).toEqual(['a.png']);
    expect(prepared.unsupported).toBe(1);
    expect(prepared.overflow).toBe(1);
  });
});
