"""CLAUDE.md rule 8 and the read-only promises, checked mechanically on the parsed YAML (not by spelling).

The old text scan (test_repo_safety.py) looks for a few literal patterns in four directories. This one parses every task
file, names the module of every task and holds each directory to a REVIEWED list:

  playbooks/ roles/   the propose / execute / deny / cleared path: only the modules below, a URL only to Slack, files only
                      under the state directory, the one FlashArray write and the one vSphere expand
  setup/              the gated steps: no removal
  tests/probes/ and environments/*/probes/    read only: GET, plus the token / login calls that only authenticate
  demo/               the one exception of rule 8, checked by test_repo_safety.py::test_demo_removes_only_its_own_objects

A new module, a new removal or a new HTTP method fails here until someone adds it to the list below, in the open, in a
diff the safety review reads. Skipped where PyYAML is missing, like test_repo_safety.py.
"""
import pathlib
import re
import shlex

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP_PARTS = {"group_vars", "defaults", "vars", "inventory", "templates", "stubs", "files"}
KEYWORDS = {"name", "when", "loop", "loop_control", "register", "vars", "changed_when", "failed_when", "check_mode",
            "no_log", "tags", "delegate_to", "become", "environment", "until", "retries", "delay", "notify", "args",
            "with_items", "run_once", "ignore_errors", "any_errors_fatal", "diff", "connection", "collections", "async",
            "poll", "throttle", "module_defaults", "listen", "timeout", "become_user", "ignore_unreachable"}
CUSTOM_MODULES = {"dsgrow_datastore_backing", "dsgrow_datastore_refresh", "esx_vmfs_volume", "cluster_ha_heartbeat_info",
                  "cluster_ha_heartbeat_pref"}
B = "ansible.builtin."

EXECUTE_PATH_MODULES = {                      # playbooks/ and roles/
    B + "assert", B + "debug", B + "fail", B + "include_role", B + "include_tasks", B + "meta", B + "set_fact",
    B + "set_stats",
    B + "copy", B + "file", B + "slurp", B + "stat",                       # the request files and locks, under state/
    B + "uri",                                                             # Slack only
    "everpure.flasharray.purefa_info",                                     # the reads
    "everpure.flasharray.purefa_volume",                                   # THE write: the grow, to absolute bytes
    "community.vmware.vmware_host_scanhba",                                # rescan and storage refresh
    "community.vmware.vmware_host_datastore",                              # the VMFS expand (auto_expand)
    "dsgrow_datastore_backing",                                            # read only
}
SETUP_MODULES = {
    B + "assert", B + "command", B + "copy", B + "debug", B + "file", B + "find", B + "include_tasks", B + "include_vars",
    B + "set_fact", B + "stat", B + "unarchive", B + "uri", B + "include_role", B + "fail", B + "slurp", B + "meta",
    "community.vmware.vcenter_folder", "community.vmware.vmware_about_info", "community.vmware.vmware_deploy_ovf",
    "community.vmware.vmware_guest", "community.vmware.vmware_guest_info", "community.vmware.vmware_local_role_manager",
    "community.vmware.vmware_object_role_permission", "everpure.flasharray.purefa_info",
    "everpure.flasharray.purefa_user",
}
PROBE_MODULES = {
    B + "assert", B + "copy", B + "debug", B + "file", B + "include_role", B + "include_tasks", B + "set_fact",
    B + "shell", B + "uri", B + "fail", B + "stat", B + "slurp", B + "add_host", B + "raw",
    "community.vmware.vmware_about_info", "community.vmware.vmware_dvs_portgroup_info",
    "community.vmware.vmware_guest_disk_info",
    "community.vmware.vmware_guest_info", "community.vmware.vmware_host_facts", "community.vmware.vsphere_file",
    "dsgrow_datastore_backing", "everpure.flasharray.purefa_info", "cluster_ha_heartbeat_info",
}
REMOVING_STATES = {"absent", "detached", "unmounted", "forgotten", "removed", "destroyed", "deleted"}
# (file, module, what): the only removals outside demo/, each with the reason it is accepted. A new one needs the
# owner's decision, not a quiet edit.
REMOVAL_EXCEPTIONS = {
    # The rollback of setup/vcfops.yml: it deletes only the VCF Operations symptom / alert definitions, rules, templates
    # and instance that THIS repo created (by name), never a vCenter or FlashArray object.
    ("setup/vcfops_cleanup.yml", B + "uri", "DELETE"),
}
# probes that talk to a FlashArray's REST API themselves (the rule is "through the collection's modules, never direct
# REST"): a lab's own read-only investigation, listed so that it stays the only one.
DIRECT_REST_PROBES = {"environments/vcf9-1/probes/eui_serial_array.yml"}
AUTH_ONLY = ("/auth/token/acquire", "/auth/token/release", "/login", "/logout")


