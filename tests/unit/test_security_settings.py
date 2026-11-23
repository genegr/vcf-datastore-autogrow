"""Transport and credential settings that must stay visible in the inventory and the README, not in the code.
Skipped where PyYAML is missing, like test_repo_safety.py."""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKED = ("playbooks", "roles", "rulebooks", "setup", "demo", "tests/probes", "tests/offline", "environments")


def _files():
    for d in CHECKED:
        for p in sorted(list((ROOT / d).rglob("*.yml")) + list((ROOT / d).rglob("*.yaml")) + list((ROOT / d).rglob("*.j2"))):
            rel = p.relative_to(ROOT).parts
            if "inventory" not in rel and "stubs" not in rel and "group_vars" not in rel:
                yield p


def test_no_literal_certificate_bypass_in_setup_demo_or_probes():
    """Setup, demo and probes send administrator credentials: they honour the inventory (validate_certs of the vCenter or
    array entry, dsgrow_vcfops_validate_certs), they never turn verification off in the code (was 84 places)."""
    bad = []
    for path in _files():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"validate_certs:\s*(false|False|no)\b", code) or re.search(r"GOVC_INSECURE:\s*[\"']?1", code):
                bad.append("%s:%d" % (path.relative_to(ROOT), n))
    assert not bad, "literal certificate bypass at %s" % bad


def test_every_validate_certs_is_an_inventory_expression():
    """Setup, demo and probes read the inventory entry with a default of true; the runtime roles read the connection
    dict built from it (roles/dsgrow_vmfs). Never a literal."""
    seen = 0
    for path in _files():
        rel = path.relative_to(ROOT).parts
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*validate_certs:\s*(.*?)\s*$", line)
            if m:
                seen += 1
                value = m.group(1)
                if rel[0] in ("playbooks", "roles", "tests"):
                    assert value.startswith('"{{') and "validate_certs" in value, "%s: %s" % ("/".join(rel), value)
                else:
                    assert "default(true)" in value, "%s: %s" % ("/".join(rel), value)
    assert seen >= 60


def test_the_example_inventory_verifies_certificates():
    for path in (ROOT / "environments" / "example" / "inventory").rglob("*.yml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"validate_certs:\s*false", text), path
    eda = yaml.safe_load((ROOT / "environments/example/inventory/group_vars/dsgrow/eda.yml").read_text(encoding="utf-8"))
    assert eda["dsgrow_vcfops_validate_certs"] is True


@pytest.mark.parametrize("env", sorted(p.name for p in (ROOT / "environments").iterdir() if (p / "inventory").is_dir()))
def test_every_environment_states_its_vcfops_certificate_policy(env):
    eda = yaml.safe_load((ROOT / "environments" / env / "inventory/group_vars/dsgrow/eda.yml").read_text(encoding="utf-8"))
    assert isinstance(eda.get("dsgrow_vcfops_validate_certs"), bool), "%s must say true or false, in its own files" % env


def test_the_webhook_url_scheme_is_a_variable():
    text = (ROOT / "setup" / "vcfops.yml").read_text(encoding="utf-8")
    assert "dsgrow_eda_vcfops_scheme" in text and not re.search(r"value:\s*\"http://", text)


def test_the_vcfops_bind_address_comes_from_the_run_hosts_env_and_approvals_stay_on_loopback():
    """Not from a tracked file: tools/sync.sh would put the committed value back on the next sync."""
    rulebook = yaml.safe_load((ROOT / "rulebooks" / "datastore_capacity.yml").read_text(encoding="utf-8"))[0]
    sources = {s["name"]: s["ansible.eda.webhook"] for s in rulebook["sources"]}
    assert sources["vcfops"]["host"] == "{{ DSGROW_EDA_VCFOPS_BIND }}"
    assert sources["approvals"]["host"] == "127.0.0.1"
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))["services"]["rulebook"]
    assert compose["environment"]["DSGROW_EDA_VCFOPS_BIND"] == "${DSGROW_EDA_VCFOPS_BIND:-0.0.0.0}"
    assert "DSGROW_EDA_VCFOPS_BIND" in compose["command"][compose["command"].index("--env-vars") + 1].split(",")
    assert "DSGROW_EDA_VCFOPS_BIND=" in (ROOT / ".env.example").read_text(encoding="utf-8")
    rb_vars = yaml.safe_load((ROOT / "rulebooks" / "vars.yml").read_text(encoding="utf-8"))
    assert not [k for k in rb_vars if "bind" in k], "a site setting in a tracked file that sync overwrites"


def test_the_alert_minimum_is_a_margin_under_the_symptom_threshold():
    policy = yaml.safe_load((ROOT / "playbooks/group_vars/all/dsgrow.yml").read_text(encoding="utf-8"))
    assert 0 < policy["dsgrow_alert_min_used_pct"] <= 80   # the used-percent symptom fires at 80


# The invariant behind "every field has a pattern": nothing renders an event as a whole. ansible-core 2.21 renders an item
# of the event when it is read, but a whole-container operation (to_json, a loop, debug var=) renders EVERY value in it,
# including the fields no pattern covers (meta.headers, sent_at, an extra key).
_WHOLE = re.compile(
    r"\b(ansible_eda(?:\.event(?:\.payload|\.meta(?:\.headers)?)?)?|dsgrow_alert|dsgrow_approval|dsgrow_denial)\b"
    r"(?!\.)(?!\s+is\b)\s*(\||\}\}|\)|,|$)")


def test_no_playbook_renders_an_event_as_a_whole():
    seen, bad = 0, []
    for d in ("playbooks", "roles"):
        for path in sorted((ROOT / d).rglob("*")):
            if path.suffix not in (".yml", ".yaml", ".j2"):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#", 1)[0] if path.suffix != ".j2" else line
                for m in _WHOLE.finditer(code):
                    seen += 1
                    rest = code[m.end(1):].lstrip()
                    if not rest.startswith("| default("):       # an alias that stays lazy
                        bad.append("%s:%d %s" % (path.relative_to(ROOT).as_posix(), n, code.strip()[:90]))
    assert seen >= 3, "the check must see the aliases (dsgrow_alert: ... | default({}))"
    assert not bad, "an event rendered as a whole: %s" % bad


def test_the_readme_says_up_front_that_it_is_a_demo_without_a_firewall():
    """A reader who stops after the introduction still learns that this is a demo and that no firewall is set up."""
    head = "\n".join((ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:30])
    assert "**Status: a demo.**" in head
    assert "nothing in this repository sets up a firewall" in " ".join(head.replace("**", "").split())
    assert "(#security-notes)" in head, "the status must point to the Security notes"


def test_the_readme_states_the_limits_it_has():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Security notes" in readme
    section = readme.split("## Security notes", 1)[1].split("\n## ", 1)[0]
    for needle in ("plain HTTP", "nothing here sets up a firewall", "a demo does not need one", "DSGROW_EDA_VCFOPS_BIND",
                   "FlashArray TLS is not verified", "Host.Config.Storage",
                   "dsgrow_alert_min_used_pct", "SYNC_FORCE", "templates"):
        assert needle in section, "README 'Security notes' must mention %r" % needle
