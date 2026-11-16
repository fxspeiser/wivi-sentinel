import { useState, useEffect, useRef, useMemo, useCallback } from 'react';

/**
 * Signal Lab — live view of analysis pipeline v2 (engine/analysis.py).
 *
 * Shows what the radio is actually measuring (signal health, motion
 * spectrogram, subcarrier heatmap, high-resolution PSD), the v2 rate estimates
 * beside the original pipeline's, and enrolled-roster identification with its
 * validation evidence. Everything here is additive: the profile cards, radar
 * and filters are untouched.
 */

// ─── Palette ────────────────────────────────────────────────────────────────
// Sequential = one hue (blue) from surface to bright; diverging = blue ↔ red
// around a neutral midpoint; categorical = fixed slot order, never cycled.

const PALETTE = {
  dark: {
    sequential: ['#0b1120', '#0d366b', '#184f95', '#256abf', '#3987e5', '#86b6ef', '#cde2fb'],
    diverging:  ['#3987e5', '#184f95', '#1e293b', '#8f2f2f', '#e66767'],
    series:     ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
    other:      '#64748b',
  },
  light: {
    sequential: ['#ffffff', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#1c5cab', '#0d366b'],
    diverging:  ['#1c5cab', '#86b6ef', '#f0efec', '#eba3a3', '#b02a2a'],
    series:     ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
    other:      '#94a3b8',
  },
};
const STATUS = { good: '#0ca30c', warning: '#fab219', critical: '#d03b3b' };
const MONO = "'JetBrains Mono', monospace";

const hexRgb = (hex) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const rgba = (hex, a) => { const [r, g, b] = hexRgb(hex); return `rgba(${r},${g},${b},${a})`; };

/** 256-entry RGB lookup table interpolated through the given colour stops. */
function buildLut(stops) {
  const rgb = stops.map(hexRgb);
  const lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) {
    const x = (i / 255) * (rgb.length - 1);
    const k = Math.min(Math.floor(x), rgb.length - 2), f = x - k;
    for (let c = 0; c < 3; c++) lut[i * 3 + c] = Math.round(rgb[k][c] + (rgb[k + 1][c] - rgb[k][c]) * f);
  }
  return lut;
}

function decodeBase64(data) {
  const bin = atob(data || '');
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(d));

// ─── Canvas plumbing ────────────────────────────────────────────────────────

/** Canvas that tracks its container width and redraws on data/theme change. */
function PlotCanvas({ height, draw, deps, onHover, label }) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = canvasRef.current; if (!c || width < 40) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr; c.height = height * dpr;
    const ctx = c.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    draw(ctx, width, height);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [width, height, ...deps]);

  const handleMove = (e) => {
    if (!onHover) return;
    const r = canvasRef.current.getBoundingClientRect();
    onHover({ x: e.clientX - r.left, y: e.clientY - r.top, width: r.width, height: r.height });
  };

  return (
    <div ref={wrapRef} style={{ position: 'relative', width: '100%' }}>
      <canvas
        ref={canvasRef} role="img" aria-label={label}
        onMouseMove={handleMove} onMouseLeave={() => onHover && onHover(null)}
        style={{ width: '100%', height, display: 'block', cursor: onHover ? 'crosshair' : 'default' }}
      />
    </div>
  );
}

function Tooltip({ t, tip }) {
  if (!tip) return null;
  const flip = tip.x > tip.width * 0.6;
  return (
    <div style={{
      position: 'absolute', top: Math.max(0, tip.y - 8), pointerEvents: 'none', zIndex: 5,
      ...(flip ? { right: tip.width - tip.x + 12 } : { left: tip.x + 12 }),
      background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 4,
      padding: '5px 8px', fontSize: 10, lineHeight: 1.5, color: t.textPrimary,
      whiteSpace: 'nowrap', boxShadow: '0 4px 14px rgba(0,0,0,0.35)',
    }}>
      {tip.lines.map((l, i) => (
        <div key={i} style={{ color: i === 0 ? t.textPrimary : t.textSecondary, fontWeight: i === 0 ? 700 : 500 }}>{l}</div>
      ))}
    </div>
  );
}

function Panel({ t, title, subtitle, right, children, style }) {
  return (
    <section style={{ background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 6, padding: 12, minWidth: 0, ...style }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: subtitle ? 2 : 8 }}>
        <h3 style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.12em', color: t.textMid }}>{title}</h3>
        {right}
      </div>
      {subtitle && <div style={{ fontSize: 9, color: t.textMuted, lineHeight: 1.45, marginBottom: 8 }}>{subtitle}</div>}
      {children}
    </section>
  );
}

function ScaleLegend({ t, lutStops, left, right }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 9, color: t.textMuted, marginTop: 6 }}>
      <span>{left}</span>
      <span style={{ flex: '0 0 110px', height: 6, borderRadius: 3, border: `1px solid ${t.borderDim}`, background: `linear-gradient(90deg, ${lutStops.join(',')})` }} />
      <span>{right}</span>
    </div>
  );
}

const AXIS = { left: 34, right: 6, top: 6, bottom: 18 };

function drawAxes(ctx, t, w, h, { xTicks, yTicks }) {
  ctx.font = `9px ${MONO}`;
  ctx.fillStyle = t.textMuted;
  ctx.strokeStyle = t.borderDim;
  ctx.lineWidth = 1;
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  yTicks.forEach(({ pos, text }) => {
    const y = AXIS.top + pos * (h - AXIS.top - AXIS.bottom);
    ctx.fillText(text, AXIS.left - 5, y);
    ctx.beginPath(); ctx.moveTo(AXIS.left, y + 0.5); ctx.lineTo(w - AXIS.right, y + 0.5); ctx.stroke();
  });
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  xTicks.forEach(({ pos, text }) => {
    ctx.fillText(text, AXIS.left + pos * (w - AXIS.left - AXIS.right), h - AXIS.bottom + 5);
  });
}

// ─── Image plots (spectrogram, heatmap) ─────────────────────────────────────

