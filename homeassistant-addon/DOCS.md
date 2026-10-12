# DoSync

DoSync sits between an AI agent (the mind) and your devices (the body), like a
nervous system: the agent asks for an outcome, DoSync decides which devices may
act, applies your rules, holds what you marked for your confirmation, and keeps
a tamper-evident record. It is the reference hub of SAMUEL, a standard for what
an agent may do to physical devices — in a home, a workshop or a plant.

## Start

1. Install the add-on and press **Start**.
2. Open the **Log** tab: on first start the hub prints its token.
3. Press **Open web UI**, paste the token, and press **Connect**.
4. With **Govern Home Assistant's devices** on, your entities appear as devices.

## Your rules

In the dashboard, **Governance** is where you say where each device is, what it
does in an emergency, and which actions must wait for you. Held actions appear
under **⏸**: confirm or deny them there. Releasing one takes the **operator
token** you set in this add-on's configuration — keep it to yourself; it must be
different from the token you give your agent.

## Connect your agent

In the dashboard press **Agent** — it shows the configuration for any MCP client
(Claude Desktop, Claude Code, Cursor, …). For an agent on another computer, see
the project README, "Connecting an agent".

## Devices described by W3C WoT

Every governed device is also published as a W3C WoT Thing Description at
`/v1/things`, and `/.well-known/wot` describes the directory, so WoT tools can
use them — through the same rules.

More: https://github.com/giulianireg-spec/dosync-protocol
