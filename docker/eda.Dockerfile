# dsgrow-eda: ansible-rulebook (Java for drools) + ansible-core + the collections the playbooks use.
# Build on the run host with the host network (the lab DNS sits in 172.17/16, see nested-vcf compose.yaml):
#   docker build --network host -f docker/eda.Dockerfile -t dsgrow-eda:1 docker
FROM python:3.12-slim-bookworm

ARG DSGROW_UID=1000
ARG DSGROW_GID=1000

RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jdk-headless ca-certificates patch openssh-client \
 && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt requirements.yml check_flasharray_patch.py /tmp/dsgrow/
COPY patches/ /tmp/dsgrow/patches/
# everpure.flasharray 1.46.0 plus the issue #1103 fix (docker/patches/; drop it when a release ships the fix). The build
# fails when the patch does not apply cleanly or the check finds purefa_info volumes / purefa_volume still broken.
RUN pip install --no-cache-dir -r /tmp/dsgrow/requirements.txt \
 && ansible-galaxy collection install -r /tmp/dsgrow/requirements.yml -p /usr/share/ansible/collections \
 && patch -d /usr/share/ansible/collections/ansible_collections/everpure/flasharray -p1 --forward \
      --no-backup-if-mismatch < /tmp/dsgrow/patches/everpure-flasharray-1.46.0-issue1103.patch \
 && python /tmp/dsgrow/check_flasharray_patch.py /usr/share/ansible/collections \
 && pip freeze > /usr/share/dsgrow-pip-freeze.txt

RUN groupadd -g "${DSGROW_GID}" dsgrow && useradd -u "${DSGROW_UID}" -g "${DSGROW_GID}" -m dsgrow
USER dsgrow
WORKDIR /work