function ImagePlot({ t, image, lut, height, flipY, yTicks, describe, label }) {
  const [tip, setTip] = useState(null);
  const pixels = useMemo(() => (image?.data ? decodeBase64(image.data) : null), [image?.data]);
  const rows = image?.rows || 0, cols = image?.cols || 0;

  const draw = useCallback((ctx, w, h) => {
    const pw = w - AXIS.left - AXIS.right, ph = h - AXIS.top - AXIS.bottom;
    if (pixels && rows && cols && pixels.length === rows * cols) {
      const off = document.createElement('canvas');
      off.width = cols; off.height = rows;
      const octx = off.getContext('2d');
      const img = octx.createImageData(cols, rows);
      for (let r = 0; r < rows; r++) {
        const src = (flipY ? rows - 1 - r : r) * cols;
        for (let c = 0; c < cols; c++) {
          const v = pixels[src + c] * 3, o = (r * cols + c) * 4;
          img.data[o] = lut[v]; img.data[o + 1] = lut[v + 1]; img.data[o + 2] = lut[v + 2]; img.data[o + 3] = 255;
        }
      }
      octx.putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(off, AXIS.left, AXIS.top, pw, ph);
    }
    const secs = image?.seconds || 0;
    drawAxes(ctx, t, w, h, {
      yTicks,
      xTicks: [0, 0.25, 0.5, 0.75, 1].map(p => ({ pos: p, text: p === 1 ? 'now' : `-${Math.round(secs * (1 - p))}s` })),
    });
  }, [pixels, rows, cols, lut, flipY, yTicks, image?.seconds, t]);

  const onHover = (pos) => {
    if (!pos || !pixels || !rows) return setTip(null);
    const fx = (pos.x - AXIS.left) / (pos.width - AXIS.left - AXIS.right);
    const fy = (pos.y - AXIS.top) / (pos.height - AXIS.top - AXIS.bottom);
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return setTip(null);
    const col = Math.min(cols - 1, Math.floor(fx * cols));
    const rowDrawn = Math.min(rows - 1, Math.floor(fy * rows));
    const row = flipY ? rows - 1 - rowDrawn : rowDrawn;
    setTip({ ...pos, lines: describe({ fx, row, value: pixels[row * cols + col] }) });
  };

  return (
    <div style={{ position: 'relative' }}>
      <PlotCanvas height={height} draw={draw} deps={[draw]} onHover={onHover} label={label} />
      <Tooltip t={t} tip={tip} />
    </div>
  );
}

function Spectrogram({ t, pal, data, health }) {
  const lut = useMemo(() => buildLut(pal.sequential), [pal]);
  const fmax = data?.f_max_hz || 50, range = data?.range_db || 45, hzPerMps = data?.hz_per_mps || 16.4;
  const yTicks = useMemo(() => [0, 0.25, 0.5, 0.75, 1].map(p => ({ pos: p, text: `${Math.round(fmax * (1 - p))}` })), [fmax]);
  return (
    <Panel
      t={t} title="MOTION SPECTROGRAM"
      subtitle={`How fast the channel is changing, over the last ${fmt(data?.seconds, 0)} s. Faster body parts sit higher. Amplitude only: it shows speed, not direction.`}
      right={<span style={{ fontSize: 9, color: t.textMuted }}>Hz</span>}
    >
      <ImagePlot
        t={t} image={data} lut={lut} height={190} flipY yTicks={yTicks}
        label="Motion spectrogram: frequency over time"
        describe={({ fx, row, value }) => {
          const hz = (row / Math.max(1, (data.rows - 1))) * fmax;
          return [
            `${fmt(hz, 1)} Hz`,
            `≈ ${fmt(hz / hzPerMps, 2)} m/s if moving along the link`,
            `${fmt((value / 255) * range - range, 0)} dB re. peak`,
            `${fmt((1 - fx) * (data.seconds || 0), 1)} s ago`,
          ];
        }}
      />
      <ScaleLegend t={t} lutStops={pal.sequential} left={`-${range} dB`} right="peak" />
      {health && (
        <div style={{ fontSize: 9, color: t.textMuted, marginTop: 5, lineHeight: 1.45 }}>
          Sampling at {fmt(health.fs_hz, 0)} Hz resolves motion up to {fmt(health.nyquist_hz, 0)} Hz
          ({fmt(health.max_unaliased_speed_mps, 2)} m/s). Faster limbs fold back into this band.
        </div>
      )}
    </Panel>
  );
}

