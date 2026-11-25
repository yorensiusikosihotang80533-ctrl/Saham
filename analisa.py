import html, json, os, sys, time
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
import yfinance as yf, plotly.graph_objects as go

# ================= PENGATURAN (boleh diubah) =================
MODAL = 10_000_000   # angka awal di kotak modal (bisa diganti langsung di halaman)
RISIKO = 1           # angka awal maksimal rugi per transaksi, % dari modal
MIN_LIKUID = 5       # minimal nilai transaksi harian, miliar rupiah
JUMLAH_TOP = 5       # berapa saham yang ditampilin di bagian "layak beli"
MACD_MIN_SKOR = 50   # MACD ditampilin buat saham yang skornya di atas angka ini
MACD_BARU = 3        # cross disebut "baru" kalau terjadi dalam sekian hari bursa terakhir
AREA_HARI = 250      # area demand/supply dicari dari sekian hari bursa terakhir
AREA_JENDELA = 5     # titik balik = harga terendah/tertinggi dalam 5 hari sebelum dan sesudahnya
AREA_MIN_SENTUH = 2  # area yang disentuh kurang dari ini ditandai lemah
AREA_BUFFER = 0.5    # stop loss ditaruh 0,5x gerak harian di bawah area demand
TARGET_MAKS_ATR = 6  # target paling jauh 6x gerak harian dari harga masuk
RR_MIN = 1.5         # untung banding rugi di bawah ini dianggap kurang layak
PBV_MAKS = 50        # PBV di atas ini dianggap data rusak (biasanya laporan dalam dolar)
BIAYA = 0.002        # perkiraan fee + selisih harga tiap transaksi (0,2%)
STOP_ATR = 2         # cadangan kalau area demand gak ketemu: stop = harga - 2x gerak harian
TARGET_ATR = 3       # cadangan kalau area supply gak ketemu: target = harga + 3x gerak harian
VOLUME_RAMAI = 1.5   # volume disebut ramai kalau 1,5x rata-rata 20 hari
# =============================================================

LABEL = ["🟢 Layak beli", "🟡 Pantau / cicil", "⚪ Tunggu", "🔴 Hindari dulu", "⚫ Kurang likuid"]
TEKS_MACD = {"baru-naik": "▲ Baru naik", "naik": "▲ Naik", "baru-turun": "▼ Baru turun", "turun": "▼ Turun"}
TEKS_AREA = {"di": "✅ Di area beli", "dekat": "🟡 Dekat area beli", "jauh": "⏳ Jauh, tunggu turun",
             "": "Area demand gak ketemu"}
TEKS_UJI = {"lulus": "LULUS", "tipis": "LULUS TIPIS, kalah jauh dari beli-diamkan",
            "gagal": "GAGAL, sinyalnya kurang bisa dipercaya"}
BOBOT_UJI = {"lulus": 1.0, "tipis": 0.7, "gagal": 0.4}
SIMPANAN_FUN = {}

# ---------- AMBIL DATA ----------
def ambil_harga(kode):
    """Harga asli (belum disesuaiin dividen) plus kolom Adj Close dan Dividends."""
    for _ in range(3):
        try:
            df = yf.Ticker(kode + ".JK").history(period="5y", auto_adjust=False)
            if df is not None and len(df) >= 300:
                df = df.copy()
                df.index = pd.to_datetime(df.index).tz_localize(None)
                if "Adj Close" not in df.columns:
                    df["Adj Close"] = df["Close"]
                if "Dividends" not in df.columns:
                    df["Dividends"] = 0.0
                df = df[["Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends"]]
                df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
                df["Adj Close"] = df["Adj Close"].fillna(df["Close"])
                df["Dividends"] = df["Dividends"].fillna(0.0)
                if len(df) >= 300:
                    return df
        except Exception:
            pass
        time.sleep(3)
    return None

def versi_disesuaikan(df):
    """Harga yang sudah disesuaiin dividen, khusus buat nguji strategi dan ngukur tren."""
    f = df["Adj Close"] / df["Close"]
    return pd.DataFrame({"High": df["High"] * f, "Low": df["Low"] * f,
                         "Close": df["Adj Close"], "Volume": df["Volume"]})

def hitung_dividen(df, harga):
    batas = df.index[-1] - pd.Timedelta(days=365)
    total_div = float(df["Dividends"][df.index > batas].sum())
    return total_div / harga if harga > 0 else None

def ambil_fundamental(kode):
    try:
        info = yf.Ticker(kode + ".JK").info or {}
    except Exception:
        info = {}
    def angka(k):
        v = info.get(k)
        return float(v) if isinstance(v, (int, float)) and np.isfinite(v) else None
    f = {"nama": info.get("shortName"), "sektor": info.get("sector"),
         "PER": angka("trailingPE"), "PBV": angka("priceToBook"),
         "ROE": angka("returnOnEquity"), "DER": angka("debtToEquity"),
         "Dividen": None, "Growth": angka("earningsGrowth")}
    # kalau Yahoo gagal ngasih sebagian data, pakai simpanan terakhir yang berhasil
    lama = SIMPANAN_FUN.get(kode, {})
    for k in f:
        if f[k] is None and lama.get(k) is not None:
            f[k] = lama[k]
    # buang angka yang jelas rusak
    if f["PBV"] is not None and (f["PBV"] <= 0 or f["PBV"] > PBV_MAKS):
        f["PBV"] = None
    if f["PER"] is not None and abs(f["PER"]) > 1000:
        f["PER"] = None
    SIMPANAN_FUN[kode] = dict(f)
    f["nama"] = f["nama"] or kode
    f["sektor"] = f["sektor"] or "Lainnya"
    return f

def cek_ihsg():
    try:
        c = yf.Ticker("^JKSE").history(period="2y")["Close"]
        if len(c) < 200:
            return None
        return bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1])
    except Exception:
        return None

def baca_daftar():
    kode = []
    with open("saham.txt") as fh:
        for baris in fh:
            baris = baris.split("#")[0]
            for k in baris.replace(";", ",").replace(" ", ",").split(","):
                k = k.strip().upper().replace(".JK", "")
                if k and k not in kode:
                    kode.append(k)
    return kode

