# Accepted architecture

One Cloudflare Worker in ChangePoint, with direct HTTPS API requests, its own scheduled
handler and an exported SQLite Durable Object for encrypted session/job state and write
coordination. No separate Worker, server or permanent browser process.

The source Python client supplied endpoint paths, schemas and the booking funnel. Its
logic was ported to JavaScript, using the actual MCP SDK in the Workers runtime. A live
public search from Cloudflare returned HTTP 206; authenticated account access remains
subject to a valid Doctolib login and live verification.

The Browser Run/Containers alternative was investigated but not selected or deployed.
Do not use the older architecture proposal as evidence that those services are required.
