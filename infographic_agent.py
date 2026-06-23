"""
Infographic Agent for CrewAI Framework
=======================================
Converts a blog article into a structured visual HTML infographic.
Drop-in compatible with CrewAI 0.x / 1.x.

Dependencies:
    pip install crewai anthropic

Usage in your existing crew:
    from infographic_agent import build_infographic_agent_and_task
    agent, task = build_infographic_agent_and_task(blog_text="<your article>")
    # Add agent to your agents list and task to your tasks list.
"""

import os
import re
import json
import textwrap
from pathlib import Path
from typing import Optional

import anthropic
from crewai import Agent, Task
from crewai.tools import tool


# ─────────────────────────────────────────────
# 1. Core Claude helpers
# ─────────────────────────────────────────────

def _call_claude(prompt: str, system: str) -> str:
    client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=8192,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def parse_blog_to_structure(blog_text: str) -> dict:
    """Parse a blog article into a JSON structure for infographic rendering."""
    system = textwrap.dedent("""\
        You are an expert information architect.
        Read the blog article and output a clean JSON structure for a visual infographic.
        Return ONLY valid JSON (no markdown fences, no commentary).

        Schema:
        {
          "infographic_title": "short punchy title (max 8 words)",
          "subtitle": "one clarifying line",
          "key_themes": ["theme1", "theme2", "theme3"],
          "sections": [
            {
              "section_title": "SECTION LABEL",
              "color_hint": "blue|green|orange|purple|red|teal",
              "items": [
                {
                  "heading": "POINT TITLE (ALL CAPS, max 5 words)",
                  "body": "One or two sentence description, max 25 words.",
                  "emoji": "🎯"
                }
              ]
            }
          ],
          "conclusion": {
            "heading": "CONCLUSION HEADING",
            "body": "2-3 sentence synthesis or call to action.",
            "emoji": "🚀"
          }
        }

        Rules:
        - 3-5 sections, each with 2-4 items.
        - Keep headings ALL-CAPS, max 5 words.
        - Keep body text concise (max 25 words per item).
        - Choose clear, visually distinct emojis.
    """)
    raw = _call_claude(blog_text, system)
    raw = re.sub(r"```(?:json)?", "", raw).strip().strip("`")
    return json.loads(raw)


def render_infographic_html(structure: dict, output_path: str = "infographic.html") -> str:
    """Render a JSON structure into a self-contained HTML infographic file."""
    system = textwrap.dedent("""\
        You are a world-class infographic designer who writes HTML/CSS.
        Output ONLY a complete, self-contained HTML document. No commentary.

        Design requirements:
        - Single file, all styles inline / in <style> tag.
        - Google Fonts via <link> allowed.
        - Use CSS Grid and Flexbox for layout.
        - Sketchnote / hand-drawn infographic style:
            * Off-white background (#FAFAF5).
            * Each section in a rounded-corner box with bold coloured border.
            * Section title as a badge overlapping the top border.
            * Items: large emoji icon left, bold heading, muted description.
            * SVG arrows connecting sections in the flow.
            * Title uses Bebas Neue or Oswald (Google Fonts).
            * Key themes as coloured pill tags.
            * Conclusion in a cloud-bubble shape bottom-right.
        - Max-width 1100px, centred, fully responsive.
        - Print-friendly @media print.
        - "Save as PNG" button using html2canvas CDN.
        - Color map for color_hint values:
            blue: #4A90D9 border, #EAF3FB bg
            green: #27AE60 border, #EAFAF1 bg
            orange: #E67E22 border, #FEF9E7 bg
            purple: #8E44AD border, #F4ECF7 bg
            red: #E74C3C border, #FDEDEC bg
            teal: #16A085 border, #E8F8F5 bg
    """)
    prompt = (
        "Create the HTML infographic for this structure:\n\n"
        + json.dumps(structure, indent=2, ensure_ascii=False)
    )
    html = _call_claude(prompt, system)
    html = re.sub(r"^```html?\n?", "", html, flags=re.MULTILINE)
    html = re.sub(r"\n?```$", "", html, flags=re.MULTILINE).strip()
    Path(output_path).write_text(html, encoding="utf-8")
    return output_path


# ─────────────────────────────────────────────
# 2. CrewAI Tool definitions (new decorator API)
# ─────────────────────────────────────────────

@tool("Parse Blog to Infographic Structure")
def parse_blog_tool(blog_text: str) -> str:
    """
    Parses a raw blog article and returns a structured JSON string
    with sections, items, emojis, and colour hints for infographic rendering.
    Input: full blog article text.
    Output: JSON string.
    """
    structure = parse_blog_to_structure(blog_text)
    return json.dumps(structure, indent=2, ensure_ascii=False)


