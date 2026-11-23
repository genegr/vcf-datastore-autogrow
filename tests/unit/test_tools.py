"""The run-host tooling (tools/*.sh): the gate that keeps probes read-only and setup steps confirmed one by one, and the
sync that must not copy over a datastore in flight. The real scripts run in a sandbox copy with a fake `docker` (it only
records its arguments) and a fake `ssh` (it runs the command locally), so no host, lab or container is touched.

Needs bash: skipped without it.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")

FAKE_DOCKER = '#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$FAKE_DOCKER_LOG"\n'
# <host> <command...>: runs the command in the fake remote home (one word = a shell command line, as ssh does)
FAKE_SSH = ('#!/usr/bin/env bash\nshift\ncd "$FAKE_REMOTE_HOME" || exit 255\n'
            'if [ $# -eq 1 ]; then bash -c "$1"; else "$@"; fi\n')


def _exe(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def box(tmp_path):
    """A repo-shaped sandbox with the real tools/ scripts in it."""
    root = tmp_path / "repo"
    (root / "tools").mkdir(parents=True)
    for script in ("env.sh", "probe.sh", "setup.sh", "sync.sh", "lock_check.sh"):
        shutil.copyfile(ROOT / "tools" / script, root / "tools" / script)
    for rel in ("tests/probes/ok.yml", "setup/ok.yml", "setup/other.yml", "demo/ok.yml", "environments/lab1/probes/ok.yml"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("---\n", encoding="utf-8")
    (root / "environments" / "example" / "inventory").mkdir(parents=True)
    (root / ".env").write_text("DSGROW_ENV=example\n", encoding="utf-8")
    (root / ".state").mkdir()
    (root / ".state" / "vault-pass").write_text("not-a-secret\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "docker", FAKE_DOCKER)
    _exe(bin_dir / "ssh", FAKE_SSH)
    (tmp_path / "remote").mkdir()
    return root


def run(box, script, *args, **env):
    full = dict(os.environ, PATH="%s%s%s" % (box.parent / "bin", os.pathsep, os.environ["PATH"]),
                FAKE_DOCKER_LOG=str(box.parent / "docker.log"), FAKE_REMOTE_HOME=str(box.parent / "remote"))
    full.pop("SYNC_FORCE", None)
    full.update(env)
    return subprocess.run([BASH, str(box / "tools" / script), *args], cwd=str(box), env=full, capture_output=True,
                          text=True, timeout=120)


def docker_args(box):
    log = box.parent / "docker.log"
    return log.read_text(encoding="utf-8").split("\n") if log.exists() else None


# ====================================================================== probe.sh: read-only, one file of the allow-list
def test_a_probe_runs_in_the_container(box):
    proc = run(box, "probe.sh", "tests/probes/ok.yml", "-e", "dsgrow_x=1", "-vv")
    assert proc.returncode == 0, proc.stderr
    args = docker_args(box)
    assert "tests/probes/ok.yml" in args and "-vv" in args and "dsgrow_x=1" in args


@pytest.mark.parametrize("pair", ["pr_ds=ds01", "dsgrow_vcfops_interval_min=5", "dd_x=1"])
def test_the_projects_own_variables_may_be_set(box, pair):
    assert run(box, "probe.sh", "tests/probes/ok.yml", "-e", pair).returncode == 0
    assert pair in docker_args(box)


def test_a_lab_probe_is_allowed(box):
    assert run(box, "probe.sh", "environments/lab1/probes/ok.yml").returncode == 0


@pytest.mark.parametrize("path", [
    "tests/probes/../../setup/ok.yml",     # the glob tests/probes/*.yml used to match this: a setup step, ungated
    "tests/probes/../ok.yml", "tests/probes/sub/ok.yml", "tests/probes//ok.yml", "setup/ok.yml", "/etc/passwd.yml",
    "environments/lab1/../lab1/probes/ok.yml", "environments/lab1/probes/../../../setup/ok.yml",
    "tests/probes/ok.yml/..", "tests/probes/.yml", "tests/probes/-x.yml", "tests/probes/nothere.yml",
])
def test_probe_refuses_a_path_outside_the_allow_list(box, path):
    proc = run(box, "probe.sh", path)
    assert proc.returncode != 0, "accepted %s" % path
    assert docker_args(box) is None, "the container started for %s" % path


@pytest.mark.parametrize("extra", [["setup/ok.yml"], ["tests/probes/ok.yml"], ["-e", "@setup/vars.yml"],
                                   ["-e", '{"x": 1}'], ["-i", "other.ini"], ["-c", "ssh"], ["--vault-password-file", "x"],
                                   ["-e", "ansible_connection=ssh"], ["-e", "ansible_host=203.0.113.9"],
                                   ["-e", "ansible_python_interpreter=/work/.state/x"], ["-e", "other_thing=1"],
                                   ["-e"], ["--tags"], ["-e", "a=b", "setup/other.yml"], ["--check"]])
def test_probe_refuses_extra_arguments_that_could_run_another_playbook(box, extra):
    proc = run(box, "probe.sh", "tests/probes/ok.yml", *extra)
    assert proc.returncode != 0, "accepted %s" % extra
    assert docker_args(box) is None


def test_probe_refuses_a_symlink(box):
    link = box / "tests" / "probes" / "link.yml"
    try:
        link.symlink_to(box / "setup" / "ok.yml")
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    assert run(box, "probe.sh", "tests/probes/link.yml").returncode != 0 and docker_args(box) is None


# ====================================================================== setup.sh: a dry run unless --confirm names the file
def test_setup_is_a_dry_run_without_confirm(box):
    proc = run(box, "setup.sh", "setup/ok.yml")
    assert proc.returncode == 0, proc.stderr
    args = docker_args(box)
    assert "--check" in args and "--diff" in args and "setup/ok.yml" in args


def test_setup_runs_for_real_only_with_the_files_own_name(box):
    assert run(box, "setup.sh", "setup/ok.yml", "--confirm", "ok.yml").returncode == 0
    assert "--check" not in docker_args(box)
    (box.parent / "docker.log").unlink()
    assert run(box, "setup.sh", "setup/ok.yml", "--confirm", "other.yml").returncode != 0
    assert docker_args(box) is None
    assert run(box, "setup.sh", "setup/ok.yml", "--confirm").returncode != 0


def test_setup_passes_the_documented_extra_variable(box):
    """setup/vcfops_interval.yml's rollback: -e dsgrow_vcfops_interval_min=5."""
    assert run(box, "setup.sh", "setup/ok.yml", "--confirm", "ok.yml", "-e", "dsgrow_vcfops_interval_min=5").returncode == 0
    assert "dsgrow_vcfops_interval_min=5" in docker_args(box)


