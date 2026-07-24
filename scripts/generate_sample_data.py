#!/usr/bin/env python3
"""열흘치 daily regression log 예시 데이터를 생성한다 (schema v2 — run 단위).

이 파일은 **시나리오 정의(run 타임라인 + 에피소드)**만 소유한다.
직렬화·diff·rollup·md 템플릿은 전부 logbook 공용 모듈을 호출한다 —
production 파이프라인(ingest_run / apply_mapping / build_rollup /
render_reviews)과 같은 쓰기 경로를 지나므로 예시 데이터가 스키마·템플릿과
어긋날 수 없다.

- RUN_TIMELINE: 날짜별 pegging run 목록 (전 구성 공통, pegging = FTL sha 1:1)
- Episode.onset: 해당 since 날짜의 몇 번째 run부터 fail인지 (0-based)
  → since_sha가 그 run의 pegging으로 기록된다

사용법: python3 scripts/generate_sample_data.py  (repo 루트에서 실행)
"""

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import logbook

REPO_ROOT = Path(__file__).resolve().parent.parent

DATES = [date(2026, 7, 14) + timedelta(days=i) for i in range(10)]


@dataclass
class Episode:
    """한 TC의 연속 fail 구간. until은 다시 pass가 된 날(비활성 시작일), None이면 계속 fail."""
    tc: str
    since: date
    until: date | None
    log_excerpt: str
    # agent 추정 필드 (신규 fail 분석 시점에 채워지고 이후 run으로 승계)
    suspect_sha: str | None = None
    confidence: str | None = None
    onset: int = 0  # since 날짜의 몇 번째 run부터 fail인지 (0-based)

    def active_on(self, d: date) -> bool:
        return self.since <= d and (self.until is None or d < self.until)

    def active_in_run(self, d: date, run_idx: int) -> bool:
        if not self.active_on(d):
            return False
        return run_idx >= self.onset if d == self.since else True


def d_(s: str) -> date:
    return date.fromisoformat(s)


def chronic(entries: list[tuple[str, str, str]]) -> list[Episode]:
    """윈도우 시작 전부터 계속 fail 중인 만성 케이스들."""
    return [Episode(tc, d_(s), None, log) for tc, s, log in entries]


