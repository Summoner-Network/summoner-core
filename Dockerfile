###############################################################################
# Dockerfile – Summoner “splt” server (Python + Rust back-ends)               #
###############################################################################

############################
# Stage 1: build wheels
############################
FROM python:3.11-slim AS builder
ENV DEBIAN_FRONTEND=noninteractive

# system deps for compiling Rust libs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential curl git pkg-config && \
    rm -rf /var/lib/apt/lists/*

# Rust tool-chain
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

# wheel builder
RUN pip install --no-cache-dir --upgrade pip maturin setuptools wheel

WORKDIR /app
COPY . /app

# one shared wheelhouse for both crates
RUN mkdir -p /wheelhouse && \
    maturin build --release -m summoner/rust/rust_server_sdk_2/Cargo.toml -o /wheelhouse && \
    maturin build --release -m summoner/rust/rust_server_sdk_3/Cargo.toml -o /wheelhouse

############################
# Stage 2: runtime image
############################
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends libssl3 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ① install Rust wheels
COPY --from=builder /wheelhouse /tmp/wheels
RUN pip install --no-cache-dir /tmp/wheels/*.whl

# ② install Python package
COPY . /app
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir . && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# default config (override with -e at runtime)
ENV SUMMONER_CONFIG=/app/templates/server_config.json

EXPOSE 8888

# Copy entry-point and make it executable
COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py
RUN chmod +x /usr/local/bin/docker-entrypoint.py

# Default command
ENTRYPOINT ["python", "/usr/local/bin/docker-entrypoint.py"]
