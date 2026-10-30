import { useRef, useState } from "react";

const MAX_MS = 28_000; // Sarvam's synchronous STT is meant for clips under 30 s

// Minimal MediaRecorder hook: start() asks for the mic, stop() resolves to an audio Blob.
export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const rec = useRef(null);

  const start = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mr = new MediaRecorder(stream);
    const chunks = [];
    mr.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mr.donePromise = new Promise((resolve) => {
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        resolve(new Blob(chunks, { type: mr.mimeType || "audio/webm" }));
      };
    });
    mr.timer = setTimeout(() => mr.state === "recording" && mr.stop(), MAX_MS);
    mr.start();
    rec.current = mr;
    setRecording(true);
  };

  const stop = async () => {
    const mr = rec.current;
    if (!mr) return null;
    clearTimeout(mr.timer);
    if (mr.state === "recording") mr.stop();
    setRecording(false);
    rec.current = null;
    return mr.donePromise;
  };

  return { recording, start, stop };
}
