import type {Artifact, JsonValue} from './types';

export type StoryboardPreview = {
  id: string;
  description: string;
};

export type ReadinessStatus = 'ready' | 'partial' | 'missing' | 'inactive';

export type StoryboardReadiness = {
  overall: ReadinessStatus;
  readyToRender: boolean;
  storyboards: {status: ReadinessStatus; count: number};
  shotDescriptions: {status: ReadinessStatus; count: number; expected: number};
  cameraPlans: {status: ReadinessStatus; count: number; expected: number};
  render: {
    started: boolean;
    frames: {status: ReadinessStatus; count: number; expected: number};
    clips: {status: ReadinessStatus; count: number; expected: number};
    finalVideo: {status: ReadinessStatus; count: number; expected: number};
  };
};

export type RenderCheckpoint = 'frames' | 'clips' | 'finalVideo';

export type MediaClass = 'final_video' | 'video_clip' | 'key_frame' | 'character' | 'scene' | 'prop' | 'voice' | 'other';

export type MediaOrientation = 'portrait' | 'landscape' | 'square';

/** 依据真实像素宽高判断横屏/竖屏：用于给播放窗口套用正确比例，避免竖屏视频两侧留大黑边。 */
export function resolveMediaOrientation(width: number, height: number): MediaOrientation {
  if (!width || !height) return 'portrait';
  const ratio = width / height;
  if (ratio > 1.08) return 'landscape';
  if (ratio < 0.92) return 'portrait';
  return 'square';
}

export function classifyMediaArtifact(artifact: Artifact): MediaClass {
  const name = artifact.name.toLowerCase();
  const normalizedPath = normalizeArtifactPath(artifact.path);
  if (name === 'final_video.mp4') return 'final_video';
  if (artifact.kind === 'video') return 'video_clip';
  if (artifact.kind === 'audio') return 'voice';
  if (normalizedPath.includes('/character_portraits/')) return 'character';
  if (normalizedPath.includes('/scenes/')) return 'scene';
  if (normalizedPath.includes('/props/')) return 'prop';
  if (artifact.kind === 'image') return 'key_frame';
  return 'other';
}

export function mediaClassLabel(mediaClass: MediaClass): string {
  const labels: Record<MediaClass, string> = {
    final_video: '成片',
    video_clip: '视频片段',
    key_frame: '关键帧',
    character: '角色资产',
    scene: '场景资产',
    prop: '道具资产',
    voice: '音色资产',
    other: '其他',
  };
  return labels[mediaClass];
}

const FIELD_LABELS: Record<string, string> = {
  idx: '序号',
  is_last: '末镜',
  cam_idx: '机位',
  visual_desc: '画面描述',
  visual_description: '画面描述',
  audio_desc: '音频',
  description: '描述',
  ff_desc: '首帧',
  lf_desc: '末帧',
  motion_desc: '动作',
  variation_type: '转场类型',
  variation_reason: '转场说明',
  ff_vis_char_idxs: '首帧角色',
  lf_vis_char_idxs: '末帧角色',
  character_idx: '角色',
  character_id: '角色',
  shot_idx: '镜头',
  scene_idx: '场次',
  camera_idx: '机位',
  // 机位树 / 镜头树字段
  active_shot_idxs: '活动镜头',
  active_shot_idx: '活动镜头',
  parent_cam_idx: '父机位',
  parent_shot_idx: '父镜头',
  parent_idx: '父节点',
  is_parent: '是否父节点',
  fully_covers_child: '完全覆盖子节点',
  is_parent_fully_covers_child: '父节点完全覆盖子节点',
  reason: '原因',
  missing_info: '缺失信息',
  start_ms: '开始时间',
  duration_ms: '时长',
  shot_idxs: '镜头列表',
  char_idxs: '角色列表',
  dst_cam_idx: '目标机位',
  src_cam_idx: '源机位',
  is_transition: '是否转场',
  transition_duration_ms: '转场时长',
  // 通用语义字段
  name: '名称',
  title: '标题',
  style: '风格',
  tone: '色调',
  lighting: '光线',
  mood: '氛围',
  scene_title: '场次标题',
  shot_goals: '镜头目标',
  beats: '节拍',
  dialogue: '对白',
  action: '动作',
  visual_effects: '特效',
  character_name: '角色名',
  props: '道具',
  timing: '时长',
  camera_movement: '运镜',
  composition: '构图',
  background: '背景',
  foreground: '前景',
  // 角色 / 帧选择输出字段
  identifier_in_scene: '场景内标识',
  is_visible: '是否可见',
  static_features: '静态特征',
  dynamic_features: '动态特征',
  reference_image_path_and_text_pairs: '参考图像与文本',
  text_prompt: '图像提示词',
  image_path: '图像路径',
  front: '正面',
  side: '侧面',
  back: '背面',
};

