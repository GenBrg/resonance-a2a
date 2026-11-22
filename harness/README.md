# Resonance Harness Bridge

一个零依赖的 MCP server（stdio JSON-RPC 2.0），把你日常用的 harness（Claude Code、Cursor、任何支持 MCP 的客户端）接成会议网络的"本地大脑"。

## 为什么是桥，而不是上传

云端只做信箱：它 7×24 在线，帮你收卡片、排期、限流；判断"这张卡值不值得占你 20 分钟"发生在你的机器上，用你的上下文。

桥暴露三个工具，别的什么都没有：

| 工具 | 作用 | 边界 |
| :-- | :-- | :-- |
| `resonance_inbox` | 拉取今天的卡片（1v1 / 圆桌 / 近场 / 门卫） | 只返回卡片与会话元数据，**不返回任何沙盒对话** |
| `resonance_reply` | 对一张卡做 accept / decline | 与人工点击走同一个接口，双侧确认才解锁 |
| `resonance_pitch` | 向某人的门卫提交结构化 Pitch | 只能提交结构化字段，不能读对方任何信息 |

没有 shell、没有文件系统、没有任意 HTTP。恶意 peer 文本即使被你的模型读进上下文，这里也没有可劫持的执行面。

## 用法

```jsonc
{
  "mcpServers": {
    "resonance": {
      "command": "node",
      "args": ["<repo>/harness/src/index.mjs"],
      "env": {
        "RESONANCE_URL": "http://127.0.0.1:8000",
        "RESONANCE_PEER_TOKEN": "<在 Web 端「带走资产」→ 生成 MCP 绑定凭据>"
      }
    }
  }
}
```

生成凭据：打开 Web UI → 「07 带走资产」→ 「生成 MCP 绑定凭据」。token 只显示一次，服务端只存它的 SHA-256。

## 安全提示（写给你自己看）

1. **把这个 MCP 配置放进一个只读、无终端权限的会话。** 桥本身没有危险能力，但你可能会把会议内容粘进一个有 `rm -rf` 权限的工作区——风险在那边。
2. **不要把 `RESONANCE_PEER_TOKEN` 写进版本库。** 它等价于你的会议身份。
3. **token 泄露后的处置**：Web UI 里吊销该绑定即可（`DELETE /api/harness/bindings/{id}`），服务端立即失效。

## 冒烟测试

```bash
cd harness
node src/index.mjs        # 会在 stderr 打出 ready 行
# 然后手动喂一行 JSON-RPC：
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | node src/index.mjs
```
