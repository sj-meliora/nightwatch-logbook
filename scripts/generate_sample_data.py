#!/usr/bin/env python3
"""열흘치 daily regression log 예시 데이터를 생성한다.

실제 운영에서는 스케줄 워크플로우가 dobee 결과를 파싱해 데일리 파일을
커밋하지만, 이 스크립트는 대시보드/스키마 검증용 예시 데이터를 만든다.

원칙 (설계 문서와 동일):
- 데일리 파일은 생성 후 불변, JSON이 source of truth
- failures는 배열이 아닌 TC 이름 키 객체, 키 정렬 + pretty print
  → git diff가 TC 추가/제거를 사람이 읽을 수 있게 보여준다
- suspect_sha/author/confidence는 agent 추정 결과로, dobee 사실(로그)과 구분
- index.json은 데일리 파일에서 파생되는 rollup으로, 매일 재생성

사용법: python3 scripts/generate_sample_data.py  (repo 루트에서 실행)
"""

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "results"
SCHEMA_VERSION = 1

DATES = [date(2026, 7, 14) + timedelta(days=i) for i in range(10)]


@dataclass
class Episode:
    """한 TC의 연속 fail 구간. until은 다시 pass가 된 날(비활성 시작일), None이면 계속 fail."""
    tc: str
    since: date
    until: date | None
    log_excerpt: str
    # agent 추정 필드 (신규 fail 분석 시점에 채워지고 이후 날짜로 승계)
    suspect_sha: str | None = None
    author: str | None = None
    confidence: str | None = None

    def active_on(self, d: date) -> bool:
        return self.since <= d and (self.until is None or d < self.until)


