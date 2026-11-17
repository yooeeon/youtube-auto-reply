"""python -m app → 대시보드 서버 실행 + 브라우저 열기."""
import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8000


def main() -> None:
    url = f"http://{HOST}:{PORT}"
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"대시보드: {url}  (종료: Ctrl+C)")
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
