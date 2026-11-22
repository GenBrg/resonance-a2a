import { useCallback, useEffect, useMemo, useState } from "react";
import { api, getToken, setToken, setUnauthorizedHandler } from "./api";
import { Spinner, ToastProvider, useToast } from "./components/ui";
import Login from "./views/Login";
import Onboarding from "./views/Onboarding";
import Sandbox from "./views/Sandbox";
import Cards from "./views/Cards";
import Groups from "./views/Groups";
import Proximity from "./views/Proximity";
import Gatekeeper from "./views/Gatekeeper";
import ExportView from "./views/ExportView";
import type { Config, Me } from "./types";

type ViewKey = "onboarding" | "sandbox" | "cards" | "groups" | "proximity" | "gatekeeper" | "export";

const NAV: { key: ViewKey; index: string; label: string }[] = [
  { key: "onboarding", index: "01", label: "建档与隐私" },
  { key: "sandbox", index: "02", label: "暗池沙盒" },
  { key: "cards", index: "03", label: "今日卡片" },
  { key: "groups", index: "04", label: "微圆桌" },
  { key: "proximity", index: "05", label: "现场雷达" },
  { key: "gatekeeper", index: "06", label: "智能门卫" },
  { key: "export", index: "07", label: "带走资产" },
];

function AppInner() {
  const toast = useToast();
  const [config, setConfig] = useState<Config | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [booting, setBooting] = useState(true);
  const [view, setView] = useState<ViewKey>("cards");
  const [cardCount, setCardCount] = useState(0);

  const loadMe = useCallback(async () => {
    try {
      const profile = await api.me();
      setMe(profile);
      setView(profile.onboarding.complete ? "cards" : "onboarding");
      try {
        const cards = await api.cards();
        setCardCount((cards.cards ?? []).filter((card: any) => card.state === "pending").length);
      } catch {
        setCardCount(0);
      }
    } catch {
      setMe(null);
      setToken(null);
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setMe(null);
      toast.push("登录已过期，请重新登录", "err");
    });
    api
      .config()
      .then(setConfig)
      .catch(() => toast.push("连不上后端，请确认 API 已启动", "err"));
    if (getToken()) {
      loadMe().finally(() => setBooting(false));
    } else {
      setBooting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!me) return;
    const timer = window.setInterval(() => {
      api
        .cards()
        .then((cards) => setCardCount((cards.cards ?? []).filter((card: any) => card.state === "pending").length))
        .catch(() => undefined);
    }, 30000);
    return () => window.clearInterval(timer);
  }, [me]);

  const initials = useMemo(() => (me?.display_name ?? "?").slice(0, 1).toUpperCase(), [me]);

  function logout() {
    setToken(null);
    setMe(null);
    toast.push("已退出");
  }

  if (booting) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spinner />
      </div>
    );
  }

  if (!me) {
    return (
      <Login
        config={config}
        onAuthed={() => {
          loadMe();
        }}
      />
    );
  }

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark" />
          <div>
            <div className="brand-name">RESONANCE</div>
            <div className="brand-sub">NeurIPS 2026</div>
          </div>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <button
              key={item.key}
              className={`nav-item ${view === item.key ? "active" : ""}`}
              onClick={() => setView(item.key)}
            >
              <span className="nav-index">{item.index}</span>
              <span className="nav-label">{item.label}</span>
              {item.key === "cards" && cardCount > 0 && <span className="nav-badge">{cardCount}</span>}
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="rail-user">
            <div className="avatar">{initials}</div>
            <div>
              <div className="rail-user-name">{me.display_name}</div>
              <div className="rail-user-meta">
                {me.venue_label.split(" · ")[0]} · k={config?.privacy.k_anonymity ?? 10}
              </div>
            </div>
          </div>
          <div className="chips">
            <span className={`chip ${me.onboarding.complete ? "lime" : "warn"}`}>
              {me.onboarding.complete ? `cohort ${me.onboarding.cohort_size}` : "未建档"}
            </span>
            <span className="chip">{config?.engine === "live" ? "live llm" : "deterministic"}</span>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={logout}>
            退出登录
          </button>
        </div>
      </aside>

      <main className="main">
        {view === "onboarding" && <Onboarding config={config} onChanged={loadMe} />}
        {view === "sandbox" && <Sandbox />}
        {view === "cards" && <Cards />}
        {view === "groups" && <Groups />}
        {view === "proximity" && <Proximity />}
        {view === "gatekeeper" && <Gatekeeper />}
        {view === "export" && <ExportView />}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
