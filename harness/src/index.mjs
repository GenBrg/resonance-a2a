#!/usr/bin/env node
/**
 * Resonance harness bridge — a dependency-free MCP server over stdio.
 *
 * Design constraints, in order of importance:
 *
 * 1. **No write tools.** This process exposes three HTTP calls and nothing else. It
 *    cannot read files, shell out, or reach a network host other than the relay.
 *    A malicious peer message that lands in the host model's context has nothing to
 *    hijack here.
 * 2. **Cloud mailbox, local brain.** The relay holds a queue. All judgement about
 *    whether a card is worth the owner's time happens on the owner's machine, with
 *    the owner's context. The relay never sees that reasoning.
 * 3. **Fail closed.** Missing token or unreachable relay produces an error string,
 *    not a silent no-op.
 */

import process from "node:process";

const RELAY = (process.env.RESONANCE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN = process.env.RESONANCE_PEER_TOKEN || "";

const TOOLS = [
  {
    name: "resonance_inbox",
    description:
      "拉取今天的待处理卡片（双盲待解锁的 1v1、微圆桌邀请、附近打招呼、门卫申请）。只返回卡片，不返回任何沙盒对话。",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "resonance_reply",
    description: "对一张卡片做决定：accept 或 decline。可在本地结合主人的私密上下文判断后再调用。",
    inputSchema: {
      type: "object",
      properties: {
        card_id: { type: "string", description: "卡片 id" },
        action: { type: "string", enum: ["accept", "decline"] },
        slot_id: { type: "string", description: "可选：选择哪个见面时段" },
        reason: { type: "string", description: "可选：本地判断理由，仅回传给你自己，不上传" },
      },
      required: ["card_id", "action"],
      additionalProperties: false,
    },
  },
  {
    name: "resonance_pitch",
    description:
      "向某位参会者的门卫提交一份结构化 Pitch（你想要什么 / 为什么是你 / 一件可核验证据 / 占用时长）。返回 tier 与原因码。",
    inputSchema: {
      type: "object",
      properties: {
        target_id: { type: "string", description: "对方 user id（来自对方 AgentCard）" },
        ask: { type: "string" },
        why_you: { type: "string" },
        evidence: { type: "string" },
        time_ask: { type: "string", enum: ["10min", "15min", "20min"] },
      },
      required: ["target_id", "ask", "why_you", "evidence"],
      additionalProperties: false,
    },
  },
];

async function call(path, init = {}) {
  if (!TOKEN) throw new Error("RESONANCE_PEER_TOKEN 未设置：先在 Web 端生成 MCP 绑定凭据");
  const response = await fetch(`${RELAY}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-Resonance-Peer": TOKEN,
      ...(init.headers || {}),
    },
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text };
  }
  if (!response.ok) {
    const message = payload?.error?.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

async function invoke(name, args) {
  switch (name) {
    case "resonance_inbox":
      return call("/api/harness/inbox");
    case "resonance_reply":
      return call("/api/harness/reply", {
        method: "POST",
        body: JSON.stringify({
          card_id: args.card_id,
          action: args.action,
          slot_id: args.slot_id ?? null,
        }),
      });
    case "resonance_pitch":
      return call("/api/gatekeeper/requests", {
        method: "POST",
        body: JSON.stringify({
          target_id: args.target_id,
          ask: args.ask,
          why_you: args.why_you,
          evidence: args.evidence,
          time_ask: args.time_ask ?? "15min",
        }),
      });
    default:
      throw new Error(`未知工具 ${name}`);
  }
}

/* ------------------------------------------------------------ MCP plumbing */
function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function reply(id, result) {
  send({ jsonrpc: "2.0", id, result });
}

function fail(id, code, message) {
  send({ jsonrpc: "2.0", id, error: { code, message } });
}

async function handle(message) {
  const { id, method, params } = message;
  if (method === "initialize") {
    reply(id, {
      protocolVersion: params?.protocolVersion ?? "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "resonance-harness", version: "1.0.0" },
    });
    return;
  }
  if (method === "tools/list") {
    reply(id, { tools: TOOLS });
    return;
  }
  if (method === "tools/call") {
    const name = params?.name;
    const args = params?.arguments ?? {};
    try {
      const result = await invoke(name, args);
      reply(id, {
        content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        isError: false,
      });
    } catch (error) {
      reply(id, {
        content: [{ type: "text", text: `调用失败：${error.message}` }],
        isError: true,
      });
    }
    return;
  }
  if (method === "ping") {
    reply(id, {});
    return;
  }
  if (id !== undefined) fail(id, -32601, `method not found: ${method}`);
}

let buffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      continue;
    }
    handle(message).catch((error) => {
      if (message?.id !== undefined) fail(message.id, -32603, error.message);
    });
  }
});

process.stderr.write(`resonance-harness ready · relay=${RELAY} · token=${TOKEN ? "set" : "MISSING"}\n`);
