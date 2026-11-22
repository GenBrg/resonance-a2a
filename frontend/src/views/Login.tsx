import { useState } from "react";
import { api, setToken } from "../api";
import { useToast } from "../components/ui";
import type { Config } from "../types";

export default function Login({
  config,
  onAuthed,
}: {
  config: Config | null;
  onAuthed: () => void;
}) {
  const toast = useToast();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("chen@demo.resonance");
  const [password, setPassword] = useState("demo1234");
  const [displayName, setDisplayName] = useState("");
  const [affiliation, setAffiliation] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      if (mode === "login") {
        const result = await api.login(email.trim(), password);
        setToken(result.access_token);
      } else {
        const domain = email.includes("@") ? email.split("@")[1] : "";
        const result = await api.register({
          email: email.trim(),
          password,
          display_name: displayName || email.split("@")[0],
          venue: "sydney",
          affiliation,
          affiliation_domain: domain,
        });
        setToken(result.access_token);
      }
      onAuthed();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const demoAccounts = config?.demo_accounts ?? [];

  return (
    <div className="gate">
      <div className="gate-left">
        <div className="brand" style={{ padding: 0 }}>
          <div className="brand-mark" />
          <div>
            <div className="brand-name">RESONANCE</div>
            <div className="brand-sub">NeurIPS 2026 · Agent Relay</div>
          </div>
        </div>
        <h1 className="gate-title">
          让 Agent 在暗处替你试错，
          <br />
          让人类在<em>光里</em>真诚相遇。
        </h1>
        <p className="gate-sub">
          万人顶会里，真正的瓶颈不是认识谁，而是不敢开口、开不了口、开了口被反推身份。
          这套系统把发现、试探、初筛、排期交给 agent，把见面留给你。
        </p>
        <div className="gate-points">
          <div className="gate-point">
            <span className="ic">01</span>
            <span>
              <b>双盲门控。</b>
              双方 agent 在封闭沙盒里匿名互考，只有两边独立评分都过线才解锁真身；单向不合适静默销毁，对方永远不知道你来过。
            </span>
          </div>
          <div className="gate-point">
            <span className="ic">02</span>
            <span>
              <b>人类不可触达撮合层。</b>
              API 里没有取回原始对话的接口，数据库里没有那一列。沙盒跑完只剩一个哈希。
            </span>
          </div>
          <div className="gate-point">
            <span className="ic">03</span>
            <span>
              <b>k-匿名发布。</b>
              论文名、star 数、机构这些可反查的指纹在进场前被强制泛化；任何一条对外信息背后的同簇人群不得少于 {config?.privacy.k_anonymity ?? 10} 人。
            </span>
          </div>
          <div className="gate-point">
            <span className="ic">04</span>
            <span>
              <b>克制的注意力预算。</b>
              每人每天最多 {config?.privacy.daily_card_budget ?? 3} 张卡，写进数据库配额表，不做又一个消息炸锅的社交软件。
            </span>
          </div>
        </div>
      </div>

      <div className="gate-right">
        <div className="row-between">
          <div>
            <div className="eyebrow">ACCESS</div>
            <h2>{mode === "login" ? "进入你的暗池" : "新建参会者"}</h2>
          </div>
          <div className="tabs">
            <button className={`tab ${mode === "login" ? "active" : ""}`} onClick={() => setMode("login")}>
              登录
            </button>
            <button className={`tab ${mode === "register" ? "active" : ""}`} onClick={() => setMode("register")}>
              注册
            </button>
          </div>
        </div>

        <label className="field">
          <span className="field-label">邮箱</span>
          <input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="you@lab.edu" />
        </label>
        <label className="field">
          <span className="field-label">
            密码
            {config?.demo_password && <span className="hint">demo: {config.demo_password}</span>}
          </span>
          <input
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && submit()}
          />
        </label>
        {mode === "register" && (
          <>
            <label className="field">
              <span className="field-label">显示名</span>
              <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">
                机构 <span className="hint">用于同机构硬屏蔽</span>
              </span>
              <input value={affiliation} onChange={(event) => setAffiliation(event.target.value)} placeholder="Lab X" />
            </label>
          </>
        )}

        <div className="btn-row">
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? <span className="spinner" /> : null}
            {mode === "login" ? "登录并进入" : "注册并建档"}
          </button>
        </div>

        {demoAccounts.length > 0 && (
          <div className="gap-sm mt">
            <div className="eyebrow">演示账号 · 一键切换视角</div>
            {demoAccounts.map((account) => (
              <button
                key={account.email}
                className="demo-account"
                onClick={() => {
                  setMode("login");
                  setEmail(account.email);
                  setPassword(config?.demo_password ?? "demo1234");
                  toast.push(`已填入 ${account.email}，点登录`);
                }}
              >
                <span>
                  <span className="demo-name mono">{account.email}</span>
                  <br />
                  <span className="demo-role">{account.role}</span>
                </span>
                <span className="chip">填充</span>
              </button>
            ))}
          </div>
        )}

        <div className="notice info mt">
          这是本地可跑的生产级实例：{config?.engine === "live" ? "真实大模型引擎" : "确定性引擎（未配置 LLM_API_KEY）"} · k=
          {config?.privacy.k_anonymity ?? 10} · 每日 {config?.privacy.daily_card_budget ?? 3} 卡
        </div>
      </div>
    </div>
  );
}
