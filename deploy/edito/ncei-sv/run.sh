#!/usr/bin/env bash
# =============================================================================
# Sv for selected days of an NCEI EK80 cruise, on an EDITO GPU node
# =============================================================================
# Runs inside the Denoise Lab worker image as a Kubernetes Job (job.yaml). Each
# day: download the raw files from NCEI, compute Sv (stages 1-4, no denoising)
# into s3://$OCEANSTREAM_S3_BUCKET/$S3_PREFIX/sv/<day>/, delete the local copy.
#
# The pipeline comes from oceanstream-cli at $BRANCH, not from the image. CuPy is
# installed at start, so echopype's CUDA paths run when a GPU is present.
#
# INSTALL_CUPY=0 and REQUIRE_GPU=0 run it on CPU only, e.g. on a large-memory node
# without a GPU request.
#
# On $COMPARE_CPU_DAY, Sv is computed three times from the same converted files:
# GPU (keeping them), CPU only, then GPU again. The last two do identical work,
# so their times compare like for like, and their outputs must agree.
#
# CTD_URL (optional, MODE=sv): a CCHDO CF netCDF of the cruise's CTD casts. Sv then takes
# sound speed and absorption from the casts (--ctd-file). SV_CONTAINER names the Sv
# container (default sv) and PIPELINE_ARGS adds pipeline flags, in every mode.
#
# MODE=denoise skips the downloads and runs the pipeline from stage 5 on the stored
# Sv with $PRESET into the container denoised-$PRESET, through echograms, then writes
# per-segment statistics (split_stats.py) for each day.
#
# Logs, per-run metrics and the GPU/CPU comparison go to
# s3://$OCEANSTREAM_S3_BUCKET/$S3_PREFIX/sv-runs/<run id>/.
# =============================================================================
set -uo pipefail

MODE="${MODE:-sv}"
: "${CRUISE:?}" "${DAYS:?}"
if [ "$MODE" = sv ]; then : "${SHIP:?}" "${NAV_URL:?}"
elif [ "$MODE" = report ]; then : "${REPORT_RUNS:?}"
else : "${PRESET:?}"; fi
# MODE=report renders echogram_report.py for each day: the stored Sv and each
# "label=container" run in $REPORT_RUNS (';'-separated), frequency differences from
# $REPORT_MVBS (a container) if set, into sv-runs/<run id>/report/<day>/.
# MODE=stats reruns only split_stats.py for each preset in $PRESET (comma-separated).
: "${OCEANSTREAM_S3_BUCKET:?}" "${AWS_ENDPOINT_URL:?}"
BRANCH="${BRANCH:-main}"
PREFIX="${S3_PREFIX:-r2r/$CRUISE}"
COMPARE_CPU_DAY="${COMPARE_CPU_DAY:-}"
REQUIRE_GPU="${REQUIRE_GPU:-1}"
W="${WORK_DIR:-/work}"
# The pod name keeps runs that start in the same second apart.
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${HOSTNAME:-run}"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
LOGS="$W/logs"
mkdir -p "$W/raw" "$W/gps/$CRUISE" "$LOGS" "$W/pylib"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
fetch() { python -u "$SCRIPTS/ncei_fetch.py" "$@"; }
upload_logs() {
    fetch put "$LOGS" --bucket "$OCEANSTREAM_S3_BUCKET" --key "$PREFIX/sv-runs/$RUN_ID" >/dev/null 2>&1 \
        || log "log upload failed"
}

# -- samplers: pod memory and GPU, every 10 s ----------------------------------------
mem_now() { cat /sys/fs/cgroup/memory.current 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes; }
gpu_now() {
    nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null \
        | head -1 | tr -d ' ' || echo "-,-"
}
( while true; do echo "$(date +%s) $(mem_now) $(gpu_now)" >> "$LOGS/samples.txt"; sleep 10; done ) &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null; upload_logs' EXIT

# Peak memory and GPU use between two epoch seconds, from the samples.
summarise() {
    python - "$LOGS/samples.txt" "$1" "$2" <<'PY'
import json, sys
t0, t1 = int(sys.argv[2]), int(sys.argv[3])
mem, util, gmem = [], [], []
for line in open(sys.argv[1]):
    parts = line.split()
    if len(parts) < 2 or not t0 <= int(parts[0]) <= t1:
        continue
    mem.append(int(parts[1]))
    if len(parts) > 2 and parts[2] not in ("-,-", "-"):
        u, m = parts[2].split(",")
        util.append(float(u)); gmem.append(float(m))
print(json.dumps({
    "max_mem_gib": round(max(mem) / 2**30, 1) if mem else None,
    "gpu_util_mean": round(sum(util) / len(util), 1) if util else None,
    "gpu_util_max": max(util) if util else None,
    "gpu_mem_max_mib": max(gmem) if gmem else None,
}))
PY
}

