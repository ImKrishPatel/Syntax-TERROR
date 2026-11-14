// The map: basemap, selected-area outline, drawing tools and data layers.
import { useEffect, useState } from 'react'
import { MapContainer, TileLayer, Rectangle, Polygon, useMap } from 'react-leaflet'
import './leaflet.js'
import { useSelection } from '../state/selection.js'
import DrawControl from './DrawControl.jsx'
import HeatLayer, { useHeatCells, HEAT_COLORS } from './HeatLayer.jsx'

const AREA_STYLE = { color: '#ff7a1a', weight: 2, fill: false }
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas'

// what the heatmap can show: value = column in the data, unit = label
const METRICS = {
  hotspot_count: { label: 'Hotspot count', unit: 'hotspots' },
  frp_mw: { label: 'Fire radiative power', unit: 'MW' },
}

// [west, south, east, north] -> Leaflet's [[south, west], [north, east]]
const toLeafletBounds = ([w, s, e, n]) => [[s, w], [n, e]]

// Zooms the map to the selected area whenever it changes.
function FitToArea() {
  const map = useMap()
  const { bounds } = useSelection()
  useEffect(() => {
    map.fitBounds(toLeafletBounds(bounds), { padding: [40, 40] })
  }, [map, bounds])
  return null
}

export default function MapView() {
  const { area, bounds } = useSelection()
  const [showHeat, setShowHeat] = useState(true)
  const [metric, setMetric] = useState('hotspot_count')
  const heat = useHeatCells(metric)
  const unit = METRICS[metric].unit

  return (
    <div className="map-wrap">
      <MapContainer bounds={toLeafletBounds(bounds)} className="map" worldCopyJump>
        <TileLayer
          url={`${ESRI}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`}
          attribution="Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors"
          maxZoom={16}
        />

        {showHeat && <HeatLayer cells={heat.cells} max={heat.max} cellSize={heat.cellSize} unit={unit} />}

        {/* place names drawn on top of the data */}
        <TileLayer url={`${ESRI}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`} maxZoom={16} />

        {area.type === 'polygon' ? (
          <Polygon positions={area.points} pathOptions={AREA_STYLE} interactive={false} />
        ) : (
          <Rectangle bounds={toLeafletBounds(area.bounds)} pathOptions={AREA_STYLE} interactive={false} />
        )}

        <DrawControl />
        <FitToArea />
      </MapContainer>

      {/* layer switches, top-right of the map */}
      <div className="layer-panel">
        <label className="toggle">
          <input type="checkbox" checked={showHeat} onChange={(e) => setShowHeat(e.target.checked)} />
          Hotspot heatmap
        </label>
        {showHeat && (
          <>
            <select value={metric} onChange={(e) => setMetric(e.target.value)}>
              {Object.entries(METRICS).map(([key, m]) => (
                <option key={key} value={key}>
                  {m.label}
                </option>
              ))}
            </select>
            <div className="legend">
              <div className="legend-bar">
                {HEAT_COLORS.map((color) => (
                  <span key={color} style={{ background: color }} />
                ))}
              </div>
              <div className="legend-ends">
                <span>0</span>
                <span>
                  {Math.round(heat.max).toLocaleString()} {unit} per cell
                </span>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