function SubcarrierHeatmap({ t, pal, data }) {
  const lut = useMemo(() => buildLut(pal.diverging), [pal]);
  const rows = data?.rows || 52, scale = data?.scale_pct || 0;
  const yTicks = useMemo(() => [0, 0.5, 1].map(p => ({ pos: p, text: `${Math.round(p * (rows - 1))}` })), [rows]);
  return (
    <Panel
      t={t} title="SUBCARRIER HEATMAP"
      subtitle="Each row is one WiFi subcarrier; colour is its change from its own resting level. A body moving through the link shows as ripples across rows."
      right={<span style={{ fontSize: 9, color: t.textMuted }}>subcarrier #</span>}
    >
      <ImagePlot
        t={t} image={data} lut={lut} height={190} yTicks={yTicks}
        label="Subcarrier amplitude deviation over time"
        describe={({ fx, row, value }) => [
          `Subcarrier ${row}`,
          `${value >= 128 ? '+' : ''}${fmt(((value - 127.5) / 127.5) * scale, 2)}% vs resting level`,
          `${fmt((1 - fx) * (data.seconds || 0), 1)} s ago`,
        ]}
      />
      <ScaleLegend t={t} lutStops={pal.diverging} left={`-${fmt(scale, 1)}%`} right={`+${fmt(scale, 1)}%`} />
    </Panel>
  );
}

// ─── PSD ────────────────────────────────────────────────────────────────────

const PSD_FLOOR_DB = -60;

function PsdPlot({ t, pal, psd, vitals }) {
  const [tip, setTip] = useState(null);
  const fmax = psd?.freqs?.length ? psd.freqs[psd.freqs.length - 1] : 5;
  const marks = useMemo(() => {
    if (!vitals) return [];
    return [['respiration', 'breaths'], ['heart', 'beats'], ['cadence', 'steps']]
      .filter(([k]) => vitals[k]?.valid)
      .map(([k, unit]) => ({ hz: vitals[k].hz, text: `${fmt(vitals[k].per_min, 0)} ${unit}/min` }));
  }, [vitals]);

  const draw = useCallback((ctx, w, h) => {
    const pw = w - AXIS.left - AXIS.right, ph = h - AXIS.top - AXIS.bottom;
    const X = hz => AXIS.left + (hz / fmax) * pw;
    const Y = db => AXIS.top + (Math.max(PSD_FLOOR_DB, Math.min(0, db)) / PSD_FLOOR_DB) * ph;
    drawAxes(ctx, t, w, h, {
      yTicks: [0, -20, -40, -60].map(db => ({ pos: db / PSD_FLOOR_DB, text: `${db}` })),
      xTicks: [0, 1, 2, 3, 4, 5].filter(f => f <= fmax + 0.01).map(f => ({ pos: f / fmax, text: `${f}` })),
    });
    if (!psd?.freqs?.length) return;

    // Analysis bands: labelled brackets along the top rather than overlapping fills.
    ctx.font = `8px ${MONO}`; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    [['respiration', 'RESP'], ['heart', 'HEART'], ['cadence', 'GAIT']].forEach(([key, name], i) => {
      const [lo, hi] = psd.bands[key];
      const y = AXIS.top + 5 + i * 9;
      ctx.strokeStyle = t.textDim; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(X(lo), y); ctx.lineTo(X(Math.min(hi, fmax)), y); ctx.stroke();
      ctx.fillStyle = t.textMuted;
      ctx.fillText(name, X(Math.min(hi, fmax)) + 4, y);
    });

    // Original pipeline: 256-sample Welch — few, widely spaced points.
    ctx.strokeStyle = pal.series[1]; ctx.fillStyle = pal.series[1]; ctx.lineWidth = 2;
    ctx.beginPath();
    psd.legacy_freqs.forEach((f, i) => { const x = X(f), y = Y(psd.legacy_db[i]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();
    psd.legacy_freqs.forEach((f, i) => { ctx.beginPath(); ctx.arc(X(f), Y(psd.legacy_db[i]), 4, 0, Math.PI * 2); ctx.fill(); });

    // v2: long-window spectrum.
    ctx.strokeStyle = pal.series[0]; ctx.lineWidth = 2;
    ctx.beginPath();
    psd.freqs.forEach((f, i) => { const x = X(f), y = Y(psd.db[i]); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.stroke();

    // Valid estimates, labelled directly.
    ctx.font = `9px ${MONO}`; ctx.textBaseline = 'bottom';
    marks.forEach((m, i) => {
      const x = X(m.hz);
      ctx.strokeStyle = t.textSecondary; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x + 0.5, AXIS.top + 34); ctx.lineTo(x + 0.5, h - AXIS.bottom); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = t.textPrimary;
      ctx.textAlign = x > w - 90 ? 'right' : 'left';
      ctx.fillText(m.text, x + (x > w - 90 ? -4 : 4), h - AXIS.bottom - 3 - i * 11);
    });

    if (tip) {
      ctx.strokeStyle = t.textMuted; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(tip.x + 0.5, AXIS.top); ctx.lineTo(tip.x + 0.5, h - AXIS.bottom); ctx.stroke();
    }
  }, [psd, fmax, marks, pal, t, tip]);

  const onHover = (pos) => {
    if (!pos || !psd?.freqs?.length) return setTip(null);
    const fx = (pos.x - AXIS.left) / (pos.width - AXIS.left - AXIS.right);
    if (fx < 0 || fx > 1) return setTip(null);
    const hz = fx * fmax;
    const nearest = (fs) => fs.reduce((b, f, i) => (Math.abs(f - hz) < Math.abs(fs[b] - hz) ? i : b), 0);
    const i = nearest(psd.freqs), j = nearest(psd.legacy_freqs);
    setTip({ ...pos, lines: [
      `${fmt(hz, 2)} Hz · ${fmt(hz * 60, 0)} per min`,
      `v2: ${fmt(psd.db[i], 1)} dB`,
      `original: ${fmt(psd.legacy_db[j], 1)} dB (nearest point ${fmt(psd.legacy_freqs[j], 2)} Hz)`,
    ] });
  };

  const perMin = hz => fmt(hz * 60, 1);
  return (
    <Panel
      t={t} title="RATE SPECTRUM (0–5 Hz)"
      subtitle={psd ? `Where breathing, pulse and step rates are read from. The original 256-sample estimate can only answer in ${perMin(psd.legacy_resolution_hz)}-per-minute steps; the v2 window resolves ${perMin(psd.resolution_hz)}.` : 'Waiting for data…'}
      right={<span style={{ fontSize: 9, color: t.textMuted }}>dB re. peak</span>}
    >
      <div style={{ position: 'relative' }}>
        <PlotCanvas height={200} draw={draw} deps={[draw]} onHover={onHover} label="Power spectral density, original versus v2" />
        <Tooltip t={t} tip={tip} />
      </div>
      <div style={{ display: 'flex', gap: 14, fontSize: 9, color: t.textSecondary, marginTop: 4 }}>
        {[[pal.series[0], 'v2 · long window'], [pal.series[1], 'original · 256-sample Welch']].map(([c, l]) => (
          <span key={l} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 14, height: 2, background: c, display: 'inline-block' }} />{l}
          </span>
        ))}
        <span style={{ marginLeft: 'auto', color: t.textMuted }}>Hz</span>
      </div>
    </Panel>
  );
}

// ─── Health, motion, vitals ─────────────────────────────────────────────────

function StatusChip({ t, tone, icon, text }) {
  const color = { good: STATUS.good, warning: STATUS.warning, critical: STATUS.critical, muted: t.textMuted }[tone];
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 9, fontWeight: 700, color: t.textSecondary, letterSpacing: '0.04em' }}>
      <span style={{ color }} aria-hidden="true">{icon}</span>{text}
    </span>
  );
}

function Tile({ t, label, value, unit, note, tone }) {
  const mark = tone && tone !== 'good' ? { warning: ['⚠', STATUS.warning], critical: ['✕', STATUS.critical] }[tone] : null;
  return (
    <div style={{ background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 6, padding: '8px 10px', minWidth: 0 }}>
      <div style={{ fontSize: 8, color: t.textSecondary, letterSpacing: '0.1em', fontWeight: 600, marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 800, color: t.textPrimary, lineHeight: 1.1, whiteSpace: 'nowrap' }}>
        {mark && <span style={{ color: mark[1], fontSize: 12, marginRight: 4 }} aria-hidden="true">{mark[0]}</span>}
        {value}<span style={{ fontSize: 9, fontWeight: 600, color: t.textMuted, marginLeft: 3 }}>{unit}</span>
      </div>
      {note && <div style={{ fontSize: 8, color: t.textMuted, marginTop: 3, lineHeight: 1.35 }}>{note}</div>}
    </div>
  );
}

