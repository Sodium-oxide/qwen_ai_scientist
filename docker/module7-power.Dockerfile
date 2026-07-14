FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PYTHONIOENCODING=utf-8 \
    MPLBACKEND=Agg \
    HOME=/sandbox_home \
    USERPROFILE=/sandbox_home \
    ANDES_NCPUS=1 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gfortran \
        libopenblas-dev \
        liblapack-dev \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /sandbox_home /tmp/matplotlib \
    && chmod 777 /sandbox_home /tmp/matplotlib

COPY docker/requirements-power.txt /tmp/requirements-power.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r /tmp/requirements-power.txt

RUN python - <<'PY'
import importlib.util
required = ["numpy", "scipy", "matplotlib", "pandapower", "andes", "ams", "cvxpy"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(f"Missing packages in module7 power image: {missing}")
PY

CMD ["python", "experiment.py"]
