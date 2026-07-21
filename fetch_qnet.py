#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
큐넷(Q-Net) 자격증 시험일정 수집 스크립트
=========================================

공공데이터포털(data.go.kr)에 등록된 한국산업인력공단 Open API 두 개를 호출해서
- 국가자격 종목 목록 (자격명, 종목코드 등)
- 국가자격 시험일정 (원서접수일, 시험일, 합격자발표일 등)
을 받아온 뒤 data/ 폴더에 JSON으로 저장합니다.

사전 준비
---------
1. https://www.data.go.kr 회원가입 후 아래 두 API를 "활용신청" (개발계정, 자동승인, 무료)
   - 한국산업인력공단_국가자격 종목 목록 정보
     https://www.data.go.kr/data/15003024/openapi.do
   - 한국산업인력공단_국가자격 시험일정 조회 서비스
     https://www.data.go.kr/data/15074408/openapi.do
2. 마이페이지 > 개발계정 상세보기에서 "일반 인증키(Decoding)" 값을 복사
3. 아래처럼 실행:

   pip install requests --break-system-packages
   python fetch_qnet.py --service-key "발급받은_디코딩_인증키" --years 2026

옵션
----
--years        수집할 시행년도 (여러 개 가능, 기본값: 올해)
--qualgb       자격구분코드 (T:국가기술자격, S:국가전문자격, C:과정평가형, W:일학습병행)
               기본값: T S (가장 흔히 찾는 두 종류)
--out          저장 폴더 (기본값: ./data)

이 스크립트는 서버(내 컴퓨터)에서 직접 API를 호출하므로 브라우저의 CORS 제약을
받지 않습니다. 받아온 JSON은 dashboard.html이 읽어서 화면에 보여줍니다.
"""

import argparse
import datetime
import functools
import json
import os
import sys
import time
import xml.etree.ElementTree as ET

import requests

# GitHub Actions 로그 창에서 print()가 스크립트가 끝날 때까지 하나도 안 보이는
# 문제(출력 버퍼링)를 막기 위해, 모든 print를 즉시 flush 되도록 강제한다.
print = functools.partial(print, flush=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

QUAL_LIST_URL = "http://openapi.q-net.or.kr/api/service/rest/InquiryListNationalQualifcationSVC/getList"
SCHEDULE_URL = "http://apis.data.go.kr/B490007/qualExamSchd/getQualExamSchdList"
# 종목별 자격정보(시험과목/검정방법/합격기준/출제경향 등). jmCd(종목코드) 필요.
QUAL_DETAIL_URL = "http://openapi.q-net.or.kr/api/service/rest/InquiryInformationTradeNTQSVC/getList"

QUALGB_NAMES = {
    "T": "국가기술자격",
    "S": "국가전문자격",
    "C": "과정평가형자격",
    "W": "일학습병행자격",
}


def fetch_qualification_list(service_key: str, max_retries: int = 3) -> list:
    """종목(자격명) 목록을 가져온다."""
    print("[1/2] 국가자격 종목 목록 수집 중...")
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                QUAL_LIST_URL,
                params={"serviceKey": service_key},
                headers=HEADERS,
                timeout=30,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            items = []
            for item in root.iter("item"):
                rec = {child.tag: (child.text or "").strip() for child in item}
                items.append(rec)

            if items:
                print(f"  -> {len(items)}개 종목 수집 완료")
                return items

            print("  경고: 종목 목록이 비어 있습니다. 응답 원문 일부:")
            print(" ", resp.text[:500])
            return items

        except ET.ParseError as e:
            last_error = e
            print(f"  경고: XML 파싱 실패 ({attempt}/{max_retries}차 시도): {e}")
            try:
                print("  응답 원문 일부:", resp.text[:500])
            except Exception:
                pass
            time.sleep(2 * attempt)
        except requests.RequestException as e:
            last_error = e
            print(f"  경고: 네트워크 오류 ({attempt}/{max_retries}차 시도): {e}")
            time.sleep(2 * attempt)

    print(f"  실패: {max_retries}번 재시도했지만 계속 실패했습니다: {last_error}")
    return []


def fetch_qualification_detail(service_key: str, jm_cd: str, max_retries: int = 2) -> list:
    """특정 종목코드(jmCd)의 자격정보(시험과목/검정방법/합격기준/출제경향 등)를 가져온다.
    참고: 이 API는 응시수수료를 직접 제공하지 않는다. 정확한 응시료는 큐넷 공식
    수수료 안내 페이지(필기: rcv011.do?id=rcv01102, 실기: rcv013.do?id=rcv01305)를
    참고해야 한다 - 종목별로 실기 재료비 등이 달라 API로 일괄 제공되지 않는다.

    613개 종목을 순서대로 조회하다 보면 그중 몇 개는 타임아웃/네트워크 오류가
    나기 마련이다. 여기서 예외가 새어나가면 전체 스크립트가 죽고, 그러면
    이미 잘 받아온 시험일정(schedules.json)까지 커밋되지 못하고 날아간다.
    그래서 실패한 종목은 재시도 후 조용히 건너뛰고 나머지는 계속 진행한다.
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                QUAL_DETAIL_URL,
                params={"ServiceKey": service_key, "jmCd": jm_cd},
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = []
            for item in root.iter("item"):
                rec = {child.tag: (child.text or "").strip() for child in item}
                items.append(rec)
            return items
        except (requests.RequestException, ET.ParseError) as e:
            last_error = e
            time.sleep(1 * attempt)

    print(f"    경고: {jm_cd} 상세정보 조회 실패(건너뜀): {last_error}", flush=True)
    return []


