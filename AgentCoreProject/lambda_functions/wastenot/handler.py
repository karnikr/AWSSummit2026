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
