# Syntax TERROR: Fire Hotspot Explorer

NASA Space Apps 2026, challenge: Harmonization of MODIS and VIIRS Hot Spots.

## Run it locally

Needs Node.js LTS (https://nodejs.org).

```bash
npm install
npm run dev
```

Then open the link Vite prints (usually http://localhost:5173).

## Deploy

Every push to `main` is built and published to GitHub Pages by
`.github/workflows/deploy.yml`.

## Git rules

- Work on your own branch.
- Before pushing to `main`: pull `main` and merge it into your branch first.
- Never force-push.