@tool("Render Infographic HTML")
def render_infographic_tool(json_structure: str, output_path: str = "") -> str:
    """
    Takes a JSON structure string (from Parse Blog to Infographic Structure)
    and generates a self-contained HTML infographic file.
    Input: json_structure (string), optional output_path (string).
    Output: file path of the saved HTML file.
    """
    structure = json.loads(json_structure)
    if not output_path:
        slug = re.sub(r"\W+", "_", structure.get("infographic_title", "infographic"))[:40]
        output_path = f"{slug}.html"
    saved = render_infographic_html(structure, output_path)
    return f"Infographic saved to: {saved}"


# ─────────────────────────────────────────────
# 3. Factory — returns Agent + Task for CrewAI
# ─────────────────────────────────────────────

def build_infographic_agent_and_task(
    blog_text: str,
    output_path: Optional[str] = None,
    llm=None,
) -> tuple:
    """
    Returns a (Agent, Task) pair ready to append to your CrewAI crew.

    Parameters
    ----------
    blog_text   : Full text of the blog article to visualise.
    output_path : Where to save the HTML file (default: auto-named).
    llm         : Optional CrewAI LLM override.

    Example
    -------
    from crewai import Crew, Process
    from infographic_agent import build_infographic_agent_and_task

    agent, task = build_infographic_agent_and_task(blog_text=my_article)

    crew = Crew(
        agents=[...your_agents..., agent],
        tasks=[...your_tasks..., task],
        process=Process.sequential,
        verbose=True,
    )
    crew.kickoff()
    """
    agent_kwargs = dict(
        role="Visual Infographic Designer",
        goal=(
            "Transform a blog article into a clear, structured, visually engaging "
            "HTML infographic that communicates key ideas at a glance."
        ),
        backstory=(
            "You are a senior information designer with a decade of experience "
            "turning dense written content into sketchnote-style visual summaries. "
            "You understand both design principles and content strategy."
        ),
        tools=[parse_blog_tool, render_infographic_tool],
        verbose=True,
        allow_delegation=False,
    )
    if llm is not None:
        agent_kwargs["llm"] = llm

    infographic_agent = Agent(**agent_kwargs)

    output_instruction = (
        f"Save the HTML file to: {output_path}" if output_path
        else "Auto-name the output HTML file based on the article title."
    )

    infographic_task = Task(
        description=textwrap.dedent(f"""\
            You have been given a blog article. Complete these two steps in order:

            STEP 1 — Use the 'Parse Blog to Infographic Structure' tool with the
            article text below to extract a structured JSON summary.

            STEP 2 — Use the 'Render Infographic HTML' tool with the JSON string
            from step 1 to generate and save the HTML infographic.
            {output_instruction}

            --- ARTICLE START ---
            {blog_text}
            --- ARTICLE END ---
        """),
        expected_output=(
            "The file path of the saved HTML infographic, "
            "e.g. 'The_Power_of_Deep_Work.html'."
        ),
        agent=infographic_agent,
    )

    return infographic_agent, infographic_task


# ─────────────────────────────────────────────
# 4. Standalone CLI / quick test
# ─────────────────────────────────────────────

SAMPLE_ARTICLE = """
The Power of Deep Work in a Distracted World

In today's hyper-connected world, the ability to focus deeply on cognitively demanding tasks
has become both rare and extremely valuable. Cal Newport, in his book "Deep Work,"
argues that the capacity for distraction-free concentration is the superpower of the 21st century.

What Is Deep Work?
Deep work refers to tasks that push your cognitive abilities to their limit: writing, coding,
designing, researching. These create real value and are hard to replicate.
Shallow work — emails, meetings, admin — can be done distracted and adds little unique value.

Why It Matters
The economy increasingly rewards those who master hard skills quickly and produce at an elite level.
Both abilities depend on deep work. In a world of open offices, social media, and constant pings,
the ability to concentrate is a rare competitive advantage.

How to Cultivate Deep Work
Schedule deep work blocks — treat them like unmissable meetings. Embrace boredom; resist switching
to your phone when distracted. Quit social media or use it intentionally. Work with a strict end
time to create urgency. Track deep work hours as your core productivity metric.

The Results
People who practise deep work consistently produce more meaningful output in less time,
advance faster in their careers, and report higher satisfaction — because they operate
at the edge of their abilities daily.
"""

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        blog_text = Path(sys.argv[1]).read_text(encoding="utf-8")
        print(f"Loaded article from: {sys.argv[1]}")
    else:
        blog_text = SAMPLE_ARTICLE
        print("Using built-in sample article.")

    print("\n[1/2] Parsing article structure...")
    structure = parse_blog_to_structure(blog_text)
    print(json.dumps(structure, indent=2, ensure_ascii=False))

    print("\n[2/2] Rendering HTML infographic...")
    slug = re.sub(r"\W+", "_", structure.get("infographic_title", "infographic"))[:40]
    out = render_infographic_html(structure, f"{slug}.html")
    print(f"\n✅  Done! Open your infographic: {out}")
