#!/usr/bin/env bash
# Predefined WasteNot agent setup. Fixed sequence so every team gets the same agent,
# tool Lambda, memory, and gateway. Requires the AgentCore CLI (@aws/agentcore, Node
# 20+; run `agentcore --version` to confirm the Node CLI), the AWS CLI v2, and AWS
# credentials for us-west-2. CodeZip builds in the cloud, so no local Docker.
# VALIDATE END TO END IN A SANDBOX before the event (confirm the agent-to-gateway bind).
set -euo pipefail

REGION="us-west-2"
PROJECT="AgentCoreProject"
AGENT="WasteNotAgent"
GATEWAY="workshop-gateway"
TOOL_LAMBDA="workshop-wastenot-tools"
COGNITO_POOL="workshop-gateway-auth"
export AWS_DEFAULT_REGION="$REGION"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
LAMBDA_ROLE_ARN="$(aws ssm get-parameter --name /app/workshop/lambda/execution-role-arn --query Parameter.Value --output text)"

echo ">> 1/8 Scaffold a Strands code agent (project $PROJECT, agent $AGENT)"
if [ ! -d "$PROJECT" ]; then
  agentcore create --project-name "$PROJECT" --name "$AGENT" \
    --framework Strands --model-provider Bedrock --protocol HTTP \
    --build CodeZip --memory none
fi
cd "$PROJECT"
mkdir -p lambda_functions/wastenot tool_specs

echo ">> 2/8 Write the tool Lambda and tool spec"
cat > lambda_functions/wastenot/handler.py <<'PY'
import boto3
REGION = "us-west-2"
ddb = boto3.resource("dynamodb", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)
geo_places = boto3.client("geo-places", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
def _p(n): return ssm.get_parameter(Name=n)["Parameter"]["Value"]
DONORS = _p("/app/workshop/wastenot/donors-table")
RECIPIENTS = _p("/app/workshop/wastenot/recipients-table")
DRIVERS = _p("/app/workshop/wastenot/drivers-table")
ALERTS = _p("/app/workshop/wastenot/alerts-topic-arn")
def handler(event, context):
    raw = context.client_context.custom["bedrockAgentCoreToolName"]
    tool = raw.split("___")[-1] if "___" in raw else raw.split("__")[-1]
    return TOOLS[tool](**event)
def register_surplus(donor_id, food_type, quantity, expiry_time, dietary_info):
    return {"listing_id": f"L-{donor_id}-{food_type}", "status": "listed", "quantity": quantity}
def find_matches(food_type, quantity, location, radius_km, dietary_info=""):
    pt = geo_places.geocode(QueryText=location, MaxResults=1)["ResultItems"][0]["Position"]
    rows = ddb.Table(RECIPIENTS).scan().get("Items", [])
    ok = [r for r in rows if dietary_info not in r.get("dietary_needs", "")]
    return {"point": pt, "candidates": [r["recipient_id"] for r in ok[:3]]}
def dispatch_driver(pickup, drops):
    rows = ddb.Table(DRIVERS).scan().get("Items", [])
    avail = [x for x in rows if x.get("available") == "true"]
    return {"driver_id": avail[0]["driver_id"] if avail else None, "stops": len(drops)}
def confirm_delivery(delivery_id, recipient_id, quantity):
    return {"delivered": True, "recipient_id": recipient_id, "quantity": quantity}
def get_impact(donor_id, period):
    return {"donor_id": donor_id, "period": period, "note": "compute meals rescued and cost avoided"}
TOOLS = {"register_surplus": register_surplus, "find_matches": find_matches,
         "dispatch_driver": dispatch_driver, "confirm_delivery": confirm_delivery,
         "get_impact": get_impact}
PY
printf 'boto3\n' > lambda_functions/wastenot/requirements.txt

cat > tool_specs/wastenot.json <<'JSON'
[
  {"name": "register_surplus", "description": "List surplus food from a donor.", "inputSchema": {"type": "object", "properties": {"donor_id": {"type": "string"}, "food_type": {"type": "string"}, "quantity": {"type": "number"}, "expiry_time": {"type": "string"}, "dietary_info": {"type": "string"}}, "required": ["donor_id", "food_type", "quantity", "expiry_time", "dietary_info"]}},
  {"name": "find_matches", "description": "Rank nearby recipients by need and distance, excluding dietary conflicts.", "inputSchema": {"type": "object", "properties": {"food_type": {"type": "string"}, "quantity": {"type": "number"}, "location": {"type": "string"}, "radius_km": {"type": "number"}, "dietary_info": {"type": "string"}}, "required": ["food_type", "quantity", "location", "radius_km"]}},
  {"name": "dispatch_driver", "description": "Assign an available driver for a pickup and drops.", "inputSchema": {"type": "object", "properties": {"pickup": {"type": "string"}, "drops": {"type": "array", "items": {"type": "string"}}}, "required": ["pickup", "drops"]}},
  {"name": "confirm_delivery", "description": "Confirm a delivery and update impact.", "inputSchema": {"type": "object", "properties": {"delivery_id": {"type": "string"}, "recipient_id": {"type": "string"}, "quantity": {"type": "number"}}, "required": ["delivery_id", "recipient_id", "quantity"]}},
  {"name": "get_impact", "description": "Return meals rescued and cost avoided.", "inputSchema": {"type": "object", "properties": {"donor_id": {"type": "string"}, "period": {"type": "string"}}, "required": ["donor_id", "period"]}}
]
JSON

echo ">> 3/8 Write the agent code (gateway MCP tools)"
cat > "app/$AGENT/main.py" <<'PY'
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
PY

echo ">> 4/8 Package and deploy the tool Lambda"
( cd lambda_functions/wastenot && zip -qr ../../wastenot-tools.zip . )
if aws lambda get-function --function-name "$TOOL_LAMBDA" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$TOOL_LAMBDA" --zip-file fileb://wastenot-tools.zip >/dev/null
else
  aws lambda create-function --function-name "$TOOL_LAMBDA" \
    --runtime python3.12 --handler handler.handler --role "$LAMBDA_ROLE_ARN" \
    --timeout 30 --zip-file fileb://wastenot-tools.zip >/dev/null
fi
LAMBDA_ARN="$(aws lambda get-function --function-name "$TOOL_LAMBDA" --query Configuration.FunctionArn --output text)"

echo ">> 5/8 Create (idempotent) the Cognito machine-to-machine client for gateway auth"
POOL_ID="$(aws cognito-idp list-user-pools --max-results 60 --query "UserPools[?Name=='$COGNITO_POOL'].Id | [0]" --output text)"
if [ -z "$POOL_ID" ] || [ "$POOL_ID" = "None" ]; then
  POOL_ID="$(aws cognito-idp create-user-pool --pool-name "$COGNITO_POOL" --query UserPool.Id --output text)"
fi
DOMAIN="workshop-gw-${ACCOUNT_ID}"
aws cognito-idp describe-user-pool-domain --domain "$DOMAIN" --query DomainDescription.UserPoolId --output text 2>/dev/null | grep -q "$POOL_ID" \
  || aws cognito-idp create-user-pool-domain --domain "$DOMAIN" --user-pool-id "$POOL_ID" >/dev/null 2>&1 || true
aws cognito-idp describe-resource-server --user-pool-id "$POOL_ID" --identifier "workshop-gateway" >/dev/null 2>&1 \
  || aws cognito-idp create-resource-server --user-pool-id "$POOL_ID" --identifier "workshop-gateway" \
       --name "workshop-gateway" --scopes ScopeName=invoke,ScopeDescription="invoke gateway" >/dev/null
SCOPE="workshop-gateway/invoke"
CLIENT_ID="$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --max-results 60 --query "UserPoolClients[?ClientName=='workshop-gateway-m2m'].ClientId | [0]" --output text)"
if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "None" ]; then
  read CLIENT_ID CLIENT_SECRET < <(aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" --client-name "workshop-gateway-m2m" \
    --generate-secret --allowed-o-auth-flows client_credentials \
    --allowed-o-auth-scopes "$SCOPE" --allowed-o-auth-flows-user-pool-client \
    --supported-identity-providers COGNITO \
    --query 'UserPoolClient.[ClientId,ClientSecret]' --output text)
else
  CLIENT_SECRET="$(aws cognito-idp describe-user-pool-client --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" --query 'UserPoolClient.ClientSecret' --output text)"
