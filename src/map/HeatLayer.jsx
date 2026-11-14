// Hotspot heatmap: one coloured square per grid cell, for the selected dates.
// It covers the whole map (not just the selected area) so the area can be
// compared with its surroundings.
import { useEffect, useMemo } from 'react'
import { useMap } from 'react-leaflet'
import L from './leaflet.js'
import { useSelection } from '../state/selection.js'
import { useHotspots } from '../data/hotspots.js'

// dark purple -> orange -> pale yellow
const COLORS = ['#2c115f', '#721f81', '#b73779', '#f1605d', '#feb078', '#fcfdbf']

// colour for t between 0 and 1
export function heatColor(t) {
  return COLORS[Math.min(COLORS.length - 1, Math.floor(t * COLORS.length))]
}
export const HEAT_COLORS = COLORS

// Adds up `metric` ('hotspot_count' or 'frp_mw') per grid cell over the dates.
// Returns { cells: [{ lat, lon, value, estimated }], max, cellSize }
export function useHeatCells(metric) {
  const { from, to } = useSelection()
  const { records } = useHotspots()

  return useMemo(() => {
    const byId = new Map()
    for (const r of records) {
      if (r.date < from || r.date > to) continue
      let cell = byId.get(r.cell_id)
      if (!cell) byId.set(r.cell_id, (cell = { lat: r.lat, lon: r.lon, value: 0, estimated: false }))
      cell.value += r[metric]
      if (r.is_estimated) cell.estimated = true
    }
    const cells = [...byId.values()]
    const max = cells.reduce((m, c) => Math.max(m, c.value), 0)

    // work out the grid spacing from the data (smallest gap between latitudes)
    const lats = [...new Set(records.map((r) => r.lat))].sort((a, b) => a - b)
    let cellSize = 0.5
    for (let i = 1; i < lats.length; i++) {
      const gap = lats[i] - lats[i - 1]
      if (gap > 1e-6 && (i === 1 || gap < cellSize)) cellSize = gap
    }
    return { cells, max, cellSize }
  }, [records, from, to, metric])
}

export default function HeatLayer({ cells, max, cellSize, unit }) {
  const map = useMap()

  useEffect(() => {
    const renderer = L.canvas()
    const group = L.layerGroup()
    const half = cellSize / 2

    for (const c of cells) {
      // square-root scale so a few extreme cells don't hide everything else
      const t = Math.sqrt(c.value / max)
      const square = L.rectangle(
        [[c.lat - half, c.lon - half], [c.lat + half, c.lon + half]],
        { renderer, stroke: false, fillColor: heatColor(t), fillOpacity: 0.3 + 0.5 * t }
      )
      square.bindTooltip(
        `${Math.round(c.value).toLocaleString()} ${unit}${c.estimated ? ' (includes estimates)' : ''}`,
        { sticky: true }
      )
      group.addLayer(square)
    }

    group.addTo(map)
    return () => {
      map.removeLayer(group)
    }
  }, [map, cells, max, cellSize, unit])

  return null
}
