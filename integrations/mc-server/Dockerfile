FROM rust:1.86-slim AS builder
WORKDIR /build

COPY integrations/mc-server/Cargo.toml integrations/mc-server/Cargo.lock ./integrations/mc-server/
RUN mkdir -p integrations/mc-server/src && \
    echo 'fn main() {}' > integrations/mc-server/src/main.rs && \
    echo 'pub fn stub() {}' > integrations/mc-server/src/lib.rs
RUN cargo build --release --manifest-path integrations/mc-server/Cargo.toml

COPY integrations/mc-server ./integrations/mc-server
RUN touch integrations/mc-server/src/main.rs integrations/mc-server/src/lib.rs && \
    cargo build --release --manifest-path integrations/mc-server/Cargo.toml

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /build/integrations/mc-server/target/release/mc-server /usr/local/bin/mc-server

EXPOSE 8008
ENTRYPOINT ["mc-server"]
CMD ["--serve"]
