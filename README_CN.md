<div align="center">

# CoreCoder

这是一个 Fork 的项目，主要关注的是 MCP 连接相关的改进。

[点我进原项目地址](https://github.com/he-yufeng/CoreCoder)

</div>

## MCP 服务器

在**你的运行目录下的** `.corecoder` 下放一个 `mcp.json`，任何 MCP 服务器的工具就能通过 stdio 接进 agent，配置形状和 Claude Code 的一样：

```json
{
  "mcpServers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
  }
}
```

这里的改进做了什么？

1. 完善 MCPClient 连接，实现 MCP Server Ping 响应，防止连接中断，并加入对 Resources、Prompts、非文本的二进制响应的处理方式。
2. 将 Resources 和 Prompts 的获取封装为 Tools，让 Agent 自己决定要不要调用。

关于MCP，还有什么是没有做到的？

1. [List Changed Notification](https://modelcontextprotocol.io/specification/2025-06-18/server/resources#list-changed-notification)。资源不总是一成不变的，当 MCP Server 的 Resources 和 Prompts 产生变动时，MCP Server 会发送一条 JSON 信息通报客户端以重新获取内容，但我们的实现没有包括动态更新。
2. [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http)。它支持更好的并发性能和安全认证机制，这是目前实现的 Stdio 连接所做不到的。
3. 对 MCP Server 的监测。当 MCP Server 因各种原因死亡时，MCPClient 不会自动重新建立连接，本次会话里对它的后续调用会继续返回错误字符串。应该加入一个定期的 Ping，监测 MCP Server 的情况。

还有很多！

---

CoreCoder 作者：[何宇峰](https://github.com/he-yufeng)，曾任职 Moonshot AI (Kimi)。早前写过一篇相当完整的 [Claude Code 源码分析](https://zhuanlan.zhihu.com/p/1898797658343862272)，这个项目是它的动手版：那篇带你读懂，这个带你重建。

> CoreCoder 原名 NanoCoder，为避免和 [Nano-Collective/nanocoder](https://github.com/Nano-Collective/nanocoder) 混淆而改名，旧链接会自动跳到这里。
