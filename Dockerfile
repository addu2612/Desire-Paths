# One container: FalkorDB + the Desire Paths API/UI.
FROM falkordb/falkordb:latest

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/python

WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
COPY web ./web
COPY data ./data
COPY start.sh ./
RUN chmod +x start.sh

ENV PORT=8000
EXPOSE 8000
ENTRYPOINT []
CMD ["./start.sh"]
