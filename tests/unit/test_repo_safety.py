"""Static safety checks (skill expansion-safety-review, the machine-checkable part) and consistency of duplicated
settings. Reads the YAML; runs anywhere PyYAML is installed."""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]
CODE_DIRS = ["playbooks", "roles", "rulebooks", "setup"]
# demo/ creates and removes only its own filler VM (CLAUDE.md rule 8 exception); it is checked separately below.


def _yaml_files(*dirs):
    for d in dirs:
        base = ROOT / d
        if base.exists():
            yield from sorted(p for p in base.rglob("*.yml"))


def _tasks(node):
    """Every task dict in a playbook / task file, through blocks."""
    if isinstance(node, list):
        for x in node:
            yield from _tasks(x)
    elif isinstance(node, dict):
        if any(k in node for k in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers")):
            for k in ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers"):
                yield from _tasks(node.get(k, []))
        else:
            yield node


def _load(p):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ forbidden patterns (rule 8 and the review table)
FORBIDDEN = [r"state:\s*absent", r"\beradicate", r"\bresignature", r"purestorage\.flasharray\.",
             r"ignore_errors:\s*true", r"\btruncate:\s", r"\boverwrite:\s"]


@pytest.mark.parametrize("path", list(_yaml_files(*CODE_DIRS)), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_forbidden_patterns(path):
    text = path.read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for pat in FORBIDDEN:
        assert not re.search(pat, code), "%s: forbidden pattern %s" % (path.relative_to(ROOT), pat)


def test_failed_when_false_is_always_checked_right_after():
    """failed_when: false is allowed only on a no_log call whose result the very next task checks (it hides a
    token-carrying result, it never swallows an error)."""
    for path in _yaml_files(*CODE_DIRS):
        tasks = list(_tasks(_load(path) or []))
        for i, task in enumerate(tasks):
            if task.get("failed_when") is False or str(task.get("failed_when")).lower() == "false":
                reg = task.get("register")
                assert reg and task.get("no_log") is True, "%s: %s" % (path.relative_to(ROOT), task.get("name"))
                nxt = str(tasks[i + 1]) if i + 1 < len(tasks) else ""
                assert reg in nxt and ("assert" in nxt or "check.yml" in nxt), \
                    "%s: %s is not checked by the next task" % (path.relative_to(ROOT), task.get("name"))


def test_no_self_referencing_variables():
    """`x: "{{ x }}"` (e.g. in an include's vars) loops forever on ansible-core 2.21: 'Recursive loop detected'.
    It stopped the first real approval on 2026-11-20 (execute.yml passed dsgrow_fa_match to itself)."""
    pat = re.compile(r'^(\s*)(\w+):\s*"\{\{\s*\2\s*(\|[^}]*)?\}\}"\s*$')
    vars_start = re.compile(r"^(\s*)vars:\s*$")
    for path in _yaml_files(*CODE_DIRS, "demo", "tests/offline", "tests/probes"):
        vars_indent = None   # inside a `vars:` mapping (lazy variables) while lines are indented deeper than this
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip() and vars_indent is not None and len(line) - len(line.lstrip()) <= vars_indent:
                vars_indent = None
            m = vars_start.match(line)
            if m:
                vars_indent = len(m.group(1))
                continue
            # module args such as set_stats data or set_fact are evaluated once and do not loop; vars: do
            if vars_indent is not None:
                assert not pat.match(line), "%s:%d self-reference in vars: %s" % (path.relative_to(ROOT), n, line.strip())


def _vars_blocks(node):
    """Every `vars:` mapping of plays, tasks and blocks (lazy variables: a self-reference there loops)."""
    if isinstance(node, list):
        for x in node:
            yield from _vars_blocks(x)
    elif isinstance(node, dict):
        if isinstance(node.get("vars"), dict):
            yield node["vars"]
        for k, v in node.items():
            if k != "vars":
                yield from _vars_blocks(v)


def test_no_self_referencing_variables_multiline():
    """The same loop through a folded value: `x: >- {{ x | combine(...) }}` (caught by the review, 2026-11-20)."""
    for path in _yaml_files(*CODE_DIRS, "demo", "tests/offline", "tests/probes"):
        for block in _vars_blocks(_load(path) or []):
            for name, value in block.items():
                if isinstance(value, str) and "{{" in value:
                    exprs = " ".join(re.findall(r"\{\{(.*?)\}\}|\{%(.*?)%\}", value, re.S).__str__().split())
                    assert not re.search(r"(?<![\w.])%s(?![\w])" % re.escape(name), exprs), \
                        "%s: vars %s refers to itself" % (path.relative_to(ROOT), name)


def test_demo_never_removes_the_template():
    """Owner 2026-11-20: the Ubuntu template survives every demo reset (no re-deploy, no download). It lives on the
    domain's main datastore, never on the demo datastore that cleanup destroys, and no demo task removes it."""
    for inv in (ROOT / "inventory").glob("*/group_vars/dsgrow/demo.yml"):
        demo = _load(inv)["dsgrow_demo"]
        assert demo["template_datastore"] != demo["datastore"], inv
    for path in _yaml_files("demo"):
        for task in _tasks(_load(path) or []):
            for args in (v for v in task.values() if isinstance(v, dict)):
                if args.get("state") in REMOVING_STATES or args.get("eradicate") or args.get("is_template") is False:
                    assert "template" not in str(args), "%s: %s" % (path.relative_to(ROOT), task.get("name"))
                if "template" in args:      # a clone: the template is only ever the source, by its inventory name
                    assert args["template"] == "{{ dd.template }}" and args.get("name") == "{{ dd.vm }}", path


def test_assert_messages_are_strings():
    """ansible-core 2.21 validates fail_msg / success_msg (str or list of str) BEFORE evaluating `that`: a message that
    is one bare dict or list of dicts fails the task even when every condition holds (setup/template.yml's read-back,
    2026-11-20). A single-expression message must be `| to_json`, a string expression, or a *_problems list of str."""
    single = re.compile(r'^\s*(fail_msg|success_msg): "\{\{ ([^{}]+?) \}\}"\s*$')
    for path in _yaml_files(*CODE_DIRS, "demo", "tests/offline", "tests/probes"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = single.match(line)
            if m:
                expr = m.group(2)
                assert re.search(r"to_json|to_nice_json|\bstring\b|join\(|_problems$|~|'", expr), \
                    "%s:%d %s may not be a str: %s" % (path.relative_to(ROOT), n, m.group(1), expr)


def test_problem_lists_render_as_lists():
    """`{% set out = [] %} ... {{ out }}` in a folded scalar: a line break before `{{ out }}` folds into a space, the
    result is the string ' [...]' and `| length == 0` never holds (review, 2026-11-20). Write `{{- out }}`."""
    for path in _yaml_files(*CODE_DIRS, "demo"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not re.match(r"^\s*\{\{ out \}\}\s*$", line), "%s:%d use {{- out }}" % (path.relative_to(ROOT), n)


# ------------------------------------------------------------------ FlashArray: everpure.flasharray modules only (owner 2026-11-20)
FA_REST = re.compile(r"fa_array|fu_array|pr_fa\b|dsgrow_flasharrays|api_version")
FA_PREFIX = "everpure.flasharray."
ALL_DIRS = CODE_DIRS + ["demo"]


def _fa_tasks(*dirs):
    for path in _yaml_files(*dirs):
        for task in _tasks(_load(path) or []):
            mod = next((k for k in task if str(k).startswith(FA_PREFIX)), None)
            if mod:
                yield path, mod[len(FA_PREFIX):], task[mod] or {}


def test_no_direct_flasharray_rest():
    """Every FlashArray call goes through the collection's modules; no ansible.builtin.uri to an array."""
    for path in _yaml_files(*ALL_DIRS):
        for task in _tasks(_load(path) or []):
            uri = task.get("ansible.builtin.uri")
            if uri:
                assert not FA_REST.search(str(uri.get("url", ""))), \
                    "%s: %s calls the FlashArray REST API directly" % (path.relative_to(ROOT), task.get("name"))


def test_execute_path_writes_the_array_once_and_only_grows():
    """playbooks/ and roles/ (the propose/execute/cleared path): purefa_info reads, and exactly one purefa_volume, with
    nothing but name and size (purefa_volume only grows: 1.46.0 ignores a smaller size)."""
    writes = []
    for path, mod, args in _fa_tasks("playbooks", "roles"):
        assert mod in ("purefa_info", "purefa_volume"), "%s: %s in the execute path" % (path.relative_to(ROOT), mod)
        if mod == "purefa_volume":
            writes.append(path)
            assert set(args) == {"fa_url", "api_token", "name", "size"}, (path, sorted(args))
    assert [p.relative_to(ROOT).as_posix() for p in writes] == ["roles/dsgrow_flasharray/tasks/extend.yml"]


def test_flasharray_disconnect_always_names_the_volume():
    """purefa_host / purefa_hg with state absent and no `volume` deletes the host or host group (1.46.0 line 1473)."""
    for path, mod, args in _fa_tasks(*ALL_DIRS):
        if mod in ("purefa_host", "purefa_hg") and args.get("state") == "absent":
            assert args.get("volume"), "%s: %s state: absent without volume" % (path.relative_to(ROOT), mod)


def test_flasharray_destroy_only_in_demo():
    for path, mod, args in _fa_tasks(*ALL_DIRS):
        rel = path.relative_to(ROOT).as_posix()
        if args.get("state") == "absent" or args.get("eradicate"):
            assert rel.startswith("demo/"), "%s: %s removes something outside demo/" % (rel, mod)
            assert mod in ("purefa_volume", "purefa_host", "purefa_hg"), (rel, mod)


def test_flasharrays_have_purity_name_and_stable_key():
    """name = the array's Purity name (shown); key = the stable alias that names its secret and env var, so a rename
    of `name` never orphans a token (owner 2026-11-20: z-x70-a -> z-fastaines-f)."""
    for inv in (ROOT / "inventory").glob("*/group_vars/dsgrow/flasharrays.yml"):
        arrays = (_load(inv) or {})["dsgrow_flasharrays"]
        for a in arrays:
            assert {"name", "key", "url", "token_env"} <= set(a), (inv, a)
        assert len({a["name"] for a in arrays}) == len(arrays), inv
        assert len({a["key"] for a in arrays}) == len(arrays), inv
    for rel in ("playbooks/group_vars/all/dsgrow.yml", "setup/deploy.yml", "setup/tasks/fa_user_one.yml"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "dsgrow_secret_fa_token_" in text, rel
        assert "fa_array.name | regex_replace" not in text and "map(attribute='name') | map('regex_replace'" not in text, \
            "%s builds a secret id from the array's name, not its key" % rel


def test_flasharray_patch_is_in_the_image():
    """everpure.flasharray 1.46.0 needs the #1103 fix: pinned together, applied and checked at build."""
    req = _load(ROOT / "docker/requirements.yml")["collections"]
    fa = [c for c in req if c["name"] == "everpure.flasharray"]
    assert fa and fa[0]["version"] == "1.46.0", "bump the patch (or drop it) together with the collection"
    dockerfile = (ROOT / "docker/eda.Dockerfile").read_text(encoding="utf-8")
    assert "everpure-flasharray-1.46.0-issue1103.patch" in dockerfile and "check_flasharray_patch.py" in dockerfile
    assert (ROOT / "docker/patches/everpure-flasharray-1.46.0-issue1103.patch").exists()


def _secret_headers(node):
    """True when a `headers` dict anywhere in the task sends a templated secret header."""
    if isinstance(node, dict):
        hdrs = node.get("headers")
        if isinstance(hdrs, dict) and any(k.lower() in ("api-token", "x-auth-token", "authorization")
                                          and "{{" in str(v) for k, v in hdrs.items()):
            return True
        return any(_secret_headers(v) for v in node.values())
    if isinstance(node, list):
        return any(_secret_headers(v) for v in node)
    return False


def test_tasks_with_secrets_are_no_log():
    found = 0
    for path in _yaml_files(*CODE_DIRS):
        for task in _tasks(_load(path) or []):
            if _secret_headers(task):
                found += 1
                assert task.get("no_log") is True, "%s: %s" % (path.relative_to(ROOT), task.get("name"))
    assert found >= 3, "the check must see the Slack and VCF Ops calls"


# ------------------------------------------------------------------ rulebook (rule 11)
def _rulebook():
    return _load(ROOT / "rulebooks" / "datastore_capacity.yml")[0]


def test_approvals_source_is_loopback_with_token_and_hmac():
    src = {s["name"]: s for s in _rulebook()["sources"]}
    ap = src["approvals"]["ansible.eda.webhook"]
    assert ap["host"] == "127.0.0.1"
    assert ap.get("token") and ap.get("hmac_secret")
    assert src["vcfops"]["ansible.eda.webhook"].get("token")


def test_execute_only_from_approvals():
    rules = _rulebook()["rules"]
    runs = [(r, r["action"]["run_playbook"]["name"]) for r in rules if "run_playbook" in r.get("action", {})]
    assert {name for _, name in runs} == {"playbooks/propose.yml", "playbooks/cleared.yml", "playbooks/execute.yml"}
    for rule, name in runs:
        cond = " ".join(rule["condition"].split())
        if name == "playbooks/execute.yml":
            assert 'event.meta.source.name == "approvals"' in cond
            assert 'event.payload.schema == "dsgrow.approval.v1"' in cond
            assert not rule["action"]["run_playbook"].get("retry"), "a write is never retried automatically"
        else:
            assert 'event.meta.source.name == "vcfops"' in cond
            assert 'event.payload.schema == "dsgrow.vcfops.v1"' in cond
    assert _rulebook().get("execution_strategy") == "sequential"
    assert "run_playbook" not in rules[-1].get("action", {}), "the catch-all must stay last and harmless"


def test_throttle_matches_policy():
    policy = _load(ROOT / "playbooks/group_vars/all/dsgrow.yml")
    propose = [r for r in _rulebook()["rules"] if r.get("throttle")][0]
    assert propose["throttle"]["once_within"] == policy["dsgrow_throttle"]


def test_alert_definitions_in_sync():
    policy = _load(ROOT / "playbooks/group_vars/all/dsgrow.yml")
    rb_vars = _load(ROOT / "rulebooks/vars.yml")
    assert policy["dsgrow_alert_definitions"] == rb_vars["dsgrow_alert_definitions"]


def test_policy_caps_within_vmfs6():
    policy = _load(ROOT / "playbooks/group_vars/all/dsgrow.yml")
    assert policy["dsgrow_max_volume_bytes"] <= 64 * 1024 ** 4
    for inv in (ROOT / "inventory").glob("*/group_vars/dsgrow/*.yml"):
        data = _load(inv) or {}
        if "dsgrow_max_volume_bytes" in data:
            assert data["dsgrow_max_volume_bytes"] <= 64 * 1024 ** 4, inv
        if "dsgrow_size_step_bytes" in data:
            assert data["dsgrow_size_step_bytes"] % (1024 ** 3) == 0, inv


# ------------------------------------------------------------------ execute.yml re-validates before the first write
def test_execute_order():
    text = (ROOT / "playbooks/execute.yml").read_text(encoding="utf-8")
    order = ["The event is an approval from the bridge", "The request propose.yml wrote",
             "The approval matches the request", "Resolve the datastore again", "Preflight with the approved target",
             "Extend the FlashArray volume", "Rescan, expand and verify VMFS"]
    positions = [text.index(name) for name in order]
    assert positions == sorted(positions)


def test_expand_is_guarded_by_mount_check():
    text = (ROOT / "roles/dsgrow_vmfs/tasks/main.yml").read_text(encoding="utf-8")
    assert text.index("The expand host mounts") < text.index("community.vmware.vmware_host_datastore")
    assert "auto_expand: true" in text


REMOVING_STATES = {"absent", "detached", "unmounted", "forgotten"}
# what names the object a removal acts on, per module (the rest of its arguments don't choose the target)
TARGET_ARGS = ("name", "uuid", "volume", "vmfs_uuid", "device", "path")


def test_demo_removes_only_its_own_objects():
    """CLAUDE.md rule 8, demo exception: demo/ removes only the inventory's demo objects (dd.*: dsgrow_demo), never
    a name written in the task, and every VM removal is by the instance UUID init recorded, not by name alone."""
    seen = 0
    for path in _yaml_files("demo"):
        for task in _tasks(_load(path) or []):
            mod = next((k for k in task if k not in ("name", "when", "loop", "loop_control", "register", "vars",
                                                      "changed_when", "failed_when", "check_mode", "no_log",
                                                      "until", "retries", "delay", "tags", "become", "args")), None)
            args = task.get(mod) if isinstance(task.get(mod), dict) else {}
            if args.get("state") not in REMOVING_STATES and not args.get("eradicate"):
                continue
            seen += 1
            where = "%s: %s" % (path.relative_to(ROOT), task.get("name"))
            if mod == "ansible.builtin.file":
                assert args.get("path") == "{{ dd_state_file }}", where
                continue
            targets = [t for k in TARGET_ARGS if k in args
                       for t in (args[k] if isinstance(args[k], list) else [args[k]])]
            assert targets, where
            for t in targets:
                assert re.fullmatch(r"\{\{ (dd|dd_rec)[.\w]* \}\}|\{\{ item \}\}", str(t)), \
                    "%s targets %r" % (where, t)
            if "{{ item }}" in targets:
                # the demo's ESXi hosts, or the host objects init recorded it connected the volume to
                assert str(task.get("loop")) in ("{{ dd.esx_hosts }}", "{{ dd_rec.attach.hosts | default([]) }}"), where
            if mod == "community.vmware.vmware_guest":
                assert args.get("use_instance_uuid") is True and "uuid" in args and args.get("force") is True, where
    assert seen >= 6, "the check must see the demo's removals (cleanup)"


# ------------------------------------------------------------------ HBA rescans only where a device changed
SCANHBA = "community.vmware.vmware_host_scanhba"


def test_scanhba_always_says_whether_it_rescans_hbas():
    """vmware_host_scanhba's rescan_hba defaults to true: a task that means a storage or VMFS refresh would also rescan
    every HBA (owner 2026-11-21: a needless rescan when start.yml added the virtual disk). Each task states it."""
    missing = []
    for path in _yaml_files(*ALL_DIRS):
        for task in _tasks(_load(path) or []):
            args = task.get(SCANHBA)
            if args is not None and "rescan_hba" not in (args or {}):
                missing.append("%s: %s" % (path.relative_to(ROOT), task.get("name")))
    assert not missing, missing


def test_start_never_rescans_hbas():
    """demo/start.yml adds a VMDK (a file, not a device): no host scan or host storage refresh at all, only the
    datastore's own refresh (owner 2026-11-21: no "Refresh host storage system" tasks during start)."""
    tasks = list(_tasks(_load(ROOT / "demo" / "start.yml")))
    assert not [t.get("name") for t in tasks if SCANHBA in t]
    assert len([t for t in tasks if "dsgrow_datastore_refresh" in t]) == 2


# Owner 2026-11-21 (skill vmfs-datastore-expand "When to rescan"): an HBA rescan only when a LUN is connected, resized or
# disconnected; a VMFS rescan only when a LUN is attached. The tasks allowed to do each, by file and task name.
HBA_RESCAN_ALLOWED = {
    ("demo/init.yml", "Rescan the hosts (the new device)"),                         # LUN connected
    ("roles/dsgrow_vmfs/tasks/main.yml", "Rescan HBAs on every host that mounts {{ dsgrow_datastore_name }}"),  # resized
    ("demo/cleanup.yml", "Rescan so the hosts drop the device"),                    # LUN disconnected
}
VMFS_RESCAN_ALLOWED = {("demo/init.yml", "The other hosts mount it (rescan VMFS)")}   # LUN attached


def test_rescans_only_where_a_lun_changes():
    hba, vmfs = set(), set()
    for path in _yaml_files(*ALL_DIRS):
        for task in _tasks(_load(path) or []):
            args = task.get(SCANHBA)
            if args is None:
                continue
            key = (path.relative_to(ROOT).as_posix(), task.get("name"))
            if args.get("rescan_hba", True) is not False:
                hba.add(key)
            if args.get("rescan_vmfs", False) is not False:
                vmfs.add(key)
    assert hba <= HBA_RESCAN_ALLOWED, sorted(hba - HBA_RESCAN_ALLOWED)
    assert vmfs <= VMFS_RESCAN_ALLOWED, sorted(vmfs - VMFS_RESCAN_ALLOWED)


def test_execute_refreshes_only_lagging_hosts():
    """Owner 2026-11-21: after the expand, a refresh runs only for the hosts whose VMFS still lags (dsgrow_lagging),
    never for every mounting host. The HBA rescan KEEPS its storage refresh: without it the expand found no space
    (09:21 run failed verify; the 09:28 retry after a refresh grew the VMFS)."""
    tasks = [t for t in _tasks(_load(ROOT / "roles" / "dsgrow_vmfs" / "tasks" / "main.yml")) if SCANHBA in t]
    rescans = [t for t in tasks if t[SCANHBA].get("rescan_hba") is True]
    refreshes = [t for t in tasks if t[SCANHBA].get("rescan_hba") is False]
    assert len(rescans) == 1 and rescans[0][SCANHBA].get("refresh_storage") is True
    assert len(refreshes) == 1
    assert all(t["loop"] == "{{ dsgrow_lagging }}" and t[SCANHBA].get("rescan_vmfs") is False for t in refreshes)


# ------------------------------------------------------------------ public repo: no addresses in git
PRIVATE_IP = re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b")
NOT_IN_GIT = ("site.yml",)                    # inventory/<env>/group_vars/dsgrow/site.yml is gitignored
SKIP_DIRS = {".git", ".venv", ".state", "state", "__pycache__", ".pytest_cache", "out"}


def test_no_private_addresses_committed():
    """Owner 2026-11-21 (public repo): lab addresses live only in the gitignored site.yml (dsgrow_site); every
    committed file refers to them. Documentation uses RFC 5737 example addresses (192.0.2.x etc.)."""
    found = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if not path.is_file() or SKIP_DIRS & set(rel.parts) or path.name in NOT_IN_GIT:
            continue
        if path.name.startswith("w01_") or path.name == ".sync.env":      # gitignored local files
            continue
        if path.suffix not in (".yml", ".yaml", ".md", ".py", ".sh", ".j2", ".json", ".cfg", ".txt", ""):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if PRIVATE_IP.search(line):
                found.append("%s:%d" % (rel.as_posix(), n))
    assert not found, found


# ------------------------------------------------------------------ environment-neutral code (owner 2026-11-21)
LAB_SPECIFIC = re.compile(r"vcf9-1|/nvcf\b|nested-vcf|z-fastaines|z-x[79]0-a|everpure\.demo")
NEUTRAL_DIRS = ["playbooks", "roles", "rulebooks", "plugins", "bridge", "demo", "tools", "docker", "slack", "setup/tasks",
                "vcf-ops", "tests/probes", "tests/fixtures", "tests/offline", "tests/rulebook"]


def test_code_is_environment_neutral():
    """Lab specifics live in inventory/<env>/ (and docs); code, probes and fixtures name no lab, so the demo runs in any
    environment that meets the README's prerequisites."""
    found = []
    files = [ROOT / "compose.yaml", ROOT / "ansible.cfg"] + sorted((ROOT / "setup").glob("*.yml"))
    for d in NEUTRAL_DIRS:
        files += [p for p in (ROOT / d).rglob("*") if p.is_file()]
    for path in files:
        rel = path.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts) or path.name.startswith("w01_") or path.suffix in (".pyc", ".patch"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if LAB_SPECIFIC.search(line):
                found.append("%s:%d: %s" % (rel.as_posix(), n, line.strip()[:80]))
    assert not found, "\n".join(found)
