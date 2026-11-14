"""Step 5: regime labelling.

Each cluster's profile is compared with the average cell (z-scores) and named
from rules matching the archetypes in the project doc. Every regime also gets a
plain-English summary sentence for the card / tooltip.
"""
import calendar

import numpy as np

# Okabe-Ito: colour-blind safe, one colour per archetype
ARCHETYPE_COLORS = {
    "high_intensity": "#D55E00",   # vermillion
    "seasonal_wildfire": "#E69F00",  # orange
    "agricultural": "#F0E442",     # yellow
    "year_round": "#CC79A7",       # reddish purple
    "sporadic": "#56B4E9",         # sky blue
    "mixed": "#009E73",            # bluish green
}
SPARE_COLORS = ["#0072B2", "#999999", "#882255", "#44AA99", "#DDCC77"]

SEASONS_NH = {12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
              6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"}
FLIP = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}

PROFILE_COLS = ["hotspots_per_year", "fire_month_share", "active_year_share", "season_concentration",
                "season_length_months", "frp_mean", "frp_p95", "frp_skew", "longest_run_months",
                "runs_per_year", "interannual_cv", "trend_pct_per_year", "frp_trend_pct_per_year",
                "estimated_share", "night_share", "fire_day_share", "mean_run_days", "reburns_per_year"]


def _z(df, cols):
    sd = df[cols].std().replace(0, 1)
    return (df[cols] - df[cols].mean()) / sd


def _season(peak_month, lat, curve=None):
    """Season word for the peak; "spring and autumn" style when there are two
    clear peaks at least 3 months apart (e.g. two crop-residue burns)."""
    name = lambda m: FLIP[SEASONS_NH[m]] if lat < 0 else SEASONS_NH[m]
    if curve is not None:
        c = np.asarray(curve)
        peaks = [m for m in range(12) if c[m] >= 0.5 * c.max()
                 and c[m] >= c[(m - 1) % 12] and c[m] >= c[(m + 1) % 12]]
        peaks = sorted(peaks, key=lambda m: -c[m])
        if len(peaks) >= 2:
            a, b = peaks[0], next((m for m in peaks[1:] if min(abs(m - peaks[0]), 12 - abs(m - peaks[0])) >= 3), None)
            if b is not None and name(a + 1) != name(b + 1):
                first, second = sorted([a, b])
                return f"{name(first + 1)} and {name(second + 1)}"
    return name(peak_month)


def _archetype(z, r):
    g = lambda k: z.get(k, 0.0)
    night = z.get("night_share")
    if r["season_concentration"] < 0.35 and g("fire_month_share") > 0.3:
        return "year_round"
    if g("log_frp_p95") > 0.6 and (g("longest_run_months") > 0.2 or g("frp_skew") > 0.4):
        return "high_intensity"
    ag_night = night is None or night < -0.3
    if g("log_frp_mean") < -0.4 and ag_night and (g("runs_per_year") > 0.2 or g("longest_run_months") < -0.2):
        return "agricultural"
    if g("log_hotspots_per_year") < -0.6 and g("active_year_share") < -0.3:
        return "sporadic"
    if r["season_concentration"] >= 0.5:
        return "seasonal_wildfire"
    return "mixed"


def _freq_word(hpy, q):
    """Frequency relative to all classified cells (terciles)."""
    return "Frequent" if hpy > q[1] else "Rare" if hpy < q[0] else "Occasional"


# (feature, word if this regime is higher, word if lower) used to tell apart
# two regimes that would otherwise get the same name
CONTRASTS = [("log_frp_p95", "higher-intensity", "lower-intensity"),
             ("log_hotspots_per_year", "busier", "quieter"),
             ("season_length_months", "longer-season", "shorter-season"),
             ("log_interannual_cv", "boom-bust", "steady"),
             ("longest_run_months", "longer-burning", "shorter-burning"),
             ("night_share", "night-burning", "daytime")]


def _name(arch, z, r, season, q):
    freq = _freq_word(r["hotspots_per_year"], q)
    peak = calendar.month_abbr[int(r["peak_month"])]
    return {
        "year_round": f"Year-round burning ({freq.lower()})",
        "high_intensity": f"{freq} high-intensity {season} wildfires",
        "agricultural": f"{freq} low-intensity {season} burning (agricultural-style)",
        "sporadic": f"Rare, sporadic fires (peak {peak})",
        "seasonal_wildfire": f"{freq} {season} wildfires (peak {peak})",
        "mixed": f"{freq} mixed fire activity",
    }[arch]