def _files(*dirs):
    for d in dirs:
        base = ROOT / d
        if base.exists():
            for p in sorted(list(base.rglob("*.yml")) + list(base.rglob("*.yaml"))):
                if not SKIP_PARTS & set(p.relative_to(ROOT).parts[:-1]):
                    yield p


def _probe_files():
    yield from _files("tests/probes")
    for d in sorted((ROOT / "environments").glob("*/probes")):
        yield from _files(str(d.relative_to(ROOT)))


def _tasks(node):
    if isinstance(node, list):
        for x in node:
            yield from _tasks(x)
    elif isinstance(node, dict):
        blocks = ("block", "rescue", "always", "tasks", "pre_tasks", "post_tasks", "handlers")
        if any(k in node for k in blocks):
            for k in blocks:
                yield from _tasks(node.get(k, []))
        else:
            yield node


def _module(task):
    """The module of a task: its first key that is not a task keyword. A bare name (shell:, file:, action:) counts as a
    module too, so it shows up in the lists below instead of being skipped."""
    keys = [k for k in task if k not in KEYWORDS]
    return keys[0] if keys else None


_FREE_FORM = re.compile(r"""(\w+)=("[^"]*"|'[^']*'|\S+)""")


def _args_of(task, mod):
    """The module's arguments as a dict: a mapping as it is, a free-form string (`path=/x state=absent`) parsed, plus the
    task-level `args:`."""
    value = task[mod]
    args = dict(value) if isinstance(value, dict) else {}
    if isinstance(value, str):
        args.update({k: v.strip("\"'") for k, v in _FREE_FORM.findall(value)})
    if isinstance(task.get("args"), dict):
        args.update(task["args"])
    return args


def _all(files):
    """(relative path, task, module name, module args dict) for every task."""
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):      # a variables file (secrets.example.yml), not a playbook or a task file
            continue
        for task in _tasks(data):
            if isinstance(task, dict) and _module(task):
                mod = _module(task)
                yield path.relative_to(ROOT).as_posix(), task, mod, _args_of(task, mod)


def _removals(files):
    for rel, task, mod, args in _all(files):
        method = str(args.get("method", "")).upper()
        if str(args.get("state", "")) in REMOVING_STATES or method == "DELETE" or args.get("eradicate") \
                or args.get("force") is True:
            yield rel, task, mod, method or str(args.get("state", "")) or "eradicate/force"


# ====================================================================== which modules each part of the repo may use
def test_the_execute_path_uses_only_reviewed_modules():
    seen = {mod for _, _, mod, _ in _all(_files("playbooks", "roles"))}
    assert seen, "the check must see the playbooks"
    assert seen <= EXECUTE_PATH_MODULES, "new module(s) in the execute path: %s" % sorted(seen - EXECUTE_PATH_MODULES)


def test_setup_steps_use_only_reviewed_modules():
    seen = {mod for _, _, mod, _ in _all(_files("setup"))}
    assert seen and seen <= SETUP_MODULES, "new module(s) in setup/: %s" % sorted(seen - SETUP_MODULES)


def test_probes_use_only_reviewed_modules():
    seen = {mod for _, _, mod, _ in _all(_probe_files())}
    assert seen and seen <= PROBE_MODULES, "new module(s) in the probes: %s" % sorted(seen - PROBE_MODULES)


def test_the_destructive_custom_modules_stay_in_the_demo():
    """esx_vmfs_volume can unmount, detach and forget a VMFS; the heartbeat module changes cluster HA settings."""
    for rel, _, mod, _ in _all(_files("playbooks", "roles", "setup", "rulebooks") if False else
                               list(_files("playbooks", "roles", "setup")) + list(_probe_files())):
        assert mod not in ("esx_vmfs_volume", "cluster_ha_heartbeat_pref", "dsgrow_datastore_refresh"), "%s: %s" % (rel, mod)


def test_every_task_names_its_module_with_the_collection():
    """FQCN for every module (CLAUDE.md): a bare `shell:` or `file:`, `action:` or `local_action:` is how a task would
    slip past a list of FQCNs."""
    bare = [(rel, mod) for rel, _, mod, _ in _all(list(_files("playbooks", "roles", "setup", "demo")) + list(_probe_files()))
            if "." not in mod and mod not in CUSTOM_MODULES]
    assert not bare, "module without its collection: %s" % bare[:10]


SETUP_COMMANDS = {"ansible-vault": {"encrypt"}, "govc": {"sso.user.ls", "sso.user.create"}}


def test_the_commands_setup_runs_are_the_reviewed_ones():
    """setup/ may run `ansible-vault encrypt` and two govc calls (list and create the dsgrow SSO user): nothing else."""
    seen = 0
    for rel, task, mod, args in _all(_files("setup")):
        if mod in (B + "command", B + "shell"):
            seen += 1
            argv = args.get("argv") or shlex.split(str(task[mod]))
            assert argv and argv[0] in SETUP_COMMANDS, "%s runs %r" % (rel, argv[:2])
            assert argv[1] in SETUP_COMMANDS[argv[0]], "%s runs %r" % (rel, argv[:2])
    assert seen >= 3


