import { useRef, useState } from "react";
import { askAgent, speechToText, textToSpeech } from "../api.js";
import { useRecorder } from "../useRecorder.js";

const SAMPLES = [
  "इस हफ्ते प्याज की खुदाई करूँ या रुकूँ? और कहाँ बेचूँ?",
  "माझ्या शेताला या आठवड्यात पाणी द्यावे का?",
  "Is my crop healthy? Should I worry about rain this week?",
  "प्याज में थ्रिप्स कीट लगे हैं, क्या करूँ?",
  "What is the capital of France?", // off-topic guardrail demo
];

const TOOL_LABELS = {
  location: "📍 Locate field",
  weather_forecast: "🌦️ Weather forecast",
  climate_anomaly: "📉 Rain vs normal",
  soil_profile: "🟫 Soil profile",
  crop_ndvi: "🛰️ Satellite NDVI",
  mandi_prices: "💰 Mandi prices",
  agronomy_docs: "📚 Official guides (ICAR, MPKV)",
};

function TraceItem({ e }) {
  switch (e.type) {
    case "node":
      return <li className="t-node">▸ {e.msg}</li>;
    case "plan":
      return (
        <li className="t-plan">
          🧭 Plan: {e.understanding.plan}
          <div className="t-meta">
            intent <b>{e.understanding.intent}</b> · lang <b>{e.understanding.language}</b>
            {e.understanding.crop && <> · crop <b>{e.understanding.crop}</b></>}
          </div>
        </li>
      );
    case "tool_start":
      return <li className="t-tool">⏳ {TOOL_LABELS[e.tool] || e.tool}</li>;
    case "tool_end":
      return (
        <li className={e.ok ? "t-ok" : "t-fail"}>
          {e.ok ? "✅" : "⚠️"} {TOOL_LABELS[e.tool] || e.tool} · {e.latency_s}s
          {e.stale && " · cached"}
          {!e.ok && <div className="t-meta">{e.error}</div>}
        </li>
      );
    case "verify":
      return (
        <li className={e.ok ? "t-ok" : "t-fail"}>
          🔎 Verifier (attempt {e.attempt}): {e.ok ? `all ${e.numbers_checked} numbers grounded in data` : "rejected draft"}
          {!e.ok && <ul className="t-meta">{e.issues.map((i, k) => <li key={k}>{i}</li>)}</ul>}
        </li>
      );
    case "warning":
    case "error":
      return <li className="t-fail">⚠️ {e.msg}</li>;
    default:
      return null;
  }
}

