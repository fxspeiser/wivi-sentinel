import { useState, useEffect, useRef, useCallback, useMemo, createContext, useContext } from 'react';

const API = window.location.origin + "/api";

// ─── Theme ──────────────────────────────────────────────────────────────────

const ThemeContext = createContext(null);

const THEMES = {
  dark: {
    bg:               '#04080f',
    bgCard:           '#0b1120',
    bgSidebar:        '#060b14',
    bgHeader:         'rgba(4,8,15,0.97)',
    bgInput:          '#1e293b',
    bgProgress:       '#1e293b',
    border:           '#1e293b',
    borderDim:        'rgba(30,41,59,0.27)',
    textPrimary:      '#f1f5f9',
    textSecondary:    '#94a3b8',
    textMuted:        '#64748b',
    textDim:          '#334155',
    textMid:          '#cbd5e1',
    green:            '#00ff87',
    scanLine:         'rgba(0,255,135,0.1)',
    radarRing:        'rgba(0,255,135,0.12)',
    radarCross:       'rgba(0,255,135,0.08)',
    radarDotInactive: '#334155',
    profileNewBg:     'linear-gradient(135deg, rgba(251,191,36,0.1) 0%, #0b1120 40%)',
    awaiting:         '#334155',
  },
  light: {
    bg:               '#f1f5f9',
    bgCard:           '#ffffff',
    bgSidebar:        '#e2e8f0',
    bgHeader:         'rgba(241,245,249,0.97)',
    bgInput:          '#cbd5e1',
    bgProgress:       '#cbd5e1',
    border:           '#94a3b8',
    borderDim:        'rgba(100,116,139,0.3)',
    textPrimary:      '#0f172a',
    textSecondary:    '#1e293b',
    textMuted:        '#475569',
    textDim:          '#64748b',
    textMid:          '#0f172a',
    green:            '#059669',
    scanLine:         'rgba(0,140,180,0.07)',
    radarRing:        'rgba(5,150,105,0.2)',
    radarCross:       'rgba(5,150,105,0.12)',
    radarDotInactive: '#64748b',
    profileNewBg:     'linear-gradient(135deg, rgba(251,191,36,0.15) 0%, #ffffff 40%)',
    awaiting:         '#475569',
  },
};

// ─── Utilities ──────────────────────────────────────────────────────────────

