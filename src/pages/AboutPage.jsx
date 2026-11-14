export default function AboutPage() {
  return (
    <div className="about">
      <h2>About</h2>
      <p>
        Fire Hotspot Explorer shows a harmonized record of MODIS and VIIRS active-fire hot spots. From 2012
        onward the values are real VIIRS observations; before 2012 they are model estimates derived from MODIS,
        and are marked as estimated wherever they appear.
      </p>
      <p>Built by team Syntax TERROR for NASA Space Apps 2026.</p>
    </div>
  )
}
