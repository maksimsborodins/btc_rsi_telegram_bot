import time
import requests
from datetime import datetime

# ================== НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ==================

TELEGRAM_BOT_TOKEN = "8483676787:AAEVtCfTPro4PtEkH7BSw60rmBHXhNnKH7I"
CHAT_ID = "5209523280"  # например "123456789"

SYMBOL = "BTCUSDT"
INTERVAL = "30m"        # 30-минутный таймфрейм
RSI_PERIOD = 14

RISK_USD = 50           # фиксированный риск на сделку
LEVERAGE = 50           # плечо

SLEEP_SECONDS = 60      # как часто обновлять (секунд)
SWING_LOOKBACK = 120    # сколько последних свечей брать для поиска TVH

# RSI уровни
RSI_PRE_LONG_MIN = 30
RSI_PRE_LONG_MAX = 35

RSI_PRE_SHORT_MIN = 65
RSI_PRE_SHORT_MAX = 70

RSI_LOWER = 30
RSI_UPPER = 70

# ================== КОНСТАНТЫ API ==================

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


# ================== УТИЛИТЫ ==================

def send_telegram_message(text: str):
    try:
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
        }
        r = requests.post(TELEGRAM_API_URL, json=payload, timeout=10)
        r.raise_for_status()
        print(f"[{datetime.utcnow()}] Сообщение отправлено в Telegram")
    except Exception as e:
        print(f"[{datetime.utcnow()}] Ошибка отправки в Telegram: {e}")


def get_binance_klines(symbol: str, interval: str, limit: int = 500):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }
    r = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    # каждый kline: [open_time, open, high, low, close, volume, ...]
    closes = [float(k[4]) for k in data]
    highs = [float(k[2]) for k in data]
    lows = [float(k[3]) for k in data]
    volumes = [float(k[5]) for k in data]
    return closes, highs, lows, volumes


