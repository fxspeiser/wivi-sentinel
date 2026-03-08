import { useState, useEffect, useRef, useCallback } from "react";

const API = "http://localhost:5555/api";

// ─── Utility Functions ──────────────────────────────────────────────────────

const timeAgo = (iso) => {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
};

const confidenceColor = (c) => {
  if (c >= 0.9) return "#00ff87";
  if (c >= 0.75) return "#fbbf24";
  if (c >= 0.5) return "#fb923c";
  return "#ef4444";
};

const confidenceLabel = (c) => {
  if (c >= 0.92) return "LOCKED";
  if (c >= 0.8) return "HIGH";
  if (c >= 0.65) return "MEDIUM";
  if (c >= 0.4) return "LOW";
  return "WEAK";
};

// ─── Waveform Canvas ────────────────────────────────────────────────────────

function Waveform({ data, color, width = 200, height = 40 }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data?.length) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    ctx.clearRect(0, 0, width, height);
    ctx.strokeStyle = color || "#00ff87";
    ctx.lineWidth = 1.5;
    ctx.shadowColor = color || "#00ff87";
    ctx.shadowBlur = 4;
    ctx.beginPath();

    const step = width / (data.length - 1);
    const mid = height / 2;
    const amp = height * 0.4;

    data.forEach((v, i) => {
      const x = i * step;
      const y = mid - v * amp;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  }, [data, color, width, height]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height, display: "block" }}
    />
  );
}

// ─── Confidence Sparkline ───────────────────────────────────────────────────

function Sparkline({ data, width = 120, height = 28 }) {
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data?.length) return;
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    const min = Math.min(...data) - 0.05;
    const max = Math.max(...data) + 0.05;
    const range = max - min || 1;
    const step = width / (data.length - 1);

    // Fill gradient
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, "rgba(0,255,135,0.15)");
    grad.addColorStop(1, "rgba(0,255,135,0)");

    ctx.beginPath();
    ctx.moveTo(0, height);
    data.forEach((v, i) => {
      ctx.lineTo(i * step, height - ((v - min) / range) * height);
    });
    ctx.lineTo(width, height);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Line
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = i * step;
      const y = height - ((v - min) / range) * height;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#00ff87";
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // Current value dot
    const last = data[data.length - 1];
    const lx = width;
    const ly = height - ((last - min) / range) * height;
    ctx.beginPath();
    ctx.arc(lx - 2, ly, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = confidenceColor(last);
    ctx.fill();
  }, [data, width, height]);

  return <canvas ref={canvasRef} style={{ width, height, display: "block" }} />;
}

// ─── Radar Pulse Animation ──────────────────────────────────────────────────

function RadarPulse({ active }) {
  const canvasRef = useRef(null);
  const animRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const size = 160;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);
    const center = size / 2;
    let angle = 0;

    const draw = () => {
      ctx.clearRect(0, 0, size, size);

      // Concentric rings
      [0.2, 0.4, 0.6, 0.8, 1.0].forEach((r) => {
        ctx.beginPath();
        ctx.arc(center, center, center * r - 2, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(0,255,135,0.08)";
        ctx.lineWidth = 0.5;
        ctx.stroke();
      });

      // Cross hairs
      ctx.strokeStyle = "rgba(0,255,135,0.06)";
      ctx.beginPath();
      ctx.moveTo(center, 4);
      ctx.lineTo(center, size - 4);
      ctx.moveTo(4, center);
      ctx.lineTo(size - 4, center);
      ctx.stroke();

      if (active) {
        // Sweep
        const grad = ctx.createConicalGradient
          ? null
          : ctx.createRadialGradient(center, center, 0, center, center, center - 4);

        ctx.save();
        ctx.translate(center, center);
        ctx.rotate(angle);

        // Sweep arc
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.arc(0, 0, center - 4, -0.4, 0);
        ctx.closePath();
        const sweepGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, center - 4);
        sweepGrad.addColorStop(0, "rgba(0,255,135,0.3)");
        sweepGrad.addColorStop(1, "rgba(0,255,135,0.02)");
        ctx.fillStyle = sweepGrad;
        ctx.fill();

        // Sweep line
        ctx.beginPath();
        ctx.moveTo(0, 0);
        ctx.lineTo(center - 4, 0);
        ctx.strokeStyle = "rgba(0,255,135,0.6)";
        ctx.lineWidth = 1.5;
        ctx.stroke();

        ctx.restore();
        angle += 0.03;
      }

      // Center dot
      ctx.beginPath();
      ctx.arc(center, center, 3, 0, Math.PI * 2);
      ctx.fillStyle = active ? "#00ff87" : "#334155";
      ctx.fill();

      animRef.current = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animRef.current);
  }, [active]);

  return <canvas ref={canvasRef} style={{ width: 160, height: 160, display: "block" }} />;
}

