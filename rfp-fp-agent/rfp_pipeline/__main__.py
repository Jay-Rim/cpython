"""문서 파싱 결과 확인용 CLI: python -m rfp_pipeline input.pptx -o parsed.json"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .chunking import build_chunks
from .parsers import parse_document


def main() -> None:
    parser = argparse.ArgumentParser(description="RFP 문서를 추적 가능한 공통 JSON으로 변환")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--max-chars", type=int, default=24_000)
    args = parser.parse_args()
    document = parse_document(args.input)
    payload = document.to_dict()
    payload["chunks"] = [chunk.to_llm_payload() for chunk in build_chunks(document, max_chars=args.max_chars)]
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()

