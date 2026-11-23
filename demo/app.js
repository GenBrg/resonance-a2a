/* Resonance interactive demo — no backend, no build step, no network calls.

   Structure: a four-stage wizard. Only the current stage is in the DOM flow; the
   next one is revealed when the current one is *finished*. Inside step 3 the cards
   are also serialized — you handle one card, then the next one arrives. Both levels
   of sequencing exist for the same reason: people skip ahead and then conclude the
   product is "just a list of people".

   All data below is canned and deterministic so the page works as a static file. */

const $ = (sel) => document.querySelector(sel);

/* --------------------------------------------------------------- wizard */
const STAGE_COUNT = 4;
const unlocked = new Set([0]);
let current = 0;

function showStage(step) {
  document.querySelectorAll(".stage").forEach((node) => {
    node.hidden = Number(node.dataset.step) !== step;
  });
  $("#rail").hidden = step === 0;
  $("#hero").hidden = step !== 0;
  current = step;
  document.querySelectorAll(".rail-item").forEach((node) => {
    const n = Number(node.dataset.step);
    node.classList.toggle("active", n === step);
    node.classList.toggle("done", unlocked.has(n) && n !== step);
    node.disabled = !unlocked.has(n);
  });
  if (step > 0) {
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

function advance(to) {
  unlocked.add(to);
  showStage(to);
}

$("#btn-start").addEventListener("click", () => advance(1));

document.querySelectorAll(".rail-item").forEach((node) => {
  node.addEventListener("click", () => {
    const n = Number(node.dataset.step);
    if (unlocked.has(n)) showStage(n);
  });
});

/* ------------------------------------------------------------- step 01 */
const GENERALIZED = ["推理服务与调度", "可验证奖励推理", "长上下文与记忆"];
let personaReady = false;

$("#btn-generalize").addEventListener("click", () => {
  if (personaReady) return;
  personaReady = true;
  const button = $("#btn-generalize");
  button.disabled = true;
  button.textContent = "生成中…";

  const box = $("#generalized");
  box.innerHTML = "";
  GENERALIZED.forEach((label, index) => {
    window.setTimeout(() => {
      const chip = document.createElement("span");
      chip.className = "chip safe";
      chip.textContent = label;
      box.appendChild(chip);

      if (index === GENERALIZED.length - 1) {
        $("#cohort").hidden = false;
        button.textContent = "分身已生成";
        $("#step1-hint").textContent = "进入下一步：今晚的暗池";
        const next = document.createElement("button");
        next.type = "button";
        next.className = "btn primary";
        next.textContent = "下一步 · 看暗池怎么跑";
        next.addEventListener("click", () => advance(2));
        button.after(next);
        next.focus({ preventScroll: true });
      }
    }, 240 * index);
  });
});

/* ------------------------------------------------------------- step 02 */
const DIALOGUE = [
  {
    who: "Peer_A",
    text: "我在探索推理与 RL 结合方向的创业，目前卡点是千卡级异步 rollout 阶段，KV cache 换入换出把长尾延迟顶起来了。听说你在推理引擎侧有实战，想确认你是否也在看早期机会？",
  },
  {
    who: "Peer_B",
    text: "是的，正在找算法背景的联创。不过我更关心你说的长尾是卡在通信带宽还是显存碎片？我之前在 [已脱敏] 里用分层分页调度压掉过四成尾延迟，代价是吞吐掉 6%。",
  },
  {
    who: "Peer_A",
    text: "主要是显存碎片叠加不同长度序列的同步等待。如果把策略更新改成细粒度异步流水线，你的分页调度在权重热更新时怎么避免显存峰值翻倍？",
  },
  {
    who: "Peer_B",
    text: "双缓冲权重指针加页表延迟回收。前提是算法侧能容忍 1–2 个 step 的 off-policy 偏差，你的框架能接受吗？",
  },
];

let ranOnce = false;

$("#btn-run").addEventListener("click", () => {
  if (ranOnce) return;
  ranOnce = true;
  const button = $("#btn-run");
  button.disabled = true;
  button.textContent = "运行中…";
  $("#step2-hint").textContent = "匿名 · 封闭 · 两轮 · 记录不留";

  const box = $("#dialogue");
  box.innerHTML = "";

  DIALOGUE.forEach((turn, index) => {
    window.setTimeout(() => {
      const node = document.createElement("div");
      node.className = `msg ${turn.who === "Peer_B" ? "b" : "a"}`;
      node.innerHTML = `<span class="who">${turn.who}</span><p></p>`;
      node.querySelector("p").textContent = turn.text;
      box.appendChild(node);

      if (index === DIALOGUE.length - 1) window.setTimeout(showScores, 500);
    }, 850 * index + 150);
  });
});

function showScores() {
  $("#scores").hidden = false;
  document.querySelectorAll(".score-n").forEach((node) => {
    const target = Number(node.dataset.target);
    let value = 40;
    const timer = window.setInterval(() => {
      value = Math.min(target, value + 3);
      node.textContent = value;
      if (value >= target) window.clearInterval(timer);
    }, 40);
  });

  window.setTimeout(() => {
    $("#verdict-ok").hidden = false;
    $("#side-pair").hidden = false;
    $("#step2-hint").textContent = "明早 8 点，卡片出现在口袋里";
    $("#btn-run").hidden = true;
    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn primary";
    next.textContent = "下一步 · 早上八点开手机";
    next.addEventListener("click", () => advance(3));
    $("#step2-hint").before(next);
    next.focus({ preventScroll: true });
    if (!cardsStarted) startCards();
  }, 900);
}

/* ------------------------------------------------------------- step 03 */
/* Three cards, strictly serialized. Card 1 teaches the double-blind unlock, card 2
   the micro-group, card 3 the proximity handshake — each is a different reason a
   card exists, and each consumes one slot of the daily budget. */
const CARDS = [
  {
    kind: "1v1 · 双盲待解锁",
    title: "共振 · 可验证奖励推理",
    state: "未解锁",
    rows: [
      ["为什么", "你们在异步 rollout 与 KV 调度上的卡点直接互补，对方的回答里有具体的机制和代价。"],
      ["第一句", "双缓冲切换指针时，怎么保证旧版权重还在被读的时候不被回收？"],
      ["时间", "Day 1 · 14:30–14:50 · Level 2 咖啡角"],
    ],
    sealed: true,
    quotaHint: "先处理这张 1v1 —— 对方是谁，要等你们俩都点同意",
    actions: [
      { label: "同意，解锁身份", primary: true, act: "accept" },
      { label: "暂不感兴趣", act: "decline" },
    ],
  },
  {
    kind: "微圆桌 · 4 人",
    title: "长上下文与记忆 · 咖啡圆桌",
    state: "待加入",
    rows: [
      ["谁", "你 + 3 位做长上下文、剪枝和评测的人（不同机构）"],
      ["为什么", "一个人聊天有压力，四个人围一桌没有。这类桌子比 1v1 更容易聊出东西。"],
      ["议程", "50 分钟 · 各自卡点 → 互相给一条能立刻试的做法 → 定下一个动作"],
    ],
    quotaHint: "第 2 张 —— 系统优先给你拼桌，而不是又一个 1v1",
    actions: [
      { label: "加入这张桌子", primary: true, act: "join" },
      { label: "这次不了", act: "skip" },
    ],
  },
  {
    kind: "附近 · 同一展厅",
    title: "附近有人想打个招呼",
    state: "待回应",
    rows: [
      ["位置", "Poster Board 2B · 你现在就在这里"],
      ["共同方向", "推理服务与调度"],
      ["有效期", "15 分钟后自动失效，不留足迹"],
    ],
    quotaHint: "第 3 张 —— 线上的事线上解决，线下的擦肩而过也接住",
    actions: [
      { label: "打个招呼", primary: true, act: "wave" },
      { label: "忽略", act: "ignore" },
    ],
  },
];

let cardIndex = 0;
let cardsStarted = false;

function startCards() {
  cardsStarted = true;
  cardIndex = 0;
  renderCard();
}

function updateQuota(done) {
  $("#quota-count").textContent = `${done} / 3`;
}

function renderCard() {
  const host = $("#card-host");
  host.innerHTML = "";
  updateQuota(cardIndex);

  if (cardIndex >= CARDS.length) {
    $("#quota-count").textContent = "3 / 3";
    $("#quota-hint").textContent = "今天的额度用完了 —— 系统不会再多推一条";
    $("#step3-next").hidden = false;
    return;
  }

  const data = CARDS[cardIndex];
  $("#quota-hint").textContent = data.quotaHint;

  const card = document.createElement("article");
  card.className = "card";
  card.innerHTML = `
    <div class="card-top">
      <span class="card-kind"></span>
      <span class="chip ghost" data-role="state"></span>
    </div>
    <h3></h3>
    <dl></dl>
    <div class="card-msg" data-role="msg" hidden></div>
    <div class="revealed" data-role="revealed" hidden></div>
    <div class="actions" data-role="actions"></div>
  `;
  card.querySelector(".card-kind").textContent = data.kind;
  card.querySelector("[data-role=state]").textContent = data.state;
  card.querySelector("h3").textContent = data.title;

  const dl = card.querySelector("dl");
  data.rows.forEach(([key, value]) => {
    const row = document.createElement("div");
    row.innerHTML = "<dt></dt><dd></dd>";
    row.querySelector("dt").textContent = key;
    row.querySelector("dd").textContent = value;
    dl.appendChild(row);
  });

  if (data.sealed) {
    const sealed = document.createElement("div");
    sealed.className = "sealed";
    sealed.innerHTML = "<b>双方确认前，彼此都只看到匿名画像</b>";
    dl.after(sealed);
  }

  const actions = card.querySelector("[data-role=actions]");
  data.actions.forEach((action) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `btn ${action.primary ? "primary" : "ghost"}`;
    button.textContent = action.label;
    button.addEventListener("click", () => handleAction(card, data, action, actions));
    actions.appendChild(button);
  });

  host.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function handleAction(card, data, action, actions) {
  actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const msg = card.querySelector("[data-role=msg]");
  const state = card.querySelector("[data-role=state]");

  const done = (text, tone) => {
    msg.hidden = false;
    msg.className = `card-msg ${tone || ""}`;
    msg.textContent = text;
    nextCard();
  };

  if (cardIndex === 0) {
    // the canonical unlock flow: one-sided accept must NOT reveal anything
    if (action.act === "decline") {
      done("已拒绝。对方不会收到任何通知，也不会知道你看过。", "muted");
      return;
    }
    state.textContent = "已同意 · 等对方";
    msg.hidden = false;
    msg.className = "card-msg";
    msg.textContent = "你点了同意。但对方还没点，所以你们彼此仍然看不到对方是谁。";
    window.setTimeout(() => {
      card.querySelector(".sealed").hidden = true;
      const revealed = card.querySelector("[data-role=revealed]");
      revealed.hidden = false;
      revealed.innerHTML = `
        <div class="peer-name">陈哲</div>
        <div class="peer-meta">RL 算法研究员 · Singapore AI Lab · 悉尼会场</div>
        <div class="peer-line">共同方向：可验证奖励推理</div>
        <div class="peer-line">你们约在 Day 1 · 14:30 · Level 2 咖啡角</div>
      `;
      state.textContent = "已解锁";
      state.className = "chip safe";
      msg.textContent = "对方也点了同意 → 真名、机构、日程同时解锁。";
      nextCard();
    }, 2000);
    return;
  }

  if (cardIndex === 1) {
    if (action.act === "join") {
      done("已加入。桌子和议程已经在你日程里，4 个人都确认后自动定下来。", "ok");
    } else {
      done("这次不了。桌子不会等你，但也不会追着你。", "muted");
    }
    return;
  }

  done(
    action.act === "wave"
      ? "已打招呼。只有对方回应，你才会知道是谁 —— 没回应就当没见过。"
      : "已忽略。15 分钟后这条信号自动失效。",
    action.act === "wave" ? "ok" : "muted",
  );
}

function nextCard() {
  cardIndex += 1;
  updateQuota(cardIndex);
  window.setTimeout(() => {
    if (cardIndex >= CARDS.length) {
      renderCard();
    } else {
      const host = $("#card-host");
      host.style.opacity = "0";
      window.setTimeout(() => {
        host.style.opacity = "1";
        renderCard();
      }, 260);
    }
  }, 900);
}

$("#btn-to-step4").addEventListener("click", () => advance(4));

/* ------------------------------------------------------------- step 04 */
const DOSSIER = `# 我的 NeurIPS 2026 连接纪要

> 演示样例 · 全部姓名与机构均为虚构

## 一、双向确认过的连接

### 陈哲 · 已双向解锁
- 共同方向：可验证奖励推理
- 为什么值得见：他在异步 rollout 与 KV 调度上的卡点，与你的分层分页方案直接互补
- 第一句：双缓冲切换指针时，怎么保证旧版权重还在被读的时候不被回收？
- 约定：Day 1 · 14:30–14:50 · Level 2 咖啡角

## 二、微圆桌
- 长上下文与记忆 · 4 人 · Day 2 19:00 主题晚餐
- 议题：各自的卡点、最贵的一次踩坑、下次可复用的东西

## 三、偏好校准（给下一个 agent 用）
- 真正让我有兴趣的人：能说出具体机制与代价，而不是只谈方向
- 雷区：只讲商业模式、没有开源交付记录
- 当前卡点：KV cache 换入换出的长尾延迟
- 反共识：纯堆合成数据长度已见顶，杠杆在调度

## 四、隐私账本
- 沙盒场次：18
- 双向达标：14
- 单向不达标、静默销毁：4
- 原始对话留存：0（结构上不落盘）
`;

$("#btn-export").addEventListener("click", () => {
  const blob = new Blob([DOSSIER], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "neurips-2026-连接纪要.md";
  anchor.click();
  URL.revokeObjectURL(url);
  $("#preview").textContent = DOSSIER;
});