const MOTION_COPY = {
  quiet:   ['QUIET',   'Nothing above the empty-room floor'],
  still:   ['STILL',   'Someone present, not walking'],
  moving:  ['MOVING',  'Walking detected'],
  unknown: ['—',       'Learning the noise floor…'],
};

function HealthStrip({ t, health, motion }) {
  const fsOff = health && Math.abs(health.fs_hz - health.fs_nominal_hz) / health.fs_nominal_hz > 0.1;
  const [mLabel, mNote] = MOTION_COPY[motion?.state] || MOTION_COPY.unknown;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(118px, 1fr))', gap: 8 }}>
      <Tile t={t} label="FRAME RATE" value={fmt(health?.fs_hz, 1)} unit="Hz" tone={fsOff ? 'warning' : 'good'}
            note={fsOff ? `Original pipeline assumes ${fmt(health.fs_nominal_hz, 0)} Hz` : health?.timestamps_valid ? 'Measured from frame timestamps' : 'No usable timestamps — assumed'} />
      <Tile t={t} label="JITTER" value={fmt(health?.jitter_ms, 2)} unit="ms" note="Spread of the frame interval" />
      <Tile t={t} label="FRAME LOSS" value={fmt(health?.loss_pct, 1)} unit="%" tone={health?.loss_pct > 5 ? 'warning' : 'good'}
            note={`${health?.gaps ?? 0} gap${health?.gaps === 1 ? '' : 's'} in buffer`} />
      <Tile t={t} label="SUBCARRIERS" value={health?.subcarriers ?? '—'} note={`${fmt(health?.outlier_pct, 2)}% spikes removed`} />
      <Tile t={t} label="LINK RSSI" value={health?.rssi_dbm ?? '—'} unit="dBm" note="Router link, not range to a person" />
      <Tile t={t} label="BUFFER" value={fmt(health?.buffer_s, 0)} unit="s" note="Respiration needs 20 s+" />
      <Tile t={t} label="MOTION" value={mLabel} note={`${mNote}${motion?.ratio != null ? ` · ${fmt(motion.ratio, 1)}× floor` : ''}`} />
    </div>
  );
}

function vitalStatus(t, v) {
  if (!v) return <StatusChip t={t} tone="muted" icon="·" text="NO DATA" />;
  if (v.valid) return <StatusChip t={t} tone="good" icon="✓" text="VALID" />;
  if (v.gated) return <StatusChip t={t} tone="muted" icon="⏸" text={`GATED · ${v.gate_reason}`} />;
  return <StatusChip t={t} tone="warning" icon="⚠" text={(v.gate_reason || 'weak').toUpperCase()} />;
}

function VitalsTable({ t, vitals, legacy, motion, onBaseline, classifierInputs }) {
  const rows = [
    ['Respiration', 'respiration', 'breaths/min', legacy?.respiratory_rate],
    ['Heart rate',  'heart',       'BPM',         legacy?.bpm],
    ['Step cadence', 'cadence',    'steps/min',   legacy?.cadence_spm],
  ];
  const th = { fontSize: 8, fontWeight: 700, letterSpacing: '0.1em', color: t.textMuted, textAlign: 'right', padding: '0 0 5px 8px' };
  const td = { fontSize: 11, padding: '5px 0 5px 8px', textAlign: 'right', borderTop: `1px solid ${t.borderDim}`, color: t.textPrimary, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' };
  return (
    <Panel
      t={t} title="RATES · ORIGINAL vs v2"
      subtitle={`Both pipelines read the same frames. Species and sex are currently fed the ${classifierInputs === 'v2' ? 'v2' : 'original'} values (Admin controls → “Feed v2 rates to classifiers”).`}
    >
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={{ ...th, textAlign: 'left', paddingLeft: 0 }}>MEASURE</th>
            <th style={th}>ORIGINAL</th><th style={th}>v2</th><th style={th}>± STEP</th><th style={th}>SNR</th>
            <th style={{ ...th, textAlign: 'left' }}>v2 STATUS</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, key, unit, old]) => {
            const v = vitals?.[key];
            return (
              <tr key={key}>
                <td style={{ ...td, textAlign: 'left', paddingLeft: 0, fontSize: 10, color: t.textSecondary }}>
                  {name}{v?.experimental && <span style={{ color: t.textMuted }}> · experimental</span>}
                  <div style={{ fontSize: 8, color: t.textMuted }}>{unit}</div>
                </td>
                <td style={{ ...td, color: t.textSecondary }}>{old != null ? fmt(old, 1) : '—'}</td>
                <td style={{ ...td, fontWeight: 700, color: v?.valid ? t.textPrimary : t.textMuted }}>{v?.per_min ? fmt(v.per_min, 1) : '—'}</td>
                <td style={{ ...td, color: t.textSecondary }}>{v?.resolution_hz ? fmt(v.resolution_hz * 60, 1) : '—'}</td>
                <td style={{ ...td, color: t.textSecondary }}>{v?.snr_db ? `${fmt(v.snr_db, 0)} dB` : '—'}</td>
                <td style={{ ...td, textAlign: 'left' }}>{vitalStatus(t, v)}</td>
              </tr>
            );
          })}
          <tr>
            <td style={{ ...td, textAlign: 'left', paddingLeft: 0, fontSize: 10, color: t.textSecondary }}>Harmonic ratio<div style={{ fontSize: 8, color: t.textMuted }}>2nd / 1st step harmonic</div></td>
            <td style={{ ...td, color: t.textSecondary }}>{legacy?.harmonic_ratio != null ? fmt(legacy.harmonic_ratio, 2) : '—'}</td>
            <td style={{ ...td, fontWeight: 700 }}>{vitals ? fmt(vitals.harmonic_ratio, 2) : '—'}</td>
            <td style={td} /><td style={td} />
            <td style={{ ...td, textAlign: 'left', fontSize: 9, color: t.textMuted, whiteSpace: 'normal' }}>v2 reads it before band-pass filtering</td>
          </tr>
        </tbody>
      </table>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
        <LabButton t={t} onClick={() => onBaseline('POST')}>CALIBRATE EMPTY ROOM</LabButton>
        {motion?.calibrated && <LabButton t={t} quiet onClick={() => onBaseline('DELETE')}>CLEAR</LabButton>}
        <span style={{ fontSize: 9, color: t.textMuted, lineHeight: 1.4, flex: 1, minWidth: 160 }}>
          {motion?.calibrated
            ? 'Motion gate is calibrated to this room.'
            : 'Press with nobody in the room to calibrate the motion gate. Until then it guesses the floor from the quietest recent moments.'}
        </span>
      </div>
    </Panel>
  );
}

