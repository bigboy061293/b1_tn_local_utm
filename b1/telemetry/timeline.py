"""Trục thời gian chung 10 Hz (L3): đặt mẫu telemetry lên lưới đều, đánh dấu điểm đo, điểm nội suy, khoảng trống.

- `measured`: có mẫu cách mốc lưới ≤ nửa bước lưới (50 ms ở 10 Hz), dùng nguyên giá trị mẫu;
- `interpolated`: hai mẫu kẹp mốc cách nhau ≤ `max_gap_s`, nội suy tuyến tính vị trí, độ cao, tốc độ;
- `gap`: khoảng trống dài hơn, không bịa vị trí.
"""
from __future__ import annotations

import bisect
import datetime as dt


def resample(samples: list[dict], hz: float = 10.0, max_gap_s: float = 1.0, start: dt.datetime | None = None,
             end: dt.datetime | None = None) -> list[dict]:
    if not samples:
        return []
    samples = sorted(samples, key=lambda s: s["t_source"])
    ts = [s["t_source"].timestamp() for s in samples]
    step = 1.0 / hz
    t0 = (start or samples[0]["t_source"]).timestamp()
    t1 = (end or samples[-1]["t_source"]).timestamp()
    t0 = round(t0 * hz) / hz
    out = []
    n = int((t1 - t0) * hz + 1e-9) + 1
    for k in range(max(n, 0)):
        t = t0 + k * step
        i = bisect.bisect_left(ts, t)
        cand = [j for j in (i - 1, i) if 0 <= j < len(ts)]
        j = min(cand, key=lambda j: abs(ts[j] - t))
        at = dt.datetime.fromtimestamp(t, dt.timezone.utc)
        if abs(ts[j] - t) <= step / 2 + 1e-6:
            s = samples[j]
            out.append({"t": at, "kind": "measured", "lat": s["lat"], "lon": s["lon"], "alt_agl": s.get("alt_agl"),
                        "ground_speed": s.get("ground_speed"), "sample_t_source": s["t_source"]})
            continue
        if 0 < i < len(ts) and ts[i] - ts[i - 1] <= max_gap_s:
            a, b = samples[i - 1], samples[i]
            f = (t - ts[i - 1]) / (ts[i] - ts[i - 1])

            def lerp(key):
                va, vb = a.get(key), b.get(key)
                return None if va is None or vb is None else va + (vb - va) * f
            out.append({"t": at, "kind": "interpolated", "lat": lerp("lat"), "lon": lerp("lon"), "alt_agl": lerp("alt_agl"),
                        "ground_speed": lerp("ground_speed"), "sample_t_source": None})
        else:
            out.append({"t": at, "kind": "gap", "lat": None, "lon": None, "alt_agl": None, "ground_speed": None,
                        "sample_t_source": None})
    return out
