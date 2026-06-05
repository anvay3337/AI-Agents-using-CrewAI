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
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load env variables
load_dotenv()

# Import the crew factory from main.py
from main import create_marketing_crew, reset_termination, request_termination

app = FastAPI(title="CrewAI Marketing Agent API")

# Add CORS middleware to allow connections from Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
def read_index():
    """Serves the React frontend dashboard."""
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')

def clean_and_parse_line(line):
    # Strip ANSI color codes
    line = ANSI_ESCAPE.sub('', line).strip()
    if not line:
        return None
    
    # Filter out ASCII box-drawing characters and layout frames
    if any(char in line for char in ['┌', '─', '┐', '│', '└', '┘', '═', '╔', '╗', '╚', '╝']):
        return None
        
    # Filter out generic lifecycle tags
    if line in ['Task Started', 'Task Completed', 'Tool Completed', 'Agent Started']:
        return None
        
    # Filter out CrewAI event bus warnings and tracing messages
    if '[CrewAIEventsBus]' in line:
        return None
    if 'Tracing is disabled' in line or 'To enable tracing' in line or 'CREWAI_TRACING_ENABLED' in line:
        return None
        
    # Detect agent lifecycle starts
    agent_start_match = re.search(r'Agent:\s*([A-Za-z0-9\s\-\&]+)', line)
    if agent_start_match:
        agent_name = agent_start_match.group(1).strip()
        agent_id = None
        if "Researcher" in agent_name:
            agent_id = "researcher"
        elif "Creator" in agent_name or "Writer" in agent_name:
            agent_id = "creator"
        elif "Specialist" in agent_name or "SEO" in agent_name:
            agent_id = "seo"
        return {"type": "agent_start", "agent_id": agent_id, "agent_name": agent_name}
        
    # Detect tool starts
    tool_start_match = re.search(r'Tool:\s*([A-Za-z0-9_]+)', line)
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
    if line.startswith("Args:"):
        return None
        
    # Detect tool completions (we hide the actual result block from the log but emit an end notification)
    if (line.startswith("Tool ") and "executed with result:" in line) or "Tool Completed" in line:
        return {"type": "tool_end"}
        
    # Detect task starts
    if line.startswith("Name:"):
        task_name = line.replace("Name:", "").strip()
        task_id = None
        if "Search the web" in task_name or "research" in task_name.lower():
            task_id = "research"
        elif "write" in task_name.lower() or "blog post" in task_name.lower():
            task_id = "writing"
        elif "optimize" in task_name.lower() or "seo" in task_name.lower():
            task_id = "seo"
        elif "distribute" in task_name.lower() or "distribution" in task_name.lower():
            task_id = "distribution"
            
        if task_id:
            return {"type": "task_start", "task_id": task_id, "task_name": task_name}
            
    # Detect task completion
    if "Task Completed" in line:
        return {"type": "task_end"}
        
    # Suppress final answers or raw outputs in the console log
    if "Final Answer:" in line or "CREW RUN COMPLETE" in line:
        return None
    if line.startswith("{") and line.endswith("}"):
        return None
    if "todos_count=" in line or "todos_with_results=" in line:
        return None

    # Limit long lines (e.g. residual raw output leaks)
    if len(line) > 200:
        line = line[:197] + "..."
        
    return {"type": "status", "message": line}


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

@app.get("/api/run")
def run_crew(topic: str = Query(..., description="Topic to run market research on")):
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
        # Temporarily redirect stdout and stderr
        sys.stdout = writer
        sys.stderr = writer
        try:
            reset_termination()
            inputs = {"topic": topic}
            crew = create_marketing_crew()
            res = crew.kickoff(inputs=inputs)
            execution_result["output"] = str(res)
            execution_result["status"] = "success"
        except Exception as e:
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
    request_termination()
    return {"status": "termination_requested"}

if __name__ == "__main__":
    import uvicorn
    # Run the server on port 8000
    uvicorn.run(app, host="127.0.0.1", port=8000)
