import { useEffect, useState } from "react";
import { api } from "../api";
import { Field, Meter, Panel, Spinner, Tabs, useAsync, useToast } from "../components/ui";
import type { Config, GeneralizationOutcome } from "../types";

const SAMPLE_BORROW = `{
  "topics": ["RL 推理加速", "KV cache 调度", "长上下文"],
  "bottleneck": "多机异步 rollout 阶段 KV cache 换入换出把长尾延迟顶到不可接受，显存碎片比容量更致命",
  "contrarian": "纯靠合成数据堆长度已经见顶，关键在动态剪枝与调度而不是更多 token",
  "deal_breakers": ["只讲商业模式、没碰过底层", "没有开源/工程交付记录"],
  "intents": ["cofounder_infra", "job_quiet"],
  "avoid_domains": ["singapore-ailab.ai"]
}`;

const SAMPLE_NARRATIVE =
  "我这次来最想解决的是多机 rollout 的 KV cache 换出问题，长尾延迟一直压不下去。" +
  "我觉得纯堆合成数据长度已经见顶了，真正的杠杆在调度而不是更多 token。" +
  "想低调找一位做推理引擎的联创，但我绝不能被现公司发现，singapore-ailab.ai 请屏蔽。";

export default function Onboarding({ config, onChanged }: { config: Config | null; onChanged: () => void }) {
  const toast = useToast();
  const [tab, setTab] = useState<"borrow" | "narrative" | "anchor">("borrow");
  const [prompt, setPrompt] = useState("");
  const [borrowText, setBorrowText] = useState("");
  const [narrative, setNarrative] = useState(SAMPLE_NARRATIVE);
  const [consent, setConsent] = useState(true);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<GeneralizationOutcome | null>(null);
  const [anchorTopics, setAnchorTopics] = useState("rl_policy_opt, kv_cache, long_context");
  const profile = useAsync(() => api.profile(), []);

  useEffect(() => {
    api
      .borrowPrompt()
      .then((data) => setPrompt(data.prompt))
      .catch(() => setPrompt(""));
  }, []);

  async function copyPrompt() {
    try {
      await navigator.clipboard.writeText(prompt);
      toast.push("提炼 Prompt 已复制，丢给你日常用的 Claude / ChatGPT");
    } catch {
      toast.push("复制失败，请手动选中文本", "err");
    }
  }

  async function runBorrow() {
    if (borrowText.trim().length < 10) {
      toast.push("先把模型吐出来的 JSON 粘进来", "err");
      return;
    }
    setBusy(true);
    try {
      const result = await api.borrow(borrowText, consent);
      setOutcome(result.generalization);
      toast.push(`私密简报已加密入库，质量分 ${(result.vault.quality * 100).toFixed(0)}`);
      profile.reload();
      onChanged();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function runNarrative() {
    setBusy(true);
    try {
      const result = await api.narrative(narrative, consent);
      setOutcome(result.generalization);
      toast.push(`已从自述里抽取 ${result.vault.intent_labels.length} 条隐性诉求`);
      profile.reload();
      onChanged();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function runPreview() {
    setBusy(true);
    try {
      const topics = anchorTopics
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean);
      const result = await api.preview(topics);
      setOutcome(result);
      profile.reload();
      toast.push("已按当前画像池重新计算泛化结果");
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  async function wipe() {
    setBusy(true);
    try {
      await api.wipeVault();
      setOutcome(null);
      toast.push("私密档案已销毁，匹配权重归零");
      profile.reload();
      onChanged();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  const current = profile.data;
  const generalized = current?.generalized;
  const vault = current?.vault;
  const display = outcome ?? (generalized ? { ...generalized, ok: generalized.tags.length > 0 } : null);
  const k = config?.privacy.k_anonymity ?? 10;

  return (
    <div className="grid">
      <div className="page-head">
        <div>
          <div className="eyebrow">STEP 01 · ONBOARDING</div>
          <h1>三分钟，把公开简历补成有灵魂的分身</h1>
          <p className="lede">
            公开论文只能当底板，真正让匹配有意义的是简历上不会写的东西：你卡在哪个具体环节、你对主流做法哪点不认同、你不愿意公开广播的诉求。
            下面三条路任选其一，数据进加密档案，出去的是经过 k-匿名泛化的标签包。
          </p>
        </div>
        <div className="chips">
          <span className="chip lime">k ≥ {k}</span>
          <span className="chip violet">AES-256-GCM</span>
          <span className="chip">blind index</span>
        </div>
      </div>

      <div className="grid grid-rail">
        <Panel
          title="私有简报录入"
          right={
            <Tabs
              value={tab}
              onChange={setTab}
              options={[
                { key: "borrow", label: "10 秒借壳" },
                { key: "narrative", label: "60 秒自述" },
                { key: "anchor", label: "公开锚点" },
              ]}
            />
          }
        >
          {tab === "borrow" && (
            <div className="gap">
              <div className="notice info">
                复制下面这段 Prompt 给你天天用的 Claude / Cursor，让它吐出你<b>不含机密</b>的研究品味与卡点，再粘回来。
                原始笔记和代码一步都没有离开你的电脑。
              </div>
              <div className="term" style={{ maxHeight: 190 }}>
                {prompt || "加载中…"}
              </div>
              <div className="btn-row">
                <button className="btn" onClick={copyPrompt}>
                  复制提炼 Prompt
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => setBorrowText(SAMPLE_BORROW)}>
                  填入示例输出
                </button>
              </div>
              <Field label="把模型吐出的 JSON 粘到这里" hint="只会解析受控字段">
                <textarea
                  value={borrowText}
                  onChange={(event) => setBorrowText(event.target.value)}
                  placeholder={SAMPLE_BORROW}
                  style={{ minHeight: 150 }}
                />
              </Field>
              <ConsentRow consent={consent} setConsent={setConsent} />
              <div className="btn-row">
                <button className="btn btn-primary" onClick={runBorrow} disabled={busy}>
                  {busy ? <span className="spinner" /> : null}
                  加密入库并计算泛化
                </button>
              </div>
            </div>
          )}

          {tab === "narrative" && (
            <div className="gap">
              <div className="notice info">
                没有常用 AI 也没关系。对着手机说 60 秒：这次来最想解决的困惑、最想吐槽的圈内现象、最想认识什么人。
                浏览器语音转写后，系统自己抽结构化字段（{config?.engine === "live" ? "大模型抽取" : "确定性规则抽取"}）。
              </div>
              <Field label="一句话也行，说清楚卡在哪" hint={`${narrative.length} 字`}>
                <textarea value={narrative} onChange={(event) => setNarrative(event.target.value)} style={{ minHeight: 150 }} />
              </Field>
              <ConsentRow consent={consent} setConsent={setConsent} />
              <div className="btn-row">
                <button className="btn btn-primary" onClick={runNarrative} disabled={busy}>
                  {busy ? <span className="spinner" /> : null}
                  抽取私密增量
                </button>
              </div>
            </div>
          )}

          {tab === "anchor" && (
            <div className="gap">
              <div className="notice">
                公开锚点只解决「省掉填基础资料的苦力活」，本身不构成价值。它会进锚点库，供沙盒里的引用核查使用。
              </div>
              <Field label="研究方向（用受控词汇里的 key 或中文都行）" hint="逗号分隔">
                <input value={anchorTopics} onChange={(event) => setAnchorTopics(event.target.value)} />
              </Field>
              <div className="chips">
                {(config?.umbrellas ?? []).map((item) => (
                  <span key={item.key} className="chip">
                    {item.label}
                  </span>
                ))}
              </div>
              <div className="btn-row">
                <button className="btn" onClick={runPreview} disabled={busy}>
                  {busy ? <span className="spinner" /> : null}
                  只算泛化，不写入
                </button>
              </div>
            </div>
          )}
        </Panel>

        <div className="gap">
          <Panel title="防反推预览 · 出去的到底是什么">
            {display ? (
              <div className="gap">
                <div className="kv-row">
                  <div className="kv-k">原始指纹</div>
                  <div className="kv-v">
                    <div className="chips">
                      {(display.raw_fingerprint ?? []).slice(0, 6).map((item: string) => (
                        <span key={item} className="chip red">
                          {item}
                        </span>
                      ))}
                    </div>
                    <div className="tiny muted mt">这些是能被人肉搜到的指纹，一条都不会对外发布。</div>
                  </div>
                </div>
                <div className="kv-row">
                  <div className="kv-k">泛化后标签</div>
                  <div className="kv-v">
                    <div className="chips">
                      {(display.tags ?? []).map((tag: any) => (
                        <span key={tag.tag} className="chip lime">
                          {tag.label} · L{tag.level}
                        </span>
                      ))}
                      {(display.tags ?? []).length === 0 && <span className="muted tiny">没有可安全发布的标签</span>}
                    </div>
                  </div>
                </div>
                <div>
                  <div className="meter-row">
                    <span>同簇人数</span>
                    <Meter value={Math.min(1, (display.cohort_size ?? 0) / Math.max(k * 5, 1))} />
                    <span className="mono">
                      {display.cohort_size ?? 0} / k={display.k_target ?? k}
                    </span>
                  </div>
                  <div className="tiny muted mt">
                    {display.cohort_size >= (display.k_target ?? k)
                      ? "这条特征组合在参会池里至少对应这么多人，任何一条都无法反推到具体某位。"
                      : "同簇人数不足，系统会主动降级或删除最具体的标签，而不是硬发。"}
                  </div>
                </div>
                {(display.suppressed ?? []).length > 0 && (
                  <div className="notice">
                    被丢弃/降级的标签：<span className="mono">{(display.suppressed ?? []).join(", ")}</span>
                  </div>
                )}
                {(display.promoted ?? []).length > 0 && (
                  <div className="tiny muted">
                    升到上层类目：<span className="mono">{(display.promoted ?? []).join(", ")}</span>
                  </div>
                )}
                {(generalized?.internal_tags ?? []).length > 0 && (
                  <div className="kv-row">
                    <div className="kv-k">沙盒内标签</div>
                    <div className="kv-v">
                      <div className="chips">
                        {(generalized?.internal_tags ?? []).map((tag: string) => (
                          <span key={tag} className="chip">
                            {tag}
                          </span>
                        ))}
                      </div>
                      <div className="tiny muted mt">
                        只用于封闭沙盒里的召回与对话，不出现在卡片、导出或 AgentCard 里。
                      </div>
                    </div>
                  </div>
                )}
                <div className="tiny muted mono">cohort_key {display.cohort_key?.slice(0, 18) ?? "-"}…</div>
              </div>
            ) : (
              <div className="empty">还没有画像。先走左边的某一条录入路径。</div>
            )}
          </Panel>

          <Panel title="当前私密档案">
            {vault ? (
              <div className="gap">
                <div className="meter-row">
                  <span>简报质量</span>
                  <Meter value={vault.quality} tone="violet" />
                  <span className="mono">{Math.round(vault.quality * 100)}%</span>
                </div>
                <div className="kv">
                  <div className="kv-row">
                    <div className="kv-k">卡点</div>
                    <div className="kv-v small">{vault.bottleneck || "—"}</div>
                  </div>
                  <div className="kv-row">
                    <div className="kv-k">反共识</div>
                    <div className="kv-v small">{vault.contrarian || "—"}</div>
                  </div>
                  <div className="kv-row">
                    <div className="kv-k">隐性诉求</div>
                    <div className="kv-v">
                      <div className="chips">
                        {(vault.intent_labels ?? []).map((label: string) => (
                          <span key={label} className="chip violet">
                            {label}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="kv-row">
                    <div className="kv-k">屏蔽域名</div>
                    <div className="kv-v mono small">
                      {(vault.avoid_domains ?? []).length ? (Array.isArray(vault.avoid_domains) ? vault.avoid_domains.join(", ") : `${vault.avoid_domains} 个`) : "—"}
                    </div>
                  </div>
                  <div className="kv-row">
                    <div className="kv-k">沙盒授权</div>
                    <div className="kv-v">
                      <span className={`chip ${vault.sandbox_consent ? "lime" : "warn"}`}>
                        {vault.sandbox_consent ? "允许在封闭沙盒内解密" : "仅标签匹配（能力降级）"}
                      </span>
                    </div>
                  </div>
                </div>
                <button className="btn btn-danger btn-sm" onClick={wipe} disabled={busy}>
                  删除私密档案
                </button>
              </div>
            ) : (
              <div className="empty">还没有私密档案。没有它，你只能被按标签匹配。</div>
            )}
          </Panel>
        </div>
      </div>

      <Panel title="凭证（可选）" right={<span className="chip">只验证格式与域名，不验证身份</span>}>
        <Credentials config={config} onChange={() => profile.reload()} />
      </Panel>
    </div>
  );
}

function ConsentRow({ consent, setConsent }: { consent: boolean; setConsent: (value: boolean) => void }) {
  return (
    <label className="demo-account" style={{ cursor: "pointer" }}>
      <span>
        <span className="demo-name">允许我的 agent 在封闭沙盒里使用这份简报</span>
        <br />
        <span className="demo-role">
          关掉之后仍能按公开标签参与合作匹配，但无法进入暗池的诉求撮合，双盲解锁也不会发生。
        </span>
      </span>
      <input
        type="checkbox"
        checked={consent}
        onChange={(event) => setConsent(event.target.checked)}
        style={{ width: 18, height: 18 }}
      />
    </label>
  );
}

function Credentials({ config, onChange }: { config: Config | null; onChange: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState("orcid");
  const [subject, setSubject] = useState("");
  const [busy, setBusy] = useState(false);
  const profile = useAsync(() => api.profile(), []);

  async function add() {
    setBusy(true);
    try {
      const result = await api.addCredential(kind, subject);
      toast.push(`${result.label} 状态：${result.status}`);
      setSubject("");
      profile.reload();
      onChange();
    } catch (error) {
      toast.push((error as Error).message, "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-2">
      <div className="gap-sm">
        {(profile.data?.credentials ?? []).map((item: any) => (
          <div key={item.id} className="row-between">
            <span className="small">
              {item.label} · <span className="mono muted">{item.subject}</span>
            </span>
            <span className={`chip ${item.status === "verified" ? "lime" : "warn"}`}>{item.status}</span>
          </div>
        ))}
        {(profile.data?.credentials ?? []).length === 0 && <div className="muted small">还没有凭证。</div>}
      </div>
      <div className="gap-sm">
        <div className="form-row">
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="orcid">ORCID</option>
            <option value="github">GitHub 用户名</option>
            <option value="arxiv">arXiv 编号</option>
            <option value="doi">DOI</option>
            <option value="openreview">OpenReview 身份</option>
            <option value="institutional_email">机构邮箱</option>
          </select>
          <input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="0000-0002-1825-0097" />
        </div>
        <button className="btn btn-sm" onClick={add} disabled={busy || subject.trim().length < 3}>
          添加凭证
        </button>
        <div className="tiny muted">
          门卫只用它区分「熟人/同行」与「陌生群发」。{config?.privacy.transcript_policy === "never_persisted" ? " 沙盒对话不落盘。" : ""}
        </div>
      </div>
    </div>
  );
}
