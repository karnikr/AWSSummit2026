"""FastAPI backend for the WasteNot demo UI.

Serves a single-page app and a /rescue endpoint that:
- computes structured map data (pickup, split allocations, ordered route) deterministically
  from the local tools, and
- runs the WasteNot agent for the reasoning narrative (tool-by-tool explanation).
"""
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from strands import Agent
from strands.models import BedrockModel

import local_tools as t

REGION = "us-west-2"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM_PROMPT = """You are WasteNot, a food-rescue coordinator agent operating in a
bilingual (English/Arabic), right-to-left region. For every surplus donation, work in
clear numbered steps and briefly narrate WHY you take each action, so a human can follow
your reasoning:

1. lookup_food_safety: find the safe holding time for the food and the dietary rules.
   State the binding safe window (the strictest item wins).
2. match_recipients: match nearby recipients. Never propose a match that violates a
   recipient's dietary need (hard rule). Prefer recipients reachable well inside the
   remaining safe window, then nearest, then greatest need. If one recipient cannot take
   the full quantity, split across nearby recipients.
3. plan_route: order multiple drops to minimise total travel time.
4. dispatch_driver: assign an available driver. If a driver is unavailable, re-dispatch.
5. compute_impact: report impact, leading with meals rescued.
6. notify: send a short bilingual (English + Arabic) alert to the parties.

Explain the beat-the-clock reasoning (time left vs. drive time), the split decision, and
confirm the dietary safety rule held. Be concise but show your reasoning."""

app = FastAPI(title="WasteNot")


class RescueRequest(BaseModel):
    food_type: str
    dietary_info: str
    quantity: int
    hours_available: float
    pickup_name: str
    pickup_lat: float
    pickup_lon: float
    lang: str = "en"


def _plan(req: RescueRequest) -> dict:
    """Deterministic match + split + route + dispatch + impact for the map (no LLM)."""
    match = t.match_recipients.__wrapped__(
        food_dietary_info=req.dietary_info,
        pickup_lat=req.pickup_lat,
        pickup_lon=req.pickup_lon,
        quantity=req.quantity,
        hours_until_expiry=req.hours_available,
    )
    allocations = match["allocations"]

    stops = [
        {"recipient_id": a["recipient_id"], "name": a["name"],
         "latitude": a["latitude"], "longitude": a["longitude"]}
        for a in allocations
    ]
    route = t.plan_route.__wrapped__(
        pickup_lat=req.pickup_lat, pickup_lon=req.pickup_lon, stops=stops
    ) if stops else {"ordered_stops": [], "total_km": 0.0, "total_minutes": 0.0}

    # Merge allocation amounts back into the ordered route for the map.
    alloc_by_id = {a["recipient_id"]: a for a in allocations}
    ordered = []
    for s in route["ordered_stops"]:
        a = alloc_by_id.get(s["recipient_id"], {})
        ordered.append({**s, "allocated": a.get("allocated", 0),
                        "dietary_needs": a.get("dietary_needs", ""),
                        "need": a.get("need", 0)})

    dispatch = t.dispatch_driver.__wrapped__(
        pickup_name=req.pickup_name,
        drop_recipient_ids=[s["recipient_id"] for s in ordered],
    )

    meals = sum(a["allocated"] for a in allocations)
    impact = t.compute_impact.__wrapped__(meals_rescued=meals, distance_km=route["total_km"])

    return {
        "pickup": {"name": req.pickup_name, "lat": req.pickup_lat, "lon": req.pickup_lon},
        "route": {"ordered_stops": ordered, "total_km": route["total_km"],
                  "total_minutes": route["total_minutes"]},
        "unallocated": match["unallocated"],
        "excluded": match["excluded"],
        "split_across": match["split_across"],
        "dispatch": dispatch,
        "impact": impact,
    }


def _narrative(req: RescueRequest) -> str:
    """Run the agent for a human-readable, tool-by-tool reasoning summary."""
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    lang_note = ""
    if req.lang == "ar":
        lang_note = (
            " Respond ENTIRELY in Arabic (العربية). Write all headings, explanations, and "
            "tables in Arabic. Keep numbers and place names readable."
        )
    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT + lang_note, tools=t.ALL_TOOLS)
    prompt = (
        f"A donor ({req.pickup_name}, lat {req.pickup_lat}, lon {req.pickup_lon}) has "
        f"{req.quantity} portions of {req.food_type} ({req.dietary_info}) available for "
        f"the next {req.hours_available} hours. Classify the safe window, match and split "
        f"across recipients as needed, order the route, dispatch a driver, report impact, "
        f"and send a bilingual notification. Explain each decision."
    )
    if req.lang == "ar":
        prompt += " اكتب ردك بالكامل باللغة العربية."
    return str(agent(prompt))


@app.post("/rescue")
def rescue(req: RescueRequest) -> dict:
    plan = _plan(req)
    try:
        plan["narrative"] = _narrative(req)
    except Exception as e:  # keep the map usable even if the LLM call fails
        plan["narrative"] = f"(Agent narrative unavailable: {e})"
    return plan


@app.get("/donors")
def donors() -> list:
    rows = t._ddb.Table(t.DONORS_TABLE).scan().get("Items", [])
    return [
        {"donor_id": d.get("donor_id"), "name": d.get("name"),
         "lat": float(d.get("latitude", 0)), "lon": float(d.get("longitude", 0)),
         "type": d.get("type", "")}
        for d in rows
    ]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(HERE, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
