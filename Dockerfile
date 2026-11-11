# Build and test with a pinned GCC, then ship only the benchmark binary.
FROM gcc:14 AS build
RUN apt-get update \
    && apt-get install -y --no-install-recommends cmake ninja-build git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY . .
RUN cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DLOB_WERROR=ON \
        -DCMAKE_EXE_LINKER_FLAGS="-static-libstdc++ -static-libgcc" \
    && cmake --build build \
    && ctest --test-dir build --output-on-failure

FROM debian:bookworm-slim
COPY --from=build /src/build/lob_bench /usr/local/bin/lob_bench
CMD ["lob_bench"]
