"""The vcfops source's bind address is DSGROW_EDA_VCFOPS_BIND (compose.yaml passes the run host's .env value; it is not a
tracked file, which tools/sync.sh would overwrite), and the approvals
source never leaves loopback. Starts the REAL rulebook twice (its own module: the ports 5080/5081 are fixed, so the
module fixture of test_rulebook.py must be finished first) and asks the OS which addresses answer. Everything in
127.0.0.0/8 is local to the container's own network namespace, so 127.0.0.2 stands for "another interface".

Run with `docker run --network none`, like test_rulebook.py. Skipped where ansible-rulebook is not installed.
"""
import os
import pathlib
import shutil
import socket
import subprocess
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
pytestmark = pytest.mark.skipif(shutil.which("ansible-rulebook") is None, reason="ansible-rulebook not installed")


def _answers(host, port):
    with socket.socket() as s:
        s.settimeout(2)
        return s.connect_ex((host, port)) == 0


def _wait(host, port, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        if _answers(host, port):
            return
        time.sleep(0.5)
    raise TimeoutError("%s:%d not listening" % (host, port))


def _start(tmp_path, bind):
    env = dict(os.environ, DSGROW_EDA_VCFOPS_TOKEN="t1", DSGROW_EDA_APPROVALS_TOKEN="t2", DSGROW_APPROVAL_HMAC_SECRET="s",
               DSGROW_EDA_VCFOPS_BIND=bind, DSGROW_TEST_CALLS=str(tmp_path / "calls.jsonl"))
    env.pop("ANSIBLE_CONFIG", None)
    for port in (5080, 5081):
        assert not _answers("127.0.0.1", port), "port %d is in use: run with docker --network none" % port
    log = open(tmp_path / "rulebook.log", "w")
    proc = subprocess.Popen(
        ["ansible-rulebook", "-r", str(ROOT / "rulebooks" / "datastore_capacity.yml"), "-i", str(HERE / "inventory.yml"),
         "--vars", str(ROOT / "rulebooks" / "vars.yml"), "--env-vars",
         "DSGROW_EDA_VCFOPS_TOKEN,DSGROW_EDA_APPROVALS_TOKEN,DSGROW_APPROVAL_HMAC_SECRET,DSGROW_EDA_VCFOPS_BIND"],
        cwd=str(HERE), env=env, stdout=log, stderr=subprocess.STDOUT)
    _wait("127.0.0.1", 5080)
    _wait("127.0.0.1", 5081)
    return proc, log


def _stop(proc, log):
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


def test_the_default_listens_on_every_interface(tmp_path):
    proc, log = _start(tmp_path, "0.0.0.0")
    try:
        assert _answers("127.0.0.2", 5080), "0.0.0.0 must answer on another local address"
        assert not _answers("127.0.0.2", 5081), "the approvals source is loopback only, always"
    finally:
        _stop(proc, log)


def test_a_narrower_bind_address_is_honoured(tmp_path):
    proc, log = _start(tmp_path, "127.0.0.1")
    try:
        assert _answers("127.0.0.1", 5080)
        assert not _answers("127.0.0.2", 5080), "the vcfops source still answers on an address it was not told to use"
        assert not _answers("127.0.0.2", 5081)
    finally:
        _stop(proc, log)
