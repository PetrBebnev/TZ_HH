# test_refresh_signals.py
"""
Автотест критичного сценария: обновление значений сигналов
по кнопке «Обновить».

Логика:
1. Поднимаем TCP-сервер-заглушку (эмулятор сервиса-симулятора на порту 2001).
2. Клиент подключается к нему.
3. Вызываем refresh() — аналог нажатия кнопки «Обновить».
4. Проверяем, что все 10 сигналов получены, значения корректны
   и обновились относительно предыдущего состояния.
"""

import json
import math
import socket
import threading
import time

import pytest


# ---------------------------------------------------------------------------
# Тестируемый клиент (упрощённая модель приложения)
# ---------------------------------------------------------------------------
class SignalClient:
    """Клиент, эмулирующий работу десктоп-приложения с сервисом сигналов."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2001):
        self.host = host
        self.port = port
        self.sock: socket.socket | None = None
        # модель данных, которая в реальном приложении отображается в таблице
        self.signals: dict[int, dict] = {}

    def connect(self) -> None:
        """Устанавливает TCP-соединение (аналог кнопки «Подключиться»)."""
        self.sock = socket.create_connection((self.host, self.port), timeout=5)

    def disconnect(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def refresh(self) -> dict[int, dict]:
        """
        Аналог нажатия кнопки «Обновить»:
        отправляет запрос сервису и парсит ответ в модель signals.
        """
        assert self.sock is not None, "Сначала нужно вызвать connect()"

        # отправляем запрос (в реальном протоколе может быть просто read)
        self.sock.sendall(b"GET_SIGNALS\n")

        # читаем ответ (предполагаем, что сервис шлёт одну JSON-строку с \n)
        raw = self._recv_until_newline()
        payload = json.loads(raw.decode("utf-8"))

        # обновляем модель данных
        for item in payload["signals"]:
            self.signals[item["id"]] = item

        return self.signals

    def _recv_until_newline(self) -> bytes:
        buf = bytearray()
        while not buf.endswith(b"\n"):
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf).strip()


# ---------------------------------------------------------------------------
# TCP-сервер-заглушка (эмулятор сервиса-симулятора)
# ---------------------------------------------------------------------------
class FakeSignalServer:
    """
    Мини-сервер, эмулирующий сервис-симулятор:
    отдаёт 10 сигналов со значениями, зависящими от времени (синусоида).
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2001, n_signals: int = 10):
        self.host = host
        self.port = port
        self.n_signals = n_signals
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.host, self.port))
        self._srv.listen(1)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        # небольшая пауза, чтобы сокет успел подняться
        time.sleep(0.1)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=2)

    def _serve(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    data = conn.recv(1024)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not data:
                    break
                if b"GET_SIGNALS" in data:
                    conn.sendall(self._build_response())

    def _build_response(self) -> bytes:
        """Формирует JSON-ответ со значениями сигналов (синусоида)."""
        t = time.time()
        signals = []
        for i in range(1, self.n_signals + 1):
            signals.append({
                "id": i,
                "name": f"Signal_{i}",
                "value": round(math.sin(t + i), 4),
                "quality": "Good",
                "timestamp": t,
            })
        return (json.dumps({"signals": signals}) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------
# Фикстуры pytest
# ---------------------------------------------------------------------------
@pytest.fixture(scope="function")
def fake_server():
    """Поднимает TCP-заглушку на порту 2001 и гасит её после теста."""
    server = FakeSignalServer(host="127.0.0.1", port=2001, n_signals=10)
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="function")
def client(fake_server):
    """Создаёт клиента и подключает его к заглушке."""
    c = SignalClient(host="127.0.0.1", port=2001)
    c.connect()
    yield c
    c.disconnect()


# ---------------------------------------------------------------------------
# Сам тест — критичный сценарий «Обновить»
# ---------------------------------------------------------------------------
def test_refresh_updates_all_signals(client):
    """
    Проверяем, что после нажатия «Обновить»:
      • получены все 10 сигналов;
      • у каждого корректные поля (id, name, value, quality, timestamp);
      • значения действительно обновились относительно предыдущего запроса.
    """

    # --- 1. Первый запрос (аналог первого нажатия «Обновить») -------------
    first_snapshot = client.refresh()

    # Должны быть получены ровно 10 сигналов (см. ТЗ)
    assert len(first_snapshot) == 10, (
        f"Ожидалось 10 сигналов, получено {len(first_snapshot)}"
    )

    # Проверяем структуру и корректность каждого сигнала
    for sig_id, sig in first_snapshot.items():
        assert sig["id"] == sig_id, "ID в модели не совпадает с ключом"
        assert sig["name"] == f"Signal_{sig_id}"
        assert isinstance(sig["value"], (int, float))
        assert sig["quality"] in {"Good", "Bad", "Uncertain"}
        assert isinstance(sig["timestamp"], (int, float))

    # Запоминаем значения для сравнения после второго обновления
    values_before = {sid: sig["value"] for sid, sig in first_snapshot.items()}
    timestamps_before = {sid: sig["timestamp"] for sid, sig in first_snapshot.items()}

    # --- 2. Ждём, чтобы сервис успел сгенерировать новые значения ---------
    time.sleep(1.1)

    # --- 3. Второй запрос (аналог повторного нажатия «Обновить») ----------
    second_snapshot = client.refresh()

    assert len(second_snapshot) == 10, "Количество сигналов изменилось между запросами"

    # --- 4. Проверяем, что данные РЕАЛЬНО обновились ----------------------
    values_after = {sid: sig["value"] for sid, sig in second_snapshot.items()}
    timestamps_after = {sid: sig["timestamp"] for sid, sig in second_snapshot.items()}

    # timestamp должен увеличиться — иначе «Обновить» не сходил за данными
    for sid in timestamps_before:
        assert timestamps_after[sid] > timestamps_before[sid], (
            f"Время сигнала {sid} не обновилось"
        )

    # Значения синусоиды должны измениться (сдвиг во времени)
    changed = sum(
        1 for sid in values_before
        if values_before[sid] != values_after[sid]
    )
    assert changed >= 5, (
        f"Ожидалось, что обновится большинство значений, изменилось только {changed}"
    )