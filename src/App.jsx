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

// ─── Radar ───────────────────────────────────────────────────────────────────

function RadarPulse({ active }) {
  const t = useContext(ThemeContext);
  const ref = useRef(null), anim = useRef(null);
  useEffect(() => {
    const c = ref.current; if (!c) return;
    const ctx = c.getContext("2d"); const size = 150, dpr = window.devicePixelRatio || 1;
    c.width = size * dpr; c.height = size * dpr; ctx.scale(dpr, dpr);
    const ctr = size / 2; let angle = 0;
    const draw = () => {
      ctx.clearRect(0, 0, size, size);
      [0.2, 0.4, 0.6, 0.8, 1.0].forEach(r => {
        ctx.beginPath(); ctx.arc(ctr, ctr, ctr * r - 2, 0, Math.PI * 2);
        ctx.strokeStyle = t.radarRing; ctx.lineWidth = 0.5; ctx.stroke();
      });
      ctx.strokeStyle = t.radarCross; ctx.beginPath();
      ctx.moveTo(ctr, 4); ctx.lineTo(ctr, size - 4);
      ctx.moveTo(4, ctr); ctx.lineTo(size - 4, ctr); ctx.stroke();
      if (active) {
        ctx.save(); ctx.translate(ctr, ctr); ctx.rotate(angle);
        ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, ctr - 4, -0.4, 0); ctx.closePath();
        const g = ctx.createRadialGradient(0, 0, 0, 0, 0, ctr - 4);
        g.addColorStop(0, hexToRgba(t.green, 0.35)); g.addColorStop(1, hexToRgba(t.green, 0.02));
        ctx.fillStyle = g; ctx.fill();
        ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(ctr - 4, 0);
        ctx.strokeStyle = hexToRgba(t.green, 0.7); ctx.lineWidth = 1.5; ctx.stroke();
        ctx.restore(); angle += 0.03;
      }
      ctx.beginPath(); ctx.arc(ctr, ctr, 3, 0, Math.PI * 2);
      ctx.fillStyle = active ? t.green : t.radarDotInactive; ctx.fill();
      anim.current = requestAnimationFrame(draw);
    };
    draw(); return () => cancelAnimationFrame(anim.current);
  }, [active, t]);
  return <canvas ref={ref} style={{ width: 150, height: 150, display: "block" }} />;
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

  const handleSave = () => { if (nickname.trim()) { onTag(profile.id, nickname.trim()); setEditing(false); } };
  const handleDelete = () => {
    if (confirmDelete) { onDelete(profile.id); setConfirmDelete(false); }
    else { setConfirmDelete(true); setTimeout(() => setConfirmDelete(false), 4000); }
  };

  const dirBorder = dir === "approaching" ? "rgba(239,68,68,0.4)" : dir === "receding" ? "rgba(59,130,246,0.4)" : `${t.border}80`;

  return (
    <div style={{
      background: isNew ? t.profileNewBg : t.bgCard,
      border: `1px solid ${isNew ? "rgba(251,191,36,0.5)" : dirBorder}`,
      borderRadius: 8, padding: 16, position: "relative", overflow: "hidden",
      transition: "background 0.2s ease, border-color 0.2s ease",
      animation: dir === "approaching" ? "approachGlow 3s ease-in-out infinite" : dir === "receding" ? "recedeGlow 3s ease-in-out infinite" : "none",
    }}>
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
            <div style={{ display: "flex", justifyContent: "center" }}><RadarPulse active={connected} /></div>

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
