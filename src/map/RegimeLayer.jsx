// Fire regime zones from public/data/fire_regimes.geojson (see DATA_FORMAT.md).
import { useEffect, useState } from 'react'
import { GeoJSON } from 'react-leaflet'

const URL = `${import.meta.env.BASE_URL}data/fire_regimes.geojson`
const FALLBACK_COLORS = ['#f6c85f', '#f28e2b', '#e15759', '#b07aa1', '#76b7b2', '#59a14f', '#9c755f']

// Loads the GeoJSON and returns { geojson, regimes: [{ id, name, color }] }
export function useRegimes() {
  const [state, setState] = useState({ geojson: null, regimes: [] })

  useEffect(() => {
    fetch(URL)
      .then((res) => res.json())
      .then((geojson) => {
        // one legend entry per regime; use the file's colour or pick one
        const byId = new Map()
        for (const feature of geojson.features) {
          const p = feature.properties
          if (!byId.has(p.regime_id)) {
            const color = p.color || FALLBACK_COLORS[byId.size % FALLBACK_COLORS.length]
            byId.set(p.regime_id, { id: p.regime_id, name: p.regime_name, color })
          }
        }
        setState({ geojson, regimes: [...byId.values()] })
      })
      .catch((err) => console.error('Could not load fire_regimes.geojson', err))
  }, [])

  return state
}

export default function RegimeLayer({ geojson, regimes }) {
  if (!geojson) return null
  const colorOf = (feature) => regimes.find((r) => r.id === feature.properties.regime_id)?.color

  return (
    <GeoJSON
      data={geojson}
      style={(feature) => ({ color: colorOf(feature), weight: 2, fillOpacity: 0.18 })}
      onEachFeature={(feature, layer) => layer.bindTooltip(feature.properties.regime_name, { sticky: true })}
    />
  )
}
