"""SignalBacktest — Desktop-Tool für Custom-Signal-Backtests auf US-Aktien.

Lädt Kursdaten aus market_data.db, lässt den Nutzer eine Formel über typische
Kurs-Variablen eingeben, kauft die Top-N Aktien mit dem höchsten Signalwert am
Startdatum und plottet die Equity-Curve. Ein Slider trimmt nachträglich das
Anzeige-Enddatum.
"""
from __future__ import annotations

import json
import subprocess
import sys
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from PySide6.QtCore import Qt, QThread, Signal, QDate, QDateTime
from PySide6.QtGui import QFont, QPen, QColor, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QDateEdit,
    QComboBox, QListWidget, QListWidgetItem, QTextEdit, QSlider, QStatusBar,
    QMessageBox, QSplitter, QGroupBox, QFormLayout, QSizePolicy,
    QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QAbstractItemView,
)
from PySide6.QtCharts import (
    QChart, QChartView, QLineSeries, QValueAxis, QDateTimeAxis, QAreaSeries,
)


DB_PATH = Path(__file__).resolve().parent / "market_data.db"
DB_XZ_PATH = DB_PATH.with_suffix(DB_PATH.suffix + ".xz")  # market_data.db.xz
CUSTOM_VARS_PATH = DB_PATH.parent / "custom_variables.json"
REPO_PATH = DB_PATH.parent
BENCHMARK_TICKERS = ["SPY", "QQQ", "IWM", "DIA", "VTI"]
BENCHMARK_COLORS = {
    "SPY": "#888888",
    "QQQ": "#9467bd",
    "IWM": "#8c564b",
    "DIA": "#17becf",
    "VTI": "#bcbd22",
}


