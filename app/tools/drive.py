import io

from . import tool, google_service

EXPORTS = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}
TEXT_LIKE = ("text/", "application/json", "application/csv")


@tool("드라이브 검색")
def search_drive_files(keyword: str, max_results: int = 10) -> dict:
    """구글 드라이브에서 파일을 검색합니다 (파일 이름과 내용 모두).

    Args:
        keyword: 검색어. 예) "캡스톤 계획서", "회의록".
        max_results: 최대 개수 (1~20).
    """
    svc = google_service("drive", "v3")
    kw = keyword.replace("\\", "").replace("'", "\\'")
    q = f"(name contains '{kw}' or fullText contains '{kw}') and trashed = false"
    res = svc.files().list(q=q, pageSize=max(1, min(int(max_results), 20)),
                           orderBy="modifiedTime desc",
                           fields="files(id,name,mimeType,modifiedTime,webViewLink)").execute()
    return {"count": len(res.get("files", [])), "files": res.get("files", [])}


@tool("드라이브 파일 읽기")
def read_drive_file(file_id: str) -> dict:
    """드라이브 파일의 내용을 텍스트로 읽습니다 (구글 문서/시트/슬라이드, 텍스트 파일).

    Args:
        file_id: search_drive_files 결과의 id.
    """
    from googleapiclient.http import MediaIoBaseDownload
    svc = google_service("drive", "v3")
    meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,webViewLink").execute()
    mime = meta["mimeType"]
    if mime in EXPORTS:
        data = svc.files().export(fileId=file_id, mimeType=EXPORTS[mime]).execute()
    elif mime.startswith(TEXT_LIKE):
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = dl.next_chunk()
        data = buf.getvalue()
    else:
        return {"name": meta["name"], "link": meta.get("webViewLink"),
                "content": "", "note": f"이 형식({mime})은 아직 내용을 읽을 수 없어요. 링크로 안내하세요."}
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return {"name": meta["name"], "link": meta.get("webViewLink"),
            "content": text[:8000] + ("\n…(이하 생략)" if len(text) > 8000 else "")}
