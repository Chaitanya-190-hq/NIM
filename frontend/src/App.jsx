import { useEffect, useRef, useState } from "react";

const API_BASE = "/api";

const SAMPLE_CONFIG = `from openai import OpenAI

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key="nvapi-YOUR_KEY_HERE",
)

response = client.chat.completions.create(
    model="openai/gpt-oss-20b",
    messages=[{"role": "user", "content": "Hello"}],
)
`;

function extractDetail(data) {
  if (!data) return null;
  const detail = data.detail ?? data.message;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || JSON.stringify(item)).join(" ");
  }
  return null;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(API_BASE + path, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
  } catch {
    throw new Error(
      "Could not reach the backend. Is the FastAPI server running on port 8000?"
    );
  }
  const text = await response.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!response.ok) {
    throw new Error(
      extractDetail(data) || `Request failed with status ${response.status}.`
    );
  }
  return data;
}

export default function App() {
  const [configs, setConfigs] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [code, setCode] = useState(SAMPLE_CONFIG);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null);
  const [notes, setNotes] = useState([]);
  const logRef = useRef(null);

  const active = configs.find((config) => config.id === activeId) || null;

  useEffect(() => {
    let cancelled = false;
    request("/configs")
      .then((data) => {
        if (cancelled) return;
        const list = data.configs || [];
        setConfigs(list);
        if (list.length) setActiveId(list[list.length - 1].id);
      })
      .catch((error) => {
        if (!cancelled) setNotice({ kind: "error", text: error.message });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [active?.messages, busy]);

  function upsert(config) {
    setConfigs((previous) => {
      const index = previous.findIndex((item) => item.id === config.id);
      if (index === -1) return [...previous, config];
      const next = previous.slice();
      next[index] = config;
      return next;
    });
  }

  async function handleConnect(event) {
    event.preventDefault();
    setBusy("connect");
    setNotice(null);
    setNotes([]);
    try {
      const created = await request("/configs", {
        method: "POST",
        body: JSON.stringify({ python_code: code }),
      });
      upsert(created.config);
      setActiveId(created.config.id);
      setNotes(created.notes || []);
      setNotice({
        kind: "info",
        text: "Configuration extracted. Testing the NVIDIA connection…",
      });

      const tested = await request(`/configs/${created.config.id}/test`, {
        method: "POST",
      });
      upsert(tested.config);
      setNotice(
        tested.config.status === "connected"
          ? { kind: "ok", text: `Connected to ${tested.config.model}.` }
          : { kind: "error", text: `Connection failed — ${tested.config.error}` }
      );
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setBusy("");
    }
  }

  async function handleRetryTest(config) {
    setBusy("test");
    setNotice({ kind: "info", text: `Testing ${config.model}…` });
    try {
      const tested = await request(`/configs/${config.id}/test`, { method: "POST" });
      upsert(tested.config);
      setNotice(
        tested.config.status === "connected"
          ? { kind: "ok", text: `Connected to ${tested.config.model}.` }
          : { kind: "error", text: `Connection failed — ${tested.config.error}` }
      );
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setBusy("");
    }
  }

  async function handleSend(event) {
    event.preventDefault();
    const message = draft.trim();
    if (!message || !active) return;
    setBusy("chat");
    setNotice(null);
    try {
      const data = await request(`/configs/${active.id}/chat`, {
        method: "POST",
        body: JSON.stringify({ message }),
      });
      upsert(data.config);
      setDraft("");
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setBusy("");
    }
  }

  async function handleClear() {
    if (!active) return;
    setBusy("clear");
    try {
      const data = await request(`/configs/${active.id}/clear`, { method: "POST" });
      upsert(data.config);
      setNotice(null);
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setBusy("");
    }
  }

  async function handleRemove(config) {
    setBusy("remove");
    try {
      const data = await request(`/configs/${config.id}`, { method: "DELETE" });
      const list = data.configs || [];
      setConfigs(list);
      if (config.id === activeId) {
        setActiveId(list.length ? list[list.length - 1].id : null);
      }
      setNotice({ kind: "info", text: `Removed ${config.model} from memory.` });
    } catch (error) {
      setNotice({ kind: "error", text: error.message });
    } finally {
      setBusy("");
    }
  }

  const canChat = Boolean(active) && active.status === "connected" && busy !== "chat";

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <h1>NVIDIA // BRIDGE</h1>
          <p className="subtitle">Paste a Python config, connect, chat.</p>
        </div>
        <span className="pill">
          {configs.length} model{configs.length === 1 ? "" : "s"} in memory
        </span>
      </header>

      {notice && <div className={`banner ${notice.kind}`}>{notice.text}</div>}

      <main className="grid">
        <section className="panel">
          <h2>1 · Paste Python configuration</h2>
          <textarea
            className="editor"
            value={code}
            spellCheck={false}
            onChange={(event) => setCode(event.target.value)}
            aria-label="Python configuration"
          />
          <form className="row" onSubmit={handleConnect}>
            <button type="submit" disabled={busy === "connect"}>
              {busy === "connect" ? "Connecting…" : "Connect"}
            </button>
            <span className="hint">Parsed, never executed. Keys stay on the backend.</span>
          </form>

          {notes.length > 0 && (
            <ul className="notes">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}

          <h2>2 · Connected models</h2>
          {configs.length === 0 && (
            <p className="empty">No models yet. Paste a config above and press Connect.</p>
          )}
          <ul className="configs">
            {configs.map((config) => (
              <li
                key={config.id}
                className={`config ${config.id === activeId ? "active" : ""}`}
              >
                <button
                  type="button"
                  className="config-main"
                  onClick={() => setActiveId(config.id)}
                >
                  <span className="model">{config.model}</span>
                  <span className="meta">{config.base_url}</span>
                  <span className="meta">key {config.api_key_preview}</span>
                </button>
                <div className="config-side">
                  <span className={`status ${config.status}`}>{config.status}</span>
                  {config.status !== "connected" && (
                    <button
                      type="button"
                      className="link"
                      onClick={() => handleRetryTest(config)}
                      disabled={Boolean(busy)}
                    >
                      retry test
                    </button>
                  )}
                  <button
                    type="button"
                    className="link danger"
                    onClick={() => handleRemove(config)}
                    disabled={Boolean(busy)}
                  >
                    remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel chat">
          <h2>3 · Chat{active ? ` · ${active.model}` : ""}</h2>
          <div className="log" ref={logRef}>
            {!active && <p className="empty">Connect a model to start chatting.</p>}
            {active && active.messages.length === 0 && (
              <p className="empty">
                {active.status === "connected"
                  ? "No messages yet. Ask something below."
                  : "This model is not connected. Retry the test or reconnect."}
              </p>
            )}
            {active &&
              active.messages.map((message, index) => (
                <div key={index} className={`msg ${message.role}`}>
                  <span className="who">{message.role === "user" ? "You" : "Model"}</span>
                  <div className="body">{message.content}</div>
                </div>
              ))}
            {busy === "chat" && <p className="pending">Waiting for the model…</p>}
          </div>

          <form className="composer" onSubmit={handleSend}>
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={canChat ? "Ask the model something…" : "Connect a model first"}
              disabled={!canChat}
              aria-label="Prompt"
            />
            <button type="submit" disabled={!canChat || !draft.trim()}>
              Send
            </button>
            <button
              type="button"
              className="secondary"
              onClick={handleClear}
              disabled={!active || Boolean(busy)}
            >
              Clear Chat
            </button>
          </form>
        </section>
      </main>
    </div>
  );
}
