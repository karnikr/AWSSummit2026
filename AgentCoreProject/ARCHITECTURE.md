# WasteNot — Architecture & Flow

WasteNot is an agentic food-rescue system. A restaurant reports surplus meals; the
agent classifies safe-holding time, matches dietary-compatible recipients, splits and
routes the delivery, dispatches a driver, and reports impact — explaining every step.

The agent runs **locally** (FastAPI) using the **Strands Agents SDK** with **Amazon
Bedrock** for reasoning. Tools call the workshop's pre-provisioned AWS resources
directly (DynamoDB, Bedrock Knowledge Base, SNS). This avoids the AgentCore gateway /
CDK deploy path, which the locked-down workshop participant role cannot bootstrap.

## System architecture

```mermaid
flowchart TB
    subgraph Browser["🌐 Browser (bilingual EN / العربية, RTL-aware)"]
        UI["Single-page UI<br/>form + Leaflet map + agent reasoning panel"]
    end

    subgraph Local["💻 Local host (FastAPI)"]
        API["server.py<br/>POST /rescue · GET /donors"]
        PLAN["Deterministic plan<br/>(match → split → route → dispatch → impact)"]
        AGENT["WasteNot Agent<br/>Strands SDK + BedrockModel"]
        TOOLS["Strands @tool functions<br/>local_tools.py"]
    end

    subgraph AWS["☁️ Amazon Web Services — us-west-2"]
        BR["Amazon Bedrock<br/>claude-sonnet-4-6 (reasoning)"]
        KB["Bedrock Knowledge Base<br/>food-safety + dietary rules"]
        DDB[("DynamoDB<br/>donors · recipients · drivers")]
        SNS["SNS<br/>bilingual alerts"]
        SSM["SSM Parameter Store<br/>resource IDs"]
    end

    UI -->|"donation + lang"| API
    API --> PLAN
    API --> AGENT
    PLAN --> TOOLS
    AGENT -->|"reasons, calls tools"| TOOLS
    AGENT <-->|"model inference"| BR
    TOOLS -->|"retrieve"| KB
    TOOLS -->|"scan"| DDB
    TOOLS -->|"publish"| SNS
    TOOLS -->|"get parameters"| SSM
    API -->|"map data + narrative"| UI
```

## Rescue flow (per submission)

```mermaid
sequenceDiagram
    participant U as User (UI)
    participant S as FastAPI (server.py)
    participant A as Strands Agent
    participant B as Bedrock (LLM)
    participant K as Knowledge Base
    participant D as DynamoDB
    participant N as SNS

    U->>S: POST /rescue (food, qty, hours, pickup, lang)
    S->>A: run agent (system prompt + tools)

    Note over A,B: 1. Classify safe window
    A->>K: lookup_food_safety(food)
    K-->>A: holding times + dietary matrix
    A->>B: reason about binding window

    Note over A,D: 2. Match + split (dietary hard rule)
    A->>D: list_recipients / match_recipients
    D-->>A: recipients (need, diet, location)
    A->>A: exclude conflicts, time-weight, split

    Note over A: 3. Plan route (nearest-neighbour)
    A->>A: plan_route(stops) → ordered, distance, time

    Note over A,D: 4. Dispatch driver (+ no-show re-dispatch)
    A->>D: dispatch_driver(...)
    D-->>A: assigned driver

    Note over A: 5. Compute impact
    A->>A: meals, cost, CO2, water

    Note over A,N: 6. Notify (English + Arabic)
    A->>N: notify(message, lang)

    A-->>S: reasoning narrative
    S-->>U: map (pickup, ordered stops, route) + impact + narrative
```

## Agent tools (local_tools.py)

| Tool | Purpose | AWS service |
|------|---------|-------------|
| `lookup_food_safety` | Safe holding time + dietary rules | Bedrock Knowledge Base |
| `list_recipients` | Recipients with need, diet, location | DynamoDB |
| `match_recipients` | Dietary-safe, time-weighted matching + split | DynamoDB |
| `plan_route` | Nearest-neighbour multi-stop ordering | (compute) |
| `dispatch_driver` | Assign driver; re-dispatch on no-show | DynamoDB |
| `compute_impact` | Meals rescued, cost, CO₂, water avoided | (compute) |
| `notify` | Bilingual (EN/AR) alert | SNS |

## Distinctive capabilities

- **Real-time matching + routing** — the core differentiator.
- **Beat the clock** — matches are weighted by time left vs. drive time.
- **Split donations** — a large surplus is divided across nearby recipients on one route.
- **Dietary hard rule** — vegetarian/vegan food satisfies halal; conflicts never matched.
- **Regional relevance** — bilingual EN/Arabic UI (RTL) and notifications; halal-aware.
- **Explainable** — the agent narrates each decision in the selected language.
