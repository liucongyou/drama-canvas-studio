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
  if (ratio === 'adaptive') {
    return (
      <svg
        className="video-param-ratio-svg"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <rect
          x="4.25"
          y="4.25"
          width="15.5"
          height="15.5"
          rx="2.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeDasharray="2.5 2.5"
        />
      </svg>
    );
  }
  const match = /^(\d+)\s*:\s*(\d+)$/.exec(ratio);
  const widthRatio = match ? Number(match[1]) : 1;
  const heightRatio = match ? Number(match[2]) : 1;
  const scale = Math.min(16 / widthRatio, 16 / heightRatio);
  const width = Math.max(6, widthRatio * scale);
  const height = Math.max(6, heightRatio * scale);
  const x = (24 - width) / 2;
  const y = (24 - height) / 2;
  return (
    <svg
      className="video-param-ratio-svg"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx="2.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
    </svg>
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
        {countOptions.length > 1 ? (
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
        ) : (
          <div className="video-params-choice-row is-fixed-count">
            <span>单次生成 {countOptions[0] ?? 1} 个结果</span>
          </div>
        )}
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
