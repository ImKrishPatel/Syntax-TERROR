// Type-in controls for the area (lat/lon box) and the date range.
import { useEffect, useState } from 'react'
import { useSelection } from '../state/selection.js'

const LABELS = ['West (lon)', 'South (lat)', 'East (lon)', 'North (lat)']

export default function SelectionControls() {
  const { area, bounds, from, to, setArea, setDates } = useSelection()
  const [box, setBox] = useState(bounds.map(String))
  const [error, setError] = useState('')

  // keep the inputs in sync when the area changes elsewhere (map drawing, URL)
  useEffect(() => {
    setBox(bounds.map(String))
    setError('')
  }, [bounds])

  const setField = (i, value) => setBox(box.map((v, j) => (j === i ? value : v)))

  function applyBox(e) {
    e.preventDefault()
    if (box.some((v) => v.trim() === '' || !Number.isFinite(Number(v)))) return setError('Enter four numbers.')
    const [w, s, east, n] = box.map(Number)
    if (s < -90 || n > 90 || w < -180 || east > 180) return setError('Lat must be -90..90, lon -180..180.')
    if (s >= n || w >= east) return setError('South must be below north, west left of east.')
    setError('')
    setArea({ type: 'bbox', bounds: [w, s, east, n] })
  }

  return (
    <section className="card">
      <h2>Area and dates</h2>

      <form className="bbox-form" onSubmit={applyBox}>
        {LABELS.map((label, i) => (
          <label key={label}>
            {label}
            <input type="number" step="any" value={box[i]} onChange={(e) => setField(i, e.target.value)} />
          </label>
        ))}
        <button type="submit">Apply area</button>
      </form>
      {error && <p className="error">{error}</p>}
      {area.type === 'polygon' && (
        <p className="hint">A drawn polygon is selected; the box above is its outer bounds.</p>
      )}

      <div className="date-row">
        <label>
          From
          <input type="date" value={from} max={to} onChange={(e) => e.target.value && setDates(e.target.value, to)} />
        </label>
        <label>
          To
          <input type="date" value={to} min={from} onChange={(e) => e.target.value && setDates(from, e.target.value)} />
        </label>
      </div>
    </section>
  )
}