def d_(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------- cfg-a
# FTL full regression: TC 900개, 만성 fail이 20건 이상 깔려 있는 대형 구성.
CFG_A_CHRONIC = [
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
]

CFG_A_EPISODES = [
    Episode("TC_PWR_LOSS_118", d_("2026-07-10"), d_("2026-07-18"),
            "spor recovery hang at stage 2, ftl_open() no return"),
    Episode("TC_FTL_MAP_141", d_("2026-07-15"), d_("2026-07-20"),
            "map rebuild reads stale l2p page after unclean shutdown",
            suspect_sha="9e12ab4", author="jung.hh", confidence="medium"),
    Episode("TC_IO_QD32_055", d_("2026-07-16"), None,
            "hang: qd32 rand write stalls at 97% completion",
            suspect_sha="4c77f02", author="seo.mm", confidence="unknown"),
    Episode("TC_META_JOURNAL_019", d_("2026-07-18"), None,
            "journal checkpoint skipped when gc pressure high"),
    Episode("TC_FTL_TRIM_072", d_("2026-07-19"), d_("2026-07-22"),
            "deallocate during gc migrates trimmed block, data resurrect"),
    Episode("TC_IO_MIXED_090", d_("2026-07-20"), None,
            "MISCOMPARE under 70/30 mixed after 2h, lba=0x29ff10",
            suspect_sha="b8d4310", author="kang.jj", confidence="medium"),
    # 2026-07-23 신규 3건 — 설계 문서 예시 시나리오
    Episode("TC_FTL_GC_017", d_("2026-07-23"), None,
            "ASSERT at gc_victim.c:412: invalid victim block state during gc",
            suspect_sha="a3f9c21", author="kim.xx", confidence="high"),
    Episode("TC_FTL_MAP_205", d_("2026-07-23"), None,
            "SEGV in map_cache_evict(), map_cache.c:207",
            suspect_sha="77d0e4f", author="lee.yy", confidence="high"),
    Episode("TC_IO_SEQ_078", d_("2026-07-23"), None,
            "IO latency spike 4200ms > budget 500ms in seq write",
            suspect_sha="c91b502", author="park.zz", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-b
# NVMe protocol suite: TC 420개, 중형 구성.
CFG_B_CHRONIC = [
    ("TC_NVME_FMT_009",    "2026-07-01", "format nvm with ses=1 returns invalid field"),
    ("TC_IO_LAT_QOS_021",  "2026-06-28", "qos class B p95 4.1ms > sla 2.0ms"),
    ("TC_META_GC_MIX_044", "2026-07-06", "gc during meta update drops one dirty page"),
    ("TC_WEAR_STATIC_130", "2026-07-11", "static wear leveling never triggers in 24h soak"),
    ("TC_PWR_CYCLE_202",   "2026-06-15", "device not ready within 5s after 500th cycle"),
]

CFG_B_EPISODES = [
    Episode("TC_IO_FLUSH_067", d_("2026-07-15"), d_("2026-07-17"),
            "flush returns before nand program complete (write-through off)"),
    Episode("TC_NVME_RESET_033", d_("2026-07-17"), None,
            "controller reset during io leaves qpair zombie",
            suspect_sha="f0a9912", author="yoon.ss", confidence="medium"),
    Episode("TC_MAP_REBUILD_058", d_("2026-07-21"), d_("2026-07-23"),
            "rebuild time 41s > budget 30s on 75% full drive"),
    Episode("TC_IO_WRCACHE_012", d_("2026-07-22"), None,
            "write cache disable ignored under sustained seq write",
            suspect_sha="5b21d9e", author="choi.aa", confidence="medium"),
]

# ---------------------------------------------------------------- cfg-c
# smoke suite: TC 150개, 대부분 green인 소형 구성 (0 fail인 날 존재).
CFG_C_CHRONIC: list[tuple[str, str, str]] = []

CFG_C_EPISODES = [
    Episode("TC_SMOKE_BOOT_001", d_("2026-07-16"), d_("2026-07-18"),
            "boot-to-ready 6.2s > smoke budget 5s"),
    Episode("TC_IO_BASIC_005", d_("2026-07-21"), d_("2026-07-22"),
            "single seq write/read verify miscompare at lba=0x100"),
]

CONFIGS = [
    {"id": "cfg-a", "label": "FTL full regression", "total": 900,
     "episodes": [Episode(tc, d_(s), None, log) for tc, s, log in CFG_A_CHRONIC] + CFG_A_EPISODES},
    {"id": "cfg-b", "label": "NVMe protocol suite", "total": 420,
     "episodes": [Episode(tc, d_(s), None, log) for tc, s, log in CFG_B_CHRONIC] + CFG_B_EPISODES},
    {"id": "cfg-c", "label": "smoke suite", "total": 150,
     "episodes": [Episode(tc, d_(s), None, log) for tc, s, log in CFG_C_CHRONIC] + CFG_C_EPISODES},
]


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def run_id_for(cfg_id: str, d: date) -> int:
    # dobee run 번호 흉내: 구성/날짜별로 안정적인 가짜 번호
    base = {"cfg-a": 8700, "cfg-b": 5200, "cfg-c": 3100}[cfg_id]
    return base + (d - DATES[0]).days * 11


def main() -> None:
    manifest = {"schema_version": SCHEMA_VERSION, "configs": []}

    for cfg in CONFIGS:
        cfg_dir = RESULTS_DIR / cfg["id"]
        rollup_days = []

        for d in DATES:
            active = [ep for ep in cfg["episodes"] if ep.active_on(d)]
            failures = {}
            for ep in active:
                entry = {
                    "status": "new" if ep.since == d else "ongoing",
                    "since": ep.since.isoformat(),
                    "log_excerpt": ep.log_excerpt,
                    "log_url": (f"https://dobee.example.internal/run/"
                                f"{run_id_for(cfg['id'], d)}/tc/{ep.tc}"),
                    "suspect_sha": ep.suspect_sha,
                    "author": ep.author,
                    "confidence": ep.confidence,
                }
                failures[ep.tc] = entry

            summary = {
                "total": cfg["total"],
                "pass": cfg["total"] - len(active),
                "fail": len(active),
                "new_fail": sum(1 for ep in active if ep.since == d),
            }
            write_json(cfg_dir / f"{d.isoformat()}.json", {
                "schema_version": SCHEMA_VERSION,
                "config": cfg["id"],
                "date": d.isoformat(),
                "summary": summary,
                "failures": failures,
            })
            rollup_days.append({"date": d.isoformat(), **summary})

        # index.json — 데일리 파일에서 파생되는 rollup (agent가 매일 재생성)
        write_json(cfg_dir / "index.json", {
            "schema_version": SCHEMA_VERSION,
            "config": cfg["id"],
            "label": cfg["label"],
            "updated": DATES[-1].isoformat(),
            "days": rollup_days,
        })
        manifest["configs"].append({"id": cfg["id"], "label": cfg["label"], "total": cfg["total"]})
        print(f"{cfg['id']}: {len(DATES)} daily files, "
              f"fail {rollup_days[0]['fail']} -> {rollup_days[-1]['fail']}")

    # 대시보드가 구성 목록을 하드코딩하지 않도록 하는 매니페스트
    write_json(RESULTS_DIR / "configs.json", manifest)
    print(f"wrote {RESULTS_DIR / 'configs.json'}")


if __name__ == "__main__":
    main()