fi
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${POOL_ID}/.well-known/openid-configuration"
TOKEN_ENDPOINT="https://${DOMAIN}.auth.${REGION}.amazoncognito.com/oauth2/token"

echo ">> 6/8 Add memory (user preference)"
agentcore add memory --name wastenot_memory --strategies USER_PREFERENCE --expiry 7

echo ">> 7/8 Add the gateway (Custom JWT) and the Lambda target"
agentcore add gateway --name "$GATEWAY" --protocol-type MCP --authorizer-type CUSTOM_JWT \
  --discovery-url "$DISCOVERY_URL" --allowed-clients "$CLIENT_ID" \
  --client-id "$CLIENT_ID" --client-secret "$CLIENT_SECRET" --runtimes "$AGENT"
agentcore add gateway-target --name wastenot --gateway "$GATEWAY" \
  --type lambda-function-arn --lambda-arn "$LAMBDA_ARN" --tool-schema-file tool_specs/wastenot.json

echo ">> 8/8 Inject the Cognito token config into agentcore.json runtime envVars, then deploy"
PY="$(command -v python3 || command -v python)"
"$PY" - "$AGENT" "$TOKEN_ENDPOINT" "$CLIENT_ID" "$CLIENT_SECRET" "$SCOPE" <<'PYEOF'
import json, sys
agent, tok, cid, csec, scope = sys.argv[1:6]
path = "agentcore/agentcore.json"
d = json.load(open(path))
want = {"GATEWAY_TOKEN_ENDPOINT": tok, "GATEWAY_CLIENT_ID": cid,
        "GATEWAY_CLIENT_SECRET": csec, "GATEWAY_SCOPE": scope}
for rt in d.get("runtimes", []):
    if rt.get("name") == agent:
        cur = {e["name"]: e for e in rt.get("envVars", [])}
        for k, v in want.items():
            cur[k] = {"name": k, "value": v}
        rt["envVars"] = list(cur.values())
json.dump(d, open(path, "w"), indent=2)
print("envVars written to", path)
PYEOF
agentcore deploy

echo "Done. Verify with:"
echo "  agentcore status"
echo "  agentcore invoke --prompt \"Match 40 portions of chicken biryani near Cedar Avenue\""
echo
echo "NOTE (validate on first deploy): the gateway MCP URL is read from the CLI-injected"
echo "  AGENTCORE_GATEWAY_WORKSHOP_GATEWAY_URL. If the agent cannot reach its tools, confirm"
echo "  that variable is present in the deployed runtime (agentcore status / agentcore logs)."
