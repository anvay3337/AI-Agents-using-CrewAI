import sys
import io
import ssl
import os

# Force standard streams to UTF-8 on Windows to prevent cp1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# --- SSL Fix for network/proxy environments ---
# Some ISPs and corporate firewalls do SSL inspection which breaks strict SSL.
# This patches the default SSL context to allow such connections.
os.environ["PYTHONHTTPSVERIFY"] = "0"
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

_orig_create_default_context = ssl.create_default_context
def _patched_ssl_context(*args, **kwargs):
    ctx = _orig_create_default_context(*args, **kwargs)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
ssl.create_default_context = _patched_ssl_context

try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

try:
    import httpx
    # Patch Client
    _orig_client_init = httpx.Client.__init__
    def _patched_client_init(self, *args, **kwargs):
        kwargs['http2'] = False
        _orig_client_init(self, *args, **kwargs)
    httpx.Client.__init__ = _patched_client_init

    # Patch AsyncClient
    _orig_async_client_init = httpx.AsyncClient.__init__
    def _patched_async_client_init(self, *args, **kwargs):
        kwargs['http2'] = False
        _orig_async_client_init(self, *args, **kwargs)
    httpx.AsyncClient.__init__ = _patched_async_client_init
    print("✅ httpx patched: forced http2=False to prevent UNEXPECTED_EOF_WHILE_READING SSL errors")
except Exception as e:
    print(f"⚠️ httpx patch failed: {e}")


# --- Smart App Control / pywin32 compatibility shim ---
# Must run BEFORE importing crewai (crewai -> portalocker -> pywin32). On machines
# where Smart App Control blocks the unsigned pywin32 DLL, this provides a
# ctypes/kernel32 fallback so the backend can still import. No-op otherwise.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import win_sac_shim  # noqa: E402,F401

from crewai.tools import tool
from crewai_tools import SerperDevTool
from dotenv import load_dotenv
from crewai import Agent
from crewai import Task
from crewai import Crew, Process

# --- LiteLLM Groq fix: strip cache_breakpoint + auto-retry on rate limits ---
# Groq rejects 'cache_breakpoint'. Also auto-sleeps on TPM rate limits so
# the crew never crashes mid-run — it simply waits and continues.
try:
    import re as _re
    import time as _time
    import litellm as _litellm
    from litellm.exceptions import RateLimitError as _RateLimitError

    # Transient network/server errors worth retrying (a dropped TLS connection,
    # e.g. "[SSL: UNEXPECTED_EOF_WHILE_READING]", surfaces as InternalServerError /
    # APIConnectionError). We intentionally do NOT retry Auth/BadRequest errors.
    _TRANSIENT_ERRORS = []
    for _exc_name in ("InternalServerError", "ServiceUnavailableError",
                      "APIConnectionError", "Timeout"):
        _exc = getattr(_litellm.exceptions, _exc_name, None)
        if isinstance(_exc, type):
            _TRANSIENT_ERRORS.append(_exc)
    _TRANSIENT_ERRORS = tuple(_TRANSIENT_ERRORS)

    _orig_completion = _litellm.completion

    def _strip_cache_keys_from_messages(messages):
        """Remove Groq-unsupported cache properties from all messages."""
        cleaned = []
        for msg in messages:
            m = dict(msg)
            for key in ("cache_breakpoint", "cache_control", "cache"):
                m.pop(key, None)
            if isinstance(m.get("content"), list):
                new_content = []
                for block in m["content"]:
                    b = dict(block)
                    for key in ("cache_breakpoint", "cache_control", "cache"):
                        b.pop(key, None)
                    new_content.append(b)
                m["content"] = new_content
            cleaned.append(m)
        return cleaned

    def _patched_completion(*args, **kwargs):
        if "messages" in kwargs:
            kwargs["messages"] = _strip_cache_keys_from_messages(kwargs["messages"])
        elif len(args) >= 2:
            args = list(args)
            args[1] = _strip_cache_keys_from_messages(args[1])
            args = tuple(args)

        # Auto-retry with sleep on rate limits (up to 10 attempts) and on
        # transient connection/server errors (exponential backoff, up to 6).
        _transient_tries = 0
        for _attempt in range(10):
            try:
                return _orig_completion(*args, **kwargs)
            except _RateLimitError as _rle:
                _msg = str(_rle)
                # Parse "try again in X.XXs" from error message
                _match = _re.search(r'try again in ([0-9.]+)s', _msg)
                _wait = float(_match.group(1)) + 3 if _match else 35
                print(f"\n⏳ [Rate Limit] Groq TPM limit hit. Sleeping {_wait:.1f}s then retrying... (attempt {_attempt+1}/10)")
                _time.sleep(_wait)
                continue
            except _TRANSIENT_ERRORS as _te:
                _transient_tries += 1
                if _transient_tries > 6:
                    raise
                _wait = min(2 ** _transient_tries, 30)
                print(f"\n🔁 [Transient] Groq {type(_te).__name__}: {str(_te)[:80]} — retrying in {_wait}s... ({_transient_tries}/6)")
                _time.sleep(_wait)
                continue
        return _orig_completion(*args, **kwargs)  # final attempt, let it raise

    _litellm.completion = _patched_completion
    _litellm.cache = None
    _litellm.ssl_verify = False
    print("✅ LiteLLM patched: cache_breakpoint stripping + rate-limit auto-retry enabled")