// ─── Profile Card ───────────────────────────────────────────────────────────

function ProfileCard({ profile, onTag, onDelete, isNew }) {
  const [editing, setEditing] = useState(false);
  const [nickname, setNickname] = useState(profile.nickname || "");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [waveData] = useState(() =>
    Array.from({ length: 60 }, (_, i) =>
      Math.sin(i * 0.3 + Math.random() * 0.5) * (0.5 + Math.random() * 0.5)
    )
  );

  const isHeartbeat = profile.sig_type === "heartbeat";
  const accent = isHeartbeat ? "#ff3e6c" : "#00b4d8";
  const icon = isHeartbeat ? "♥" : "⦿";

  const handleSave = () => {
    if (nickname.trim()) {
      onTag(profile.id, nickname.trim());
      setEditing(false);
    }
  };

  const handleDelete = () => {
    if (confirmDelete) {
      onDelete(profile.id);
      setConfirmDelete(false);
    } else {
      setConfirmDelete(true);
      setTimeout(() => setConfirmDelete(false), 4000);
    }
  };

  return (
    <div
      style={{
        background: isNew
          ? "linear-gradient(135deg, rgba(251,191,36,0.08) 0%, rgba(15,23,42,0.95) 40%)"
          : "rgba(15,23,42,0.7)",
        border: `1px solid ${isNew ? "rgba(251,191,36,0.4)" : "rgba(51,65,85,0.5)"}`,
        borderRadius: 8,
        padding: 16,
        position: "relative",
        overflow: "hidden",
        backdropFilter: "blur(8px)",
        transition: "all 0.3s ease",
      }}
    >
      {/* Type badge */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 10 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            fontSize: 18,
            color: accent,
            textShadow: `0 0 8px ${accent}`,
            animation: isHeartbeat ? "pulse 1.2s ease-in-out infinite" : "none",
          }}>
            {icon}
          </span>
          <div>
            {editing ? (
              <div style={{ display: "flex", gap: 4 }}>
                <input
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSave()}
                  placeholder="Enter nickname..."
                  autoFocus
                  style={{
                    background: "rgba(30,41,59,0.8)",
                    border: "1px solid rgba(0,255,135,0.3)",
                    borderRadius: 4,
                    color: "#e2e8f0",
                    padding: "3px 8px",
                    fontSize: 13,
                    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                    outline: "none",
                    width: 130,
                  }}
                />
                <button
                  onClick={handleSave}
                  style={{
                    background: "rgba(0,255,135,0.15)",
                    border: "1px solid rgba(0,255,135,0.3)",
                    color: "#00ff87",
                    borderRadius: 4,
                    padding: "2px 8px",
                    cursor: "pointer",
                    fontSize: 11,
                    fontFamily: "'JetBrains Mono', monospace",
                  }}
                >
                  SAVE
                </button>
              </div>
            ) : (
              <div
                onClick={() => setEditing(true)}
                style={{
                  color: profile.nickname ? "#e2e8f0" : "rgba(148,163,184,0.6)",
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                  letterSpacing: profile.nickname ? "0.02em" : "0.05em",
                }}
                title="Click to tag"
              >
                {profile.nickname || "UNTAGGED — click to name"}
              </div>
            )}
            <div style={{
              fontSize: 9,
              color: "#64748b",
              fontFamily: "'JetBrains Mono', monospace",
              marginTop: 2,
              letterSpacing: "0.08em",
            }}>
              {profile.sig_type.toUpperCase()} • ID:{profile.id}
            </div>
          </div>
        </div>

        {isNew && (
          <span style={{
            background: "rgba(251,191,36,0.15)",
            color: "#fbbf24",
            fontSize: 9,
            padding: "2px 8px",
            borderRadius: 10,
            fontFamily: "'JetBrains Mono', monospace",
            fontWeight: 700,
            letterSpacing: "0.1em",
            border: "1px solid rgba(251,191,36,0.3)",
            animation: "fadeFlash 2s ease-in-out infinite",
          }}>
            NEW DETECT
          </span>
        )}
      </div>

      {/* Waveform */}
      <Waveform data={waveData} color={accent} width={260} height={32} />

      {/* Stats row */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        gap: 8,
        marginTop: 10,
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        {isHeartbeat ? (
          <>
            <div style={{ color: "#94a3b8" }}>
              BPM <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{profile.metadata?.bpm || "—"}</span>
            </div>
            <div style={{ color: "#94a3b8" }}>
              RESP <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{profile.metadata?.respiratory_rate || "—"}/m</span>
            </div>
          </>
        ) : (
          <>
            <div style={{ color: "#94a3b8" }}>
              CADENCE <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{profile.metadata?.cadence_spm || "—"} spm</span>
            </div>
            <div style={{ color: "#94a3b8" }}>
              STRIDE <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{profile.metadata?.stride_regularity || "—"}</span>
            </div>
          </>
        )}
        <div style={{ color: "#94a3b8" }}>
          SEEN <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{profile.detection_count}x</span>
        </div>
        <div style={{ color: "#94a3b8" }}>
          LAST <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{timeAgo(profile.last_seen)}</span>
        </div>
      </div>

      {/* Confidence bar */}
      {profile.avg_confidence > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 4,
          }}>
            <span style={{
              fontSize: 9,
              color: "#64748b",
              fontFamily: "'JetBrains Mono', monospace",
              letterSpacing: "0.1em",
            }}>
              MATCH CONFIDENCE
            </span>
            <span style={{
              fontSize: 10,
              color: confidenceColor(profile.avg_confidence),
              fontFamily: "'JetBrains Mono', monospace",
              fontWeight: 700,
            }}>
              {confidenceLabel(profile.avg_confidence)} {(profile.avg_confidence * 100).toFixed(1)}%
            </span>
          </div>
          <div style={{
            height: 3,
            background: "rgba(30,41,59,0.8)",
            borderRadius: 2,
            overflow: "hidden",
          }}>
            <div style={{
              height: "100%",
              width: `${profile.avg_confidence * 100}%`,
              background: `linear-gradient(90deg, ${confidenceColor(profile.avg_confidence)}88, ${confidenceColor(profile.avg_confidence)})`,
              borderRadius: 2,
              transition: "width 0.5s ease",
            }} />
          </div>
          {profile.confidence_history?.length > 3 && (
            <div style={{ marginTop: 6 }}>
              <Sparkline data={profile.confidence_history.slice(-30)} width={260} height={24} />
            </div>
          )}
        </div>
      )}

      {/* First seen */}
      <div style={{
        marginTop: 10,
        fontSize: 9,
        color: "#475569",
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.05em",
      }}>
        FIRST SEEN {new Date(profile.first_seen).toLocaleString()}
      </div>

      {/* Delete button */}
      <button
        onClick={handleDelete}
        style={{
          position: "absolute",
          top: 12,
          right: 12,
          background: confirmDelete ? "rgba(239,68,68,0.2)" : "transparent",
          border: confirmDelete ? "1px solid rgba(239,68,68,0.4)" : "1px solid transparent",
          color: confirmDelete ? "#ef4444" : "#475569",
          fontSize: 10,
          padding: "2px 8px",
          borderRadius: 4,
          cursor: "pointer",
          fontFamily: "'JetBrains Mono', monospace",
          letterSpacing: "0.05em",
          transition: "all 0.2s ease",
        }}
        title={confirmDelete ? "Click again to permanently scrub" : "Delete and scrub signature"}
      >
        {confirmDelete ? "CONFIRM SCRUB" : "×"}
      </button>
    </div>
  );
}

