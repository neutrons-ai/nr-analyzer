# Stage 1: Install pixi and create the conda environment with Mantid
FROM ubuntu:24.04 AS base

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies required by Mantid, Qt, and general tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libgl1 \
        libegl1 \
        libxkbcommon0 \
        libdbus-1-3 \
        libfontconfig1 \
    && rm -rf /var/lib/apt/lists/*

# Install pixi (conda-compatible package manager)
RUN curl -fsSL https://pixi.sh/install.sh | bash
ENV PATH="/root/.pixi/bin:${PATH}"

WORKDIR /app

# ---------- conda environment via pixi ----------
# Copy only the pixi manifest first so the environment layer is cached
COPY pixi.toml pixi.toml
RUN pixi install

# ---------- analyzer-tools (pip inside the pixi env) ----------
# Copy project files for pip install
COPY pyproject.toml README.md .env.example ./
COPY analyzer_tools/ analyzer_tools/
COPY tests/ tests/

# Install analyzer-tools (editable) and its dev extras inside the pixi env
RUN pixi run pip install -e ".[dev]"

# Stub out plot_publisher (SNS-internal web publishing module required by
# lr_reduction.output but not needed for offline reduction).
RUN SITE=$(pixi run python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])") && \
    mkdir -p "${SITE}/plot_publisher" && \
    printf '%s\n' \
        "\"\"\"Stub plot_publisher – satisfies lr_reduction imports.\"\"\"" \
        "" \
        "def publish_plot(*args, **kwargs):" \
        "    pass" \
        "" \
        "def plot1d(*args, **kwargs):" \
        "    pass" \
        > "${SITE}/plot_publisher/__init__.py"

# ---------- Mantid configuration ----------
# Set up Mantid so it can resolve bare run references (e.g. "REF_L_218386")
# by searching the mounted /app/data directory for .nxs.h5 files.
# First, trigger Mantid to create its default user properties file,
# then overwrite with our custom settings.
RUN pixi run python -c "from mantid.kernel import ConfigService; ConfigService.Instance()" && \
    printf '%s\n' \
        "datasearch.directories = /app/data" \
        "default.savedirectory = /app/results" \
        "default.facility = SNS" \
        "default.instrument = REF_L" \
        "datasearch.searcharchive = Off" \
        "network.github.api_token = " \
        "CheckMantidVersion.OnStartup = 0" \
        "UpdateInstrumentDefinitions.OnStartup = 0" \
        "logging.loggers.root.level = debug" \
        >> /root/.mantid/Mantid.user.properties

# Make container runnable as a non-root user
RUN chmod og+rwX -R /app /root
ENV HOME=/root

# Default to a shell inside the pixi environment
ENTRYPOINT ["pixi", "run"]
CMD ["bash"]


