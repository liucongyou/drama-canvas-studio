import type {CanvasMediaParams} from './api';

type VideoParamsPanelProps = {
  params: CanvasMediaParams;
  ratioOptions: string[];
  resolutionOptions: string[];
  countOptions: number[];
  ratioLabel: string;
  resolutionLabel: string;
  countLabel: string;
  onPatch: (key: keyof CanvasMediaParams, value: unknown) => void;
};

function RatioGlyph({ratio}: {ratio: string}) {
  const match = /^(\d+)\s*:\s*(\d+)$/.exec(ratio);
  const raw = match ? Number(match[1]) / Number(match[2]) : 1;
  const width = raw >= 1 ? 18 : Math.max(8, Math.round(18 * raw));
  const height = raw >= 1 ? Math.max(8, Math.round(18 / raw)) : 18;
  return (
    <span
      className="video-param-ratio-glyph"
      style={{width, height}}
      aria-hidden="true"
    />
  );
}

export function VideoParamsPanel({
  params,
  ratioOptions,
  resolutionOptions,
  countOptions,
  ratioLabel,
  resolutionLabel,
  countLabel,
  onPatch,
}: VideoParamsPanelProps) {
  return (
    <div className="video-params-compact">
      <section className="video-params-group">
        <div className="video-params-label">选择比例</div>
        <div
          className="video-params-ratio-row"
          style={{gridTemplateColumns: `repeat(${Math.max(1, ratioOptions.length)}, minmax(0, 1fr))`}}
        >
          {ratioOptions.map((ratio) => (
            <button
              key={ratio}
              type="button"
              className={ratioLabel === ratio ? 'is-active' : ''}
              onClick={() => onPatch('ratio', ratio)}
            >
              <RatioGlyph ratio={ratio} />
              <span>{ratio}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="video-params-group">
        <div className="video-params-label">选择分辨率</div>
        <div
          className="video-params-choice-row"
          style={{gridTemplateColumns: `repeat(${Math.max(1, resolutionOptions.length)}, minmax(0, 1fr))`}}
        >
          {resolutionOptions.map((resolution) => (
            <button
              key={resolution}
              type="button"
              className={resolutionLabel === resolution ? 'is-active' : ''}
              onClick={() => onPatch('resolution', resolution)}
            >
              {resolution}
            </button>
          ))}
        </div>
      </section>

      <section className="video-params-group">
        <div className="video-params-label">选择生成数量</div>
        <div
          className="video-params-choice-row is-count"
          style={{gridTemplateColumns: `repeat(${Math.max(1, countOptions.length)}, minmax(0, 1fr))`}}
        >
          {countOptions.map((count) => (
            <button
              key={count}
              type="button"
              className={String(countLabel) === String(count) ? 'is-active' : ''}
              onClick={() => onPatch('count', count)}
            >
              {count}
            </button>
          ))}
        </div>
      </section>

      {params.generateAudio !== undefined && (
        <div className="video-params-audio-toggle">
          <span>生成音频</span>
          <button
            type="button"
            role="switch"
            aria-checked={Boolean(params.generateAudio)}
            className={`video-params-switch ${params.generateAudio ? 'is-on' : ''}`}
            onClick={() => onPatch('generateAudio', !params.generateAudio)}
          >
            <i />
          </button>
        </div>
      )}
    </div>
  );
}
