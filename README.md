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

## Hear the Fire Season (Mridul)

Select **Listen** above the calendar to open the integrated panel. Choose a listening
 year, play activity or anomaly tones, enable spoken month descriptions, or hear a
season summary. Playback selects the same month on the calendar, map, verdict and
season chart. Changing the area, year range or measure stops prior playback.
The listening mode and calendar colour mode stay synchronized.

Summary speech uses the selected speed and a fixed half-second gap after periods
and semicolons. The sound guide includes named examples; the guided tutorial is
not included in this version. All current repository data remains demo data.

`js/listening-model.js` adapts the shared calendar table. Missing months and areas
without grid cells are unavailable, not zero. Within covered areas, the upstream
CSV loader currently assumes omitted rows mean zero; it does not provide finer
observation-coverage flags. Comparisons need at least two years and nonzero
variation. Anomaly shortcuts use the existing two-sigma unusual threshold.
FRP audio descriptions use megawatts of summed fire radiative power, not energy.

Checks: `node --test tests/listening.test.mjs`.

### Microphone commands

The **Speak** microphone button beside Listen accepts one English command per click:

- `2024 Activity` (or just `2024`): January–December activity playback.
- `2024 Unusual Activity`: January–December anomaly playback.
- `April 2024 Activity` / `April 2024 Unusual Activity`: only that month.
- `Slow` / `Normal` / `Fast`: 0.75× / 1× / 1.5×.
- `2024 Season Overview`: the existing spoken summary with half-second punctuation gaps.
- `2024 Strongest Ano`: select the strongest qualifying anomaly and play its spoken description followed by anomaly tones, once. No qualifying anomaly gets a spoken explanation.
- `Leave`: cancel the microphone action without playing data.
- `Keyword Guide`: open the guide inside Listen and read every keyword and its short purpose in order.
- `2024 Fire Energy` / `2024 Fire Energy Unusual`: full-year FRP activity or anomaly playback.
- `April 2024 Fire Energy` / `April 2024 Fire Energy Unusual`: one month of FRP activity or anomaly playback.
- `Sound Guide`: speak each example's name and play its pattern sequentially, in guide order.

Replace the year/month with any supported value within the selected year range.
Commands use the current geographic area. Activity commands select hotspots; Fire Energy commands select FRP. Overview and Strongest Ano use the current measure. Voice requests enable spoken
month descriptions and unmute audio; zero volume is restored to 60%. Unknown
commands say exactly “Try again with right keyword”. A new microphone click stops
previous playback. There are no voice replay/back/forward commands.

Uses the browser Web Speech API, with an explicit microphone-permission prompt.
Speech recognition may use the browser provider's online service. There is no
application recording storage. Unsupported browsers keep regular Listen controls.
Permission, device and network failures get distinct explanations.

Run `node --test tests/*.mjs`. Automated tests exercise command parsing, mocked
recognition lifecycle and audio dispatch; real microphone recognition and audio
quality require a manual browser check with the user's microphone.