def ensure_db_extracted() -> None:
    """Falls nur die komprimierte DB (oder ihre Chunks) vorliegt, einmalig nach
    `market_data.db` entpacken. Chunks werden zuerst zusammengesetzt, da GitHub
    Dateien >100 MB ablehnt."""
    if DB_PATH.exists():
        return

    import lzma
    import shutil
    import time

    chunks = sorted(DB_PATH.parent.glob(f"{DB_XZ_PATH.name}.part*"))

    if not DB_XZ_PATH.exists():
        if not chunks:
            raise FileNotFoundError(
                f"Weder {DB_PATH.name}, noch {DB_XZ_PATH.name}, noch Chunks "
                f"({DB_XZ_PATH.name}.part*) in {DB_PATH.parent} gefunden."
            )
        print(
            f"Setze {len(chunks)} Chunks zu {DB_XZ_PATH.name} zusammen…",
            flush=True,
        )
        tmp_xz = DB_XZ_PATH.with_suffix(DB_XZ_PATH.suffix + ".tmp")
        try:
            with open(tmp_xz, "wb") as out:
                for c in chunks:
                    with open(c, "rb") as src:
                        shutil.copyfileobj(src, out, length=8 * 1024 * 1024)
            tmp_xz.replace(DB_XZ_PATH)
        except BaseException:
            tmp_xz.unlink(missing_ok=True)
            raise

    print(
        f"Entpacke {DB_XZ_PATH.name} → {DB_PATH.name} (einmalig, dauert ein paar Minuten)…",
        flush=True,
    )
    tmp = DB_PATH.with_suffix(DB_PATH.suffix + ".tmp")
    t0 = time.monotonic()
    try:
        with lzma.open(DB_XZ_PATH, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
        tmp.replace(DB_PATH)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    size_gb = DB_PATH.stat().st_size / 1e9
    print(
        f"Fertig in {time.monotonic() - t0:.1f}s — {DB_PATH.name} ({size_gb:.2f} GB).",
        flush=True,
    )


# --------------------------------------------------------------------------- #
# Variablen-Definitionen (Name + deutsche Tooltip-Beschreibung)
# --------------------------------------------------------------------------- #
VARIABLES: list[tuple[str, str]] = [
    ("close",              "Schlusskurs am Kaufdatum"),
    ("open",               "Eröffnungskurs am Kaufdatum"),
    ("high",               "Tageshoch am Kaufdatum"),
    ("low",                "Tagestief am Kaufdatum"),
    ("volume",             "Handelsvolumen am Kaufdatum (Stück)"),
    ("ret_5d",             "Rendite über die letzten 5 Handelstage (0.05 = +5%)"),
    ("ret_20d",            "Rendite über die letzten 20 Handelstage (~1 Monat)"),
    ("ret_60d",            "Rendite über die letzten 60 Handelstage (~3 Monate)"),
    ("ret_252d",           "Rendite über die letzten 252 Handelstage (~1 Jahr) — klassisches Momentum"),
    ("sma_20",             "Gleitender Durchschnitt des Schlusskurses über 20 Tage"),
    ("sma_50",             "Gleitender Durchschnitt des Schlusskurses über 50 Tage"),
    ("sma_200",            "Gleitender Durchschnitt des Schlusskurses über 200 Tage"),
    ("vol_20",             "Volatilität (Std-Abw. der Tagesrenditen) über 20 Tage"),
    ("vol_60",             "Volatilität über 60 Tage"),
    ("rsi_14",             "Relative Strength Index über 14 Tage (0–100; >70 = überkauft, <30 = überverkauft)"),
    ("high_52w",           "Höchster Schlusskurs der letzten 252 Handelstage"),
    ("low_52w",            "Tiefster Schlusskurs der letzten 252 Handelstage"),
    ("dist_from_high",     "Relativer Abstand zum 52W-Hoch (0 = am Hoch, -0.3 = 30 % darunter)"),
    ("avg_volume_20",      "Durchschnittliches Handelsvolumen über 20 Tage"),
    ("market_cap",         "Marktkapitalisierung = close × shares_outstanding"),
    ("shares_outstanding", "Anzahl ausstehender Aktien (aus Metadaten)"),
]

VARIABLE_NAMES = [v[0] for v in VARIABLES]
BUILTIN_NAMES = set(VARIABLE_NAMES)


# --------------------------------------------------------------------------- #
# Custom-Variablen Persistenz
# --------------------------------------------------------------------------- #
def load_custom_variables() -> list[dict]:
    """Liest gespeicherte Custom-Variablen. Format pro Eintrag:
    {"name": str, "formula": str, "description": str}."""
    if not CUSTOM_VARS_PATH.exists():
        return []
    try:
        with open(CUSTOM_VARS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        formula = entry.get("formula")
        if not isinstance(name, str) or not isinstance(formula, str):
            continue
        if not name.isidentifier() or name in BUILTIN_NAMES:
            continue
        out.append({
            "name": name,
            "formula": formula,
            "description": str(entry.get("description", "")),
        })
    return out


def save_custom_variables(custom_vars: list[dict]) -> None:
    with open(CUSTOM_VARS_PATH, "w", encoding="utf-8") as f:
        json.dump(custom_vars, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# Sichere Formel-Auswertung
# --------------------------------------------------------------------------- #
def _multi_sum(*args):
    """Summe von beliebig vielen Series/Skalaren — sum(a, b, c, ...)."""
    if not args:
        return 0.0
    out = args[0]
    for a in args[1:]:
        out = out + a
    return out


SAFE_FUNCTIONS: dict[str, object] = {
    "log":   np.log,
    "ln":    np.log,
    "log10": np.log10,
    "log2":  np.log2,
    "sqrt":  np.sqrt,
    "abs":   np.abs,
    "exp":   np.exp,
    "sign":  np.sign,
    "min":   np.minimum,
    "max":   np.maximum,
    "where": np.where,
    "clip":  np.clip,
    "sum":   _multi_sum,
}


def evaluate_formula(df: pd.DataFrame, formula: str) -> pd.Series:
    """Auswerten der Formel über die Indikator-Tabelle. Rückgabe: Series je Ticker."""
    formula = formula.strip()
    if not formula:
        raise ValueError("Formel ist leer.")

    namespace: dict[str, object] = {col: df[col] for col in df.columns}
    namespace.update(SAFE_FUNCTIONS)
    try:
        result = eval(formula, {"__builtins__": {}}, namespace)
    except Exception as exc:
        raise ValueError(f"Formel-Fehler: {exc}") from exc

    if isinstance(result, (int, float, np.floating, np.integer)):
        return pd.Series(float(result), index=df.index)
    if isinstance(result, np.ndarray):
        if result.shape != (len(df),):
            raise ValueError(
                f"Formel-Ergebnis hat Form {result.shape}, erwartet ({len(df)},)."
            )
        return pd.Series(result, index=df.index, dtype=float)
    if isinstance(result, pd.Series):
        return result.astype(float)
    raise ValueError(
        f"Formel muss eine Reihe je Ticker ergeben (got {type(result).__name__})."
    )


# --------------------------------------------------------------------------- #
# Datenladen + Indikator-Berechnung
# --------------------------------------------------------------------------- #
@dataclass
class MarketData:
    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    meta: pd.DataFrame  # ticker -> sector, shares_outstanding
    sector_filter: str | None
    start: pd.Timestamp
    end: pd.Timestamp


def load_market_data(
    db_path: Path,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    sector_filter: str | None,
    progress_cb=None,
) -> MarketData:
    """Lädt OHLCV + Metadaten, gepivotet auf wide-Form (date × ticker)."""
    lookback_start = start_date - pd.Timedelta(days=400)

    if progress_cb:
        progress_cb("Lade Metadaten…")

    conn = sqlite3.connect(str(db_path))
    try:
        meta_sql = (
            "SELECT ticker, sector, shares_outstanding "
            "FROM symbol_metadata WHERE active = 1"
        )
        params: list = []
        if sector_filter and sector_filter != "Alle":
            meta_sql += " AND sector = ?"
            params.append(sector_filter)
        meta = pd.read_sql(meta_sql, conn, params=params)
        if meta.empty:
            raise ValueError("Keine Symbole nach Filterung gefunden.")

        if progress_cb:
            progress_cb(f"Lade Kursdaten ({len(meta)} Tickers)…")

        # JOIN ist effizienter als IN-Liste für ~12k Tickers
        price_sql = """
            SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.volume
            FROM daily_prices p
            JOIN symbol_metadata s ON s.ticker = p.ticker AND s.active = 1
            WHERE p.date >= ? AND p.date <= ?
        """
        price_params: list = [
            lookback_start.strftime("%Y-%m-%d"),
            end_date.strftime("%Y-%m-%d"),
        ]
        if sector_filter and sector_filter != "Alle":
            price_sql += " AND s.sector = ?"
            price_params.append(sector_filter)

        prices = pd.read_sql(price_sql, conn, params=price_params, parse_dates=["date"])
    finally:
        conn.close()

    if prices.empty:
        raise ValueError("Keine Kursdaten im Zeitraum.")

    if progress_cb:
        progress_cb("Pivotiere Datenmatrix…")

    # Duplikate entfernen (defensiv) und pivotieren
    prices = prices.drop_duplicates(subset=["ticker", "date"], keep="last")

    def _pivot(col: str) -> pd.DataFrame:
        return prices.pivot(index="date", columns="ticker", values=col).sort_index()

    return MarketData(
        close=_pivot("close"),
        open=_pivot("open"),
        high=_pivot("high"),
        low=_pivot("low"),
        volume=_pivot("volume"),
        meta=meta.set_index("ticker"),
        sector_filter=sector_filter,
        start=lookback_start,
        end=end_date,
    )


def load_benchmarks(
    db_path: Path,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    portfolio_value: float,
) -> dict[str, pd.Series]:
    """Lädt Benchmark-Closes und normalisiert sie auf den Start-Portfoliowert.
    Unabhängig vom Sektor-Filter, weil ETFs sonst rausfliegen."""
    placeholders = ",".join("?" * len(BENCHMARK_TICKERS))
    conn = sqlite3.connect(str(db_path))
    try:
        df = pd.read_sql(
            f"SELECT ticker, date, close FROM daily_prices "
            f"WHERE ticker IN ({placeholders}) AND date >= ? AND date <= ?",
            conn,
            params=BENCHMARK_TICKERS + [
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            ],
            parse_dates=["date"],
        )
    finally:
        conn.close()
    if df.empty:
        return {}
    pivot = df.pivot(index="date", columns="ticker", values="close").sort_index()
    out: dict[str, pd.Series] = {}
    for tkr in BENCHMARK_TICKERS:
        if tkr not in pivot.columns:
            continue
        s = pivot[tkr].ffill().dropna()
        if s.empty:
            continue
        out[tkr] = (s / s.iloc[0]) * portfolio_value
    return out


def compute_indicators(
    data: MarketData,
    target_date: pd.Timestamp,
    custom_variables: list[dict] | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Berechnet alle Variablen je Ticker am ersten Handelstag >= target_date.
    Custom-Variablen werden am Ende auf Basis der Built-ins ausgewertet."""
    close = data.close
    available = close.index[close.index >= target_date]
    if len(available) == 0:
        raise ValueError(f"Kein Handelstag ab {target_date.date()} verfügbar.")
    buy_date = available[0]
    pos = close.index.get_loc(buy_date)

    df = pd.DataFrame(index=close.columns)
    df["close"] = close.iloc[pos]
    df["open"] = data.open.iloc[pos]
    df["high"] = data.high.iloc[pos]
    df["low"] = data.low.iloc[pos]
    df["volume"] = data.volume.iloc[pos]

    def _back(n: int) -> pd.Series | None:
        return close.iloc[pos - n] if pos - n >= 0 else None

    for n, col in [(5, "ret_5d"), (20, "ret_20d"), (60, "ret_60d"), (252, "ret_252d")]:
        prev = _back(n)
        df[col] = (df["close"] / prev - 1) if prev is not None else np.nan

    for n, col in [(20, "sma_20"), (50, "sma_50"), (200, "sma_200")]:
        if pos + 1 - n >= 0:
            df[col] = close.iloc[pos + 1 - n: pos + 1].mean(axis=0)
        else:
            df[col] = np.nan

    daily_ret = close.pct_change(fill_method=None)
    for n, col in [(20, "vol_20"), (60, "vol_60")]:
        if pos + 1 - n >= 0:
            df[col] = daily_ret.iloc[pos + 1 - n: pos + 1].std(axis=0)
        else:
            df[col] = np.nan

    # RSI 14 (klassisch nach Wilder, vereinfacht: einfacher Durchschnitt)
    if pos + 1 - 15 >= 0:
        delta = close.iloc[pos + 1 - 15: pos + 1].diff().iloc[1:]
        gain = delta.clip(lower=0).mean()
        loss = (-delta.clip(upper=0)).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi_14"] = 100 - 100 / (1 + rs)
    else:
        df["rsi_14"] = np.nan

    n52 = min(252, pos + 1)
    df["high_52w"] = close.iloc[pos + 1 - n52: pos + 1].max(axis=0)
    df["low_52w"] = close.iloc[pos + 1 - n52: pos + 1].min(axis=0)
    df["dist_from_high"] = df["close"] / df["high_52w"] - 1

    if pos + 1 - 20 >= 0:
        df["avg_volume_20"] = data.volume.iloc[pos + 1 - 20: pos + 1].mean(axis=0)
    else:
        df["avg_volume_20"] = np.nan

    df["shares_outstanding"] = data.meta["shares_outstanding"].reindex(df.index)
    df["market_cap"] = df["close"] * df["shares_outstanding"]

    # Custom-Variablen auf Basis der Built-ins berechnen. Fehlerhafte Formeln
    # werden auf NaN gesetzt, damit ein einzelner kaputter Eintrag nicht den
    # ganzen Backtest abbricht.
    if custom_variables:
        for cv in custom_variables:
            name = cv["name"]
            if name in df.columns:
                continue  # nicht ueberschreiben
            try:
                df[name] = evaluate_formula(df, cv["formula"])
            except Exception:
                df[name] = np.nan

    return df, buy_date


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
@dataclass
class BacktestResult:
    equity_curve: pd.Series  # date -> portfolio value
    top_tickers: list[str]
    signals: pd.Series       # ticker -> signal value (only top N)
    all_signals: pd.Series   # ticker -> signal value, sorted desc
    sectors_by_ticker: dict[str, str]
    benchmark_curves: dict[str, pd.Series] = field(default_factory=dict)
    buy_date: pd.Timestamp = pd.Timestamp("1970-01-01")
    portfolio_value: float = 0.0
    formula: str = ""


def run_backtest(
    data: MarketData,
    start_date: pd.Timestamp,
    formula: str,
    n_stocks: int,
    portfolio_value: float,
    custom_variables: list[dict] | None = None,
    progress_cb=None,
) -> BacktestResult:
    if progress_cb:
        progress_cb("Berechne Indikatoren…")
    indicators, buy_date = compute_indicators(data, start_date, custom_variables)
    indicators = indicators.dropna(subset=["close"])

    if progress_cb:
        progress_cb("Werte Formel aus…")
    raw_signals = evaluate_formula(indicators, formula)
    signals = raw_signals.replace([np.inf, -np.inf], np.nan).dropna()

    if len(signals) < n_stocks:
        # Diagnose: welche Variablen sind hauptverantwortlich für NaNs?
        import re
        used = sorted(set(re.findall(r"\b([a-zA-Z_]\w*)\b", formula)) & set(indicators.columns))
        nan_share = {col: float(indicators[col].isna().mean()) for col in used}
        worst = sorted(nan_share.items(), key=lambda kv: -kv[1])[:3]
        worst_txt = ", ".join(f"{c}: {p*100:.0f}% NaN" for c, p in worst) or "—"

        hint = ""
        if any(c in used for c in ("ret_252d", "sma_200", "high_52w", "low_52w", "dist_from_high")):
            hint = (
                "\n\nHinweis: Die Formel nutzt langfristige Indikatoren "
                "(ret_252d / sma_200 / high_52w …). Diese brauchen ~252 Handelstage "
                "Historie vor dem Startdatum. DB beginnt 2021-03-26, frühestes "
                "sinnvolles Startdatum ist etwa 2022-04-05."
            )
        raise ValueError(
            f"Nur {len(signals)} gültige Signal-Werte vorhanden, brauche {n_stocks}.\n"
            f"NaN-Anteile (Top 3): {worst_txt}{hint}"
        )

    sorted_signals = signals.sort_values(ascending=False)
    top = sorted_signals.head(n_stocks)
    top_tickers = top.index.tolist()

    if progress_cb:
        progress_cb("Berechne Equity-Curve…")
    held = data.close[top_tickers].loc[buy_date:].ffill()
    per_stock = portfolio_value / n_stocks
    shares = per_stock / held.iloc[0]
    equity = held.multiply(shares, axis=1).sum(axis=1)

    sectors = {}
    if "sector" in data.meta.columns:
        sectors = data.meta["sector"].dropna().to_dict()

    return BacktestResult(
        equity_curve=equity,
        top_tickers=top_tickers,
        signals=top,
        all_signals=sorted_signals,
        sectors_by_ticker=sectors,
        buy_date=buy_date,
        portfolio_value=portfolio_value,
        formula=formula,
    )


# --------------------------------------------------------------------------- #
# Worker-Threads (UI nicht blockieren)
# --------------------------------------------------------------------------- #
class LoadAndBacktestWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)  # (BacktestResult, MarketData)
    failed = Signal(str)

    def __init__(
        self,
        db_path: Path,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
        formula: str,
        n_stocks: int,
        portfolio_value: float,
        sector_filter: str,
        custom_variables: list[dict],
        existing_data: MarketData | None,
    ):
        super().__init__()
        self.db_path = db_path
        self.start_date = start_date
        self.end_date = end_date
        self.formula = formula
        self.n_stocks = n_stocks
        self.portfolio_value = portfolio_value
        self.sector_filter = sector_filter
        self.custom_variables = custom_variables
        self.existing_data = existing_data

    def run(self) -> None:
        try:
            data = self.existing_data
            need_load = (
                data is None
                or data.sector_filter != self.sector_filter
                or self.start_date < data.start + pd.Timedelta(days=400)
                or self.end_date > data.end
            )
            if need_load:
                data = load_market_data(
                    self.db_path,
                    self.start_date,
                    self.end_date,
                    self.sector_filter,
                    progress_cb=self.progress.emit,
                )
            result = run_backtest(
                data,
                self.start_date,
                self.formula,
                self.n_stocks,
                self.portfolio_value,
                custom_variables=self.custom_variables,
                progress_cb=self.progress.emit,
            )
            # Equity-Curve auf End-Date trimmen
            result.equity_curve = result.equity_curve.loc[: self.end_date]

            # Benchmarks ab Kaufdatum laden, normalisiert auf Portfolio-Startwert
            self.progress.emit("Lade Benchmark-Kurse…")
            try:
                result.benchmark_curves = load_benchmarks(
                    self.db_path,
                    result.buy_date,
                    self.end_date,
                    self.portfolio_value,
                )
            except Exception:
                result.benchmark_curves = {}

            self.finished_ok.emit((result, data))
        except Exception as exc:
            self.failed.emit(str(exc))


class GitUpdateChecker(QThread):
    """Prueft im Hintergrund, ob origin/main neuere Commits hat als HEAD."""
    update_status = Signal(bool, str)  # (update_available, info_text)

    def __init__(self, repo_path: Path):
        super().__init__()
        self.repo_path = repo_path

    def _git(self, *args: str, timeout: int = 20) -> tuple[int, str]:
        try:
            r = subprocess.run(
                ["git", *args],
                cwd=str(self.repo_path),
                capture_output=True, text=True, timeout=timeout,
            )
            return r.returncode, r.stdout.strip()
        except Exception:
            return 1, ""

    def run(self) -> None:
        # Ist es ueberhaupt ein Git-Repo?
        rc, _ = self._git("rev-parse", "--is-inside-work-tree", timeout=5)
        if rc != 0:
            self.update_status.emit(False, "")
            return

        # Fetch (offline -> nicht-Null returncode -> stillschweigend abbrechen)
        rc, _ = self._git("fetch", "origin", "main", timeout=20)
        if rc != 0:
            self.update_status.emit(False, "")
            return

        rc, local = self._git("rev-parse", "HEAD", timeout=5)
        if rc != 0 or not local:
            self.update_status.emit(False, "")
            return
        rc, remote = self._git("rev-parse", "origin/main", timeout=5)
        if rc != 0 or not remote:
            self.update_status.emit(False, "")
            return

        if local == remote:
            self.update_status.emit(False, "")
            return

        rc, count = self._git(
            "rev-list", "--count", f"{local}..{remote}", timeout=5,
        )
        n = count if rc == 0 and count.isdigit() else "?"
        self.update_status.emit(True, f"{n} neue Commits")


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
class FormulaEditor(QTextEdit):
    """QTextEdit mit Insert-Helper für Funktions-Buttons."""

    def insert_text(self, text: str, cursor_offset: int = 0) -> None:
        cursor = self.textCursor()
        cursor.insertText(text)
        if cursor_offset != 0:
            cursor.setPosition(cursor.position() + cursor_offset)
            self.setTextCursor(cursor)
        self.setFocus()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SignalBacktest")
        self.resize(1500, 900)

        self.cached_data: MarketData | None = None
        self.last_result: BacktestResult | None = None
        self.worker: LoadAndBacktestWorker | None = None
        self.git_worker: GitUpdateChecker | None = None
        self._series_refs: list = []
        self.custom_variables: list[dict] = load_custom_variables()
        self.benchmark_checks: dict[str, QCheckBox] = {}

        self.setStatusBar(QStatusBar())

        # Update-Button rechts in der Statusleiste — initial versteckt.
        self.update_btn = QPushButton("🔄  Update verfügbar")
        self.update_btn.setVisible(False)
        self.update_btn.clicked.connect(self._do_git_pull)
        self.statusBar().addPermanentWidget(self.update_btn)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([520, 980])
        outer.addWidget(splitter)

        self._populate_sectors_async()
        self._refresh_var_list()
        self._start_git_check()

    # ------------------------------ Panels --------------------------------- #
    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setSpacing(8)

        # Konfig
        cfg_box = QGroupBox("Konfiguration")
        cfg = QFormLayout(cfg_box)

        # DB beginnt 2021-03-26. ret_252d braucht 252 Handelstage Vorlauf,
        # frühester sinnvoller Start ist daher ~2022-04-05. Default deutlich
        # später, damit alle Indikatoren stabil verfügbar sind.
        self.start_date = QDateEdit(QDate(2023, 1, 3))
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("yyyy-MM-dd")
        self.start_date.setMinimumDate(QDate(2021, 9, 1))
        self.start_date.setMaximumDate(QDate(2026, 4, 24))
        cfg.addRow("Startdatum:", self.start_date)

        self.end_date = QDateEdit(QDate(2026, 4, 24))
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("yyyy-MM-dd")
        self.end_date.setMinimumDate(QDate(2021, 4, 1))
        self.end_date.setMaximumDate(QDate(2026, 4, 24))
        cfg.addRow("Enddatum:", self.end_date)

        self.n_stocks = QSpinBox()
        self.n_stocks.setRange(1, 200)
        self.n_stocks.setValue(20)
        cfg.addRow("Anzahl Aktien (N):", self.n_stocks)

        self.portfolio_value = QDoubleSpinBox()
        self.portfolio_value.setRange(100, 1e12)
        self.portfolio_value.setValue(100_000)
        self.portfolio_value.setSingleStep(1_000)
        self.portfolio_value.setDecimals(0)
        self.portfolio_value.setSuffix(" $")
        self.portfolio_value.setGroupSeparatorShown(True)
        cfg.addRow("Portfoliowert:", self.portfolio_value)

        self.sector = QComboBox()
        self.sector.addItem("Alle")
        cfg.addRow("Sektor:", self.sector)

        v.addWidget(cfg_box)

        # Variablen-Liste (Built-in + Custom). Single-Click fuegt ein.
        var_box = QGroupBox("Verfügbare Variablen  (Hover = Beschreibung, Klick = einfügen)")
        var_layout = QVBoxLayout(var_box)
        self.var_list = QListWidget()
        self.var_list.setAlternatingRowColors(True)
        self.var_list.itemClicked.connect(
            lambda item: self.formula.insert_text(item.data(Qt.UserRole) or item.text())
        )
        var_layout.addWidget(self.var_list)
        v.addWidget(var_box, 1)

        # Eigene Variable speichern / aktualisieren / loeschen
        cv_box = QGroupBox("Eigene Variable  (verwendet aktuelle Formel als Definition)")
        cv_form = QFormLayout(cv_box)
        self.cv_name = QLineEdit()
        self.cv_name.setPlaceholderText("z. B. momentum_score")
        cv_form.addRow("Name:", self.cv_name)
        self.cv_desc = QLineEdit()
        self.cv_desc.setPlaceholderText("kurze Beschreibung (Tooltip)")
        cv_form.addRow("Beschreibung:", self.cv_desc)
        cv_btn_row = QHBoxLayout()
        self.cv_save_btn = QPushButton("Speichern")
        self.cv_save_btn.clicked.connect(self._save_custom_variable)
        self.cv_update_btn = QPushButton("Aktualisieren")
        self.cv_update_btn.clicked.connect(lambda: self._save_custom_variable(update_only=True))
        self.cv_delete_btn = QPushButton("Löschen")
        self.cv_delete_btn.clicked.connect(self._delete_custom_variable)
        cv_btn_row.addWidget(self.cv_save_btn)
        cv_btn_row.addWidget(self.cv_update_btn)
        cv_btn_row.addWidget(self.cv_delete_btn)
        cv_form.addRow(cv_btn_row)
        v.addWidget(cv_box)

        # Formel + Funktions-Buttons
        formula_box = QGroupBox("Signal-Formel  (höchste Werte werden gekauft)")
        fl = QVBoxLayout(formula_box)

        self.formula = FormulaEditor()
        self.formula.setPlainText("ret_252d / vol_60")
        self.formula.setMaximumHeight(72)
        self.formula.setFont(QFont("Consolas", 11))
        fl.addWidget(self.formula)

        # Funktions-Buttons: (Label, einzufügender Text, Cursor-Offset, Tooltip)
        button_specs: list[tuple[str, str, int, str]] = [
            ("+",      "+",            0, "Addition"),
            ("−",      "-",            0, "Subtraktion"),
            ("×",      "*",            0, "Multiplikation"),
            ("÷",      "/",            0, "Division"),
            ("(",      "(",            0, "Klammer auf"),
            (")",      ")",            0, "Klammer zu"),
            ("x²",     "**2",          0, "Quadrat"),
            ("xʸ",     "**",           0, "Potenz, z. B. close**3"),
            ("√",      "sqrt()",      -1, "Quadratwurzel  sqrt(x)"),
            ("ln",     "log()",       -1, "Natürlicher Logarithmus  ln(x)"),
            ("log",    "log10()",     -1, "Logarithmus zur Basis 10"),
            ("eˣ",     "exp()",       -1, "Exponentialfunktion  e^x"),
            ("|x|",    "abs()",       -1, "Absolutbetrag"),
            ("sgn",    "sign()",      -1, "Vorzeichen (+1, 0, −1)"),
            ("min",    "min(, )",     -3, "Element-weises Minimum  min(a, b)"),
            ("max",    "max(, )",     -3, "Element-weises Maximum  max(a, b)"),
            ("∑",      "sum(, , )",   -5, "Summe mehrerer Terme  sum(a, b, c, …)"),
            ("clip",   "clip(, , )",  -5, "Begrenzen  clip(x, untere, obere)"),
            ("if",     "where(, , )", -5, "Bedingung  where(cond, dann, sonst)"),
            ("<",      " < ",          0, "Kleiner-als"),
            (">",      " > ",          0, "Größer-als"),
            ("&",      " & ",          0, "Logisches UND zwischen Bedingungen"),
            ("|",      " | ",          0, "Logisches ODER zwischen Bedingungen"),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        cols = 6
        for i, (label, ins, off, tip) in enumerate(button_specs):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedHeight(28)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(
                lambda _checked=False, t=ins, o=off: self.formula.insert_text(t, o)
            )
            grid.addWidget(btn, i // cols, i % cols)
        fl.addLayout(grid)
        v.addWidget(formula_box)

        # Run
        self.run_btn = QPushButton("▶  Backtest starten")
        run_font = QFont()
        run_font.setBold(True)
        run_font.setPointSize(11)
        self.run_btn.setFont(run_font)
        self.run_btn.setMinimumHeight(36)
        self.run_btn.clicked.connect(self._run_backtest)
        v.addWidget(self.run_btn)

        return panel

    def _build_right_panel(self) -> QWidget:
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_chart_tab(), "Equity Curve")
        self.tabs.addTab(self._build_ranking_tab(), "Ranking")
        return self.tabs

    def _build_chart_tab(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setSpacing(8)

        # Benchmark-Checkboxen ueber dem Chart
        bm_box = QGroupBox("Vergleichskurven  (auf Portfolio-Startwert normalisiert)")
        bm_layout = QHBoxLayout(bm_box)
        for tkr in BENCHMARK_TICKERS:
            cb = QCheckBox(tkr)
            cb.setChecked(tkr == "SPY")
            cb.stateChanged.connect(lambda _state: self._redraw())
            self.benchmark_checks[tkr] = cb
            bm_layout.addWidget(cb)
        bm_layout.addStretch(1)
        v.addWidget(bm_box)

        # Chart
        # _series_refs haelt Python-Referenzen auf alle QLineSeries / QAreaSeries
        # die aktuell im Chart leben. Ohne diese Refs sammelt der Python-GC die
        # Series ein, waehrend Qt sie noch rendert -> Access Violation 0xC0000005.
        self.chart = QChart()
        self.chart.setTitle("Equity Curve")
        self.chart.legend().hide()
        self.chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        self.chart_view = QChartView(self.chart)
        v.addWidget(self.chart_view, 1)

        # Slider für Anzeige-Enddatum
        slider_box = QGroupBox("Anzeige-Enddatum  (verschieben um Performance-Verlauf zu trimmen)")
        slider_layout = QHBoxLayout(slider_box)
        self.end_slider = QSlider(Qt.Horizontal)
        self.end_slider.setEnabled(False)
        self.end_slider.valueChanged.connect(self._slider_changed)
        slider_layout.addWidget(self.end_slider, 1)
        self.slider_label = QLabel("—")
        self.slider_label.setMinimumWidth(120)
        f = QFont("Consolas", 10)
        f.setBold(True)
        self.slider_label.setFont(f)
        slider_layout.addWidget(self.slider_label)
        v.addWidget(slider_box)

        # Stats
        stats_box = QGroupBox("Statistik")
        sl = QVBoxLayout(stats_box)
        self.stats_label = QLabel("Noch kein Backtest gelaufen.")
        self.stats_label.setFont(QFont("Consolas", 10))
        self.stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.stats_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.stats_label.setWordWrap(False)
        sl.addWidget(self.stats_label)
        v.addWidget(stats_box)

        return panel

    def _build_ranking_tab(self) -> QWidget:
        panel = QWidget()
        v = QVBoxLayout(panel)
        info = QLabel(
            "Alle Aktien sortiert nach Signalwert. Die Top-N (gekauft) sind grün hinterlegt. "
            "Spaltenkopf klicken zum Umsortieren."
        )
        info.setWordWrap(True)
        v.addWidget(info)

        self.ranking_table = QTableWidget(0, 5)
        self.ranking_table.setHorizontalHeaderLabels(
            ["Rang", "Ticker", "Signal", "Sektor", "Status"]
        )
        self.ranking_table.setSortingEnabled(True)
        self.ranking_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ranking_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ranking_table.setAlternatingRowColors(True)
        self.ranking_table.verticalHeader().setVisible(False)
        hdr = self.ranking_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        v.addWidget(self.ranking_table, 1)

        return panel

    # --------------------------- Variablen-Liste --------------------------- #
    def _refresh_var_list(self) -> None:
        """Befuellt var_list neu mit Built-ins + Custom-Variablen."""
        self.var_list.clear()
        for name, desc in VARIABLES:
            item = QListWidgetItem(name)
            item.setToolTip(f"<b>{name}</b><br>{desc}")
            item.setData(Qt.UserRole, name)
            self.var_list.addItem(item)
        for cv in self.custom_variables:
            label = f"★ {cv['name']}"
            item = QListWidgetItem(label)
            tooltip = f"<b>★ {cv['name']}</b>"
            if cv["description"]:
                tooltip += f"<br>{cv['description']}"
            tooltip += f"<br><i>Formel:</i> <code>{cv['formula']}</code>"
            item.setToolTip(tooltip)
            item.setData(Qt.UserRole, cv["name"])  # ohne Stern einfuegen
            item.setForeground(QBrush(QColor("#1565c0")))
            self.var_list.addItem(item)

    def _save_custom_variable(self, update_only: bool = False) -> None:
        name = self.cv_name.text().strip()
        formula = self.formula.toPlainText().strip()
        desc = self.cv_desc.text().strip()

        if not name:
            QMessageBox.warning(self, "Name fehlt", "Bitte einen Namen für die Variable eingeben.")
            return
        if not name.isidentifier():
            QMessageBox.warning(
                self, "Name ungültig",
                "Name muss ein gültiger Python-Bezeichner sein "
                "(Buchstaben/Ziffern/Unterstriche, kein führendes Ziffer)."
            )
            return
        if name in BUILTIN_NAMES:
            QMessageBox.warning(
                self, "Name belegt",
                f"'{name}' ist bereits eine Built-in-Variable. Bitte einen anderen Namen wählen."
            )
            return
        if not formula:
            QMessageBox.warning(self, "Formel fehlt", "Bitte oben eine Formel eingeben.")
            return

        existing_idx = next(
            (i for i, cv in enumerate(self.custom_variables) if cv["name"] == name),
            -1,
        )
        if update_only and existing_idx < 0:
            QMessageBox.warning(
                self, "Nicht gefunden",
                f"Es gibt noch keine Variable namens '{name}'. "
                f"Klick stattdessen 'Speichern' zum Anlegen."
            )
            return
        if not update_only and existing_idx >= 0:
            ret = QMessageBox.question(
                self, "Überschreiben?",
                f"Es gibt bereits eine Variable '{name}'. Überschreiben?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if ret != QMessageBox.Yes:
                return

        entry = {"name": name, "formula": formula, "description": desc}
        if existing_idx >= 0:
            self.custom_variables[existing_idx] = entry
        else:
            self.custom_variables.append(entry)

        try:
            save_custom_variables(self.custom_variables)
        except Exception as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return

        self._refresh_var_list()
        self.statusBar().showMessage(
            f"Variable '{name}' {'aktualisiert' if existing_idx >= 0 else 'gespeichert'}.",
            4000,
        )

    def _delete_custom_variable(self) -> None:
        name = self.cv_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Name fehlt", "Bitte den Namen der zu löschenden Variable eingeben.")
            return
        idx = next(
            (i for i, cv in enumerate(self.custom_variables) if cv["name"] == name),
            -1,
        )
        if idx < 0:
            QMessageBox.warning(self, "Nicht gefunden", f"Keine Variable namens '{name}' gespeichert.")
            return
        ret = QMessageBox.question(
            self, "Löschen?",
            f"Variable '{name}' wirklich löschen?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if ret != QMessageBox.Yes:
            return
        del self.custom_variables[idx]
        try:
            save_custom_variables(self.custom_variables)
        except Exception as exc:
            QMessageBox.critical(self, "Speichern fehlgeschlagen", str(exc))
            return
        self._refresh_var_list()
        self.statusBar().showMessage(f"Variable '{name}' gelöscht.", 4000)

    # ----------------------------- Aktionen -------------------------------- #
    def _populate_sectors_async(self) -> None:
        try:
            conn = sqlite3.connect(str(DB_PATH))
            sectors = pd.read_sql(
                "SELECT DISTINCT sector FROM symbol_metadata "
                "WHERE active = 1 AND sector IS NOT NULL ORDER BY sector",
                conn,
            )["sector"].tolist()
            conn.close()
            for sec in sectors:
                self.sector.addItem(sec)
        except Exception as exc:
            self.statusBar().showMessage(f"Sektoren konnten nicht geladen werden: {exc}", 5000)

    def _start_git_check(self) -> None:
        self.git_worker = GitUpdateChecker(REPO_PATH)
        self.git_worker.update_status.connect(self._on_git_status)
        self.git_worker.start()

    def _on_git_status(self, available: bool, info: str) -> None:
        if available:
            self.update_btn.setText(f"🔄  Update verfügbar ({info})")
            self.update_btn.setVisible(True)
        else:
            self.update_btn.setVisible(False)

    def _do_git_pull(self) -> None:
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Aktualisiere…")
        try:
            r = subprocess.run(
                ["git", "pull", "--ff-only", "origin", "main"],
                cwd=str(REPO_PATH),
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                QMessageBox.critical(
                    self, "Update fehlgeschlagen",
                    (r.stderr or r.stdout or "Unbekannter Fehler").strip(),
                )
                self.update_btn.setEnabled(True)
                self.update_btn.setText("🔄  Update verfügbar")
                return
            QMessageBox.information(
                self, "Update eingespielt",
                "Code wurde aktualisiert. Bitte App schließen und über run.bat neu starten.",
            )
            self.update_btn.setVisible(False)
        except Exception as exc:
            QMessageBox.critical(self, "Update fehlgeschlagen", str(exc))
            self.update_btn.setEnabled(True)
            self.update_btn.setText("🔄  Update verfügbar")

    def _run_backtest(self) -> None:
        formula_text = self.formula.toPlainText().strip()
        if not formula_text:
            QMessageBox.warning(self, "Fehlt", "Bitte eine Formel eingeben.")
            return
        start = pd.Timestamp(self.start_date.date().toPython())
        end = pd.Timestamp(self.end_date.date().toPython())
        if end <= start:
            QMessageBox.warning(self, "Fehler", "Enddatum muss nach Startdatum liegen.")
            return

        self.run_btn.setEnabled(False)
        self.run_btn.setText("Läuft…")

        self.worker = LoadAndBacktestWorker(
            db_path=DB_PATH,
            start_date=start,
            end_date=end,
            formula=formula_text,
            n_stocks=self.n_stocks.value(),
            portfolio_value=float(self.portfolio_value.value()),
            sector_filter=self.sector.currentText(),
            custom_variables=list(self.custom_variables),
            existing_data=self.cached_data,
        )
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.finished_ok.connect(self._on_backtest_done)
        self.worker.failed.connect(self._on_backtest_failed)
        self.worker.start()

    def _on_backtest_done(self, payload: tuple[BacktestResult, MarketData]) -> None:
        result, data = payload
        self.cached_data = data
        self.last_result = result

        n = len(result.equity_curve)
        self.end_slider.blockSignals(True)
        self.end_slider.setRange(1, max(1, n - 1))
        self.end_slider.setValue(n - 1)
        self.end_slider.setEnabled(True)
        self.end_slider.blockSignals(False)

        self._populate_ranking()
        self._redraw()
        self.statusBar().showMessage(
            f"Backtest fertig — Kaufdatum {result.buy_date.date()}", 6000
        )
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶  Backtest starten")

    def _on_backtest_failed(self, msg: str) -> None:
        QMessageBox.critical(self, "Fehler", msg)
        self.run_btn.setEnabled(True)
        self.run_btn.setText("▶  Backtest starten")
        self.statusBar().clearMessage()

    def _slider_changed(self, _value: int) -> None:
        self._redraw()

    def _populate_ranking(self) -> None:
        if self.last_result is None:
            return
        result = self.last_result
        all_signals = result.all_signals
        top_set = set(result.top_tickers)

        self.ranking_table.setSortingEnabled(False)
        self.ranking_table.setRowCount(len(all_signals))

        green_bg = QBrush(QColor(200, 230, 201))
        for row, (ticker, sig_val) in enumerate(all_signals.items()):
            rank = row + 1
            is_top = ticker in top_set
            sector = result.sectors_by_ticker.get(ticker, "")

            rank_item = QTableWidgetItem()
            rank_item.setData(Qt.DisplayRole, rank)
            rank_item.setTextAlignment(Qt.AlignCenter)

            ticker_item = QTableWidgetItem(ticker)
            ticker_item.setFont(QFont("Consolas", 10))

            sig_item = QTableWidgetItem()
            sig_item.setData(Qt.DisplayRole, float(sig_val))
            sig_item.setText(f"{float(sig_val):.4g}")
            sig_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            sector_item = QTableWidgetItem(sector)
            status_item = QTableWidgetItem("✓ gekauft" if is_top else "")
            status_item.setTextAlignment(Qt.AlignCenter)

            if is_top:
                for it in (rank_item, ticker_item, sig_item, sector_item, status_item):
                    it.setBackground(green_bg)

            self.ranking_table.setItem(row, 0, rank_item)
            self.ranking_table.setItem(row, 1, ticker_item)
            self.ranking_table.setItem(row, 2, sig_item)
            self.ranking_table.setItem(row, 3, sector_item)
            self.ranking_table.setItem(row, 4, status_item)

        self.ranking_table.setSortingEnabled(True)
        self.ranking_table.sortByColumn(0, Qt.AscendingOrder)

    def _redraw(self) -> None:
        if self.last_result is None:
            return
        equity = self.last_result.equity_curve
        end_pos = self.end_slider.value() if self.end_slider.isEnabled() else len(equity) - 1
        end_pos = min(end_pos, len(equity) - 1)
        eq = equity.iloc[: end_pos + 1]

        start_val = self.last_result.portfolio_value
        self.slider_label.setText(eq.index[-1].strftime("%Y-%m-%d"))

        xs = [int(ts.timestamp() * 1000) for ts in eq.index]
        ys = [float(v) for v in eq.values]

        # Komplett neues QChart-Objekt bauen statt das alte zu cleanen.
        # PySide6 + QtCharts hat einen Ownership-Bug bei removeAllSeries() in
        # Kombination mit QAreaSeries -> Access Violation 0xC0000005 beim Repaint.
        new_chart = QChart()
        new_chart.legend().setVisible(True)
        new_chart.legend().setAlignment(Qt.AlignBottom)
        new_chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        new_chart.setTitle(
            f"Equity Curve — Kauf am {self.last_result.buy_date.date()}, "
            f"{len(self.last_result.top_tickers)} Aktien, Formel:  {self.last_result.formula}"
        )

        # Equity-Linie
        equity_line = QLineSeries()
        equity_line.setName("Portfolio")
        for x, y in zip(xs, ys):
            equity_line.append(x, y)
        pen = QPen(QColor("#1f77b4"))
        pen.setWidthF(1.8)
        equity_line.setPen(pen)

        # Baseline (gestrichelt, horizontal bei start_val)
        baseline_line = QLineSeries()
        baseline_line.setName("Startwert")
        baseline_line.append(xs[0], start_val)
        baseline_line.append(xs[-1], start_val)
        baseline_pen = QPen(QColor(120, 120, 120))
        baseline_pen.setStyle(Qt.PenStyle.DashLine)
        baseline_pen.setWidthF(0.9)
        baseline_line.setPen(baseline_pen)

        # Gruene Flaeche oberhalb der Baseline (upper = max(equity, baseline)),
        # rote Flaeche unterhalb (lower = min(equity, baseline))
        green_upper = QLineSeries()
        green_lower = QLineSeries()
        red_upper = QLineSeries()
        red_lower = QLineSeries()
        for x, y in zip(xs, ys):
            green_upper.append(x, max(y, start_val))
            green_lower.append(x, start_val)
            red_upper.append(x, start_val)
            red_lower.append(x, min(y, start_val))

        green_area = QAreaSeries(green_upper, green_lower)
        green_area.setName("Gewinn")
        green_area.setBrush(QBrush(QColor(44, 160, 44, 46)))   # alpha ~18%
        green_area.setPen(QPen(Qt.PenStyle.NoPen))

        red_area = QAreaSeries(red_upper, red_lower)
        red_area.setName("Verlust")
        red_area.setBrush(QBrush(QColor(214, 39, 40, 46)))
        red_area.setPen(QPen(Qt.PenStyle.NoPen))

        # Reihenfolge bestimmt Z-Order: Flaechen zuerst, Linien drueber
        for s in (green_area, red_area, baseline_line, equity_line):
            new_chart.addSeries(s)

        # Benchmark-Linien (nur die aktivierten)
        end_ts = eq.index[-1]
        benchmark_lines: list[QLineSeries] = []
        bm_y_min = start_val
        bm_y_max = start_val
        for tkr in BENCHMARK_TICKERS:
            cb = self.benchmark_checks.get(tkr)
            if cb is None or not cb.isChecked():
                continue
            curve = self.last_result.benchmark_curves.get(tkr)
            if curve is None or curve.empty:
                continue
            curve = curve.loc[: end_ts]
            if curve.empty:
                continue
            line = QLineSeries()
            line.setName(tkr)
            for ts, val in curve.items():
                line.append(int(ts.timestamp() * 1000), float(val))
            bp = QPen(QColor(BENCHMARK_COLORS.get(tkr, "#777777")))
            bp.setWidthF(1.2)
            line.setPen(bp)
            new_chart.addSeries(line)
            benchmark_lines.append(line)
            bm_y_min = min(bm_y_min, float(curve.min()))
            bm_y_max = max(bm_y_max, float(curve.max()))

        # Achsen
        x_axis = QDateTimeAxis()
        x_axis.setFormat("yyyy-MM")
        x_axis.setMin(QDateTime.fromMSecsSinceEpoch(xs[0]))
        x_axis.setMax(QDateTime.fromMSecsSinceEpoch(xs[-1]))
        new_chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)

        y_axis = QValueAxis()
        y_axis.setTitleText("Portfoliowert ($)")
        y_axis.setLabelFormat("%.0f")
        y_min = min(min(ys), start_val, bm_y_min)
        y_max = max(max(ys), start_val, bm_y_max)
        pad = max(1.0, (y_max - y_min) * 0.05)
        y_axis.setRange(y_min - pad, y_max + pad)
        new_chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)

        for s in (green_area, red_area, baseline_line, equity_line, *benchmark_lines):
            s.attachAxis(x_axis)
            s.attachAxis(y_axis)

        # Python-Refs auf alle Series halten, sonst raeumt der GC sie ab,
        # waehrend Qt sie noch rendert (insbesondere die upper/lower-Linien
        # innerhalb der QAreaSeries -> Access Violation).
        self._series_refs = [
            equity_line, baseline_line,
            green_upper, green_lower, red_upper, red_lower,
            green_area, red_area,
            x_axis, y_axis,
            *benchmark_lines,
        ]

        # Altes Chart wegwerfen, neues anhaengen. setChart() uebernimmt das
        # neue Chart, das alte wird vom QChartView freigegeben.
        old_chart = self.chart
        self.chart = new_chart
        self.chart_view.setChart(new_chart)
        old_chart.deleteLater()

        # Stats
        end_val = float(eq.iloc[-1])
        total_ret = end_val / start_val - 1
        days = max(1, (eq.index[-1] - eq.index[0]).days)
        cagr = (end_val / start_val) ** (365.25 / days) - 1
        running_max = eq.cummax()
        max_dd = float((eq / running_max - 1).min())

        stats_text = (
            f"Zeitraum:           {eq.index[0].date()} → {eq.index[-1].date()}  ({days} Tage)\n"
            f"Startwert:          {start_val:>14,.2f} $\n"
            f"Endwert:            {end_val:>14,.2f} $\n"
            f"Gesamtrendite:      {total_ret * 100:>13,.2f} %\n"
            f"CAGR:               {cagr * 100:>13,.2f} %\n"
            f"Max Drawdown:       {max_dd * 100:>13,.2f} %"
        )
        self.stats_label.setText(stats_text)


def main() -> int:
    ensure_db_extracted()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