def compute_rsi(closes, period=14):
    # используем только закрытые свечи -> убираем последнюю (формирующуюся)
    if len(closes) < period + 2:
        return None

    prices = closes[:-1]
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        rs = float("inf")
    else:
        rs = avg_gain / avg_loss

    rsi_values = []
    first_rsi = 100 - (100 / (1 + rs))
    rsi_values.append(first_rsi)

    for i in range(period + 1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            rs = float("inf")
        else:
            rs = avg_gain / avg_loss

        rsi = 100 - (100 / (1 + rs))
        rsi_values.append(rsi)

    return rsi_values


def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema_vals = []
    ema_val = values[0]
    ema_vals.append(ema_val)
    for price in values[1:]:
        ema_val = price * k + ema_val * (1 - k)
        ema_vals.append(ema_val)
    return ema_vals


def calc_trend(closes):
    # используем закрытые свечи
    prices = closes[:-1]
    if len(prices) < 200:
        return "недостаточно данных", None, None

    ema50_list = ema(prices, 50)
    ema200_list = ema(prices, 200)

    if ema50_list is None or ema200_list is None:
        return "недостаточно данных", None, None

    ema50 = ema50_list[-1]
    ema200 = ema200_list[-1]
    last_close = prices[-1]

    # небольшой допуск, чтобы не дёргался
    if ema50 > ema200 * 1.002 and last_close > ema200:
        trend = "бычий"
    elif ema50 < ema200 * 0.998 and last_close < ema200:
        trend = "медвежий"
    else:
        trend = "флэт/смешанный"

    return trend, ema50, ema200


def calc_fib_and_margin(closes, direction: str):
    prices = closes[:-1]
    if len(prices) < SWING_LOOKBACK:
        recent = prices
    else:
        recent = prices[-SWING_LOOKBACK:]

    if not recent:
        return None

    if direction == "long":
        tv_price = max(recent)  # TVH – локальный максимум
        fib_9 = tv_price * (1 - 0.09)
        fib_18 = tv_price * (1 - 0.18)
        fib_24 = tv_price * (1 - 0.24)
    else:  # short
        tv_price = min(recent)  # TVL – локальный минимум как база
        fib_9 = tv_price * (1 + 0.09)
        fib_18 = tv_price * (1 + 0.18)
        fib_24 = tv_price * (1 + 0.24)

    # расстояние от средней (-9% или +9%) до стопа (-24% или +24%) в процентах
    risk_pct_from_avg = abs(fib_9 - fib_24) / fib_9

    if risk_pct_from_avg <= 0:
        position_notional = None
        margin = None
    else:
        position_notional = RISK_USD / risk_pct_from_avg
        margin = position_notional / LEVERAGE

    return {
        "tv_price": tv_price,
        "fib_9": fib_9,
        "fib_18": fib_18,
        "fib_24": fib_24,
        "risk_pct_from_avg": risk_pct_from_avg,
        "position_notional": position_notional,
        "margin": margin,
    }


def format_signal_message(signal_type, direction, current_rsi, trend, fib_info):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    dir_text = "ЛОНГ" if direction == "long" else "ШОРТ"

    # Заголовок по типу сигнала
    if signal_type == "pre_long":
        title = "[BTCUSDT 30m] RSI ПОДХОДИТ к зоне лонга (ниже 35)"
    elif signal_type == "in_long":
        title = "[BTCUSDT 30m] RSI В ЗОНЕ перепроданности (<30) — лонг зона"
    elif signal_type == "exit_long":
        title = "[BTCUSDT 30m] RSI ВЫШЕЛ из зоны перепроданности (>30)"
    elif signal_type == "pre_short":
        title = "[BTCUSDT 30m] RSI ПОДХОДИТ к зоне шорта (выше 65)"
    elif signal_type == "in_short":
        title = "[BTCUSDT 30m] RSI В ЗОНЕ перекупленности (>70) — шорт зона"
    elif signal_type == "exit_short":
        title = "[BTCUSDT 30m] RSI ВЫШЕЛ из зоны перекупленности (<70)"
    else:
        title = "[BTCUSDT 30m] RSI сигнал"

    lines = []
    lines.append(f"{title}")
    lines.append("")
    lines.append(f"Время (UTC): {now}")
    lines.append(f"Направление сигнала: {dir_text}")
    lines.append(f"RSI(14): {current_rsi:.2f}")
    lines.append(f"Тренд по EMA50/EMA200: {trend}")

    # предупреждение, если входим против тренда
    if direction == "long" and trend == "медвежий":
        lines.append("Внимание: сигнал лонг против медвежьего тренда.")
    if direction == "short" and trend == "бычий":
        lines.append("Внимание: сигнал шорт против бычьего тренда.")

    if fib_info:
        tv = fib_info["tv_price"]
        fib_9 = fib_info["fib_9"]
        fib_18 = fib_info["fib_18"]
        fib_24 = fib_info["fib_24"]
        risk_pct = fib_info["risk_pct_from_avg"]
        pos_notional = fib_info["position_notional"]
        margin = fib_info["margin"]

        lines.append("")
        lines.append("Fibonacci уровни (авто):")
        if direction == "long":
            lines.append(f"TVH (0%): {tv:.2f}")
            lines.append(f"Средняя входа (-9%): {fib_9:.2f}")
            lines.append(f"Набор зоны до -18%: {fib_18:.2f}")
            lines.append(f"Стоп (-24%): {fib_24:.2f}")
        else:
            lines.append(f"TVL (0%): {tv:.2f}")
            lines.append(f"Средняя входа (+9%): {fib_9:.2f}")
            lines.append(f"Набор зоны до +18%: {fib_18:.2f}")
            lines.append(f"Стоп (+24%): {fib_24:.2f}")

        if risk_pct and pos_notional and margin:
            lines.append("")
            lines.append("Риск-менеджмент (из расчёта 50$ риска и плеча 50x):")
            lines.append(f"Расстояние от средней до стопа: {risk_pct * 100:.2f}%")
            lines.append(f"Рекомендованный размер позиции: {pos_notional:.2f} USDT")
            lines.append(f"Требуемая маржа при 50x: {margin:.2f} USDT")
        else:
            lines.append("")
            lines.append("Не удалось корректно посчитать маржу (risk_pct_from_avg слишком мало).")

    return "\n".join(lines)


# ================== ОСНОВНОЙ ЦИКЛ ==================

def main():
    print("Старт бота RSI для BTCUSDT 30m. Нажми Ctrl+C чтобы остановить.\n")

    rsi_state = "normal"  # normal / pre_long / in_long / pre_short / in_short

    while True:
        try:
            closes, highs, lows, volumes = get_binance_klines(SYMBOL, INTERVAL, limit=500)
            rsi_list = compute_rsi(closes, RSI_PERIOD)

            if not rsi_list:
                print(f"[{datetime.utcnow()}] Недостаточно данных для RSI")
                time.sleep(SLEEP_SECONDS)
                continue

            current_rsi = rsi_list[-1]
            trend, ema50, ema200 = calc_trend(closes)

            now_print = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now_print} UTC] RSI({RSI_PERIOD}) = {current_rsi:.2f}, тренд: {trend}, текущее состояние: {rsi_state}")

            signal_type = None
            direction = None
            new_state = rsi_state

            # ---------- ЛОНГ СТОРОНА (сначала выходим из зоны, потом вход/предзона) ----------

            if rsi_state == "in_long" and current_rsi > RSI_LOWER:
                signal_type = "exit_long"
                direction = "long"
                new_state = "normal"
            elif current_rsi <= RSI_LOWER and rsi_state != "in_long":
                signal_type = "in_long"
                direction = "long"
                new_state = "in_long"
            elif RSI_LOWER < current_rsi <= RSI_PRE_LONG_MAX and rsi_state not in ["pre_long", "in_long"]:
                signal_type = "pre_long"
                direction = "long"
                new_state = "pre_long"

            # ---------- ШОРТ СТОРОНА (обрабатываем, только если сигнала ещё нет) ----------

            if signal_type is None:
                if rsi_state == "in_short" and current_rsi < RSI_UPPER:
                    signal_type = "exit_short"
                    direction = "short"
                    new_state = "normal"
                elif current_rsi >= RSI_UPPER and rsi_state != "in_short":
                    signal_type = "in_short"
                    direction = "short"
                    new_state = "in_short"
                elif RSI_PRE_SHORT_MIN <= current_rsi < RSI_UPPER and rsi_state not in ["pre_short", "in_short"]:
                    signal_type = "pre_short"
                    direction = "short"
                    new_state = "pre_short"

            # ---------- ОТПРАВКА СИГНАЛА, ЕСЛИ ЕСТЬ ПЕРЕХОД СОСТОЯНИЯ ----------

            if signal_type and direction:
                fib_info = calc_fib_and_margin(closes, direction)
                msg = format_signal_message(signal_type, direction, current_rsi, trend, fib_info)
                send_telegram_message(msg)
                rsi_state = new_state
            else:
                # если вышли из предзоны обратно в норму — сбрасываем
                if rsi_state in ["pre_long", "pre_short"]:
                    if (rsi_state == "pre_long" and current_rsi > RSI_PRE_LONG_MAX) or \
                       (rsi_state == "pre_short" and current_rsi < RSI_PRE_SHORT_MIN):
                        rsi_state = "normal"

            time.sleep(SLEEP_SECONDS)

        except KeyboardInterrupt:
            print("Остановка бота пользователем.")
            break
        except Exception as e:
            print(f"[{datetime.utcnow()}] Ошибка в основном цикле: {e}")
            time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