// ─── Event Feed ─────────────────────────────────────────────────────────────

function EventFeed({ events }) {
  const feedRef = useRef(null);

  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = 0;
    }
  }, [events.length]);

  return (
    <div
      ref={feedRef}
      style={{
        maxHeight: 320,
        overflowY: "auto",
        fontSize: 11,
        fontFamily: "'JetBrains Mono', monospace",
        scrollbarWidth: "thin",
        scrollbarColor: "#1e293b #0a0f1a",
      }}
    >
      {events.slice().reverse().map((e, i) => (
        <div
          key={i}
          style={{
            padding: "6px 10px",
            borderBottom: "1px solid rgba(30,41,59,0.4)",
            display: "flex",
            alignItems: "center",
            gap: 8,
            opacity: Math.max(0.3, 1 - i * 0.04),
          }}
        >
          <span style={{
            color: e.type === "heartbeat" ? "#ff3e6c" : "#00b4d8",
            fontSize: 10,
            width: 12,
          }}>
            {e.type === "heartbeat" ? "♥" : "⦿"}
          </span>
          <span style={{
            color: e.status === "new" ? "#fbbf24" : "#00ff87",
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: "0.08em",
            width: 40,
          }}>
            {e.status === "new" ? "NEW" : "MATCH"}
          </span>
          <span style={{ color: "#94a3b8", flex: 1 }}>
            {e.nickname || e.profile_id?.slice(0, 8)}
          </span>
          {e.confidence > 0 && (
            <span style={{ color: confidenceColor(e.confidence), fontSize: 10 }}>
              {(e.confidence * 100).toFixed(0)}%
            </span>
          )}
          <span style={{ color: "#334155", fontSize: 9 }}>
            {timeAgo(e.timestamp)}
          </span>
        </div>
      ))}
      {events.length === 0 && (
        <div style={{ color: "#334155", padding: 20, textAlign: "center", letterSpacing: "0.1em" }}>
          AWAITING DETECTIONS...
        </div>
      )}
    </div>
  );
}