const FILE_TITLES: Record<string, string> = {
  'camera_tree.json': '机位规划',
  'characters.json': '角色',
  'script.json': '剧本',
  'shot_description.json': '镜头描述',
  'storyboard.json': '分镜',
  'character_portraits_registry.json': '角色立绘登记表',
  'characters_registry.json': '角色登记表',
  'first_frame_selector_output.json': '首帧选择输出',
  'last_frame_selector_output.json': '末帧选择输出',
  'storyboard_tree.json': '分镜树',
  'shot_descriptions.json': '镜头描述总表',
  'render_status.json': '渲染状态',
  'render_events.jsonl': '渲染事件',
};

const FILE_TITLE_SUFFIXES: Array<{match: RegExp; label: string}> = [
  {match: /^first_frame_selector_output\.json$/i, label: '首帧选择输出'},
  {match: /^last_frame_selector_output\.json$/i, label: '末帧选择输出'},
  {match: /^shot_description\.json$/i, label: '镜头描述'},
  {match: /^camera_tree\.json$/i, label: '机位规划'},
  {match: /^characters\.json$/i, label: '角色'},
];

/** 文档分组：用于产物页文档列表的分类展示 */
export type DocumentGroupKey = 'script' | 'characters' | 'storyboard' | 'segments' | 'camera' | 'shots' | 'outputs' | 'other';

export const DOCUMENT_GROUPS: Array<{key: DocumentGroupKey; label: string}> = [
  {key: 'script', label: '剧本'},
  {key: 'characters', label: '角色'},
  {key: 'storyboard', label: '分镜'},
  {key: 'segments', label: '段'},
  {key: 'camera', label: '机位'},
  {key: 'shots', label: '镜头描述'},
  {key: 'outputs', label: '渲染输出'},
  {key: 'other', label: '其他'},
];

export function documentGroupKey(artifact: Artifact): DocumentGroupKey {
  const name = artifact.name.toLowerCase();
  const path = normalizeArtifactPath(artifact.path);
  if (name === 'script.json' || path.includes('/script/')) return 'script';
  if (name === 'characters.json' || name.includes('characters_registry') || name.includes('character_portraits_registry')) return 'characters';
  if (name === 'storyboard.json' || name === 'storyboard_tree.json') return 'storyboard';
  if (name === 'segments.json') return 'segments';
  if (name === 'camera_tree.json') return 'camera';
  if (name.endsWith('shot_description.json') || name.endsWith('shot_descriptions.json')) return 'shots';
  if (name.includes('selector_output') || name.includes('render_') || path.includes('/shots/')) return 'outputs';
  return 'other';
}

export function isJsonArtifact(artifact: Artifact): boolean {
  const name = artifact.name.toLowerCase();
  return name.endsWith('.json') && name !== 'render_status.json';
}

export function isStoryboardArtifact(artifact: Artifact): boolean {
  return artifact.name.toLowerCase() === 'storyboard.json';
}