function LabButton({ t, children, onClick, disabled, quiet, danger }) {
  const fg = danger ? STATUS.critical : t.green;
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: '5px 10px', borderRadius: 4, cursor: disabled ? 'not-allowed' : 'pointer',
      background: quiet ? 'transparent' : rgba(fg, 0.12),
      border: `1px solid ${quiet ? t.border : rgba(fg, 0.35)}`,
      color: disabled ? t.textMuted : quiet ? t.textSecondary : fg, opacity: disabled ? 0.6 : 1,
      fontSize: 10, fontFamily: MONO, fontWeight: 700, letterSpacing: '0.08em', whiteSpace: 'nowrap',
    }}>{children}</button>
  );
}

// ─── Identity ───────────────────────────────────────────────────────────────

/** Colour follows the person (their position in the sorted roster), never rank. */
function useEntityColor(pal, classes) {
  return useCallback((label) => {
    const i = (classes || []).indexOf(label);
    return i >= 0 && i < pal.series.length ? pal.series[i] : pal.other;
  }, [pal, classes]);
}

function IdentityPanel({ t, pal, identity, classes, minConfidence }) {
  const colorOf = useEntityColor(pal, classes);
  const status = identity?.status;
  const headline = {
    identified: [identity?.label, `${fmt((identity?.confidence || 0) * 100, 0)}% confident`],
    unknown:    ['Unknown', identity?.best_guess ? `Closest: ${identity.best_guess} (${fmt((identity.confidence || 0) * 100, 0)}%) — ${(identity.rejected_because || []).join(', ')}` : ''],
    idle:       ['No one walking', 'Waiting for motion in the link'],
    warming_up: ['Warming up', 'Collecting a few seconds of signal'],
    untrained:  ['No roster model yet', identity?.reason || 'Enrol at least two people below, then train.'],
    disabled:   ['Roster identification is off', 'Enable it in Admin controls.'],
  }[status] || ['—', ''];
  const enrolled = identity?.enrolled || {};
  const timeline = identity?.timeline || [];

  // People worth a line: anyone who reached 20% in the window (capped at 4).
  const lines = useMemo(() => {
    const peak = {};
    timeline.forEach(pt => Object.entries(pt.p || {}).forEach(([k, v]) => { peak[k] = Math.max(peak[k] || 0, v); }));
    return Object.keys(peak).filter(k => peak[k] >= 0.2).sort((a, b) => peak[b] - peak[a]).slice(0, 4);
  }, [timeline]);

  const draw = useCallback((ctx, w, h) => {
    const ribbon = 8, pw = w - AXIS.left - AXIS.right, ph = h - AXIS.top - AXIS.bottom - ribbon - 4;
    drawAxes(ctx, t, w, h - ribbon - 4, {
      yTicks: [0, 0.5, 1].map(p => ({ pos: p, text: `${Math.round((1 - p) * 100)}%` })),
      xTicks: [],
    });
    const n = timeline.length; if (n < 2) return;
    const X = i => AXIS.left + (i / (n - 1)) * pw, Y = p => AXIS.top + (1 - p) * ph;
    ctx.strokeStyle = t.textDim; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(AXIS.left, Y(minConfidence) + 0.5); ctx.lineTo(w - AXIS.right, Y(minConfidence) + 0.5); ctx.stroke();
    ctx.setLineDash([]);
    lines.forEach(name => {
      ctx.strokeStyle = colorOf(name); ctx.lineWidth = 2; ctx.beginPath();
      let pen = false;
      timeline.forEach((pt, i) => {
        const p = pt.p?.[name];
        if (p == null) { pen = false; return; }
        pen ? ctx.lineTo(X(i), Y(p)) : ctx.moveTo(X(i), Y(p)); pen = true;
      });
      ctx.stroke();
    });
    // Decision ribbon: who the system actually named at each moment.
    const y0 = h - AXIS.bottom - ribbon + 4, cw = pw / n;
    timeline.forEach((pt, i) => {
      if (!pt.label) return;
      ctx.fillStyle = pt.label === 'unknown' ? pal.other : colorOf(pt.label);
      ctx.fillRect(AXIS.left + i * cw, y0, Math.max(1, cw - 0.5), ribbon);
    });
    ctx.font = `8px ${MONO}`; ctx.fillStyle = t.textMuted; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText('named', AXIS.left - 5, y0 + ribbon / 2);
  }, [timeline, lines, colorOf, minConfidence, pal, t]);

  return (
    <Panel t={t} title="ROSTER IDENTIFICATION" subtitle="Who the classifier thinks is walking, among the people enrolled. Evidence accumulates across a walk.">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 4 }}>
        <span style={{ fontSize: 24, fontWeight: 800, color: t.textPrimary, lineHeight: 1.1 }}>
          {status === 'identified' && <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: colorOf(identity.label), marginRight: 8 }} />}
          {headline[0]}
        </span>
        <span style={{ fontSize: 10, color: t.textSecondary }}>{headline[1]}</span>
      </div>
      {status === 'identified' && Object.keys(enrolled).length > 0 && (
        <div style={{ fontSize: 10, color: t.textSecondary, marginBottom: 6 }}>
          Enrolled attributes: {Object.entries(enrolled).map(([k, v]) => `${k} ${v}`).join(' · ')}
          <span style={{ color: t.textMuted }}> (looked up from the roster, not inferred from the signal)</span>
        </div>
      )}
      {identity?.warning && <div style={{ marginBottom: 6 }}><StatusChip t={t} tone="warning" icon="⚠" text={identity.warning} /></div>}

      {(identity?.probabilities || []).length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(60px, auto) 1fr 38px', gap: '4px 8px', alignItems: 'center', margin: '8px 0' }}>
          {identity.probabilities.map(pr => (
            <div key={pr.label} style={{ display: 'contents' }}>
              <span style={{ fontSize: 10, color: t.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pr.label}</span>
              <span style={{ height: 8, background: t.bgProgress, borderRadius: 4, overflow: 'hidden' }} title={`${pr.label}: ${fmt(pr.p * 100, 0)}% over the walk, ${fmt(pr.p_window * 100, 0)}% this window`}>
                <span style={{ display: 'block', height: '100%', width: `${Math.max(1, pr.p * 100)}%`, background: colorOf(pr.label), borderRadius: 4, transition: 'width 0.3s ease' }} />
              </span>
              <span style={{ fontSize: 10, color: t.textPrimary, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmt(pr.p * 100, 0)}%</span>
            </div>
          ))}
        </div>
      )}

      {identity?.trained && (
        <>
          <PlotCanvas height={110} draw={draw} deps={[draw]} label="Identity confidence over time" />
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 9, color: t.textSecondary, marginTop: 2 }}>
            {lines.map(name => (
              <span key={name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={{ width: 12, height: 2, background: colorOf(name), display: 'inline-block' }} />{name}
              </span>
            ))}
            <span style={{ color: t.textMuted }}>dashed: acceptance threshold ({fmt(minConfidence * 100, 0)}%)</span>
          </div>
        </>
      )}
    </Panel>
  );
}