@pytest.mark.parametrize("path", ["setup/../tests/probes/ok.yml", "setup/sub/ok.yml", "tests/probes/ok.yml", "setup/nothere.yml",
                                  "demo/../setup/ok.yml", "/work/setup/ok.yml"])
def test_setup_refuses_a_path_outside_the_allow_list(box, path):
    assert run(box, "setup.sh", path).returncode != 0 and docker_args(box) is None


@pytest.mark.parametrize("extra", [["setup/other.yml"], ["--confirm", "ok.yml", "setup/other.yml"], ["-e", "@x.yml"],
                                   ["-i", "x"], ["--ask-vault-pass"]])
def test_setup_refuses_a_second_playbook_or_a_connection_option(box, extra):
    """A second path used to reach ansible-playbook beside the one that was confirmed, or was run unconfirmed."""
    proc = run(box, "setup.sh", "setup/ok.yml", *extra)
    assert proc.returncode != 0 and docker_args(box) is None, (extra, proc.stderr)


# ====================================================================== lock_check.sh / sync.sh: never over a datastore in flight
def lock(box_or_dir, status, expires_in, name="vc__ds"):
    d = pathlib.Path(box_or_dir) / "state" / "locks"
    d.mkdir(parents=True, exist_ok=True)
    (d / (name + ".json")).write_text('{\n  "rid": "0b5e6f1c",\n  "status": "%s",\n  "expires": %d\n}\n'
                                      % (status, int(time.time()) + expires_in), encoding="utf-8")


@pytest.mark.parametrize("status,expires_in,busy", [
    ("executing", 3600, True), ("proposed", 3600, True), ("cleaning", 3600, True),
    ("proposed", -10, False),                                    # expired: nobody is waiting on it any more
    ("released", 3600, False), ("failed", 3600, False), ("cleared", 3600, False), ("denied", 3600, False),
])
def test_lock_check(tmp_path, status, expires_in, busy):
    live = tmp_path / "live"
    lock(live, status, expires_in)
    proc = subprocess.run([BASH, str(ROOT / "tools" / "lock_check.sh"), str(live)], capture_output=True, text=True)
    assert proc.returncode == (3 if busy else 0), proc.stderr


