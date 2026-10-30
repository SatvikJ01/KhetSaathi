import { useState } from "react";
import L from "leaflet";
import { CircleMarker, MapContainer, Polygon, Polyline, TileLayer, useMapEvents } from "react-leaflet";

const SATELLITE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

function centroid(points) {
  const lat = points.reduce((s, p) => s + p[0], 0) / points.length;
  const lon = points.reduce((s, p) => s + p[1], 0) / points.length;
  return [lat, lon];
}

function ClickHandler({ mode, onPin, onVertex }) {
  useMapEvents({
    click(e) {
      const pt = [e.latlng.lat, e.latlng.lng];
      if (mode === "draw") onVertex(pt);
      else onPin(pt);
    },
  });
  return null;
}

// Click to drop a pin, or switch to "Draw field" and click the plot corners.
export default function FieldMap({ center, field, onFieldChange, onDemo }) {
  const [mode, setMode] = useState("pin");
  const [vertices, setVertices] = useState([]);

  const finishPolygon = () => {
    if (vertices.length < 3) return;
    const [lat, lon] = centroid(vertices);
    onFieldChange({ lat, lon, polygon: vertices.map(([a, b]) => [b, a]) }); // GeoJSON order
    setVertices([]);
    setMode("pin");
  };

  const locateMe = () => {
    navigator.geolocation?.getCurrentPosition(
      (pos) => onFieldChange({ lat: pos.coords.latitude, lon: pos.coords.longitude, polygon: null }),
      () => alert("Location permission denied")
    );
  };

  const drawn = field?.polygon?.map(([lon, lat]) => [lat, lon]);

  return (
    <div className="card map-card">
      <div className="map-toolbar">
        <button className={mode === "pin" ? "active" : ""} onClick={() => { setMode("pin"); setVertices([]); }}>
          📍 Drop pin
        </button>
        <button className={mode === "draw" ? "active" : ""} onClick={() => { setMode("draw"); setVertices([]); }}>
          ✏️ Draw field
        </button>
        {mode === "draw" && (
          <>
            <button disabled={vertices.length < 3} onClick={finishPolygon}>✅ Finish ({vertices.length} pts)</button>
            <button onClick={() => setVertices([])}>Clear</button>
          </>
        )}
        <button onClick={locateMe}>🎯 My location</button>
        <button onClick={onDemo}>🧅 Demo: Lasalgaon onion field</button>
        {field && <span className="coords">{field.lat.toFixed(5)}, {field.lon.toFixed(5)}</span>}
      </div>
      <MapContainer center={center} zoom={14} className="map" scrollWheelZoom>
        <TileLayer url={SATELLITE} attribution="Imagery © Esri, Maxar, Earthstar Geographics" maxZoom={19} />
        <ClickHandler
          mode={mode}
          onPin={([lat, lon]) => onFieldChange({ lat, lon, polygon: null })}
          onVertex={(pt) => setVertices((v) => [...v, pt])}
        />
        {vertices.length > 0 && <Polyline positions={vertices} pathOptions={{ color: "#facc15", dashArray: "4" }} />}
        {drawn && <Polygon positions={drawn} pathOptions={{ color: "#22c55e", weight: 2 }} />}
        {field && !drawn && (
          <CircleMarker center={L.latLng(field.lat, field.lon)} radius={8} pathOptions={{ color: "#22c55e" }} />
        )}
      </MapContainer>
    </div>
  );
}