# ---------- INDIKATOR ----------
def rsi(c, n=14):
    d = c.diff()
    naik = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    turun = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + naik / turun.replace(0, np.nan))

def atr(df, n=14):
    c0 = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c0).abs(), (df["Low"] - c0).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()

def info_volume(df):
    v, c = df["Volume"], df["Close"]
    rata = v.rolling(20).mean().iloc[-1]
    rasio = float(v.iloc[-1] / rata) if rata > 0 else 0.0
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    akumulasi = bool(obv.iloc[-1] > obv.rolling(20).mean().iloc[-1])
    nilai_harian = float((c * v).rolling(20).mean().iloc[-1])
    return rasio, akumulasi, nilai_harian

def info_macd(c):
    """MACD standar 12/26/9."""
    garis = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sinyal = garis.ewm(span=9, adjust=False).mean()
    hist = garis - sinyal
    di_atas = hist > 0
    ganti = np.flatnonzero((di_atas != di_atas.shift(1)).to_numpy())
    hari = len(hist) - 1 - int(ganti[-1]) if len(ganti) else 999
    naik = bool(di_atas.iloc[-1])
    status = ("baru-" if hari <= MACD_BARU else "") + ("naik" if naik else "turun")
    return {"garis": garis, "sinyal": sinyal, "hist": hist, "status": status, "hari": hari,
            "menguat": bool(hist.iloc[-1] > hist.iloc[-2]), "atas_nol": bool(garis.iloc[-1] > 0)}

# ---------- AREA DEMAND DAN SUPPLY ----------
def fraksi(h):
    return 1 if h < 200 else 2 if h < 500 else 5 if h < 2000 else 10 if h < 5000 else 25