// Minimal markdown: **bold**, "* " / "- " bullets, "#" headings; [T#] become citation chips.
function AnswerText({ text, citations }) {
  const byId = Object.fromEntries(citations.map((c) => [c.id, c]));
  const cleaned = text.replace(/^\s*[*-]\s+/gm, "• ").replace(/^#+\s*/gm, "");
  const parts = cleaned.split(/(\[T\d+\]|\*\*[^*]+\*\*)/g);
  return (
    <div className="answer-text">
      {parts.map((p, i) => {
        if (p.startsWith("**") && p.endsWith("**")) return <strong key={i}>{p.slice(2, -2)}</strong>;
        const m = p.match(/^\[(T\d+)\]$/);
        if (!m) return <span key={i}>{p}</span>;
        const c = byId[m[1]];
        return (
          <a key={i} className="chip" href={c?.url} target="_blank" rel="noreferrer"
             title={c ? `${c.source} · ${c.fetched_at}` : "unverified"}>
            {m[1]}
          </a>
        );
      })}
    </div>
  );
}

export default function Chat({ field }) {
  const [question, setQuestion] = useState("");
  const [crop, setCrop] = useState("onion");
  const [trace, setTrace] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [voiceBusy, setVoiceBusy] = useState("");
  const abortRef = useRef(null);
  const recorder = useRecorder();

  const toggleMic = async () => {
    setError("");
    if (!recorder.recording) {
      try { await recorder.start(); } catch (e) { setError(`Microphone: ${e.message}`); }
      return;
    }
    const blob = await recorder.stop();
    setVoiceBusy("Transcribing…");
    try {
      const { transcript } = await speechToText(blob);
      if (!transcript.trim()) throw new Error("No speech detected, please try again");
      setQuestion(transcript);
      await submit(transcript);
    } catch (e) {
      setError(e.message);
    } finally {
      setVoiceBusy("");
    }
  };

  const speak = async () => {
    setVoiceBusy("Generating speech…");
    try {
      const { audio_base64 } = await textToSpeech(answer.text, answer.language);
      new Audio(`data:audio/wav;base64,${audio_base64}`).play();
    } catch (e) {
      // Fallback: browser speech synthesis so the demo never goes silent
      const u = new SpeechSynthesisUtterance(answer.text.replace(/\[T\d+\]/g, ""));
      u.lang = `${answer.language}-IN`;
      window.speechSynthesis.speak(u);
      setError(`${e.message} (used browser voice instead)`);
    } finally {
      setVoiceBusy("");
    }
  };

  const submit = async (q) => {
    const text = (q ?? question).trim();
    if (!field || !text || busy) return;
    setQuestion(text);
    setTrace([]);
    setAnswer(null);
    setError("");
    setBusy(true);
    abortRef.current = new AbortController();
    try {
      await askAgent(
        { ...field, question: text, crop: crop || null },
        (type, payload) => {
          if (type === "final") setAnswer(payload.answer);
          else if (type === "error") setError(payload.msg);
          else if (type !== "done") setTrace((t) => [...t, payload]);
        },
        abortRef.current.signal
      );
    } catch (e) {
      if (e.name !== "AbortError") setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card chat">
      <h2>Ask about this field</h2>
      {!field && <div className="hint">Select a field on the map first.</div>}
      <div className="row">
        <label>Crop <input value={crop} onChange={(e) => setCrop(e.target.value)} placeholder="e.g. onion" /></label>
      </div>
      <textarea
        rows={3}
        value={question}
        placeholder="Ask in any Indian language…"
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
      />
      <div className="row">
        <button className="primary" disabled={!field || busy || !question.trim()} onClick={() => submit()}>
          {busy ? "Thinking…" : "Ask"}
        </button>
        <button className={recorder.recording ? "active" : ""} disabled={!field || busy || !!voiceBusy}
                onClick={toggleMic}>
          {recorder.recording ? "⏹ Stop & send" : "🎤 Speak"}
        </button>
        {busy && <button onClick={() => abortRef.current?.abort()}>Stop</button>}
        {voiceBusy && <span className="note">{voiceBusy}</span>}
      </div>
      <div className="samples">
        {SAMPLES.map((s) => (
          <button key={s} disabled={!field || busy} onClick={() => submit(s)}>{s}</button>
        ))}
      </div>

      {trace.length > 0 && (
        <div className="trace">
          <h4>Agent trace</h4>
          <ul>{trace.map((e, i) => <TraceItem key={i} e={e} />)}</ul>
        </div>
      )}
      {error && <div className="failed">{error}</div>}
      {answer && (
        <div className="answer">
          <div className="badges">
            <span className={`badge ${answer.verified ? "ok" : "warn"}`}>
              {answer.verified ? "✓ verified against live data" : "⚠ partially verified"}
            </span>
            <span className="badge">confidence: {answer.confidence}</span>
            <button className="listen" disabled={!!voiceBusy} onClick={speak}>🔊 Listen</button>
          </div>
          <AnswerText text={answer.text} citations={answer.citations} />
          {answer.citations.length > 0 && (
            <ol className="cites">
              {answer.citations.map((c) => (
                <li key={c.id}>
                  <b>{c.id}</b> <a href={c.url} target="_blank" rel="noreferrer">{c.source}</a>
                  {" · "}{new Date(c.fetched_at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" })}
                  {c.stale && " · cached"}
                  {c.refs && (
                    <ul className="refs">
                      {c.refs.map((r, k) => (
                        <li key={k}>
                          <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a>, {r.publisher} ({r.year}), p.{r.page}
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ol>
          )}
          {answer.warnings.length > 0 && <div className="note">{answer.warnings.join(" · ")}</div>}
        </div>
      )}
    </div>
  );
}
