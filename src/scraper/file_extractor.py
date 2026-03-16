"""
첨부파일 텍스트 추출기.

PDF, DOCX, XLSX, HWP 파일에서 텍스트를 추출한다.
"""

from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".hwp", ".pptx", ".md"}


def extract_text(file_path: Path) -> str:
    """파일 확장자에 따라 적절한 방법으로 텍스트를 추출한다."""
    ext = file_path.suffix.lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext == ".docx":
        return _extract_docx(file_path)
    elif ext == ".xlsx":
        return _extract_xlsx(file_path)
    elif ext == ".hwp":
        return _extract_hwp(file_path)
    elif ext == ".pptx":
        return _extract_pptx(file_path)
    elif ext == ".md":
        return _extract_md(file_path)
    else:
        print(f"[skip] 지원하지 않는 파일 형식: {file_path.name}")
        return ""


def extract_from_directory(dir_path: Path) -> list[dict]:
    """폴더 내 지원되는 모든 파일에서 텍스트를 일괄 추출한다."""
    results = []
    if not dir_path.is_dir():
        print(f"[error] 디렉토리가 존재하지 않습니다: {dir_path}")
        return results

    files = sorted(
        f for f in dir_path.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    print(f"[extract] {dir_path}에서 {len(files)}개 파일 발견")

    for file_path in files:
        print(f"  - {file_path.name}")
        try:
            text = extract_text(file_path)
            results.append({
                "filename": file_path.name,
                "extracted_text": text,
            })
        except Exception as e:
            print(f"    [error] 텍스트 추출 실패: {e}")
            results.append({
                "filename": file_path.name,
                "extracted_text": "",
            })

    return results


def extract_pdf_images(file_path: Path, output_dir: Path) -> list[str]:
    """PDF 각 페이지를 PNG 이미지로 변환하여 저장한다. 저장된 파일 경로 목록을 반환."""
    import fitz  # pymupdf

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem
    image_paths = []

    doc = fitz.open(file_path)
    for i, pg in enumerate(doc):
        pix = pg.get_pixmap(dpi=150)
        img_path = output_dir / f"{stem}_p{i + 1}.png"
        pix.save(str(img_path))
        # 프로젝트 루트 기준 상대 경로로 저장
        try:
            rel_path = str(img_path.relative_to(Path(__file__).resolve().parents[2]))
        except ValueError:
            rel_path = str(img_path)
        image_paths.append(rel_path)
    doc.close()

    print(f"    [img] {len(image_paths)}페이지 이미지 저장: {output_dir}")
    return image_paths


def _extract_pdf(file_path: Path) -> str:
    """pdfplumber로 PDF 텍스트를 추출한다."""
    import pdfplumber

    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def _extract_docx(file_path: Path) -> str:
    """python-docx로 DOCX 텍스트를 추출한다."""
    from docx import Document

    doc = Document(file_path)
    return "\n".join(para.text for para in doc.paragraphs if para.text.strip())


def _extract_xlsx(file_path: Path) -> str:
    """openpyxl로 XLSX의 모든 시트에서 텍스트를 추출한다."""
    from openpyxl import load_workbook

    wb = load_workbook(file_path, read_only=True, data_only=True)
    text_parts = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        text_parts.append(f"[시트: {sheet_name}]")
        for row in ws.iter_rows(values_only=True):
            cells = [str(cell) for cell in row if cell is not None]
            if cells:
                text_parts.append("\t".join(cells))
    wb.close()
    return "\n".join(text_parts)


def _extract_pptx(file_path: Path) -> str:
    """python-pptx로 PPTX 슬라이드별 텍스트를 추출한다."""
    from pptx import Presentation

    prs = Presentation(file_path)
    text_parts = []
    for i, slide in enumerate(prs.slides, 1):
        slide_texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        slide_texts.append(text)
        if slide_texts:
            text_parts.append(f"[슬라이드 {i}]\n" + "\n".join(slide_texts))
    return "\n\n".join(text_parts)


def _extract_md(file_path: Path) -> str:
    """마크다운 파일을 UTF-8 텍스트로 읽는다."""
    return file_path.read_text(encoding="utf-8")


def _extract_hwp(file_path: Path) -> str:
    """python-hwp로 HWP 텍스트를 추출한다. 실패 시 olefile 폴백."""
    try:
        from hwp5.hwp5txt import extract_text as hwp5_extract

        return hwp5_extract(str(file_path))
    except Exception:
        pass

    # olefile 폴백
    try:
        import olefile

        if not olefile.isOleFile(str(file_path)):
            print(f"    [warn] OLE 형식이 아닌 HWP: {file_path.name}")
            return ""

        ole = olefile.openole(str(file_path))
        text_parts = []
        for stream in ole.listdir():
            stream_path = "/".join(stream)
            if "BodyText" in stream_path or "PrvText" in stream_path:
                try:
                    data = ole.openstream(stream).read()
                    text = data.decode("utf-16-le", errors="ignore")
                    # NULL 문자 및 제어 문자 제거
                    text = "".join(c for c in text if c.isprintable() or c in "\n\t")
                    if text.strip():
                        text_parts.append(text.strip())
                except Exception:
                    continue
        ole.close()
        return "\n".join(text_parts)
    except Exception as e:
        print(f"    [error] HWP 추출 실패: {e}")
        return ""
