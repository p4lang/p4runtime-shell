FROM ubuntu:20.04 AS deps

SHELL ["/bin/bash", "-c"]
ENV PKG_DEPS python3 python3-venv
ENV VENV /p4runtime-sh/venv

RUN apt-get update && \
    apt-get install -y --no-install-recommends $PKG_DEPS && \
    rm -rf /var/cache/apt/* /var/lib/apt/lists/*

COPY . /p4runtime-sh/
WORKDIR /p4runtime-sh/

RUN python3 -m venv $VENV && \
    source $VENV/bin/activate && \
    pip3 install --upgrade pip && \
    pip3 install --upgrade setuptools && \
    python3 setup.py install && \
    rm -rf ~/.cache/pip

FROM ubuntu:20.04
LABEL maintainer="Antonin Bas <antonin@barefootnetworks.com>"
LABEL description="A shell based on ipython3 for P4Runtime"

# Any easy way to avoid installing these packages again?
ENV PKG_DEPS python3
ENV VENV /p4runtime-sh/venv

RUN apt-get update && \
    apt-get install -y --no-install-recommends $PKG_DEPS && \
    rm -rf /var/cache/apt/* /var/lib/apt/lists/*

COPY --from=deps /p4runtime-sh/venv /p4runtime-sh/venv
COPY --from=deps /p4runtime-sh/docker_entry_point.sh /p4runtime-sh/docker_entry_point.sh

WORKDIR /p4runtime-sh

ENTRYPOINT ["/p4runtime-sh/docker_entry_point.sh"]
