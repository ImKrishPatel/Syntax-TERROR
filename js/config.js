// Settings for the whole app live here, so teammates can change things
// without digging through the other files.

export const CONFIG = {
  // Show the "Demo data" tag in the header. Set to false once real data is in.
  isDemoData: true,

  // Where the data files are (see DATA_FORMAT.md for what goes in each one)
  files: {
    hotspots: 'public/data/hotspots.csv',
    regimes: 'public/data/fire_regimes.geojson',
    regimeSummary: 'public/data/regime_summary.json',
    forecast: 'public/data/forecast.json',
    validation: 'public/data/validation.json',
    basemap: 'public/basemap/world.json',
  },

  // Size of one grid cell in degrees. Must match hotspots.csv.
  cellSize: 0.5,

  // The area selected when the page first opens: [south, west, north, east]
  defaultArea: [55, -119, 59.5, -110],

  // Quick picks in the "Area" menu: [south, west, north, east]
  presets: [
    { name: 'Northern Alberta', box: [55, -119, 59.5, -110] },
    { name: 'Fort McMurray region', box: [55.5, -113, 58, -109.5] },
    { name: 'BC Interior', box: [49.5, -125, 54, -118.5] },
    { name: 'Great Slave Lake, NWT', box: [59.5, -121, 63.5, -110] },
    { name: 'Northern Saskatchewan', box: [54, -109.5, 58.5, -101.5] },
    { name: 'Prairie cropland', box: [49, -114, 53, -98] },
    { name: 'Jasper and the Rockies', box: [51.5, -119.5, 54, -116] },
  ],

  // A month counts as "unusual" when its score is at least this many sigma
  unusualSigma: 2,

  // Extra town labels for the map (the basemap only has the bigger cities)
  extraPlaces: [
    ['Prince George', 53.92, -122.75], ['Kamloops', 50.67, -120.33], ['Kelowna', 49.89, -119.5],
    ['Grande Prairie', 55.17, -118.79], ['Jasper', 52.87, -118.08], ['Slave Lake', 55.28, -114.77],
    ['High Level', 58.52, -117.14], ['Fort Nelson', 58.81, -122.7], ['Hay River', 60.82, -115.8],
    ['La Ronge', 55.1, -105.28], ['Lytton', 50.23, -121.58], ['Flin Flon', 54.77, -101.88],
    ['Prince Albert', 53.2, -105.75], ['Lethbridge', 49.69, -112.84], ['Red Deer', 52.27, -113.81],
  ],
}

export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December']
export const MONTHS_SHORT = MONTHS.map(m => m.slice(0, 3))
