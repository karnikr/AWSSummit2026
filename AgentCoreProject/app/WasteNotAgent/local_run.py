"""Run the WasteNot agent locally against the workshop's provisioned AWS resources.

Usage:
    python local_run.py "Match 40 portions of chicken biryani from Cedar Avenue Hotel"

No gateway, Lambda, or CDK deploy needed. Uses the caller's us-west-2 credentials.
"""
import os
import sys

from strands import Agent
from strands.models import BedrockModel

from local_tools import ALL_TOOLS

REGION = "us-west-2"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")

SYSTEM_PROMPT = """You are WasteNot, a food-rescue coordinator. For a surplus donation:
1. Look up safe holding time and dietary rules with lookup_food_safety before deciding.
2. Match nearby recipients with match_recipients, excluding any whose dietary need the
   food violates (a hard rule). Prefer nearest, then greatest need.
3. Dispatch an available driver with dispatch_driver.
4. Report impact with compute_impact, leading with meals rescued.
Be concise and explain the reasoning behind each choice."""


def main() -> None:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        prompt = "What can you do?"
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=ALL_TOOLS)
    result = agent(prompt)
    print("\n=== WasteNot response ===\n")
    print(result)


if __name__ == "__main__":
    main()
