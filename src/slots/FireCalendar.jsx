// SLOT: fire calendar + sound toggle. Owner: Mridul.
//
// Replace the placeholder below with your component. Everything you need:
//   const { area, bounds, from, to } = useSelection()          // what the user picked
//   const { records, loading } = useHotspots()                 // all hotspot rows
//   const mine = filterRecords(records, area)                  // rows in the area, all dates
// See DATA_FORMAT.md for the fields on each record.

import { useState } from 'react'
import { useSelection } from '../state/selection.js'

export default function FireCalendar() {
  const { from, to } = useSelection()
  const [soundOn, setSoundOn] = useState(false)

  return (
    <section className="card">
      <h2>Fire calendar</h2>
      <p className="slot-note">
        Placeholder (Mridul). Calendar for {from} to {to} goes here.
      </p>
      <label className="toggle">
        <input type="checkbox" checked={soundOn} onChange={(e) => setSoundOn(e.target.checked)} />
        Sound {soundOn ? 'on' : 'off'}
      </label>
    </section>
  )
}