# ====================================================================== rule 8: no removal outside demo/
def test_nothing_removes_anything_outside_the_demo_but_the_listed_rollback():
    files = list(_files("playbooks", "roles", "setup")) + list(_probe_files())
    found = {(rel, mod, what) for rel, _, mod, what in _removals(files)}
    assert found <= REMOVAL_EXCEPTIONS, "removal outside demo/: %s" % sorted(found - REMOVAL_EXCEPTIONS)
    assert found == REMOVAL_EXCEPTIONS, "an exception that no longer exists: %s" % sorted(REMOVAL_EXCEPTIONS - found)


def test_the_demo_is_the_only_place_that_removes_the_array_and_the_datastore():
    """purefa_volume / purefa_host / purefa_hg with state absent, esx_vmfs_volume detach: in demo/cleanup.yml only."""
    where = {rel for rel, _, mod, _ in _removals(_files("demo"))}
    assert where == {"demo/cleanup.yml"}, where


# ====================================================================== the execute path touches only what it should
def test_the_only_url_the_execute_path_calls_is_slack():
    seen = 0
    for rel, task, mod, args in _all(_files("playbooks", "roles")):
        if mod == B + "uri":
            seen += 1
            assert str(args.get("url", "")).startswith("{{ dsgrow_slack_api }}/"), "%s: %s" % (rel, args.get("url"))
            assert str(args.get("method", "GET")).upper() == "POST", rel
    assert seen >= 1


def test_the_execute_path_writes_files_only_under_the_state_directory():
    seen = 0
    for rel, task, mod, args in _all(_files("playbooks", "roles")):
        if mod in (B + "copy", B + "file"):
            seen += 1
            target = str(args.get("dest") or args.get("path"))
            assert target.startswith("{{ dsgrow_state_dir }}/"), "%s: %s writes %s" % (rel, task.get("name"), target)
            assert args.get("state", "file") in ("file", "directory") or mod == B + "copy", rel
    assert seen >= 3


# ====================================================================== probes are read only
def test_probes_only_read():
    seen = 0
    for rel, task, mod, args in _all(_probe_files()):
        if mod == B + "uri":
            seen += 1
            method = str(args.get("method", "GET")).upper()
            url = str(args.get("url", ""))
            assert method == "GET" or (method == "POST" and url.endswith(AUTH_ONLY)), \
                "%s: %s %s is not a read" % (rel, method, url)
        if mod == "community.vmware.vsphere_file":
            assert args.get("state") == "file", "%s: vsphere_file may only test for a file" % rel
        if mod == B + "file":
            assert args.get("state") == "directory", "%s: a probe may only create its output directory" % rel
    assert seen >= 10


# the only commands a probe may run on a host: list / get queries (the raw ESXi probes and the demo guest's df)
READ_COMMANDS = (re.compile(r"^esxcli storage [a-z ]+? list\b"), re.compile(r"^partedUtil get(ptbl|UsableSectors)\b"),
                 re.compile(r"^vmkfstools -Ph\b"), re.compile(r"^vmware -vl$"), re.compile(r"^uptime$"),
                 re.compile(r"^ls -la /vmfs/(volumes|devices/disks)/"),
                 re.compile(r'^set -o pipefail df -h (\{\{ [a-z_.]+ \}\}|\S+) 2>/dev/null \| tail -1 \|\| echo "[^"]*"$'))
CHAINING = (";", "&&", "||", "`", "$(", ">")


def test_probe_commands_only_query():
    seen = 0
    for rel, task, mod, args in _all(_probe_files()):
        if mod not in (B + "raw", B + "shell", B + "command"):
            continue
        loop = task.get("loop") or task.get("with_items")
        texts = [str(x) for x in loop] if isinstance(loop, list) else [" ".join(str(task[mod]).split())]
        for text in texts:
            seen += 1
            assert any(p.match(text) for p in READ_COMMANDS), "%s: %r is not a read-only query" % (rel, text)
            if isinstance(loop, list):   # a looped command is wrapped by the task ("{{ item }} 2>&1; echo rc=$?")
                assert not any(c in text for c in CHAINING), "%s: %r chains commands" % (rel, text)
    assert seen >= 5


def test_only_the_listed_probe_talks_to_a_flasharray_directly():
    direct = set()
    for rel, task, mod, args in _all(_probe_files()):
        if mod == B + "uri" and ("pr_fa" in str(args.get("url", "")) or "/api/api_version" in str(args.get("url", ""))):
            direct.add(rel)
    assert direct == DIRECT_REST_PROBES, direct
