# The public copy of HardRead: engine and web app, unprivileged, with no access to anything else on the machine.
# Build context: a clean export with foul-play/ beside app/ (see up.sh).
FROM python:3.13-slim
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends build-essential curl ca-certificates \
    && useradd --create-home --uid 10001 hardread
COPY app /srv/hardread/app
COPY tools /srv/hardread/tools
COPY foul-play /srv/hardread/foul-play
ENV RUSTUP_HOME=/opt/rustup CARGO_HOME=/opt/cargo
RUN curl -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.89.0 --profile minimal \
    && python -m venv /opt/fp \
    && PATH=/opt/cargo/bin:$PATH /opt/fp/bin/pip install -q -r /srv/hardread/foul-play/requirements.txt \
    && pip install -q fastapi==0.128.8 uvicorn==0.39.0 \
    && rm -rf /opt/rustup /opt/cargo \
    && apt-get purge -y -qq build-essential curl && apt-get autoremove -y -qq && rm -rf /var/lib/apt/lists/* \
    && chown -R root:root /srv/hardread && chmod -R a-w /srv/hardread
ENV HARDREAD_PUBLIC=1 HARDREAD_FP_PYTHON=/opt/fp/bin/python HARDREAD_APP_STATE=/state HARDREAD_EXPLAIN_MODE=auto \
    HARDREAD_QUEUE_CAP=5 HARDREAD_RATE_REVIEWS=6 HARDREAD_RATE_VERDICTS=6 HARDREAD_RATE_EXPLAIN=20 \
    PYTHONDONTWRITEBYTECODE=1
USER hardread
WORKDIR /srv/hardread
EXPOSE 8080
CMD ["python", "app/server.py", "--host", "0.0.0.0", "--port", "8080", "--concurrency", "1"]
