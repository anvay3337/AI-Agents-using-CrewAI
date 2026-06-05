# Implementation Steps: Multi-Agent CrewAI Architecture (Using Ollama & Llama 3.2)

This guide explains how to implement the multi-agent architecture containing three agents (**Market Researcher**, **Content Creator**, and **Marketing Specialist**) and four sequential tasks (**Research**, **Write**, **Refine**, and **Distribute**), powered by a local **Ollama** server running **Llama 3.2**.

---

## Prerequisites: Set Up Ollama Local Server
1. Download and install [Ollama](https://ollama.com/).
2. Open your terminal or command prompt and run the Llama 3.2 model:
   ```bash
   ollama run llama3.2
   ```
3. Keep the Ollama server running. By default, it runs on `http://localhost:11434`.

---

## Step 1: Set Up Environment Variables (`.env`)
Create a file named `.env` in the root of your workspace to store configuration details securely.

```ini
# Since we are using local Ollama, we don't need a real OpenAI key.
# However, CrewAI/LiteLLM may check for its existence, so set it to 'NA'.
OPENAI_API_KEY=NA

# Ollama settings
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL_NAME=ollama/llama3.2

# Required if using SerperDevTool for web search
SERPER_API_KEY=your_serper_api_key_here
```

---

## Step 2: Define Tools (Search and Custom SEO Tool)
Define the Web Search and Custom SEO Keyword tools:

```python
from crewai.tools import tool
from crewai_tools import SerperDevTool

# 1. Web Search Tool (Built-in)
search_tool = SerperDevTool()

# 2. SEO Keyword Tool (Custom)
@tool("SEO Keyword Analysis Tool")
def seo_keyword_tool(topic: str) -> str:
    """Analyzes a topic and returns a list of high-traffic SEO keywords and search intent trends."""
    # Mock SEO keyword suggestions based on the topic
    return f"SEO Keywords for '{topic}': best {topic}, {topic} guide, how to use {topic}, trending {topic} 2026. Search Intent: Informational."
```

---

## Step 3: Configure Ollama LLM
Import the `LLM` class from CrewAI and instantiate it to point to your local Ollama instance running the `llama3.2` model.

```python
from crewai import LLM

# Configure LLM to point to local Ollama
ollama_llm = LLM(
    model="ollama/llama3.2",
    base_url="http://localhost:11434"
)
```

---

## Step 4: Define the Agents
Configure the three agents and assign the `ollama_llm` to each.

```python
from crewai import Agent

# Agent 1: Market Researcher (uses Web Search Tool)
market_researcher = Agent(
    role="Senior Market Researcher",
    goal="Gather and analyze the latest market trends, competitor activities, and user needs regarding a topic.",
    backstory="You are a seasoned analyst with an eye for detail. You excel at filtering noise to find valuable market insights using web search tools.",
    tools=[search_tool],
    llm=ollama_llm,
    verbose=True
)

# Agent 2: Content Creator (does not require tools, uses research output)
content_creator = Agent(
    role="Expert Content Creator",
    goal="Draft high-quality, engaging, and clear articles or reports based on market research reports.",
    backstory="You are a creative writer who knows how to capture reader attention. You translate raw analytical data into compelling narratives.",
    llm=ollama_llm,
    verbose=True
)

# Agent 3: Marketing Specialist (uses SEO Keyword Tool)
marketing_specialist = Agent(
    role="SEO and Distribution Specialist",
    goal="Optimize content for search engines using SEO keywords and outline a distribution strategy.",
    backstory="You are a digital marketing guru who understands search engine algorithms and content distribution channels to maximize reach and conversion.",
    tools=[seo_keyword_tool],
    llm=ollama_llm,
    verbose=True
)
```

---

## Step 5: Define the Tasks
Define the tasks and assign them to the correct agents.

```python
from crewai import Task

# Task 1: Research (Market Researcher)
research_task = Task(
    description="Search the web and compile a comprehensive market research report about: '{topic}'. Focus on key challenges, trends, and opportunities.",
    expected_output="A detailed market research report containing key findings, trends, and user pain points.",
    agent=market_researcher
)

# Task 2: Write (Content Creator)
write_task = Task(
    description="Using the market research report, write an informative blog post or article. Ensure the tone is engaging and fits a professional audience.",
    expected_output="A complete draft article (approx. 800-1000 words) with clear headings and engaging content.",
    agent=content_creator
)

# Task 3: Refine (Marketing Specialist)
refine_task = Task(
    description="Analyze the draft article and optimize it for SEO. Use the SEO Keyword Tool to find relevant search terms, insert them naturally, write meta tags, and suggest heading updates.",
    expected_output="An SEO-optimized version of the article with a list of keywords used, target meta title, and meta description.",
    agent=marketing_specialist
)

# Task 4: Distribute (Marketing Specialist)
distribute_task = Task(
    description="Based on the SEO-optimized article, create a content distribution plan specifying target platforms (e.g., LinkedIn, Twitter, newsletters) and draft promotional social media posts.",
    expected_output="A distribution plan with schedule suggestions and 3 tailored social media posts.",
    agent=marketing_specialist
)
```

---

## Step 6: Assemble the Crew and Kick Off
Combine your agents and tasks into a Crew. The default execution is sequential (`Process.sequential`), meaning Task 1 -> Task 2 -> Task 3 -> Task 4.

```python
from crewai import Crew, Process

# Assemble the crew
marketing_crew = Crew(
    agents=[market_researcher, content_creator, marketing_specialist],
    tasks=[research_task, write_task, refine_task, distribute_task],
    process=Process.sequential,
    verbose=True
)

# Run the crew
inputs = {'topic': 'AI Coding Agents in 2026'}
result = marketing_crew.kickoff(inputs=inputs)

print("######################")
print("## CREW RUN COMPLETE ##")
print("######################")
print(result)
```

---

## Step 7: Run the Script
1. Save the above code inside `main.py`.
2. Ensure your virtual environment is active.
3. Ensure Ollama is running and has downloaded the model (`ollama run llama3.2`).
4. Run the script:
   ```bash
   python main.py
   ```
