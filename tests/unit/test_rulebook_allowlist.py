"""The rulebook's field allow-lists: ansible renders event strings as Jinja templates, so no playbook may start for an
event whose fields are not on the allow-list (rulebooks/datastore_capacity.yml, skill eda-rulebook).

Reads the patterns out of the rulebook's conditions and checks them in Python, so it runs on the workstation. It cannot
prove how the rulebook engine (Java) evaluates them: tests/rulebook/test_rulebook.py posts hostile events to the real
rulebook for that (dsgrow-eda container). Skipped where PyYAML is missing, as test_repo_safety.py is.
"""
import importlib
import json
import pathlib
import re
import sys

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT / "bridge"))

# a pattern in a condition: event.payload.resource_name is match("...")
_MATCH = re.compile(r'event\.(payload|meta)\.([A-Za-z_]+) is match\("([^"]*)"\)')
# a field compared for equality or membership (schema, event, resource_kind, alert_definition)
_EQUAL = re.compile(r'event\.payload\.([A-Za-z_]+) (?:==|in) ')
# fields the rulebook checks by value, not by pattern, on the approvals rule
_APPROVAL_NOT_TEXT = {"schema", "decision", "tgt", "sent_at"}

HOSTILE = [
    "{{ 7 * 7 }}",
    "{{ lookup('ansible.builtin.env', 'HOME') }}",
    "{% set x = 1 %}",
    "{# a comment #}",
    "#jinja2:variable_start_string:'[%'",
    "ds-capacity01{{ 7 * 7 }}",
    "ds-capacity01\n{{ 7 * 7 }}",
    " {{1}}",
    "{{",
]


def _rulebook():
    return yaml.safe_load((ROOT / "rulebooks" / "datastore_capacity.yml").read_text(encoding="utf-8"))[0]


def _rules():
    return {r["name"]: r for r in _rulebook()["rules"]}


def _condition(rule):
    return " ".join(rule["condition"].split())


def _patterns(rule):
    """{(scope, field): pattern} of every `is match` in the rule's condition."""
    found = {}
    for scope, field, pattern in _MATCH.findall(_condition(rule)):
        assert (scope, field) not in found, "%s is matched twice in %s" % (field, rule["name"])
        found[(scope, field)] = pattern
    return found


def _equal_fields(rule):
    return set(_EQUAL.findall(_condition(rule)))


def _playbook_rules():
    return [r for r in _rulebook()["rules"] if "run_playbook" in r.get("action", {})]


def _vcfops_rules():
    return [r for r in _playbook_rules() if 'event.meta.source.name == "vcfops"' in _condition(r)]


def _approvals_rule(decision="approve"):
    [rule] = [r for r in _playbook_rules() if 'event.meta.source.name == "approvals"' in _condition(r)
              and 'event.payload.decision == "%s"' % decision in _condition(r)]
    return rule


