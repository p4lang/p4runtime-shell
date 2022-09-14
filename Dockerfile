FROM ubuntu:22.04 AS deps

SHELL ["/bin/bash", "-c"]
ENV PKG_DEPS python3 python3-venv git
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
    pip3 install --upgrade wheel && \
    pip3 install . && \
    rm -rf ~/.cache/pip

FROM ubuntu:22.04
LABEL maintainer="P4 Developers <p4-dev@lists.p4.org>"
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