function EmbeddingScatter({ t, pal, report, live }) {
  const [tip, setTip] = useState(null);
  const trail = useRef([]);
  const colorOf = useEntityColor(pal, report?.classes);
  const points = report?.embedding || [];

  useEffect(() => {
    if (!live) { trail.current = []; return; }
    trail.current = [...trail.current.slice(-11), live];
  }, [live?.[0], live?.[1]]);  // eslint-disable-line react-hooks/exhaustive-deps

  const frame = useMemo(() => {
    if (!points.length) return null;
    const xs = points.map(p => p.x), ys = points.map(p => p.y);
    const pad = v => (Math.max(...v) - Math.min(...v) || 1) * 0.12;
    return { x0: Math.min(...xs) - pad(xs), x1: Math.max(...xs) + pad(xs), y0: Math.min(...ys) - pad(ys), y1: Math.max(...ys) + pad(ys) };
  }, [points]);

  const project = useCallback((w, h) => {
    const m = 10;
    return (x, y) => [m + ((x - frame.x0) / (frame.x1 - frame.x0)) * (w - 2 * m), h - m - ((y - frame.y0) / (frame.y1 - frame.y0)) * (h - 2 * m)];
  }, [frame]);

  const draw = useCallback((ctx, w, h) => {
    if (!frame) return;
    const P = project(w, h);
    points.forEach(p => {
      const [x, y] = P(p.x, p.y);
      ctx.fillStyle = colorOf(p.label); ctx.strokeStyle = t.bgCard; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    });
    // Direct labels at each person's centroid — identity never rests on colour alone.
    ctx.font = `700 10px ${MONO}`; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    (report.classes || []).forEach(name => {
      const mine = points.filter(p => p.label === name); if (!mine.length) return;
      const [x, y] = P(mine.reduce((s, p) => s + p.x, 0) / mine.length, mine.reduce((s, p) => s + p.y, 0) / mine.length);
      ctx.lineWidth = 3; ctx.strokeStyle = t.bgCard; ctx.strokeText(name, x, y - 7);
      ctx.fillStyle = t.textPrimary; ctx.fillText(name, x, y - 7);
    });
    const tr = trail.current;
    if (tr.length) {
      ctx.strokeStyle = t.textSecondary; ctx.lineWidth = 1; ctx.beginPath();
      tr.forEach(([ex, ey], i) => { const [x, y] = P(ex, ey); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.stroke();
      const [lx, ly] = P(...tr[tr.length - 1]);
      ctx.strokeStyle = t.textPrimary; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(lx, ly, 8, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(lx - 13, ly); ctx.lineTo(lx + 13, ly); ctx.moveTo(lx, ly - 13); ctx.lineTo(lx, ly + 13); ctx.stroke();
    }
  }, [frame, points, report, colorOf, project, t, live]);

  const onHover = (pos) => {
    if (!pos || !frame) return setTip(null);
    const P = project(pos.width, pos.height);
    let best = null, bestD = 18 * 18;
    points.forEach(p => { const [x, y] = P(p.x, p.y); const d = (x - pos.x) ** 2 + (y - pos.y) ** 2; if (d < bestD) { bestD = d; best = p; } });
    setTip(best ? { ...pos, lines: [best.label, `session ${best.session}`] } : null);
  };

  return (
    <Panel t={t} title="GAIT EMBEDDING" subtitle="Every enrolled walk window, projected onto the two directions that best separate people. The ring is the current walker. Tight, separate clusters mean the roster is distinguishable; a 2-D view can hide or exaggerate that.">
      {points.length ? (
        <div style={{ position: 'relative' }}>
          <PlotCanvas height={230} draw={draw} deps={[draw]} onHover={onHover} label="Two-dimensional embedding of enrolled gait samples" />
          <Tooltip t={t} tip={tip} />
        </div>
      ) : <Empty t={t}>Train a roster model to see the embedding.</Empty>}
    </Panel>
  );
}

function ConfusionMatrix({ t, pal, report }) {
  const lut = useMemo(() => buildLut(pal.sequential), [pal]);
  if (!report?.trained || !report.confusion) {
    return <Panel t={t} title="VALIDATION"><Empty t={t}>{report?.reason || 'No validation yet.'}</Empty></Panel>;
  }
  const { labels, matrix } = report.confusion;
  const cell = (v, total) => {
    const frac = total ? v / total : 0, i = Math.round(frac * 255) * 3;
    const lum = 0.2126 * lut[i] + 0.7152 * lut[i + 1] + 0.0722 * lut[i + 2];
    return { background: `rgb(${lut[i]},${lut[i + 1]},${lut[i + 2]})`, color: lum > 140 ? '#0b0b0b' : '#f1f5f9' };
  };
  const acc = report.accuracy != null ? `${fmt(report.accuracy * 100, 0)}%` : '—';
  const optimistic = report.protocol !== 'leave-one-session-out';
  return (
    <Panel t={t} title="VALIDATION" subtitle="Held-out walks: rows are who it really was, columns are who the classifier said.">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 26, fontWeight: 800, color: t.textPrimary }}>{acc}</span>
        <span style={{ fontSize: 10, color: t.textSecondary }}>top-1 · {report.protocol} · chance {fmt(report.chance * 100, 0)}% · {report.n_tested} windows</span>
      </div>
      <div style={{ margin: '4px 0 8px' }}>
        <StatusChip t={t} tone={optimistic ? 'warning' : 'good'} icon={optimistic ? '⚠' : '✓'} text={report.protocol_note} />
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'separate', borderSpacing: 2, fontSize: 10 }}>
          <thead>
            <tr>
              <th />
              {labels.map(l => <th key={l} scope="col" style={{ color: t.textSecondary, fontWeight: 600, padding: '0 4px 3px', maxWidth: 64, overflow: 'hidden', textOverflow: 'ellipsis' }}>{l}</th>)}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, i) => {
              const total = row.reduce((a, b) => a + b, 0);
              return (
                <tr key={labels[i]}>
                  <th scope="row" style={{ color: t.textSecondary, fontWeight: 600, textAlign: 'right', paddingRight: 6, whiteSpace: 'nowrap' }}>{labels[i]}</th>
                  {row.map((v, j) => (
                    <td key={j} title={`${labels[i]} named as ${labels[j]}: ${v} of ${total} windows`}
                        style={{ ...cell(v, total), minWidth: 30, height: 26, textAlign: 'center', borderRadius: 3, fontWeight: i === j ? 800 : 500, fontVariantNumeric: 'tabular-nums' }}>{v}</td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: 9, color: t.textMuted, marginTop: 8, lineHeight: 1.5 }}>
        {report.leave_one_path_out_accuracy != null && <div>Holding out each walking path instead: {fmt(report.leave_one_path_out_accuracy * 100, 0)}%.</div>}
        {report.unknown_false_accept_rate != null && <div>Strangers wrongly accepted as someone enrolled (each person held out in turn): {fmt(report.unknown_false_accept_rate * 100, 0)}%.</div>}
        <div>{report.n_samples} windows from {report.classes.length} people · {report.n_features} features · trained at {fmt(report.fs, 0)} Hz.</div>
      </div>
    </Panel>
  );
}

const Empty = ({ t, children }) => (
  <div style={{ padding: '26px 10px', textAlign: 'center', fontSize: 10, color: t.textMuted, lineHeight: 1.5 }}>{children}</div>
);

// ─── Enrolment ──────────────────────────────────────────────────────────────

function Enrollment({ t, pal, api, capture, roster, report, onChanged }) {
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [form, setForm] = useState({ name: '', session: today, path: 'A', sex: '', duration_s: 20 });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const colorOf = useEntityColor(pal, report?.classes);

  const call = async (path, method = 'POST', body) => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(`${api}${path}`, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      const data = await r.json();
      if (!r.ok) setMsg({ tone: 'critical', text: data.error || 'Request failed' });
      onChanged();
      return r.ok ? data : null;
    } catch { setMsg({ tone: 'critical', text: 'Network error' }); return null; }
    finally { setBusy(false); }
  };

  const train = async () => {
    const rep = await call('/model/train');
    if (rep) setMsg(rep.trained
      ? { tone: 'good', text: `Trained on ${rep.n_samples} windows. Held-out accuracy ${fmt(rep.accuracy * 100, 0)}% (${rep.protocol}).` }
      : { tone: 'warning', text: rep.reason });
  };

  const input = { background: t.bgInput, border: `1px solid ${t.border}`, borderRadius: 4, color: t.textPrimary, padding: '5px 8px', fontSize: 11, fontFamily: MONO, outline: 'none', width: '100%', minWidth: 0 };
  const field = (label, node, flex = 1) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3, flex, minWidth: 70 }}>
      <span style={{ fontSize: 8, fontWeight: 700, letterSpacing: '0.1em', color: t.textMuted }}>{label}</span>{node}
    </label>
  );
  const set = k => e => setForm(f => ({ ...f, [k]: e.target.value }));
  const last = capture?.last;
  const pct = capture?.active ? Math.min(100, (capture.elapsed_s / capture.duration_s) * 100) : 0;

  return (
    <Panel t={t} title="ENROLMENT" subtitle="Record labelled walks, then train. For a number you can trust, give each person several walks, on more than one path, across at least two sessions (ideally different days).">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'flex-end' }}>
        {field('NAME', <input style={input} value={form.name} onChange={set('name')} placeholder="e.g. Ana" maxLength={40} />, 2)}
        {field('SESSION', <input style={input} value={form.session} onChange={set('session')} maxLength={40} />, 2)}
        {field('PATH', <input style={input} value={form.path} onChange={set('path')} maxLength={40} />)}
        {field('SEX (ENROLLED)', (
          <select style={input} value={form.sex} onChange={set('sex')}>
            <option value="">not recorded</option><option value="female">female</option><option value="male">male</option>
          </select>
        ), 2)}
        {field('SECONDS', <input style={input} type="number" min={5} max={60} value={form.duration_s} onChange={set('duration_s')} />)}
        {capture?.active ? (
          <>
            <LabButton t={t} onClick={() => call('/enroll/stop', 'POST', {})} disabled={busy}>FINISH NOW</LabButton>
            <LabButton t={t} quiet onClick={() => call('/enroll/stop', 'POST', { discard: true })} disabled={busy}>DISCARD</LabButton>
          </>
        ) : (
          <LabButton t={t} disabled={busy || !form.name.trim()} onClick={() => call('/enroll/start', 'POST', { ...form, name: form.name.trim(), duration_s: Number(form.duration_s) })}>● RECORD WALK</LabButton>
        )}
      </div>

      {capture?.active && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 10, color: t.textPrimary, fontWeight: 700, marginBottom: 4 }}>
            Recording {capture.label} · {fmt(capture.elapsed_s, 0)} / {fmt(capture.duration_s, 0)} s — keep walking
          </div>
          <div style={{ height: 6, background: t.bgProgress, borderRadius: 3, overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${pct}%`, background: STATUS.critical, borderRadius: 3, transition: 'width 0.5s linear' }} />
          </div>
        </div>
      )}
      {!capture?.active && last && (
        <div style={{ marginTop: 8 }}>
          <StatusChip t={t} tone={last.saved ? 'good' : 'warning'} icon={last.saved ? '✓' : '⚠'}
            text={last.saved ? `Saved ${last.samples} windows for ${last.label} (${fmt(last.seconds, 0)} s at ${fmt(last.fs_hz, 0)} Hz)` : `Last capture not saved: ${last.reason}`} />
        </div>
      )}
      {msg && <div style={{ marginTop: 8 }}><StatusChip t={t} tone={msg.tone} icon={msg.tone === 'good' ? '✓' : '⚠'} text={msg.text} /></div>}

      <div style={{ marginTop: 12, overflowX: 'auto' }}>
        {roster.length ? (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
            <thead>
              <tr>{['PERSON', 'WINDOWS', 'WALKS', 'SESSIONS', 'PATHS', 'SEX', ''].map((h, i) => (
                <th key={i} style={{ fontSize: 8, fontWeight: 700, letterSpacing: '0.1em', color: t.textMuted, textAlign: i === 0 || i > 2 ? 'left' : 'right', padding: '0 8px 5px 0' }}>{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {roster.map(r => {
                const cellStyle = { padding: '5px 8px 5px 0', borderTop: `1px solid ${t.borderDim}`, color: t.textSecondary, fontVariantNumeric: 'tabular-nums' };
                return (
                  <tr key={r.label}>
                    <td style={{ ...cellStyle, color: t.textPrimary, fontWeight: 700, whiteSpace: 'nowrap' }}>
                      <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: colorOf(r.label), marginRight: 6 }} />{r.label}
                    </td>
                    <td style={{ ...cellStyle, textAlign: 'right' }}>{r.samples}</td>
                    <td style={{ ...cellStyle, textAlign: 'right' }}>{r.captures}</td>
                    <td style={cellStyle}>{r.sessions.join(', ')}</td>
                    <td style={cellStyle}>{r.paths.join(', ')}</td>
                    <td style={cellStyle}>{r.sex || '—'}</td>
                    <td style={{ ...cellStyle, textAlign: 'right', paddingRight: 0 }}>
                      <LabButton t={t} quiet danger disabled={busy} onClick={() => {
                        if (window.confirm(`Delete ${r.label}'s enrolment? This removes their recorded walks and cannot be undone.`)) call(`/enroll/${encodeURIComponent(r.label)}`, 'DELETE');
                      }}>DELETE</LabButton>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : <Empty t={t}>Nobody enrolled yet. Enter a name, press Record, and have them walk their usual path.</Empty>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
        <LabButton t={t} disabled={busy || roster.length < 2} onClick={train}>TRAIN ROSTER MODEL</LabButton>
        <span style={{ fontSize: 9, color: t.textMuted }}>Retrains from scratch on everything above and re-runs validation. Takes under a second.</span>
      </div>
    </Panel>
  );
}

