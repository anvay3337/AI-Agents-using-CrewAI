import sys
import io
import re

# Force standard streams to UTF-8 on Windows to prevent cp1252 encoding errors
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

import queue
import threading
import json
import asyncio
import os
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional, List

# Load env variables
load_dotenv()

# Print environment diagnostic information (masking keys)
import os
print("=== ENVIRONMENT DIAGNOSTICS ===")
for key in ["OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "GEMINI_IMAGE_API_KEY", "SERPER_API_KEY", "PORT", "RAILWAY_ENVIRONMENT"]:
    val = os.environ.get(key)
    if val is None:
        print(f"  {key}: NOT SET")
    elif val == "":
        print(f"  {key}: EMPTY STRING")
    else:
        # Mask key for security
        masked = val[:6] + "..." + val[-4:] if len(val) > 10 else "SET (short)"
        if key == "PORT" or key == "RAILWAY_ENVIRONMENT":
            masked = val
        print(f"  {key}: {masked}")
print("===============================")


# Import the crew factory from main.py (deferred to prevent circular import issues)

app = FastAPI(title="CrewAI Marketing Agent API")

# Add CORS middleware to allow connections from Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated infographic images (created by the Infographic agent's Gemini tool).
_GENERATED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")
os.makedirs(_GENERATED_DIR, exist_ok=True)
app.mount("/generated", StaticFiles(directory=_GENERATED_DIR), name="generated")

# Serve vendored frontend libraries (React/ReactDOM/Babel) locally instead of
# from unpkg.com — the CDN is unreliable on this network and a failed download
# leaves the dashboard blank. See /vendor/*.js referenced in index.html.
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
os.makedirs(_VENDOR_DIR, exist_ok=True)
app.mount("/vendor", StaticFiles(directory=_VENDOR_DIR), name="vendor")

@app.get("/", response_class=HTMLResponse)
def read_index():
    """Serves the React frontend dashboard."""
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


_FAVICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.png")

@app.get("/favicon.png")
def favicon():
    """Serves the dashboard favicon (referenced by index.html)."""
    return FileResponse(_FAVICON_PATH, media_type="image/png")


ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

def clean_and_parse_line(line):
    # Strip ANSI color codes
    line = ANSI_ESCAPE.sub('', line).strip()
    if not line:
        return None
    
    # Check if this line is purely a box border (e.g. ┌────, └────, etc.)
    border_chars = set('┌─┐│└┘═╔╗╚╝─')
    stripped_for_border_check = ''.join(c for c in line if c not in border_chars).strip()
    if not stripped_for_border_check:
        return None
        
    # Strip leading/trailing vertical bars and box outlines to process inner content
    cleaned_line = line.strip('│┌┐└┘═╔╗╚╝─').strip()
    if not cleaned_line:
        return None
        
    # Filter out generic lifecycle tags
    if cleaned_line in ['Task Started', 'Task Completed', 'Tool Completed', 'Agent Started']:
        return None
    cleaned_line_no_emoji = re.sub(r'[^\x00-\x7F]+', '', cleaned_line).strip()
    if cleaned_line_no_emoji in ['Task Started', 'Task Completed', 'Tool Completed', 'Agent Started']:
        return None
        
    # Filter out CrewAI event bus warnings and tracing messages
    if '[CrewAIEventsBus]' in cleaned_line:
        return None
    if 'Tracing is disabled' in cleaned_line or 'To enable tracing' in cleaned_line or 'CREWAI_TRACING_ENABLED' in cleaned_line:
        return None
        
    # Detect agent lifecycle starts
    agent_start_match = re.search(r'Agent:\s*([A-Za-z0-9\s\-\&]+)', cleaned_line)
    if agent_start_match:
        agent_name = agent_start_match.group(1).strip()
        agent_id = None
        if "Researcher" in agent_name:
            agent_id = "researcher"
        elif "Script" in agent_name:
            agent_id = "scriptwriter"
        elif "Infographic" in agent_name:
            agent_id = "infographic"
        elif "Creator" in agent_name or "Writer" in agent_name:
            agent_id = "creator"
        elif "Specialist" in agent_name or "SEO" in agent_name:
            agent_id = "seo"
        return {"type": "agent_start", "agent_id": agent_id, "agent_name": agent_name}
        
    # Detect tool starts
    tool_start_match = re.search(r'Tool:\s*([A-Za-z0-9_]+)', cleaned_line)
    if tool_start_match:
        tool_name = tool_start_match.group(1).strip()
        # Map to user friendly name
        friendly_tool = tool_name
        if tool_name == 'search_tool' or tool_name == 'web_search_tool':
            friendly_tool = "Google Search Tool"
        elif tool_name == 'seo_keyword_tool' or tool_name == 'seo_keyword_analysis_tool':
            friendly_tool = "SEO Analysis Tool"
        return {"type": "tool_start", "tool_name": friendly_tool}
        
    # Ignore raw tool arguments to avoid exposing JSON details in user view
    if cleaned_line.startswith("Args:"):
        return None
        
    # Detect tool completions (we hide the actual result block from the log but emit an end notification)
    if (cleaned_line.startswith("Tool ") and "executed with result:" in cleaned_line) or "Tool Completed" in cleaned_line:
        return {"type": "tool_end"}
        
    # Detect task starts
    if cleaned_line.startswith("Name:"):
        task_name = cleaned_line.replace("Name:", "").strip().lower()
        task_id = None
        
        # Check writing task (starts with 'Using the market research report')
        if "using the market research" in task_name or "write" in task_name or "blog post" in task_name:
            task_id = "writing"
        # Check distribution task (starts with 'Based on the SEO-optimized')
        elif "based on the seo-optimized" in task_name or "distribute" in task_name or "distribution" in task_name:
            task_id = "distribution"
        # Check research task (starts with 'Search the web' or 'Compile a comprehensive')
        elif "compile a comprehensive" in task_name or "search the web" in task_name or "research" in task_name:
            task_id = "research"
        # Check SEO task (starts with 'Analyze the draft article and optimize')
        elif "optimize" in task_name or "seo" in task_name:
            task_id = "seo"
            
        if task_id:
            return {"type": "task_start", "task_id": task_id, "task_name": cleaned_line.replace("Name:", "").strip()}
            
    # Detect task completion
    if "Task Completed" in cleaned_line:
        return {"type": "task_end"}
        
    # Suppress final answers or raw outputs in the console log
    if "Final Answer:" in cleaned_line or "CREW RUN COMPLETE" in cleaned_line:
        return None
    if cleaned_line.startswith("{") and cleaned_line.endswith("}"):
        return None
    if "todos_count=" in cleaned_line or "todos_with_results=" in cleaned_line:
        return None

    # Limit long lines (e.g. residual raw output leaks)
    if len(cleaned_line) > 200:
        cleaned_line = cleaned_line[:197] + "..."
        
    return {"type": "status", "message": cleaned_line}


class QueueWriter:
    """Redirects stdout to a queue, buffering lines."""
    def __init__(self, log_queue):
        self.log_queue = log_queue
        self.original_stdout = sys.stdout
        self.buffer = []

    def write(self, data):
        self.original_stdout.write(data)
        if data:
            self.buffer.append(data)
            if '\n' in data:
                full_text = "".join(self.buffer)
                lines = full_text.split('\n')
                self.buffer = [lines[-1]]
                for line in lines[:-1]:
                    self.log_queue.put(line)

    def flush(self):
        self.original_stdout.flush()
        if self.buffer:
            remaining = "".join(self.buffer)
            if remaining:
                self.log_queue.put(remaining)
            self.buffer = []

# Thread lock to prevent concurrent crew executions
crew_lock = threading.Lock()

class CampaignRequest(BaseModel):
    topic: Optional[str] = None
    blog_draft: Optional[str] = None
    research_report: Optional[str] = None
    content: Optional[str] = None
    enabled_agents: List[str] = ["researcher", "writer", "seo"]

@app.post("/api/run")
def run_crew(request: CampaignRequest):
    # Try to acquire lock non-blockingly
    acquired = crew_lock.acquire(blocking=False)
    if not acquired:
        # Return a stream that immediately yields an error indicating system is busy
        async def busy_generator():
            yield f"data: {json.dumps({'type': 'error', 'message': 'Neural Core is busy processing another campaign. Please wait.'})}\n\n"
        return StreamingResponse(busy_generator(), media_type="text/event-stream")

    log_queue = queue.Queue()

    # Capture stdout and stderr
    writer = QueueWriter(log_queue)
    
    # Store execution result
    execution_result = {"status": "running", "output": None, "error": None}

    def run_in_thread():
        from main import create_marketing_crew, reset_termination, is_termination_requested
        # Temporarily redirect stdout and stderr
        sys.stdout = writer
        sys.stderr = writer
        crew = None
        try:
            reset_termination()
            inputs = {}
            if request.topic:
                inputs["topic"] = request.topic
            if request.blog_draft:
                inputs["blog_draft"] = request.blog_draft
            if request.research_report:
                inputs["research_report"] = request.research_report

            # Script writer / infographic agents may run standalone and reference
            # a {content} placeholder; always provide the key to avoid interpolation errors.
            standalone_creative = (
                any(a in request.enabled_agents for a in ("scriptwriter", "infographic"))
                and not any(a in request.enabled_agents for a in ("researcher", "writer", "seo"))
            )
            if request.content is not None or standalone_creative:
                inputs["content"] = request.content or request.topic or ""

            crew = create_marketing_crew(
                enabled_agents=request.enabled_agents,
                blog_draft=request.blog_draft,
                research_report=request.research_report,
                content=request.content
            )
            res = crew.kickoff(inputs=inputs)
            # Concatenate every task's output so the UI shows the work of each
            # enabled agent (not just the final task in the sequence).
            outputs = []
            if hasattr(crew, "tasks"):
                for task in crew.tasks:
                    task_out = getattr(task, "output", None)
                    raw = getattr(task_out, "raw", None) if task_out else None
                    if raw:
                        role = getattr(getattr(task, "agent", None), "role", "Agent")
                        outputs.append(f"## {role}\n\n{raw}\n")
            execution_result["output"] = "\n".join(outputs) if outputs else str(res)
            execution_result["status"] = "success"
        except Exception as e:
            # Check if this was a termination request and we have partial outputs
            if is_termination_requested() or "terminated" in str(e).lower():
                partial_outputs = []
                if crew and hasattr(crew, "tasks"):
                    for task in crew.tasks:
                        if hasattr(task, "output") and task.output and hasattr(task.output, "raw") and task.output.raw:
                            task_desc = getattr(task, "description", "Task Output")
                            # Truncate description for headers
                            task_header = task_desc[:60] + "..." if len(task_desc) > 60 else task_desc
                            # Clean task header description formatting
                            task_header = task_header.replace('\n', ' ').strip()
                            partial_outputs.append(f"## {task_header}\n{task.output.raw}\n")
                
                if partial_outputs:
                    execution_result["output"] = "\n".join(partial_outputs)
                    execution_result["status"] = "success"
                    return
            execution_result["error"] = str(e)
            execution_result["status"] = "error"
        finally:
            writer.flush()
            # Restore stdout and stderr
            sys.stdout = writer.original_stdout
            sys.stderr = writer.original_stdout
            # Put sentinel in queue to indicate completion
            log_queue.put(None)
            # Release lock
            crew_lock.release()

    # Start kickoff in a background thread
    thread = threading.Thread(target=run_in_thread)
    thread.start()

    async def event_generator():
        loop = asyncio.get_event_loop()
        ping_counter = 0
        while True:
            # Non-blocking check of the queue in a separate thread
            try:
                line = await loop.run_in_executor(None, lambda: log_queue.get(timeout=0.1))
                if line is None:
                    # Thread has finished
                    break
                event = clean_and_parse_line(line)
                if event:
                    # Yield the parsed event as an SSE message
                    yield f"data: {json.dumps(event)}\n\n"
            except queue.Empty:
                ping_counter += 1
                if ping_counter >= 50:  # Every 5 seconds
                    yield ": ping\n\n"
                    ping_counter = 0
                await asyncio.sleep(0.1)

        # Send final execution status and outputs
        if execution_result["status"] == "success":
            yield f"data: {json.dumps({'type': 'result', 'output': execution_result['output']})}\n\n"
        else:
            yield f"data: {json.dumps({'type': 'error', 'message': execution_result['error'] or 'Unknown error occurred.'})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/terminate")
def terminate_crew():
    from main import request_termination
    request_termination()
    return {"status": "termination_requested"}

if __name__ == "__main__":
    import uvicorn
    import os
    # Run the server on the port specified by the environment variable, defaulting to 8001
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)
