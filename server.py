"""
День 27. Веб-приложение «Ассистент автосервиса» на локальной LLM.

Браузер ──► этот сервер (127.0.0.1:8000) ──► Ollama (localhost:11434) ──► локальная модель
   ▲                                                                            │
   └──────────── ответ печатается по мере генерации (потоковая передача) ◄──────┘

Только стандартная библиотека Python — устанавливать ничего не нужно.

Без облака — по построению:
  - адрес модели обязан быть на этом компьютере (localhost / 127.0.0.1 / ::1),
    иначе сервер откажется запускаться;
  - сам сервер слушает только 127.0.0.1 — снаружи к нему не подключиться;
  - страница не загружает ничего из интернета: ни шрифтов, ни скриптов.

Режимы:
  chat   — обычный разговор с историей;
  reply  — вежливый ответ на сообщение клиента;
  order  — заявка из сообщения клиента в JSON (Ollama format=json).

Запуск:  python server.py [--model qwen2.5:7b] [--port 8000]
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
HISTORY_LIMIT = 12  # сообщений истории, которые уходят модели в режиме чата

MODES = {
    "chat": {
        "title": "Чат",
        "history": True,
        "system": "Ты — помощник администратора автосервиса. Отвечай по-русски, кратко и по делу. "
                  "Если не знаешь точно — так и скажи, не выдумывай цены, сроки и характеристики.",
    },
    "reply": {
        "title": "Ответ клиенту",
        "history": False,
        "system": "Ты пишешь ответы клиентам автосервиса от имени администратора. На сообщение клиента "
                  "напиши вежливый, короткий ответ по-русски: поприветствуй, ответь по сути, предложи "
                  "следующий шаг (запись, звонок, уточнение). Не выдумывай цены, свободное время и сроки — "
                  "вместо них пиши [уточнить]. Верни только текст ответа.",
    },
    "order": {
        "title": "Заявка в JSON",
        "history": False,
        "format": "json",
        "system": "Извлеки из сообщения клиента автосервиса заявку. Верни ТОЛЬКО JSON с полями: "
                  "name (имя клиента), phone (телефон), car (марка и модель), service (что нужно сделать), "
                  "date (желаемая дата или время), comment (прочее важное). Если данных нет — null. "
                  "Ничего не придумывай.",
    },
}


class OllamaError(RuntimeError):
    pass


def ensure_local(url: str) -> str:
    host = urlparse(url).hostname or ""
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"Адрес модели {url} не на этом компьютере. Приложение работает только с локальной "
                         f"моделью: укажите http://localhost:11434")
    return url.rstrip("/")


def ollama(url: str, path: str, payload: dict = None, timeout: int = 10, stream: bool = False):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body).get("error", body)
        except json.JSONDecodeError:
            pass
        raise OllamaError(f"Ollama ответила ошибкой {e.code}: {body[:200]}") from e
    except (urllib.error.URLError, OSError) as e:
        raise OllamaError("Ollama не отвечает. Запустите приложение Ollama или команду «ollama serve».") from e
    if stream:
        return resp
    with resp:
        return json.loads(resp.read().decode("utf-8"))


def status(url: str, default_model: str) -> dict:
    try:
        version = ollama(url, "/api/version").get("version")
        models = sorted(m["name"] for m in ollama(url, "/api/tags").get("models", []))
        loaded = {}
        for m in ollama(url, "/api/ps").get("models", []):
            size, vram = m.get("size") or 0, m.get("size_vram") or 0
            share = vram / size if size else 0
            loaded[m["name"]] = "GPU" if share >= 0.99 else ("процессор" if share <= 0.01 else f"GPU {share:.0%} + процессор")
        return {"ok": True, "version": version, "models": models, "loaded": loaded, "default_model": default_model,
                "ollama_url": url, "modes": {k: v["title"] for k, v in MODES.items()}}
    except OllamaError as e:
        return {"ok": False, "error": str(e), "models": [], "default_model": default_model, "ollama_url": url,
                "modes": {k: v["title"] for k, v in MODES.items()}}


def build_messages(mode: str, messages: list) -> list:
    cfg = MODES[mode]
    clean = [{"role": m["role"], "content": str(m["content"])[:8000]}
             for m in messages if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    if not cfg["history"]:
        clean = [m for m in clean if m["role"] == "user"][-1:]
    else:
        clean = clean[-HISTORY_LIMIT:]
    return [{"role": "system", "content": cfg["system"]}] + clean


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalLLMApp/1.0"

    def log_message(self, fmt, *args):  # тишина для статики, логи чата пишем сами
        pass

    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(BASE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            self._json(200, status(self.server.ollama_url, self.server.default_model))
        else:
            self._json(404, {"error": "Не найдено"})

    def do_POST(self):
        if self.path != "/api/chat":
            return self._json(404, {"error": "Не найдено"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "Запрос не в формате JSON"})
        mode = req.get("mode", "chat")
        model = str(req.get("model") or self.server.default_model)
        if mode not in MODES:
            return self._json(400, {"error": f"Неизвестный режим: {mode}"})
        messages = build_messages(mode, req.get("messages") or [])
        if len(messages) < 2:
            return self._json(400, {"error": "Пустое сообщение"})

        payload = {"model": model, "messages": messages, "stream": True, "options": {"temperature": 0.3}}
        if MODES[mode].get("format"):
            payload["format"] = MODES[mode]["format"]
        started = time.perf_counter()
        try:
            upstream = ollama(self.server.ollama_url, "/api/chat", payload, timeout=600, stream=True)
        except OllamaError as e:
            return self._json(502, {"error": str(e)})

        # потоковый ответ: каждая строка — JSON {"t": "кусок текста"}, последняя — {"done": true, ...}
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        tokens, final = 0, {}
        try:
            with upstream:
                for raw in upstream:
                    if not raw.strip():
                        continue
                    chunk = json.loads(raw.decode("utf-8"))
                    if chunk.get("error"):
                        self._line({"error": chunk["error"]})
                        break
                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        self._line({"t": piece})
                    if chunk.get("done"):
                        final = chunk
                        break
            tokens = final.get("eval_count") or 0
            eval_s = (final.get("eval_duration") or 0) / 1e9
            stats = {"done": True, "model": model, "tokens": tokens,
                     "tok_per_s": round(tokens / eval_s, 1) if eval_s else None,
                     "seconds": round(time.perf_counter() - started, 1)}
            self._line(stats)
            print(f"[{time.strftime('%H:%M:%S')}] {MODES[mode]['title']:<14} {model:<16} "
                  f"{tokens:>4} ток. · {stats['tok_per_s']} ток/с · {stats['seconds']} с", flush=True)
        except (BrokenPipeError, ConnectionResetError):
            print(f"[{time.strftime('%H:%M:%S')}] ответ остановлен пользователем", flush=True)

    def _line(self, obj: dict) -> None:
        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()


def main() -> None:
    ap = argparse.ArgumentParser(description="Веб-приложение на локальной LLM")
    ap.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST_URL", "http://localhost:11434"))
    args = ap.parse_args()

    url = ensure_local(args.ollama)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    srv.ollama_url, srv.default_model = url, args.model
    st = status(url, args.model)
    print(f"Ассистент автосервиса: http://127.0.0.1:{args.port}")
    print(f"Модель: {args.model} · Ollama: {url} · " +
          (f"версия {st['version']}, моделей {len(st['models'])}" if st["ok"] else f"⚠ {st['error']}"))
    if st["ok"] and args.model not in st["models"]:
        print(f"⚠ Модель {args.model} не скачана. Выполните: ollama pull {args.model}")
    print("Облачные модели не используются. Остановить: Ctrl+C\n", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
