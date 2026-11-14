// SLOT: forecast chart. Owner: Snavya.
//
// Replace the placeholder below with your component. Everything you need:
//   const { area, bounds, from, to } = useSelection()          // what the user picked
//   const { records, loading } = useHotspots()                 // all hotspot rows
//   const mine = filterRecords(records, area)                  // rows in the area, all dates
// See DATA_FORMAT.md for the fields on each record.

import { useSelection } from '../state/selection.js'

export default function ForecastChart() {
  const { bounds } = useSelection()

  return (
    <section className="card">
      <h2>Forecast</h2>
      <p className="slot-note">
        Placeholder (Snavya). Forecast chart for area {bounds.join(', ')} goes here.
      </p>
    </section>
  )
}
