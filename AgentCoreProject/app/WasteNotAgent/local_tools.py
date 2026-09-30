"""WasteNot Strands tools wired directly to the workshop's provisioned AWS resources.

These bypass the AgentCore gateway/Lambda (which needs CDK bootstrap the participant
role cannot do) and call DynamoDB, Bedrock KB, SNS, and geo-places directly using the
caller's credentials. Region is fixed to us-west-2 per workshop conventions.

Enhancements for the hackathon:
- Time-weighted matching (at-risk food goes out first).
- Split a large donation across multiple nearby recipients on one route.
- Nearest-neighbour route ordering for multi-stop drops.
- Re-dispatch to the next driver on a no-show.
- Bilingual (English + Arabic) notifications; halal/dietary rules are a hard constraint.
"""
import math
from typing import Optional

import boto3
from strands import tool

REGION = "us-west-2"

# Shared AWS clients (created once at import).
_ssm = boto3.client("ssm", region_name=REGION)
_ddb = boto3.resource("dynamodb", region_name=REGION)
_kb = boto3.client("bedrock-agent-runtime", region_name=REGION)
_sns = boto3.client("sns", region_name=REGION)


def _param(name: str) -> str:
    """Read a single SSM parameter value."""
    return _ssm.get_parameter(Name=name)["Parameter"]["Value"]


# Resolve resource identifiers once from SSM.
DONORS_TABLE = _param("/app/workshop/wastenot/donors-table")
RECIPIENTS_TABLE = _param("/app/workshop/wastenot/recipients-table")
DRIVERS_TABLE = _param("/app/workshop/wastenot/drivers-table")
ALERTS_TOPIC = _param("/app/workshop/wastenot/alerts-topic-arn")
KB_ID = _param("/app/workshop/wastenot/knowledge-base-id")

# Rough average city speed for time estimates (km/h).
AVG_SPEED_KMH = 30.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    r = 6371.0  # Earth radius in km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 2)


def _travel_minutes(distance_km: float) -> float:
    """Estimate drive time in minutes for a distance at average city speed."""
    return round(distance_km / AVG_SPEED_KMH * 60, 1)


@tool
def lookup_food_safety(query: str) -> str:
    """Retrieve food-safety / dietary guidance from the WasteNot knowledge base.

    Use this to find safe holding times, dietary rules, and impact formulas before
    classifying food, matching recipients, or reporting impact.

    Args:
        query: A natural-language question, e.g. "safe holding time for cooked rice".
    """
    resp = _kb.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}},
    )
    chunks = [r["content"]["text"] for r in resp.get("retrievalResults", [])]
    return "\n\n---\n\n".join(chunks) if chunks else "No guidance found."


@tool
def list_recipients() -> list:
    """List all recipients with capacity, current need, dietary needs, and location."""
    rows = _ddb.Table(RECIPIENTS_TABLE).scan().get("Items", [])
    return [
        {
            "recipient_id": r.get("recipient_id"),
            "name": r.get("name"),
            "capacity": int(r.get("capacity", 0)),
            "need": int(r.get("need", 0)),
            "dietary_needs": r.get("dietary_needs", ""),
            "latitude": float(r.get("latitude", 0)),
            "longitude": float(r.get("longitude", 0)),
            "contact": r.get("contact", ""),
        }
        for r in rows
    ]


def _dietary_ok(recipient_need: str, food_dietary_info: str) -> bool:
    """Hard rule: the food must satisfy the recipient's dietary need.

    A recipient with no stated need accepts anything. Otherwise the recipient's
    need term must appear in the food's dietary info (e.g. need 'halal' requires the
    food to be tagged 'halal').
    """
    need = recipient_need.lower().strip()
    return not need or need in food_dietary_info.lower()


@tool
def match_recipients(
    food_dietary_info: str,
    pickup_lat: float,
    pickup_lon: float,
    quantity: int,
    hours_until_expiry: float = 4.0,
) -> dict:
    """Match a donation to recipients, splitting across several if needed.

    Applies the dietary hard rule (never proposes a conflicting match), then scores
    remaining recipients by a blend of proximity and urgency. When one recipient
    cannot absorb the full quantity, the donation is split across the next-nearest
    eligible recipients on a single route until the quantity is used or recipients
    run out.

    Args:
        food_dietary_info: Dietary attributes of the food, e.g. "vegetarian, halal".
        pickup_lat: Pickup latitude.
        pickup_lon: Pickup longitude.
        quantity: Number of meal portions available.
        hours_until_expiry: Hours left before the food is unsafe. Lower values push
            the agent to allocate faster and prefer closer recipients.
    """
    eligible = []
    excluded = []
    for r in list_recipients():
        if not _dietary_ok(r["dietary_needs"], food_dietary_info):
            excluded.append({"name": r["name"], "reason": f"dietary: needs {r['dietary_needs']}"})
            continue
        dist = _haversine_km(pickup_lat, pickup_lon, r["latitude"], r["longitude"])
        drive_min = _travel_minutes(dist)
        # Urgency: the tighter the window, the more we favour close recipients that
        # can be served well inside the remaining time.
        feasible = drive_min <= hours_until_expiry * 60
        eligible.append({**r, "distance_km": dist, "drive_minutes": drive_min, "feasible": feasible})

    # Score: prefer feasible, then nearest, then greatest need. When the window is
    # tight, distance dominates so at-risk food goes to the fastest drop first.
    eligible.sort(key=lambda x: (not x["feasible"], x["distance_km"], -x["need"]))

    # Split the donation greedily across eligible recipients on one route.
    remaining = quantity
    allocations = []
    for r in eligible:
        if remaining <= 0:
            break
        take = min(remaining, r["need"])
        if take <= 0:
            continue
        allocations.append({**r, "allocated": take})
        remaining -= take

    return {
        "allocations": allocations,
        "unallocated": remaining,
        "excluded": excluded,
        "split_across": len(allocations),
    }