const hexToRgba = (hex, a) => {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${a})`;
};

const timeAgo = (iso) => {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
};

// Rough distance estimate from body_attenuation (0–1, higher = more signal blocked = closer).
// Based on indoor WiFi path-loss model; accuracy ±2 m.
const estimateDistance = (attenuation) => {
  if (attenuation == null || attenuation <= 0) return null;
  const d = Math.max(0.5, (1.0 - attenuation) * 8.0 + 0.5);
  return d < 10 ? `~${d.toFixed(1)} m` : `~${Math.round(d)} m`;
};

const confColor  = (c, green = '#00ff87') => c >= 0.9 ? green : c >= 0.75 ? "#fbbf24" : c >= 0.5 ? "#fb923c" : "#ef4444";
const confLabel  = (c) => c >= 0.92 ? "LOCKED" : c >= 0.8 ? "HIGH" : c >= 0.65 ? "MED" : c >= 0.4 ? "LOW" : "WEAK";

const speciesIcon  = (s) => ({ human: "👤", dog: "🐕", cat: "🐈", large_animal: "🦌" }[s] || "❓");
const speciesColor = (s, green = '#00ff87') => ({ human: green, dog: "#f59e0b", cat: "#c084fc", large_animal: "#78716c" }[s] || "#94a3b8");
const sexIcon      = (s) => ({ male: "♂", female: "♀", indeterminate: "⚥" }[s] || "—");
const sexColor     = (s) => ({ male: "#3b82f6", female: "#ec4899" }[s] || "#64748b");
const dirIcon      = (d) => ({ approaching: "↗", receding: "↙", stationary: "●" }[d] || "—");
const dirColor     = (d) => ({ approaching: "#ef4444", receding: "#3b82f6", stationary: "#64748b" }[d] || "#475569");
const dirLabel     = (d) => ({ approaching: "APPROACHING", receding: "RECEDING", stationary: "STATIONARY" }[d] || "UNKNOWN");

// ─── Waveform ────────────────────────────────────────────────────────────────

function Waveform({ data, color, width = 220, height = 36 }) {
  const ref = useRef(null);
  useEffect(() => {
    const c = ref.current; if (!c || !data?.length) return;
    const ctx = c.getContext("2d"); const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr; c.height = height * dpr; ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = color; ctx.lineWidth = 1.5;
    ctx.shadowColor = color; ctx.shadowBlur = 6;
    ctx.beginPath();
    const step = width / (data.length - 1), mid = height / 2, amp = height * 0.4;
    data.forEach((v, i) => { const x = i * step, y = mid - v * amp; i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
    ctx.stroke();
  }, [data, color, width, height]);
  return <canvas ref={ref} style={{ width, height, display: "block" }} />;
}

// ─── Sparkline ───────────────────────────────────────────────────────────────

function Sparkline({ data, width = 140, height = 26 }) {
  const t = useContext(ThemeContext);
  const ref = useRef(null);
  useEffect(() => {
    const c = ref.current; if (!c || !data?.length) return;
    const ctx = c.getContext("2d"); const dpr = window.devicePixelRatio || 1;
    c.width = width * dpr; c.height = height * dpr; ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    const min = Math.min(...data) - 0.05, max = Math.max(...data) + 0.05, range = max - min || 1;
    const step = width / (data.length - 1);
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, hexToRgba(t.green, 0.2)); grad.addColorStop(1, hexToRgba(t.green, 0));
    ctx.beginPath(); ctx.moveTo(0, height);
    data.forEach((v, i) => ctx.lineTo(i * step, height - ((v - min) / range) * height));
    ctx.lineTo(width, height); ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
    ctx.beginPath();
    data.forEach((v, i) => { const x = i * step, y = height - ((v - min) / range) * height; i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
    ctx.strokeStyle = t.green; ctx.lineWidth = 1.5; ctx.stroke();
  }, [data, width, height, t]);
  return <canvas ref={ref} style={{ width, height, display: "block" }} />;
}

// ─── Radar Canvas ─────────────────────────────────────────────────────────────
// Pure canvas renderer — shared by the compact sidebar radar and the expanded modal.
// sweepAngle / pingTimes are passed as refs so both instances share the same state.

function RadarCanvas({ size, profiles, active, sweepAngle, pingTimes, selectedIdRef, positionsRef }) {
  const t = useContext(ThemeContext);
  const ref = useRef(null);
  const animRef = useRef(null);
  const expanded = size > 200;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const ctr = size / 2;
    const maxR = ctr - 8;
    const maxDist = 8.5;           // metres — matches estimateDistance upper bound
    const dotR = expanded ? 5 : 3.5;

    // Fallback: stable angle per profile ID (used when positionsRef has no entry yet)
    const angleMap = new Map(profiles.map(p => {
      const hash = p.id.split('').reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7);
      return [p.id, ((Math.abs(hash) % 3600) / 3600) * Math.PI * 2];
    }));

    const getTargets = () => profiles.map(p => {
      const pos = positionsRef?.current?.[p.id];
      const m = p.metadata || {};
      let angle, r, dist;
      if (pos) {
        angle = pos.angle;
        r = Math.min(maxR - dotR - 2, pos.rFrac * maxR);
        dist = pos.rFrac * maxDist;
      } else {
        angle = angleMap.get(p.id) ?? 0;
        const attn = m.body_attenuation || 0;
        dist = attn > 0 ? Math.max(0.5, (1.0 - attn) * 8.0 + 0.5) : null;
        r = dist ? Math.min(maxR - dotR - 2, (dist / maxDist) * maxR) : maxR * 0.72;
      }
      return {
        id: p.id, angle, r, dist,
        x: ctr + r * Math.cos(angle),
        y: ctr + r * Math.sin(angle),
        dir: m.direction || 'unknown',
        nickname: p.nickname || null,
      };
    });

    const draw = (ts) => {
      ctx.clearRect(0, 0, size, size);

      // ── Range rings ───────────────────────────────────────────────────────
      for (let i = 1; i <= 4; i++) {
        const r = (maxR * i) / 4;
        ctx.beginPath();
        ctx.arc(ctr, ctr, r, 0, Math.PI * 2);
        ctx.strokeStyle = t.radarRing;
        ctx.lineWidth = 0.5;
        ctx.stroke();
        if (expanded) {
          ctx.font = '8px "JetBrains Mono", monospace';
          ctx.fillStyle = hexToRgba(t.green, 0.28);
          ctx.textAlign = 'left';
          ctx.fillText(`${i * 2}m`, ctr + r + 3, ctr - 2);
        }
      }

      // ── Crosshairs ────────────────────────────────────────────────────────
      ctx.setLineDash([2, 5]);
      ctx.strokeStyle = t.radarCross;
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(ctr, 4); ctx.lineTo(ctr, size - 4);
      ctx.moveTo(4, ctr); ctx.lineTo(size - 4, ctr);
      ctx.stroke();
      if (expanded) {
        const d45 = maxR * 0.707;
        ctx.beginPath();
        ctx.moveTo(ctr - d45, ctr - d45); ctx.lineTo(ctr + d45, ctr + d45);
        ctx.moveTo(ctr + d45, ctr - d45); ctx.lineTo(ctr - d45, ctr + d45);
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // Compass labels (expanded)
      if (expanded) {
        ctx.font = 'bold 8px "JetBrains Mono", monospace';
        ctx.fillStyle = hexToRgba(t.green, 0.4);
        ctx.textAlign = 'center';
        ctx.fillText('N', ctr, 12);
        ctx.fillText('S', ctr, size - 4);
        ctx.textAlign = 'right';
        ctx.fillText('W', 14, ctr + 4);
        ctx.textAlign = 'left';
        ctx.fillText('E', size - 8, ctr + 4);
        ctx.textAlign = 'left';
      }

      if (active) {
        sweepAngle.current = (sweepAngle.current + 0.022) % (Math.PI * 2);
        const sweep = sweepAngle.current;
        const targets = getTargets();

        // Ping detection: fire when sweep crosses a target's bearing
        targets.forEach(tgt => {
          const diff = ((sweep - tgt.angle) % (Math.PI * 2) + Math.PI * 2) % (Math.PI * 2);
          if (diff < 0.08) pingTimes.current[tgt.id] = ts;
        });

        // ── Sweep sector ──────────────────────────────────────────────────
        ctx.save();
        ctx.translate(ctr, ctr);
        ctx.rotate(sweep);
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.arc(0, 0, maxR, -0.4, 0);
        ctx.closePath();
        const sg = ctx.createRadialGradient(0, 0, 0, 0, 0, maxR);
        sg.addColorStop(0, hexToRgba(t.green, 0.4));
        sg.addColorStop(1, hexToRgba(t.green, 0.01));
        ctx.fillStyle = sg;
        ctx.fill();
        ctx.beginPath();
        ctx.moveTo(0, 0); ctx.lineTo(maxR, 0);
        ctx.strokeStyle = hexToRgba(t.green, 0.9);
        ctx.lineWidth = 1.5;
        ctx.shadowColor = t.green; ctx.shadowBlur = 5;
        ctx.stroke();
        ctx.shadowBlur = 0;
        ctx.restore();

        // ── Target dots ───────────────────────────────────────────────────
        targets.forEach(tgt => {
          const pingAge = pingTimes.current[tgt.id] != null ? ts - pingTimes.current[tgt.id] : 99999;
          const pingFade = Math.max(0, 1 - pingAge / 2000);

          const dotColor = tgt.dir === 'approaching' ? '#ef4444'
            : tgt.dir === 'receding' ? '#3b82f6'
            : t.green;
          const [tr, tg, tb] = [1, 3, 5].map(i => parseInt(dotColor.slice(i, i + 2), 16));

          // Dual expanding echo rings on ping
          if (pingFade > 0) {
            const maxPingR = expanded ? 36 : 22;
            [1.0, 0.58].forEach((phase, k) => {
              const fade = Math.max(0, pingFade * phase - k * 0.08);
              if (fade <= 0) return;
              const ringR = dotR + 1 + (1 - phase * pingFade) * maxPingR;
              ctx.beginPath();
              ctx.arc(tgt.x, tgt.y, ringR, 0, Math.PI * 2);
              ctx.strokeStyle = `rgba(${tr},${tg},${tb},${fade * 0.85})`;
              ctx.lineWidth = 1.5 - k * 0.5;
              ctx.stroke();
            });
          }

          // Approaching: animated pulsing halo
          if (tgt.dir === 'approaching') {
            const pulse = 0.35 + 0.6 * Math.abs(Math.sin(ts / 480));
            const haloR = dotR + 2 + pulse * (expanded ? 9 : 5);
            ctx.beginPath();
            ctx.arc(tgt.x, tgt.y, haloR, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(239,68,68,${0.12 + pulse * 0.32})`;
            ctx.lineWidth = 1.5;
            ctx.stroke();
          }

          // Selection ring (drawn when this dot is the active selection)
          if (selectedIdRef?.current === tgt.id) {
            const selR = dotR + 5 + Math.sin(ts / 300) * 2;
            ctx.beginPath();
            ctx.arc(tgt.x, tgt.y, selR, 0, Math.PI * 2);
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([3, 3]);
            ctx.stroke();
            ctx.setLineDash([]);
          }

          // Dot: radial gradient for a lens/sphere look + glow
          ctx.shadowColor = dotColor;
          ctx.shadowBlur = pingFade > 0.2 ? 18 : 9;
          const dotGrad = ctx.createRadialGradient(
            tgt.x - dotR * 0.3, tgt.y - dotR * 0.3, 0,
            tgt.x, tgt.y, dotR,
          );
          dotGrad.addColorStop(0, `rgba(255,255,255,${0.65 + pingFade * 0.35})`);
          dotGrad.addColorStop(1, dotColor);
          ctx.beginPath();
          ctx.arc(tgt.x, tgt.y, dotR, 0, Math.PI * 2);
          ctx.fillStyle = dotGrad;
          ctx.fill();
          ctx.shadowBlur = 0;

          // Labels (expanded only)
          if (expanded) {
            const label = (tgt.nickname || tgt.id.slice(0, 6)).toUpperCase();
            const distStr = tgt.dist != null ? `~${tgt.dist.toFixed(1)}m` : '';
            const lx = tgt.x + dotR + 6;
            const ly = tgt.y;
            ctx.font = 'bold 9px "JetBrains Mono", monospace';
            ctx.textAlign = 'left';
            const lw = ctx.measureText(label).width;
            ctx.fillStyle = `rgba(${tr},${tg},${tb},0.14)`;
            ctx.fillRect(lx - 2, ly - 11, lw + 8, 14);
            ctx.fillStyle = dotColor;
            ctx.fillText(label, lx + 2, ly);
            if (distStr) {
              ctx.font = '8px "JetBrains Mono", monospace';
              ctx.fillStyle = `rgba(${tr},${tg},${tb},0.7)`;
              ctx.fillText(distStr, lx + 2, ly + 11);
            }
          }
        });
      }

      // ── Center dot (Pi / AP) ──────────────────────────────────────────────
      const cDotR = expanded ? 5 : 3;
      const cg = ctx.createRadialGradient(ctr - 1, ctr - 1, 0, ctr, ctr, cDotR);
      cg.addColorStop(0, 'rgba(255,255,255,0.9)');
      cg.addColorStop(1, active ? t.green : t.radarDotInactive);
      ctx.beginPath();
      ctx.arc(ctr, ctr, cDotR, 0, Math.PI * 2);
      ctx.fillStyle = cg;
      ctx.shadowColor = t.green;
      ctx.shadowBlur = active ? 10 : 0;
      ctx.fill();
      ctx.shadowBlur = 0;
      if (expanded) {
        ctx.font = 'bold 9px "JetBrains Mono", monospace';
        ctx.textAlign = 'left';
        ctx.fillStyle = hexToRgba(t.green, 0.7);
        ctx.fillText('AP', ctr + cDotR + 4, ctr + 4);
      }

      animRef.current = requestAnimationFrame(draw);
    };

    animRef.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(animRef.current);
  }, [active, profiles, t, size, expanded]);

  return <canvas ref={ref} style={{ width: size, height: size, display: 'block' }} />;
}