@pytest.mark.parametrize("text,busy", [
    ('{"rid": "x", "status": "proposed", "expires": "%d"}', True),            # a quoted number is still a time
    ('{"rid": "x", "status": "proposed"}', True),                              # no expiry: cannot tell when it ends
    ('{"rid": "x", "status": "EXECUTING", "expires": %d}', True),              # another case
    ('{"rid": "x", "status": "proposed", "expires": "soon"}', True),
    ('{"rid": "x", "stat', True), ("", True), ("not json at all", True),       # truncated or unreadable: fail closed
    ('{"rid": "x", "status": "mystery", "expires": %d}', True),                # a status nobody knows
    ('{"rid": "x", "status": "released", "expires": 0}', False), ('{"rid": "x", "status": "denied"}', False),
])
def test_lock_check_fails_closed_on_files_it_cannot_read(tmp_path, text, busy):
    locks = tmp_path / "live" / "state" / "locks"
    locks.mkdir(parents=True)
    (locks / "vc__ds.json").write_text(text % (int(time.time()) + 3600) if "%d" in text else text, encoding="utf-8")
    proc = subprocess.run([BASH, str(ROOT / "tools" / "lock_check.sh"), str(tmp_path / "live")], capture_output=True, text=True)
    assert proc.returncode == (3 if busy else 0), (text, proc.stderr)


def test_an_executing_lock_gets_an_hour_of_grace_after_its_expiry(tmp_path):
    for expires_in, busy in ((-600, True), (-7200, False)):
        live = tmp_path / ("live%d" % expires_in)
        lock(live, "executing", expires_in)
        proc = subprocess.run([BASH, str(ROOT / "tools" / "lock_check.sh"), str(live)], capture_output=True, text=True)
        assert proc.returncode == (3 if busy else 0), (expires_in, proc.stderr)


def test_only_the_exact_value_1_forces_a_sync(box):
    sync_env(box)
    lock(box.parent / "remote" / "live", "executing", 3600)
    for value in ("0", "no", "true", ""):
        assert run(box, "sync.sh", SYNC_FORCE=value).returncode == 3, value
    assert run(box, "sync.sh", SYNC_FORCE="1").returncode == 0


def test_lock_check_without_a_state_directory(tmp_path):
    assert subprocess.run([BASH, str(ROOT / "tools" / "lock_check.sh"), str(tmp_path)]).returncode == 0


def sync_env(box):
    (box / ".sync.env").write_text("SYNC_HOST=user@run-host\nSYNC_DIR=live\n", encoding="utf-8")


def test_sync_copies_when_nothing_is_in_flight(box):
    sync_env(box)
    proc = run(box, "sync.sh")
    assert proc.returncode == 0, proc.stderr
    assert (box.parent / "remote" / "live" / "tools" / "env.sh").exists()


@pytest.mark.parametrize("status", ["executing", "proposed"])
def test_sync_refuses_over_a_datastore_in_flight(box, status):
    sync_env(box)
    lock(box.parent / "remote" / "live", status, 3600)
    proc = run(box, "sync.sh")
    assert proc.returncode == 3, proc.stderr
    assert not (box.parent / "remote" / "live" / "tools").exists(), "files were copied anyway"


def test_sync_can_be_forced_by_the_owner(box):
    sync_env(box)
    lock(box.parent / "remote" / "live", "executing", 3600)
    assert run(box, "sync.sh", SYNC_FORCE="1").returncode == 0
    assert (box.parent / "remote" / "live" / "tools" / "env.sh").exists()


def test_sync_stops_when_the_host_does_not_answer(box):
    sync_env(box)
    _exe(box.parent / "bin" / "ssh", "#!/usr/bin/env bash\nexit 255\n")
    proc = run(box, "sync.sh")
    assert proc.returncode == 3 and not (box.parent / "remote" / "live").exists()


def test_a_scratch_directory_is_not_blocked_by_the_live_locks(box):
    """The scratch copy used to run tests on the host is another directory: only ITS locks count."""
    sync_env(box)
    lock(box.parent / "remote" / "live", "executing", 3600)
    assert run(box, "sync.sh", SYNC_DIR="scratch").returncode == 0
    assert (box.parent / "remote" / "scratch" / "tools" / "env.sh").exists()


# ====================================================================== Windows -> Linux: no carriage returns in scripts
def test_no_script_or_dockerfile_carries_a_carriage_return():
    """A CR after the shebang or a quote breaks a script on Linux ('bash\\r: No such file'); .gitattributes says eol=lf."""
    bad = []
    for pattern in ("tools/*.sh", "docker/*", "*.sh", "compose.yaml", ".env.example"):
        for path in ROOT.glob(pattern):
            if path.is_file() and path.suffix != ".patch" and b"\r" in path.read_bytes():   # .patch is -text: exact bytes
                bad.append(str(path.relative_to(ROOT)))
    assert not bad, "CR bytes in %s" % sorted(set(bad))
