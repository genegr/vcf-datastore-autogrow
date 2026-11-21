"""Unit tests for plugins/filter/dsgrow.py (pure functions, no ansible needed)."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("dsgrow_filter", ROOT / "plugins" / "filter" / "dsgrow.py")
f = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f)

GIB = 1024 ** 3
TIB = 1024 ** 4
POLICY = {
    "size_step_bytes": 256 * GIB,
    "max_growth_pct": 50,
    "max_growth_bytes": 10 * TIB,
    "max_volume_bytes": 64 * TIB,
    "fa_min_free_pct_after": 20,
}
DEMO = dict(POLICY, size_step_bytes=64 * GIB)
SERIAL = "361019ECACE43D83000120A4"


# ------------------------------------------------------------------ device <-> serial
def test_naa_to_serial():
    assert f.dsgrow_device_to_fa_serial("naa.624a9370361019ecace43d83000120a4") == SERIAL


def test_naa_uppercase_input_accepted():
    assert f.dsgrow_device_to_fa_serial("NAA.624A9370361019ECACE43D83000120A4") == SERIAL


def test_eui_to_serial():
    assert f.dsgrow_device_to_fa_serial("eui.00361019ecace43d24a93783000120a4") == SERIAL


@pytest.mark.parametrize("kind", ["naa", "eui"])
def test_roundtrip(kind):
    dev = f.dsgrow_fa_serial_to_device(SERIAL, kind)
    assert f.dsgrow_device_to_fa_serial(dev) == SERIAL
    assert f.dsgrow_is_everpure_device(dev)


def test_serial_to_devices_exact():
    assert f.dsgrow_fa_serial_to_device(SERIAL, "naa") == "naa.624a9370361019ecace43d83000120a4"
    assert f.dsgrow_fa_serial_to_device(SERIAL, "eui") == "eui.00361019ecace43d24a93783000120a4"


@pytest.mark.parametrize("dev", [
    "naa.600a098038303053",                               # other vendor
    "naa.624a9370361019ecace43d83000120a",                # 23 digits
    "naa.624a9370361019ecace43d83000120a4ff",             # too long
    "naa.624a9370361019ecace43d83000120zz",               # not hex
    "eui.0123456789abcdef0123456789abcdef",               # NVMe, not the Pure OUI
    "eui.01361019ecace43d24a93783000120a4",               # wrong leading byte
    "eui.00361019ecace43d24a93783000120a",                # 31 digits
    "t10.ATA_____disk",
    "",
])
def test_rejects_non_everpure(dev):
    assert not f.dsgrow_is_everpure_device(dev)
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_device_to_fa_serial(dev)


def test_rejects_non_string():
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_device_to_fa_serial(None)


@pytest.mark.parametrize("serial", ["ABC", SERIAL + "0", "Z" * 24, None])
def test_serial_to_device_rejects_bad_serial(serial):
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_fa_serial_to_device(serial)


def test_serial_to_device_rejects_kind():
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_fa_serial_to_device(SERIAL, "t10")


# ------------------------------------------------------------------ units
def test_roundup():
    assert f.dsgrow_roundup(1, 64 * GIB) == 64 * GIB
    assert f.dsgrow_roundup(64 * GIB, 64 * GIB) == 64 * GIB
    assert f.dsgrow_roundup(64 * GIB + 1, 64 * GIB) == 128 * GIB
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_roundup(1, 0)


@pytest.mark.parametrize("value,text", [
    (640 * GIB, "640 GiB"),
    (TIB + TIB // 4, "1.25 TiB"),
    (2 * TIB, "2 TiB"),
    (512 * 1024 ** 2, "512 MiB"),
    (10, "10 B"),
])
def test_human_bytes(value, text):
    assert f.dsgrow_human_bytes(value) == text


@pytest.mark.parametrize("text,value", [
    ("704", 704 * GIB),
    ("704 GiB", 704 * GIB),
    ("704G", 704 * GIB),
    ("704 gb", 704 * GIB),
    ("1.5 TiB", TIB + TIB // 2),
    ("1T", TIB),
    (" 2 tib ", 2 * TIB),
])
def test_parse_size(text, value):
    assert f.dsgrow_parse_size(text) == value


@pytest.mark.parametrize("text", ["", "abc", "-5 GiB", "5 PB", "0.3 GiB", "1e3"])
def test_parse_size_rejects(text):
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_parse_size(text)


# ------------------------------------------------------------------ caps
def test_check_target_ok():
    assert f.dsgrow_check_target(704 * GIB, 512 * GIB, DEMO) == []


def test_check_target_grow_only():
    reasons = f.dsgrow_check_target(512 * GIB, 512 * GIB, DEMO)
    assert any("grow only" in r for r in reasons)
    assert any("grow only" in r for r in f.dsgrow_check_target(448 * GIB, 512 * GIB, DEMO))


def test_check_target_step():
    assert any("multiple" in r for r in f.dsgrow_check_target(700 * GIB, 512 * GIB, DEMO))


def test_check_target_growth_pct():
    # 512 -> 832 is +62.5 %
    assert any("50%" in r for r in f.dsgrow_check_target(832 * GIB, 512 * GIB, DEMO))
    # exactly +50 % is allowed
    assert f.dsgrow_check_target(768 * GIB, 512 * GIB, DEMO) == []


def test_check_target_growth_bytes():
    pol = dict(POLICY, max_growth_pct=1000)
    assert any("per-request cap" in r for r in f.dsgrow_check_target(30 * TIB + 256 * GIB, 20 * TIB, pol))


def test_check_target_max_volume():
    pol = dict(POLICY, max_growth_pct=1000, max_growth_bytes=100 * TIB)
    assert any("maximum volume size" in r for r in f.dsgrow_check_target(64 * TIB + 256 * GIB, 60 * TIB, pol))


def test_check_target_array_headroom():
    array = {"capacity_bytes": 100 * TIB, "used_bytes": 79 * TIB}
    pol = dict(POLICY, max_growth_pct=1000)
    # +1 TiB leaves exactly 20 % -> allowed; +1.25 TiB leaves 19.75 % -> refused
    assert f.dsgrow_check_target(2 * TIB, TIB, pol, array) == []
    reasons = f.dsgrow_check_target(TIB + 1280 * GIB, TIB, pol, array)
    assert len(reasons) == 1 and "array free space" in reasons[0]


def test_array_free_pct_after():
    assert f.dsgrow_array_free_pct_after({"capacity_bytes": 100, "used_bytes": 50}, 10) == 40.0
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_array_free_pct_after({"capacity_bytes": 0, "used_bytes": 0}, 1)


# ------------------------------------------------------------------ options
def test_size_options_demo():
    """512 GiB demo datastore at ~80 %: 0.70 and 0.65 both round to 640 GiB (shown once), 0.60 -> 704 GiB."""
    res = f.dsgrow_size_options(410 * GIB, 512 * GIB, [0.70, 0.65, 0.60], DEMO)
    assert [o["target_bytes"] for o in res["options"]] == [640 * GIB, 704 * GIB]
    assert res["refused"] == []
    first = res["options"][0]
    assert first["growth_bytes"] == 128 * GIB
    assert first["utilization_after_pct"] == 64.1
    assert first["label"] == "Grow to 640 GiB (→ 64.1%)"


def test_size_options_production_step_collapses_to_one():
    """With the 256 GiB default step every option for a 512 GiB volume rounds to 768 GiB (+50 %, the cap)."""
    res = f.dsgrow_size_options(410 * GIB, 512 * GIB, [0.70, 0.65, 0.60], POLICY)
    assert [o["target_bytes"] for o in res["options"]] == [768 * GIB]


def test_size_options_all_refused_keeps_reasons():
    """A 512 GiB volume at 99 %: 0.60 needs 846 GiB -> 1024 GiB (+100 %), over the cap: no buttons, reasons kept."""
    res = f.dsgrow_size_options(507 * GIB, 512 * GIB, [0.60], POLICY)
    assert res["options"] == []
    assert res["refused"][0]["target_bytes"] == 1024 * GIB
    assert any("50%" in r for r in res["refused"][0]["reasons"])


def test_size_options_skip_when_not_larger():
    # 10 % used: every target is below current
    res = f.dsgrow_size_options(50 * GIB, 512 * GIB, [0.70], DEMO)
    assert res["options"] == []
    assert "grow only" in res["refused"][0]["reasons"][0]


def test_size_options_bad_utilization():
    with pytest.raises(f.AnsibleFilterError):
        f.dsgrow_size_options(1, 2, [1.2], DEMO)


# ------------------------------------------------------------------ requests
def test_request_expired():
    assert f.dsgrow_request_expired(100, 100)
    assert f.dsgrow_request_expired(100, 101)
    assert not f.dsgrow_request_expired(100, 99)


def test_lock_name():
    assert f.dsgrow_lock_name("VCenter-W01.vcf9-1.lab", "vcf9-1-w02-demo01") == "vcenter-w01.vcf9-1.lab__vcf9-1-w02-demo01"
    assert "/" not in f.dsgrow_lock_name("vc", "a/b c")


def test_slack_escape():
    assert f.dsgrow_slack_escape("a<b>&c") == "a&lt;b&gt;&amp;c"
    assert f.dsgrow_slack_escape("<!channel>") == "&lt;!channel&gt;"


def test_filter_module_lists_all():
    names = f.FilterModule().filters()
    assert "dsgrow_size_options" in names and "dsgrow_device_to_fa_serial" in names
