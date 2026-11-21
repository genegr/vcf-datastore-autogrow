"""Build-time check of docker/patches/everpure-flasharray-1.46.0-issue1103.patch (run by docker/eda.Dockerfile).

Feeds the two code paths that #1103 breaks with what the lab arrays send (priority_adjustment_value null, as the real
py-pure-client model holds it) and a fake array, no network:
  - purefa_info generate_vol_dict  (gather_subset volumes)
  - purefa_volume _volfact         (the end of every purefa_volume call on an existing volume, the extend included)
Exits non-zero when either still fails, when the SDK no longer behaves as in the report (then the patch may be moot:
re-check upstream), or when an unguarded read is left. On the unpatched 1.46.0 both paths raise AttributeError."""
import pathlib
import re
import sys
from types import SimpleNamespace

COLL = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/usr/share/ansible/collections")
sys.path.insert(0, str(COLL))
MODS = COLL / "ansible_collections/everpure/flasharray/plugins/modules"

from pypureclient.flasharray._common.models.priority_adjustment_v_2_10 import PriorityAdjustment  # noqa: E402

from ansible_collections.everpure.flasharray.plugins.modules import purefa_info, purefa_volume  # noqa: E402

problems = []

# 1. The SDK still stores a JSON null as an unset attribute (what the report describes).
pa = PriorityAdjustment(priority_adjustment_operator="=", priority_adjustment_value=None)
try:
    pa.priority_adjustment_value
    problems.append("SDK: an unset priority_adjustment_value no longer raises; re-check whether the patch is needed")
except AttributeError:
    pass

# 2. No unguarded attribute read left in the three modules.
for name in ("purefa_info.py", "purefa_volume.py", "purefa_vg.py"):
    text = (MODS / name).read_text(encoding="utf-8")
    left = [n for n, line in enumerate(text.splitlines(), 1) if re.search(r"\.priority_adjustment_value\b", line)]
    if left:
        problems.append("%s: unguarded read at lines %s" % (name, left))


def volume():
    return SimpleNamespace(
        name="dsgrow-check", subtype="regular", provisioned=214748364800, source=None, created=1763600000000,
        serial="0123456789ABCDEF01234567", space=None, host_encryption_key_status="none", priority=0,
        priority_adjustment=PriorityAdjustment(priority_adjustment_operator="=", priority_adjustment_value=None),
        qos=SimpleNamespace(), promotion_status="promoted", requested_promotion_state="promoted", destroyed=False,
        context=SimpleNamespace(name="fa-b"))


class FakeArray:
    def get_rest_version(self):
        return "2.59"

    def _items(self, *_, **__):
        return SimpleNamespace(status_code=200, items=[volume()])

    def _none(self, *_, **__):
        return SimpleNamespace(status_code=200, items=[])

    get_volumes = _items
    get_connections = get_volumes_tags = _none


# 3. purefa_info volumes
try:
    info = purefa_info.generate_vol_dict(FakeArray(), False)
    if info["dsgrow-check"]["priority_adjustment"] != "=":
        problems.append("purefa_info: priority_adjustment %r, expected '='" % info["dsgrow-check"]["priority_adjustment"])
except Exception as exc:  # noqa: BLE001 - report whatever the module raises
    problems.append("purefa_info generate_vol_dict: %s: %s" % (type(exc).__name__, exc))

# 4. purefa_volume _volfact
try:
    fact = purefa_volume._volfact(SimpleNamespace(check_mode=False, params={"context": ""}), FakeArray(), "dsgrow-check")
    if fact["dsgrow-check"]["size"] != 214748364800 or fact["dsgrow-check"]["priority_value"] is not None:
        problems.append("purefa_volume _volfact: unexpected %r" % fact)
except Exception as exc:  # noqa: BLE001
    problems.append("purefa_volume _volfact: %s: %s" % (type(exc).__name__, exc))

if problems:
    sys.exit("everpure.flasharray #1103 patch check FAILED:\n  " + "\n  ".join(problems))
print("everpure.flasharray #1103 patch check ok: purefa_info volumes and purefa_volume tolerate a null priority value")
