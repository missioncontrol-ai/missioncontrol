FROM rust:1.86-slim AS builder
WORKDIR /build

COPY integrations/mc/Cargo.toml integrations/mc/Cargo.lock ./integrations/mc/
RUN mkdir -p integrations/mc/src && echo 'fn main() {}' > integrations/mc/src/main.rs
RUN cargo build --release --manifest-path integrations/mc/Cargo.toml

COPY integrations/mc ./integrations/mc
COPY distribution/templates ./distribution/templates
COPY distribution/hooks ./distribution/hooks
RUN cargo build --release --manifest-path integrations/mc/Cargo.toml

FROM debian:bookworm-slim
RUN apt-get update     && apt-get install -y --no-install-recommends ca-certificates curl     && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/integrations/mc/target/release/mc /usr/local/bin/mc

ENTRYPOINT ["mc", "daemon"]
