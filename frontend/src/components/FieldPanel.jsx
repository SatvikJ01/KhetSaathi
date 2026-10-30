import {
  Bar, CartesianGrid, ComposedChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

function Source({ r }) {
  if (!r) return null;
  const when = r.fetched_at ? new Date(r.fetched_at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" }) : "";
  return (
    <div className="source">
      {r.ok ? "✓" : "✗"} <a href={r.url} target="_blank" rel="noreferrer">{r.source}</a>
      {when && ` · ${when}`}
      {r.stale && <span className="stale"> · cached copy (live call failed)</span>}
    </div>
  );
}

function Failed({ r }) {
  return <div className="failed">Unavailable right now: {r?.error || "unknown error"}</div>;
}

function NdviCard({ r }) {
  const s = r?.data?.summary;
  return (
    <div className="card">
      <h3>🛰️ Crop vigour (Sentinel-2 NDVI)</h3>
      {!r?.ok ? <Failed r={r} /> : (
        <>
          <div className="stats">
            <div><b>{s.latest_ndvi}</b><span>latest NDVI ({s.latest_date})</span></div>
            {s.trend && <div><b>{s.trend}</b><span>trend ({s.trend_per_10_days}/10 days)</span></div>}
            <div><b>{s.stage_hint}</b><span>canopy reading</span></div>
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <LineChart data={r.data.series}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} />
              <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} width={30} />
              <Tooltip />
              <Line dataKey="ndvi_mean" stroke="#16a34a" strokeWidth={2} dot />
            </LineChart>
          </ResponsiveContainer>
          {r.data.cloudy_scenes_skipped?.length > 0 && (
            <div className="note">Skipped cloudy passes: {r.data.cloudy_scenes_skipped.join(", ")}</div>
          )}
        </>
      )}
      <Source r={r} />
    </div>
  );
}

function WeatherCard({ r }) {
  const s = r?.data?.summary;
  return (
    <div className="card">
      <h3>🌦️ Forecast (16 days)</h3>
      {!r?.ok ? <Failed r={r} /> : (
        <>
          <div className="stats">
            <div><b>{s.next7_rain_total_mm} mm</b><span>rain next 7 days</span></div>
            <div><b>{s.next7_max_tmax_c}°C</b><span>max temp next 7 days</span></div>
            <div><b>{s.dry_days_next7}</b><span>dry days next 7</span></div>
          </div>
          <ResponsiveContainer width="100%" height={150}>
            <ComposedChart data={r.data.daily}>
              <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} />
              <YAxis yAxisId="r" tick={{ fontSize: 10 }} width={30} />
              <YAxis yAxisId="t" orientation="right" tick={{ fontSize: 10 }} width={30} />
              <Tooltip />
              <Bar yAxisId="r" dataKey="rain_mm" fill="#3b82f6" name="rain mm" />
              <Line yAxisId="t" dataKey="tmax_c" stroke="#ef4444" name="max °C" dot={false} />
            </ComposedChart>
          </ResponsiveContainer>
          {r.data.alerts.length > 0 && (
            <ul className="alerts">
              {r.data.alerts.map((a, i) => (
                <li key={i}>⚠️ {a.date}: {a.type.replace("_", " ")} ({a.value_mm ?? a.value_c ?? a.value_kmh})</li>
              ))}
            </ul>
          )}
        </>
      )}
      <Source r={r} />
    </div>
  );
}

function ClimateCard({ r }) {
  const d = r?.data;
  return (
    <div className="card">
      <h3>📉 Last 30 days vs normal</h3>
      {!r?.ok ? <Failed r={r} /> : (
        <div className="stats">
          <div><b>{d.rain_observed_mm} mm</b><span>rain observed (normal {d.rain_normal_mm})</span></div>
          <div><b>{d.rain_category}</b><span>{d.rain_pct_of_normal ?? "–"}% of normal</span></div>
          <div><b>{d.tmax_departure_c > 0 ? "+" : ""}{d.tmax_departure_c}°C</b><span>max temp vs normal</span></div>
        </div>
      )}
      <Source r={r} />
    </div>
  );
}

function SoilCard({ r }) {
  const top = r?.data?.by_depth?.["0-5cm"];
  return (
    <div className="card">
      <h3>🟫 Soil at this spot</h3>
      {!r?.ok ? <Failed r={r} /> : (
        <>
          <div className="stats">
            <div><b>{top.ph}</b><span>pH</span></div>
            <div><b>{top.organic_carbon} g/kg</b><span>organic carbon</span></div>
            <div><b>{r.data.texture_0_5cm}</b><span>texture</span></div>
          </div>
          <div className="note">{r.data.resolution}{r.data.notes.length ? ` · ${r.data.notes.join(", ")}` : ""}</div>
        </>
      )}
      <Source r={r} />
    </div>
  );
}

function Pending({ title, msg }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="hint">⏳ {msg}</div>
    </div>
  );
}

export default function FieldPanel({ overview, hasField }) {
  if (!hasField) return <div className="card hint">Tap your field on the map (or draw its boundary) to load live data.</div>;
  const loc = overview.location;
  return (
    <div className="panel">
      {loc?.ok && <div className="place">📍 {loc.data.village || ""} {loc.data.district}, {loc.data.state}</div>}
      <div className="grid">
        {overview.crop_ndvi ? <NdviCard r={overview.crop_ndvi} />
          : <Pending title="🛰️ Crop vigour (Sentinel-2 NDVI)" msg="Reading cloud-free satellite passes for this field…" />}
        {overview.weather_forecast ? <WeatherCard r={overview.weather_forecast} />
          : <Pending title="🌦️ Forecast (16 days)" msg="Fetching forecast…" />}
        {overview.climate_anomaly ? <ClimateCard r={overview.climate_anomaly} />
          : <Pending title="📉 Last 30 days vs normal" msg="Comparing with 30-year normal…" />}
        {overview.soil_profile ? <SoilCard r={overview.soil_profile} />
          : <Pending title="🟫 Soil at this spot" msg="Querying soil map…" />}
      </div>
    </div>
  );
}
