import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import ExplorePage from './pages/ExplorePage.jsx'
import AboutPage from './pages/AboutPage.jsx'
import ShareButton from './components/ShareButton.jsx'

export default function App() {
  // keep the selection (?bbox=...&from=...) when switching pages
  const { search } = useLocation()

  return (
    <div className="app">
      <header className="topbar">
        <h1>Fire Hotspot Explorer</h1>
        <span className="tagline">Harmonized MODIS + VIIRS hot spots</span>
        <nav>
          <NavLink to={{ pathname: '/', search }} end>
            Explore
          </NavLink>
          <NavLink to={{ pathname: '/about', search }}>About</NavLink>
        </nav>
        <ShareButton />
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<ExplorePage />} />
          <Route path="/about" element={<AboutPage />} />
        </Routes>
      </main>
    </div>
  )
}
