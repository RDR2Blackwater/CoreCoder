<div align="center">

# CoreCoder

This is a forked project, mainly focusing on improvements related to MCP connections.

[Click me to go to the original project](https://github.com/he-yufeng/CoreCoder)

</div>

## MCP servers

Put a `mcp.json` under `.corecoder` in **your running directory**, any MCP server tool can connect to the agent via stdio, and the same config shape as Claude Code's:

```json
{
  "mcpServers": {
    "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]}
  }
}
```

What improvements were made here?

1. Improved MCPClient connection by implementing MCP Server Ping responses to prevent connection interruptions, and added handling for Resources, Prompts, and non-text binary responses.
2. Encapsulated the retrieval of Resources and Prompts into Tools, allowing the Agent to decide whether to call it or not.

What hasn’t been done regarding MCP?

1. [List Changed Notification](https://modelcontextprotocol.io/specification/2025-06-18/server/resources#list-changed-notification). Resources aren’t always static. When the MCP Server’s Resources and Prompts change, the MCP Server sends a JSON message to notify the client to refresh the content, but our implementation doesn’t include dynamic updates.
2. [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#streamable-http). It supports better concurrency and security authentication, which our current Stdio connection can’t do.
3. Monitoring the MCP Server. If the MCP Server dies for some reason, MCPClient doesn’t automatically reconnect, and subsequent calls in this session will keep returning error strings. A periodic Ping should be added to monitor the MCP Server.

And there’s a lot more!

---

CoreCoder creator: [Yufeng He](https://github.com/he-yufeng), formerly at Moonshot AI (Kimi). He earlier wrote a fairly complete [Claude Code source analysis](https://zhuanlan.zhihu.com/p/1898797658343862272) on Zhihu; this project is its hands-on counterpart: that one walks you through reading it, this one through rebuilding it.

> CoreCoder was formerly named NanoCoder; it was renamed to avoid confusion with [Nano-Collective/nanocoder](https://github.com/Nano-Collective/nanocoder), and old links redirect here automatically.
