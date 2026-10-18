#!/usr/bin/env node
/**
 * Adds the "sudowin-semcomp" MCP server to the OpenCode global config.
 *
 * Usage:  node setup-sudowin-mcp.mjs
 *
 * Writes to <XDG_CONFIG_HOME|~/.config>/opencode/opencode.json and preserves
 * every other setting already in that file.
 *
 * The client secret is written into the file verbatim. To keep it out of the
 * file instead, replace the CLIENT_SECRET value below with the string
 * "{env:MCP_CLIENT_SECRET}" and set that variable for OpenCode itself.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const SERVER_NAME = "sudowin-semcomp";
const SERVER_URL = "https://consumer-mcp.ifood.com.br/marketplace";
const CLIENT_ID = "3q0Cz-sbQhDqH0yuqoaAZGMAvkamub02";
const CLIENT_SECRET =
  "25kU6XNbyptRrxG6BUXvtL1bFxN-robK_VgFYR5TbFwPmHs1Xbi9CfF-tbfqrBoB";

/**
 * iFood's protected-resource metadata points at authorization server
 * "https://id.ifood.com.br", but the metadata document it serves there declares
 * issuer "https://id.ifood.com.br/3p". OpenCode validates the issuer per
 * RFC 8414 §3.3 and aborts OAuth discovery on the mismatch, so we pin the
 * metadata document explicitly and skip that resolution step.
 */
const AUTH_SERVER_METADATA_URL =
  "https://id.ifood.com.br/.well-known/openid-configuration";

/**
 * iFood matches redirect URIs exactly, so this must be byte-for-byte the URI
 * registered for the client: host "localhost" (not 127.0.0.1), port 8080,
 * path "/agent". OpenCode derives its local callback listener from this URI,
 * which is why the port has to be free during sign-in.
 */
const REDIRECT_URI = "http://localhost:8080/agent";
const CALLBACK_PORT = 8080;

const configPath = join(
  process.env.XDG_CONFIG_HOME || join(homedir(), ".config"),
  "opencode",
  "opencode.json",
);

function readConfig(file) {
  if (!existsSync(file)) return {};
  const raw = readFileSync(file, "utf8");
  try {
    return JSON.parse(raw);
  } catch {
    // Tolerate JSONC: drop comments and trailing commas, then retry.
    const stripped = raw
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:"'\\])\/\/[^\n]*/g, "$1")
      .replace(/,(\s*[}\]])/g, "$1");
    try {
      return JSON.parse(stripped);
    } catch (error) {
      console.error(`could not parse ${file}: ${error.message}`);
      console.error("fix the file by hand, then re-run this script.");
      process.exit(1);
    }
  }
}

const config = readConfig(configPath);
config.$schema ??= "https://opencode.ai/config.json";
config.mcp ??= {};
config.mcp.servers ??= {};
config.mcp.servers[SERVER_NAME] = {
  type: "remote",
  url: SERVER_URL,
  oauth: {
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET,
    auth_server_metadata_url: AUTH_SERVER_METADATA_URL,
    redirect_uri: REDIRECT_URI,
  },
};

mkdirSync(dirname(configPath), { recursive: true });
writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n");

console.log(`updated ${configPath}`);
console.log(`added   mcp.servers.${SERVER_NAME}`);

// The sign-in browser redirect lands on the loopback port, so a busy port
// turns into a confusing failure later. Warn now instead.
const portBusy = await new Promise((resolve) => {
  const probe = createServer();
  probe.once("error", (error) => resolve(error.code === "EADDRINUSE"));
  probe.once("listening", () => probe.close(() => resolve(false)));
  probe.listen(CALLBACK_PORT);
});
console.log("");
if (portBusy) {
  console.log(`warning: port ${CALLBACK_PORT} is already in use.`);
  console.log(`         free it before signing in, or the browser redirect will fail.`);
}

// Best-effort connection check. Inherits stdio so OpenCode's own output and
// any OAuth prompt stay visible in the terminal.
spawnSync("opencode mcp list", { shell: true, stdio: "inherit" });

console.log("");
console.log("next: sign in with the OAuth flow.");
console.log("      in OpenCode: run /mcps, pick \"sudowin-semcomp\", and complete the browser sign-in.");
console.log(`      the browser must reach localhost:${CALLBACK_PORT}, and that port must stay free until it does.`);