// ─── Main Dashboard ─────────────────────────────────────────────────────────

export default function WiViSentinel() {
  const [profiles, setProfiles] = useState([]);
  const [detections, setDetections] = useState([]);
  const [history, setHistory] = useState([]);
  const [status, setStatus] = useState(null);
  const [filter, setFilter] = useState("all"); // all, heartbeat, gait, new
  const [connected, setConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const pollRef = useRef(null);

  const fetchData = useCallback(async () => {
    try {
      const [statusRes, profilesRes, detectionsRes] = await Promise.all([
        fetch(`${API}/status`),
        fetch(`${API}/profiles`),
        fetch(`${API}/detections`),
      ]);
      const statusData = await statusRes.json();
      const profilesData = await profilesRes.json();
      const detectionsData = await detectionsRes.json();

      setStatus(statusData);
      setProfiles(profilesData.profiles || []);
      setDetections(detectionsData.current || []);
      setHistory(detectionsData.recent_history || []);
      setConnected(true);
      setLastUpdate(new Date());
    } catch (err) {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    pollRef.current = setInterval(fetchData, 2000);
    return () => clearInterval(pollRef.current);
  }, [fetchData]);

  const handleTag = async (profileId, nickname) => {
    try {
      await fetch(`${API}/profiles/tag`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_id: profileId, nickname }),
      });
      fetchData();
    } catch (err) {
      console.error("Tag failed:", err);
    }
  };

  const handleDelete = async (profileId) => {
    try {
      await fetch(`${API}/profiles/${profileId}`, { method: "DELETE" });
      fetchData();
    } catch (err) {
      console.error("Delete failed:", err);
    }
  };

  // Determine which profiles are "new" (seen < 3 times, no nickname)
  const newProfileIds = new Set(
    profiles
      .filter((p) => p.detection_count <= 3 && !p.nickname)
      .map((p) => p.id)
  );

  const filteredProfiles = profiles.filter((p) => {
    if (filter === "heartbeat") return p.sig_type === "heartbeat";
    if (filter === "gait") return p.sig_type === "gait";
    if (filter === "new") return newProfileIds.has(p.id);
    return true;
  });

  const heartbeatCount = profiles.filter((p) => p.sig_type === "heartbeat").length;
  const gaitCount = profiles.filter((p) => p.sig_type === "gait").length;
  const newCount = profiles.filter((p) => newProfileIds.has(p.id)).length;

  return (
    <div style={{
      minHeight: "100vh",
      background: "#060a12",
      color: "#e2e8f0",
      fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', monospace",
      position: "relative",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&display=swap');
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes fadeFlash {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
        @keyframes scanLine {
          0% { transform: translateY(-100%); }
          100% { transform: translateY(100vh); }
        }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0a0f1a; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
        * { box-sizing: border-box; }
      `}</style>

      {/* Scan line effect */}
      <div style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        height: 2,
        background: "linear-gradient(90deg, transparent, rgba(0,255,135,0.08), transparent)",
        animation: "scanLine 8s linear infinite",
        pointerEvents: "none",
        zIndex: 100,
      }} />

      {/* Header */}
      <header style={{
        borderBottom: "1px solid rgba(0,255,135,0.1)",
        padding: "16px 24px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        background: "rgba(6,10,18,0.95)",
        backdropFilter: "blur(12px)",
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div>
            <div style={{
              fontSize: 18,
              fontWeight: 700,
              letterSpacing: "0.15em",
              background: "linear-gradient(135deg, #00ff87, #00b4d8)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
            }}>
              WI-VI SENTINEL
            </div>
            <div style={{ fontSize: 9, color: "#475569", letterSpacing: "0.2em", marginTop: 2 }}>
              WiFi CSI BIOMETRIC DETECTION SYSTEM
            </div>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            fontSize: 10,
            color: connected ? "#00ff87" : "#ef4444",
          }}>
            <div style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: connected ? "#00ff87" : "#ef4444",
              boxShadow: connected ? "0 0 8px #00ff87" : "0 0 8px #ef4444",
              animation: "pulse 2s ease-in-out infinite",
            }} />
            {connected ? "ONLINE" : "DISCONNECTED"}
          </div>
          {lastUpdate && (
            <div style={{ fontSize: 9, color: "#334155" }}>
              UPD {lastUpdate.toLocaleTimeString()}
            </div>
          )}
        </div>
      </header>

      <div style={{
        display: "grid",
        gridTemplateColumns: "200px 1fr",
        minHeight: "calc(100vh - 60px)",
      }}>
        {/* Left Sidebar */}
        <aside style={{
          borderRight: "1px solid rgba(30,41,59,0.5)",
          padding: 16,
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}>
          {/* Radar */}
          <div style={{ display: "flex", justifyContent: "center" }}>
            <RadarPulse active={connected} />
          </div>

          {/* Stats */}
          <div style={{ fontSize: 10 }}>
            <div style={{
              fontSize: 9,
              color: "#475569",
              letterSpacing: "0.15em",
              marginBottom: 8,
              borderBottom: "1px solid rgba(30,41,59,0.5)",
              paddingBottom: 6,
            }}>
              SYSTEM OVERVIEW
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>CSI Source</span>
                <span style={{ color: "#94a3b8" }}>{status?.csi_source || "—"}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Active Sigs</span>
                <span style={{ color: "#00ff87" }}>{status?.active_signals || 0}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Profiles</span>
                <span style={{ color: "#e2e8f0" }}>{status?.total_profiles || 0}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#64748b" }}>Threshold</span>
                <span style={{ color: "#94a3b8" }}>
                  {((status?.config?.match_threshold || 0) * 100).toFixed(0)}%
                </span>
              </div>
            </div>
          </div>

          {/* Filters */}
          <div>
            <div style={{
              fontSize: 9,
              color: "#475569",
              letterSpacing: "0.15em",
              marginBottom: 8,
              borderBottom: "1px solid rgba(30,41,59,0.5)",
              paddingBottom: 6,
            }}>
              FILTER
            </div>
            {[
              { key: "all", label: "ALL", count: profiles.length },
              { key: "heartbeat", label: "HEARTBEAT", count: heartbeatCount, color: "#ff3e6c" },
              { key: "gait", label: "GAIT", count: gaitCount, color: "#00b4d8" },
              { key: "new", label: "NEW", count: newCount, color: "#fbbf24" },
            ].map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  width: "100%",
                  padding: "6px 10px",
                  marginBottom: 2,
                  background: filter === f.key ? "rgba(0,255,135,0.06)" : "transparent",
                  border: filter === f.key
                    ? "1px solid rgba(0,255,135,0.15)"
                    : "1px solid transparent",
                  borderRadius: 4,
                  color: filter === f.key ? (f.color || "#00ff87") : "#64748b",
                  fontSize: 10,
                  fontFamily: "'JetBrains Mono', monospace",
                  letterSpacing: "0.08em",
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                }}
              >
                <span>{f.label}</span>
                <span style={{
                  background: f.count > 0 ? "rgba(0,255,135,0.1)" : "transparent",
                  padding: "1px 6px",
                  borderRadius: 8,
                  fontSize: 9,
                }}>
                  {f.count}
                </span>
              </button>
            ))}
          </div>

          {/* Event Feed */}
          <div style={{ flex: 1 }}>
            <div style={{
              fontSize: 9,
              color: "#475569",
              letterSpacing: "0.15em",
              marginBottom: 8,
              borderBottom: "1px solid rgba(30,41,59,0.5)",
              paddingBottom: 6,
            }}>
              LIVE FEED
            </div>
            <EventFeed events={history} />
          </div>
        </aside>

        {/* Main Content */}
        <main style={{ padding: 20 }}>
          {/* Summary strip */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: 12,
            marginBottom: 20,
          }}>
            {[
              { label: "TOTAL PROFILES", value: profiles.length, color: "#e2e8f0" },
              { label: "HEARTBEAT SIGS", value: heartbeatCount, color: "#ff3e6c" },
              { label: "GAIT SIGS", value: gaitCount, color: "#00b4d8" },
              { label: "NEW DETECTIONS", value: newCount, color: "#fbbf24" },
            ].map((s, i) => (
              <div
                key={i}
                style={{
                  background: "rgba(15,23,42,0.5)",
                  border: "1px solid rgba(30,41,59,0.5)",
                  borderRadius: 6,
                  padding: "12px 14px",
                }}
              >
                <div style={{
                  fontSize: 9,
                  color: "#475569",
                  letterSpacing: "0.12em",
                  marginBottom: 4,
                }}>
                  {s.label}
                </div>
                <div style={{
                  fontSize: 28,
                  fontWeight: 700,
                  color: s.color,
                  lineHeight: 1,
                }}>
                  {s.value}
                </div>
              </div>
            ))}
          </div>

          {/* Profile Grid */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(290px, 1fr))",
            gap: 14,
          }}>
            {filteredProfiles
              .sort((a, b) => {
                // New detections first, then by last_seen
                const aNew = newProfileIds.has(a.id);
                const bNew = newProfileIds.has(b.id);
                if (aNew && !bNew) return -1;
                if (!aNew && bNew) return 1;
                return new Date(b.last_seen) - new Date(a.last_seen);
              })
              .map((p) => (
                <ProfileCard
                  key={p.id}
                  profile={p}
                  onTag={handleTag}
                  onDelete={handleDelete}
                  isNew={newProfileIds.has(p.id)}
                />
              ))}
          </div>

          {filteredProfiles.length === 0 && (
            <div style={{
              textAlign: "center",
              padding: 60,
              color: "#1e293b",
              fontSize: 13,
              letterSpacing: "0.1em",
            }}>
              NO SIGNATURES MATCHING CURRENT FILTER
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