export function relatedVisualArtifacts(documentArtifact: Artifact, artifacts: Artifact[]): Artifact[] {
  const media = artifacts.filter((artifact) => artifact.kind === 'image' || artifact.kind === 'video');
  const documentPath = normalizeArtifactPath(documentArtifact.path);
  const documentDirectory = parentArtifactPath(documentPath);
  const shotDirectory = documentPath.match(/^(.*\/shots\/\d+)(?:\/|$)/i)?.[1];
  const sceneDirectory = documentPath.match(/^(.*\/scene_\d+)(?:\/|$)/i)?.[1];
  const workflowRoot = documentPath.split('/')[0] || '';
  const name = documentArtifact.name.toLowerCase();

  const related = media.filter((artifact) => {
    const mediaPath = normalizeArtifactPath(artifact.path);
    if (shotDirectory) return parentArtifactPath(mediaPath) === shotDirectory;
    if (sceneDirectory) return mediaPath.startsWith(`${sceneDirectory}/`);
    if (name === 'characters.json' || name === 'character_portraits_registry.json') {
      return mediaPath.startsWith(`${workflowRoot}/character_portraits/`);
    }
    if (name === 'script.json') {
      return mediaPath.startsWith(`${workflowRoot}/scene_`) || mediaPath === `${workflowRoot}/final_video.mp4`;
    }
    return parentArtifactPath(mediaPath) === documentDirectory;
  });

  return related.sort((left, right) => left.path.localeCompare(right.path, undefined, {numeric: true}));
}

