"""Quick local runner for the two new agents: Script Writer + Infographic Designer."""
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from main import create_marketing_crew, reset_termination

TOPIC = "The benefits of intermittent fasting"
CONTENT = (
    "Intermittent fasting (IF) cycles between periods of eating and fasting. "
    "Common methods: 16:8 (16h fast, 8h eating window), 5:2 (two low-calorie days/week), "
    "and alternate-day fasting. Research links IF to improved insulin sensitivity, "
    "autophagy (cellular cleanup), weight loss via lower calorie intake and increased fat "
    "oxidation, and possible benefits for brain health (BDNF) and longevity. Risks: not "
    "suitable for people with a history of eating disorders, pregnant women, or some diabetics. "
    "A 2019 NEJM review by de Cabo & Mattson summarized metabolic switching benefits."
)

if __name__ == "__main__":
    reset_termination()
    crew = create_marketing_crew(
        enabled_agents=["scriptwriter", "infographic"],
        content=CONTENT,
    )
    result = crew.kickoff(inputs={"topic": TOPIC, "content": CONTENT})

    # Collect every task's output (sequential crew: result == last task only).
    sections = [f"# New Agents Output\n\n**Topic:** {TOPIC}\n"]
    for task in crew.tasks:
        agent_role = getattr(task.agent, "role", "Agent")
        raw = task.output.raw if getattr(task, "output", None) else "(no output)"
        sections.append(f"\n\n---\n\n# === {agent_role} ===\n\n{raw}")

    out = "".join(sections)
    with open("new_agents_output.md", "w", encoding="utf-8") as f:
        f.write(out)
    print("\n\n===== BOTH AGENT OUTPUTS saved to new_agents_output.md =====\n")
    print(out)