# -- code and CuPy ---------------------------------------------------------------------
log "oceanstream-cli $BRANCH"
# Retries: a node's DNS can fail for minutes at a time.
CURL=(curl -fsSL --retry 8 --retry-delay 15 --retry-all-errors --connect-timeout 20)
"${CURL[@]}" -o "$W/src.tar.gz" "https://github.com/OceanStreamIO/oceanstream-cli/archive/refs/heads/$BRANCH.tar.gz" \
    && tar -xzf "$W/src.tar.gz" -C "$W" || { log "could not download $BRANCH"; exit 2; }
SRC="$(ls -d "$W"/oceanstream-cli-* | head -1)"
COMMIT="$("${CURL[@]}" "https://api.github.com/repos/OceanStreamIO/oceanstream-cli/commits/$BRANCH" \
    | python -c 'import json,sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null || echo unknown)"
export PYTHONPATH="$SRC:$W/pylib"
PIPELINE="$SRC/scripts/batch_processing/process_from_raw.py"

if [ "${INSTALL_CUPY:-1}" = "1" ]; then
NUMPY_MAJOR="$(python -c 'import numpy; print(numpy.__version__.split(".")[0])')"
if [ "$NUMPY_MAJOR" -ge 2 ]; then CUPY="cupy-cuda12x==14.2.0"; else CUPY="cupy-cuda12x==13.6.0"; fi
# CUDA 12.2 libraries to match the node driver (535 = CUDA 12.2); --no-deps keeps the
# image's NumPy and everything else untouched.
python -m pip install -q --no-cache-dir --no-deps --target "$W/pylib" "$CUPY" fastrlock \
    cuda-pathfinder==1.8.3 "nvidia-cuda-runtime-cu12==12.2.*" "nvidia-cuda-nvrtc-cu12==12.2.*" \
    "nvidia-cufft-cu12==11.0.8.*" "nvidia-cublas-cu12==12.2.*" "nvidia-curand-cu12==10.3.3.*" \
    "nvidia-nvjitlink-cu12==12.2.*" "nvidia-cusparse-cu12==12.1.2.*" "nvidia-cusolver-cu12==11.5.2.*" \
    || log "CuPy install failed"
LD_LIBRARY_PATH="$(ls -d "$W"/pylib/nvidia/*/lib 2>/dev/null | tr '\n' ':')${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH
fi

python - > "$LOGS/environment.json" <<'PY'
import json, platform
info = {"python": platform.python_version()}
try:
    import numpy, xarray, echopype
    info.update(numpy=numpy.__version__, xarray=xarray.__version__, echopype=echopype.__version__)
    from echopype.utils import gpu
    info["echopype_gpu_module"] = True
    info["echopype_has_cuda"] = gpu.has_cuda()
    import cupy as cp
    from cupyx.scipy.signal import fftconvolve
    x = cp.random.random(1 << 20, dtype=cp.float32)
    fftconvolve(x[:65536], x[:512]).sum().item()
    props = cp.cuda.runtime.getDeviceProperties(0)
    info.update(cupy=cp.__version__, gpu=props["name"].decode(),
                cuda_runtime=cp.cuda.runtime.runtimeGetVersion(),
                cuda_driver=cp.cuda.runtime.driverGetVersion(), gpu_ok=True)
except Exception as e:  # noqa: BLE001 - reported, then judged below
    info.update(gpu_ok=False, error=f"{type(e).__name__}: {e}")
print(json.dumps(info))
PY
cat "$LOGS/environment.json"
if ! grep -q '"gpu_ok": true' "$LOGS/environment.json" || ! grep -q '"echopype_has_cuda": true' "$LOGS/environment.json"; then
    log "GPU preflight failed"
    [ "$REQUIRE_GPU" = "1" ] && exit 3
fi
printf '{"run_id": "%s", "branch": "%s", "commit": "%s", "days": "%s"}\n' \
    "$RUN_ID" "$BRANCH" "$COMMIT" "$DAYS" > "$LOGS/run.json"