def _summary(r, season):
    peak = calendar.month_name[int(r["peak_month"])]
    hpy = r["hotspots_per_year"]
    parts = [f"A typical cell here sees about {hpy:,.0f} hotspots a year"]
    if r["season_concentration"] < 0.35:
        parts.append("spread across most of the year")
    else:
        parts.append(f"mostly in {season} (peak {peak}, about {r['season_length_months']:.0f} months long)")
    s = ", ".join(parts) + ". "
    s += f"Fires average {r['frp_mean']:.0f} MW per hotspot"
    s += (" and some get very large" if r["frp_skew"] > 0.8 else "") + ". "
    cv = r["interannual_cv"]
    s += ("Activity swings a lot between years" if cv > 1 else
          "Activity is fairly steady year to year" if cv < 0.5 else "Some years are much worse than others")
    t = r["trend_pct_per_year"]
    if abs(t) >= 1:
        s += f", and it is {'rising' if t > 0 else 'falling'} about {abs(t):.0f}% a year"
    s += "."
    if r["estimated_share"] > 0.5:
        s += " Most of this record is pre-2012 model estimates, so treat it with more caution."
    return s


def label(feats, labels, conf, features_used):
    """Returns (regimes list, label->regime_id map)."""
    zf = _z(feats, features_used)
    regimes, used_colors = [], set()
    q = np.percentile(feats["hotspots_per_year"], [33, 66])
    order = feats.assign(_l=labels).groupby("_l")["hotspots_per_year"].median().sort_values(ascending=False).index
    for rid, lab in enumerate(order, start=1):
        m = labels == lab
        z = zf[m].mean().to_dict()
        r = feats.loc[m, [c for c in PROFILE_COLS if c in feats]].median().to_dict()
        r["peak_month"] = int(np.argmax(np.mean(np.stack(feats.loc[m, "monthly_share"]), 0)) + 1)
        r["season_concentration"] = float(feats.loc[m, "season_concentration"].median())
        lat = float(feats.loc[m, "lat"].median())
        curve = np.mean(np.stack(feats.loc[m, "monthly_share"]), 0)
        season = _season(r["peak_month"], lat, curve)
        arch = _archetype(z, r)
        name = _name(arch, z, r, season, q)
        color = ARCHETYPE_COLORS[arch]
        if color in used_colors:
            color = next(c for c in SPARE_COLORS if c not in used_colors)
        used_colors.add(color)
        regimes.append({
            "regime_id": rid, "label": int(lab), "regime_name": name, "archetype": arch, "color": color,
            "n_cells": int(m.sum()), "mean_confidence": round(float(conf[m].mean()), 3),
            "summary": _summary(r, season),
            "profile": {k: round(float(v), 3) for k, v in r.items()},
            "z_profile": {k: round(float(v), 2) for k, v in z.items()},
            "monthly_curve": [round(float(v), 4) for v in curve / curve.sum()],
        })
    _disambiguate(regimes)
    return regimes, {r["label"]: r["regime_id"] for r in regimes}


def _disambiguate(regimes):
    """Same name twice -> prefix each with what sets it apart from the others."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in regimes:
        groups[r["regime_name"]].append(r)
    for name, rs in groups.items():
        if len(rs) < 2:
            continue
        taken = set()
        for r in rs:
            others = [o for o in rs if o is not r]
            best = None
            for feat, hi, lo in CONTRASTS:
                if feat not in r["z_profile"]:
                    continue
                d = r["z_profile"][feat] - np.mean([o["z_profile"][feat] for o in others])
                word = hi if d > 0 else lo
                if word not in taken and (best is None or abs(d) > best[0]):
                    best = (abs(d), word)
            word = best[1] if best else f"group {r['regime_id']}"
            taken.add(word)
            r["regime_name"] = f"{word.capitalize()} {name[0].lower()}{name[1:]}"


# Step 6c: compare against regions whose regime is well known.
KNOWN_REGIONS = [
    {"name": "Canadian boreal", "bbox": (-125, 52, -95, 62), "expect": {"high_intensity", "seasonal_wildfire", "sporadic"}},
    {"name": "Indian agricultural belt (Punjab/Haryana)", "bbox": (73, 28, 78, 32), "expect": {"agricultural"}},
    {"name": "Amazon arc of deforestation", "bbox": (-65, -13, -45, -5), "expect": {"seasonal_wildfire", "high_intensity"}},
    {"name": "Northern Australian savanna", "bbox": (125, -18, 145, -11), "expect": {"seasonal_wildfire", "agricultural"}},
    {"name": "Central African savanna", "bbox": (10, 0, 35, 12), "expect": {"seasonal_wildfire", "agricultural"}},
]


def known_region_check(feats, regime_ids, regimes, min_cells=5):
    by_id = {r["regime_id"]: r for r in regimes}
    out = []
    for reg in KNOWN_REGIONS:
        w, s, e, n = reg["bbox"]
        m = (feats["lon"].between(w, e) & feats["lat"].between(s, n)).to_numpy()
        if m.sum() < min_cells:
            continue
        ids, counts = np.unique(regime_ids[m], return_counts=True)
        top = by_id[int(ids[counts.argmax()])]
        out.append({"region": reg["name"], "cells": int(m.sum()), "dominant_regime": top["regime_name"],
                    "dominant_share": round(float(counts.max() / m.sum()), 2),
                    "matches_expectation": top["archetype"] in reg["expect"]})
    return out