// ─── Radar Display ─────────────────────────────────────────────────────────────
// Compact sidebar widget (150 px) that expands into a 400 px modal on click.
// Click any dot in expanded mode to inspect that signature's profile data.

function RadarDisplay({ profiles, active, onTag, onDelete, onSuggest, newIds }) {
  const t = useContext(ThemeContext);
  const [expanded, setExpanded] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const sweepAngle = useRef(0);
  const pingTimes = useRef({});
  // Ref so the canvas draw loop can read selection without restarting the animation
  const selectedIdRef = useRef(null);
  // Animated positions — updated every RAF frame, read by both canvas instances
  const positionsRef = useRef({});
  const profilesRef = useRef(profiles);
  profilesRef.current = profiles;
  const lastPosTs = useRef(null);
  const posUpdateRef = useRef(null);

  const selectProfile = (id) => { selectedIdRef.current = id; setSelectedId(id); };
  const clearSelection = () => { selectedIdRef.current = null; setSelectedId(null); };

  const EXPANDED_SIZE = 400;

  // ── Position simulation loop ──────────────────────────────────────────────
  useEffect(() => {
    if (!active) return;
    const update = (ts) => {
      const dt = lastPosTs.current != null ? Math.min((ts - lastPosTs.current) / 1000, 0.05) : 0;
      lastPosTs.current = ts;
      const maxDist = 8.5;
      profilesRef.current.forEach(p => {
        const m = p.metadata || {};
        const species = m.species || 'human';
        const dir = m.direction || 'stationary';
        if (!positionsRef.current[p.id]) {
          const hash = p.id.split('').reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7);
          const angle = ((Math.abs(hash) % 3600) / 3600) * Math.PI * 2;
          const attn = m.body_attenuation || 0;
          const dist = attn > 0 ? Math.max(0.5, (1.0 - attn) * 8.0 + 0.5) : 6;
          positionsRef.current[p.id] = { angle, rFrac: Math.min(0.92, dist / maxDist) };
        }
        const pos = positionsRef.current[p.id];
        const speedMps = species === 'dog' ? 1.8 : species === 'cat' ? 0.12 : 1.0;
        const drFrac = (speedMps / maxDist) * dt;
        if (dir === 'approaching') pos.rFrac -= drFrac;
        else if (dir === 'receding') pos.rFrac += drFrac;
        const angularSpeed = species === 'dog' ? 0.4 : species === 'cat' ? 0.05 : 0.15;
        pos.angle += (Math.random() - 0.5) * angularSpeed * dt;
        pos.rFrac = Math.max(0.05, Math.min(0.92, pos.rFrac));
      });
      posUpdateRef.current = requestAnimationFrame(update);
    };
    posUpdateRef.current = requestAnimationFrame(update);
    return () => cancelAnimationFrame(posUpdateRef.current);
  }, [active]);

  // ── ESC key: first press clears selection, second closes modal ───────────
  useEffect(() => {
    if (!expanded) return;
    const handler = (e) => {
      if (e.key === 'Escape') {
        if (selectedIdRef.current) clearSelection();
        else setExpanded(false);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [expanded]);

  // Replicate position from positionsRef (or fallback formula) for hit-testing
  const hitTest = (clientX, clientY, canvasEl) => {
    if (!canvasEl) return null;
    const rect = canvasEl.getBoundingClientRect();
    const scaleX = EXPANDED_SIZE / rect.width;
    const scaleY = EXPANDED_SIZE / rect.height;
    const cx = (clientX - rect.left) * scaleX;
    const cy = (clientY - rect.top) * scaleY;
    const ctr = EXPANDED_SIZE / 2;
    const maxR = ctr - 8;
    const maxDist = 8.5;
    const dotR = 5;
    for (const p of profiles) {
      const pos = positionsRef.current[p.id];
      let tx, ty;
      if (pos) {
        const r = Math.min(maxR - dotR - 2, pos.rFrac * maxR);
        tx = ctr + r * Math.cos(pos.angle);
        ty = ctr + r * Math.sin(pos.angle);
      } else {
        const hash = p.id.split('').reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7);
        const angle = ((Math.abs(hash) % 3600) / 3600) * Math.PI * 2;
        const attn = p.metadata?.body_attenuation || 0;
        const dist = attn > 0 ? Math.max(0.5, (1 - attn) * 8 + 0.5) : null;
        const r = dist ? Math.min(maxR - dotR - 2, (dist / maxDist) * maxR) : maxR * 0.72;
        tx = ctr + r * Math.cos(angle);
        ty = ctr + r * Math.sin(angle);
      }
      if (Math.hypot(cx - tx, cy - ty) < dotR + 10) return p.id;
    }
    return null;
  };

  const expandedCanvasRef = useRef(null);

  const handleCanvasClick = (e) => {
    const hit = hitTest(e.clientX, e.clientY, expandedCanvasRef.current?.querySelector('canvas'));
    if (hit) selectProfile(hit === selectedId ? null : hit);
    else clearSelection();
  };

  const selectedProfile = profiles.find(p => p.id === selectedId);
  const sm = selectedProfile?.metadata || {};
  const selDir = sm.direction || 'unknown';
  const selDist = estimateDistance(sm.body_attenuation);

  return (
    <>
      <div
        onClick={() => setExpanded(true)}
        style={{ cursor: 'pointer', position: 'relative', lineHeight: 0 }}
        title="Click to expand tactical radar"
      >
        <RadarCanvas size={150} profiles={profiles} active={active} sweepAngle={sweepAngle} pingTimes={pingTimes} selectedIdRef={selectedIdRef} positionsRef={positionsRef} />
        <div style={{
          position: 'absolute', bottom: 4, left: 0, right: 0, textAlign: 'center',
          fontSize: 7, color: hexToRgba(t.green, 0.55), fontWeight: 700,
          letterSpacing: '0.1em', fontFamily: "'JetBrains Mono', monospace",
          pointerEvents: 'none',
        }}>
          {profiles.length > 0 ? `${profiles.length} TRACKED` : 'SCANNING'} · EXPAND
        </div>
      </div>

      {expanded && (
        <div
          onClick={() => { clearSelection(); setExpanded(false); }}
          style={{
            position: 'fixed', inset: 0,
            background: 'rgba(0,0,0,0.82)',
            backdropFilter: 'blur(6px)',
            zIndex: 300,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              background: t.bgCard,
              border: `1px solid ${hexToRgba(t.green, 0.3)}`,
              borderRadius: 14, padding: 24,
              boxShadow: `0 0 60px ${hexToRgba(t.green, 0.12)}, 0 0 120px ${hexToRgba(t.green, 0.05)}`,
              display: 'flex', flexDirection: 'column', gap: 0,
            }}
          >
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div>
                <span style={{ fontSize: 13, fontWeight: 800, color: t.green, letterSpacing: '0.12em' }}>◉ TACTICAL RADAR</span>
                <span style={{ fontSize: 10, color: t.textSecondary, marginLeft: 12 }}>
                  {profiles.length} SIGNATURE{profiles.length !== 1 ? 'S' : ''} · 8m RANGE · CLICK DOT TO INSPECT
                </span>
              </div>
              <button
                onClick={() => { if (selectedId) clearSelection(); else { clearSelection(); setExpanded(false); } }}
                style={{
                  background: 'transparent', border: `1px solid ${t.border}`,
                  color: t.textMuted, borderRadius: 5, padding: '3px 10px',
                  cursor: 'pointer', fontSize: 11,
                  fontFamily: "'JetBrains Mono', monospace", fontWeight: 700,
                }}
                title={selectedId ? 'Deselect target (ESC)' : 'Close radar (ESC)'}
              >{selectedId ? '× DESELECT' : 'ESC'}</button>
            </div>

            {/* Radar + optional info panel side by side */}
            <div style={{ display: 'flex', gap: 20, alignItems: 'flex-start' }}>
              <div ref={expandedCanvasRef} onClick={handleCanvasClick} style={{ cursor: 'crosshair', flexShrink: 0 }}>
                <RadarCanvas size={EXPANDED_SIZE} profiles={profiles} active={active} sweepAngle={sweepAngle} pingTimes={pingTimes} selectedIdRef={selectedIdRef} positionsRef={positionsRef} />
              </div>

              {/* Info panel — appears when a dot is selected */}
              {selectedProfile ? (
                <div style={{
                  width: 260, background: t.bgSidebar,
                  border: `1px solid ${hexToRgba(dirColor(selDir), 0.4)}`,
                  borderRadius: 10, padding: 16,
                  animation: selDir === 'approaching' ? 'approachGlow 3s ease-in-out infinite' : selDir === 'receding' ? 'recedeGlow 3s ease-in-out infinite' : 'none',
                }}>
                  {/* Approaching banner */}
                  {selDir === 'approaching' && (
                    <div style={{
                      background: 'linear-gradient(90deg, rgba(239,68,68,0.85), rgba(239,68,68,0.6))',
                      margin: '-16px -16px 12px -16px', padding: '5px 12px',
                      borderRadius: '10px 10px 0 0',
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    }}>
                      <span style={{ fontSize: 10, fontWeight: 800, color: '#fff', letterSpacing: '0.1em' }}>▲ TARGET APPROACHING</span>
                      {selDist && <span style={{ fontSize: 10, fontWeight: 700, color: 'rgba(255,255,255,0.9)' }}>{selDist}</span>}
                    </div>
                  )}

                  {/* Name + type */}
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 14, fontWeight: 800, color: t.textPrimary, letterSpacing: '0.02em' }}>
                      {selectedProfile.nickname || 'UNTAGGED'}
                    </div>
                    <div style={{ fontSize: 10, color: t.textSecondary, marginTop: 2, letterSpacing: '0.06em' }}>
                      {selectedProfile.sig_type.toUpperCase()} · {selectedProfile.id.slice(0, 10)}
                    </div>
                  </div>

                  {/* Badges */}
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 10 }}>
                    <Badge label={sm.species?.toUpperCase() || '?'} color={speciesColor(sm.species, t.green)} icon={speciesIcon(sm.species)} />
                    {sm.species === 'human' && sm.sex_estimation && sm.sex_estimation !== 'n/a' && (
                      <Badge label={sm.sex_estimation?.toUpperCase()} color={sexColor(sm.sex_estimation)} icon={sexIcon(sm.sex_estimation)} />
                    )}
                    <Badge label={dirLabel(selDir)} color={dirColor(selDir)} icon={dirIcon(selDir)}
                      glow={selDir === 'approaching' ? 'approachGlow 2s ease-in-out infinite' : undefined} />
                  </div>

                  {/* Metrics grid */}
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, fontSize: 11, fontWeight: 500, marginBottom: 10 }}>
                    {selectedProfile.sig_type === 'heartbeat' ? (<>
                      <div><span style={{ color: t.textSecondary }}>BPM </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{sm.bpm || '—'}</span></div>
                      <div><span style={{ color: t.textSecondary }}>RESP </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{sm.respiratory_rate || '—'}/m</span></div>
                    </>) : (<>
                      <div><span style={{ color: t.textSecondary }}>CADENCE </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{sm.cadence_spm || '—'} spm</span></div>
                      <div><span style={{ color: t.textSecondary }}>STRIDE </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{sm.stride_regularity || '—'}</span></div>
                    </>)}
                    <div><span style={{ color: t.textSecondary }}>SEEN </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{selectedProfile.detection_count}×</span></div>
                    <div><span style={{ color: t.textSecondary }}>LAST </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{timeAgo(selectedProfile.last_seen)}</span></div>
                    {selDist && (
                      <div style={{ gridColumn: '1 / -1' }}>
                        <span style={{ color: t.textSecondary }}>DIST </span>
                        <span style={{ color: dirColor(selDir), fontWeight: 700 }}>{selDist}</span>
                        {sm.speed_kmh > 0 && selDir !== 'stationary' && (
                          <span style={{ color: t.textMuted, marginLeft: 8 }}>{sm.speed_kmh} km/h</span>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Confidence bar */}
                  {selectedProfile.avg_confidence > 0 && (
                    <div style={{ marginBottom: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                        <span style={{ fontSize: 9, color: t.textSecondary, fontWeight: 600, letterSpacing: '0.08em' }}>MATCH</span>
                        <span style={{ fontSize: 10, color: confColor(selectedProfile.avg_confidence, t.green), fontWeight: 800 }}>
                          {confLabel(selectedProfile.avg_confidence)} {(selectedProfile.avg_confidence * 100).toFixed(1)}%
                        </span>
                      </div>
                      <div style={{ height: 3, background: t.bgProgress, borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${selectedProfile.avg_confidence * 100}%`, background: confColor(selectedProfile.avg_confidence, t.green), borderRadius: 2 }} />
                      </div>
                    </div>
                  )}

                  {/* Species confidence */}
                  {sm.species_confidence > 0 && (
                    <div style={{ marginBottom: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                        <span style={{ fontSize: 9, color: t.textSecondary, fontWeight: 600, letterSpacing: '0.08em' }}>SPECIES ID</span>
                        <span style={{ fontSize: 9, color: speciesColor(sm.species, t.green), fontWeight: 700 }}>{sm.species?.toUpperCase()} {(sm.species_confidence * 100).toFixed(0)}%</span>
                      </div>
                      <div style={{ height: 3, background: t.bgProgress, borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${sm.species_confidence * 100}%`, background: speciesColor(sm.species, t.green), borderRadius: 2 }} />
                      </div>
                    </div>
                  )}

                  {/* Tag input if untagged */}
                  {!selectedProfile.nickname && onTag && (
                    <button
                      onClick={() => {
                        const name = prompt('Enter nickname for this signature:');
                        if (name?.trim()) onTag(selectedProfile.id, name.trim());
                      }}
                      style={{
                        width: '100%', marginTop: 4, padding: '5px 0',
                        background: hexToRgba(t.green, 0.1), border: `1px solid ${hexToRgba(t.green, 0.3)}`,
                        color: t.green, borderRadius: 5, cursor: 'pointer', fontSize: 10,
                        fontFamily: "'JetBrains Mono', monospace", fontWeight: 700, letterSpacing: '0.08em',
                      }}
                    >+ TAG THIS SIGNATURE</button>
                  )}

                  <button onClick={clearSelection} style={{
                    width: '100%', marginTop: 8, padding: '4px 0',
                    background: 'transparent', border: `1px solid ${t.border}`,
                    color: t.textMuted, borderRadius: 5, cursor: 'pointer', fontSize: 9,
                    fontFamily: "'JetBrains Mono', monospace", fontWeight: 600, letterSpacing: '0.08em',
                  }}>DESELECT</button>
                </div>
              ) : (
                /* Placeholder when nothing selected */
                <div style={{
                  width: 220, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center',
                  color: t.textDim, fontSize: 10, fontWeight: 600, letterSpacing: '0.1em',
                  fontFamily: "'JetBrains Mono', monospace", gap: 10, padding: '0 16px',
                }}>
                  <div style={{ fontSize: 28, opacity: 0.25 }}>◎</div>
                  <div style={{ textAlign: 'center', lineHeight: 1.7 }}>CLICK A DOT<br/>TO INSPECT<br/>THAT TARGET</div>
                  <div style={{ fontSize: 8, opacity: 0.5, textAlign: 'center' }}>{profiles.length} SIGNATURE{profiles.length !== 1 ? 'S' : ''} TRACKED</div>
                </div>
              )}
            </div>

            {/* Legend */}
            <div style={{ display: 'flex', gap: 18, marginTop: 16, justifyContent: 'center', flexWrap: 'wrap' }}>
              {[['#ef4444', '▲ APPROACHING'], ['#3b82f6', '↙ RECEDING'], [t.green, '● STATIONARY']].map(([color, label]) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}` }} />
                  <span style={{ fontSize: 9, color: t.textSecondary, fontWeight: 600, letterSpacing: '0.08em', fontFamily: "'JetBrains Mono', monospace" }}>{label}</span>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 8, textAlign: 'center', fontSize: 8, color: t.textDim, letterSpacing: '0.08em', fontFamily: "'JetBrains Mono', monospace" }}>
              BEARING ASSIGNED PER SIGNATURE ID · RANGE FROM CSI ATTENUATION · ±2m ACCURACY
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ─── Badge ───────────────────────────────────────────────────────────────────

function Badge({ label, color, bg, icon, glow }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      background: bg || `${color}18`, border: `1px solid ${color}55`,
      color: color, fontSize: 10, fontWeight: 700, padding: "2px 8px",
      borderRadius: 4, letterSpacing: "0.06em", fontFamily: "'JetBrains Mono', monospace",
      animation: glow || "none", whiteSpace: "nowrap",
    }}>
      {icon && <span style={{ fontSize: 11 }}>{icon}</span>}
      {label}
    </span>
  );
}

// ─── Profile Card ────────────────────────────────────────────────────────────

function ProfileCard({ profile, onTag, onDelete, onSuggest, isNew }) {
  const t = useContext(ThemeContext);
  const [editing, setEditing] = useState(false);
  const [nickname, setNickname] = useState(profile.nickname || "");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [waveData] = useState(() => Array.from({ length: 60 }, (_, i) => Math.sin(i * 0.3 + Math.random() * 0.5) * (0.5 + Math.random() * 0.5)));

  const isHB = profile.sig_type === "heartbeat";
  const accent = isHB ? "#ff3e6c" : "#00b4d8";
  const icon = isHB ? "♥" : "⦿";
  const m = profile.metadata || {};
  const dir = m.direction || "unknown";
  const dist = estimateDistance(m.body_attenuation);

  const handleSave = () => { if (nickname.trim()) { onTag(profile.id, nickname.trim()); setEditing(false); } };
  const handleDelete = () => {
    if (confirmDelete) { onDelete(profile.id); setConfirmDelete(false); }
    else { setConfirmDelete(true); setTimeout(() => setConfirmDelete(false), 4000); }
  };

  const dirBorder = dir === "approaching" ? "rgba(239,68,68,0.4)" : dir === "receding" ? "rgba(59,130,246,0.4)" : `${t.border}80`;
  const isApproaching = dir === "approaching";

  return (
    <div style={{
      background: isNew ? t.profileNewBg : t.bgCard,
      border: `1px solid ${isNew ? "rgba(251,191,36,0.5)" : dirBorder}`,
      borderRadius: 8, padding: 16, paddingTop: isApproaching ? 38 : 16,
      position: "relative", overflow: "hidden",
      transition: "background 0.2s ease, border-color 0.2s ease",
      animation: isApproaching ? "approachGlow 3s ease-in-out infinite" : dir === "receding" ? "recedeGlow 3s ease-in-out infinite" : "none",
    }}>
      {isApproaching && (
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 26,
          background: "linear-gradient(90deg, rgba(239,68,68,0.85), rgba(239,68,68,0.6))",
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "0 10px",
        }}>
          <span style={{ fontSize: 10, fontWeight: 800, color: "#fff", letterSpacing: "0.1em" }}>▲ TARGET APPROACHING</span>
          {dist && <span style={{ fontSize: 10, fontWeight: 700, color: "rgba(255,255,255,0.9)", letterSpacing: "0.06em" }}>{dist}</span>}
        </div>
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
          <span style={{ fontSize: 20, color: accent, textShadow: `0 0 10px ${accent}`, animation: isHB ? "pulse 1.2s ease-in-out infinite" : "none", flexShrink: 0 }}>{icon}</span>
          <div style={{ minWidth: 0 }}>
            {editing ? (
              <div style={{ display: "flex", gap: 4 }}>
                <input value={nickname} onChange={e => setNickname(e.target.value)} onKeyDown={e => e.key === "Enter" && handleSave()} placeholder="Enter nickname..." autoFocus
                  style={{ background: t.bgInput, border: `1px solid ${t.green}`, borderRadius: 4, color: t.textPrimary, padding: "4px 8px", fontSize: 13, fontFamily: "'JetBrains Mono', monospace", outline: "none", width: 140 }} />
                <button onClick={handleSave} style={{ background: hexToRgba(t.green, 0.13), border: `1px solid ${t.green}`, color: t.green, borderRadius: 4, padding: "3px 10px", cursor: "pointer", fontSize: 11, fontFamily: "'JetBrains Mono', monospace", fontWeight: 700 }}>OK</button>
              </div>
            ) : (
              <div onClick={() => setEditing(true)} style={{ color: profile.nickname ? t.textPrimary : t.textSecondary, fontSize: 15, fontWeight: 700, cursor: "pointer", letterSpacing: "0.02em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title="Click to tag">
                {profile.nickname || "UNTAGGED — click to name"}
              </div>
            )}
            <div style={{ fontSize: 10, color: t.textSecondary, marginTop: 2, letterSpacing: "0.06em", fontWeight: 500 }}>
              {profile.sig_type.toUpperCase()} · <span style={{ color: t.textMid }}>{profile.id}</span>
            </div>
          </div>
        </div>
        {isNew && <Badge label="NEW" color="#fbbf24" icon="⚡" glow="fadeFlash 2s ease-in-out infinite" />}
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        <Badge label={m.species?.toUpperCase() || "?"} color={speciesColor(m.species, t.green)} icon={speciesIcon(m.species)} />
        {m.species === "human" && m.sex_estimation && m.sex_estimation !== "n/a" && (
          <Badge label={m.sex_estimation?.toUpperCase()} color={sexColor(m.sex_estimation)} icon={sexIcon(m.sex_estimation)} />
        )}
        <Badge label={dirLabel(dir)} color={dirColor(dir)} icon={dirIcon(dir)}
          glow={dir === "approaching" ? "approachGlow 2s ease-in-out infinite" : undefined} />
        {m.speed_kmh > 0 && dir !== "stationary" && (
          <Badge label={`${m.speed_kmh} km/h`} color={t.textMuted} />
        )}
      </div>

      <Waveform data={waveData} color={accent} width={280} height={34} />

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 10, fontSize: 12, fontWeight: 500 }}>
        {isHB ? (<>
          <div><span style={{ color: t.textSecondary }}>BPM </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{m.bpm || "—"}</span></div>
          <div><span style={{ color: t.textSecondary }}>RESP </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{m.respiratory_rate || "—"}/m</span></div>
        </>) : (<>
          <div><span style={{ color: t.textSecondary }}>CADENCE </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{m.cadence_spm || "—"} spm</span></div>
          <div><span style={{ color: t.textSecondary }}>STRIDE </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{m.stride_regularity || "—"}</span></div>
        </>)}
        <div><span style={{ color: t.textSecondary }}>SEEN </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{profile.detection_count}×</span></div>
        <div><span style={{ color: t.textSecondary }}>LAST </span><span style={{ color: t.textPrimary, fontWeight: 700 }}>{timeAgo(profile.last_seen)}</span></div>
        {dist && (
          <div style={{ gridColumn: "1 / -1" }}>
            <span style={{ color: t.textSecondary }}>DIST </span>
            <span style={{ color: dirColor(dir), fontWeight: 700 }}>{dist}</span>
          </div>
        )}
      </div>

      {m.species_confidence > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
            <span style={{ fontSize: 10, color: t.textSecondary, fontWeight: 600, letterSpacing: "0.08em" }}>SPECIES ID</span>
            <span style={{ fontSize: 10, color: speciesColor(m.species, t.green), fontWeight: 700 }}>{m.species?.toUpperCase()} {(m.species_confidence * 100).toFixed(0)}%</span>
          </div>
          <div style={{ height: 3, background: t.bgProgress, borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${m.species_confidence * 100}%`, background: speciesColor(m.species, t.green), borderRadius: 2 }} />
          </div>
        </div>
      )}
      {m.species === "human" && m.sex_confidence > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
            <span style={{ fontSize: 10, color: t.textSecondary, fontWeight: 600, letterSpacing: "0.08em" }}>SEX EST</span>
            <span style={{ fontSize: 10, color: sexColor(m.sex_estimation), fontWeight: 700 }}>{m.sex_estimation?.toUpperCase()} {(m.sex_confidence * 100).toFixed(0)}%</span>
          </div>
          <div style={{ height: 3, background: t.bgProgress, borderRadius: 2, overflow: "hidden", display: "flex" }}>
            <div style={{ height: "100%", width: `${(m.sex_male_prob || 0.5) * 100}%`, background: "#3b82f6", borderRadius: "2px 0 0 2px" }} />
            <div style={{ height: "100%", width: `${(m.sex_female_prob || 0.5) * 100}%`, background: "#ec4899", borderRadius: "0 2px 2px 0" }} />
          </div>
        </div>
      )}

      {profile.avg_confidence > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
            <span style={{ fontSize: 10, color: t.textSecondary, fontWeight: 600, letterSpacing: "0.08em" }}>MATCH</span>
            <span style={{ fontSize: 11, color: confColor(profile.avg_confidence, t.green), fontWeight: 800 }}>
              {confLabel(profile.avg_confidence)} {(profile.avg_confidence * 100).toFixed(1)}%
            </span>
          </div>
          <div style={{ height: 3, background: t.bgProgress, borderRadius: 2, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${profile.avg_confidence * 100}%`, background: `linear-gradient(90deg, ${confColor(profile.avg_confidence, t.green)}88, ${confColor(profile.avg_confidence, t.green)})`, borderRadius: 2 }} />
          </div>
          {profile.confidence_history?.length > 3 && (
            <div style={{ marginTop: 5 }}><Sparkline data={profile.confidence_history.slice(-30)} width={280} height={22} /></div>
          )}
        </div>
      )}

      {profile.device_candidates?.length > 0 && (
        <div style={{ marginTop: 8, borderTop: `1px solid ${t.border}`, paddingTop: 8 }}>
          <div style={{ fontSize: 9, color: t.textSecondary, letterSpacing: "0.12em", fontWeight: 700, marginBottom: 5 }}>DEVICE LINK</div>
          {profile.device_candidates.slice(0, 4).map((c, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <span style={{ fontSize: 11, flexShrink: 0 }}>📱</span>
              <span style={{
                color: c.score >= 0.82 ? t.green : c.suggested ? "#fbbf24" : t.textMid,
                fontSize: 10, fontWeight: 600, flex: 1,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }} title={c.display_name}>{c.display_name}</span>
              <div style={{ width: 48, height: 3, background: t.bgProgress, borderRadius: 2, flexShrink: 0 }}>
                <div style={{
                  height: "100%",
                  width: `${Math.min(100, (c.score / 0.82) * 100)}%`,
                  background: c.score >= 0.82 ? t.green : c.suggested ? "#fbbf24" : "#475569",
                  borderRadius: 2, transition: "width 0.5s ease",
                }} />
              </div>
              <span style={{ fontSize: 9, fontWeight: 700, color: c.score >= 0.82 ? t.green : t.textMuted, width: 28, textAlign: "right" }}>
                {(c.score * 100).toFixed(0)}%
              </span>
              {c.score >= 0.82 ? (
                <span style={{ fontSize: 9, color: t.green, fontWeight: 800 }}>✓</span>
              ) : c.suggested ? (
                <span style={{ fontSize: 9, color: "#fbbf24", fontWeight: 700 }}>WATCH</span>
              ) : (
                <button onClick={() => onSuggest(profile.id, c.display_name)} style={{
                  background: "#fbbf2418", border: "1px solid #fbbf2455", color: "#fbbf24",
                  fontSize: 9, padding: "1px 5px", borderRadius: 3, cursor: "pointer",
                  fontFamily: "'JetBrains Mono', monospace", fontWeight: 700,
                }}>TAG</button>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ marginTop: 8, fontSize: 10, color: t.textMuted, fontWeight: 500, letterSpacing: "0.04em" }}>
        FIRST {new Date(profile.first_seen).toLocaleString()}
      </div>

      <button onClick={handleDelete} style={{
        position: "absolute", top: 10, right: 10,
        background: confirmDelete ? "#ef444433" : "transparent",
        border: confirmDelete ? "1px solid #ef444488" : "1px solid transparent",
        color: confirmDelete ? "#fca5a5" : t.textMuted, fontSize: 11, padding: "3px 10px",
        borderRadius: 4, cursor: "pointer", fontFamily: "'JetBrains Mono', monospace",
        fontWeight: 700, transition: "all 0.2s ease",
      }} title={confirmDelete ? "Click again to permanently scrub" : "Delete and scrub"}>
        {confirmDelete ? "⚠ SCRUB" : "×"}
      </button>
    </div>
  );
}

// ─── Event Feed ──────────────────────────────────────────────────────────────

function EventFeed({ events }) {
  const t = useContext(ThemeContext);
  const ref = useRef(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = 0; }, [events.length]);
  return (
    <div ref={ref} style={{ maxHeight: 280, overflowY: "auto", fontSize: 11 }}>
      {events.slice().reverse().map((e, i) => (
        <div key={i} style={{ padding: "5px 8px", borderBottom: `1px solid ${t.border}`, display: "flex", alignItems: "center", gap: 6, opacity: Math.max(0.3, 1 - i * 0.04) }}>
          <span style={{ color: e.type === "heartbeat" ? "#ff3e6c" : "#00b4d8", fontSize: 10, width: 14 }}>{e.type === "heartbeat" ? "♥" : "⦿"}</span>
          <span style={{ color: e.status === "new" ? "#fbbf24" : t.green, fontSize: 9, fontWeight: 800, width: 38 }}>{e.status === "new" ? "NEW" : "MATCH"}</span>
          <span style={{ fontSize: 10, marginRight: 2 }}>{speciesIcon(e.metadata?.species)}</span>
          <span style={{ color: dirColor(e.metadata?.direction), fontSize: 10 }}>{dirIcon(e.metadata?.direction)}</span>
          <span style={{ color: t.textMid, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 500 }}>{e.nickname || e.profile_id?.slice(0, 8)}</span>
          {e.confidence > 0 && <span style={{ color: confColor(e.confidence, t.green), fontSize: 10, fontWeight: 700 }}>{(e.confidence * 100).toFixed(0)}%</span>}
        </div>
      ))}
      {events.length === 0 && <div style={{ color: t.awaiting, padding: 16, textAlign: "center", letterSpacing: "0.1em", fontWeight: 600 }}>AWAITING...</div>}
    </div>
  );
}

// ─── Filter Button ───────────────────────────────────────────────────────────

function FilterBtn({ label, active, color, count, onClick }) {
  const t = useContext(ThemeContext);
  const fg = color || t.green;
  return (
    <button onClick={onClick} style={{
      display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%",
      padding: "5px 8px", marginBottom: 2, borderRadius: 4, cursor: "pointer",
      background: active ? hexToRgba(fg, 0.07) : "transparent",
      border: active ? `1px solid ${hexToRgba(fg, 0.3)}` : "1px solid transparent",
      color: active ? fg : t.textSecondary,
      fontSize: 10, fontFamily: "'JetBrains Mono', monospace", fontWeight: active ? 700 : 500,
      letterSpacing: "0.06em", transition: "all 0.15s ease",
    }}>
      <span>{label}</span>
      <span style={{ background: count > 0 ? hexToRgba(fg, 0.1) : "transparent", padding: "1px 6px", borderRadius: 8, fontSize: 9, fontWeight: 700 }}>{count}</span>
    </button>
  );
}

// ─── App ─────────────────────────────────────────────────────────────────────

export default function App() {
  const [darkMode, setDarkMode] = useState(() => {
    try { return localStorage.getItem('wivi-theme') !== 'light'; } catch { return true; }
  });
  const [profiles, setProfiles] = useState([]);
  const [history, setHistory]   = useState([]);
  const [status, setStatus]     = useState(null);
  const [devices, setDevices]   = useState({});
  const [filter, setFilter]     = useState("all");
  const [sortBy, setSortBy]     = useState("last_seen");
  const [connected, setConnected]   = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);

  const t = darkMode ? THEMES.dark : THEMES.light;

  useEffect(() => {
    document.body.style.background = t.bg;
    document.body.style.color      = t.textPrimary;
    document.documentElement.setAttribute('data-theme', darkMode ? 'dark' : 'light');
    try { localStorage.setItem('wivi-theme', darkMode ? 'dark' : 'light'); } catch {}
  }, [darkMode, t]);

  const fetchData = useCallback(async () => {
    try {
      const [sr, pr, dr, devr] = await Promise.all([
        fetch(`${API}/status`), fetch(`${API}/profiles`),
        fetch(`${API}/detections`), fetch(`${API}/devices`),
      ]);
      setStatus(await sr.json());
      setProfiles((await pr.json()).profiles || []);
      setHistory((await dr.json()).recent_history || []);
      setDevices((await devr.json()).devices || {});
      setConnected(true);
      setLastUpdate(new Date());
    } catch { setConnected(false); }
  }, []);

  useEffect(() => { fetchData(); const id = setInterval(fetchData, 2000); return () => clearInterval(id); }, [fetchData]);

  const handleTag     = async (id, name) => { try { await fetch(`${API}/profiles/tag`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: id, nickname: name }) }); fetchData(); } catch {} };
  const handleDelete  = async (id) => { try { await fetch(`${API}/profiles/${id}`, { method: "DELETE" }); fetchData(); } catch {} };
  const handleSuggest = async (profileId, deviceName) => { try { await fetch(`${API}/devices/suggest`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile_id: profileId, device_name: deviceName }) }); fetchData(); } catch {} };

  const newIds = new Set(profiles.filter(p => p.detection_count <= 3 && !p.nickname).map(p => p.id));

  const counts = useMemo(() => {
    const c = { all: profiles.length, heartbeat: 0, gait: 0, new: 0, human: 0, dog: 0, cat: 0, large_animal: 0, male: 0, female: 0, approaching: 0, receding: 0, stationary: 0 };
    profiles.forEach(p => {
      c[p.sig_type] = (c[p.sig_type] || 0) + 1;
      if (newIds.has(p.id)) c.new++;
      const m = p.metadata || {};
      if (m.species) c[m.species] = (c[m.species] || 0) + 1;
      if (m.sex_estimation === "male") c.male++;
      if (m.sex_estimation === "female") c.female++;
      if (m.direction) c[m.direction] = (c[m.direction] || 0) + 1;
    });
    return c;
  }, [profiles, newIds]);

  const filtered = useMemo(() => {
    let list = profiles;
    if (filter === "heartbeat") list = list.filter(p => p.sig_type === "heartbeat");
    else if (filter === "gait") list = list.filter(p => p.sig_type === "gait");
    else if (filter === "new") list = list.filter(p => newIds.has(p.id));
    else if (["human", "dog", "cat", "large_animal"].includes(filter))
      list = list.filter(p => p.metadata?.species === filter);
    else if (filter === "male" || filter === "female")
      list = list.filter(p => p.metadata?.sex_estimation === filter);
    else if (["approaching", "receding", "stationary"].includes(filter))
      list = list.filter(p => p.metadata?.direction === filter);

    const sorters = {
      last_seen:  (a, b) => new Date(b.last_seen) - new Date(a.last_seen),
      confidence: (a, b) => (b.avg_confidence || 0) - (a.avg_confidence || 0),
      detections: (a, b) => b.detection_count - a.detection_count,
      species:    (a, b) => (a.metadata?.species || "").localeCompare(b.metadata?.species || ""),
      direction:  (a, b) => { const ord = { approaching: 0, receding: 1, stationary: 2 }; return (ord[a.metadata?.direction] ?? 3) - (ord[b.metadata?.direction] ?? 3); },
      sex:        (a, b) => (a.metadata?.sex_estimation || "").localeCompare(b.metadata?.sex_estimation || ""),
    };
    list = [...list].sort(sorters[sortBy] || sorters.last_seen);
    return [...list.filter(p => newIds.has(p.id)), ...list.filter(p => !newIds.has(p.id))];
  }, [profiles, filter, sortBy, newIds]);

  const SectionLabel = ({ children }) => (
    <div style={{ fontSize: 9, color: t.textMid, letterSpacing: "0.15em", fontWeight: 700, marginBottom: 6, paddingBottom: 5, borderBottom: `1px solid ${t.border}` }}>{children}</div>
  );

  return (
    <ThemeContext.Provider value={t}>
      <div style={{ minHeight: "100vh", background: t.bg, color: t.textPrimary, transition: "background 0.2s ease, color 0.2s ease" }}>
        <div style={{ position: "fixed", top: 0, left: 0, right: 0, height: 2, background: `linear-gradient(90deg, transparent, ${t.scanLine}, transparent)`, animation: "scanLine 8s linear infinite", pointerEvents: "none", zIndex: 100 }} />

        <header style={{ borderBottom: `1px solid ${t.border}`, padding: "14px 24px", display: "flex", justifyContent: "space-between", alignItems: "center", background: t.bgHeader, backdropFilter: "blur(12px)", position: "sticky", top: 0, zIndex: 50 }}>
          <div>
            <div className="app-title">WI-VI SENTINEL</div>
            <div style={{ fontSize: 10, color: t.textSecondary, letterSpacing: "0.15em", marginTop: 1, fontWeight: 500 }}>WiFi CSI BIOMETRIC DETECTION · CLASSIFICATION · TRACKING</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: connected ? t.green : "#ef4444", fontWeight: 700 }}>
              <div style={{ width: 7, height: 7, borderRadius: "50%", background: connected ? t.green : "#ef4444", boxShadow: connected ? `0 0 10px ${t.green}` : "0 0 10px #ef4444", animation: "pulse 2s ease-in-out infinite" }} />
              {connected ? "ONLINE" : "OFFLINE"}
            </div>
            {lastUpdate && <div style={{ fontSize: 10, color: t.textMuted, fontWeight: 500 }}>UPD {lastUpdate.toLocaleTimeString()}</div>}
            <button
              onClick={() => setDarkMode(d => !d)}
              title={darkMode ? "Switch to light mode" : "Switch to dark mode"}
              style={{
                background: "transparent", border: `1px solid ${t.border}`,
                color: t.textSecondary, borderRadius: 6, padding: "4px 10px",
                cursor: "pointer", fontSize: 15, lineHeight: 1,
                fontFamily: "'JetBrains Mono', monospace", transition: "border-color 0.15s ease",
              }}
            >{darkMode ? "☀" : "🌙"}</button>
          </div>
        </header>

        <div style={{ display: "grid", gridTemplateColumns: "210px 1fr", minHeight: "calc(100vh - 56px)" }}>
          <aside style={{ borderRight: `1px solid ${t.border}`, padding: 14, display: "flex", flexDirection: "column", gap: 16, background: t.bgSidebar }}>
            <div style={{ display: "flex", justifyContent: "center" }}><RadarDisplay profiles={profiles} active={connected} onTag={handleTag} onDelete={handleDelete} onSuggest={handleSuggest} newIds={newIds} /></div>

            <div style={{ fontSize: 10 }}>
              <SectionLabel>SYSTEM</SectionLabel>
              {[["Source", status?.csi_source || "—", t.textMid], ["Active", status?.active_signals || 0, t.green], ["Profiles", status?.total_profiles || 0, t.textPrimary], ["Threshold", `${((status?.config?.match_threshold || 0) * 100).toFixed(0)}%`, t.textMid]].map(([l, v, c], i) => (
                <div key={i} style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ color: t.textSecondary, fontWeight: 500 }}>{l}</span>
                  <span style={{ color: c, fontWeight: 700 }}>{v}</span>
                </div>
              ))}
            </div>

            <div>
              <SectionLabel>SIGNAL TYPE</SectionLabel>
              <FilterBtn label="ALL"       active={filter === "all"}       count={counts.all}       onClick={() => setFilter("all")} />
              <FilterBtn label="HEARTBEAT" active={filter === "heartbeat"} count={counts.heartbeat} color="#ff3e6c" onClick={() => setFilter("heartbeat")} />
              <FilterBtn label="GAIT"      active={filter === "gait"}      count={counts.gait}      color="#00b4d8" onClick={() => setFilter("gait")} />
              <FilterBtn label="NEW"       active={filter === "new"}       count={counts.new}       color="#fbbf24" onClick={() => setFilter("new")} />
            </div>

            <div>
              <SectionLabel>SPECIES</SectionLabel>
              <FilterBtn label="👤 HUMAN" active={filter === "human"} count={counts.human} onClick={() => setFilter("human")} />
              <FilterBtn label="🐕 DOG"   active={filter === "dog"}   count={counts.dog}   color="#f59e0b" onClick={() => setFilter("dog")} />
              <FilterBtn label="🐈 CAT"   active={filter === "cat"}   count={counts.cat}   color="#c084fc" onClick={() => setFilter("cat")} />
            </div>

            <div>
              <SectionLabel>SEX (HUMAN)</SectionLabel>
              <FilterBtn label="♂ MALE"   active={filter === "male"}   count={counts.male}   color="#3b82f6" onClick={() => setFilter("male")} />
              <FilterBtn label="♀ FEMALE" active={filter === "female"} count={counts.female} color="#ec4899" onClick={() => setFilter("female")} />
            </div>

            <div>
              <SectionLabel>DIRECTION</SectionLabel>
              <FilterBtn label="↗ APPROACHING" active={filter === "approaching"} count={counts.approaching} color="#ef4444" onClick={() => setFilter("approaching")} />
              <FilterBtn label="↙ RECEDING"    active={filter === "receding"}    count={counts.receding}    color="#3b82f6" onClick={() => setFilter("receding")} />
              <FilterBtn label="● STATIONARY"  active={filter === "stationary"}  count={counts.stationary}  color="#64748b" onClick={() => setFilter("stationary")} />
            </div>

            <div>
              <SectionLabel>SORT BY</SectionLabel>
              {[["last_seen", "LAST SEEN"], ["confidence", "CONFIDENCE"], ["detections", "DETECTIONS"], ["species", "SPECIES"], ["direction", "DIRECTION"], ["sex", "SEX"]].map(([k, l]) => (
                <button key={k} onClick={() => setSortBy(k)} style={{
                  display: "block", width: "100%", textAlign: "left", padding: "4px 8px", marginBottom: 1,
                  background: sortBy === k ? hexToRgba(t.green, 0.07) : "transparent",
                  border: sortBy === k ? `1px solid ${hexToRgba(t.green, 0.25)}` : "1px solid transparent",
                  borderRadius: 3, color: sortBy === k ? t.green : t.textSecondary,
                  fontSize: 10, fontFamily: "'JetBrains Mono', monospace", fontWeight: sortBy === k ? 700 : 500,
                  cursor: "pointer", letterSpacing: "0.06em",
                }}>{l}</button>
              ))}
            </div>

            <div>
              <SectionLabel>NEARBY DEVICES ({Object.keys(devices).length})</SectionLabel>
              {Object.values(devices).slice(0, 8).map((d, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 5, padding: "3px 0", borderBottom: `1px solid ${t.borderDim}` }}>
                  <span style={{ fontSize: 10, flexShrink: 0 }}>📱</span>
                  <span style={{ color: t.textMid, fontSize: 10, fontWeight: 500, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.display_name}>{d.display_name}</span>
                  <span style={{ fontSize: 8, color: t.textMuted, fontWeight: 600, letterSpacing: "0.05em", flexShrink: 0 }}>{d.source === "mdns" ? "mDNS" : "PROBE"}</span>
                </div>
              ))}
              {Object.keys(devices).length === 0 && (
                <div style={{ color: t.awaiting, fontSize: 10, fontWeight: 600, padding: "4px 0", letterSpacing: "0.08em" }}>SCANNING...</div>
              )}
            </div>

            <div style={{ flex: 1 }}>
              <SectionLabel>LIVE FEED</SectionLabel>
              <EventFeed events={history} />
            </div>
          </aside>

          <main style={{ padding: 20, background: t.bg }}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 10, marginBottom: 18 }}>
              {[
                { label: "TOTAL",       value: profiles.length,                                                      color: t.textPrimary },
                { label: "HEARTBEAT",   value: counts.heartbeat,                                                     color: "#ff3e6c" },
                { label: "GAIT",        value: counts.gait,                                                          color: "#00b4d8" },
                { label: "HUMAN",       value: counts.human,                                                         color: t.green },
                { label: "ANIMAL",      value: (counts.dog || 0) + (counts.cat || 0) + (counts.large_animal || 0),  color: "#f59e0b" },
                { label: "APPROACHING", value: counts.approaching,                                                   color: "#ef4444" },
                { label: "DEVICES",     value: Object.keys(devices).length,                                          color: "#a78bfa" },
              ].map((s, i) => (
                <div key={i} style={{ background: t.bgCard, border: `1px solid ${t.border}`, borderRadius: 6, padding: "10px 12px" }}>
                  <div style={{ fontSize: 9, color: t.textSecondary, letterSpacing: "0.1em", marginBottom: 3, fontWeight: 600 }}>{s.label}</div>
                  <div style={{ fontSize: 26, fontWeight: 800, color: s.color, lineHeight: 1 }}>{s.value}</div>
                </div>
              ))}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(310px, 1fr))", gap: 12 }}>
              {filtered.map(p => (
                <ProfileCard key={p.id} profile={p} onTag={handleTag} onDelete={handleDelete} onSuggest={handleSuggest} isNew={newIds.has(p.id)} />
              ))}
            </div>
            {filtered.length === 0 && (
              <div style={{ textAlign: "center", padding: 60, color: t.textDim, fontSize: 13, fontWeight: 600, letterSpacing: "0.1em" }}>NO SIGNATURES MATCHING FILTER</div>
            )}
          </main>
        </div>
      </div>
    </ThemeContext.Provider>
  );
}