// ─── Signal Lab ─────────────────────────────────────────────────────────────

export default function SignalLab({ api, t, dark, legacy, config }) {
  const pal = dark ? PALETTE.dark : PALETTE.light;
  const [snap, setSnap] = useState(null);
  const [roster, setRoster] = useState([]);
  const [report, setReport] = useState(null);
  const [error, setError] = useState(false);

  const refreshRoster = useCallback(async () => {
    try {
      const [er, mr] = await Promise.all([fetch(`${api}/enroll`), fetch(`${api}/model/report`)]);
      setRoster((await er.json()).roster || []);
      setReport(await mr.json());
    } catch { /* the analysis poll reports connectivity */ }
  }, [api]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(`${api}/analysis`);
        const data = await r.json();
        if (alive) { setSnap(data); setError(false); }
      } catch { if (alive) setError(true); }
    };
    tick();
    const id = setInterval(tick, 600);
    return () => { alive = false; clearInterval(id); };
  }, [api]);

  useEffect(() => { refreshRoster(); const id = setInterval(refreshRoster, 4000); return () => clearInterval(id); }, [refreshRoster]);

  // A capture finishing changes the roster; pick that up immediately.
  const captureActive = snap?.capture?.active;
  useEffect(() => { if (captureActive === false) refreshRoster(); }, [captureActive, refreshRoster]);

  const baseline = async (method) => {
    try { await fetch(`${api}/analysis/baseline`, { method }); } catch { /* next poll shows state */ }
  };

  if (error && !snap) return <Panel t={t} title="SIGNAL LAB"><Empty t={t}>Cannot reach the analysis endpoint.</Empty></Panel>;
  const ready = snap?.ready;
  const grid = (min) => ({ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap: 10 });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 18 }}>
      {!ready && <Panel t={t} title="SIGNAL LAB"><Empty t={t}>{snap?.reason || 'Connecting…'}</Empty></Panel>}
      {ready && (
        <>
          <HealthStrip t={t} health={snap.health} motion={snap.motion} />
          <div style={grid(340)}>
            <Spectrogram t={t} pal={pal} data={snap.spectrogram} health={snap.health} />
            <SubcarrierHeatmap t={t} pal={pal} data={snap.heatmap} />
          </div>
          <div style={grid(340)}>
            <PsdPlot t={t} pal={pal} psd={snap.psd} vitals={snap.vitals} />
            <VitalsTable t={t} vitals={snap.vitals} legacy={legacy} motion={snap.motion} onBaseline={baseline}
                         classifierInputs={config?.use_v2_measurements ? 'v2' : 'original'} />
          </div>
          <div style={grid(300)}>
            <IdentityPanel t={t} pal={pal} identity={snap.identity} classes={report?.classes}
                           minConfidence={config?.identity_min_confidence ?? 0.55} />
            <EmbeddingScatter t={t} pal={pal} report={report?.trained ? report : null}
                              live={snap.identity?.embedding || null} />
            <ConfusionMatrix t={t} pal={pal} report={report} />
          </div>
        </>
      )}
      <Enrollment t={t} pal={pal} api={api} capture={snap?.capture} roster={roster} report={report} onChanged={refreshRoster} />
    </div>
  );
}
