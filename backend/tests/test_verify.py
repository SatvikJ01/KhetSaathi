from app.agent.verify import normalize_digits, verify_draft

RESULTS = {
    "location": {"id": "T1", "tool": "location", "ok": True, "data": {"state": "Maharashtra"}},
    "weather_forecast": {
        "id": "T2", "tool": "weather_forecast", "ok": True,
        "data": {"daily": [{"date": "2026-11-01", "rain_mm": 38.4, "tmax_c": 31.2}],
                 "summary": {"next7_rain_total_mm": 52.0}},
    },
    "mandi_prices": {"id": "T3", "tool": "mandi_prices", "ok": False, "data": {}, "error": "down"},
}


def test_grounded_answer_passes():
    draft = "Harvest before 1 Nov: 38 mm rain expected [T2]. Week total 52 mm [T2]."
    v = verify_draft(draft, RESULTS, "When to harvest?")
    assert v["ok"], v["issues"]
    assert v["citations_used"] == ["T2"]


def test_invented_number_fails():
    v = verify_draft("Expect 120 mm rain [T2].", RESULTS, "rain?")
    assert not v["ok"]
    assert "120" in v["issues"][0]


def test_citing_failed_tool_fails():
    v = verify_draft("Price is good [T3].", RESULTS, "price?")
    assert not v["ok"]


def test_devanagari_digits_are_checked():
    assert normalize_digits("३८ मिमी") == "38 मिमी"
    assert verify_draft("३८ मिमी बारिश [T2]", RESULTS, "बारिश?")["ok"]
    assert not verify_draft("९९ मिमी बारिश [T2]", RESULTS, "बारिश?")["ok"]


def test_pesticide_dose_blocked():
    v = verify_draft("Spray 2 ml/litre [T2].", RESULTS, "pest?")
    assert any("dosage" in i for i in v["issues"])