def fetch_schedule(service_key: str, impl_yy: str, qualgb_cd: str) -> list:
    """특정 연도/자격구분의 시험일정을 페이지네이션하며 전부 가져온다."""
    all_items = []
    page = 1
    num_of_rows = 50  # 이 API는 페이지당 최대 50건까지만 허용함
    while True:
        resp = requests.get(
            SCHEDULE_URL,
            params={
                "serviceKey": service_key,
                "numOfRows": num_of_rows,
                "pageNo": page,
                "dataFormat": "json",
                "implYy": impl_yy,
                "qualgbCd": qualgb_cd,
            },
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
            print(f"  경고: JSON 파싱 실패 ({impl_yy}/{qualgb_cd}, page {page})")
            print(" ", resp.text[:500])
            break

        # 응답이 {"response": {...}} 형태일 수도, {"header":..., "body":...} 형태일 수도 있음
        envelope = data.get("response", data)
        header = envelope.get("header", {})
        body = envelope.get("body", {})

        result_code = header.get("resultCode")
        if result_code not in (None, "00", "0"):
            msg = header.get("resultMsg")
            print(f"  경고: API 오류 응답 ({impl_yy}/{qualgb_cd}): {result_code} {msg}")
            break

        items = body.get("items", []) if isinstance(body, dict) else []
        if isinstance(items, dict):
            items = items.get("item", [])
        if isinstance(items, dict):
            items = [items]

        if not items:
            break

        all_items.extend(items)

        total_count = int(body.get("totalCount", len(all_items)) or 0)
        if page * num_of_rows >= total_count:
            break
        page += 1
        time.sleep(0.2)

    return all_items


def normalize_date(yyyymmdd: str):
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    try:
        return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description="큐넷 자격증 시험일정 수집")
    parser.add_argument("--service-key", required=True, help="data.go.kr에서 발급받은 디코딩 인증키")
    current_year = datetime.datetime.now().year
    parser.add_argument(
        "--years",
        nargs="+",
        default=[str(current_year)],
        help=f"수집할 시행년도 (기본값: {current_year})",
    )
    parser.add_argument(
        "--qualgb",
        nargs="+",
        default=["T", "S"],
        choices=list(QUALGB_NAMES.keys()),
        help="자격구분코드 (기본값: T S)",
    )
    parser.add_argument("--out", default="data", help="저장 폴더 (기본값: ./data)")
    parser.add_argument(
        "--with-details",
        action="store_true",
        help="종목별 자격정보(시험과목/검정방법/합격기준 등)도 함께 수집 (종목 수만큼 API 호출이 늘어남)",
    )
    parser.add_argument(
        "--jmcd",
        nargs="*",
        default=None,
        help="--with-details 사용 시 특정 종목코드만 수집 (예: --jmcd 1320 7910). 생략하면 전체 종목 수집",
    )
    args = parser.parse_args()

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out)
    os.makedirs(out_dir, exist_ok=True)

    # 1) 종목 목록
    qualifications = fetch_qualification_list(args.service_key)
    with open(os.path.join(out_dir, "qualifications.json"), "w", encoding="utf-8") as f:
        json.dump(qualifications, f, ensure_ascii=False, indent=2)

    # jmcd -> 종목명 매핑 (대시보드에서 이름 매칭용, 참고용)
    jmcd_to_name = {q.get("jmcd"): q.get("jmfldnm") for q in qualifications if q.get("jmcd")}

    # 2) 시험일정
    print("[2/2] 시험일정 수집 중...")
    all_schedules = []
    for year in args.years:
        for qualgb in args.qualgb:
            print(f"  - {year}년 / {QUALGB_NAMES.get(qualgb, qualgb)} 조회 중...")
            items = fetch_schedule(args.service_key, year, qualgb)
            for it in items:
                it["qualgbNmKo"] = QUALGB_NAMES.get(qualgb, it.get("qualgbNm", qualgb))
                it["_docRegStart"] = normalize_date(it.get("docRegStartDt"))
                it["_docRegEnd"] = normalize_date(it.get("docRegEndDt"))
                it["_docExamStart"] = normalize_date(it.get("docExamStartDt"))
                it["_docExamEnd"] = normalize_date(it.get("docExamEndDt"))
                it["_docPass"] = normalize_date(it.get("docPassDt"))
                it["_pracRegStart"] = normalize_date(it.get("pracRegStartDt"))
                it["_pracRegEnd"] = normalize_date(it.get("pracRegEndDt"))
                it["_pracExamStart"] = normalize_date(it.get("pracExamStartDt"))
                it["_pracExamEnd"] = normalize_date(it.get("pracExamEndDt"))
                it["_pracPass"] = normalize_date(it.get("pracPassDt"))
            all_schedules.extend(items)
            print(f"    -> {len(items)}건")

    with open(os.path.join(out_dir, "schedules.json"), "w", encoding="utf-8") as f:
        json.dump(all_schedules, f, ensure_ascii=False, indent=2)

    # 3) (선택) 종목별 자격정보 - 시험과목/검정방법/합격기준/출제경향
    detail_count = 0
    details_by_jmcd = {}
    if args.with_details:
        print("[3/3] 종목별 자격정보(과목/검정방법 등) 수집 중...")
        target_codes = args.jmcd if args.jmcd else sorted(jmcd_to_name.keys())
        detail_path = os.path.join(out_dir, "qual_details.json")
        for i, jm_cd in enumerate(target_codes, 1):
            # 종목 하나에서 무슨 일이 나든(타임아웃, 파싱 오류 등) 전체 스크립트가
            # 죽어서는 안 된다 - 이미 받은 시험일정까지 커밋 못 하고 날아가기 때문.
            try:
                items = fetch_qualification_detail(args.service_key, jm_cd)
                if items:
                    details_by_jmcd[jm_cd] = {
                        "jmCd": jm_cd,
                        "jmfldnm": jmcd_to_name.get(jm_cd, items[0].get("jmfldnm", "")),
                        "info": [
                            {"infogb": it.get("infogb", ""), "contents": it.get("contents", "")}
                            for it in items
                        ],
                    }
                detail_count += 1
            except Exception as e:
                print(f"    경고: {jm_cd} 처리 중 예외 발생(건너뜀): {e}", flush=True)

            if i % 10 == 0:
                print(f"    ... {i}/{len(target_codes)}", flush=True)
                # 중간중간 저장해두면, 혹시 중간에 워크플로가 타임아웃 나도
                # 그때까지 모은 데이터는 남는다.
                with open(detail_path, "w", encoding="utf-8") as f:
                    json.dump(details_by_jmcd, f, ensure_ascii=False, indent=2)

            time.sleep(0.1)

        with open(detail_path, "w", encoding="utf-8") as f:
            json.dump(details_by_jmcd, f, ensure_ascii=False, indent=2)
        print(f"  -> {len(details_by_jmcd)}개 종목의 상세정보 저장됨")

    meta = {
        "generated_at": datetime.datetime.now().isoformat(),
        "years": args.years,
        "qualgb": args.qualgb,
        "schedule_count": len(all_schedules),
        "qualification_count": len(qualifications),
        "with_details": bool(args.with_details),
        "detail_count": len(details_by_jmcd) if args.with_details else 0,
    }
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print()
    print(f"완료: 시험일정 {len(all_schedules)}건, 종목 {len(qualifications)}건 저장됨 -> {out_dir}")
    print("이제 이 폴더에서 다음을 실행한 뒤 브라우저로 열어보세요:")
    print("  python -m http.server 8000")
    print("  -> http://localhost:8000/index.html")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"HTTP 오류: {e}", file=sys.stderr)
        sys.exit(1)
