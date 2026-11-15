// Settings for the whole app live here, so teammates can change things
// without digging through the other files.

export const CONFIG = {
  // Show the "Demo data" tag in the header. Set to false once real data is in.

  // What the record is, for the "Record" fact. hotspots.csv is real MODIS observations
  // (NASA FIRMS, global, 2006 to 2026) until the harmonized MODIS + VIIRS record replaces it.
  observedLabel: 'MODIS observations',

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
  // 2 degrees keeps the global 21-year file small enough to load in a browser (~17 MB).
  cellSize: 2,

  // The area selected when the page first opens: [south, west, north, east]
  defaultArea: [55, -119, 59.5, -110],

  // Quick picks in the "Area" menu: [south, west, north, east]
  presets: [
    { name: 'Northern Alberta', box: [55, -119, 59.5, -110] },
    { name: 'BC Interior', box: [49.5, -125, 54, -118.5] },
    { name: 'Canadian boreal forest', box: [52, -125, 62, -95] },
    { name: 'California', box: [32, -124, 42, -114] },
    { name: 'Amazon arc of deforestation', box: [-13, -65, -5, -45] },
    { name: 'Central African savanna', box: [0, 10, 12, 35] },
    { name: 'Southern African savanna', box: [-20, 15, -5, 35] },
    { name: 'Punjab and Haryana, India', box: [28, 73, 32, 78] },
    { name: 'Siberian taiga', box: [55, 95, 68, 135] },
    { name: 'Northern Australia', box: [-18, 125, -11, 145] },
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
