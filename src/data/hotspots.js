// Loads the harmonized hotspot data (public/data/hotspots.csv, see DATA_FORMAT.md).
//
// In a component:
//   const { records, loading, error } = useHotspots()
//   const { area, from, to } = useSelection()
//   const mine = filterRecords(records, area, from, to)
//
// Each record:
//   { cell_id, lat, lon, date: 'YYYY-MM-DD', hotspot_count, frp_mw, is_estimated }

import { useEffect, useState } from 'react'
import { areaContains } from '../state/selection.js'

const URL = `${import.meta.env.BASE_URL}data/hotspots.csv`

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/)
  const header = lines[0].split(',').map((h) => h.trim())
  const col = (name) => {
    const i = header.indexOf(name)
    if (i === -1) throw new Error(`hotspots.csv is missing the "${name}" column`)
    return i
  }
  const [iId, iLat, iLon, iDate, iCount, iFrp, iEst] = [
    'cell_id', 'lat', 'lon', 'date', 'hotspot_count', 'frp_mw', 'is_estimated',
  ].map(col)

  return lines.slice(1).map((line) => {
    const c = line.split(',')
    return {
      cell_id: c[iId],
      lat: Number(c[iLat]),
      lon: Number(c[iLon]),
      date: c[iDate],
      hotspot_count: Number(c[iCount]),
      frp_mw: Number(c[iFrp]),
      is_estimated: /^(true|1)$/i.test(c[iEst].trim()),
    }
  })
}

// the file is fetched once and shared by every component
let cache = null
function loadHotspots() {
  if (!cache) {
    cache = fetch(URL)
      .then((res) => {
        if (!res.ok) throw new Error(`Could not load hotspots.csv (${res.status})`)
        return res.text()
      })
      .then(parseCsv)
  }
  return cache
}

export function useHotspots() {
  const [state, setState] = useState({ records: [], loading: true, error: null })
  useEffect(() => {
    let alive = true
    loadHotspots()
      .then((records) => alive && setState({ records, loading: false, error: null }))
      .catch((error) => alive && setState({ records: [], loading: false, error }))
    return () => {
      alive = false
    }
  }, [])
  return state
}

// Records inside the area. Pass from/to ('YYYY-MM-DD') to also filter by date.
export function filterRecords(records, area, from, to) {
  return records.filter(
    (r) => (!from || r.date >= from) && (!to || r.date <= to) && areaContains(area, r.lat, r.lon)
  )
}
