"""Kiểm thử bản sao số: đầu ra đúng lược đồ I-07/I-06, phủ Remote ID toàn tuyến, tiêm lỗi đúng."""
import io, json, pathlib
import jsonschema, yaml
from shapely.geometry import Point

from twin import sim

API = pathlib.Path(__file__).parent.parent / "api"


def validator(spec_file, schema_name):
    spec = yaml.safe_load((API / spec_file).read_text(encoding="utf-8"))
    schema = dict(spec["components"]["schemas"][schema_name], components=spec["components"])
    return jsonschema.Draft202012Validator(schema)


def run_twin(*argv):
    buf = io.StringIO()
    sim.run(sim.parser().parse_args(["--start", "2026-11-25T07:00:00+00:00", *argv]), out=buf)
    return [json.loads(x) for x in buf.getvalue().splitlines()]


def test_outputs_match_interface_schemas():
    i07 = validator("i07-telemetry.openapi.yaml", "TelemetrySample")
    i06 = validator("i06-ris-station.openapi.yaml", "StationRecord")
    recs = run_twin("--duration", "20", "--count", "2", "--inject", "c2_lost:5:8", "--inject", "ups:0:20:3")
    assert {r["interface"] for r in recs} == {"I-07", "I-06"}
    for r in recs:
        (i07 if r["interface"] == "I-07" else i06).validate(r["payload"])


def test_telemetry_rate_is_10hz_by_default():
    recs = [r for r in run_twin("--duration", "10") if r["interface"] == "I-07"]
    assert len(recs) == 101  # 0..10 s ở 10 Hz


def test_broadcast_rid_covers_whole_centerline_at_design_range():
    line, stations = sim.centerline_utm(), sim.stations_utm()
    d = 0.0
    while d <= line.length:
        p = line.interpolate(d)
        assert any(p.distance(s) <= sim.RID_RANGE_M for s in stations.values()), f"hở phủ tại Km {d / 1000:.2f}"
        d += 100.0


def test_offset_fault_moves_aircraft_off_centerline():
    line = sim.centerline_utm()
    recs = [r["payload"] for r in run_twin("--duration", "40", "--inject", "offset:20:40:900") if r["interface"] == "I-07"]
    def off(p):
        x, y = sim.TO_UTM(p["position"]["lon"], p["position"]["lat"])
        return Point(x, y).distance(line)
    assert off(recs[100]) < 1.0          # t = 10 s, trên tim tuyến
    assert abs(off(recs[300]) - 900) < 2  # t = 30 s, lệch 900 m


def test_station_down_sends_nothing():
    recs = run_twin("--duration", "5", "--inject", "station_down:0:10:1")
    assert not [r for r in recs if r.get("stationId") == "RIS-1"]
