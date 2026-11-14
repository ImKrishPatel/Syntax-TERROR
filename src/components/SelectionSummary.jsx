// Totals for the selected area and dates.
import { useMemo } from 'react'
import { useSelection } from '../state/selection.js'
import { useHotspots, filterRecords } from '../data/hotspots.js'

export default function SelectionSummary() {
  const { area, from, to } = useSelection()
  const { records, loading, error } = useHotspots()

  const totals = useMemo(() => {
    const rows = filterRecords(records, area, from, to)
    return {
      hotspots: rows.reduce((sum, r) => sum + r.hotspot_count, 0),
      frp: rows.reduce((sum, r) => sum + r.frp_mw, 0),
      anyEstimated: rows.some((r) => r.is_estimated),
    }
  }, [records, area, from, to])

  if (loading) return <section className="card">Loading hotspot data...</section>
  if (error) return <section className="card error">{error.message}</section>

  return (
    <section className="card">
      <h2>In this selection</h2>
      <div className="stats">
        <div>
          <strong>{totals.hotspots.toLocaleString()}</strong>
          <span>hotspots</span>
        </div>
        <div>
          <strong>{Math.round(totals.frp / 1000).toLocaleString()} GW</strong>
          <span>total fire radiative power</span>
        </div>
      </div>
      {totals.anyEstimated && (
        <p className="hint">Includes model estimates: values before 2012 are estimated, not observed by VIIRS.</p>
      )}
    </section>
  )
}