# -- navigation ------------------------------------------------------------------------
if [ "$MODE" = sv ]; then
    log "navigation"
    fetch nav --url "$NAV_URL" --md5 "${NAV_MD5:-}" --dest "$W/gps/$CRUISE" | tee "$LOGS/nav.json" \
        || { log "navigation failed"; exit 4; }
    fetch put "$W/gps/$CRUISE" --bucket "$OCEANSTREAM_S3_BUCKET" --key "$PREFIX/nav" || exit 4
fi
CTD_ARGS=()
if [ "$MODE" = sv ] && [ -n "${CTD_URL:-}" ]; then
    log "CTD casts"
    mkdir -p "$W/ctd"
    curl -fsSL --retry 5 --retry-all-errors -o "$W/ctd/casts.nc" "$CTD_URL" \
        || { log "CTD download failed"; exit 4; }
    sha256sum "$W/ctd/casts.nc" | tee "$LOGS/ctd.sha256"
    CTD_ARGS=(--ctd-file "$W/ctd/casts.nc")
fi

# -- Sv -------------------------------------------------------------------------------
# run_sv DAY CONTAINER TAG [extra pipeline args]; appends a line to metrics.jsonl.
# TAG "cpu" hides the GPU from the pipeline and its Dask workers.
run_sv() {
    local day="$1" container="$2" tag="$3"; shift 3
    local t0 t1 rc envp=()
    [ "$tag" = cpu ] && envp=(env CUDA_VISIBLE_DEVICES=)
    t0="$(date +%s)"
    "${envp[@]+"${envp[@]}"}" python -u "$PIPELINE" --cruise-id "$CRUISE" --start-date "$day" --end-date "$day" \
        --expected-categories short_pulse --force \
        --raw-source local --raw-dir "$W/raw/$day" --raw-cache-dir "$W/cache/raw" \
        --storage-backend s3 --s3-bucket "$OCEANSTREAM_S3_BUCKET" --s3-prefix "$PREFIX" \
        --s3-endpoint-url "$AWS_ENDPOINT_URL" --s3-region "${AWS_DEFAULT_REGION:-}" \
        --output-container "$container" --stop-after-stage 4 \
        --waveform-mode CW --encode-mode complex --sv-float32 \
        --sv-range-bin-m "${SV_RANGE_BIN_M:-0.18}" --chunk-ping-time "${CHUNK_PING_TIME:-100}" \
        --n-workers "${DASK_WORKERS:-2}" --memory-limit "${DASK_MEMORY:-20GB}" \
        --gps-dir "$W/gps/$CRUISE" "$@" > "$LOGS/$day-$tag.log" 2>&1
    rc=$?
    t1="$(date +%s)"
    printf '{"day": "%s", "run": "%s", "exit": %d, "seconds": %d, "usage": %s}\n' \
        "$day" "$tag" "$rc" "$((t1 - t0))" "$(summarise "$t0" "$t1")" | tee -a "$LOGS/metrics.jsonl"
    tail -5 "$LOGS/$day-$tag.log"
    return "$rc"
}

# run_denoise DAY: stages 5-9 from the stored Sv; appends to metrics.jsonl.
# DENOISED_CONTAINER keeps runs of one preset with different pipeline flags apart.
DENOISED="${DENOISED_CONTAINER:-denoised-${PRESET:-}}"
run_denoise() {
    local day="$1" t0 t1 rc
    t0="$(date +%s)"
    python -u "$PIPELINE" --cruise-id "$CRUISE" --start-date "$day" --end-date "$day" \
        --expected-categories short_pulse --force \
        --storage-backend s3 --s3-bucket "$OCEANSTREAM_S3_BUCKET" --s3-prefix "$PREFIX" \
        --s3-endpoint-url "$AWS_ENDPOINT_URL" --s3-region "${AWS_DEFAULT_REGION:-}" \
        --resume-stage 5 --sv-source-container "${SV_CONTAINER:-sv}" --output-container "$DENOISED" \
        --preset "$PRESET" --emit-denoise-diagnostics --stop-after-stage 9 \
        --skip-nasc --skip-pmtiles --skip-combined-echograms --skip-campaign-echograms \
        --n-workers "${DASK_WORKERS:-2}" --memory-limit "${DASK_MEMORY:-20GB}" \
        ${PIPELINE_ARGS:-} > "$LOGS/$day-denoise.log" 2>&1
    rc=$?
    t1="$(date +%s)"
    printf '{"day": "%s", "run": "denoise", "preset": "%s", "denoised": "%s", "args": "%s", "exit": %d, "seconds": %d, "usage": %s}\n' \
        "$day" "$PRESET" "$DENOISED" "${PIPELINE_ARGS:-}" "$rc" "$((t1 - t0))" "$(summarise "$t0" "$t1")" \
        | tee -a "$LOGS/metrics.jsonl"
    tail -5 "$LOGS/$day-denoise.log"
    return "$rc"
}

