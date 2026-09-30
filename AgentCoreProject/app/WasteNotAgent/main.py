import os, json, urllib.parse, urllib.request
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client

REGION = "us-west-2"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
# The gateway MCP URL is injected by the AgentCore CLI at deploy as
# AGENTCORE_GATEWAY_<NAME>_URL (gateway "workshop-gateway" -> WORKSHOP_GATEWAY).
GATEWAY_URL = os.environ.get("AGENTCORE_GATEWAY_WORKSHOP_GATEWAY_URL", os.environ.get("GATEWAY_URL", ""))
TOKEN_ENDPOINT = os.environ.get("GATEWAY_TOKEN_ENDPOINT", "")
CLIENT_ID = os.environ.get("GATEWAY_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GATEWAY_CLIENT_SECRET", "")
SCOPE = os.environ.get("GATEWAY_SCOPE", "")

SYSTEM_PROMPT = """You are WasteNot, a food-rescue coordinator. Classify food and safe
holding time, match nearby recipients by need and distance, exclude any whose dietary
constraints the food violates (a hard rule), dispatch a driver, notify parties, and
record impact."""

def _gateway_token():
    body = urllib.parse.urlencode({"grant_type": "client_credentials",
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "scope": SCOPE}).encode()
    req = urllib.request.Request(TOKEN_ENDPOINT, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)["access_token"]

def _gateway_client():
    token = _gateway_token()
    return MCPClient(lambda: streamablehttp_client(GATEWAY_URL,
        headers={"Authorization": f"Bearer {token}"}))

app = BedrockAgentCoreApp()
model = BedrockModel(model_id=MODEL_ID, region_name=REGION)

@app.entrypoint
def handler(event):
    prompt = event.get("prompt", "")
    if GATEWAY_URL:
        gw = _gateway_client()
        with gw:
            agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=gw.list_tools_sync())
            return agent(prompt)
    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT)
    return agent(prompt)

app.run()
