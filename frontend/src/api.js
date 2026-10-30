// Backend client. In dev, Vite proxies /api to localhost:8000; in prod set VITE_API_BASE.
const BASE = import.meta.env.VITE_API_BASE || "";

export const FIELD_TOOLS = ["location", "weather_forecast", "climate_anomaly", "soil_profile", "crop_ndvi"];

// Fetch each dashboard card independently; onResult(tool, result) fires as each one lands.
export function fetchFieldCards(field, onResult) {
  return Promise.all(
    FIELD_TOOLS.map(async (tool) => {
      try {
        const res = await fetch(`${BASE}/api/field/${tool}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(field),
        });
        onResult(tool, res.ok ? await res.json() : { ok: false, error: `HTTP ${res.status}` });
      } catch (e) {
        onResult(tool, { ok: false, error: e.message });
      }
    })
  );
}

async function jsonOrThrow(res, what) {
  if (res.ok) return res.json();
  let detail = "";
  try { detail = (await res.json()).detail; } catch { /* non-JSON error body */ }
  throw new Error(`${what} failed: ${detail || `HTTP ${res.status}`}`);
}

export async function speechToText(blob) {
  const form = new FormData();
  form.append("audio", blob, "voice.webm");
  return jsonOrThrow(await fetch(`${BASE}/api/voice/stt`, { method: "POST", body: form }), "Transcription");
}

export async function textToSpeech(text, language) {
  const res = await fetch(`${BASE}/api/voice/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, language }),
  });
  return jsonOrThrow(res, "Speech");
}

// POST + Server-Sent Events: calls onEvent(type, payload) for each trace event.
export async function askAgent(body, onEvent, signal) {
  const res = await fetch(`${BASE}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`Agent request failed: HTTP ${res.status} ${detail.slice(0, 200)}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop();
    for (const frame of frames) {
      let type = "message";
      let data = "";
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("event:")) type = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) onEvent(type, JSON.parse(data));
    }
  }
}