FAILED=0
if [ "$MODE" = report ]; then
    for day in ${DAYS//,/ }; do
        log "== $day: echograms"
        python -u "$SCRIPTS/echogram_report.py" --root "$OCEANSTREAM_S3_BUCKET/$PREFIX" --day "$day" \
            --runs "$REPORT_RUNS" --sv-container "${SV_CONTAINER:-sv}" --mvbs-container "${REPORT_MVBS:-}" \
            --mvbs-label "${REPORT_MVBS_LABEL:-$REPORT_MVBS}" --out "$LOGS/report/$day" \
            > "$LOGS/$day-report.log" 2>&1 || FAILED=1
        tail -3 "$LOGS/$day-report.log"
        upload_logs
    done
    log "done, failed=$FAILED"
    exit "$FAILED"
fi
if [ "$MODE" = stats ]; then
    for preset in ${PRESET//,/ }; do
        for day in ${DAYS//,/ }; do
            log "== $day: stats for $preset"
            python -u "$SCRIPTS/split_stats.py" --root "$OCEANSTREAM_S3_BUCKET/$PREFIX" --day "$day" \
                --sv-container "${SV_CONTAINER:-sv}" --denoised-container "denoised-$preset" \
                --out "$LOGS/$preset--$day-segments.json" || FAILED=1
        done
    done
    upload_logs
    log "done, failed=$FAILED"
    exit "$FAILED"
fi
if [ "$MODE" = denoise ]; then
    for day in ${DAYS//,/ }; do
        log "== $day: denoise with $PRESET"
        if run_denoise "$day"; then
            python -u "$SCRIPTS/split_stats.py" --root "$OCEANSTREAM_S3_BUCKET/$PREFIX" --day "$day" \
                --sv-container "${SV_CONTAINER:-sv}" --denoised-container "$DENOISED" \
                --out "$LOGS/$day-segments.json" || FAILED=1
        else
            FAILED=1
        fi
        upload_logs
    done
    log "done, failed=$FAILED"
    exit "$FAILED"
fi

for day in ${DAYS//,/ }; do
    log "== $day: download"
    if ! fetch raw --ship "$SHIP" --cruise "$CRUISE" --day "$day" --dest "$W/raw/$day" \
            | tee "$LOGS/$day-raw.json"; then
        FAILED=1; continue
    fi
    if [ "$day" = "$COMPARE_CPU_DAY" ]; then
        log "== $day: Sv on GPU, keeping converted files"
        run_sv "$day" sv gpu-convert --keep-intermediate || FAILED=1
        log "== $day: Sv on CPU from the converted files"
        run_sv "$day" sv-cpu-check cpu --keep-intermediate --reuse-converted || FAILED=1
        log "== $day: Sv on GPU from the converted files"
        run_sv "$day" sv gpu --reuse-converted || FAILED=1
        fetch compare "$OCEANSTREAM_S3_BUCKET/$PREFIX/sv/$day" \
            "$OCEANSTREAM_S3_BUCKET/$PREFIX/sv-cpu-check/$day" \
            --out "$LOGS/$day-gpu-vs-cpu.json" --delete-b || FAILED=1
    else
        log "== $day: Sv"
        # shellcheck disable=SC2086 # PIPELINE_ARGS is a word list
        run_sv "$day" "${SV_CONTAINER:-sv}" "$( [ "${REQUIRE_GPU}" = "1" ] && echo gpu || echo sv )" \
            "${CTD_ARGS[@]+"${CTD_ARGS[@]}"}" ${PIPELINE_ARGS:-} || FAILED=1
    fi
    rm -rf "$W/raw/$day" "$W/cache"
    upload_logs
done

log "done, failed=$FAILED"
exit "$FAILED"
