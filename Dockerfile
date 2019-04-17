FROM p4lang/third-party:stable AS deps

SHELL ["/bin/bash", "-c"]

COPY . /p4runtime-sh/
WORKDIR /p4runtime-sh/

ENV PKG_DEPS git python3 python3-venv
ENV VENV /p4runtime-sh/venv

RUN apt-get update && \
    apt-get install -y --no-install-recommends $PKG_DEPS && \
    rm -rf /var/cache/apt/* /var/lib/apt/lists/*

RUN python3 -m venv $VENV && \
    source $VENV/bin/activate && \
    pip3 install --upgrade pip && \
    pip3 install --upgrade setuptools && \
    pip3 install -r requirements.txt && \
    rm -rf ~/.cache/pip

ENV PROTO_DIR /p4runtime-sh/p4runtime/proto
ENV GOOGLE_PROTO_DIR /p4runtime-sh/googleapis
ENV PROTOS="$PROTO_DIR/p4/v1/p4data.proto \
$PROTO_DIR/p4/v1/p4runtime.proto \
$PROTO_DIR/p4/config/v1/p4info.proto \
$PROTO_DIR/p4/config/v1/p4types.proto \
$GOOGLE_PROTO_DIR/google/rpc/status.proto \
$GOOGLE_PROTO_DIR/google/rpc/code.proto"
ENV PROTOFLAGS "-I$GOOGLE_PROTO_DIR -I$PROTO_DIR"
ENV PROTO_BUILD_DIR /p4runtime-sh/py_out

RUN source $VENV/bin/activate && \
    mkdir -p $PROTO_BUILD_DIR && \
    git clone --depth 1 https://github.com/googleapis/googleapis.git $GOOGLE_PROTO_DIR && \
    protoc $PROTOS --python_out $PROTO_BUILD_DIR $PROTOFLAGS \
        --grpc_out $PROTO_BUILD_DIR --plugin=protoc-gen-grpc=$(which grpc_python_plugin) && \
    touch $PROTO_BUILD_DIR/__init__.py $PROTO_BUILD_DIR/p4/__init__.py \
        $PROTO_BUILD_DIR/p4/v1/__init__.py $PROTO_BUILD_DIR/p4/config/__init__.py \
        $PROTO_BUILD_DIR/p4/config/v1/__init__.py $PROTO_BUILD_DIR/google/__init__.py \
        $PROTO_BUILD_DIR/google/rpc/__init__.py && \
    rm -rf $GOOGLE_PROTO_DIR

# google.rpc import fails without this, need to figure out why exactly
RUN source $VENV/bin/activate && \
    SITE_PACKAGES=$(python -c "import site; print(site.getsitepackages()[0])") && \
    cp -r $PROTO_BUILD_DIR/google/* $SITE_PACKAGES/google/

RUN echo "export PYTHONPATH=\"$PROTO_BUILD_DIR\"" >> $VENV/bin/activate

FROM ubuntu:16.04
LABEL maintainer="Antonin Bas <antonin@barefootnetworks.com>"
LABEL description="A shell based on ipython3 for P4Runtime"

# Any easy way to avoid installing these packages again?
ENV PKG_DEPS python3 python3-venv
ENV VENV /p4runtime-sh/venv

RUN apt-get update && \
    apt-get install -y --no-install-recommends $PKG_DEPS && \
    rm -rf /var/cache/apt/* /var/lib/apt/lists/*

COPY --from=deps /p4runtime-sh/ /p4runtime-sh/

WORKDIR /p4runtime-sh/

ENTRYPOINT ["/p4runtime-sh/docker_entry_point.sh"]