except Exception as _e:
    print(f"⚠️  LiteLLM patch skipped: {_e}")

# Shared execution state for mid-run termination
_state = {
    "is_terminated": False
}

def request_termination():
    _state["is_terminated"] = True

def reset_termination():
    _state["is_terminated"] = False

def is_termination_requested():
    return _state["is_terminated"]

load_dotenv()



import os
import requests

@tool("Web Search Tool")
def search_tool(search_query: str) -> str:
    """Searches the web for information about a query using Serper API and returns a compact summary of the top results."""
    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        return "Error: SERPER_API_KEY is not set."
    
    url = "https://google.serper.dev/search"
    payload = {"q": search_query, "num": 3}
    headers = {
        'X-API-KEY': api_key,
        'Content-Type': 'application/json'
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get('organic', [])[:3]:
            title = item.get('title')
            link = item.get('link')
            snippet = item.get('snippet')
            results.append(f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n")
            
        if not results:
            return "No results found."
            
        return "\n".join(results)
    except Exception as e:
        return f"Error executing web search: {str(e)}"

@tool("Scrape Website Tool")
def scrape_website_tool(url: str) -> str:
    """Scrapes the text content of a given URL (webpage) and returns a clean plain-text representation of the page."""
    from bs4 import BeautifulSoup
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Remove navigation, headers, footers, script, and style elements to isolate core content
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.decompose()
            
        text = soup.get_text()
        
        # Clean up whitespace and empty lines
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text_content = '\n'.join(chunk for chunk in chunks if chunk)
        
        # Truncate to avoid exceeding LLM context window limits
        if len(text_content) > 8000:
            text_content = text_content[:8000] + "\n\n[Content truncated due to length limits...]"
            
        return text_content
    except Exception as e:
        return f"Error scraping website: {str(e)}"

@tool("SEO Keyword Analysis Tool")
def seo_keyword_tool(topic: str) -> str:
    """Analyzes a topic and returns a list of high-traffic SEO keywords and search intent trends."""
    # Mock SEO keyword suggestions based on the topic
    return f"SEO Keywords for '{topic}': best {topic}, {topic} guide, how to use {topic}, trending {topic} 2026. Search Intent: Informational."


# Directory where generated infographic images are written (served by the backend at /generated).
GENERATED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")

@tool("Generate Infographic Image")
def generate_infographic_image_tool(title: str, key_points: str) -> str:
    """Generates a REAL infographic PNG image using Google Gemini's image model and saves it to the served 'generated/' folder.

    Inputs:
      - title: a short infographic title (max ~6 words), e.g. 'Intermittent Fasting Benefits'.
      - key_points: the 4-6 key callouts to feature, ONE PER LINE, formatted 'HEADING — supporting one-liner'.

    Returns a single line 'INFOGRAPHIC_IMAGE: generated/<file>.png' on success,
    or a string starting with 'IMAGE_ERROR:' on failure.
    """
    import re as _re
    import time as _time
    import uuid as _uuid

    api_key = os.environ.get("GEMINI_IMAGE_API_KEY", "").strip().strip('"').strip("'")
    if not api_key or api_key == "NA":
        return "IMAGE_ERROR: GEMINI_IMAGE_API_KEY is not set in .env."

    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        return f"IMAGE_ERROR: google-genai SDK unavailable: {e}"

    prompt = (
        "Create a professional, modern SQUARE (1:1, 1080x1080) social-media INFOGRAPHIC poster.\n"
        f'Title displayed prominently at the top: "{title}".\n'
        "Feature the following points as clean icon cards, each with a short bold label and a one-line description:\n"
        f"{key_points}\n\n"
        "Design: flat vector illustration style, cohesive modern color palette, strong visual hierarchy, "
        "generous spacing, simple line icons, HIGH CONTRAST, and crisp LEGIBLE text with every word spelled "
        "correctly. Suitable for Instagram and LinkedIn. No watermark, no signature."
    )

    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                client_args={"verify": False},
                async_client_args={"verify": False},
            ),
        )
    except Exception as e:
        return f"IMAGE_ERROR: could not init Gemini client: {e}"

    # Try newest/best image models first, then fall back.
    models = ["gemini-2.5-flash-image", "gemini-3-pro-image", "imagen-4.0-fast-generate-001"]
    last_err = "no model produced an image"

    for model in models:
        for attempt in range(3):
            try:
                data = None
                if model.startswith("imagen"):
                    resp = client.models.generate_images(
                        model=model,
                        prompt=prompt,
                        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="1:1"),
                    )
                    if resp.generated_images:
                        data = resp.generated_images[0].image.image_bytes
                else:
                    resp = client.models.generate_content(model=model, contents=prompt)
                    for part in resp.candidates[0].content.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            data = inline.data
                            break

                if data:
                    os.makedirs(GENERATED_DIR, exist_ok=True)
                    slug = _re.sub(r"\W+", "_", title).strip("_")[:40] or "infographic"
                    fname = f"{slug}_{_uuid.uuid4().hex[:8]}.png"
                    with open(os.path.join(GENERATED_DIR, fname), "wb") as f:
                        f.write(data)
                    print(f"🖼️  Infographic image generated via {model}: generated/{fname}")
                    return f"INFOGRAPHIC_IMAGE: generated/{fname}"

                last_err = f"{model}: response contained no image bytes"
                break  # retrying a clean no-image response won't help
            except Exception as e:
                msg = str(e)
                last_err = f"{model}: {type(e).__name__}: {msg[:180]}"
                # Retry transient rate limits / SSL drops; otherwise move to next model.
                if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "UNEXPECTED_EOF" in msg:
                    _time.sleep(3)
                    continue
                break

    return f"IMAGE_ERROR: {last_err}"

