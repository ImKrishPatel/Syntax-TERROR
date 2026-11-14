// Main page: map on the left, side panel on the right.
// To add a panel, import your component and put it in the <aside> below.
import SelectionControls from '../components/SelectionControls.jsx'
import SelectionSummary from '../components/SelectionSummary.jsx'
import FireCalendar from '../slots/FireCalendar.jsx'
import ForecastChart from '../slots/ForecastChart.jsx'

export default function ExplorePage() {
  return (
    <div className="explore">
      <div className="map-area">
        <div className="placeholder">Map goes here.</div>
      </div>
      <aside className="side-panel">
        <SelectionControls />
        <SelectionSummary />
        <FireCalendar />
        <ForecastChart />
      </aside>
    </div>
  )
}
