# Syntax TERROR: Fire Hotspot Explorer

NASA Space Apps Challenge 2026: Harmonization of MODIS and VIIRS Hot Spots.

We're building a web app that combines MODIS and VIIRS fire hotspot data into one
consistent record, then shows it as a burning activity calendar. Pick an area and a
date range to see which months and years had the most fire, and how the current
month compares with past years.

## Run the app on your computer

The app is plain HTML, CSS and JavaScript. There is nothing to install or build.
It only needs a small local web server, because browsers will not load data files
from a page opened by double-clicking.

```bash
python -m http.server 8000
```

Then open http://localhost:8000 in your browser. Press Ctrl+C in the terminal to stop it.

(In VS Code you can also right-click `index.html` and choose "Open with Live Server".)

## What is in the app

- **Map**: pick an area from the menu, draw a box, drag its corners, or type the coordinates.
- **Verdict**: one plain sentence about the selected area or month, with an anomaly score.
- **Burning calendar**: years down, months across. Dots mark unusual months, hatching marks
  years that are estimated from MODIS. Select a square to see that month on the map.
- **Listen**: plays the calendar as sound.
- **Fire regimes, forecast, validation**: panels that read the teammates' output files.
- **Export CSV** of the hotspots in the current search, and **Copy link** to share the exact view.

## Where things are

| path | what it is |
| --- | --- |
| `index.html` | the page |
| `css/style.css` | all the styling |
| `js/config.js` | settings: file paths, default area, area menu, demo tag |
| `js/app.js` | the main file: keeps track of what is selected and redraws the page |
| `js/data.js` | loads `hotspots.csv` and adds up hotspots for an area |
| `js/stats.js` | usual values, anomaly score, the verdict sentence |
| `js/map.js` | the map, the area box, the regime layer |
| `js/calendar.js` | the burning calendar |
| `js/charts.js` | the line charts |
| `js/sound.js` | the "Listen" button |
| `js/colors.js` | colour ramps |
| `js/tooltip.js` | the small box that appears when you hover |
| `public/data/` | data files. Format: [DATA_FORMAT.md](DATA_FORMAT.md) |
| `public/basemap/` | coastlines, borders, lakes and towns for the map |
| `vendor/` | Leaflet and topojson-client, copied in so the app works offline |
| `scripts/make-mock-data.mjs` | makes the demo data |
| `analysis/` | Python analysis code (fire regimes) |

## Demo data

The numbers in `public/data/` are made up so the app has something to show. To remake them:

```bash
node scripts/make-mock-data.mjs
python -m analysis.fire_regimes.run
```

To use real data, replace the files (see [DATA_FORMAT.md](DATA_FORMAT.md)) and set
`isDemoData: false` in `js/config.js`.

## Credits

- Fire data: NASA FIRMS (MODIS on Terra and Aqua; VIIRS on S-NPP, NOAA-20, NOAA-21)
- Basemap: Natural Earth (public domain)
- Map library: Leaflet (BSD 2-Clause)
- topojson-client (ISC)
- Typeface: Archivo (SIL Open Font License), loaded from Google Fonts

## Team git rules

- Work on your own branch.
- Before pushing to `main`, pull `main` and merge it into your branch first.
- Never force-push.