export function friendlyFieldLabel(key: string): string {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function isArtifactPathField(key: string): boolean {
  const normalized = key.trim().toLowerCase();
  return normalized.split('_').some((part) => part === 'path' || part === 'dir' || part === 'directory');
}

export function friendlyArtifactTitle(artifact: Artifact): string {
  const lowerName = artifact.name.toLowerCase();
  const baseTitle = FILE_TITLES[lowerName]
    || (() => {
      for (const rule of FILE_TITLE_SUFFIXES) {
        if (rule.match.test(lowerName)) return rule.label;
      }
      return artifact.name.replace(/\.json$/i, '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
    })();
  const sceneMatch = artifact.path.match(/(?:^|\/)scene_(\d+)(?:\/|$)/i);
  const shotMatch = artifact.path.match(/(?:^|\/)shots\/(\d+)(?:\/|$)/i);
  const context = [];
  if (sceneMatch) context.push(`第${Number(sceneMatch[1]) + 1}场`);
  if (shotMatch) context.push(`镜头${Number(shotMatch[1]) + 1}`);
  return context.length ? `${context.join(' · ')} · ${baseTitle}` : baseTitle;
}

export function structuredRecordTitle(value: JsonValue, index: number, artifact: Artifact): string {
  if (isJsonObject(value)) {
    for (const key of ['name', 'title', 'character_name', 'scene_title']) {
      const candidate = value[key];
      if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
    }
    const explicitIndex = value.idx;
    if (typeof explicitIndex === 'number') return `${recordNoun(artifact)} ${explicitIndex + 1}`;
  }
  return `${recordNoun(artifact)} ${index + 1}`;
}

export function formatStructuredValue(value: JsonValue, key = ''): string {
  if (value === null) return '未指定';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return isIndexKey(key) ? String(value + 1) : String(value);
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return '未指定';
    if (/^camera_\d+$/i.test(trimmed)) return `机位${trimmed.replace(/^camera_(\d+)$/i, '$1')}`;
    return trimmed;
  }
  if (Array.isArray(value) && value.every(isJsonPrimitive)) {
    if (value.length === 0) return '无';
    return value.map((item) => typeof item === 'number' && isIndexListKey(key) ? item + 1 : formatStructuredValue(item, key)).join(', ');
  }
  return '';
}

export function extractStoryboardPreviews(document: JsonValue, sourcePath: string): StoryboardPreview[] {
  const previews: StoryboardPreview[] = [];

  function visit(value: JsonValue) {
    if (Array.isArray(value)) {
      value.forEach(visit);
      return;
    }
    if (!isJsonObject(value)) return;
    const description = ['visual_desc', 'visual_description', 'description']
      .map((key) => value[key])
      .find((candidate) => typeof candidate === 'string' && candidate.trim());
    if (typeof description === 'string') {
      previews.push({id: `${sourcePath}:${previews.length}`, description: description.trim()});
      return;
    }
    for (const key of ['storyboards', 'storyboard', 'shots', 'scenes', 'items']) {
      const nested = value[key];
      if (nested !== undefined) visit(nested);
    }
  }

  visit(document);
  return previews;
}

export function deriveStoryboardReadiness(artifacts: Artifact[], storyboardCount: number): StoryboardReadiness {
  const storyboardFiles = artifacts.filter(isStoryboardArtifact).length;
  const shotDescriptions = artifacts.filter((artifact) => artifact.name.toLowerCase() === 'shot_description.json').length;
  const cameraPlans = artifacts.filter((artifact) => artifact.name.toLowerCase() === 'camera_tree.json').length;
  const frames = artifacts.filter((artifact) => artifact.name.toLowerCase() === 'first_frame.png').length;
  const clips = artifacts.filter((artifact) => artifact.name.toLowerCase() === 'video.mp4').length;
  const finalVideos = artifacts.filter((artifact) => artifact.name.toLowerCase() === 'final_video.mp4').length;
  const storyboards = checkpointStatus(storyboardCount, Math.max(storyboardFiles, 1));
  const shots = checkpointStatus(shotDescriptions, storyboardCount);
  const cameras = checkpointStatus(cameraPlans, storyboardFiles);
  const readyToRender = storyboardCount > 0
    && storyboards === 'ready'
    && shots === 'ready'
    && cameras === 'ready';
  const hasPlanningOutput = storyboardCount > 0 || shotDescriptions > 0 || cameraPlans > 0;

  return {
    overall: readyToRender ? 'ready' : hasPlanningOutput ? 'partial' : 'missing',
    readyToRender,
    storyboards: {status: storyboards, count: storyboardCount},
    shotDescriptions: {status: shots, count: shotDescriptions, expected: storyboardCount},
    cameraPlans: {status: cameras, count: cameraPlans, expected: storyboardFiles},
    render: {
      started: frames > 0 || clips > 0 || finalVideos > 0,
      frames: {status: renderCheckpointStatus(frames, storyboardCount), count: frames, expected: storyboardCount},
      clips: {status: renderCheckpointStatus(clips, storyboardCount), count: clips, expected: storyboardCount},
      finalVideo: {status: renderCheckpointStatus(finalVideos, 1), count: finalVideos, expected: 1},
    },
  };
}

export function activeRenderCheckpoint(stage: string): RenderCheckpoint {
  const normalized = stage.toLowerCase();
  if (normalized.includes('video_clip')) return 'clips';
  if (normalized.includes('concat') || normalized.includes('final_video') || normalized === 'render_done') return 'finalVideo';
  return 'frames';
}

export function isJsonObject(value: JsonValue): value is {[key: string]: JsonValue} {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function isJsonPrimitive(value: JsonValue): value is string | number | boolean | null {
  return value === null || ['string', 'number', 'boolean'].includes(typeof value);
}

function recordNoun(artifact: Artifact): string {
  const name = artifact.name.toLowerCase();
  if (name === 'storyboard.json' || name === 'shot_description.json') return '镜头';
  if (name === 'characters.json') return '角色';
  if (name === 'script.json') return '场次';
  return '条目';
}

function isIndexKey(key: string): boolean {
  return key === 'idx' || key.endsWith('_idx') || key.endsWith('_index');
}

function isIndexListKey(key: string): boolean {
  return key.endsWith('_idxs') || key.endsWith('_indices');
}

function checkpointStatus(count: number, expected: number): ReadinessStatus {
  if (expected <= 0 || count <= 0) return 'missing';
  return count >= expected ? 'ready' : 'partial';
}

function renderCheckpointStatus(count: number, expected: number): ReadinessStatus {
  if (count <= 0) return 'inactive';
  return expected > 0 && count >= expected ? 'ready' : 'partial';
}

function normalizeArtifactPath(path: string): string {
  return path.replace(/\\/g, '/').replace(/^\.\//, '');
}

function parentArtifactPath(path: string): string {
  const separator = path.lastIndexOf('/');
  return separator >= 0 ? path.slice(0, separator) : '';
}
