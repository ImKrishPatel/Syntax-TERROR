# Syntax TERROR: Ember

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

- **Home**: search any place (or type coordinates), pick a custom area, and keep favourites.
  Dark mode, colours for colour-blind viewers and spoken descriptions are switched on here.
- **Map**: map, satellite, terrain and a NASA burn view. The hotspot grid sits on top. Click the map
  to pick a place, drag the box to move it, drag a corner to resize it, or Shift + drag to draw one.
- **Trends**: one plain sentence about the area, a year against a usual year, and the burning
  calendar (years down, months across). Select a square to see that month on the map.
- **Anomalies**: the months that were furthest from normal.
- **Patterns**: the fire regimes in the area.
- **Forecast**: expected against actual hotspots.
- **Listen**: plays a year as sound.
- **About the data**: how MODIS and VIIRS compare, and where everything comes from.
- **⋮ menu**: copy a link to the exact view, or download the hotspots as CSV.
- **Chat**: the round button in the corner answers questions about the numbers on screen.

The three lines in the top-left corner hide and show the side menu.

## Where things are

| path | what it is |
| --- | --- |
| `index.html` | the page |
| `css/style.css` | all the styling. Colours are variables at the top; dark mode swaps them |
| `js/config.js` | settings: file paths, suggested places, map pictures, search, chat |
| `js/app.js` | the main file: keeps track of what is selected and redraws the page |
| `js/data.js` | loads `hotspots.csv` and adds up hotspots for an area |
| `js/stats.js` | usual values, anomaly score, the summary sentence |
| `js/map.js` | the map, its backgrounds, the hotspot grid, the area box, the regime layer |
| `js/search.js` | the place search |
| `js/flames.js` | the flames behind the Home page |
| `js/prefs.js` | what the browser remembers (dark mode, favourites) |
| `js/calendar.js` | the burning calendar |
| `js/charts.js` | the line charts |
| `js/sound.js`, `js/fire-audio.js`, `js/listening-model.js` | the Listen panel |
| `js/sweep.js` | the Play button on the map |
| `js/chat.js` | the chat box |
| `js/colors.js` | colour ramps, and the colour-blind palette |
| `js/tooltip.js` | the small box that appears when you hover |
| `server/chat-worker.js` | the small chat server (see "Chat box" below) |
| `public/data/` | data files. Format: [DATA_FORMAT.md](DATA_FORMAT.md) |
| `public/basemap/` | city list for the place search when there is no internet |
| `vendor/` | Leaflet and topojson-client, copied in |
| `analysis/` | Python analysis code (fire regimes) |

The map pictures and the place search come from free services (CARTO, Esri, NASA GIBS, Photon),
so those two need internet. Everything else works without it.

## Data

Everything in `public/data/` comes from real NASA FIRMS detections (MODIS C6.1, 2006 to 2026):

```bash
python -m analysis.hotspots_csv --firms data/*.zip --sensor MODIS --res 2 --start 2006-01
python -m analysis.fire_regimes.run
```

`validation.json` holds the MODIS/VIIRS checks (from `shriya_backend` and `spatial_ml`).
The forecast panel appears once a real `forecast.json` is added (format in [DATA_FORMAT.md](DATA_FORMAT.md)).

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
not included in this version. The app's data is real (NASA FIRMS); the forecast is not connected yet.

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

### Latest website integration

Integrated latest `main` revision `140c387` with Mridul’s microphone and Listen updates. Speak lives immediately after Listen in the navigation and opens the Listen window. Shortcut: Option+Shift+M on Mac or Alt+Shift+M on Windows. Keyword Guide opens inside that window. Starting a voice command stops the separate map playback so its sounds do not overlap.
## Chat box

The round button in the bottom-right corner opens a chat. You can ask about the area and years on
screen, like "which month was the most unusual?", and it answers from the same numbers the page shows.
Nobody needs a key to use it.

How it fits together:

- `js/chat.js` is the chat box on the page. `describeView()` in `js/app.js` writes down what is on screen as text.
- `server/chat-worker.js` is a small free server on Cloudflare Workers. It holds the key as a secret and passes questions on.
- `chat.url` in `js/config.js` is the address of that server. Leave it empty to switch the chat off.

**Never put the key in this repo.** It only goes into Cloudflare's secret settings.
