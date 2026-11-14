// Main page: map on the left, side panel on the right.
import SelectionControls from '../components/SelectionControls.jsx'
import { useSelection } from '../state/selection.js'

export default function ExplorePage() {
  const { bounds, from, to } = useSelection()

  return (
    <div className="explore">
      <div className="map-area">
        <div className="placeholder">
          Map goes here.
          <br />
          Area {bounds.join(', ')} | {from} to {to}
        </div>
      </div>
      <aside className="side-panel">
        <SelectionControls />
      </aside>
    </div>
  )
}