# ---------------------------------------------------------------- cfg-a
# FTL full regression: TC 900개, 만성 fail이 20건 이상 깔려 있는 대형 구성.
CFG_A = chronic([
    ("TC_FTL_GC_003",       "2026-06-30", "ASSERT at gc_policy.c:88: victim_score < 0"),
    ("TC_FTL_GC_009",       "2026-07-04", "ASSERT at gc_policy.c:132: free_blk < reserve_min"),
    ("TC_FTL_WEAR_101",     "2026-07-02", "TIMEOUT after 1800s in wear_level_stress"),
    ("TC_FTL_WEAR_115",     "2026-06-27", "wear delta 412 > threshold 256 after 10k cycles"),
    ("TC_FTL_MAP_133",      "2026-07-09", "map entry crc mismatch, lpn=0x8821a"),
    ("TC_FTL_TRIM_060",     "2026-07-07", "trimmed range readback non-zero at lba=0x1f000"),
    ("TC_IO_RAND_034",      "2026-07-05", "MISCOMPARE lba=0x3f2a10 expected=0xAA got=0x00"),
    ("TC_IO_RAND_047",      "2026-07-11", "MISCOMPARE lba=0x11b204 expected=0x5A got=0xFF"),
    ("TC_IO_LAT_083",       "2026-07-03", "p99 latency 812ms > budget 300ms under mixed load"),
    ("TC_IO_QD1_101",       "2026-06-25", "IOPS 8.2k < floor 12k at qd1 rand read"),
    ("TC_META_SNAP_007",    "2026-06-21", "PANIC: snapshot replay mismatch, gen=42"),
    ("TC_META_JOURNAL_002", "2026-07-06", "journal replay stopped at seq=99182, tail corrupt"),
    ("TC_META_FMT_027",     "2026-07-10", "format with meta_v3 leaves stale sb copy"),
    ("TC_PWR_LOSS_112",     "2026-07-08", "recovery loop exceeded 3 retries after power cut"),
    ("TC_PWR_LOSS_127",     "2026-07-12", "unflushed window 48ms > spec 20ms on plp path"),
    ("TC_STRESS_LOOP_031",  "2026-06-29", "TIMEOUT after 3600s in mixed stress iteration 88"),
    ("TC_STRESS_LOOP_042",  "2026-07-01", "TIMEOUT after 3600s in mixed stress iteration 141"),
    ("TC_STRESS_THERM_055", "2026-07-05", "throttle oscillation, temp 87C > limit 85C"),
    ("TC_NVME_AER_071",     "2026-07-09", "AER completion missing after ns attach event"),
    ("TC_NVME_FMT_083",     "2026-07-11", "format nvm timeout, csts.rdy stuck 1"),
    ("TC_SEC_ERASE_014",    "2026-07-03", "crypto erase leaves readable region at lba=0x0"),
    ("TC_DBG_TRACE_090",    "2026-07-12", "trace buffer overrun drops 1.2% events under load"),
]) + [
    Episode("TC_PWR_LOSS_118", d_("2026-07-10"), d_("2026-07-18"),
            "spor recovery hang at stage 2, ftl_open() no return"),
    Episode("TC_FTL_MAP_141", d_("2026-07-15"), d_("2026-07-20"),
            "map rebuild reads stale l2p page after unclean shutdown",
            suspect_sha="9e12ab4", confidence="medium", onset=1),
    Episode("TC_IO_QD32_055", d_("2026-07-16"), None,
            "hang: qd32 rand write stalls at 97% completion",
            suspect_sha="4c77f02", confidence="unknown"),
    Episode("TC_META_JOURNAL_019", d_("2026-07-18"), None,
            "journal checkpoint skipped when gc pressure high"),
    Episode("TC_FTL_TRIM_072", d_("2026-07-19"), d_("2026-07-22"),
            "deallocate during gc migrates trimmed block, data resurrect"),
    Episode("TC_IO_MIXED_090", d_("2026-07-20"), None,
            "MISCOMPARE under 70/30 mixed after 2h, lba=0x29ff10",
            suspect_sha="b8d4310", confidence="medium"),
    # 2026-07-23 신규 3건 — 설계 문서 예시 시나리오
    Episode("TC_FTL_GC_017", d_("2026-07-23"), None,
            "ASSERT at gc_victim.c:412: invalid victim block state during gc",
            suspect_sha="a3f9c21", confidence="high", onset=1),
    Episode("TC_FTL_MAP_205", d_("2026-07-23"), None,
            "SEGV in map_cache_evict(), map_cache.c:207",
            suspect_sha="77d0e4f", confidence="high", onset=2),
    Episode("TC_IO_SEQ_078", d_("2026-07-23"), None,
            "IO latency spike 4200ms > budget 500ms in seq write",
            suspect_sha="c91b502", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-b
CFG_B = chronic([
    ("TC_NVME_FMT_009",    "2026-07-01", "format nvm with ses=1 returns invalid field"),
    ("TC_IO_LAT_QOS_021",  "2026-06-28", "qos class B p95 4.1ms > sla 2.0ms"),
    ("TC_META_GC_MIX_044", "2026-07-06", "gc during meta update drops one dirty page"),
    ("TC_WEAR_STATIC_130", "2026-07-11", "static wear leveling never triggers in 24h soak"),
    ("TC_PWR_CYCLE_202",   "2026-06-15", "device not ready within 5s after 500th cycle"),
]) + [
    Episode("TC_IO_FLUSH_067", d_("2026-07-15"), d_("2026-07-17"),
            "flush returns before nand program complete (write-through off)"),
    Episode("TC_NVME_RESET_033", d_("2026-07-17"), None,
            "controller reset during io leaves qpair zombie",
            suspect_sha="f0a9912", confidence="medium"),
    Episode("TC_MAP_REBUILD_058", d_("2026-07-21"), d_("2026-07-23"),
            "rebuild time 41s > budget 30s on 75% full drive"),
    Episode("TC_IO_WRCACHE_012", d_("2026-07-22"), None,
            "write cache disable ignored under sustained seq write",
            suspect_sha="5b21d9e", confidence="medium", onset=1),
]

# ---------------------------------------------------------------- cfg-c
CFG_C = [
    Episode("TC_SMOKE_BOOT_001", d_("2026-07-16"), d_("2026-07-18"),
            "boot-to-ready 6.2s > smoke budget 5s"),
    Episode("TC_IO_BASIC_005", d_("2026-07-21"), d_("2026-07-22"),
            "single seq write/read verify miscompare at lba=0x100"),
]

# ---------------------------------------------------------------- cfg-d
CFG_D = chronic([
    ("TC_GCS_HOT_COLD_012", "2026-07-03", "gc write amp 4.8 > limit 3.5 under hot/cold mix"),
    ("TC_GCS_URGENT_027",   "2026-06-26", "urgent gc misses deadline, host write stall 1.2s"),
    ("TC_GCS_RESERVE_031",  "2026-07-10", "reserve blocks below min watermark after loop 77"),
]) + [
    # cfg-a의 TC_FTL_GC_017과 같은 변경점(a3f9c21)이 의심되는 교차 구성 신호
    Episode("TC_GCS_VICTIM_044", d_("2026-07-23"), None,
            "victim pick loops on same block, gc starvation",
            suspect_sha="a3f9c21", confidence="medium", onset=1),
]

# ---------------------------------------------------------------- cfg-e
CFG_E = chronic([
    ("TC_PLR_SPOR_009", "2026-06-30", "spor iteration 412 mount fail, dirty map"),
    ("TC_PLR_CAP_021",  "2026-07-08", "cap discharge curve below spec at 85C"),
]) + [
    Episode("TC_PLR_NESTED_033", d_("2026-07-12"), d_("2026-07-20"),
            "nested power cut during replay corrupts journal tail"),
]

# ---------------------------------------------------------------- cfg-f
CFG_F = chronic([
    ("TC_PERF_SEQW_005",    "2026-07-06", "seq write 2.1GB/s < target 2.4GB/s"),
    ("TC_PERF_RNDR_018",    "2026-06-24", "rand read iops 388k < target 420k"),
    ("TC_PERF_LAT_QOS_030", "2026-07-09", "p99.99 latency 9.8ms > sla 8ms"),
    ("TC_PERF_SUSTAIN_041", "2026-07-01", "sustained write drops 38% after 30min, thermal"),
]) + [
    Episode("TC_PERF_MIXED_052", d_("2026-07-17"), d_("2026-07-19"),
            "mixed rw throughput dip after fw slot swap"),
]

# ---------------------------------------------------------------- cfg-g
CFG_G = chronic([
    ("TC_MJ_REPLAY_014", "2026-07-05", "replay time 8.4s > budget 5s on full journal"),
]) + [
    Episode("TC_MJ_CKPT_022", d_("2026-07-23"), None,
            "checkpoint skip when gc pressure high, journal wrap",
            suspect_sha="77d0e4f", confidence="medium", onset=2),
]

# ---------------------------------------------------------------- cfg-h (전 기간 green)
CFG_H: list[Episode] = []

# ---------------------------------------------------------------- cfg-i
CFG_I = chronic([
    ("TC_CMP_HOSTA_101", "2026-06-28", "host A hotplug: link retrain loop after s3 resume"),
    ("TC_CMP_HOSTB_115", "2026-07-02", "host B nvme timeout on admin q during boot storm"),
    ("TC_CMP_OSX_133",   "2026-07-07", "os X trim burst causes cmd timeout at qd64"),
    ("TC_CMP_BIOS_140",  "2026-06-22", "legacy bios: option rom hang on cold boot 1/50"),
    ("TC_CMP_VMD_152",   "2026-07-09", "vmd passthrough: msix vector loss after reset"),
]) + [
    Episode("TC_CMP_HOSTC_128", d_("2026-07-11"), d_("2026-07-21"),
            "host C aspm l1.2 entry storm drops io"),
]

# ---------------------------------------------------------------- cfg-j
CFG_J = chronic([
    ("TC_WLS_STATIC_007", "2026-07-04", "static wl not triggered in 24h soak"),
    ("TC_WLS_DELTA_019",  "2026-06-29", "erase count delta 512 > limit 384"),
]) + [
    Episode("TC_WLS_MIGRATE_028", d_("2026-07-19"), None,
            "wl migration collides with gc, double relocation"),
]

# ---------------------------------------------------------------- cfg-k (7/18 운영 시작)
CFG_K = [
    Episode("TC_THM_OSC_011", d_("2026-07-22"), None,
            "throttle oscillation 2Hz between P1/P3",
            suspect_sha="f31c807", confidence="unknown"),
    Episode("TC_THM_SENSOR_024", d_("2026-07-22"), None,
            "composite temp sensor stuck during thermal ramp"),
]

# ---------------------------------------------------------------- cfg-l (7/20 운영 중단)
CFG_L = chronic([
    ("TC_LGC_SAN_003", "2026-06-20", "sanitize block erase leaves residual mapping"),
    ("TC_LGC_SAN_017", "2026-07-08", "overwrite pass verify timeout at 96%"),
])

# 구성 생애주기 규칙:
# - id는 최초 부여 후 불변 (디렉토리명·시계열 연속성의 기준)
# - dobee 쪽 구성 이름 변경은 dobee_name/renamed_from 메타데이터로만 반영
# - since = 운영 시작일, retired = 운영 중단일(그날부터 미운영). 데이터 파일은
#   운영 기간에만 존재하고, 중단돼도 디렉토리·이력은 보존한다
CONFIGS = [
    {"id": "cfg-a", "label": "FTL full regression",  "total": 900, "episodes": CFG_A,
     "dobee_name": "ftl_full_regr_v2", "renamed_from": ["ftl_full_regr"]},
    {"id": "cfg-b", "label": "NVMe protocol suite",  "total": 420, "episodes": CFG_B},
    {"id": "cfg-c", "label": "smoke suite",          "total": 150, "episodes": CFG_C},
    {"id": "cfg-d", "label": "FTL GC stress",        "total": 300, "episodes": CFG_D},
    {"id": "cfg-e", "label": "power loss recovery",  "total": 250, "episodes": CFG_E},
    {"id": "cfg-f", "label": "IO performance",       "total": 500, "episodes": CFG_F},
    {"id": "cfg-g", "label": "metadata journal",     "total": 350, "episodes": CFG_G},
    {"id": "cfg-h", "label": "security & sanitize",  "total": 180, "episodes": CFG_H},
    {"id": "cfg-i", "label": "compat matrix",        "total": 600, "episodes": CFG_I},
    {"id": "cfg-j", "label": "wear leveling soak",   "total": 220, "episodes": CFG_J},
    {"id": "cfg-k", "label": "thermal & throttle",   "total": 280, "episodes": CFG_K,
     "since": "2026-07-18"},
    {"id": "cfg-l", "label": "legacy sanitize suite", "total": 190, "episodes": CFG_L,
     "retired": "2026-07-20"},
]


def op_on(cfg: dict, d: date) -> bool:
    """해당 일자에 구성이 운영 중인가 (since <= d < retired)."""
    if "since" in cfg and d < d_(cfg["since"]):
        return False
    if "retired" in cfg and d >= d_(cfg["retired"]):
        return False
    return True


def op_dates(cfg: dict, dates: list[date]) -> list[date]:
    return [d for d in dates if op_on(cfg, d)]


# 날짜별 pegging run 타임라인 — 전 구성 공통, (run_id, pegging_sha=FTL sha)
RUN_TIMELINE: dict[str, list[tuple[int, str]]] = {
    "2026-07-14": [(8601, "4e21c9b"), (8613, "a90f735")],
    "2026-07-15": [(8625, "d7a01f3"), (8637, "9e12ab4"), (8649, "4b8cc02")],
    "2026-07-16": [(8661, "1c9de55"), (8673, "7e30ba1")],
    "2026-07-17": [(8685, "f0a9912"), (8697, "3d51c7e"), (8709, "b92aa04")],
    "2026-07-18": [(8721, "6a8f1d0"), (8733, "e45b992")],
    "2026-07-19": [(8745, "2f7c6ae"), (8757, "8d19e03"), (8769, "5c02b4f")],
    "2026-07-20": [(8781, "b8d4310"), (8793, "0a63f17")],
    "2026-07-21": [(8805, "7b40d2c"), (8817, "c58e9f6"), (8829, "91a3d08")],
    "2026-07-22": [(8841, "9f4e2b7"), (8853, "5b21d9e")],
    "2026-07-23": [(8865, "c3d9e10"), (8877, "a3f9c21"), (8889, "77d0e4f")],
}


def seed_sha(tc: str) -> str:
    """윈도우 이전부터 fail이던 만성 케이스의 가상 유입 pegging (표시용)."""
    import hashlib
    return hashlib.md5(tc.encode()).hexdigest()[:7]


def main() -> None:
    manifest = {"schema_version": logbook.SCHEMA_VERSION, "configs": []}
    for cfg in CONFIGS:
        entry = {"id": cfg["id"], "label": cfg["label"], "total": cfg["total"]}
        for key in ("dobee_name", "renamed_from", "since", "retired"):
            if key in cfg:
                entry[key] = cfg[key]
        manifest["configs"].append(entry)
    logbook.write_json(REPO_ROOT / "results" / "configs.json", manifest)

    last_iso = DATES[-1].isoformat()
    for ci, cfg in enumerate(CONFIGS):
        ods = op_dates(cfg, DATES)
        # 윈도우 전 run의 가상 상태 — 만성 fail의 since/since_sha/추정 필드 시드
        prev_run = None
        if ods:
            day0 = ods[0] - timedelta(days=1)
            seeded = {ep.tc: {
                "status": "ongoing", "since": ep.since.isoformat(),
                "since_sha": seed_sha(ep.tc),
                "log_excerpt": ep.log_excerpt, "log_url": "",
                "suspect_sha": ep.suspect_sha, "confidence": ep.confidence,
            } for ep in cfg["episodes"] if ep.active_on(day0)}
            if seeded:
                prev_run = {"date": day0.isoformat(), "pegging_sha": "",
                            "failures": seeded}
        n_runs = 0
        for d in ods:
            iso = d.isoformat()
            for run_idx, (rid, sha) in enumerate(RUN_TIMELINE[iso]):
                active = [ep for ep in cfg["episodes"] if ep.active_in_run(d, run_idx)]
                facts = {ep.tc: {
                    "log_excerpt": ep.log_excerpt,
                    "log_url": f"https://dobee.example.internal/run/{rid}/tc/{ep.tc}",
                } for ep in active}
                run = logbook.build_run(cfg["id"], iso, rid, sha,
                                        cfg["total"], facts, prev_run)
                # 이 run에서 유입된 에피소드의 agent 추정 = production의 mapping.json 역할
                logbook.apply_entries(run, [
                    {"tc": ep.tc, "suspect_sha": ep.suspect_sha,
                     "confidence": ep.confidence}
                    for ep in active
                    if ep.since == d and ep.onset == run_idx and ep.suspect_sha])
                logbook.write_json(
                    logbook.run_path(REPO_ROOT, cfg["id"], iso, rid, sha), run)
                prev_run = run
                n_runs += 1
        meta = manifest["configs"][ci]
        rollup = logbook.write_rollup(REPO_ROOT, meta, updated=last_iso)
        print(f"{cfg['id']}: {n_runs} runs / {len(rollup['days'])} days, "
              f"fail {rollup['days'][0]['fail']} -> {rollup['days'][-1]['fail']}")

    # 리뷰 렌더링 — production의 render_reviews.py와 동일 경로
    daily_dir = REPO_ROOT / "reviews" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    for d in DATES:
        iso = d.isoformat()
        (daily_dir / f"{iso}.md").write_text(
            logbook.render_daily_md(REPO_ROOT, manifest, iso), encoding="utf-8")
    monthly_dir = REPO_ROOT / "reviews" / "monthly"
    monthly_dir.mkdir(parents=True, exist_ok=True)
    for ym in sorted({d.strftime("%Y-%m") for d in DATES}):
        (monthly_dir / f"{ym}.md").write_text(
            logbook.render_monthly_md(REPO_ROOT, manifest, ym), encoding="utf-8")
    print(f"wrote {len(DATES)} daily reports, "
          f"{len({d.strftime('%Y-%m') for d in DATES})} monthly reviews")


if __name__ == "__main__":
    main()