def bulatkan(h):
    f = fraksi(h)
    return int(max(h, 1) // f * f)

def titik_balik(seri, cari_terendah):
    w = 2 * AREA_JENDELA + 1
    batas = seri.rolling(w, center=True).min() if cari_terendah else seri.rolling(w, center=True).max()
    out, terakhir = [], -999
    for i in np.flatnonzero((seri == batas).to_numpy()):
        if i - terakhir > AREA_JENDELA:          # hindari satu titik balik kehitung dobel
            out.append((float(seri.iloc[i]), int(i)))
        terakhir = i
    return out

def cari_area(df, harga, a):
    """Kelompokin titik balik harga yang berdekatan jadi area."""
    d = df.iloc[-AREA_HARI:]
    titik = sorted(titik_balik(d["Low"], True) + titik_balik(d["High"], False))
    lebar = max(a, harga * 0.015)
    grup_semua, grup = [], []
    for p, i in titik:
        if grup and p - grup[0][0] > lebar:
            grup_semua.append(grup)
            grup = []
        grup.append((p, i))
    if grup:
        grup_semua.append(grup)
    area = []
    for g in grup_semua:
        area.append({"bawah": min(p for p, _ in g) - 0.25 * a, "atas": max(p for p, _ in g) + 0.25 * a,
                     "sentuh": len(g), "lalu": len(d) - 1 - max(i for _, i in g)})
    return area

def bikin_rencana(df, harga, a):
    area = cari_area(df, harga, a)
    # demand = area terdekat yang ada di bawah harga atau yang lagi ditempati harga
    calon_d = [z for z in area if z["bawah"] <= harga]
    demand = max(calon_d, key=lambda z: z["atas"]) if calon_d else None
    # supply = area terdekat yang benar-benar di atas harga DAN di atas area beli
    lantai = max(harga, demand["atas"]) if demand else harga
    calon_s = [z for z in area if z["bawah"] > lantai]
    supply = min(calon_s, key=lambda z: z["bawah"]) if calon_s else None

    if demand:
        beli_bawah, beli_atas = bulatkan(demand["bawah"]), bulatkan(demand["atas"])
        stop = bulatkan(demand["bawah"] - AREA_BUFFER * a)
        masuk = min(harga, float(beli_atas))
        posisi = "di" if harga <= beli_atas * 1.01 else "dekat" if harga <= beli_atas + a else "jauh"
    else:
        beli_bawah = beli_atas = bulatkan(harga)
        stop, masuk, posisi = bulatkan(harga - STOP_ATR * a), harga, ""
    if stop >= masuk:
        stop = bulatkan(masuk - STOP_ATR * a)

    batas_target = masuk + TARGET_MAKS_ATR * a
    if supply and supply["bawah"] <= batas_target:
        target, asal = bulatkan(supply["bawah"]), "supply"
    elif supply:
        target, asal = bulatkan(batas_target), "dibatasi"
    else:
        target, asal = bulatkan(masuk + TARGET_ATR * a), "atr"
    if target <= masuk:
        target = bulatkan(masuk) + fraksi(masuk)
    rr = (target - masuk) / (masuk - stop) if masuk > stop else 0.0
    return {"demand": demand, "supply": supply, "beli_bawah": beli_bawah, "beli_atas": beli_atas,
            "masuk": float(masuk), "stop": stop, "target": target, "asal_target": asal,
            "rr": float(max(rr, 0)), "posisi_area": posisi}

def hitung_lot(masuk, stop):
    rugi_per_lot = (masuk - stop) * 100
    if rugi_per_lot <= 0:
        return 0
    return int(max(0, min(MODAL * RISIKO / 100 // rugi_per_lot, MODAL // (masuk * 100))))

# ---------- STRATEGI YANG DIUJI ----------
def posisi_dari_sinyal(masuk, keluar):
    s = pd.Series(np.nan, index=masuk.index)
    s[masuk] = 1.0
    s[keluar] = 0.0
    return s.ffill().fillna(0.0)

def kandidat(df):
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    out = []
    for f, s in [(5, 20), (10, 30), (10, 50), (20, 50), (20, 100), (50, 200)]:
        out.append((f"MA cross {f}/{s}", (c.rolling(f).mean() > c.rolling(s).mean()).astype(float)))
    r = rsi(c)
    for lo, hi in [(30, 70), (25, 65), (35, 65)]:
        out.append((f"RSI beli <{lo}, jual >{hi}", posisi_dari_sinyal(r < lo, r > hi)))
    ramai = v > VOLUME_RAMAI * v.rolling(20).mean()
    for n, m in [(20, 10), (55, 20)]:
        tembus = c > h.rolling(n).max().shift(1)
        jebol = c < l.rolling(m).min().shift(1)
        out.append((f"Breakout {n} hari", posisi_dari_sinyal(tembus, jebol)))
        out.append((f"Breakout {n} hari + volume", posisi_dari_sinyal(tembus & ramai, jebol)))
    return out

# ---------- BAGIAN "BELAJAR SENDIRI" ----------
def hasil_harian(c, pos):
    p = pos.shift(1).fillna(0)
    return p * c.pct_change().fillna(0) - p.diff().abs().fillna(0) * BIAYA

def sharpe(r):
    return 0.0 if r.std() == 0 else float(r.mean() / r.std() * np.sqrt(252))

def total(r):
    return float((1 + r).prod() - 1)

def belajar(df):
    c = df["Close"]
    batas = int(len(df) * 0.7)
    terbaik = None
    for nama, pos in kandidat(df):
        r = hasil_harian(c, pos)
        nilai = sharpe(r.iloc[:batas])
        if terbaik is None or nilai > terbaik["sharpe_latih"]:
            terbaik = {"nama": nama, "pos": pos, "sharpe_latih": nilai,
                       "ret_uji": total(r.iloc[batas:]), "sharpe_uji": sharpe(r.iloc[batas:])}
    terbaik["bh_uji"] = total(c.pct_change().fillna(0).iloc[batas:])
    untung = terbaik["ret_uji"] > 0 and terbaik["sharpe_uji"] > 0
    if not untung:
        terbaik["tingkat"] = "gagal"
    elif terbaik["bh_uji"] > 0 and terbaik["ret_uji"] < 0.5 * terbaik["bh_uji"]:
        terbaik["tingkat"] = "tipis"
    else:
        terbaik["tingkat"] = "lulus"
    terbaik["lulus"] = terbaik["tingkat"] == "lulus"
    terbaik["mulai_uji"] = df.index[batas]
    return terbaik

# ---------- SKOR ----------
def skor_teknikal(df, m, akumulasi, ihsg_sehat):
    c = df["Close"]
    bobot = BOBOT_UJI[m["tingkat"]]
    skor = 50 + (25 if m["pos"].iloc[-1] == 1 else -25) * bobot
    skor += 12 if c.iloc[-1] > c.rolling(200).mean().iloc[-1] else -12
    skor += 8 if c.iloc[-1] > c.iloc[-63] else -8
    skor += 5 if akumulasi else -5
    if ihsg_sehat is False:
        skor -= 15
    return float(np.clip(skor, 0, 100))

def tangga(x, anak_tangga, sisa):
    for batas, nilai in anak_tangga:
        if x < batas:
            return nilai
    return sisa

def skor_fundamental(f):
    s = []
    if f["PER"] is not None:
        s.append(0 if f["PER"] <= 0 else tangga(f["PER"], [(10, 100), (15, 75), (25, 50)], 25))
    if f["PBV"] is not None:
        s.append(tangga(f["PBV"], [(1, 100), (2, 75), (4, 50)], 25))
    if f["ROE"] is not None:
        s.append(tangga(-f["ROE"], [(-0.20, 100), (-0.15, 80), (-0.10, 60), (-0.05, 40)], 20))
    if f["DER"] is not None:
        s.append(tangga(f["DER"], [(50, 100), (100, 70), (200, 40)], 20))
    if f["Dividen"] is not None:
        s.append(tangga(-f["Dividen"], [(-0.05, 100), (-0.03, 75), (-0.01, 50)], 30))
    if f["Growth"] is not None:
        s.append(tangga(-f["Growth"], [(-0.15, 100), (-0.05, 70), (0, 50)], 20))
    return float(np.mean(s)) if len(s) >= 3 else None

def label(skor):
    if skor >= 70: return LABEL[0]
    if skor >= 55: return LABEL[1]
    if skor >= 40: return LABEL[2]
    return LABEL[3]

# ---------- ANALISA SATU SAHAM ----------
def proses(kode, ihsg):
    df = ambil_harga(kode)
    if df is None:
        return None
    dfa = versi_disesuaikan(df)
    harga = float(df["Close"].iloc[-1])
    m, f = belajar(dfa), ambil_fundamental(kode)
    f["Dividen"] = hitung_dividen(df, harga)
    rasio, akumulasi, nilai_harian = info_volume(df)
    tek, fun = skor_teknikal(dfa, m, akumulasi, ihsg), skor_fundamental(f)
    akhir = tek if fun is None else 0.5 * tek + 0.5 * fun
    a = float(atr(df).iloc[-1])
    likuid = nilai_harian >= MIN_LIKUID * 1e9
    d = {"kode": kode, "df": df, "m": m, "f": f, "tek": tek, "fun": fun, "akhir": akhir,
         "rekom": label(akhir) if likuid else LABEL[4],
         "posisi": int(m["pos"].iloc[-1]), "harga": harga, "rasio": rasio, "akumulasi": akumulasi,
         "nilai_harian": nilai_harian, "likuid": likuid,
         "macd": info_macd(df["Close"]), "pakai_macd": round(akhir) > MACD_MIN_SKOR}
    d.update(bikin_rencana(df, harga, a))
    d["lot"] = hitung_lot(d["masuk"], d["stop"])
    return d

def cari_perubahan(hasil, pembanding):
    out = []
    for d in hasil:
        lama = pembanding.get(d["kode"])
        if not lama:
            continue
        if lama["posisi"] != d["posisi"]:
            out.append((d["kode"], "sinyal strategi berubah jadi " + ("BELI" if d["posisi"] == 1 else "JUAL")))
        if lama["rekom"] != d["rekom"]:
            out.append((d["kode"], f"{lama['rekom']} → {d['rekom']} (skor {lama['skor']} → {round(d['akhir'])})"))
    return out

# ---------- BIKIN HALAMAN ----------
def e(x):
    return html.escape(str(x))

def angka(x):
    return f"{x:,.0f}".replace(",", ".")

def rp(x):
    return "Rp " + angka(x)

def fmt(v, persen=False):
    if v is None: return "tidak ada data"
    return f"{v*100:.1f}%" if persen else f"{v:.2f}"

def fmt_der(v):
    return "tidak ada data" if v is None else f"{v / 100:.2f}x"

def data_lot(d):
    """Atribut yang dibaca halaman buat ngitung ulang lot saat modal diganti."""
    return f"data-harga='{d['masuk']:.2f}' data-stop='{d['stop']}'"

def teks_area_beli(d):
    if not d["demand"]:
        return "-"
    return f"{angka(d['beli_bawah'])} - {angka(d['beli_atas'])}"

def bikin_grafik(d, pertama):
    df, m = d["df"], d["m"]
    c = df["Close"]
    lihat = c.iloc[-500:]
    k = df.iloc[-500:]
    fig = go.Figure()
    fig.add_candlestick(x=k.index, open=k["Open"], high=k["High"], low=k["Low"], close=k["Close"], name="Harga",
                        increasing_line_color="#2e9e4f", decreasing_line_color="#d64545")
    fig.add_scatter(x=lihat.index, y=c.rolling(50).mean().reindex(lihat.index), name="MA50", line=dict(dash="dot"))
    fig.add_scatter(x=lihat.index, y=c.rolling(200).mean().reindex(lihat.index), name="MA200", line=dict(dash="dot"))
    ubah = m["pos"].diff().reindex(lihat.index)
    beli, jual = lihat[ubah == 1], lihat[ubah == -1]
    fig.add_scatter(x=beli.index, y=beli, mode="markers", name="Sinyal beli",
                    marker=dict(symbol="triangle-up", size=12, color="green"))
    fig.add_scatter(x=jual.index, y=jual, mode="markers", name="Sinyal jual",
                    marker=dict(symbol="triangle-down", size=12, color="red"))
    fig.add_vline(x=m["mulai_uji"], line_dash="dash", line_color="gray")
    if d["demand"]:
        fig.add_hrect(y0=d["demand"]["bawah"], y1=d["demand"]["atas"], fillcolor="green", opacity=0.15, line_width=0)
    if d["supply"]:
        fig.add_hrect(y0=d["supply"]["bawah"], y1=d["supply"]["atas"], fillcolor="red", opacity=0.15, line_width=0)
    fig.add_hline(y=d["stop"], line_dash="dot", line_color="red", annotation_text="Stop loss")
    fig.add_hline(y=d["target"], line_dash="dot", line_color="green", annotation_text="Target")
    fig.update_layout(height=460, margin=dict(l=10, r=10, t=20, b=10), legend=dict(orientation="h"),
                      xaxis_rangeslider_visible=False)
    dekat = df.iloc[-120:]
    bawah = min(float(dekat["Low"].min()), d["stop"]) * 0.97
    atas = max(float(dekat["High"].max()), d["target"]) * 1.03
    fig.update_xaxes(range=[dekat.index[0], dekat.index[-1]], rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(range=[bawah, atas])
    return fig.to_html(full_html=False, include_plotlyjs="cdn" if pertama else False,
                       config={"responsive": True, "displayModeBar": False})

def bikin_grafik_macd(d):
    mc = d["macd"]
    hist = mc["hist"].iloc[-120:]
    warna = ["#2e9e4f" if v >= 0 else "#d64545" for v in hist]
    fig = go.Figure()
    fig.add_bar(x=hist.index, y=hist, name="Histogram", marker_color=warna)
    fig.add_scatter(x=hist.index, y=mc["garis"].reindex(hist.index), name="MACD", line=dict(color="#0b5cad"))
    fig.add_scatter(x=hist.index, y=mc["sinyal"].reindex(hist.index), name="Sinyal", line=dict(color="#e08a00"))
    fig.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h"))
    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"responsive": True, "displayModeBar": False})

def teks_macd_panjang(mc):
    arah = TEKS_MACD[mc["status"]]
    sejak = "hari ini" if mc["hari"] == 0 else f"{mc['hari']} hari bursa lalu"
    tenaga = "membesar (dorongan menguat)" if mc["menguat"] else "mengecil (dorongan melemah)"
    nol = "di atas garis nol (tren menengah naik)" if mc["atas_nol"] else "di bawah garis nol (tren menengah masih turun)"
    return f"MACD: <b>{arah}</b>, cross terakhir {sejak}. Histogram {tenaga}. Garis MACD {nol}."

def kekuatan(z):
    lemah = " (area lemah)" if z["sentuh"] < AREA_MIN_SENTUH else ""
    return f"disentuh {z['sentuh']} kali setahun terakhir, terakhir {z['lalu']} hari bursa lalu{lemah}"

def blok_detail(d, pertama, ihsg):
    m, f = d["m"], d["f"]
    harga, masuk, stop, target = d["harga"], d["masuk"], d["stop"], d["target"]
    posisi = "PEGANG / BELI" if d["posisi"] == 1 else "DI LUAR / JUAL"
    ujian = TEKS_UJI[m["tingkat"]]
    teks_fun = "tidak cukup data" if d["fun"] is None else f"{d['fun']:.0f}/100"
    teks_ihsg = {True: "sehat", False: "lemah (skor dipotong 15)", None: "data tidak tersedia"}[ihsg]
    teks_vol = "akumulasi" if d["akumulasi"] else "distribusi"
    teks_likuid = "cukup likuid" if d["likuid"] else "⚠️ KURANG LIKUID, bisa susah dijual"
    stop_pct = (stop / masuk - 1) * 100
    target_pct = (target / masuk - 1) * 100
    if d["demand"]:
        baris_beli = (f"Area beli (demand): <b>{rp(d['beli_bawah'])} sampai {rp(d['beli_atas'])}</b>, "
                      + kekuatan(d["demand"]))
        asal_stop = "sedikit di bawah area beli"
    else:
        baris_beli = "Area demand gak ketemu di bawah harga sekarang, jadi rencana dihitung dari harga sekarang pakai rumus ATR"
        asal_stop = "rumus ATR"
    if d["asal_target"] == "supply":
        asal_target = "batas bawah area supply, " + kekuatan(d["supply"])
    elif d["asal_target"] == "dibatasi":
        asal_target = (f"dibatasi {TARGET_MAKS_ATR}x gerak harian, karena area supply terdekat masih jauh "
                       f"(mulai {rp(bulatkan(d['supply']['bawah']))})")
    else:
        asal_target = "rumus ATR, karena gak ada area supply di atas harga"
    layak = "layak" if d["rr"] >= RR_MIN else f"kurang layak, di bawah {RR_MIN:g}"
    atribut = data_lot(d)
    grafik = bikin_grafik(d, pertama)
    grafik_macd = bikin_grafik_macd(d) if d["pakai_macd"] else ""
    baris_macd = "<li>" + teks_macd_panjang(d["macd"]) + "</li>" if d["pakai_macd"] else ""
    return f"""<details class="blok" id="{d['kode']}">
<summary><b>{d['kode']}</b> {e(f['nama'])} · {d['rekom']} · skor {d['akhir']:.0f}</summary>
{grafik}
{grafik_macd}
<button class="tv" type="button" data-kode="{d['kode']}">Tampilkan grafik TradingView</button><div></div>
<p class="info">Sektor: {e(f['sektor'])} · <a href="https://www.tradingview.com/chart/?symbol=IDX:{d['kode']}" target="_blank" rel="noopener">Buka di TradingView ↗</a> · <a href="https://stockbit.com/symbol/{d['kode']}" target="_blank" rel="noopener">Buka di Stockbit ↗</a></p>
<p><b>Rencana transaksi</b></p>
<ul>
<li>Harga sekarang {rp(harga)} · <b>{TEKS_AREA[d['posisi_area']]}</b></li>
<li>{baris_beli}</li>
<li>Stop loss <b>{rp(stop)}</b> ({stop_pct:+.1f}% dari harga masuk acuan {rp(masuk)}), {asal_stop}</li>
<li>Target <b>{rp(target)}</b> ({target_pct:+.1f}%), {asal_target}</li>
<li>Untung banding rugi: <b>{d['rr']:.1f} banding 1</b> ({layak})</li>
<li>Maksimal beli: <span data-mode='panjang' {atribut}>{d['lot']} lot</span></li>
</ul>
<p><b>Teknikal: {d['tek']:.0f}/100</b></p>
<ul>
<li>Strategi paling cocok: <b>{e(m['nama'])}</b>, posisi sekarang <b>{posisi}</b></li>
<li>Ujian sejak {m['mulai_uji']:%b %Y} (kanan garis abu-abu): strategi {m['ret_uji']*100:+.1f}% vs beli-diamkan {m['bh_uji']*100:+.1f}% → <b>{ujian}</b></li>
{baris_macd}
<li>Kondisi IHSG: {teks_ihsg}</li>
<li>Volume terakhir {d['rasio']:.1f}x rata-rata 20 hari, arah {teks_vol}</li>
<li>Nilai transaksi harian rata-rata {rp(d['nilai_harian'])} → {teks_likuid}</li>
</ul>
<p><b>Fundamental: {teks_fun}</b></p>
<ul>
<li>PER {fmt(f['PER'])} · PBV {fmt(f['PBV'])} · ROE {fmt(f['ROE'], True)}</li>
<li>Utang banding modal (DER) {fmt_der(f['DER'])} · Dividen setahun terakhir {fmt(f['Dividen'], True)} · Pertumbuhan laba {fmt(f['Growth'], True)}</li>
</ul>
</details>"""

CSS = """
body{font-family:system-ui,sans-serif;max-width:1100px;margin:0 auto;padding:16px;color:#1a1a1a;background:#fafafa;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:0} h2{font-size:1.15rem;margin-top:32px;border-bottom:2px solid #ddd;padding-bottom:4px}
.info{color:#555;font-size:.9rem} .peringatan{background:#fff3cd;padding:8px 12px;border-radius:8px}
.modal-box{background:#eef5ff;border:1px solid #b9d3f5;border-radius:8px;padding:12px;margin:16px 0}
.modal-box label{display:inline-block;margin:4px 16px 4px 0;font-weight:600}
.modal-box input{font-size:1rem;padding:6px 8px;border:1px solid #999;border-radius:6px;width:150px}
.modal-box input#risiko{width:60px} #modal-teks{font-weight:700;color:#0b5cad}
.filter{background:#f3f3f3;border:1px solid #ddd;border-radius:8px;padding:10px;margin:10px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.filter input[type=text],.filter select,.filter button{font-size:.95rem;padding:6px 8px;border:1px solid #999;border-radius:6px;background:#fff}
.filter #f-cari{width:190px} .filter #f-skor{width:50px} .filter button{cursor:pointer}
.filter label{font-size:.9rem} #f-jumlah{width:100%;font-size:.9rem;color:#555}
.kartu-wrap{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.kartu{background:#fff;border:1px solid #ddd;border-left:5px solid #2e9e4f;border-radius:8px;padding:12px}
.kartu .kode{font-size:1.2rem;font-weight:700} .kartu .skor{float:right;color:#2e9e4f;font-weight:700}
.kartu small{color:#555}
.geser{overflow-x:auto} table{border-collapse:collapse;width:100%;background:#fff;font-size:.9rem}
th,td{padding:6px 10px;border-bottom:1px solid #e5e5e5;text-align:right;white-space:nowrap}
th:first-child,td:first-child,th:nth-child(4),td:nth-child(4),th:nth-child(6),td:nth-child(6),th:nth-child(12),td:nth-child(12),th:last-child,td:last-child{text-align:left}
th{background:#f0f0f0;cursor:pointer;user-select:none}
details{background:#fff;border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:8px 0}
button.tv{font-size:.9rem;padding:6px 12px;border:1px solid #999;border-radius:6px;background:#fff;cursor:pointer;margin:8px 0}
summary{cursor:pointer} a{color:#0b5cad;text-decoration:none} footer{margin-top:32px;color:#777;font-size:.8rem}
"""

JS = """
function ukurUlang(){window.dispatchEvent(new Event('resize'));}
document.querySelectorAll('details').forEach(function(d){d.addEventListener('toggle',ukurUlang);});
function buka(){var el=document.getElementById(location.hash.slice(1));
  if(el&&el.tagName==='DETAILS'){el.style.display='';el.open=true;el.scrollIntoView();}}
window.addEventListener('hashchange',buka);buka();

// ----- TradingView -----
function muatTV(tombol){
  var wadah=tombol.nextElementSibling;
  wadah.style.height='640px';
  wadah.innerHTML='<div class="tradingview-widget-container" style="height:100%;width:100%"><div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div></div>';
  var s=document.createElement('script');
  s.src='https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js';
  s.async=true;
  s.innerHTML=JSON.stringify({autosize:true,symbol:'IDX:'+tombol.dataset.kode,interval:'D',timezone:'Asia/Jakarta',theme:'light',style:'1',locale:'id',allow_symbol_change:false,withdateranges:true,hide_side_toolbar:false,studies:['STD;SMA','STD;MACD','STD;RSI']});
  wadah.firstChild.appendChild(s);
  tombol.style.display='none';
}
document.querySelectorAll('button.tv').forEach(function(t){t.addEventListener('click',function(){muatTV(t);});});

// ----- filter -----
function ambilNilai(id){return document.getElementById(id).value;}
function saring(){
  var cari=ambilNilai('f-cari').toUpperCase().split(/[ ,]+/).filter(function(k){return k;});
  var rekom=ambilNilai('f-rekom'), sinyal=ambilNilai('f-sinyal'), sektor=ambilNilai('f-sektor');
  var macd=ambilNilai('f-macd'), area=ambilNilai('f-area');
  var skor=parseFloat(ambilNilai('f-skor'))||0;
  var lulus=document.getElementById('f-lulus').checked, kebeli=document.getElementById('f-lot').checked;
  var rrOk=document.getElementById('f-rr').checked;
  var tampil={}, n=0, semua=0;
  document.querySelectorAll('tr.baris').forEach(function(tr){
    semua++;
    var d=tr.dataset, lot=parseInt(tr.querySelector('[data-harga]').textContent)||0;
    var okMacd=!macd||(macd.indexOf('baru')===0?d.macd===macd:d.macd.indexOf(macd)>=0);
    var ok=(cari.length===0||cari.some(function(k){return d.kode.indexOf(k)>=0;}))
      &&(!rekom||d.rekom===rekom)&&(!sinyal||d.sinyal===sinyal)&&(!sektor||d.sektor===sektor)&&okMacd
      &&(!area||d.area===area)&&(!rrOk||parseFloat(d.rr)>=RR_MIN_NILAI)
      &&parseFloat(d.skor)>=skor&&(!lulus||d.lulus==='1')&&(!kebeli||lot>=1);
    tr.style.display=ok?'':'none'; tampil[d.kode]=ok; if(ok){n++;}
  });
  document.querySelectorAll('details.blok').forEach(function(el){el.style.display=tampil[el.id]===false?'none':'';});
  document.getElementById('f-jumlah').textContent='Menampilkan '+n+' dari '+semua+' saham';
}
['f-cari','f-skor'].forEach(function(id){document.getElementById(id).addEventListener('input',saring);});
['f-rekom','f-sinyal','f-sektor','f-macd','f-area','f-lulus','f-lot','f-rr'].forEach(function(id){document.getElementById(id).addEventListener('change',saring);});
document.getElementById('f-reset').addEventListener('click',function(){
  ['f-cari','f-rekom','f-sinyal','f-sektor','f-macd','f-area'].forEach(function(id){document.getElementById(id).value='';});
  document.getElementById('f-skor').value='0';
  ['f-lulus','f-lot','f-rr'].forEach(function(id){document.getElementById(id).checked=false;});
  saring();
});

// ----- urutkan dengan klik judul kolom -----
document.querySelectorAll('#tabel-peringkat th').forEach(function(th,i){
  th.addEventListener('click',function(){
    var rows=Array.prototype.slice.call(document.querySelectorAll('#tabel-peringkat tr.baris'));
    var naik=th.dataset.arah!=='naik'; th.dataset.arah=naik?'naik':'turun';
    function isi(tr){var t=tr.children[i].textContent.trim();
      var x=parseFloat(t.split('.').join('').replace('x',''));return isNaN(x)?t:x;}
    rows.sort(function(a,b){var x=isi(a),y=isi(b);
      if(typeof x==='number'&&typeof y==='number'){return naik?x-y:y-x;}
      return naik?String(x).localeCompare(String(y)):String(y).localeCompare(String(x));});
    rows.forEach(function(r){r.parentNode.appendChild(r);});
  });
});

// ----- modal dan maks lot -----
var kotakModal=document.getElementById('modal'), kotakRisiko=document.getElementById('risiko');
function titik(x){return Math.round(x).toLocaleString('id-ID');}
function hitung(){
  var modal=parseFloat(kotakModal.value.replace(/[^0-9]/g,''))||0;
  var risiko=parseFloat(kotakRisiko.value.replace(',','.'))||0;
  document.getElementById('modal-teks').textContent='= Rp '+titik(modal)+', maks rugi Rp '+titik(modal*risiko/100)+' per transaksi';
  try{localStorage.setItem('modal',modal);localStorage.setItem('risiko',risiko);}catch(err){}
  document.querySelectorAll('[data-harga]').forEach(function(el){
    var h=parseFloat(el.dataset.harga), s=parseFloat(el.dataset.stop), rugi=(h-s)*100, lot=0;
    if(rugi>0){lot=Math.max(0,Math.min(Math.floor(modal*risiko/100/rugi),Math.floor(modal/(h*100))));}
    if(el.dataset.mode==='panjang'){
      el.innerHTML=lot>0
        ? '<b>'+lot+' lot</b> (sekitar Rp '+titik(lot*h*100)+'), rugi sekitar Rp '+titik(lot*rugi)+' kalau kena stop loss'
        : '<b>0 lot</b>, modal atau batas risikonya terlalu kecil buat saham ini';
    } else { el.textContent=lot; }
  });
  saring();
}
try{
  var m0=localStorage.getItem('modal'), r0=localStorage.getItem('risiko');
  if(m0&&parseFloat(m0)>0){kotakModal.value=m0;}
  if(r0&&parseFloat(r0)>0){kotakRisiko.value=r0;}
}catch(err){}
kotakModal.addEventListener('input',hitung);
kotakRisiko.addEventListener('input',hitung);
hitung();
"""

def bikin_filter(hasil):
    pilihan_rekom = "".join(f"<option value='{e(x)}'>{e(x)}</option>" for x in LABEL)
    sektor = sorted({d["f"]["sektor"] for d in hasil})
    pilihan_sektor = "".join(f"<option value='{e(x)}'>{e(x)}</option>" for x in sektor)
    return ("<div class='filter'>"
            "<input id='f-cari' type='text' placeholder='Cari kode, misal BREN, CUAN'>"
            f"<select id='f-rekom'><option value=''>Semua rekomendasi</option>{pilihan_rekom}</select>"
            "<select id='f-sinyal'><option value=''>Semua sinyal</option><option value='Beli'>Beli</option>"
            "<option value='Jual'>Jual</option></select>"
            "<select id='f-area'><option value=''>Semua posisi harga</option><option value='di'>Di area beli</option>"
            "<option value='dekat'>Dekat area beli</option><option value='jauh'>Jauh dari area beli</option></select>"
            "<select id='f-macd'><option value=''>Semua MACD</option><option value='naik'>MACD naik</option>"
            "<option value='baru-naik'>MACD baru naik</option><option value='turun'>MACD turun</option>"
            "<option value='baru-turun'>MACD baru turun</option></select>"
            f"<select id='f-sektor'><option value=''>Semua sektor</option>{pilihan_sektor}</select>"
            "<label>Skor minimal <input id='f-skor' type='text' inputmode='numeric' value='0'></label>"
            "<label><input type='checkbox' id='f-lulus'> Cuma yang lulus ujian</label>"
            "<label><input type='checkbox' id='f-lot'> Cuma yang kebeli</label>"
            f"<label><input type='checkbox' id='f-rr'> U:R minimal {RR_MIN:g}</label>"
            "<button id='f-reset' type='button'>Reset</button>"
            "<div id='f-jumlah'></div></div>")

def bikin_halaman(hasil, perubahan, tgl_pembanding, ihsg, gagal, tanggal_data):
    wib = datetime.now(timezone(timedelta(hours=7)))
    b = []
    b.append("<!doctype html><html lang='id'><head><meta charset='utf-8'>")
    b.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    b.append("<title>Ringkasan Saham Harian</title><style>" + CSS + "</style></head><body>")
    b.append("<h1>📈 Ringkasan Saham Harian</h1>")
    b.append(f"<p class='info'>Data bursa per {e(tanggal_data)} · diperbarui {wib:%d %b %Y %H:%M} WIB · {len(hasil)} saham</p>")
    if ihsg is True:
        b.append("<p>IHSG: <b>sehat</b> (di atas rata-rata 200 hari)</p>")
    elif ihsg is False:
        b.append("<p class='peringatan'>⚠️ IHSG <b>lemah</b> (di bawah rata-rata 200 hari). Semua skor teknikal dipotong 15.</p>")
    else:
        b.append("<p class='info'>Data IHSG gagal diambil, filter pasar dilewati.</p>")
    if gagal:
        b.append(f"<p class='info'>Gagal diambil: {e(', '.join(gagal))}</p>")

    # --- Kotak modal ---
    b.append("<div class='modal-box'>"
             f"<label>Modal hari ini (Rp) <input id='modal' type='text' inputmode='numeric' value='{MODAL}'></label>"
             f"<label>Maks rugi per transaksi (%) <input id='risiko' type='text' inputmode='decimal' value='{RISIKO}'></label>"
             "<div id='modal-teks'></div>"
             "<div class='info'>Maks lot di seluruh halaman langsung ngikutin angka ini. Angkanya cuma kesimpen di browser lu.</div>"
             "</div>")

    # --- Top layak beli ---
    b.append("<h2>Top saham layak beli</h2>")
    top = [d for d in hasil if d["rekom"] == LABEL[0]][:JUMLAH_TOP]
    if top:
        b.append("<div class='kartu-wrap'>")
        for d in top:
            macd_kartu = "<br>MACD " + TEKS_MACD[d["macd"]["status"]] if d["pakai_macd"] else ""
            beli = f"Area beli Rp {teks_area_beli(d)}<br>" if d["demand"] else ""
            b.append(f"<div class='kartu'><span class='skor'>{d['akhir']:.0f}</span>"
                     f"<div class='kode'><a href='#{d['kode']}'>{d['kode']}</a></div>"
                     f"<small>{e(d['f']['nama'])}</small><br>"
                     f"Harga {rp(d['harga'])}<br>{beli}{TEKS_AREA[d['posisi_area']]}<br>"
                     f"Stop loss {rp(d['stop'])}<br>Target {rp(d['target'])}<br>"
                     f"Untung banding rugi {d['rr']:.1f} : 1<br>"
                     f"Maks <b><span {data_lot(d)}>{d['lot']}</span> lot</b>{macd_kartu}</div>")
        b.append("</div>")
    else:
        b.append("<p>Hari ini gak ada saham yang skornya 70 ke atas. Gak beli apa-apa juga keputusan yang sah.</p>")

    # --- Perubahan ---
    b.append("<h2>Perubahan dari hari sebelumnya</h2>")
    if tgl_pembanding is None:
        b.append("<p class='info'>Belum ada data pembanding. Bagian ini mulai keisi di hari bursa berikutnya.</p>")
    elif perubahan:
        b.append(f"<p class='info'>Dibanding data {e(tgl_pembanding)}</p><ul>")
        for kode, teks in perubahan:
            b.append(f"<li><a href='#{kode}'><b>{kode}</b></a>: {e(teks)}</li>")
        b.append("</ul>")
    else:
        b.append(f"<p>Gak ada sinyal yang berubah dibanding data {e(tgl_pembanding)}.</p>")

    # --- Peringkat + filter ---
    b.append("<h2>Peringkat lengkap</h2>")
    b.append(bikin_filter(hasil))
    b.append(f"<p class='info'>Klik judul kolom buat ngurutin. U:R = untung banding rugi. "
             f"Kolom MACD cuma keisi buat saham dengan skor di atas {MACD_MIN_SKOR}.</p>")
    b.append("<div class='geser'><table id='tabel-peringkat'><tr><th>Kode</th><th>Harga</th><th>Skor</th>"
             "<th>Rekomendasi</th><th>Sinyal</th><th>Area beli</th><th>Stop loss</th><th>Target</th><th>U:R</th>"
             "<th>Maks lot</th><th>Volume</th><th>MACD</th><th>Sektor</th></tr>")
    ikon_area = {"di": "✅ ", "dekat": "🟡 ", "jauh": "⏳ ", "": ""}
    for d in hasil:
        sinyal = "Beli" if d["posisi"] == 1 else "Jual"
        lulus = "1" if d["m"]["lulus"] else "0"
        sektor = e(d["f"]["sektor"])
        kode_macd = d["macd"]["status"] if d["pakai_macd"] else ""
        teks_macd = TEKS_MACD[kode_macd] if kode_macd else "-"
        b.append(f"<tr class='baris' data-kode='{d['kode']}' data-skor='{d['akhir']:.0f}' data-rekom='{e(d['rekom'])}' "
                 f"data-sinyal='{sinyal}' data-lulus='{lulus}' data-sektor='{sektor}' data-macd='{kode_macd}' "
                 f"data-area='{d['posisi_area']}' data-rr='{d['rr']:.2f}'>"
                 f"<td><a href='#{d['kode']}'><b>{d['kode']}</b></a></td><td>{angka(d['harga'])}</td>"
                 f"<td>{d['akhir']:.0f}</td><td>{d['rekom']}</td><td>{sinyal}</td>"
                 f"<td>{ikon_area[d['posisi_area']]}{teks_area_beli(d)}</td>"
                 f"<td>{angka(d['stop'])}</td><td>{angka(d['target'])}</td><td>{d['rr']:.1f}</td>"
                 f"<td {data_lot(d)}>{d['lot']}</td><td>{d['rasio']:.1f}x</td>"
                 f"<td>{teks_macd}</td><td>{sektor}</td></tr>")
    b.append("</table></div>")

    # --- Detail ---
    b.append("<h2>Detail per saham</h2><p class='info'>Klik buat buka grafik dan penjelasannya. Pita hijau di grafik = area demand, "
             "pita merah = area supply. Bagian ini ikut filter di atas.</p>")
    for i, d in enumerate(hasil):
        b.append(blok_detail(d, i == 0, ihsg))

    b.append("<footer>Bukan saran investasi. Performa masa lalu tidak menjamin hasil ke depan. "
             "Data dari Yahoo Finance, bisa telat atau tidak lengkap.</footer>")
    b.append("<script>" + JS.replace("RR_MIN_NILAI", str(RR_MIN)) + "</script></body></html>")
    return "\n".join(b)

# ---------- JALANKAN ----------
def utama():
    os.makedirs("docs", exist_ok=True)
    if os.path.exists("docs/fundamental.json"):
        try:
            with open("docs/fundamental.json", encoding="utf-8") as fh:
                SIMPANAN_FUN.update(json.load(fh))
        except Exception:
            pass

    daftar = baca_daftar()
    ihsg = cek_ihsg()
    hasil, gagal = [], []
    for kode in daftar:
        print("Analisa", kode)
        d = proses(kode, ihsg)
        if d is None:
            gagal.append(kode)
        else:
            hasil.append(d)
        time.sleep(0.5)
    if not hasil:
        print("Semua saham gagal diambil. Halaman lama dibiarkan.")
        sys.exit(1)
    hasil.sort(key=lambda d: d["akhir"], reverse=True)
    tanggal_data = max(d["df"].index[-1] for d in hasil).strftime("%d %b %Y")

    lama = {}
    if os.path.exists("docs/data.json"):
        try:
            with open("docs/data.json", encoding="utf-8") as fh:
                lama = json.load(fh)
        except Exception:
            lama = {}
    if lama.get("tanggal") and lama["tanggal"] != tanggal_data:
        pembanding, tgl_pembanding = lama.get("saham", {}), lama["tanggal"]
    else:
        pembanding, tgl_pembanding = lama.get("sebelumnya", {}), lama.get("tanggal_sebelumnya")
    perubahan = cari_perubahan(hasil, pembanding)

    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(bikin_halaman(hasil, perubahan, tgl_pembanding, ihsg, gagal, tanggal_data))
    sekarang = {d["kode"]: {"rekom": d["rekom"], "posisi": d["posisi"], "skor": round(d["akhir"]),
                            "stop": d["stop"], "target": d["target"],
                            "masuk": d["masuk"] if d["demand"] else None, "area": d["posisi_area"]}
                for d in hasil}
    with open("docs/data.json", "w", encoding="utf-8") as fh:
        json.dump({"tanggal": tanggal_data, "saham": sekarang,
                   "sebelumnya": pembanding, "tanggal_sebelumnya": tgl_pembanding}, fh, ensure_ascii=False)
    with open("docs/fundamental.json", "w", encoding="utf-8") as fh:
        json.dump(SIMPANAN_FUN, fh, ensure_ascii=False)
    print("Selesai:", len(hasil), "saham,", len(gagal), "gagal")

utama()
