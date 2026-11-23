/* Resonance interactive demo — no backend, no build step, no network calls.
   Everything below is deterministic canned data so the page works as a static
   file (GitHub Pages / local double-click alike). */

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------- step 01 */
const GENERALIZED = [
  "推理服务与调度",
  "可验证奖励推理",
  "长上下文与记忆",
];

$("#btn-generalize").addEventListener("click", () => {
  const box = $("#generalized");
  const cohort = $("#cohort");
  box.innerHTML = "";
  GENERALIZED.forEach((label, index) => {
    window.setTimeout(() => {
      const chip = document.createElement("span");
      chip.className = "chip safe";
      chip.textContent = label;
      box.appendChild(chip);
      if (index === GENERALIZED.length - 1) {
        cohort.hidden = false;
        $("#generalized-note").textContent =
          "对外只发这三个通用标签。每一条背后都至少有十几个人，拼在一起也反推不到你。";
        $("#btn-generalize").disabled = true;
        $("#step1-hint").textContent = "分身已生成 · 进入下一步";
        document.getElementById("step2").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, 260 * index);
  });
});

$("#btn-voice").addEventListener("click", () => {
  $("#btn-voice").disabled = true;
  $("#step1-hint").textContent = "（演示里跳过录音）已从 60 秒自述里抽出卡点、反共识和诉求";
  $("#btn-generalize").click();
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

$("#btn-run").addEventListener("click", () => {
  const button = $("#btn-run");
  button.disabled = true;
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
      node.scrollIntoView({ behavior: "smooth", block: "nearest" });

      if (index === DIALOGUE.length - 1) {
        window.setTimeout(showScores, 600);
      }
    }, 900 * index + 200);
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
    $("#step2-hint").textContent = "明早 8 点，卡片会出现在两个人口袋里";
    document.getElementById("step3").scrollIntoView({ behavior: "smooth", block: "start" });
  }, 900);
}

/* ------------------------------------------------------------- step 03 */
$("#btn-accept").addEventListener("click", () => {
  const button = $("#btn-accept");
  button.disabled = true;
  button.textContent = "已同意 · 等对方";
  $("#step3-hint").textContent = "对方还没点，所以你们彼此都还看不到对方是谁";

  window.setTimeout(() => {
    $("#sealed").hidden = true;
    $("#revealed").hidden = false;
    $("#card-state").textContent = "已解锁";
    $("#card-state").className = "chip safe";
    button.hidden = true;
    $("#btn-decline").hidden = true;
    $("#step3-hint").textContent = "双方都同意 → 真名、机构、日程同时解锁";
    document.getElementById("step4").scrollIntoView({ behavior: "smooth", block: "start" });
  }, 2000);
});

$("#btn-decline").addEventListener("click", () => {
  $("#step3-hint").textContent = "已拒绝。对方不会收到任何通知，也不会知道你看过。";
  $("#btn-accept").disabled = true;
  $("#btn-decline").disabled = true;
});

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