def create_marketing_crew(enabled_agents: list = None, blog_draft: str = None, research_report: str = None, content: str = None):
    if enabled_agents is None:
        enabled_agents = ["researcher", "writer", "seo"]

    from crewai import LLM
    import os
    
    # Check if a high-speed cloud LLM key is configured in env
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip().strip('"').strip("'")
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip().strip('"').strip("'")
    groq_key = os.environ.get("GROQ_API_KEY", "").strip().strip('"').strip("'")
    
    if openai_key and openai_key != "NA":
        print("🚀 Using High-Performance Cloud LLM: OpenAI GPT-4o-mini")
        agent_llm = LLM(model="gpt-4o-mini", temperature=0.1)
    elif gemini_key and gemini_key != "NA":
        print("🚀 Using High-Performance Cloud LLM: Google Gemini 1.5 Flash")
        agent_llm = LLM(model="gemini/gemini-1.5-flash", temperature=0.1, use_native=False)
    elif groq_key and groq_key != "NA":
        print("🚀 Using High-Performance Cloud LLM: Groq Llama3.1 8B Instant")
        os.environ["GROQ_API_KEY"] = groq_key
        try:
            import litellm as _litellm_inner
            _litellm_inner.ssl_verify = False
            _litellm_inner.cache = None
        except Exception:
            pass
        agent_llm = LLM(
            model="groq/llama-3.1-8b-instant",
            temperature=0.1,
            api_key=groq_key,
            max_retries=5
        )
    else:
        print("🐢 Using Local LLM: Ollama Llama 3.2 (Expect longer processing times)")
        agent_llm = LLM(
            model="ollama/llama3.2",
            base_url="http://localhost:11434",
            temperature=0.1
        )


    # Agent 1: Market Researcher (uses Web Search Tool)
    market_researcher = Agent(
        role="Senior Market Researcher",
        goal="Gather and analyze the latest market trends, competitor activities, and user needs regarding a topic.",
        backstory="You are a seasoned analyst with an eye for detail. You excel at filtering noise to find valuable market insights using web search tools.",
        tools=[],
        llm=agent_llm,
        verbose=True,
        max_iter=2,
        allow_delegation=False
    )

    # Agent 2: Content Creator (does not require tools, uses research output)
    content_creator = Agent(
        role="Expert Content Creator",
        goal="Draft high-quality, engaging, and clear articles or reports based on market research reports.",
        backstory="You are a creative writer who knows how to capture reader attention. You translate raw analytical data into compelling narratives.",
        llm=agent_llm,
        verbose=True,
        max_iter=1,
        allow_delegation=False
    )

    # Agent 3: Marketing Specialist (uses SEO Keyword Tool)
    marketing_specialist = Agent(
        role="SEO and Distribution Specialist",
        goal="Optimize content for search engines using SEO keywords and outline a highly specific, deep, and actionable distribution strategy.",
        backstory="""You are an expert digital marketing guru and senior research analyst. Your job is to produce deep, specific, and genuinely useful responses — never generic.

BEFORE RESPONDING:
1. Break the question into sub-questions worth investigating
2. Identify what a surface-level answer would look like — then go deeper than that
3. Ask yourself: "Would an expert in this field find this response useful?"

RESEARCH STANDARDS:
- Lead with the most non-obvious, high-value insight first
- Back every claim with reasoning, data, examples, or source references
- Include specific numbers, names, dates, case studies, or mechanisms — not vague generalities
- If something is contested or uncertain, say so explicitly
- Prioritize depth on 3 key points over shallow coverage of 10

RESPONSE STRUCTURE:
- Start with the core insight (not a preamble or restatement of the question)
- Use examples from real-world cases, research, or industry practice
- End with an actionable takeaway or the most important implication

WHAT TO AVOID:
- Filler phrases ("Great question!", "Certainly!", "In today's world...")
- Restating the question before answering it
- Bullet lists of obvious points with no depth
- Hedging everything into meaninglessness
- Generic advice that applies to every situation equally

TONE:
- Write like a senior expert briefing a peer — direct, confident, specific
- Match the complexity of the answer to the complexity of the question
- Be concise where possible, thorough where necessary""",
        tools=[],
        llm=agent_llm,
        verbose=True,
        max_iter=2,
        allow_delegation=False
    )

    # Agent 4: Video / Reel Script Writer
    script_writer = Agent(
        role="Professional Video Script Writer",
        goal="Write tight, high-retention scripts for YouTube videos and Instagram/TikTok reels that hook viewers in the first 3 seconds and drive them to a clear call-to-action.",
        backstory="""You are a viral short-form and long-form video scriptwriter who has written for creators with millions of followers.
You understand pacing, pattern interrupts, the 'hook → value → payoff → CTA' structure, and how spoken-word scripts differ from written articles.
You write scripts that are meant to be SPOKEN ALOUD on camera — conversational, punchy, and easy to perform — and you always pair the spoken lines with concrete on-screen visual / B-roll direction.""",
        tools=[],
        llm=agent_llm,
        verbose=True,
        max_iter=1,
        allow_delegation=False
    )

    # Agent 5: Infographic Generator (generates a REAL image via Google Gemini)
    infographic_designer = Agent(
        role="Social Infographic Designer",
        goal="Distill a topic or article into the most important, unique, and shareable facts, then generate a real, ready-to-post infographic IMAGE for Instagram, LinkedIn and Twitter/X using the image tool.",
        backstory="""You are a senior information designer who turns dense content into scroll-stopping visual graphics.
You pick the 4-6 most non-obvious, high-value data points worth visualising and phrase them as short punchy callouts.
You then use the 'Generate Infographic Image' tool to produce an actual PNG image (not code), and you write platform-specific captions to accompany it.""",
        tools=[generate_infographic_image_tool],
        llm=agent_llm,
        verbose=True,
        max_iter=3,
        allow_delegation=False
    )

    # Task 1: Research (Market Researcher)
    research_task = Task(
        description="""Compile a comprehensive market research report about: '{topic}'. 
If '{topic}' is a URL (starts with http:// or https://), use the Scrape Website Tool to read and analyze its content directly. 
Otherwise, use the Web Search Tool to search for information on the topic. 
Focus on key challenges, trends, and opportunities.""",
        expected_output="A detailed market research report containing key findings, trends, and user pain points.",
        agent=market_researcher,
        tools=[search_tool, scrape_website_tool]
    )

    # Task 2: Write (Content Creator)
    write_task = Task(
        description="Using the market research report, write an informative blog post or article. Ensure the tone is engaging and fits a professional audience.",
        expected_output="A complete draft article (approx. 800-1000 words) with clear headings and engaging content.",
        agent=content_creator
    )

    # Task 3: Refine (Marketing Specialist)
    refine_task = Task(
        description="""Analyze the draft article and optimize it for SEO. Use the SEO Keyword Tool exactly once to analyze the topic '{topic}' for relevant search terms, insert them naturally, write meta tags, and suggest heading updates.
Ensure the optimized content is of the highest professional standard: lead with non-obvious, high-value insights, use specific data or real-world examples, avoid generic advice or filler language, and prioritize depth over shallow coverage.""",
        expected_output="An SEO-optimized version of the article with a list of keywords used, target meta title, and meta description.",
        agent=marketing_specialist,
        tools=[seo_keyword_tool]
    )

    # Task 4: Distribute (Marketing Specialist)
    distribute_task = Task(
        description="""Based on the SEO-optimized article from the previous task, compile a complete strategic campaign document. 
Perform two actions:
1. Include the full, SEO-optimized blog post from the previous task under a '## Strategic Blog Post' heading.
2. Create a content distribution plan specifying target platforms (e.g., LinkedIn, Twitter, Quora, newsletters) and draft promotional social media posts.

Make sure your strategic distribution plan and promotional posts adhere to these standards:
- Genuinely useful and deep response — never generic.
- Lead with the most non-obvious, high-value insight first.
- Back every claim with reasoning, data, examples, or source references.
- Include specific numbers, names, dates, case studies, or mechanisms — not vague generalities.
- Avoid filler phrases, restating the question, or bullet lists of obvious points with no depth.
- Tone must be direct, confident, specific, and structured like a senior expert briefing a peer.
- The Quora post MUST be a long-form Quora answer (400-600 words) that directly answers a target question with real depth and analysis, ending with a subtle, non-promotional CTA.

Your final response MUST be structured exactly as follows:
## Strategic Blog Post
[Insert the complete SEO-optimized article here]

## Content Distribution Plan
[Insert the distribution plan here]

## Social Media Promotional Posts
**Facebook Post:**
[Insert Facebook post draft here]

**Twitter Post:**
[Insert Twitter post draft here]

**Instagram Post:**
[Insert Instagram post draft here]

**Quora Post:**
[Insert the long-form Quora answer here]
""",
        expected_output="A document containing the strategic blog post, the content distribution plan, and the social media promotional posts including a tailored Quora answer.",
        agent=marketing_specialist
    )

    # Task 5: Script Writing (Script Writer) — chained version uses prior content
    script_task = Task(
        description="""Write a complete, production-ready video script about: '{topic}'.
Use the article / content produced in the previous steps as your source material for facts and angle.

Write ONE script optimised for a short-form vertical video (Instagram Reel / YouTube Short / TikTok, 30-60 seconds)
AND a short outline for a longer YouTube video on the same topic.

Standards:
- Open with a scroll-stopping HOOK in the first 1-2 lines (a bold claim, a surprising stat, or a sharp question).
- Write spoken lines the way a creator would actually say them — conversational, punchy, no corporate filler.
- Pair every beat with concrete ON-SCREEN VISUAL / B-roll direction in [brackets].
- Build tension/value through the middle, then land a clear payoff and a single, specific call-to-action.

Format your answer EXACTLY as:
## Reel / Short Script (30-60s)
**Hook:** [spoken hook line]  [on-screen visual]
**Body:**
- [spoken line]  [on-screen visual]
- ...
**CTA:** [spoken call-to-action]  [on-screen visual]
**Suggested caption + hashtags:** ...
**On-screen text overlays:** ...

## YouTube Video Outline (longer form)
- Title ideas (3)
- Hook (first 15s, spoken)
- Main talking points / segments (with rough timestamps)
- Outro + CTA
""",
        expected_output="A reel/short script with hook, body beats, visual direction and CTA, plus a longer YouTube video outline.",
        agent=script_writer
    )

    # Task 6: Infographic (Infographic Designer) — generates a REAL image via Gemini
    infographic_task = Task(
        description="""Create a ready-to-post visual infographic IMAGE about: '{topic}'.
Use the article / content produced in the previous steps to extract the most important and UNIQUE details — prefer non-obvious facts, numbers, comparisons or steps over generic statements.

Do these steps IN ORDER:
1. Choose a short infographic TITLE (max 6 words).
2. Write the 4-6 standout callouts, ONE PER LINE, each formatted 'HEADING — supporting one-liner' (max 8 words per heading).
3. Call the 'Generate Infographic Image' tool EXACTLY ONCE, passing the title and the callouts (as the key_points argument). The tool returns a line like 'INFOGRAPHIC_IMAGE: generated/xxx.png'.
4. Write platform-tailored captions.

Your final answer MUST be structured EXACTLY as:
## Infographic Key Points
- **HEADING** — supporting line
- ...

## Infographic Image
[Paste the EXACT line returned by the tool here, e.g. 'INFOGRAPHIC_IMAGE: generated/xxx.png'. If the tool returned an error, paste that error line instead.]

## Captions
**Instagram:** ...
**LinkedIn:** ...
**Twitter/X:** ...
""",
        expected_output="A list of key callouts, the exact 'INFOGRAPHIC_IMAGE: ...' line from the image tool, and platform-specific captions.",
        agent=infographic_designer
    )

    def check_termination_callback(step_output):
        if is_termination_requested():
            raise RuntimeError("Campaign execution terminated by user.")

    # Filter agents and tasks dynamically based on user selection
    tasks = []
    agents = []

    if "researcher" in enabled_agents:
        agents.append(market_researcher)
        tasks.append(research_task)

    if "writer" in enabled_agents:
        agents.append(content_creator)
        if "researcher" not in enabled_agents:
            # Override description to read the user-supplied research report directly
            write_task = Task(
                description="""Using the provided market research report: {research_report}
Write an informative blog post or article. Ensure the tone is engaging and fits a professional audience.""",
                expected_output="A complete draft article (approx. 800-1000 words) with clear headings and engaging content.",
                agent=content_creator
            )
        tasks.append(write_task)

    if "seo" in enabled_agents:
        agents.append(marketing_specialist)
        if "writer" not in enabled_agents:
            # Override description to read the user-supplied blog draft directly
            refine_task = Task(
                description="""Analyze the provided draft article and optimize it for SEO. Draft article content: {blog_draft}
Use the SEO Keyword Tool exactly once to analyze the topic '{topic}' for relevant search terms, insert them naturally, write meta tags, and suggest heading updates.
Ensure the optimized content is of the highest professional standard: lead with non-obvious, high-value insights, use specific data or real-world examples, avoid generic advice or filler language, and prioritize depth over shallow coverage.""",
                expected_output="An SEO-optimized version of the article with a list of keywords used, target meta title, and meta description.",
                agent=marketing_specialist,
                tools=[seo_keyword_tool]
            )
        tasks.append(refine_task)
        tasks.append(distribute_task)

    # Determine whether any prior agent will produce content the later
    # creative agents can chain from within this same run.
    has_prior_content = any(a in enabled_agents for a in ("researcher", "writer", "seo"))

    if "scriptwriter" in enabled_agents:
        agents.append(script_writer)
        if not has_prior_content:
            # Standalone: read user-supplied content (fall back to topic) directly
            script_task = Task(
                description="""Write a complete, production-ready video script about: '{topic}'.
Source material / details to base the script on:
{content}

Write ONE script optimised for a short-form vertical video (Instagram Reel / YouTube Short / TikTok, 30-60 seconds)
AND a short outline for a longer YouTube video on the same topic.

Standards:
- Open with a scroll-stopping HOOK in the first 1-2 lines (a bold claim, a surprising stat, or a sharp question).
- Write spoken lines the way a creator would actually say them — conversational, punchy, no corporate filler.
- Pair every beat with concrete ON-SCREEN VISUAL / B-roll direction in [brackets].
- Build tension/value through the middle, then land a clear payoff and a single, specific call-to-action.

Format your answer EXACTLY as:
## Reel / Short Script (30-60s)
**Hook:** [spoken hook line]  [on-screen visual]
**Body:**
- [spoken line]  [on-screen visual]
- ...
**CTA:** [spoken call-to-action]  [on-screen visual]
**Suggested caption + hashtags:** ...
**On-screen text overlays:** ...

## YouTube Video Outline (longer form)
- Title ideas (3)
- Hook (first 15s, spoken)
- Main talking points / segments (with rough timestamps)
- Outro + CTA
""",
                expected_output="A reel/short script with hook, body beats, visual direction and CTA, plus a longer YouTube video outline.",
                agent=script_writer
            )
        tasks.append(script_task)

    if "infographic" in enabled_agents:
        agents.append(infographic_designer)
        if not has_prior_content:
            # Standalone: read user-supplied content (fall back to topic) directly
            infographic_task = Task(
                description="""Create a ready-to-post visual infographic IMAGE about: '{topic}'.
Source material / details to extract facts from:
{content}

Extract the most important and UNIQUE details — prefer non-obvious facts, numbers, comparisons or steps over generic statements.

Do these steps IN ORDER:
1. Choose a short infographic TITLE (max 6 words).
2. Write the 4-6 standout callouts, ONE PER LINE, each formatted 'HEADING — supporting one-liner' (max 8 words per heading).
3. Call the 'Generate Infographic Image' tool EXACTLY ONCE, passing the title and the callouts (as the key_points argument). The tool returns a line like 'INFOGRAPHIC_IMAGE: generated/xxx.png'.
4. Write platform-tailored captions.

Your final answer MUST be structured EXACTLY as:
## Infographic Key Points
- **HEADING** — supporting line
- ...

## Infographic Image
[Paste the EXACT line returned by the tool here, e.g. 'INFOGRAPHIC_IMAGE: generated/xxx.png'. If the tool returned an error, paste that error line instead.]

## Captions
**Instagram:** ...
**LinkedIn:** ...
**Twitter/X:** ...
""",
                expected_output="A list of key callouts, the exact 'INFOGRAPHIC_IMAGE: ...' line from the image tool, and platform-specific captions.",
                agent=infographic_designer
            )
        tasks.append(infographic_task)

    # Assemble the crew
    marketing_crew = Crew(
        agents=agents,
        tasks=tasks,
        process=Process.sequential,
        verbose=True,
        cache=False,  # Disabled: Groq rejects cache_breakpoint in messages
        step_callback=check_termination_callback
    )

    return marketing_crew

# Run the crew
if __name__ == "__main__":
    user_topic = input("Enter the topic for market research and content creation: ")
    if not user_topic.strip():
        user_topic = "AI Coding Agents in 2026"
        print(f"No topic entered. Using default topic: '{user_topic}'")

    inputs = {'topic': user_topic}
    marketing_crew = create_marketing_crew()
    result = marketing_crew.kickoff(inputs=inputs)

    print("######################")
    print("## CREW RUN COMPLETE ##")
    print("######################")
    print(result)