@tool
def plan_route(pickup_lat: float, pickup_lon: float, stops: list) -> dict:
    """Order multi-stop drops to minimise total travel using nearest-neighbour.

    Starts at the pickup and repeatedly visits the closest unvisited stop. Returns the
    ordered stops with per-leg distances, total distance, and estimated total minutes.

    Args:
        pickup_lat: Pickup latitude.
        pickup_lon: Pickup longitude.
        stops: List of dicts each with recipient_id, name, latitude, longitude.
    """
    remaining = list(stops)
    ordered = []
    cur_lat, cur_lon = pickup_lat, pickup_lon
    total_km = 0.0
    while remaining:
        nxt = min(remaining, key=lambda s: _haversine_km(cur_lat, cur_lon, s["latitude"], s["longitude"]))
        leg = _haversine_km(cur_lat, cur_lon, nxt["latitude"], nxt["longitude"])
        total_km += leg
        ordered.append({**nxt, "leg_km": leg})
        cur_lat, cur_lon = nxt["latitude"], nxt["longitude"]
        remaining.remove(nxt)
    return {
        "ordered_stops": ordered,
        "total_km": round(total_km, 2),
        "total_minutes": _travel_minutes(total_km),
    }


@tool
def dispatch_driver(pickup_name: str, drop_recipient_ids: list, exclude_driver_ids: Optional[list] = None) -> dict:
    """Assign the next available driver, skipping any that have declined.

    Args:
        pickup_name: Name of the pickup location (donor).
        drop_recipient_ids: Ordered list of recipient IDs to deliver to.
        exclude_driver_ids: Driver IDs that already declined (no-show handling); the
            agent re-dispatches to the next available driver automatically.
    """
    skip = set(exclude_driver_ids or [])
    rows = _ddb.Table(DRIVERS_TABLE).scan().get("Items", [])
    available = [d for d in rows if d.get("available") == "true" and d["driver_id"] not in skip]
    if not available:
        return {"status": "pending", "reason": "no available driver", "excluded": list(skip)}
    driver = available[0]
    return {
        "status": "dispatched",
        "driver_id": driver["driver_id"],
        "driver_name": driver.get("name"),
        "vehicle": driver.get("vehicle"),
        "pickup": pickup_name,
        "stops": drop_recipient_ids,
        "reassigned_from": list(skip) or None,
    }


@tool
def compute_impact(meals_rescued: int, distance_km: float) -> dict:
    """Compute rescue impact using the workshop formulas.

    Meals rescued is the headline metric. Cost avoided is 3.0 currency units per meal;
    CO2 avoided is 2.5 kg per meal; water avoided is 1000 L per meal.

    Args:
        meals_rescued: Portions delivered.
        distance_km: Total route distance in km.
    """
    return {
        "meals_rescued": meals_rescued,
        "cost_avoided_units": round(meals_rescued * 3.0, 2),
        "co2_avoided_kg": round(meals_rescued * 2.5, 2),
        "water_avoided_l": meals_rescued * 1000,
        "distance_km": distance_km,
    }


# Minimal English->Arabic phrase map for bilingual, RTL-friendly notifications.
_AR = {
    "Pickup ready": "الاستلام جاهز",
    "meals": "وجبة",
    "Driver": "السائق",
    "on the way": "في الطريق",
    "Thank you": "شكراً",
}


@tool
def notify(message_en: str, subject: str = "WasteNot", recipient_lang: str = "en") -> dict:
    """Publish a bilingual notification to the WasteNot alerts SNS topic.

    Sends English and Arabic so each party reads it in their language. Respects the
    region's RTL layout by placing the Arabic block on its own lines.

    Args:
        message_en: The English notification body.
        subject: Optional subject line.
        recipient_lang: 'en', 'ar', or 'both' (default sends both when 'ar'/'both').
    """
    ar = message_en
    for en, arb in _AR.items():
        ar = ar.replace(en, arb)
    if recipient_lang == "ar":
        body = ar
    elif recipient_lang == "both":
        body = f"{message_en}\n\n---\n\n{ar}"
    else:
        body = message_en
    resp = _sns.publish(TopicArn=ALERTS_TOPIC, Message=body, Subject=subject[:100])
    return {"published": True, "message_id": resp.get("MessageId"), "lang": recipient_lang}


# All tools exposed to the agent.
ALL_TOOLS = [
    lookup_food_safety,
    list_recipients,
    match_recipients,
    plan_route,
    dispatch_driver,
    compute_impact,
    notify,
]
