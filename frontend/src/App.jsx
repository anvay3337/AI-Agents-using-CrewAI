import React, { useState, useEffect, useRef } from 'react';

// Selectable agents. `default` controls which are pre-checked on load.
const AGENT_META = [
  { id: 'researcher', name: 'Market Researcher', desc: 'Web research & trend analysis', default: true },
  { id: 'writer', name: 'Content Writer', desc: 'Drafts the blog / article', default: true },
  { id: 'seo', name: 'SEO & Distribution', desc: 'Optimizes + social media plan', default: true },
  { id: 'scriptwriter', name: 'Video Script Writer', desc: 'YouTube video & Reel scripts', default: false },
  { id: 'infographic', name: 'Infographic Designer', desc: 'Postable visual graphic + captions', default: false },
];

function App() {
  const [topic, setTopic] = useState('');
  const [agents, setAgents] = useState(() =>
    Object.fromEntries(AGENT_META.map((a) => [a.id, a.default]))
  );
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState([]);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const terminalEndRef = useRef(null);

  // Auto-scroll terminal logs
  useEffect(() => {
    if (terminalEndRef.current) {
      terminalEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [logs]);

  const toggleAgent = (id) => {
    setAgents((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const selectedAgents = AGENT_META.filter((a) => agents[a.id]).map((a) => a.id);

  const resolveBackendBaseUrl = () => {
    if (import.meta.env.VITE_API_URL) {
      return import.meta.env.VITE_API_URL;
    }
    const host = window.location.hostname.toLowerCase();
    const port = window.location.port;

    if ((host === 'localhost' || host === '127.0.0.1') && port !== '8001') {
      return 'http://localhost:8001';
    }

    const isStaticHost =
      host.endsWith('.netlify.app') ||
      host.endsWith('.vercel.app') ||
      host.endsWith('.github.io') ||
      host.endsWith('.gitlab.io') ||
      host.endsWith('.pages.dev');

    if (isStaticHost) {
      return 'http://localhost:8001';
    }

    return '';
  };

  // Maps a single parsed SSE event onto component state.
  const handleEvent = (data) => {
    if (data.type === 'status') {
      setLogs((prev) => [...prev, data.message]);
    } else if (data.type === 'agent_start') {
      setLogs((prev) => [...prev, `🤖 Agent: ${data.agent_name}`]);
    } else if (data.type === 'tool_start') {
      setLogs((prev) => [...prev, `🔧 Tool: ${data.tool_name}`]);
    } else if (data.type === 'tool_end') {
      setLogs((prev) => [...prev, `✅ Tool Execution Complete.`]);
    } else if (data.type === 'task_start') {
      setLogs((prev) => [...prev, `🚀 Task Started: ${data.task_name}`]);
    } else if (data.type === 'result') {
      setResult(data.output);
      setRunning(false);
    } else if (data.type === 'error') {
      setError(data.message);
      setRunning(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!topic.trim()) return;
    if (selectedAgents.length === 0) {
      setError('Please select at least one agent to run.');
      return;
    }

    // Reset state
    setRunning(true);
    setLogs([]);
    setResult(null);
    setError(null);

    const backendBaseUrl = resolveBackendBaseUrl();

    try {
      const resp = await fetch(`${backendBaseUrl}/api/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ topic, enabled_agents: selectedAgents }),
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`Backend responded with status ${resp.status}`);
      }

      // Read the Server-Sent-Events stream from the POST response body.
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const blocks = buffer.split('\n\n');
        buffer = blocks.pop(); // keep incomplete trailing block

        for (const block of blocks) {
          const dataLine = block
            .split('\n')
            .find((line) => line.startsWith('data:'));
          if (!dataLine) continue; // skip ping comments etc.
          const payload = dataLine.slice(5).trim();
          if (!payload) continue;
          try {
            handleEvent(JSON.parse(payload));
          } catch (err) {
            console.error('Failed to parse SSE payload:', payload, err);
          }
        }
      }

      setRunning(false);
    } catch (err) {
      console.error('Run failed:', err);
      setError(
        'Connection lost or failed to connect to backend server. Make sure the backend server is running and accessible.'
      );
      setRunning(false);
    }
  };

  // Helper to parse social posts from the final output markdown
  const parseResult = (text) => {
    if (!text) return null;

    const facebookMatch = text.match(/\*\*Facebook Post:\*\*\s*\n?\s*["']([\s\S]*?)["'](?=\n\n|\n\*\*\w+)/i) ||
      text.match(/Facebook Post:\s*\n?\s*([\s\S]*?)(?=\n\n|\n\d+\.|\n\*|\n\[Image)/i);
    const twitterMatch = text.match(/\*\*Twitter Post:\*\*\s*\n?\s*["']([\s\S]*?)["'](?=\n\n|\n\*\*\w+)/i) ||
      text.match(/Twitter Post:\s*\n?\s*([\s\S]*?)(?=\n\n|\n\d+\.|\n\*|\n\[Image)/i);
    const instagramMatch = text.match(/\*\*Instagram Post:\*\*\s*\n?\s*["']([\s\S]*?)["'](?=\n\n|\n\*\*\w+)/i) ||
      text.match(/Instagram Post:\s*\n?\s*([\s\S]*?)(?=\n\n|\n\d+\.|\n\*|\n\[Image)/i);

    // Generated infographic image, emitted by the backend as "INFOGRAPHIC_IMAGE: generated/xxx.png"
    const imageMatch = text.match(/INFOGRAPHIC_IMAGE:\s*([^\s)\]]+\.png)/i);

    return {
      facebook: facebookMatch ? facebookMatch[1].trim() : null,
      twitter: twitterMatch ? twitterMatch[1].trim() : null,
      instagram: instagramMatch ? instagramMatch[1].trim() : null,
      image: imageMatch ? imageMatch[1].trim() : null,
      raw: text
    };
  };

  const parsedResult = parseResult(result);
  const backendBaseUrl = resolveBackendBaseUrl();
  const infographicUrl =
    parsedResult && parsedResult.image
      ? `${backendBaseUrl}/${parsedResult.image.replace(/^\/+/, '')}`
      : null;

  return (
    <div className="app-container">
      <header className="header">
        <h1>Agentic Marketing Crew</h1>
        <p>
          Pick the agents you want, then run research, articles, SEO, video scripts, and infographics — powered by CrewAI.
        </p>
      </header>

      {/* Input Form */}
      <section className="glass-card">
        <form onSubmit={handleSubmit} className="search-form">
          <div className="input-group">
            <label htmlFor="topic-input">Campaign Topic</label>
            <div className="input-container">
              <input
                id="topic-input"
                type="text"
                className="text-input"
                placeholder="e.g. https://example.com/article, Quantum Computing in 2026..."
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                disabled={running}
              />
              <button type="submit" className="glow-button" disabled={running || !topic.trim()}>
                {running ? (
                  <>
                    <div className="spinner"></div>
                    Executing Crew...
                  </>
                ) : (
                  <>
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                      <path d="M5 12h14M12 5l7 7-7 7" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                    Start Run
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Agent selector */}
          <div className="input-group">
            <label>Agents to run</label>
            <div className="agent-grid">
              {AGENT_META.map((a) => (
                <button
                  type="button"
                  key={a.id}
                  className={`agent-chip ${agents[a.id] ? 'active' : ''}`}
                  onClick={() => toggleAgent(a.id)}
                  disabled={running}
                >
                  <span className="agent-check" aria-hidden="true">
                    {agents[a.id] ? '✓' : ''}
                  </span>
                  <span className="agent-text">
                    <span className="agent-name">{a.name}</span>
                    <span className="agent-desc">{a.desc}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
        </form>
      </section>

      {/* Terminal logs console */}
      <section className="terminal-window">
        <div className="terminal-header">
          <div className="terminal-dots">
            <div className="dot red"></div>
            <div className="dot yellow"></div>
            <div className="dot green"></div>
          </div>
          <span className="terminal-title">CrewAI Output Logger</span>
          <div style={{ width: '48px' }}></div>
        </div>
        <div className="terminal-body">
          {logs.length === 0 ? (
            <div className="terminal-placeholder">
              {running ? "Initializing model..." : "Select your agents, enter a topic, and click Start Run."}
            </div>
          ) : (
            logs.map((log, index) => (
              <span key={index} className="terminal-line">
                {log}
              </span>
            ))
          )}
          <div ref={terminalEndRef} />
        </div>
      </section>

      {/* Error display */}
      {error && (
        <section className="glass-card" style={{ borderColor: 'rgba(239, 68, 68, 0.4)', background: 'rgba(239, 68, 68, 0.08)' }}>
          <h3 style={{ color: '#ef4444', display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            Execution Error
          </h3>
          <p style={{ color: '#fca5a5' }}>{error}</p>
        </section>
      )}

      {/* Final Results Dashboard */}
      {result && (
        <section className="results-container">
          {/* Main article / distribution plan output */}
          <div className="result-main glass-card">
            <h2 className="result-section-title">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <line x1="10" y1="9" x2="8" y2="9" />
              </svg>
              Final Generated Report & Strategy
            </h2>
            {infographicUrl && (
              <div className="infographic-display">
                <img src={infographicUrl} alt="Generated infographic" />
                <a className="infographic-download" href={infographicUrl} download target="_blank" rel="noreferrer">
                  ⬇ Download infographic
                </a>
              </div>
            )}
            <div className="content-block">
              {result.split('\n').map((para, i) => {
                if (para.includes('INFOGRAPHIC_IMAGE:')) {
                  return null; // raw marker is rendered as the image above
                } else if (para.startsWith('### ')) {
                  return <h3 key={i}>{para.replace('### ', '')}</h3>;
                } else if (para.startsWith('## ')) {
                  return <h2 key={i}>{para.replace('## ', '')}</h2>;
                } else if (para.startsWith('* ') || para.startsWith('- ')) {
                  return <li key={i} style={{ marginLeft: '1rem', marginBottom: '0.25rem' }}>{para.substring(2)}</li>;
                } else if (para.trim()) {
                  const boldRegex = /\*\*(.*?)\*\*/g;
                  const parts = para.split(boldRegex);
                  return (
                    <p key={i}>
                      {parts.map((part, index) =>
                        index % 2 === 1 ? <strong key={index} style={{ color: '#ffffff' }}>{part}</strong> : part
                      )}
                    </p>
                  );
                }
                return <br key={i} />;
              })}
            </div>
          </div>

          {/* Social posts sidebar */}
          <div className="result-sidebar">
            <div className="glass-card">
              <h2 className="result-section-title">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
                </svg>
                Social Media Posts
              </h2>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem', marginTop: '1rem' }}>
                {parsedResult.facebook && (
                  <div className="post-card">
                    <div className="post-header">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M18 2h-3a5 5 0 0 0-5 5v3H7v4h3v8h4v-8h3l1-4h-4V7a1 1 0 0 1 1-1h3z" />
                      </svg>
                      Facebook Post
                    </div>
                    <div className="post-body">{parsedResult.facebook}</div>
                    <div className="post-image-placeholder">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
                      </svg>
                      [Infographic / Banner Suggestion Included]
                    </div>
                  </div>
                )}

                {parsedResult.twitter && (
                  <div className="post-card">
                    <div className="post-header">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M23 3a10.9 10.9 0 0 1-3.14 1.53 4.48 4.48 0 0 0-7.86 3v1A10.66 10.66 0 0 1 3 4s-4 9 5 13a11.64 11.64 0 0 1-7 2c9 5 20 0 20-11.5a4.5 4.5 0 0 0-.08-.83A7.72 7.72 0 0 0 23 3z" />
                      </svg>
                      Twitter Post
                    </div>
                    <div className="post-body">{parsedResult.twitter}</div>
                    <div className="post-image-placeholder">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
                      </svg>
                      [Promotional Graphics Suggestion Included]
                    </div>
                  </div>
                )}

                {parsedResult.instagram && (
                  <div className="post-card">
                    <div className="post-header">
                      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="2" y="2" width="20" height="20" rx="5" ry="5" /><path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z" /><line x1="17.5" y1="6.5" x2="17.51" y2="6.5" />
                      </svg>
                      Instagram Post
                    </div>
                    <div className="post-body">{parsedResult.instagram}</div>
                    <div className="post-image-placeholder">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2" /><circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
                      </svg>
                      [Visually Appealing Graphic Suggestion Included]
                    </div>
                  </div>
                )}

                {!parsedResult.facebook && !parsedResult.twitter && !parsedResult.instagram && (
                  <p style={{ color: '#64748b', fontSize: '0.9rem', fontStyle: 'italic' }}>
                    No parsed social posts found. The full text output is shown in the main window.
                  </p>
                )}
              </div>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}

export default App;