def _fixture(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _template_fields():
    names = {"new.json", "updated.json", "canceled.json"}
    keys = {n: set(json.loads((ROOT / "vcf-ops" / "payload-templates" / n).read_text(encoding="utf-8"))) for n in names}
    assert len({frozenset(k) for k in keys.values()}) == 1, "the three templates must carry the same fields"
    return keys["new.json"]


# ------------------------------------------------------------------ the patterns themselves
def test_every_pattern_is_anchored_and_portable():
    """The same text must mean the same for Python and for the rulebook's Java matcher: plain classes only."""
    seen = 0
    for rule in _playbook_rules():
        for (scope, field), pattern in _patterns(rule).items():
            seen += 1
            where = "%s: %s.%s" % (rule["name"], scope, field)
            assert pattern.startswith("^") and pattern.endswith("$"), where + " is not anchored"
            for bad in ("\\", "(?", "&&", "{{", "}}"):
                assert bad not in pattern, "%s uses %r, which is not portable to the rulebook engine" % (where, bad)
            re.compile(pattern)
    assert seen >= 30, "the check must see the allow-lists"


def test_every_playbook_rule_checks_the_endpoint():
    """meta.endpoint is the sender's URL path and travels in the event too."""
    for rule in _playbook_rules():
        assert ("meta", "endpoint") in _patterns(rule), rule["name"]


# ------------------------------------------------------------------ coverage: no field may reach a playbook unchecked
# What the playbooks and roles READ of an event, found in their source: a field a playbook starts reading must get a
# pattern in the rulebook in the same change, or this fails.
_READ = {
    "vcfops": re.compile(r"\b(?:dsgrow_alert|ansible_eda\.event\.payload)\.([A-Za-z_]+)"),
    "approve": re.compile(r"\bdsgrow_approval\.([A-Za-z_]+)"),
    "deny": re.compile(r"\bdsgrow_denial\.([A-Za-z_]+)"),
}
# a field of the alert the rulebook checks by value, not by pattern
_BY_VALUE = {"schema", "event", "resource_kind", "alert_definition"}


def _fields_read(kind):
    found = {}
    for d in ("playbooks", "roles"):
        for path in sorted((ROOT / d).rglob("*")):
            if path.suffix in (".yml", ".yaml", ".j2") and "group_vars" not in path.parts:
                for field in _READ[kind].findall(path.read_text(encoding="utf-8")):
                    found.setdefault(field, path.relative_to(ROOT).as_posix())
    return found


def test_every_field_a_playbook_reads_from_an_alert_has_a_pattern():
    read = _fields_read("vcfops")
    assert {"alert_id", "resource_id", "resource_name", "criticality", "cancel_time", "link", "adapter_instance"} <= set(read), read
    for rule in _vcfops_rules():
        covered = {f for (scope, f) in _patterns(rule) if scope == "payload"} | _equal_fields(rule)
        missing = {f: where for f, where in read.items() if f not in covered}
        assert not missing, "%s: a playbook reads %s but the rulebook has no pattern for it" % (rule["name"], missing)
    assert len(_vcfops_rules()) == 2


def test_the_fields_no_playbook_reads_have_no_pattern_so_they_can_never_drop_an_alert():
    """status, adapter_kind, the times and symptoms: inert (ansible-core renders an item only when it is read), so a value
    VCF Operations formats in an unexpected way must not cost a proposal."""
    read = set(_fields_read("vcfops"))
    unread = _template_fields() - read - _BY_VALUE
    assert {"status", "adapter_kind", "create_time", "update_time", "symptoms"} <= unread, unread
    for rule in _vcfops_rules():
        patterned = {f for (scope, f) in _patterns(rule) if scope == "payload"}
        assert not (patterned & unread), "%s: a pattern for a field nothing reads: %s" % (rule["name"], patterned & unread)


def test_every_field_the_approval_and_denial_playbooks_read_has_a_pattern():
    for kind, rule in (("approve", _approvals_rule()), ("deny", _approvals_rule("deny"))):
        read = set(_fields_read(kind))
        covered = {f for (scope, f) in _patterns(rule) if scope == "payload"}
        missing = read - covered - {"schema", "decision", "tgt"}
        assert not missing, "%s: read by the playbook, no pattern: %s" % (kind, sorted(missing))
    assert {"rid", "tgt", "approver_id", "approver_name", "vc", "ds", "dev"} <= set(_fields_read("approve"))


def test_fixtures_carry_no_field_outside_the_contract():
    allowed = _template_fields() | {"adapter_instance"}
    for path in (FIX / "vcfops").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") == "dsgrow.vcfops.v1":
            assert set(data) <= allowed, "%s: %s" % (path.name, sorted(set(data) - allowed))


def test_approvals_rule_covers_every_field_of_the_event():
    rule = _approvals_rule()
    covered = {f for (scope, f) in _patterns(rule) if scope == "payload"}
    wanted = set(_fixture("approvals/approve.json")) - _APPROVAL_NOT_TEXT
    assert wanted <= covered, "no allow-list for %s" % sorted(wanted - covered)
    assert "event.payload.tgt > 0" in _condition(rule)


def test_denied_rule_covers_every_field_of_the_denial():
    rule = _approvals_rule("deny")
    covered = {f for (scope, f) in _patterns(rule) if scope == "payload"}
    wanted = set(_fixture("approvals/deny.json")) - {"schema", "decision", "sent_at"}
    assert wanted <= covered, "no allow-list for %s" % sorted(wanted - covered)
    assert "tgt" not in _condition(rule), "a denial carries no size"


# ------------------------------------------------------------------ legitimate events pass, hostile ones never do
@pytest.mark.parametrize("name,rule_name", [
    ("new_datastore_80.json", "Propose expansion for a datastore capacity alert"),
    ("updated_datastore_85.json", "Propose expansion for a datastore capacity alert"),
    ("new_datastore_again.json", "Propose expansion for a datastore capacity alert"),
    ("symptoms_na.json", "Propose expansion for a datastore capacity alert"),
    ("canceled_datastore.json", "Alert cleared"),
])
def test_legitimate_alerts_pass_every_pattern(name, rule_name):
    payload = _fixture("vcfops/" + name)
    for (scope, field), pattern in _patterns(_rules()[rule_name]).items():
        if scope == "payload":
            if field == "adapter_instance" and field not in payload:
                continue   # optional: the rule's `is not defined` branch lets it through (VCF Ops 9.1.1 sends none)
            assert re.fullmatch(pattern, payload[field]), "%s=%r is refused by %s" % (field, payload[field], pattern)


def test_adapter_instance_may_be_absent_or_a_host_name():
    for rule in _vcfops_rules():
        pattern = _patterns(rule)[("payload", "adapter_instance")]
        for good in ("vcenter01.example.lab", "vcenter01", "192.0.2.10"):
            assert re.fullmatch(pattern, good), good
        assert "(event.payload.adapter_instance is not defined" in _condition(rule)


def test_the_approval_the_bridge_sends_passes_every_pattern():
    rule = _approvals_rule()
    event = _bridge_event(user_name="demo-approver")
    for (scope, field), pattern in _patterns(rule).items():
        if scope == "payload":
            assert re.fullmatch(pattern, str(event[field])), "%s=%r is refused by %s" % (field, event[field], pattern)
    assert isinstance(event["tgt"], int) and event["tgt"] > 0


def test_the_denial_the_bridge_sends_passes_every_pattern():
    rule = _approvals_rule("deny")
    event = _bridge_event(user_name="demo-approver", decision="deny")
    for (scope, field), pattern in _patterns(rule).items():
        if scope == "payload":
            assert re.fullmatch(pattern, str(event[field])), "%s=%r is refused by %s" % (field, event[field], pattern)


@pytest.mark.parametrize("hostile", HOSTILE)
def test_no_field_accepts_template_text(hostile):
    for rule in _playbook_rules():
        for (scope, field), pattern in _patterns(rule).items():
            assert not re.fullmatch(pattern, hostile), "%s: %s accepts %r" % (rule["name"], field, hostile)


@pytest.mark.parametrize("hostile", [h for h in HOSTILE if "{" in h])
def test_no_field_accepts_template_text_around_a_legitimate_value(hostile):
    """A legitimate fixture value with template text in front of it or after it (any brace starts a template)."""
    payload = _fixture("vcfops/new_datastore_80.json")
    for rule in _vcfops_rules():
        for (scope, field), pattern in _patterns(rule).items():
            if scope == "payload" and field in payload:
                for value in (payload[field] + hostile, hostile + payload[field]):
                    assert not re.fullmatch(pattern, value), "%s: %s accepts %r" % (rule["name"], field, value)


# the text a playbook shows (criticality, cancel_time, link): only a Jinja opener or a leading "#" is refused
TEXT_OK = ["", "N/A", "WARNING", "Warnung", "${CANCEL_TIME}", "${LINK_TO_ALERT}", "{", "a{b", "x{", "{x", "$ {x}",
           "https://192.0.2.5/ui/index.action#/object/1/alerts?a=1&b=2", "ui/index.action#/object", "not a url at all",
           "Nov 23, 2026, 9:15:03 PM UTC", "1763932503000", "x" * 3000, "line one\nline two",
           "https://ops.example.lab/x>|<!channel>"]      # Slack markup is the proposal template's job (its own test)
TEXT_REFUSED = ["{{", "{{ 7 * 7 }}", "a{{b", "{%", "{% if 1 %}", "a{%b", "{#", "{# c #}", "x{#", "#", "#x",
                "#jinja2:variable_start_string:'[%'", "ok{{ lookup('pipe', 'id') }}", "line\n{{ 1 }}"]


@pytest.mark.parametrize("field", ["criticality", "cancel_time", "link"])
def test_text_fields_accept_real_looking_values_and_refuse_a_jinja_opener(field):
    for rule in _vcfops_rules():
        pattern = _patterns(rule)[("payload", field)]
        for value in TEXT_OK:
            assert re.fullmatch(pattern, value), (rule["name"], field, value[:40])
        for value in TEXT_REFUSED:
            assert not re.fullmatch(pattern, value), (rule["name"], field, value[:40])


def test_identifiers_do_not_accept_a_trailing_newline():
    """The text fields (link, ...) may hold a newline: without a Jinja opener it cannot start a template."""
    payload = _fixture("vcfops/new_datastore_80.json")
    for rule in _vcfops_rules():
        patterns = _patterns(rule)
        for field in ("alert_id", "resource_id", "resource_name"):
            assert not re.fullmatch(patterns[("payload", field)], payload[field] + "\n"), (rule["name"], field)


# ------------------------------------------------------------------ the bridge: approver_name is the one free-text field
def _bridge_event(user_name, decision="approve"):
    """The event the real Bridge.approve (or .deny) sends for a Slack user with this name."""
    actions, config = importlib.import_module("actions"), importlib.import_module("config")
    gib, now = 1024 ** 3, 1_763_546_400
    env = {"SLACK_BOT_TOKEN": "xoxb-x", "SLACK_APP_TOKEN": "xapp-x", "DSGROW_EDA_APPROVALS_TOKEN": "t",
           "DSGROW_APPROVAL_HMAC_SECRET": "s", "DSGROW_APPROVER_IDS": "U0DEMO0001",
           "DSGROW_SIZE_STEP_BYTES": str(64 * gib)}
    sent = []

    class Slack:
        def __getattr__(self, name):
            return lambda **kw: {"ok": True}

    bridge = actions.Bridge(config.from_env(env), Slack(), clock=lambda: now,
                            sender=lambda url, token, secret, event: sent.append(event) or 200)
    value = json.dumps({"rid": "0b5e6f1c-7d2a-4e8b-9c3f-1a2b3c4d5e6f", "vc": "vcenter01.example.lab",
                        "ds": "ds-capacity01", "dev": "eui.00361019ecace43d24a93783000120a4",
                        "cur": 512 * gib, "exp": now + 3600, "tgt": 640 * gib})
    blocks = [{"type": "actions", "block_id": "dsgrow_actions", "elements": []}]
    if decision == "deny":
        assert bridge.deny(user_id="U0DEMO0001", user_name=user_name, channel="C0DEMO0001", ts="1763546400.000100",
                           blocks=blocks, button_value=value) is None
    else:
        assert bridge.approve(user_id="U0DEMO0001", user_name=user_name, channel="C0DEMO0001",
                              ts="1763546400.000100", blocks=blocks, button_value=value) is None
    [event] = sent
    return event


@pytest.mark.parametrize("name", HOSTILE + ["jane.doe", "demo-approver", "Jane Doe (SRE)", "ünïcödé", "x" * 500,
                                            "", None, 12345, "a\tb\x00c"])
@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_the_bridge_only_signs_names_the_rulebook_accepts(name, decision):
    pattern = _patterns(_approvals_rule(decision))[("payload", "approver_name")]
    event = _bridge_event(user_name=name, decision=decision)
    assert re.fullmatch(pattern, event["approver_name"]), "%r became %r" % (name, event["approver_name"])
    assert len(event["approver_name"]) <= 80


def test_clean_name_keeps_ordinary_names():
    validate = importlib.import_module("validate")
    for name in ("jane.doe", "demo-approver", "Jane Doe (SRE)", "j_doe@example"):
        assert validate.clean_name(name) == name
    assert validate.clean_name("{{ 7 * 7 }}") == "__ 7 _ 7 __"


def test_the_fixture_is_the_event_the_bridge_sends():
    """tests/fixtures/approvals/approve.json is what the rulebook test posts: it must carry what the bridge sends."""
    assert set(_bridge_event(user_name="demo-approver")) == set(_fixture("approvals/approve.json"))
    assert set(_bridge_event(user_name="demo-approver", decision="deny")) == set(_fixture("approvals/deny.json"))
