import { useCallback, useRef, useState } from "react";
import { fetchFieldCards } from "./api.js";
import FieldMap from "./components/FieldMap.jsx";
import FieldPanel from "./components/FieldPanel.jsx";
import Chat from "./components/Chat.jsx";

// Demo field: Kotamgaon, ~3 km from Lasalgaon APMC (Asia's largest onion market), Niphad, Nashik.
// Chosen because Sentinel-2 shows a clean maturing-crop NDVI curve there in Oct 2026.
const DEMO_FIELD = { lat: 20.145, lon: 74.205, polygon: null };
const DEFAULT_CENTER = [DEMO_FIELD.lat, DEMO_FIELD.lon];

export default function App() {
  const [field, setField] = useState(null); // {lat, lon, polygon}
  const [overview, setOverview] = useState({}); // tool -> result, filled as each arrives
  const requestId = useRef(0);

  const onFieldChange = useCallback((next) => {
    const id = ++requestId.current; // ignore late results from a previously selected field
    setField(next);
    setOverview({});
    fetchFieldCards(next, (tool, result) => {
      if (id === requestId.current) setOverview((o) => ({ ...o, [tool]: result }));
    });
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">🌾</span> KhetSaathi
          <span className="tag">field-level farm copilot · live satellite, weather, soil & mandi data</span>
        </div>
      </header>
      <main className="layout">
        <section className="left">
          <FieldMap center={DEFAULT_CENTER} field={field} onFieldChange={onFieldChange}
                    onDemo={() => onFieldChange(DEMO_FIELD)} />
          <FieldPanel overview={overview} hasField={!!field} />
        </section>
        <section className="right">
          <Chat field={field} />
        </section>
      </main>
    </div>
  );
}
