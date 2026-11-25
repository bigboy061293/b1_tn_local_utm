"""Kiểm tra mẫu telemetry hợp lệ và không hợp lệ theo lược đồ I-07 (CT-1)."""
import copy, pathlib
import jsonschema, yaml

SPEC = yaml.safe_load((pathlib.Path(__file__).parent.parent / "api" / "i07-telemetry.openapi.yaml").read_text(encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]
RESOLVER_ROOT = {"components": {"schemas": SCHEMAS}}

GOOD = {
    "flightId": "3b2f6a0e-5d1c-4c2a-9a51-0f7e2b8d9c11",
    "uasId": "1596F3YB1234567890",
    "timeSource": "2026-11-25T07:20:01.123Z",
    "position": {"lat": 11.1347056, "lon": 106.4437417},
    "altitude": {"value": 120.0, "reference": "AGL"},
    "velocity": {"groundSpeed": 15.2, "verticalSpeed": 0.1, "track": 318.0},
    "gnss": {"fixType": "3d", "satellites": 17, "hdop": 0.8},
    "energy": {"batteryPercent": 76, "voltage": 47.9},
    "flightMode": {"mode": "AUTO", "autonomy": "automatic"},
    "armed": True,
    "safetyFlags": [],
    "c2Link": {"state": "ok", "rssiDbm": -71},
}


def validate(sample):
    schema = dict(SCHEMAS["TelemetrySample"], components=RESOLVER_ROOT["components"])
    jsonschema.Draft202012Validator(schema).validate(sample)


def test_good_sample_passes():
    validate(GOOD)


def test_missing_altitude_reference_fails():
    bad = copy.deepcopy(GOOD)
    del bad["altitude"]["reference"]
    try:
        validate(bad)
    except jsonschema.ValidationError:
        return
    raise AssertionError("độ cao thiếu hệ quy chiếu phải bị từ chối")


def test_time_without_millis_fails():
    bad = dict(GOOD, timeSource="2026-11-25T07:20:01Z")
    try:
        validate(bad)
    except jsonschema.ValidationError:
        return
    raise AssertionError("thời gian không có mili giây phải bị từ chối")


def test_unknown_field_fails():
    bad = dict(GOOD, extra=1)
    try:
        validate(bad)
    except jsonschema.ValidationError:
        return
    raise AssertionError("trường lạ phải bị từ chối")
