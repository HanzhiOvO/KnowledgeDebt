# 可选的本地 ASR 服务：whisper.cpp 自带 HTTP 服务。
# 未在交接环境验证（交接机器没有 Docker）；首次部署请按 README.md 验证接口与格式。
# 只在私网使用，不要把该端口暴露到公网。
FROM debian:bookworm-slim

ARG WHISPER_CPP_REF=v1.7.4
ENV DEBIAN_FRONTEND=noninteractive \
    WHISPER_MODEL=/models/ggml-medium.bin \
    WHISPER_LANGUAGE=zh \
    WHISPER_THREADS=4

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential cmake git ca-certificates libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN git clone --depth 1 --branch "${WHISPER_CPP_REF}" https://github.com/ggml-org/whisper.cpp.git \
 && cmake -S whisper.cpp -B whisper.cpp/build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build whisper.cpp/build -j"$(nproc)" --target whisper-server \
 && install -m755 whisper.cpp/build/bin/whisper-server /usr/local/bin/whisper-server \
 && rm -rf /opt/whisper.cpp

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
