import sys
import io

# Force standard streams to UTF-8 on Windows to prevent cp1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

from crewai.tools import tool
from crewai_tools import SerperDevTool
from dotenv import load_dotenv
from crewai import Agent
from crewai import Task
from crewai import Crew, Process

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

def create_marketing_crew():
    from crewai import LLM
    ollama_llm = LLM(
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
        llm=ollama_llm,
        verbose=True,
        max_iter=2,
        allow_delegation=False
    )

    # Agent 2: Content Creator (does not require tools, uses research output)
    content_creator = Agent(
        role="Expert Content Creator",
        goal="Draft high-quality, engaging, and clear articles or reports based on market research reports.",
        backstory="You are a creative writer who knows how to capture reader attention. You translate raw analytical data into compelling narratives.",
        llm=ollama_llm,
        verbose=True,
        max_iter=1,
        allow_delegation=False
    )

    # Agent 3: Marketing Specialist (uses SEO Keyword Tool)
    marketing_specialist = Agent(
        role="SEO and Distribution Specialist",
        goal="Optimize content for search engines using SEO keywords and outline a distribution strategy.",
        backstory="You are a digital marketing guru who understands search engine algorithms and content distribution channels to maximize reach and conversion.",
        tools=[],
        llm=ollama_llm,
        verbose=True,
        max_iter=2,
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
        description="Analyze the draft article and optimize it for SEO. Use the SEO Keyword Tool exactly once to analyze the topic '{topic}' for relevant search terms, insert them naturally, write meta tags, and suggest heading updates.",
        expected_output="An SEO-optimized version of the article with a list of keywords used, target meta title, and meta description.",
        agent=marketing_specialist,
        tools=[seo_keyword_tool]
    )

    # Task 4: Distribute (Marketing Specialist)
    distribute_task = Task(
        description="""Based on the SEO-optimized article from the previous task, compile a complete strategic campaign document. 
Perform two actions:
1. Include the full, SEO-optimized blog post from the previous task under a '## Strategic Blog Post' heading.
2. Create a content distribution plan specifying target platforms (e.g., LinkedIn, Twitter, newsletters) and draft promotional social media posts.

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
""",
        expected_output="A document containing the strategic blog post, the content distribution plan, and the social media promotional posts.",
        agent=marketing_specialist
    )

    def check_termination_callback(step_output):
        if is_termination_requested():
            raise RuntimeError("Campaign execution terminated by user.")

    # Assemble the crew
    marketing_crew = Crew(
        agents=[market_researcher, content_creator, marketing_specialist],
        tasks=[research_task, write_task, refine_task, distribute_task],
        process=Process.sequential,
        verbose=True,
        cache=True,
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



